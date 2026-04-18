"""중소벤처24(bizinfo) API extractor — 정책자금 사업목록 수집 모듈.

API: https://www.bizinfo.go.kr/uss/rss/bizinfo.do
수집 항목: 사업명, 공고번호, 분야, 신청기간, 소관기관, 상세URL
"""
import json
import logging
import os
import time
from datetime import datetime

import boto3
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BIZINFO_API_URL = 'https://www.bizinfo.go.kr/uss/rss/bizinfo.do'

# 분야 코드 → 카테고리 매핑
CATEGORY_MAP = {
    '융자': '금융',
    '금융': '금융',
    '보증': '금융',
    '투자': '금융',
    '기술': '기술',
    '연구개발': '기술',
    '인력': '인력',
    '고용': '인력',
    '수출': '수출',
    '해외': '수출',
    '내수': '내수',
    '판로': '내수',
    '창업': '창업',
    '경영': '경영',
    '컨설팅': '경영',
}


def _get_api_key() -> str:
    key = os.environ.get('BIZINFO_API_KEY')
    if not key:
        raise ValueError('BIZINFO_API_KEY 환경변수가 설정되지 않았습니다.')
    return key


def _get_s3_client():
    return boto3.client('s3')


def _normalize_category(raw_category: str | None) -> str:
    """원시 분야 문자열을 정규 카테고리로 변환."""
    if not raw_category:
        return '기타'
    for keyword, category in CATEGORY_MAP.items():
        if keyword in raw_category:
            return category
    return '기타'


def _parse_program(item: dict) -> dict:
    """API 응답 아이템을 스키마 형식으로 파싱."""
    return {
        'program_name': item.get('pbanc_nm') or item.get('title') or None,
        'announcement_no': item.get('pbanc_no') or item.get('biz_no') or None,
        'category': _normalize_category(item.get('supt_biz_clsfc_nm') or item.get('category')),
        'apply_start': item.get('aply_bgng_dt') or item.get('startDate') or None,
        'apply_end': item.get('aply_end_dt') or item.get('endDate') or None,
        'managing_org': item.get('jrsd_inst_nm') or item.get('organization') or None,
        'detail_url': item.get('dtl_url') or item.get('link') or None,
    }


def fetch_programs(page: int = 1, page_size: int = 100) -> dict:
    """사업목록 1페이지 조회.

    Returns:
        {'items': [...], 'total_count': int, 'page': int}
    """
    api_key = _get_api_key()
    params = {
        'serviceKey': api_key,
        'pageNo': page,
        'numOfRows': page_size,
        'pbancStatCode': '1',   # 1: 공고중
        'dataType': 'json',
    }

    last_exc = None
    for attempt in range(1, 4):
        try:
            response = requests.get(BIZINFO_API_URL, params=params, timeout=30)
            response.raise_for_status()
            break
        except Exception as exc:
            last_exc = exc
            logger.warning('bizinfo API 요청 실패 (시도 %d/3) page=%d: %s', attempt, page, exc)
            if attempt < 3:
                time.sleep(2 ** attempt)
    else:
        raise RuntimeError(f'bizinfo API 최대 재시도 횟수 초과 (page={page})') from last_exc

    # JSON 응답 파싱 (RSS/XML 형태일 경우 대비해 두 방식 모두 처리)
    try:
        data = response.json()
    except ValueError:
        # XML 응답 처리
        import xml.etree.ElementTree as ET
        root = ET.fromstring(response.text)
        items = []
        for item in root.iter('item'):
            record = {}
            for child in item:
                record[child.tag] = child.text
            items.append(_parse_program(record))
        total_count_el = root.find('.//totalCount')
        total_count = int(total_count_el.text) if total_count_el is not None else len(items)
        return {'items': items, 'total_count': total_count, 'page': page}

    # JSON 응답 파싱
    body = (
        data.get('response', {}).get('body', {})
        or data.get('body', {})
        or data
    )
    raw_items = (
        body.get('items', {}).get('item', [])
        if isinstance(body.get('items'), dict)
        else body.get('items', [])
    )
    if isinstance(raw_items, dict):
        raw_items = [raw_items]

    items = [_parse_program(item) for item in raw_items]
    total_count = int(body.get('totalCount', len(items)))

    logger.info('bizinfo 조회 완료: page=%d, 건수=%d / 전체=%d', page, len(items), total_count)
    return {'items': items, 'total_count': total_count, 'page': page}


def fetch_all_programs() -> list[dict]:
    """전체 페이지 순회 수집."""
    page_size = 100
    all_items: list[dict] = []

    # 첫 페이지 조회로 전체 건수 확인
    first = fetch_programs(page=1, page_size=page_size)
    all_items.extend(first['items'])
    total_count = first['total_count']
    total_pages = (total_count + page_size - 1) // page_size

    logger.info('bizinfo 전체 페이지 수: %d (총 %d건)', total_pages, total_count)

    for page in range(2, total_pages + 1):
        try:
            result = fetch_programs(page=page, page_size=page_size)
            all_items.extend(result['items'])
        except Exception as exc:
            logger.error('bizinfo page=%d 수집 실패: %s', page, exc)

    logger.info('bizinfo 전체 수집 완료: %d건', len(all_items))
    return all_items


def upload_to_s3(data: dict | list, bucket: str, key: str) -> None:
    """딕셔너리/리스트를 JSON으로 S3에 업로드."""
    s3 = _get_s3_client()
    body = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType='application/json')
    logger.info('S3 업로드 완료: s3://%s/%s', bucket, key)


def run_extraction() -> None:
    """메인 실행 함수."""
    bucket = os.environ.get('S3_BUCKET_NAME')
    if not bucket:
        raise ValueError('S3_BUCKET_NAME 환경변수가 설정되지 않았습니다.')

    date_str = datetime.now().strftime('%Y-%m-%d')
    logger.info('bizinfo 수집 시작: date=%s', date_str)

    try:
        programs = fetch_all_programs()
    except Exception as exc:
        logger.error('bizinfo 전체 수집 실패: %s', exc)
        raise

    record = {
        'date': date_str,
        'total_count': len(programs),
        'programs': programs,
    }
    key = f'raw/bizinfo/{date_str}/programs.json'
    upload_to_s3(record, bucket, key)
    logger.info('bizinfo 수집 및 S3 저장 완료: %d건', len(programs))


def extract_bizinfo_task(**context) -> None:
    """Airflow PythonOperator에서 호출 가능한 callable."""
    run_extraction()


if __name__ == '__main__':
    run_extraction()
