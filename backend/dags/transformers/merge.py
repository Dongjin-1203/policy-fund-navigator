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


# ---------------------------------------------------------------------------
# ProgramFeatureMerger — 크롤러 metadata + LLM 파서 requirements_db JOIN
# ---------------------------------------------------------------------------

_PROGRAM_FEATURE_COLUMNS = [
    'program_id', 'program_name', 'category', 'max_support',
    'interest_rate', 'apply_start', 'apply_end',
    'industry_limit', 'debt_ratio_limit', 'requirements', 'source_date',
]

_MOCK_METADATA = [
    {
        'slno': '999',
        'title': '[mock] 2026년도 정책자금 융자사업 연간추진계획',
        'filename': '999_mock_2026_policy_fund.hwp',
        'date': '20260429',
        'collected_at': '2026-04-29T00:00:00',
        'gubun': 'WE17',
    },
    {
        'slno': '998',
        'title': '[mock] JOIN 실패 케이스',
        'filename': '998_no_parser_output.hwp',
        'date': '20260429',
        'collected_at': '2026-04-29T00:00:00',
        'gubun': 'WE17',
    },
]

# requirements_db 키: slno 문자열 (파일명 stem이 아닌 slno 직접 사용)
_MOCK_REQUIREMENTS_MAP = {
    '999': {
        'source_file': '999',
        'announcement_title': '[mock] 2026년도 정책자금 융자사업 연간추진계획',
        'programs': [
            {
                'sub_title': '[mock] 시설자금',
                'program_category': '금융',
                'industry_limit': ['불건전 영상게임기 제조업', '주점업'],
                'requirements': ['업력 7년 미만', '상시 근로자 5인 이상'],
                'max_support': 1000000000,
                'interest_rate': '연 2.9%',
                'apply_start': '2026-01-01',
                'apply_end': '2026-12-31',
                'debt_ratio_limit': 500,
            },
        ],
    }
}


