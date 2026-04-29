"""KIPRIS Open API extractor — 특허/실용신안 수집 모듈.

출원인명 기반으로 특허 목록(발명 명칭, IPC 코드, 요약, 출원일)을 수집한다.
API: http://plus.kipris.or.kr/kipo-api/kipi/patUtiModInfoSearchSevice/getWordSearch
인증: ServiceKey (KIPRIS Open API 키)
--mock 플래그 또는 KIPRIS_API_KEY 미설정 시 mock 데이터로 자동 전환.
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

KIPRIS_API_URL = 'http://plus.kipris.or.kr/kipo-api/kipi/patUtiModInfoSearchSevice/getWordSearch'

MOCK_PATENTS = [
    {
        'application_number': '1020230001234',
        'title': '인공지능 기반 중소기업 신용평가 시스템 및 방법',
        'abstract': '본 발명은 머신러닝 알고리즘을 활용하여 중소기업의 재무 데이터와 비재무 데이터를 통합 분석함으로써 신용을 평가하는 시스템 및 방법에 관한 것이다.',
        'application_date': '2023-01-15',
        'registration_date': '2023-09-20',
        'ipc_code': 'G06Q 40/03',
        'applicant': '(주)테크스타트',
        'status': '등록',
    },
    {
        'application_number': '1020230005678',
        'title': '스마트 공장 IoT 센서 데이터 실시간 모니터링 장치',
        'abstract': '본 발명은 제조 현장의 다수 IoT 센서로부터 실시간 데이터를 수집하고, 이상 징후를 조기 탐지하여 생산 효율을 극대화하는 모니터링 장치에 관한 것이다.',
        'application_number': '1020230005678',
        'application_date': '2023-03-22',
        'registration_date': '2023-11-10',
        'ipc_code': 'H04L 67/12',
        'applicant': '(주)테크스타트',
        'status': '등록',
    },
    {
        'application_number': '1020220089012',
        'title': '친환경 수처리 필터 제조 방법',
        'abstract': '본 발명은 생분해성 소재를 활용한 고효율 수처리 필터의 제조 방법에 관한 것으로, 기존 필터 대비 처리 효율을 30% 이상 향상시킨다.',
        'application_date': '2022-07-05',
        'registration_date': '2023-02-14',
        'ipc_code': 'B01D 39/14',
        'applicant': '(주)테크스타트',
        'status': '등록',
    },
    {
        'application_number': '1020230012345',
        'title': '블록체인 기반 공급망 이력 관리 플랫폼',
        'abstract': '본 발명은 블록체인 분산원장 기술을 이용하여 제품의 생산부터 유통까지 전 과정의 이력을 투명하게 기록·관리하는 플랫폼에 관한 것이다.',
        'application_date': '2023-05-18',
        'registration_date': None,
        'ipc_code': 'G06Q 10/0833',
        'applicant': '(주)테크스타트',
        'status': '출원',
    },
    {
        'application_number': '1020210067890',
        'title': '딥러닝 기반 제품 불량 검사 장치 및 방법',
        'abstract': '본 발명은 컨볼루션 신경망(CNN)을 이용하여 생산 라인에서 실시간으로 제품 외관의 불량 여부를 자동 판별하는 장치 및 방법에 관한 것이다.',
        'application_date': '2021-11-30',
        'registration_date': '2022-08-25',
        'ipc_code': 'G06V 10/82',
        'applicant': '(주)테크스타트',
        'status': '등록',
    },
]


def _get_s3_client():
    return boto3.client('s3')


def fetch_patents_mock(corp_name: str) -> list[dict]:
    """Mock 특허 데이터 반환 (KIPRIS_API_KEY 없을 때 사용)."""
    logger.info('[MOCK] %s 특허 데이터 반환 (mock 모드)', corp_name)
    mock = []
    for patent in MOCK_PATENTS:
        item = dict(patent)
        item['applicant'] = corp_name
        mock.append(item)
    return mock


def fetch_patents_real(corp_name: str, api_key: str, max_rows: int = 100) -> list[dict]:
    """KIPRIS Open API 출원인명 검색으로 특허/실용신안 목록을 수집한다.

    Args:
        corp_name: 출원인명 (기업명)
        api_key: KIPRIS Open API accessToken
        max_rows: 최대 수집 건수

    Returns:
        특허 목록 딕셔너리 리스트 (발명명칭, IPC코드, 요약, 출원일 포함)
    """
    import xml.etree.ElementTree as ET

    params = {
        'word': corp_name,
        'ServiceKey': api_key,
        'numOfRows': max_rows,
        'pageNo': 1,
    }

    last_exc = None
    for attempt in range(1, 4):
        try:
            response = requests.get(KIPRIS_API_URL, params=params, timeout=30)
            response.raise_for_status()
            break
        except Exception as exc:
            last_exc = exc
            logger.warning('KIPRIS API 요청 실패 (시도 %d/3) corp=%s: %s', attempt, corp_name, exc)
            if attempt < 3:
                time.sleep(2 ** attempt)
    else:
        logger.error('KIPRIS API 최대 재시도 초과: corp=%s', corp_name)
        raise RuntimeError(f'KIPRIS API 최대 재시도 횟수 초과 (corp={corp_name})') from last_exc

    try:
        root = ET.fromstring(response.text)
    except ET.ParseError as exc:
        logger.error('KIPRIS XML 파싱 실패 corp=%s: %s\n응답: %s', corp_name, exc, response.text[:300])
        raise

    # 에러 코드 확인
    result_code_el = root.find('.//resultCode')
    if result_code_el is not None and result_code_el.text not in ('00', '0'):
        result_msg = (root.findtext('.//resultMsg') or '').strip()
        logger.error('KIPRIS API 오류 응답 corp=%s: code=%s msg=%s', corp_name, result_code_el.text, result_msg)
        return []

    patents = []
    for item in root.iter('item'):
        def _text(tag: str) -> str | None:
            el = item.find(tag)
            return el.text.strip() if el is not None and el.text else None

        patents.append({
            'application_number': _text('applicationNumber'),
            'title': _text('inventionTitle'),
            'abstract': _text('astrtCont'),
            'application_date': _text('applicationDate'),
            'registration_date': _text('registerDate'),
            'ipc_code': _text('ipcNumber'),
            'applicant': _text('applicantName'),
            'status': _text('registerStatus'),
        })

    logger.info('KIPRIS API 조회 완료: %s → %d건', corp_name, len(patents))
    return patents


def fetch_patents(corp_name: str, mock: bool = False) -> list[dict]:
    """API 키 유무 또는 mock 플래그에 따라 실제 API 또는 mock 데이터로 자동 분기."""
    if mock:
        return fetch_patents_mock(corp_name)
    api_key = os.environ.get('KIPRIS_API_KEY', '').strip()
    if api_key:
        return fetch_patents_real(corp_name, api_key)
    logger.warning('KIPRIS_API_KEY 미설정 — mock 모드로 전환')
    return fetch_patents_mock(corp_name)


def upload_to_s3(data: dict | list, bucket: str, key: str) -> None:
    """딕셔너리/리스트를 JSON으로 S3에 업로드."""
    s3 = _get_s3_client()
    body = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType='application/json')
    logger.info('S3 업로드 완료: s3://%s/%s', bucket, key)


def run_extraction(corp_names: list[str] | None = None, mock: bool = False) -> None:
    """메인 실행 함수.

    Args:
        corp_names: 수집할 기업명 목록. None이면 샘플 기업 사용.
        mock: True이면 실제 API 호출 없이 mock 데이터 반환.
    """
    bucket = os.environ.get('S3_BUCKET_NAME')
    if not bucket:
        raise ValueError('S3_BUCKET_NAME 환경변수가 설정되지 않았습니다.')

    if corp_names is None:
        corp_names = ['(주)테크스타트', '(주)스마트팩토리']

    date_str = datetime.now().strftime('%Y-%m-%d')
    logger.info('KIPRIS 수집 시작: %d개 기업, date=%s', len(corp_names), date_str)

    success, failure = 0, 0
    for corp_name in corp_names:
        try:
            patents = fetch_patents(corp_name, mock=mock)
            record = {
                'corp_name': corp_name,
                'date': date_str,
                'patent_count': len(patents),
                'patents': patents,
            }
            safe_name = corp_name.replace('/', '_').replace(' ', '_')
            key = f'raw/kipris/{date_str}/{safe_name}.json'
            upload_to_s3(record, bucket, key)
            success += 1
        except Exception as exc:
            logger.error('KIPRIS 수집 실패 corp_name=%s: %s', corp_name, exc)
            failure += 1

    logger.info('KIPRIS 수집 완료: 성공=%d, 실패=%d', success, failure)


def extract_kipris_task(**context) -> None:
    """Airflow PythonOperator에서 호출 가능한 callable."""
    corp_names = context.get('op_kwargs', {}).get('corp_names')
    run_extraction(corp_names=corp_names)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='KIPRIS 특허/실용신안 수집기')
    parser.add_argument('--mock', action='store_true', help='mock 모드 (실제 API 호출 없이 구조 검증)')
    args = parser.parse_args()
    run_extraction(mock=args.mock)
