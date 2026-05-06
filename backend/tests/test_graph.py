"""LangGraph MAS 그래프 통합 테스트.

모든 에이전트 노드를 mock으로 교체하여 그래프 라우팅 로직만 검증한다.
실제 노드 구현은 각 브랜치(feature/orchestrator, feature/embedding-agent,
feature/scoring-agent, feature/shap-agent)에서 개별 단위 테스트로 커버한다.
"""
from unittest.mock import patch, MagicMock

import pytest

# ── 기본 State 픽스처 ──────────────────────────────────────────────────────────

_BASE_STATE = {
    'company_id': '1234567890',
    'company_features': {
        'revenue': 5_000_000_000,
        'debt_ratio': 150.0,
        'operating_profit': 300_000_000,
        'cash_flow': 200_000_000,
        'employee_count': 45,
        'business_age': 7,
        'patent_count': 3,
        'is_venture': True,
        'is_innobiz': False,
        'industry_code': 'C2800',
        'region': '서울',
        'credit_grade': None,
    },
    'dart_found': None,
    'user_input_required': False,
    'candidate_programs': None,
    'ranked_programs': None,
    'score_breakdown': None,
    'contribution': None,
    'delta_analysis': None,
    'improvable_features': None,
    'feedback': None,
    'response': None,
    'error': None,
}

_MOCK_CANDIDATES = [
    {'program_id': 'P001', 'program_name': '창업기반지원자금', 'score': 0.85},
    {'program_id': 'P002', 'program_name': '미래기술육성자금', 'score': 0.72},
]

_MOCK_RANKED = [
    {**c, 'score': 0.65, 'score_breakdown': {'F': 0.5, 'T': 0.6, 'G': 0.4, 'alpha': 0.4, 'beta': 0.3, 'gamma': 0.3}}
    for c in _MOCK_CANDIDATES
]

_MOCK_RESPONSE = {
    'company_id': '1234567890',
    'status': 'success',
    'matched_count': 2,
    'ranked_programs': _MOCK_RANKED,
    'feedback': '적합한 정책자금 2건이 매칭되었습니다.',
}


# ── Mock 노드 팩토리 ───────────────────────────────────────────────────────────

def _make_orchestrator(phase1_result=None, phase2_result=None):
    """2단계 orchestrator 시뮬레이션. 호출 횟수로 phase 판별."""
    call_count = [0]

    def orchestrator_node(state):
        call_count[0] += 1
        # Phase 1: dart_found is None
        if state.get('dart_found') is None:
            return phase1_result or {
                **state,
                'dart_found': True,
                'user_input_required': False,
            }
        # Phase 2: SHAP 완료 후 (ranked_programs is not None)
        return phase2_result or {
            **state,
            'feedback': '테스트 피드백',
            'response': _MOCK_RESPONSE,
        }

    return orchestrator_node


def _make_embedding(candidates=None, error=None):
    def embedding_node(state):
        return {
            **state,
            'candidate_programs': candidates if candidates is not None else _MOCK_CANDIDATES,
            'error': error,
        }
    return embedding_node


def _make_scoring():
    def scoring_node(state):
        return {**state, 'ranked_programs': _MOCK_RANKED, 'score_breakdown': {'F': 0.5, 'T': 0.6, 'G': 0.4}}
    return scoring_node


def _make_shap():
    def shap_node(state):
        return {
            **state,
            'contribution': {'alpha_F': 0.2, 'beta_T': 0.18, 'gamma_G': 0.12, 'total': 0.5},
            'delta_analysis': {},
            'improvable_features': [],
        }
    return shap_node


# ── 헬퍼: 그래프 재빌드 ───────────────────────────────────────────────────────

def _build_and_compile(orch_fn, emb_fn, score_fn=None, shap_fn=None):
    """노드 함수를 patch하지 않고 graph 모듈을 직접 교체하는 방식으로 실행."""
    import agents.graph as gmod
    orig_orch  = gmod.orchestrator_node
    orig_emb   = gmod.embedding_node
    orig_score = gmod.scoring_node
    orig_shap  = gmod.shap_node

    gmod.orchestrator_node = orch_fn
    gmod.embedding_node    = emb_fn
    gmod.scoring_node      = score_fn or _make_scoring()
    gmod.shap_node         = shap_fn or _make_shap()

    compiled = gmod.build_graph().compile()

    gmod.orchestrator_node = orig_orch
    gmod.embedding_node    = orig_emb
    gmod.scoring_node      = orig_score
    gmod.shap_node         = orig_shap

    return compiled


# ── 테스트: 그래프 컴파일 ─────────────────────────────────────────────────────

