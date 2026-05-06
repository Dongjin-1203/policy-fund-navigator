import re
import logging
from typing import Optional

from agents.scoring.tools import (
    _F_DEBT_WEIGHT, _F_CASH_WEIGHT, _F_PROFIT_WEIGHT,
    _T_PATENT_WEIGHT,
    _VENTURE_WEIGHT, _INNOBIZ_WEIGHT, _YOUTH_WEIGHT, _CREDIT_WEIGHT,
    _DEBT_RATIO_BENCHMARK, _PATENT_NORM,
)

logger = logging.getLogger(__name__)


def calc_contribution(score_breakdown: dict) -> dict:
    """α·F, β·T, γ·G 각각의 기여분 산출.

    Returns:
        {
            'alpha_F': float,  # α × F
            'beta_T':  float,  # β × T
            'gamma_G': float,  # γ × G
            'total':   float   # P
        }
    """
    alpha = score_breakdown['alpha']
    beta  = score_breakdown['beta']
    gamma = score_breakdown['gamma']
    F = score_breakdown['F']
    T = score_breakdown['T']
    G = score_breakdown['G']

    alpha_F = round(alpha * F, 4)
    beta_T  = round(beta  * T, 4)
    gamma_G = round(gamma * G, 4)

    return {
        'alpha_F': alpha_F,
        'beta_T':  beta_T,
        'gamma_G': gamma_G,
        'total':   round(alpha_F + beta_T + gamma_G, 4),
    }


def calc_feature_contribution(score_breakdown: dict, company_features: dict) -> dict:
    """F/T/G 내부 feature별 최종 기여도 산출.

    score_breakdown에서 α/β/γ를 읽고, company_features에서 sub-score를 재계산.

    Returns:
        {
            'debt_ratio':       float,
            'cash_flow':        float,
            'operating_profit': float,
            'patent_count':     float,
            'is_venture':       float,
            'is_innobiz':       float,
            'youth_employment': float,
            'credit_grade':     float
        }
    """
    alpha = score_breakdown['alpha']
    beta  = score_breakdown['beta']
    gamma = score_breakdown['gamma']

    # ── F sub-scores ─────────────────────────────────────
    debt_ratio       = company_features.get('debt_ratio')
    cash_flow        = company_features.get('cash_flow')
    operating_profit = company_features.get('operating_profit')
    revenue          = company_features.get('revenue') or 1

    debt_score = (
        max(0.0, 1.0 - debt_ratio / _DEBT_RATIO_BENCHMARK)
        if debt_ratio is not None else 0.0
    )
    cash_score = (
        min(cash_flow / revenue, 1.0)
        if (cash_flow is not None and cash_flow > 0) else 0.0
    )
    profit_score = (
        min(operating_profit / revenue, 1.0)
        if (operating_profit is not None and operating_profit > 0) else 0.0
    )

    # ── T sub-scores ─────────────────────────────────────
    patent_count = company_features.get('patent_count')
    patent_score = (
        min(1.0, patent_count / _PATENT_NORM)
        if (patent_count is not None and patent_count >= 0) else 0.0
    )

    # ── G sub-scores ─────────────────────────────────────
    venture_score = 1.0 if company_features.get('is_venture') else 0.0
    innobiz_score = 1.0 if company_features.get('is_innobiz') else 0.0
    youth_score   = 1.0 if company_features.get('youth_employment') else 0.0
    credit_score  = 1.0 if company_features.get('credit_grade') is not None else 0.0

    return {
        'debt_ratio':       round(alpha * _F_DEBT_WEIGHT   * debt_score,    4),
        'cash_flow':        round(alpha * _F_CASH_WEIGHT   * cash_score,    4),
        'operating_profit': round(alpha * _F_PROFIT_WEIGHT * profit_score,  4),
        'patent_count':     round(beta  * _T_PATENT_WEIGHT * patent_score,  4),
        'is_venture':       round(gamma * _VENTURE_WEIGHT  * venture_score, 4),
        'is_innobiz':       round(gamma * _INNOBIZ_WEIGHT  * innobiz_score, 4),
        'youth_employment': round(gamma * _YOUTH_WEIGHT    * youth_score,   4),
        'credit_grade':     round(gamma * _CREDIT_WEIGHT   * credit_score,  4),
    }


def calc_delta(company_features: dict, program: dict) -> dict:
    """자격요건 컷오프와 현재 기업값의 정규화된 차이 계산.

    양수 = 부족한 만큼 (요건 미달), 음수 = 여유분 (요건 충족).
    기준값으로 나눠 정규화하므로 threshold=0.1이 "기준 대비 10% 이내"를 의미.

    대상 항목:
    - 부채비율 (max 제약): (company - limit) / limit
    - 업력     (min 제약): (required - company) / required
    """
    delta: dict = {}

    # 부채비율: 기업 비율이 한도를 초과할수록 양수
    debt_ratio = company_features.get('debt_ratio')
    debt_limit = program.get('debt_ratio_limit')
    if debt_ratio is not None and debt_limit is not None and debt_limit > 0:
        delta['debt_ratio'] = round((debt_ratio - debt_limit) / debt_limit, 4)

    # 업력: 요구 업력에 못 미칠수록 양수 (업력 미만 조건은 초과할수록 양수)
    business_age = company_features.get('business_age')
    requirements = program.get('requirements') or []
    for req in requirements:
        if not req:
            continue
        m = re.search(r'업력\s*(\d+)년\s*(이상|미만)', req)
        if not m:
            continue
        required_years = int(m.group(1))
        condition = m.group(2)
        if business_age is not None and required_years > 0:
            if condition == '이상':
                delta['business_age'] = round(
                    (required_years - business_age) / required_years, 4
                )
            else:  # 미만: 업력이 기준 이상이면 탈락
                delta['business_age'] = round(
                    (business_age - required_years) / required_years, 4
                )
        break

    return delta


def get_improvable_features(delta: dict, threshold: float = 0.1) -> list:
    """delta가 0 초과 threshold 이하인 항목 반환.

    (기준에 조금만 못 미치는 항목 — 소폭 보완으로 조건 충족 가능)
    """
    return [feat for feat, d in delta.items() if 0 < d <= threshold]


def get_top_features(feature_contribution: dict, n: int = 3) -> list:
    """기여도 절댓값 기준 상위 n개 반환.

    음수 기여(감점) 항목을 우선 포함하고, 이후 양수 항목을 절댓값 내림차순으로 정렬.

    Returns:
        [(feature_name, contribution_value), ...]
    """
    items = list(feature_contribution.items())
    negatives = sorted(
        [(k, v) for k, v in items if v < 0],
        key=lambda x: x[1],           # 가장 큰 감점(음수 최솟값)부터
    )
    positives = sorted(
        [(k, v) for k, v in items if v >= 0],
        key=lambda x: -x[1],          # 기여도 내림차순
    )
    return (negatives + positives)[:n]
