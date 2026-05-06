import re
import logging
from datetime import datetime
from typing import Tuple

logger = logging.getLogger(__name__)

# ── 재무 점수 가중치 ──────────────────────────────────────
# 부채비율: 중진공 공고 핵심 Hard Filter 지표
# 현금흐름: 실제 상환 능력 직접 지표
# 영업이익: 성장성 proxy (매출성장률 데이터 부재)
_F_DEBT_WEIGHT   = 0.5
_F_CASH_WEIGHT   = 0.3
_F_PROFIT_WEIGHT = 0.2

# ── 기술 점수 가중치 ──────────────────────────────────────
# IPC코드, 최신성은 KIPRIS 파이프라인 연동 후
# 데이터 수집 시 복원 예정 (현재 0으로 설정)
_T_PATENT_WEIGHT  = 1.0  # 데이터 없는 항목 재배분
_T_IPC_WEIGHT     = 0.0  # KIPRIS ipc_codes 수집 후 복원
_T_RECENCY_WEIGHT = 0.0  # KIPRIS latest_patent_year 수집 후 복원

# ── 정책 가점 가중치 ──────────────────────────────────────
# 정책 중요도 순서: 벤처 > 이노비즈 > 청년고용 > 신용등급
_VENTURE_WEIGHT = 0.4
_INNOBIZ_WEIGHT = 0.3
_YOUTH_WEIGHT   = 0.2
_CREDIT_WEIGHT  = 0.1

# ── 정규화 기준값 ─────────────────────────────────────────
# 부채비율 300%: 중진공 공고에서 자주 등장하는 기준값
_DEBT_RATIO_BENCHMARK = 300
# 특허 5건: 중소기업 평균 수준 기준값 (임의 설정)
_PATENT_NORM = 5


def calc_financial_score(company_features: dict) -> float:
    """재무 점수 계산 (0.0 ~ 1.0)

    구성:
    - 부채비율 (_F_DEBT_WEIGHT=0.5): 낮을수록 높은 점수,
      _DEBT_RATIO_BENCHMARK(300%) 기준 정규화
    - 영업활동 현금흐름 (_F_CASH_WEIGHT=0.3): 매출 대비 비율로 정규화
    - 영업이익 (_F_PROFIT_WEIGHT=0.2): 매출 대비 비율로 정규화
      (매출성장률 데이터 부재로 영업이익률을 proxy로 사용)

    null 처리:
    - 핵심 재무 항목 전체 null → 비상장 기업 기본값 0.5 반환
    - 항목별 null → 해당 항목 0.0 처리

    가중치 근거:
    - 부채비율(50%): 중진공 공고문 대부분이 부채비율을 핵심
      Hard Filter로 사용하는 재무 건전성의 대표 지표
    - 현금흐름(30%): 정책자금 상환 능력의 직접 지표
    - 영업이익(20%): 사업성장 가능성 지표
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

    revenue = company_features.get('revenue') or 1

    if cash_flow is None or cash_flow <= 0:
        cash_score = 0.0
    else:
        cash_score = min(cash_flow / revenue, 1.0)

    if operating_profit is None or operating_profit <= 0:
        profit_score = 0.0
    else:
        profit_score = min(operating_profit / revenue, 1.0)

    score = (
        _F_DEBT_WEIGHT   * debt_score +
        _F_CASH_WEIGHT   * cash_score +
        _F_PROFIT_WEIGHT * profit_score
    )
    return round(min(1.0, score), 4)


def calc_tech_score(company_features: dict) -> float:
    """기술 점수 계산 (0.0 ~ 1.0)

    구성:
    - 특허 보유수 (_T_PATENT_WEIGHT=1.0): _PATENT_NORM(5건) 기준 정규화,
      5건 이상 = 1.0
    - IPC 코드 존재 여부 (_T_IPC_WEIGHT=0.0): KIPRIS 연동 후 활성화 예정
    - 특허 출원일 최신성 (_T_RECENCY_WEIGHT=0.0): KIPRIS 연동 후 활성화 예정

    KIPRIS 파이프라인 연동 완료 시 가중치 복원:
        _T_PATENT_WEIGHT=0.6, _T_IPC_WEIGHT=0.2, _T_RECENCY_WEIGHT=0.2
    해당 시점에 company_features에 ipc_codes, latest_patent_year 필드 추가 필요.
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

    score = (
        _T_PATENT_WEIGHT  * patent_score +
        _T_IPC_WEIGHT     * ipc_score +
        _T_RECENCY_WEIGHT * recency_score
    )
    return round(min(1.0, score), 4)


def calc_policy_score(company_features: dict) -> float:
    """정책 가점 계산 (0.0 ~ 1.0)

    구성:
    - 벤처기업 인증 (_VENTURE_WEIGHT=0.4)
    - 이노비즈 인증 (_INNOBIZ_WEIGHT=0.3)
    - 청년고용 여부 (_YOUTH_WEIGHT=0.2)
    - 신용등급 보유 (_CREDIT_WEIGHT=0.1)
    (합산 후 1.0 초과 시 cap)

    가중치 근거:
    - 정책 중요도 순서: 벤처 > 이노비즈 > 청년고용 > 신용등급
    """
    score = 0.0
    if company_features.get('is_venture'):
        score += _VENTURE_WEIGHT
    if company_features.get('is_innobiz'):
        score += _INNOBIZ_WEIGHT
    if company_features.get('youth_employment'):
        score += _YOUTH_WEIGHT
    if company_features.get('credit_grade') is not None:
        score += _CREDIT_WEIGHT
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