def test_graph_compile_success():
    """graph.py import 및 compile 성공 확인."""
    from agents.graph import app, build_graph
    assert app is not None
    assert callable(getattr(app, 'invoke', None))


def test_build_graph_returns_compiled():
    """build_graph() 호출마다 새로운 compiled app 반환."""
    from agents.graph import build_graph
    app1 = build_graph().compile()
    app2 = build_graph().compile()
    assert app1 is not app2


# ── 테스트: 정상 흐름 ─────────────────────────────────────────────────────────

def test_graph_full_pipeline_reaches_end():
    """정상 경로: orchestrator→embedding→scoring→shap→orchestrator→END."""
    compiled = _build_and_compile(
        orch_fn=_make_orchestrator(),
        emb_fn=_make_embedding(),
    )
    result = compiled.invoke(dict(_BASE_STATE))
    assert result.get('response') is not None
    assert result.get('ranked_programs') == _MOCK_RANKED


def test_graph_response_has_required_keys():
    """response dict에 필수 키가 존재하는지 확인."""
    compiled = _build_and_compile(
        orch_fn=_make_orchestrator(),
        emb_fn=_make_embedding(),
    )
    result = compiled.invoke(dict(_BASE_STATE))
    resp = result.get('response', {})
    for key in ('company_id', 'status', 'matched_count', 'ranked_programs'):
        assert key in resp, f"response에 '{key}' 없음"


# ── 테스트: 후보 없음 → orchestrator 복귀 ────────────────────────────────────

def test_graph_no_candidates_returns_to_orchestrator():
    """embedding 후보 없음 → orchestrator로 조기 복귀."""
    visited = []

    def tracking_orchestrator(state):
        visited.append('orchestrator')
        if state.get('dart_found') is None:
            return {**state, 'dart_found': True, 'user_input_required': False}
        # 후보 없음으로 복귀 시 response 설정 → END
        return {**state, 'response': {'status': 'no_match', 'matched_count': 0}}

    compiled = _build_and_compile(
        orch_fn=tracking_orchestrator,
        emb_fn=_make_embedding(candidates=[], error='후보 없음'),
    )
    result = compiled.invoke(dict(_BASE_STATE))
    # orchestrator가 두 번 이상 호출됨 (Phase1 + 조기 복귀)
    assert visited.count('orchestrator') >= 2
    # 최종적으로 END 도달
    assert result.get('response') is not None


def test_graph_error_in_embedding_routes_to_orchestrator():
    """embedding error → should_continue → orchestrator 라우팅."""
    call_log = []

    def orch_fn(state):
        call_log.append('orchestrator')
        if state.get('dart_found') is None:
            return {**state, 'dart_found': True, 'user_input_required': False}
        return {**state, 'response': {'status': 'error'}}

    compiled = _build_and_compile(
        orch_fn=orch_fn,
        emb_fn=_make_embedding(candidates=None, error='DB 오류'),
    )
    # embedding이 error를 설정하면 candidate_programs=None → should_continue → orchestrator
    result = compiled.invoke(dict(_BASE_STATE))
    assert 'orchestrator' in call_log


# ── 테스트: user_input_required → END ────────────────────────────────────────

def test_graph_user_input_required_reaches_end():
    """user_input_required=True 시 즉시 END 도달."""
    def orch_fn(state):
        # Phase 1에서 비상장 기업으로 user_input_required 설정
        return {
            **state,
            'dart_found': False,
            'user_input_required': True,
            'response': {'status': 'user_input_required'},
        }

    compiled = _build_and_compile(
        orch_fn=orch_fn,
        emb_fn=_make_embedding(),
    )
    result = compiled.invoke(dict(_BASE_STATE))
    assert result.get('user_input_required') is True
    # response가 설정되었으므로 END 도달 (embedding 미실행)
    assert result.get('response', {}).get('status') == 'user_input_required'
    # embedding이 호출되지 않았으므로 candidate_programs는 None 그대로
    assert result.get('candidate_programs') is None


def test_graph_user_input_required_skips_pipeline():
    """user_input_required 시 embedding/scoring/shap 미실행."""
    emb_called = [False]

    def emb_fn(state):
        emb_called[0] = True
        return {**state, 'candidate_programs': [], 'error': None}

    def orch_fn(state):
        return {
            **state,
            'dart_found': False,
            'user_input_required': True,
            'response': {'status': 'user_input_required'},
        }

    compiled = _build_and_compile(orch_fn=orch_fn, emb_fn=emb_fn)
    compiled.invoke(dict(_BASE_STATE))
    assert emb_called[0] is False
