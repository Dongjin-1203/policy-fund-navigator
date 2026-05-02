import pytest
from agents.scoring.tools import (
    calc_financial_score,
    calc_tech_score,
    calc_policy_score,
    load_scoring_params,
    generate_synthetic_label,
)
from agents.scoring.agent import scoring_node

_BASE_STATE = {
    'dart_found': True,
    'user_input_required': False,
    'ranked_programs': [],
    'score_breakdown': {},
    'contribution': {},
    'delta_analysis': {},
    'improvable_features': [],
    'feedback': '',
    'response': {},
    'error': None,
}

_FULL_FEATURES = {
    'company_id': 'TEST-001',
    'debt_ratio': 150.0,
    'operating_profit': 500_000_000,
    'cash_flow': 300_000_000,
    'revenue': 5_000_000_000,
    'net_income': 200_000_000,
    'patent_count': 3,
    'is_venture': True,
    'is_innobiz': False,
    'credit_grade': 'BBB',
    'business_age': 5,
    'industry_code': 'C',
    'employee_count': 20,
    'region': '서울',
    'capital': 1_000_000_000,
}

_NULL_FEATURES = {
    'company_id': 'TEST-002',
    'debt_ratio': None,
    'operating_profit': None,
    'cash_flow': None,
    'revenue': None,
    'net_income': None,
    'patent_count': None,
    'is_venture': False,
    'is_innobiz': False,
    'credit_grade': None,
    'business_age': 2,
    'industry_code': 'G',
    'employee_count': 3,
    'region': '부산',
    'capital': None,
}

_CANDIDATES = [
    {'program_id': 'P001', 'program_name': '창업기반지원자금'},
    {'program_id': 'P002', 'program_name': '미래기술육성자금'},
    {'program_id': 'P003', 'program_name': '청년전용창업자금'},
]


# ── 재무 점수 ──────────────────────────────────────────────

def test_financial_score_in_range():
    score = calc_financial_score(_FULL_FEATURES)
    assert 0.0 <= score <= 1.0


def test_financial_score_null_returns_half():
    score = calc_financial_score(_NULL_FEATURES)
    assert score == 0.5


def test_financial_score_zero_debt_ratio():
    features = {**_FULL_FEATURES, 'debt_ratio': 0.0}
    score = calc_financial_score(features)
    assert score > calc_financial_score(_FULL_FEATURES)


def test_financial_score_high_debt_penalized():
    low_debt = {**_FULL_FEATURES, 'debt_ratio': 50.0}
    high_debt = {**_FULL_FEATURES, 'debt_ratio': 280.0}
    assert calc_financial_score(low_debt) > calc_financial_score(high_debt)


# ── 정책 가점 ──────────────────────────────────────────────

def test_policy_score_venture_higher_than_none():
    with_venture = {**_NULL_FEATURES, 'is_venture': True}
    without_venture = {**_NULL_FEATURES, 'is_venture': False}
    assert calc_policy_score(with_venture) > calc_policy_score(without_venture)


def test_policy_score_cap_at_one():
    all_certs = {
        **_NULL_FEATURES,
        'is_venture': True,
        'is_innobiz': True,
        'youth_employment': True,
        'credit_grade': 'AA',
    }
    assert calc_policy_score(all_certs) == 1.0


def test_policy_score_no_certs_is_zero():
    assert calc_policy_score(_NULL_FEATURES) == 0.0


# ── scoring_node ───────────────────────────────────────────

def test_scoring_node_produces_ranked_programs():
    state = {**_BASE_STATE, 'company_id': 'TEST-001',
             'company_features': _FULL_FEATURES,
             'candidate_programs': _CANDIDATES}
    result = scoring_node(state)
    assert len(result['ranked_programs']) > 0


def test_score_breakdown_keys_present():
    state = {**_BASE_STATE, 'company_id': 'TEST-001',
             'company_features': _FULL_FEATURES,
             'candidate_programs': _CANDIDATES}
    result = scoring_node(state)
    for key in ('F', 'T', 'G', 'alpha', 'beta', 'gamma'):
        assert key in result['score_breakdown'], f"score_breakdown에 '{key}' 없음"


def test_ranked_programs_sorted_descending():
    state = {**_BASE_STATE, 'company_id': 'TEST-001',
             'company_features': _FULL_FEATURES,
             'candidate_programs': _CANDIDATES}
    result = scoring_node(state)
    scores = [p['score'] for p in result['ranked_programs']]
    assert scores == sorted(scores, reverse=True)


def test_scoring_node_empty_candidates():
    state = {**_BASE_STATE, 'company_id': 'TEST-001',
             'company_features': _FULL_FEATURES,
             'candidate_programs': []}
    result = scoring_node(state)
    assert result['ranked_programs'] == []


def test_scoring_node_top_n_limit():
    many_candidates = [
        {'program_id': f'P{i:03d}', 'program_name': f'사업{i}'}
        for i in range(20)
    ]
    state = {**_BASE_STATE, 'company_id': 'TEST-001',
             'company_features': _FULL_FEATURES,
             'candidate_programs': many_candidates}
    result = scoring_node(state)
    assert len(result['ranked_programs']) <= 10


# ── generate_synthetic_label ───────────────────────────────

def test_synthetic_label_pass_all():
    program = {
        'industry_limit': [],
        'debt_ratio_limit': 300.0,
        'requirements': ['업력 3년 이상'],
    }
    assert generate_synthetic_label(_FULL_FEATURES, program) == 1


def test_synthetic_label_fail_debt_ratio():
    program = {'industry_limit': [], 'debt_ratio_limit': 100.0, 'requirements': []}
    assert generate_synthetic_label(_FULL_FEATURES, program) == 0


def test_synthetic_label_fail_business_age():
    program = {'industry_limit': [], 'debt_ratio_limit': None,
               'requirements': ['업력 7년 이상']}
    assert generate_synthetic_label(_FULL_FEATURES, program) == 0
