import logging

from agents.state import PolicyFundState
from agents.scoring.tools import (
    calc_financial_score,
    calc_tech_score,
    calc_policy_score,
    load_scoring_params,
)

logger = logging.getLogger(__name__)

_TOP_N = 10


def scoring_node(state: PolicyFundState) -> PolicyFundState:
    """스코어링 에이전트 노드.

    P = α·F + β·T + γ·G 수식으로 candidate_programs에 점수를 부여하고
    Top-N으로 정렬하여 ranked_programs와 score_breakdown을 반환.

    현재: 기업 단위 점수 일괄 적용.
    추후: 사업별 요건 가중치 반영 예정.
    """
    company = state['company_features']
    candidates = state.get('candidate_programs') or []

    if not candidates:
        logger.warning("scoring_node: candidate_programs 없음 — 빈 결과 반환")
        return {**state, 'ranked_programs': [], 'score_breakdown': {}}

    alpha, beta, gamma = load_scoring_params()

    F = calc_financial_score(company)
    T = calc_tech_score(company)
    G = calc_policy_score(company)
    P = round(alpha * F + beta * T + gamma * G, 4)

    logger.info(
        "scoring_node: company_id=%s F=%.4f T=%.4f G=%.4f P=%.4f (α=%.1f β=%.1f γ=%.1f)",
        state.get('company_id'), F, T, G, P, alpha, beta, gamma,
    )

    breakdown = {
        'F': F, 'T': T, 'G': G,
        'alpha': alpha, 'beta': beta, 'gamma': gamma,
    }

    ranked = sorted(
        [
            {**program, 'score': P, 'score_breakdown': breakdown}
            for program in candidates
        ],
        key=lambda x: x['score'],
        reverse=True,
    )[:_TOP_N]

    return {**state, 'ranked_programs': ranked, 'score_breakdown': breakdown}
