import io
import json
import logging
import os
import re

import boto3
import pandas as pd

from agents.state import PolicyFundState

try:
    from src.embedder import PolicyVectorStore
except ImportError:
    PolicyVectorStore = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

_CHROMA_DIR = os.environ.get('CHROMA_DB_PATH', './chroma_db')

# lifespan에서 주입되는 싱글턴 — 매 요청마다 새 인스턴스 생성 방지
_shared_vector_store = None


def set_vector_store(store) -> None:
    """FastAPI lifespan에서 초기화된 PolicyVectorStore 인스턴스를 주입한다."""
    global _shared_vector_store
    _shared_vector_store = store
_PROGRAM_FEATURES_KEY = 'processed/program_features.parquet'
_MAX_AGE = 999


def _company_to_user_profile(company: dict) -> dict:
    """
    PolicyFundState.company_features → PolicyVectorStore._hard_filter용 user_profile 변환.

    industry_section은 company_features에 직접 있으면 사용하고,
    없으면 industry_code 첫 문자가 알파벳인 경우(e.g., "C2620") 추출.
    """
    industry_code = str(company.get('industry_code', '') or '')
    industry_section = company.get('industry_section', '') or ''
    if not industry_section and industry_code and industry_code[0].isalpha():
        industry_section = industry_code[0].upper()

    return {
        'company_type': company.get('company_type', '중소기업') or '중소기업',
        'industry_code': industry_code,
        'industry_section': industry_section,
        'region': company.get('region', '') or '',
        'business_age': int(company.get('business_age', 0) or 0),
        'revenue': int(company.get('revenue', 0) or 0),
        'export_usd': int(company.get('export_usd', 0) or 0),
        'employees': int(company.get('employee_count', 0) or 0),
        'debt_ratio': company.get('debt_ratio'),
    }


def hard_filter(company: dict, programs: list[dict]) -> list[dict]:
    """
    S3 parquet의 프로그램 dict 리스트에 Hard Filter 적용.

    아래 조건 중 하나라도 미충족 시 제외:
    - 업종코드가 program의 industry_limit 항목에 prefix 일치
    - 부채비율 > program의 debt_ratio_limit
    - 업력 > program의 max_business_age (필드 없으면 제한 없음으로 처리)

    Args:
        company: company_features dict (PolicyFundState 필드)
        programs: program_features.parquet의 row dict 리스트

    Returns:
        통과한 프로그램 dict 리스트
    """
    industry_code = str(company.get('industry_code', '') or '')
    industry_section = company.get('industry_section', '') or ''
    if not industry_section and industry_code and industry_code[0].isalpha():
        industry_section = industry_code[0].upper()

    business_age = int(company.get('business_age', 0) or 0)
    debt_ratio = company.get('debt_ratio')

    passed = []
    for prog in programs:
        fail = False

        # 업종 제한 체크
        industry_limit = prog.get('industry_limit') or []
        if isinstance(industry_limit, str):
            try:
                industry_limit = json.loads(industry_limit)
            except (json.JSONDecodeError, ValueError):
                industry_limit = [industry_limit] if industry_limit else []

        for limit in industry_limit:
            limit_str = str(limit).strip()
            if not limit_str:
                continue
            # 코드 prefix 일치 또는 섹션 알파벳 일치
            if industry_code and industry_code.startswith(limit_str):
                fail = True
                break
            if industry_section and industry_section == limit_str:
                fail = True
                break

        if fail:
            continue

        # 부채비율 상한 체크
        debt_limit = prog.get('debt_ratio_limit')
        if debt_ratio is not None and debt_limit is not None:
            try:
                if float(debt_ratio) > float(debt_limit):
                    fail = True
            except (TypeError, ValueError):
                pass

        if fail:
            continue

        # 업력 상한 체크 (필드 없으면 제한 없음)
        max_age = prog.get('max_business_age')
        if max_age is not None:
            try:
                if business_age > int(max_age):
                    fail = True
            except (TypeError, ValueError):
                pass

        if not fail:
            passed.append(prog)

    logger.info("hard_filter: %d/%d programs passed", len(passed), len(programs))
    return passed


def _build_query(company: dict) -> str:
    """기업 특허 키워드와 업종·지역으로 임베딩 검색 쿼리 생성."""
    parts = []

    region = company.get('region', '')
    if region:
        parts.append(region)

    industry_section = company.get('industry_section', '') or ''
    if not industry_section:
        code = str(company.get('industry_code', '') or '')
        if code and code[0].isalpha():
            industry_section = code[0].upper()
    if industry_section:
        parts.append(industry_section)

    patent_keywords = company.get('patent_keywords', '')
    if patent_keywords:
        parts.append(patent_keywords)

    parts.append('지원사업')
    return ' '.join(parts)


