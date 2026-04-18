"""S3 raw 데이터 병합 transformer.

DART + KIPRIS 데이터 → company_features.parquet
bizinfo 데이터      → program_features.parquet

출력 스키마 (CLAUDE.md 참조):
    company_features: company_id, revenue, operating_profit, capital,
                      debt_ratio, industry_code, region, employee_count,
                      business_age, patent_count, is_venture, is_innobiz,
                      credit_grade
    program_features: program_id, program_name, category, max_support,
                      interest_rate, apply_start, apply_end,
                      industry_limit, debt_ratio_limit, requirements
"""
import io
import json
import logging
import os
from datetime import datetime, date

import boto3
import pandas as pd

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _get_s3_client():
    return boto3.client('s3')


def _calculate_business_age(founding_date: str | None) -> int | None:
    """설립일(YYYYMMDD 또는 YYYY-MM-DD)로부터 업력(년) 계산."""
    if not founding_date:
        return None
    try:
        for fmt in ('%Y%m%d', '%Y-%m-%d', '%Y.%m.%d'):
            try:
                fd = datetime.strptime(str(founding_date), fmt).date()
                break
            except ValueError:
                continue
        else:
            return None
        today = date.today()
        return (today - fd).days // 365
    except Exception:
        return None


def load_dart_data(bucket: str, date_str: str) -> pd.DataFrame:
    """S3 raw/dart/{date_str}/ 에서 모든 JSON 파일 로드.

    Args:
        bucket: S3 버킷명
        date_str: 날짜 문자열 (YYYY-MM-DD)

    Returns:
        DART 데이터 DataFrame
    """
    s3 = _get_s3_client()
    prefix = f'raw/dart/{date_str}/'
    paginator = s3.get_paginator('list_objects_v2')

    records = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get('Contents', []):
            key = obj['Key']
            if not key.endswith('.json'):
                continue
            try:
                response = s3.get_object(Bucket=bucket, Key=key)
                data = json.loads(response['Body'].read().decode('utf-8'))
                records.append(data)
            except Exception as exc:
                logger.warning('DART 파일 로드 실패 key=%s: %s', key, exc)

    if not records:
        logger.warning('DART 데이터 없음: prefix=%s', prefix)
        return pd.DataFrame()

    df = pd.DataFrame(records)
    logger.info('DART 데이터 로드 완료: %d건', len(df))
    return df


def load_kipris_data(bucket: str, date_str: str) -> pd.DataFrame:
    """S3 raw/kipris/{date_str}/ 에서 특허 데이터 로드.

    Args:
        bucket: S3 버킷명
        date_str: 날짜 문자열 (YYYY-MM-DD)

    Returns:
        corp_name, patent_count 컬럼을 가진 DataFrame
    """
    s3 = _get_s3_client()
    prefix = f'raw/kipris/{date_str}/'
    paginator = s3.get_paginator('list_objects_v2')

    records = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get('Contents', []):
            key = obj['Key']
            if not key.endswith('.json'):
                continue
            try:
                response = s3.get_object(Bucket=bucket, Key=key)
                data = json.loads(response['Body'].read().decode('utf-8'))
                records.append({
                    'corp_name': data.get('corp_name'),
                    'patent_count': data.get('patent_count', 0),
                })
            except Exception as exc:
                logger.warning('KIPRIS 파일 로드 실패 key=%s: %s', key, exc)

    if not records:
        logger.warning('KIPRIS 데이터 없음: prefix=%s', prefix)
        return pd.DataFrame(columns=['corp_name', 'patent_count'])

    df = pd.DataFrame(records)
    logger.info('KIPRIS 데이터 로드 완료: %d건', len(df))
    return df


def load_bizinfo_data(bucket: str, date_str: str) -> pd.DataFrame:
    """S3 raw/bizinfo/{date_str}/programs.json 로드.

    Args:
        bucket: S3 버킷명
        date_str: 날짜 문자열 (YYYY-MM-DD)

    Returns:
        bizinfo 사업목록 DataFrame
    """
    s3 = _get_s3_client()
    key = f'raw/bizinfo/{date_str}/programs.json'

    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        data = json.loads(response['Body'].read().decode('utf-8'))
        programs = data.get('programs', [])
        df = pd.DataFrame(programs)
        logger.info('bizinfo 데이터 로드 완료: %d건', len(df))
        return df
    except Exception as exc:
        logger.error('bizinfo 데이터 로드 실패 key=%s: %s', key, exc)
        return pd.DataFrame()