class ProgramFeatureMerger:
    """크롤러 metadata와 LLM 파서 requirements_db를 filename 기준 LEFT JOIN하여
    program_features.parquet 생성.

    run() 호출 시:
      1. S3 raw/announcements/*/metadata.json 전체 로드
      2. S3 embeddings/requirements_db/*/*.json 전체 로드
      3. filename 기준 LEFT JOIN (실패 시 requirements 필드 None, logging.warning)
      4. programs 배열 explode → 1행 per program
      5. processed/program_features.parquet 로 저장
    """

    def __init__(self, mock: bool = False):
        self.mock = mock
        self._s3 = None
        self._bucket = None

    def _init_s3(self) -> None:
        if self._s3 is None:
            self._s3 = _get_s3_client()
            self._bucket = os.environ.get('S3_BUCKET_NAME')
            if not self._bucket:
                raise ValueError('S3_BUCKET_NAME 환경변수가 설정되지 않았습니다.')

    def _load_all_metadata(self) -> list[dict]:
        """S3 raw/announcements/*/metadata.json 모두 로드."""
        paginator = self._s3.get_paginator('list_objects_v2')
        all_items: list[dict] = []
        for page in paginator.paginate(Bucket=self._bucket, Prefix='raw/announcements/'):
            for obj in page.get('Contents', []):
                key = obj['Key']
                if not key.endswith('/metadata.json'):
                    continue
                try:
                    resp = self._s3.get_object(Bucket=self._bucket, Key=key)
                    items: list[dict] = json.loads(resp['Body'].read().decode('utf-8'))
                    all_items.extend(items)
                    logger.info('메타데이터 로드: %s (%d건)', key, len(items))
                except Exception as exc:
                    logger.warning('메타데이터 로드 실패 key=%s: %s', key, exc)
        logger.info('전체 메타데이터 로드 완료: %d건', len(all_items))
        return all_items

    def _load_all_requirements(self) -> dict[str, dict]:
        """S3 embeddings/requirements_db/*/*.json 모두 로드.

        중첩 포맷 (programs 배열 있음): 파일명 stem을 slno 키로 저장.
        평탄화 포맷 (programs 배열 없음): source_file 또는 stem의 _N suffix 제거로
            slno를 추출하고, 동일 slno의 파일들을 {'programs': [...]} 로 묶어서 저장.
        중첩 포맷과 평탄화 포맷이 같은 slno에 공존하면 중첩 포맷을 우선한다.

        Returns:
            {slno: {'programs': [...]}} 형태의 딕셔너리
        """
        paginator = self._s3.get_paginator('list_objects_v2')
        req_map: dict[str, dict] = {}
        flat_buffer: dict[str, list[dict]] = {}  # slno → 평탄화 레코드 리스트

        for page in paginator.paginate(Bucket=self._bucket, Prefix='embeddings/requirements_db/'):
            for obj in page.get('Contents', []):
                key = obj['Key']
                if not key.endswith('.json'):
                    continue
                try:
                    resp = self._s3.get_object(Bucket=self._bucket, Key=key)
                    data: dict = json.loads(resp['Body'].read().decode('utf-8'))
                    stem = key.split('/')[-1].rsplit('.', 1)[0]

                    if 'programs' in data:
                        # 중첩 포맷: 파일명 stem을 slno 키로 직접 사용
                        req_map[stem] = data
                    else:
                        # 평탄화 포맷: data['source_file'] 우선, 없으면 stem 끝의 _N 제거
                        slno = str(data.get('source_file', '') or '').strip() \
                               or stem.rsplit('_', 1)[0]
                        if slno:
                            flat_buffer.setdefault(slno, []).append(data)
                except Exception as exc:
                    logger.warning('requirements_db 로드 실패 key=%s: %s', key, exc)

        # 중첩 포맷이 없는 slno에 대해서만 평탄화 레코드를 programs 배열로 묶어서 등록
        for slno, flat_progs in flat_buffer.items():
            if slno not in req_map:
                req_map[slno] = {'programs': flat_progs}
            else:
                logger.debug(
                    'slno=%s: 중첩 포맷 우선 적용 (평탄화 파일 %d건 무시)',
                    slno, len(flat_progs),
                )

        logger.info('requirements_db 로드 완료: %d건', len(req_map))
        return req_map

    def _to_str(self, value) -> str | None:
        """list는 JSON 문자열로, 나머지는 str 변환. None/빈값은 None 유지."""
        if value is None:
            return None
        if isinstance(value, list):
            return json.dumps(value, ensure_ascii=False)
        return str(value) if value != '' else None

    def _build_rows(
        self,
        metadata: list[dict],
        req_map: dict[str, dict],
    ) -> tuple[list[dict], int, int]:
        """metadata와 requirements_db를 JOIN하여 출력 행 생성.

        Returns:
            (rows, join_ok_count, join_fail_count)
        """
        rows: list[dict] = []
        join_ok = join_fail = 0

        for item in metadata:
            slno = item.get('slno', '')
            filename = item.get('filename', '')
            title = item.get('title', '')
            collected_at = item.get('collected_at', '') or ''
            source_date = collected_at[:10] if collected_at else ''

            # slno를 직접 JOIN 키로 사용 (파일명 stem 비교 제거)
            req_data = req_map.get(slno)

            if req_data is None:
                logger.warning(
                    'JOIN 실패 — requirements_db 없음: slno=%s (filename=%s)',
                    slno, filename,
                )
                join_fail += 1
                rows.append({col: None for col in _PROGRAM_FEATURE_COLUMNS} | {
                    'program_id': slno,
                    'program_name': title,
                    'source_date': source_date,
                })
                continue

            join_ok += 1
            # programs 배열이 없으면 req_data 자체를 단일 프로그램으로 처리 (평탄화 포맷 폴백)
            programs: list[dict] = req_data.get('programs') or [req_data]
            multi = len(programs) > 1

            for i, prog in enumerate(programs):
                pid = f'{slno}_{i + 1}' if multi else slno
                rows.append({
                    'program_id': pid,
                    # 신 포맷: program_name / category, 구 포맷: sub_title / program_category
                    'program_name': (
                        prog.get('program_name') or prog.get('sub_title') or title
                    ),
                    'category': prog.get('category') or prog.get('program_category'),
                    'max_support': self._to_str(prog.get('max_support')),
                    'interest_rate': self._to_str(prog.get('interest_rate')),
                    'apply_start': prog.get('apply_start'),
                    'apply_end': prog.get('apply_end'),
                    # 구 포맷: industry_limit(텍스트 리스트), 신 포맷: target_industry_codes
                    'industry_limit': self._to_str(
                        prog.get('industry_limit') or prog.get('target_industry_codes')
                    ),
                    'debt_ratio_limit': self._to_str(prog.get('debt_ratio_limit')),
                    # 구 포맷: requirements(리스트), 신 포맷: target_company_types
                    'requirements': self._to_str(
                        prog.get('requirements') or prog.get('target_company_types')
                    ),
                    'source_date': source_date,
                })

        return rows, join_ok, join_fail

    def run(self) -> pd.DataFrame:
        """메인 실행.

        Returns:
            생성된 program_features DataFrame.
        """
        if self.mock:
            logger.info('[MOCK] ProgramFeatureMerger 실행 (S3 접근 없음)')
            metadata = _MOCK_METADATA
            req_map = _MOCK_REQUIREMENTS_MAP
        else:
            self._init_s3()
            metadata = self._load_all_metadata()
            req_map = self._load_all_requirements()

        if not metadata:
            logger.warning('metadata가 비어 있습니다. 처리 대상 없음.')
            return pd.DataFrame(columns=_PROGRAM_FEATURE_COLUMNS)

        rows, join_ok, join_fail = self._build_rows(metadata, req_map)

        df = pd.DataFrame(rows, columns=_PROGRAM_FEATURE_COLUMNS)

        logger.info(
            '수집 통계 — 전체 공고수: %d, JOIN 성공: %d, JOIN 실패: %d',
            len(metadata), join_ok, join_fail,
        )

        if not self.mock:
            save_features(df, self._bucket, 'processed/program_features.parquet')

        return df


def merge_program_features_task(**context) -> None:
    """Airflow PythonOperator에서 호출 가능한 callable."""
    ProgramFeatureMerger().run()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='S3 데이터 병합')
    parser.add_argument('--mock', action='store_true', help='mock 모드 (S3 접근 없이 구조 검증)')
    parser.add_argument('--program-features', action='store_true', help='ProgramFeatureMerger만 실행')
    args = parser.parse_args()

    if args.program_features:
        merger = ProgramFeatureMerger(mock=args.mock)
        df = merger.run()
        if args.mock:
            print('\n[DataFrame 컬럼 구조]')
            print(df.dtypes.to_string())
            print(f'\n[행 수] {len(df)}')
            print(df.to_string())
    else:
        run_merge()