def _load_programs_from_s3() -> list[dict]:
    """S3에서 program_features.parquet 로드 후 dict 리스트 반환. 실패 시 빈 리스트."""
    bucket = os.environ.get('S3_BUCKET_NAME', '')
    if not bucket:
        logger.warning("S3_BUCKET_NAME 환경변수 미설정 — 프로그램 목록 S3 로드 건너뜀")
        return []
    try:
        s3 = boto3.client('s3')
        obj = s3.get_object(Bucket=bucket, Key=_PROGRAM_FEATURES_KEY)
        df = pd.read_parquet(io.BytesIO(obj['Body'].read()))
        logger.info("S3 program_features.parquet 로드 완료: %d rows", len(df))
        return df.where(pd.notna(df), None).to_dict(orient='records')
    except Exception as exc:
        logger.error("S3 program_features.parquet 로드 실패: %s", exc)
        return []


def embedding_node(state: PolicyFundState) -> PolicyFundState:
    """
    임베딩 에이전트 LangGraph 노드.

    1. S3에서 program_features.parquet 로드
    2. Hard Filter 실행 (업종·부채비율·업력)
    3. PolicyVectorStore로 Soft Filter 실행 (기업 업종·지역·특허 키워드 쿼리)
    4. green+yellow 후보를 program_id 기준으로 교집합 → candidate_programs
    5. 후보 없으면 error 설정 후 조기 복귀
    """
    company = state.get('company_features') or {}
    company_id = state.get('company_id', '')

    # 1. S3 로드
    programs = _load_programs_from_s3()

    # 2. Hard Filter
    if programs:
        hard_passed = hard_filter(company, programs)
        hard_passed_ids: set[str] | None = {
            str(p.get('program_id')) for p in hard_passed
        }
        parquet_by_id: dict[str, dict] = {
            str(p.get('program_id')): p for p in hard_passed
        }
    else:
        hard_passed_ids = None
        parquet_by_id = {}

    # 3. PolicyVectorStore Soft Filter
    user_profile = _company_to_user_profile(company)
    query = _build_query(company)

    store = _shared_vector_store
    if store is None:
        if PolicyVectorStore is None:
            logger.warning("PolicyVectorStore 미초기화 — src.embedder import 실패, Soft Filter 건너뜀")
        else:
            logger.warning("PolicyVectorStore 미초기화 — lifespan 주입 전, Soft Filter 건너뜀")
        return {
            **state,
            'candidate_programs': [],
            'error': 'PolicyVectorStore 미초기화',
        }

    try:
        result = store.search_for_agent(user_profile, query, top_k=20)
    except Exception as exc:
        logger.error("PolicyVectorStore 검색 실패: %s", exc)
        return {
            **state,
            'candidate_programs': [],
            'error': f"embedding_node 오류: {exc}",
        }

    green_items = result.get('green', [])
    yellow_items = result.get('yellow', [])
    green_set = set(id(item) for item in green_items)

    # 4. 교집합 필터링 및 candidate_programs 변환
    candidates = []
    for item in green_items + yellow_items:
        meta = item['meta_data']
        pid = str(meta.get('program_id', ''))

        if hard_passed_ids is not None and pid not in hard_passed_ids:
            continue

        parquet_row = parquet_by_id.get(pid, {})
        candidates.append({
            'program_id': pid,
            'program_name': parquet_row.get('program_name', meta.get('announcement_title', '')),
            'announcement_title': meta.get('announcement_title', ''),
            'category': meta.get('category', ''),
            'max_support': meta.get('max_support', 0),
            'interest_rate': meta.get('interest_rate', ''),
            'apply_start': meta.get('apply_start', ''),
            'apply_end': meta.get('apply_end', ''),
            'embedding_score': item['score'],
            'filter_status': 'green' if id(item) in green_set else 'yellow',
            'reasons': item.get('reasons', []),
            'caution_notes': item.get('caution_notes', []),
        })

    logger.info(
        "embedding_node: company_id=%s candidates=%d (green=%d, yellow=%d)",
        company_id, len(candidates), len(green_items), len(yellow_items),
    )

    # 5. 후보 없음 — 조기 복귀
    if not candidates:
        logger.warning("embedding_node: 후보 사업 없음 — 조기 복귀")
        return {
            **state,
            'candidate_programs': [],
            'error': '자격 요건을 충족하는 정책자금 후보가 없습니다.',
        }

    return {**state, 'candidate_programs': candidates, 'error': None}