def build_company_features(dart_df: pd.DataFrame, kipris_df: pd.DataFrame) -> pd.DataFrame:
    """DART + KIPRIS 데이터로 company_features DataFrame 생성.

    Args:
        dart_df: DART 원시 데이터
        kipris_df: KIPRIS 특허 집계 데이터

    Returns:
        company_features 스키마의 DataFrame
    """
    if dart_df.empty:
        logger.warning('DART 데이터가 비어 있습니다.')
        return pd.DataFrame()

    df = dart_df.copy()

    # corp_code를 company_id로 사용
    df['company_id'] = df.get('corp_code', pd.Series(dtype=str)).astype(str)

    # 업력 계산
    if 'founding_date' in df.columns:
        df['business_age'] = df['founding_date'].apply(_calculate_business_age)
    else:
        df['business_age'] = None

    # KIPRIS 특허 수 병합 (corp_name 기준)
    if not kipris_df.empty and 'corp_name' in df.columns and 'corp_name' in kipris_df.columns:
        df = df.merge(kipris_df[['corp_name', 'patent_count']], on='corp_name', how='left')
    else:
        df['patent_count'] = None

    # 벤처/이노비즈 인증 여부 (DART 데이터에 없으면 None 유지)
    if 'is_venture' not in df.columns:
        df['is_venture'] = None
    if 'is_innobiz' not in df.columns:
        df['is_innobiz'] = None
    if 'credit_grade' not in df.columns:
        df['credit_grade'] = None

    output_columns = [
        'company_id', 'revenue', 'operating_profit', 'capital', 'debt_ratio',
        'industry_code', 'region', 'employee_count', 'business_age',
        'patent_count', 'is_venture', 'is_innobiz', 'credit_grade',
    ]
    for col in output_columns:
        if col not in df.columns:
            df[col] = None

    result = df[output_columns].copy()
    logger.info('company_features 생성 완료: %d행', len(result))
    return result


def build_program_features(bizinfo_df: pd.DataFrame) -> pd.DataFrame:
    """bizinfo 데이터로 program_features DataFrame 생성.

    Args:
        bizinfo_df: bizinfo 원시 데이터

    Returns:
        program_features 스키마의 DataFrame
    """
    if bizinfo_df.empty:
        logger.warning('bizinfo 데이터가 비어 있습니다.')
        return pd.DataFrame()

    df = bizinfo_df.copy()

    # announcement_no → program_id
    if 'announcement_no' in df.columns:
        df['program_id'] = df['announcement_no'].astype(str)
    elif 'program_id' not in df.columns:
        df['program_id'] = pd.RangeIndex(len(df)).astype(str)

    # 팀원 파싱 결과물(industry_limit, debt_ratio_limit, requirements)은 None 유지
    for col in ('max_support', 'interest_rate', 'industry_limit', 'debt_ratio_limit', 'requirements'):
        if col not in df.columns:
            df[col] = None

    output_columns = [
        'program_id', 'program_name', 'category', 'max_support', 'interest_rate',
        'apply_start', 'apply_end', 'industry_limit', 'debt_ratio_limit', 'requirements',
    ]
    for col in output_columns:
        if col not in df.columns:
            df[col] = None

    result = df[output_columns].copy()
    logger.info('program_features 생성 완료: %d행', len(result))
    return result


def save_features(df: pd.DataFrame, bucket: str, key: str) -> None:
    """DataFrame을 Parquet으로 변환하여 S3에 저장."""
    s3 = _get_s3_client()
    buffer = io.BytesIO()
    df.to_parquet(buffer, index=False, engine='pyarrow')
    buffer.seek(0)
    s3.put_object(Bucket=bucket, Key=key, Body=buffer.getvalue(), ContentType='application/octet-stream')
    logger.info('Parquet 저장 완료: s3://%s/%s (%d행)', bucket, key, len(df))


def run_merge(date_str: str | None = None) -> None:
    """메인 실행 함수.

    Args:
        date_str: 데이터 날짜 (YYYY-MM-DD). None이면 오늘 날짜 사용.
    """
    bucket = os.environ.get('S3_BUCKET_NAME')
    if not bucket:
        raise ValueError('S3_BUCKET_NAME 환경변수가 설정되지 않았습니다.')

    if date_str is None:
        date_str = datetime.now().strftime('%Y-%m-%d')

    logger.info('병합 시작: date=%s', date_str)

    dart_df = load_dart_data(bucket, date_str)
    kipris_df = load_kipris_data(bucket, date_str)
    bizinfo_df = load_bizinfo_data(bucket, date_str)

    company_features = build_company_features(dart_df, kipris_df)
    if not company_features.empty:
        save_features(company_features, bucket, 'processed/company_features.parquet')
    else:
        logger.warning('company_features가 비어 있어 저장 건너뜀')

    program_features = build_program_features(bizinfo_df)
    if not program_features.empty:
        save_features(program_features, bucket, 'processed/program_features.parquet')
    else:
        logger.warning('program_features가 비어 있어 저장 건너뜀')

    logger.info('병합 완료')


def merge_task(**context) -> None:
    """Airflow PythonOperator에서 호출 가능한 callable."""
    date_str = context.get('op_kwargs', {}).get('date_str')
    run_merge(date_str=date_str)


if __name__ == '__main__':
    run_merge()
