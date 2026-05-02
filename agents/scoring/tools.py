import re
import logging
from datetime import datetime
from typing import Tuple

logger = logging.getLogger(__name__)

# 재무 점수 부채비율 기준 상한 (일반 중소기업 통상 기준)
_DEBT_RATIO_BENCHMARK = 300.0
# 기술 점수 특허 정규화 기준
_PATENT_NORM = 5
# 정책 가점 가중치
_VENTURE_WEIGHT = 0.4
_INNOBIZ_WEIGHT = 0.3
_YOUTH_EMPLOYMENT_WEIGHT = 0.2
_CREDIT_GRADE_WEIGHT = 0.1


def calc_financial_score(company_features: dict) -> float:
    """재무 점수 계산 (0.0 ~ 1.0)

    구성:
    - 부채비율 (50%): 낮을수록 높은 점수, 300% 기준 정규화
    - 영업활동 현금흐름 (30%): 양수면 가점
    - 영업이익 (20%): 양수면 가점 (매출성장률 proxy)

    null 처리:
    - 핵심 재무 항목 전체 null → 비상장 기업 기본값 0.5 반환
    - 항목별 null → 해당 항목 0.0 처리
    """
    debt_ratio = company_features.get('debt_ratio')
    cash_flow = company_features.get('cash_flow')
    operating_profit = company_features.get('operating_profit')

    if debt_ratio is None and cash_flow is None and operating_profit is None:
        logger.debug("재무 데이터 전체 null — 기본값 0.5 반환 (company_id=%s)",
                     company_features.get('company_id'))
        return 0.5

    debt_score = (
        max(0.0, 1.0 - debt_ratio / _DEBT_RATIO_BENCHMARK)
        if debt_ratio is not None else 0.0
    )
    cash_score = 1.0 if (cash_flow is not None and cash_flow > 0) else 0.0
    profit_score = 1.0 if (operating_profit is not None and operating_profit > 0) else 0.0

    score = 0.5 * debt_score + 0.3 * cash_score + 0.2 * profit_score
    return round(min(1.0, score), 4)


def calc_tech_score(company_features: dict) -> float:
    """기술 점수 계산 (0.0 ~ 1.0)

    구성:
    - 특허 보유수 (60%): 5건 기준 정규화, 5건 이상 = 1.0
    - IPC 코드 존재 여부 (20%): 있으면 가점
    - 특허 출원일 최신성 (20%): 최근 3년 이내면 가점
    """
    patent_count = company_features.get('patent_count')
    ipc_codes = company_features.get('ipc_codes')
    latest_patent_year = company_features.get('latest_patent_year')

    patent_score = (
        min(1.0, patent_count / _PATENT_NORM)
        if (patent_count is not None and patent_count >= 0) else 0.0
    )
    ipc_score = 1.0 if ipc_codes else 0.0

    recency_score = 0.0
    if latest_patent_year is not None:
        current_year = datetime.now().year
        recency_score = 1.0 if (current_year - latest_patent_year) <= 3 else 0.0

    score = 0.6 * patent_score + 0.2 * ipc_score + 0.2 * recency_score
    return round(min(1.0, score), 4)


def calc_policy_score(company_features: dict) -> float:
    """정책 가점 계산 (0.0 ~ 1.0)

    구성:
    - 벤처기업 인증: 0.4점
    - 이노비즈 인증: 0.3점
    - 청년고용 여부: 0.2점
    - 신용등급 보유: 0.1점
    (합산 후 1.0 초과 시 cap)
    """
    score = 0.0
    if company_features.get('is_venture'):
        score += _VENTURE_WEIGHT
    if company_features.get('is_innobiz'):
        score += _INNOBIZ_WEIGHT
    if company_features.get('youth_employment'):
        score += _YOUTH_EMPLOYMENT_WEIGHT
    if company_features.get('credit_grade') is not None:
        score += _CREDIT_GRADE_WEIGHT
    return round(min(1.0, score), 4)


def load_scoring_params() -> Tuple[float, float, float]:
    """α, β, γ 파라미터 로드.

    Returns:
        (alpha, beta, gamma) — 합이 1.0

    TODO: MLflow 연동 시 아래로 교체
        import mlflow
        client = mlflow.tracking.MlflowClient()
        run = client.get_run(run_id)
        alpha = float(run.data.params['alpha'])
        ...
    """
    return 0.4, 0.3, 0.3


def generate_synthetic_label(
    company_features: dict,
    program_features: dict,
) -> int:
    """Hard Filter 조건 충족 여부 기반 합성 레이블 생성.

    선형회귀로 α, β, γ 추정 시 타겟값으로 사용.

    Returns:
        1: 모든 Hard Filter 조건 통과
        0: 하나 이상 미통과
    """
    industry_code = company_features.get('industry_code', '') or ''
    debt_ratio = company_features.get('debt_ratio')
    business_age = company_features.get('business_age', 0) or 0

    # 업종 제한 체크
    industry_limit = program_features.get('industry_limit') or []
    for limited in industry_limit:
        if limited and industry_code and limited in industry_code:
            logger.debug("업종 제한 미통과: industry_code=%s, limit=%s",
                         industry_code, limited)
            return 0

    # 부채비율 상한 체크
    debt_ratio_limit = program_features.get('debt_ratio_limit')
    if debt_ratio is not None and debt_ratio_limit is not None:
        if debt_ratio > debt_ratio_limit:
            logger.debug("부채비율 초과: %.1f > %.1f", debt_ratio, debt_ratio_limit)
            return 0

    # 업력 조건 체크 (requirements 문자열에서 파싱)
    requirements = program_features.get('requirements') or []
    for req in requirements:
        if not req:
            continue
        m = re.search(r'업력\s*(\d+)년\s*(이상|미만)', req)
        if m:
            years = int(m.group(1))
            condition = m.group(2)
            if condition == '이상' and business_age < years:
                logger.debug("업력 조건 미통과: %d년 < 요구 %d년 이상", business_age, years)
                return 0
            if condition == '미만' and business_age >= years:
                logger.debug("업력 조건 미통과: %d년 >= 제한 %d년 미만", business_age, years)
                return 0

    return 1
