from typing import TypedDict, Optional, List


class PolicyFundState(TypedDict):
    # 입력
    company_id: str
    company_features: dict

    # 오케스트레이터
    dart_found: bool
    user_input_required: bool

    # 임베딩 에이전트 출력
    candidate_programs: List[dict]

    # 스코어링 에이전트 출력
    ranked_programs: List[dict]
    score_breakdown: dict        # {F: float, T: float, G: float}

    # SHAP 에이전트 출력
    contribution: dict           # {alpha_F, beta_T, gamma_G}
    delta_analysis: dict         # feature별 delta
    improvable_features: List[str]

    # 오케스트레이터 최종 출력
    feedback: str
    response: dict

    # 공통
    error: Optional[str]
