"""수혜이력 CSV 로더.

S3 raw/welfare/ 에 업로드된 CSV 파일을 읽어
processed/labels.parquet 으로 변환·저장한다.

출력 스키마:
    company_id  (str)  — 사업자등록번호
    program_id  (str)  — 사업공고 ID
    selected    (int)  — 선정여부 (1: 선정, 0: 미선정)
    year        (int)  — 수혜 연도
"""
import io
import logging
import os

import boto3
import pandas as pd

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 입력 CSV 컬럼명 → 출력 스키마 매핑 후보
COLUMN_ALIASES = {
    'company_id': ['company_id', '사업자등록번호', '기업id', '기업ID'],
    'program_id': ['program_id', '사업공고ID', '사업공고id', '공고번호', '사업ID'],
    'selected':   ['selected', '선정여부', '수혜여부'],
    'year':       ['year', '수혜연도', '연도'],
}


def _get_s3_client():
    return boto3.client('s3')


def _resolve_column(df: pd.DataFrame, field: str) -> str | None:
    """COLUMN_ALIASES를 활용해 DataFrame에서 실제 컬럼명을 탐색."""
    for alias in COLUMN_ALIASES.get(field, [field]):
        if alias in df.columns:
            return alias
    return None


def list_welfare_files(bucket: str) -> list[str]:
    """S3 raw/welfare/ 경로의 파일 목록 반환.

    Returns:
        S3 키 문자열 목록 (CSV 파일만)
    """
    s3 = _get_s3_client()
    paginator = s3.get_paginator('list_objects_v2')
    keys = []
    for page in paginator.paginate(Bucket=bucket, Prefix='raw/welfare/'):
        for obj in page.get('Contents', []):
            key = obj['Key']
            if key.endswith('.csv'):
                keys.append(key)
    logger.info('welfare 파일 목록: %d개', len(keys))
    return keys


def load_csv_from_s3(bucket: str, key: str) -> pd.DataFrame:
    """S3에서 CSV를 읽어 DataFrame 반환."""
    s3 = _get_s3_client()
    obj = s3.get_object(Bucket=bucket, Key=key)
    body = obj['Body'].read()
    # UTF-8 → EUC-KR 순서로 인코딩 시도
    for encoding in ('utf-8-sig', 'utf-8', 'euc-kr', 'cp949'):
        try:
            df = pd.read_csv(io.BytesIO(body), encoding=encoding)
            logger.info('CSV 로드 완료: %s (%s, %d행)', key, encoding, len(df))
            return df
        except (UnicodeDecodeError, pd.errors.ParserError):
            continue
    raise ValueError(f'지원되지 않는 인코딩 형식: {key}')


def transform_labels(df: pd.DataFrame) -> pd.DataFrame:
    """컬럼 정규화 및 타입 변환.

    - 컬럼명 매핑 (COLUMN_ALIASES 기반)
    - company_id, program_id: str
    - selected: int (0 또는 1)
    - year: int
    - 결측값은 None 유지 (임의 대체 금지)

    Returns:
        정규화된 DataFrame (columns: company_id, program_id, selected, year)
    """
    rename_map = {}
    for field in ('company_id', 'program_id', 'selected', 'year'):
        col = _resolve_column(df, field)
        if col is None:
            logger.warning('컬럼 매핑 실패: %s — 해당 컬럼 없음', field)
            continue
        if col != field:
            rename_map[col] = field

    df = df.rename(columns=rename_map)

    required = ['company_id', 'program_id', 'selected', 'year']
    for col in required:
        if col not in df.columns:
            logger.warning('필수 컬럼 누락: %s', col)
            df[col] = None

    df = df[required].copy()

    # 타입 변환 (결측값 None 유지)
    df['company_id'] = df['company_id'].astype(str).where(df['company_id'].notna(), other=None)
    df['program_id'] = df['program_id'].astype(str).where(df['program_id'].notna(), other=None)

    def _to_int_nullable(series: pd.Series) -> pd.Series:
        result = pd.to_numeric(series, errors='coerce')
        return result.where(result.notna(), other=None).astype('Int64')

    df['selected'] = _to_int_nullable(df['selected'])
    df['year'] = _to_int_nullable(df['year'])

    # selected 값 검증: 0 또는 1만 허용
    invalid_mask = df['selected'].notna() & ~df['selected'].isin([0, 1])
    if invalid_mask.any():
        logger.warning('selected 컬럼에 유효하지 않은 값 %d건 → None 처리', invalid_mask.sum())
        df.loc[invalid_mask, 'selected'] = None

    logger.info('레이블 변환 완료: %d행', len(df))
    return df


def save_parquet_to_s3(df: pd.DataFrame, bucket: str, key: str) -> None:
    """DataFrame을 Parquet으로 변환하여 S3에 저장."""
    s3 = _get_s3_client()
    buffer = io.BytesIO()
    df.to_parquet(buffer, index=False, engine='pyarrow')
    buffer.seek(0)
    s3.put_object(Bucket=bucket, Key=key, Body=buffer.getvalue(), ContentType='application/octet-stream')
    logger.info('Parquet 저장 완료: s3://%s/%s (%d행)', bucket, key, len(df))


def run_loading() -> None:
    """메인 실행 함수.

    S3 raw/welfare/ 의 모든 CSV를 통합하여
    processed/labels.parquet 으로 저장한다.
    """
    bucket = os.environ.get('S3_BUCKET_NAME')
    if not bucket:
        raise ValueError('S3_BUCKET_NAME 환경변수가 설정되지 않았습니다.')

    logger.info('welfare 로딩 시작')
    keys = list_welfare_files(bucket)

    if not keys:
        logger.warning('raw/welfare/ 에 CSV 파일이 없습니다.')
        return

    frames = []
    for key in keys:
        try:
            df = load_csv_from_s3(bucket, key)
            frames.append(df)
        except Exception as exc:
            logger.error('CSV 로드 실패 key=%s: %s', key, exc)

    if not frames:
        logger.error('처리 가능한 CSV 파일이 없습니다.')
        return

    combined = pd.concat(frames, ignore_index=True)
    logger.info('CSV 통합 완료: 총 %d행', len(combined))

    labels = transform_labels(combined)
    save_parquet_to_s3(labels, bucket, 'processed/labels.parquet')
    logger.info('welfare 로딩 완료')


def load_welfare_task(**context) -> None:
    """Airflow PythonOperator에서 호출 가능한 callable."""
    run_loading()


if __name__ == '__main__':
    run_loading()
