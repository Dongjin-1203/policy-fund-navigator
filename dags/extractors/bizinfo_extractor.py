"""중소벤처24(bizinfo) API extractor — 정책자금 사업목록 수집 모듈.

API: https://www.bizinfo.go.kr/uss/rss/bizinfoApi.do
인증키 파라미터: crtfcKey (기업마당 발급)
수집 항목: 사업명, 공고ID, 지원분야, 신청기간, 소관기관, 공고URL
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

BIZINFO_API_URL = 'https://www.bizinfo.go.kr/uss/rss/bizinfoApi.do'

# searchLclasId 코드 → 내부 카테고리 매핑
LCLASS_CATEGORY_MAP = {
    '01': '금융',
    '02': '기술',
    '03': '인력',
    '04': '수출',
    '05': '내수',
    '06': '창업',
    '07': '경영',
    '09': '기타',
}

# 분야명 키워드 → 카테고리 fallback 매핑
CATEGORY_KEYWORD_MAP = {
    '융자': '금융', '금융': '금융', '보증': '금융', '투자': '금융', '자금': '금융',
    '기술': '기술', '연구개발': '기술', 'R&D': '기술',
    '인력': '인력', '고용': '인력',
    '수출': '수출', '해외': '수출',
    '내수': '내수', '판로': '내수',
    '창업': '창업',
    '경영': '경영', '컨설팅': '경영',
}


def _get_api_key() -> str:
    key = os.environ.get('BIZINFO_API_KEY')
    if not key:
        raise ValueError('BIZINFO_API_KEY 환경변수가 설정되지 않았습니다.')
    return key


def _get_s3_client():
    return boto3.client('s3')


def _normalize_category(raw_category: str | None) -> str:
    """지원분야 문자열을 정규 카테고리로 변환."""
    if not raw_category:
        return '기타'
    for keyword, category in CATEGORY_KEYWORD_MAP.items():
        if keyword in raw_category:
            return category
    return '기타'


def _parse_apply_dates(period_str: str | None) -> tuple[str | None, str | None]:
    """'YYYY-MM-DD ~ YYYY-MM-DD' 형식의 신청기간을 시작일/종료일로 분리."""
    if not period_str:
        return None, None
    parts = [p.strip() for p in period_str.split('~')]
    apply_start = parts[0] if len(parts) > 0 else None
    apply_end = parts[1] if len(parts) > 1 else None
    return apply_start, apply_end


def _parse_program(item: dict) -> dict:
    """API 응답 아이템을 스키마 형식으로 파싱.

    실제 응답 필드 (bizinfo API 명세):
        pblancNm              → 공고명
        pblancId              → 공고ID
        pldirSportRealmLclasCodeNm → 지원분야 대분류명
        reqstBeginEndDe       → 신청기간 ('YYYY-MM-DD ~ YYYY-MM-DD')
        jrsdInsttNm           → 소관기관명
        pblancUrl             → 공고URL
    """
    apply_start, apply_end = _parse_apply_dates(item.get('reqstBeginEndDe'))
    return {
        'program_name': item.get('pblancNm') or None,
        'announcement_no': item.get('pblancId') or None,
        'category': _normalize_category(item.get('pldirSportRealmLclasCodeNm')),
        'apply_start': apply_start,
        'apply_end': apply_end,
        'managing_org': item.get('jrsdInsttNm') or None,
        'detail_url': item.get('pblancUrl') or None,
    }


def fetch_programs(lclass_id: str | None = None, search_cnt: int = 500) -> list[dict]:
    """사업목록 조회.

    Args:
        lclass_id: 분야 코드 (01~07, 09). None이면 전체 분야 조회.
        search_cnt: 최대 조회 건수.

    Returns:
        파싱된 사업 목록 리스트.
    """
    api_key = _get_api_key()
    params = {
        'crtfcKey': api_key,
        'dataType': 'json',
        'searchCnt': search_cnt,
    }
    if lclass_id:
        params['searchLclasId'] = lclass_id

    last_exc = None
    for attempt in range(1, 4):
        try:
            response = requests.get(BIZINFO_API_URL, params=params, timeout=30)
            response.raise_for_status()
            break
        except Exception as exc:
            last_exc = exc
            logger.warning('bizinfo API 요청 실패 (시도 %d/3) lclass=%s: %s', attempt, lclass_id, exc)
            if attempt < 3:
                time.sleep(2 ** attempt)
    else:
        raise RuntimeError(f'bizinfo API 최대 재시도 횟수 초과 (lclass={lclass_id})') from last_exc

    try:
        data = response.json()
    except ValueError:
        # XML/RSS 응답 fallback
        import xml.etree.ElementTree as ET
        root = ET.fromstring(response.text)
        items = []
        for item_el in root.iter('item'):
            record = {child.tag: child.text for child in item_el}
            items.append(_parse_program(record))
        logger.info('bizinfo XML 응답 파싱 완료: %d건 (lclass=%s)', len(items), lclass_id)
        return items

    # JSON 응답: 실제 API는 {'jsonArray': [...]} 구조 반환
    # fallback: 구버전 래핑 형식도 허용
    raw_items = (
        data.get('jsonArray')
        or data.get('result')
        or data.get('response', {}).get('body', {}).get('items', {}).get('item', [])
        or []
    )
    if isinstance(raw_items, dict):
        raw_items = [raw_items]

    items = [_parse_program(item) for item in raw_items]
    logger.info('bizinfo 조회 완료: %d건 (lclass=%s)', len(items), lclass_id)
    return items


def fetch_all_programs() -> list[dict]:
    """전체 분야(01~07, 09)를 순회하며 사업목록 수집."""
    all_items: list[dict] = []
    seen_ids: set[str] = set()

    for lclass_id in list(LCLASS_CATEGORY_MAP.keys()):
        try:
            items = fetch_programs(lclass_id=lclass_id, search_cnt=500)
            for item in items:
                uid = item.get('announcement_no') or ''
                if uid and uid in seen_ids:
                    continue
                if uid:
                    seen_ids.add(uid)
                all_items.append(item)
        except Exception as exc:
            logger.error('bizinfo 분야 %s 수집 실패: %s', lclass_id, exc)

    logger.info('bizinfo 전체 수집 완료: %d건 (중복 제거 후)', len(all_items))
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
