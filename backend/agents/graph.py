import logging

from langgraph.graph import StateGraph, END

from agents.state import PolicyFundState
from agents.scoring.agent import scoring_node
from agents.shap.agent import shap_node

logger = logging.getLogger(__name__)

# orchestrator_node와 embedding_node는 각자 브랜치(feature/orchestrator,
# feature/embedding-agent)에서 구현된다. 병합 전 브랜치에서는 stub으로 동작.
try:
    from agents.orchestrator.agent import orchestrator_node
except (ImportError, SyntaxError):
    logger.warning("orchestrator_node 미구현 또는 의존 모듈 오류 — 패스스루 stub 사용")

    def orchestrator_node(state: PolicyFundState) -> PolicyFundState:  # type: ignore[misc]
        """stub: dart_found 초기화 및 SHAP 완료 후 response 생성 시뮬레이션."""
        if state.get('dart_found') is None:
            return {**state, 'dart_found': True, 'user_input_required': False}
        if state.get('ranked_programs') is not None:
            return {
                **state,
                'response': {'status': 'stub', 'ranked_programs': state.get('ranked_programs')},
            }
        return state

try:
    from agents.embedding.agent import embedding_node
except (ImportError, SyntaxError):
    logger.warning("embedding_node 미구현 — 패스스루 stub 사용")

    def embedding_node(state: PolicyFundState) -> PolicyFundState:  # type: ignore[misc]
        """stub: 후보 없음 상태로 반환."""
        return {**state, 'candidate_programs': [], 'error': 'embedding stub'}


# ── 조건부 엣지 함수 ────────────────────────────────────────────────────────────

def should_continue(state: PolicyFundState) -> str:
    """embedding → 후보 있으면 scoring, 없으면 orchestrator 조기 복귀."""
    if state.get('error'):
        return "orchestrator"
    if not state.get('candidate_programs'):
        return "orchestrator"
    return "scoring"


def is_done(state: PolicyFundState) -> str:
    """orchestrator → response 완성 또는 user_input_required 시 END, 아니면 embedding.

    루프 방지: embedding이 후보 없음(candidate_programs=[])을 반환했으나
    orchestrator가 response를 구성하지 못했을 경우, 무한 루프를 방지하기 위해 END.
    (정상 구현에서는 orchestrator가 이 경우에 response를 설정해야 함.)
    """
    if state.get('response'):
        return END
    if state.get('user_input_required'):
        return END
    # 후보 없음으로 임베딩 복귀 후 orchestrator가 response를 생성하지 못한 경우
    if state.get('dart_found') and state.get('candidate_programs') == []:
        return END
    return "embedding"


# ── 그래프 구성 ────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    """PolicyFundState 기반 MAS LangGraph 빌드."""
    graph = StateGraph(PolicyFundState)

    graph.add_node("orchestrator", orchestrator_node)
    graph.add_node("embedding",    embedding_node)
    graph.add_node("scoring",      scoring_node)
    graph.add_node("shap",         shap_node)

    # 시작점: orchestrator (DART 조회 + State 초기화)
    graph.set_entry_point("orchestrator")

    # orchestrator → END 또는 embedding (조건부)
    graph.add_conditional_edges(
        "orchestrator",
        is_done,
        {
            END:         END,
            "embedding": "embedding",
        },
    )

    # embedding → scoring(후보 있음) / orchestrator(후보 없음) 조건부
    graph.add_conditional_edges(
        "embedding",
        should_continue,
        {
            "scoring":      "scoring",
            "orchestrator": "orchestrator",
        },
    )

    # scoring → shap → orchestrator 직렬 연결
    graph.add_edge("scoring", "shap")
    graph.add_edge("shap",    "orchestrator")

    return graph


app = build_graph().compile()
