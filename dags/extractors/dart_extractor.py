"""OpenDART API extractor.

수집 항목:
- 재무제표: 매출액, 영업이익, 자본금, 부채비율, 당기순이익, 영업활동현금흐름
- 기업 기본정보: 업종코드(KSIC), 설립일, 종업원수, 소재지
"""
import io
import json
import logging
import os
import time
import zipfile
from datetime import datetime, timedelta

import boto3
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_URL = 'https://opendart.fss.or.kr/api'

# 재무제표 계정명 → 스키마 필드 매핑
ACCOUNT_MAP = {
    '매출액': 'revenue',
    '영업이익': 'operating_profit',
    '자본금': 'capital',
    '당기순이익': 'net_income',
    '영업활동으로인한현금흐름': 'operating_cash_flow',
    '영업활동 현금흐름': 'operating_cash_flow',
}


def _get_api_key() -> str:
    key = os.environ.get('OPENDART_API_KEY')
    if not key:
        raise ValueError('OPENDART_API_KEY 환경변수가 설정되지 않았습니다.')
    return key


def _get_s3_client():
    return boto3.client('s3')


def _request_with_retry(url: str, params: dict, max_retries: int = 3, backoff: float = 2.0) -> dict:
    """최대 max_retries회 재시도하는 HTTP GET 요청."""
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            status = data.get('status', '000')
            if status not in ('000', '013'):  # 013: 조회 결과 없음
                raise ValueError(f'OpenDART API 오류: status={status}, message={data.get("message")}')
            return data
        except Exception as exc:
            last_exc = exc
            logger.warning('요청 실패 (시도 %d/%d): %s — %s', attempt, max_retries, url, exc)
            if attempt < max_retries:
                time.sleep(backoff ** attempt)
    raise RuntimeError(f'최대 재시도 횟수 초과: {url}') from last_exc


def fetch_corp_list() -> list[dict]:
    """전체 기업 목록 조회 (company_list API → zip 다운로드)."""
    api_key = _get_api_key()
    url = f'{BASE_URL}/corpCode.xml'
    logger.info('기업 목록 다운로드 시작')
    for attempt in range(1, 4):
        try:
            response = requests.get(url, params={'crtfc_key': api_key}, timeout=60)
            response.raise_for_status()
            break
        except Exception as exc:
            logger.warning('기업 목록 다운로드 실패 (시도 %d/3): %s', attempt, exc)
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)

    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        xml_data = zf.read('CORPCODE.xml').decode('utf-8')

    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml_data)
    corps = []
    for item in root.findall('list'):
        corp_code = item.findtext('corp_code', '').strip()
        corp_name = item.findtext('corp_name', '').strip()
        stock_code = item.findtext('stock_code', '').strip()
        if corp_code:
            corps.append({
                'corp_code': corp_code,
                'corp_name': corp_name,
                'stock_code': stock_code or None,
            })

    logger.info('기업 목록 조회 완료: %d건', len(corps))
    return corps


def fetch_financial_statements(corp_code: str, year: int) -> dict:
    """단일기업 전체 재무제표 조회 (fnlttSinglAcntAll).

    Args:
        corp_code: DART 고유번호
        year: 사업연도 (예: 2023)

    Returns:
        재무 지표 딕셔너리 (결측값 None 유지)
    """
    api_key = _get_api_key()
    params = {
        'crtfc_key': api_key,
        'corp_code': corp_code,
        'bsns_year': str(year),
        'reprt_code': '11011',  # 사업보고서
        'fs_div': 'CFS',        # 연결재무제표 (없으면 OFS로 fallback)
    }

    result = {
        'revenue': None,
        'operating_profit': None,
        'capital': None,
        'debt_ratio': None,
        'net_income': None,
        'operating_cash_flow': None,
    }

    try:
        data = _request_with_retry(f'{BASE_URL}/fnlttSinglAcntAll.json', params)
    except Exception:
        # 연결재무제표 없으면 개별재무제표 시도
        params['fs_div'] = 'OFS'
        try:
            data = _request_with_retry(f'{BASE_URL}/fnlttSinglAcntAll.json', params)
        except Exception as exc:
            logger.warning('재무제표 조회 실패 corp_code=%s year=%d: %s', corp_code, year, exc)
            return result

    items = data.get('list', [])
    account_values: dict[str, int | None] = {}

    for item in items:
        acnt_nm = item.get('account_nm', '').replace(' ', '')
        for key, field in ACCOUNT_MAP.items():
            if key.replace(' ', '') == acnt_nm:
                raw = item.get('thstrm_amount', '')
                try:
                    account_values[field] = int(str(raw).replace(',', ''))
                except (ValueError, TypeError):
                    account_values[field] = None

    result.update(account_values)

    # 부채비율 계산: (총부채 / 자기자본) × 100
    total_debt = None
    equity = None
    for item in items:
        nm = item.get('account_nm', '')
        if nm == '부채총계':
            try:
                total_debt = int(str(item.get('thstrm_amount', '')).replace(',', ''))
            except (ValueError, TypeError):
                pass
        elif nm == '자본총계':
            try:
                equity = int(str(item.get('thstrm_amount', '')).replace(',', ''))
            except (ValueError, TypeError):
                pass

    if total_debt is not None and equity and equity != 0:
        result['debt_ratio'] = round(total_debt / equity * 100, 2)

    return result


