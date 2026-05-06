import json
from unittest.mock import MagicMock, patch

import pytest

from agents.embedding.agent import hard_filter, embedding_node

# ── 공통 픽스처 ────────────────────────────────────────────

_COMPANY_PASS = {
    'company_id': 'TEST-001',
    'company_type': '중소기업',
    'industry_code': 'J5821',   # 정보통신업 세부 코드
    'industry_section': 'J',
    'region': '서울',
    'business_age': 5,
    'revenue': 1_000_000_000,
    'employee_count': 10,
    'debt_ratio': 150.0,
    'export_usd': 0,
}

_PROGRAM_OK = {
    'program_id': 'P001',
    'program_name': '정보통신 기술 지원',
    'category': '기술',
    'industry_limit': ['C', 'D'],   # J(정보통신)는 제한 아님
    'debt_ratio_limit': 300.0,
    'max_business_age': 10,
    'max_support': 100_000_000,
}

_PROGRAM_INDUSTRY_BLOCKED = {
    'program_id': 'P002',
    'program_name': '제조업 전용 지원',
    'category': '기술',
    'industry_limit': ['J'],        # J 섹션 제한 → 탈락
    'debt_ratio_limit': 300.0,
    'max_business_age': 10,
    'max_support': 50_000_000,
}

_PROGRAM_DEBT_BLOCKED = {
    'program_id': 'P003',
    'program_name': '우량기업 전용',
    'category': '금융',
    'industry_limit': [],
    'debt_ratio_limit': 100.0,     # 150 > 100 → 탈락
    'max_business_age': 10,
    'max_support': 200_000_000,
}

_PROGRAM_AGE_BLOCKED = {
    'program_id': 'P004',
    'program_name': '창업초기 전용',
    'category': '창업',
    'industry_limit': [],
    'debt_ratio_limit': 500.0,
    'max_business_age': 3,         # 업력 5 > 3 → 탈락
    'max_support': 30_000_000,
}

_BASE_STATE = {
    'company_id': 'TEST-001',
    'company_features': _COMPANY_PASS,
    'dart_found': True,
    'user_input_required': False,
    'candidate_programs': [],
    'ranked_programs': [],
    'score_breakdown': {},
    'contribution': {},
    'delta_analysis': {},
    'improvable_features': [],
    'feedback': '',
    'response': {},
    'error': None,
}


# ── hard_filter: 업종 제한 ─────────────────────────────────

def test_hard_filter_industry_limit_section():
    """섹션 코드(J)가 industry_limit에 포함되면 탈락."""
    result = hard_filter(_COMPANY_PASS, [_PROGRAM_INDUSTRY_BLOCKED])
    assert result == []


def test_hard_filter_industry_limit_prefix():
    """업종 코드 prefix 일치 시 탈락."""
    company = {**_COMPANY_PASS, 'industry_code': 'C2620', 'industry_section': 'C'}
    program = {**_PROGRAM_OK, 'industry_limit': ['C26']}
    result = hard_filter(company, [program])
    assert result == []


def test_hard_filter_industry_limit_no_match_passes():
    """업종 제한이 있어도 본인 코드가 해당 없으면 통과."""
    result = hard_filter(_COMPANY_PASS, [_PROGRAM_OK])
    assert len(result) == 1


def test_hard_filter_industry_limit_empty_passes():
    """industry_limit 빈 리스트면 통과."""
    program = {**_PROGRAM_OK, 'industry_limit': []}
    result = hard_filter(_COMPANY_PASS, [program])
    assert len(result) == 1


def test_hard_filter_industry_limit_json_string():
    """industry_limit이 JSON 문자열로 저장된 경우도 파싱하여 처리."""
    program = {**_PROGRAM_INDUSTRY_BLOCKED, 'industry_limit': json.dumps(['J', 'K'])}
    result = hard_filter(_COMPANY_PASS, [program])
    assert result == []


# ── hard_filter: 부채비율 초과 ────────────────────────────

def test_hard_filter_debt_ratio_exceeds():
    """부채비율이 debt_ratio_limit 초과 시 탈락."""
    result = hard_filter(_COMPANY_PASS, [_PROGRAM_DEBT_BLOCKED])
    assert result == []


def test_hard_filter_debt_ratio_at_limit_passes():
    """부채비율이 debt_ratio_limit과 정확히 같으면 통과."""
    company = {**_COMPANY_PASS, 'debt_ratio': 100.0}
    result = hard_filter(company, [_PROGRAM_DEBT_BLOCKED])
    assert len(result) == 1


def test_hard_filter_debt_ratio_none_skips_check():
    """debt_ratio가 None이면 부채비율 체크를 건너뜀."""
    company = {**_COMPANY_PASS, 'debt_ratio': None}
    result = hard_filter(company, [_PROGRAM_DEBT_BLOCKED])
    assert len(result) == 1


def test_hard_filter_debt_limit_none_skips_check():
    """debt_ratio_limit이 None이면 부채비율 체크를 건너뜀."""
    program = {**_PROGRAM_DEBT_BLOCKED, 'debt_ratio_limit': None}
    result = hard_filter(_COMPANY_PASS, [program])
    assert len(result) == 1


# ── hard_filter: 업력 초과 ────────────────────────────────

def test_hard_filter_business_age_exceeds():
    """업력이 max_business_age 초과 시 탈락."""
    result = hard_filter(_COMPANY_PASS, [_PROGRAM_AGE_BLOCKED])
    assert result == []


