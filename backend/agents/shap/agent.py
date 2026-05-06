import logging

from agents.state import PolicyFundState
from agents.shap.tools import (
    calc_contribution,
    calc_feature_contribution,
    calc_delta,
    get_improvable_features,
    get_top_features,
)

logger = logging.getLogger(__name__)


def shap_node(state: PolicyFundState) -> PolicyFundState:
    """SHAP 에이전트 노드.

    ranked_programs 상위 1개를 기준으로 기여도 분석 및 delta 계산.
    score_breakdown은 ranked_programs[0]에 내장된 값을 우선 사용하고,
    없으면 state['score_breakdown']로 폴백.
    """
    company  = state['company_features']
    ranked   = state.get('ranked_programs') or []

    if not ranked:
        logger.warning("shap_node: ranked_programs 없음 — 빈 결과 반환")
        return {
            **state,
            'contribution':      {},
            'delta_analysis':    {},
            'improvable_features': [],
        }

    top_program = ranked[0]
    breakdown   = top_program.get('score_breakdown') or state.get('score_breakdown') or {}

    if not breakdown:
        logger.error("shap_node: score_breakdown 없음 — 빈 결과 반환")
        return {
            **state,
            'contribution':      {},
            'delta_analysis':    {},
            'improvable_features': [],
        }

    contribution       = calc_contribution(breakdown)
    feature_contrib    = calc_feature_contribution(breakdown, company)
    delta              = calc_delta(company, top_program)
    improvable         = get_improvable_features(delta)
    top_features       = get_top_features(feature_contrib)

    logger.info(
        "shap_node: company_id=%s program=%s P=%.4f top_features=%s improvable=%s",
        state.get('company_id'),
        top_program.get('program_id') or top_program.get('program_name', '?'),
        contribution.get('total', 0.0),
        [f for f, _ in top_features],
        improvable,
    )

    return {
        **state,
        'contribution':        contribution,
        'delta_analysis':      delta,
        'improvable_features': improvable,
    }