def fetch_company_info(corp_code: str) -> dict:
    """기업 기본정보 조회 (company API).

    Returns:
        업종코드(KSIC), 설립일, 종업원수, 소재지
    """
    api_key = _get_api_key()
    params = {'crtfc_key': api_key, 'corp_code': corp_code}

    result = {
        'industry_code': None,
        'founding_date': None,
        'employee_count': None,
        'region': None,
    }

    try:
        data = _request_with_retry(f'{BASE_URL}/company.json', params)
    except Exception as exc:
        logger.warning('기업 기본정보 조회 실패 corp_code=%s: %s', corp_code, exc)
        return result

    raw = data if isinstance(data, dict) else {}
    result['industry_code'] = raw.get('induty_code') or None
    result['founding_date'] = raw.get('est_dt') or None
    result['region'] = raw.get('adres') or None

    emp_raw = raw.get('enpyr_enpls_cnt')
    try:
        result['employee_count'] = int(str(emp_raw).replace(',', '')) if emp_raw else None
    except (ValueError, TypeError):
        result['employee_count'] = None

    return result


def upload_to_s3(data: dict | list, bucket: str, key: str) -> None:
    """딕셔너리/리스트를 JSON으로 S3에 업로드."""
    s3 = _get_s3_client()
    body = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType='application/json')
    logger.info('S3 업로드 완료: s3://%s/%s', bucket, key)


def run_extraction(year: int | None = None) -> None:
    """메인 실행 함수.

    Args:
        year: 수집 대상 사업연도. None이면 전년도 기준.
    """
    bucket = os.environ.get('S3_BUCKET_NAME')
    if not bucket:
        raise ValueError('S3_BUCKET_NAME 환경변수가 설정되지 않았습니다.')

    if year is None:
        year = datetime.now().year - 1

    date_str = datetime.now().strftime('%Y-%m-%d')
    logger.info('OpenDART 수집 시작: year=%d, date=%s', year, date_str)

    corps = fetch_corp_list()
    logger.info('수집 대상 기업 수: %d', len(corps))

    success, failure = 0, 0
    for corp in corps:
        corp_code = corp['corp_code']
        try:
            financial = fetch_financial_statements(corp_code, year)
            company_info = fetch_company_info(corp_code)
            record = {
                'corp_code': corp_code,
                'corp_name': corp['corp_name'],
                'stock_code': corp.get('stock_code'),
                'year': year,
                **financial,
                **company_info,
            }
            key = f'raw/dart/{date_str}/{corp_code}.json'
            upload_to_s3(record, bucket, key)
            success += 1
        except Exception as exc:
            logger.error('기업 수집 실패 corp_code=%s: %s', corp_code, exc)
            failure += 1

    logger.info('OpenDART 수집 완료: 성공=%d, 실패=%d', success, failure)


def extract_dart_task(**context) -> None:
    """Airflow PythonOperator에서 호출 가능한 callable."""
    year = context.get('op_kwargs', {}).get('year')
    run_extraction(year=year)


if __name__ == '__main__':
    run_extraction()