def test_hard_filter_business_age_at_max_passes():
    """업력이 max_business_age와 같으면 통과."""
    company = {**_COMPANY_PASS, 'business_age': 3}
    result = hard_filter(company, [_PROGRAM_AGE_BLOCKED])
    assert len(result) == 1


def test_hard_filter_max_age_none_skips_check():
    """max_business_age가 None이면 업력 체크 건너뜀."""
    program = {**_PROGRAM_AGE_BLOCKED, 'max_business_age': None}
    result = hard_filter(_COMPANY_PASS, [program])
    assert len(result) == 1


# ── hard_filter: 모두 통과 케이스 ────────────────────────

def test_hard_filter_all_pass():
    """모든 조건을 만족하면 전부 통과."""
    programs = [_PROGRAM_OK, _PROGRAM_OK.copy()]
    programs[1] = {**_PROGRAM_OK, 'program_id': 'P001b'}
    result = hard_filter(_COMPANY_PASS, programs)
    assert len(result) == 2


def test_hard_filter_mixed_programs():
    """통과/탈락 섞인 경우 올바르게 분류."""
    programs = [
        _PROGRAM_OK,             # 통과
        _PROGRAM_INDUSTRY_BLOCKED,  # 업종 탈락
        _PROGRAM_DEBT_BLOCKED,      # 부채비율 탈락
        _PROGRAM_AGE_BLOCKED,       # 업력 탈락
    ]
    result = hard_filter(_COMPANY_PASS, programs)
    assert len(result) == 1
    assert result[0]['program_id'] == 'P001'


def test_hard_filter_empty_programs():
    """빈 프로그램 리스트 → 빈 결과."""
    result = hard_filter(_COMPANY_PASS, [])
    assert result == []


# ── embedding_node: 후보 없음 시 State 처리 ──────────────

def _make_empty_store_result():
    return {'green': [], 'yellow': [], 'red': []}


def _make_store_result_with_candidates():
    meta = {
        'program_id': 'P001',
        'announcement_title': '테스트 공고',
        'category': '기술',
        'max_support': 100_000_000,
        'interest_rate': '연 2%',
        'apply_start': '2026-01-01',
        'apply_end': '2999-12-31',
    }
    item = {
        'doc': MagicMock(),
        'score': 0.85,
        'reasons': [],
        'meta_data': meta,
        'caution_notes': [],
    }
    return {'green': [item], 'yellow': [], 'red': []}


@patch('agents.embedding.agent._load_programs_from_s3', return_value=[])
@patch('agents.embedding.agent.PolicyVectorStore')
def test_embedding_node_no_candidates_sets_error(mock_store_cls, mock_s3):
    """후보 없으면 error 설정, candidate_programs 빈 리스트."""
    mock_store_cls.return_value.search_for_agent.return_value = _make_empty_store_result()

    result = embedding_node({**_BASE_STATE})

    assert result['candidate_programs'] == []
    assert result['error'] is not None
    assert '후보' in result['error']


@patch('agents.embedding.agent._load_programs_from_s3', return_value=[])
@patch('agents.embedding.agent.PolicyVectorStore')
def test_embedding_node_with_candidates(mock_store_cls, mock_s3):
    """후보 있으면 candidate_programs 채워지고 error None."""
    mock_store_cls.return_value.search_for_agent.return_value = (
        _make_store_result_with_candidates()
    )

    result = embedding_node({**_BASE_STATE})

    assert len(result['candidate_programs']) == 1
    assert result['candidate_programs'][0]['program_id'] == 'P001'
    assert result['error'] is None


@patch('agents.embedding.agent._load_programs_from_s3', return_value=[])
@patch('agents.embedding.agent.PolicyVectorStore')
def test_embedding_node_store_exception_sets_error(mock_store_cls, mock_s3):
    """PolicyVectorStore 예외 발생 시 error 설정."""
    mock_store_cls.return_value.search_for_agent.side_effect = RuntimeError('DB 접속 실패')

    result = embedding_node({**_BASE_STATE})

    assert result['candidate_programs'] == []
    assert 'embedding_node 오류' in result['error']


@patch('agents.embedding.agent._load_programs_from_s3')
@patch('agents.embedding.agent.PolicyVectorStore')
def test_embedding_node_hard_filter_intersects(mock_store_cls, mock_s3):
    """S3에서 로드된 프로그램 중 hard_filter 탈락 program_id는 candidates에서 제외."""
    # S3에 P001(통과), P002(업종 탈락) 두 개 로드
    mock_s3.return_value = [
        {**_PROGRAM_OK, 'program_id': 'P001'},
        {**_PROGRAM_INDUSTRY_BLOCKED, 'program_id': 'P002'},
    ]

    # VectorStore는 두 개 모두 green으로 반환
    def make_item(pid):
        meta = {
            'program_id': pid,
            'announcement_title': f'공고 {pid}',
            'category': '기술',
            'max_support': 100_000_000,
            'interest_rate': '',
            'apply_start': '2026-01-01',
            'apply_end': '2999-12-31',
        }
        return {'doc': MagicMock(), 'score': 0.9, 'reasons': [], 'meta_data': meta, 'caution_notes': []}

    mock_store_cls.return_value.search_for_agent.return_value = {
        'green': [make_item('P001'), make_item('P002')],
        'yellow': [],
        'red': [],
    }

    result = embedding_node({**_BASE_STATE})

    ids = [c['program_id'] for c in result['candidate_programs']]
    assert 'P001' in ids
    assert 'P002' not in ids
