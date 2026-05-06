import pytest
from agents.shap.tools import (
    calc_contribution,
    calc_feature_contribution,
    calc_delta,
    get_improvable_features,
    get_top_features,
)

# ─────────────────────────────────────────────────────────
# Fixtures
#
# Company values chosen so sub-scores are exact fractions:
#   debt_ratio=150 → debt_score = 1 - 150/300 = 0.5
#   cash_flow=500k, revenue=1M → cash_score = 0.5
#   operating_profit=200k     → profit_score = 0.2
#   F = 0.5*0.5 + 0.3*0.5 + 0.2*0.2 = 0.44
#
#   patent_count=2 → patent_score = 2/5 = 0.4,  T = 0.4
#
#   is_venture=True, is_innobiz=False, youth=True, credit=None
#   G = 0.4 + 0.2 = 0.6
#
# P = 0.4*0.44 + 0.3*0.4 + 0.3*0.6 = 0.476
# ─────────────────────────────────────────────────────────
_BREAKDOWN = {
    'F': 0.44, 'T': 0.4, 'G': 0.6,
    'alpha': 0.4, 'beta': 0.3, 'gamma': 0.3,
}

_COMPANY = {
    'company_id':        'TEST-001',
    'debt_ratio':        150.0,
    'cash_flow':         500_000,
    'operating_profit':  200_000,
    'revenue':           1_000_000,
    'patent_count':      2,
    'is_venture':        True,
    'is_innobiz':        False,
    'youth_employment':  True,
    'credit_grade':      None,
}

_EXPECTED_P = round(0.4 * 0.44 + 0.3 * 0.4 + 0.3 * 0.6, 4)  # 0.476


# ─────────────────────────────────────────────────────────
# calc_contribution
# ─────────────────────────────────────────────────────────
class TestCalcContribution:
    def test_total_equals_sum_of_parts(self):
        r = calc_contribution(_BREAKDOWN)
        assert r['total'] == pytest.approx(r['alpha_F'] + r['beta_T'] + r['gamma_G'], abs=1e-9)

    def test_values_correct(self):
        r = calc_contribution(_BREAKDOWN)
        assert r['alpha_F'] == pytest.approx(0.4 * 0.44, abs=1e-4)
        assert r['beta_T']  == pytest.approx(0.3 * 0.4,  abs=1e-4)
        assert r['gamma_G'] == pytest.approx(0.3 * 0.6,  abs=1e-4)
        assert r['total']   == pytest.approx(_EXPECTED_P, abs=1e-4)

    def test_all_zero_scores(self):
        bd = {'F': 0.0, 'T': 0.0, 'G': 0.0, 'alpha': 0.4, 'beta': 0.3, 'gamma': 0.3}
        r = calc_contribution(bd)
        assert r['total'] == 0.0
        assert r['alpha_F'] == r['beta_T'] == r['gamma_G'] == 0.0

    def test_perfect_scores(self):
        bd = {'F': 1.0, 'T': 1.0, 'G': 1.0, 'alpha': 0.4, 'beta': 0.3, 'gamma': 0.3}
        r = calc_contribution(bd)
        assert r['total'] == pytest.approx(1.0, abs=1e-4)


# ─────────────────────────────────────────────────────────
# calc_feature_contribution
# ─────────────────────────────────────────────────────────
class TestCalcFeatureContribution:
    def test_returns_all_eight_features(self):
        result = calc_feature_contribution(_BREAKDOWN, _COMPANY)
        expected_keys = {
            'debt_ratio', 'cash_flow', 'operating_profit',
            'patent_count', 'is_venture', 'is_innobiz',
            'youth_employment', 'credit_grade',
        }
        assert set(result.keys()) == expected_keys

    def test_sum_equals_P(self):
        result = calc_feature_contribution(_BREAKDOWN, _COMPANY)
        assert sum(result.values()) == pytest.approx(_EXPECTED_P, abs=1e-4)

    def test_non_negative_values(self):
        result = calc_feature_contribution(_BREAKDOWN, _COMPANY)
        for feat, val in result.items():
            assert val >= 0.0, f"{feat} should be non-negative"

    def test_inactive_features_are_zero(self):
        result = calc_feature_contribution(_BREAKDOWN, _COMPANY)
        assert result['is_innobiz']    == 0.0
        assert result['credit_grade']  == 0.0

    def test_null_financial_data(self):
        """재무 데이터 전체 null이어도 0 반환."""
        company_null = {
            'debt_ratio': None, 'cash_flow': None, 'operating_profit': None,
            'revenue': None, 'patent_count': None,
            'is_venture': False, 'is_innobiz': False,
            'youth_employment': False, 'credit_grade': None,
        }
        result = calc_feature_contribution(_BREAKDOWN, company_null)
        assert result['debt_ratio']       == 0.0
        assert result['cash_flow']        == 0.0
        assert result['operating_profit'] == 0.0
        assert result['patent_count']     == 0.0
        assert sum(result.values())       == 0.0


# ─────────────────────────────────────────────────────────
# calc_delta
# ─────────────────────────────────────────────────────────
class TestCalcDelta:
    def test_debt_ratio_positive_when_over_limit(self):
        """부채비율이 한도를 초과하면 양수 delta."""
        company = {**_COMPANY, 'debt_ratio': 330.0}
        program = {'debt_ratio_limit': 300.0, 'requirements': []}
        delta = calc_delta(company, program)
        assert delta['debt_ratio'] > 0

    def test_debt_ratio_negative_when_under_limit(self):
        """부채비율이 한도 이하이면 음수 delta (여유)."""
        company = {**_COMPANY, 'debt_ratio': 150.0}
        program = {'debt_ratio_limit': 300.0, 'requirements': []}
        delta = calc_delta(company, program)
        assert delta['debt_ratio'] < 0

    def test_debt_ratio_normalized(self):
        """delta = (current - limit) / limit"""
        company = {**_COMPANY, 'debt_ratio': 330.0}
        program = {'debt_ratio_limit': 300.0, 'requirements': []}
        delta = calc_delta(company, program)
        assert delta['debt_ratio'] == pytest.approx((330 - 300) / 300, abs=1e-4)

    def test_business_age_positive_when_below_requirement(self):
        """업력 미달 시 양수 delta."""
        company = {**_COMPANY, 'business_age': 2}
        program = {'debt_ratio_limit': None, 'requirements': ['업력 3년 이상']}
        delta = calc_delta(company, program)
        assert delta['business_age'] > 0

    def test_business_age_negative_when_above_requirement(self):
        """업력 충족 시 음수 delta (여유)."""
        company = {**_COMPANY, 'business_age': 5}
        program = {'debt_ratio_limit': None, 'requirements': ['업력 3년 이상']}
        delta = calc_delta(company, program)
        assert delta['business_age'] < 0

    def test_no_constraints_returns_empty(self):
        """자격요건 없으면 빈 dict."""
        program = {'debt_ratio_limit': None, 'requirements': []}
        delta = calc_delta(_COMPANY, program)
        assert delta == {}

    def test_missing_company_values_skipped(self):
        """company_features에 debt_ratio 없으면 해당 항목 생략."""
        company = {k: v for k, v in _COMPANY.items() if k != 'debt_ratio'}
        program = {'debt_ratio_limit': 300.0, 'requirements': []}
        delta = calc_delta(company, program)
        assert 'debt_ratio' not in delta


# ─────────────────────────────────────────────────────────
# get_improvable_features
# ─────────────────────────────────────────────────────────
class TestGetImprovableFeatures:
    def test_returns_barely_failing_features(self):
        """0 < delta <= threshold 항목만 반환."""
        delta = {'debt_ratio': 0.07, 'business_age': -0.1, 'other': 0.0}
        result = get_improvable_features(delta, threshold=0.1)
        assert result == ['debt_ratio']

    def test_threshold_boundary_inclusive(self):
        """threshold 경계값 포함."""
        delta = {'debt_ratio': 0.1}
        assert get_improvable_features(delta, threshold=0.1) == ['debt_ratio']

    def test_threshold_boundary_exclusive_at_zero(self):
        """delta == 0은 제외."""
        delta = {'debt_ratio': 0.0}
        assert get_improvable_features(delta, threshold=0.1) == []

    def test_over_threshold_excluded(self):
        """threshold 초과 항목은 제외."""
        delta = {'debt_ratio': 0.5, 'business_age': 0.4}
        assert get_improvable_features(delta, threshold=0.1) == []

    def test_negative_delta_excluded(self):
        """음수 delta (여유)는 제외."""
        delta = {'debt_ratio': -0.05}
        assert get_improvable_features(delta, threshold=0.1) == []

    def test_multiple_improvable(self):
        delta = {'debt_ratio': 0.05, 'business_age': 0.08, 'other': 0.2}
        result = get_improvable_features(delta, threshold=0.1)
        assert set(result) == {'debt_ratio', 'business_age'}

    def test_empty_delta(self):
        assert get_improvable_features({}) == []


# ─────────────────────────────────────────────────────────
# get_top_features
# ─────────────────────────────────────────────────────────
class TestGetTopFeatures:
    def test_returns_n_items(self):
        result = calc_feature_contribution(_BREAKDOWN, _COMPANY)
        top = get_top_features(result, n=3)
        assert len(top) == 3

    def test_sorted_by_value_descending(self):
        contribs = {'a': 0.3, 'b': 0.1, 'c': 0.2, 'd': 0.05}
        top = get_top_features(contribs, n=3)
        values = [v for _, v in top]
        assert values == sorted(values, reverse=True)

    def test_negatives_come_first(self):
        contribs = {'a': 0.4, 'b': -0.2, 'c': 0.3}
        top = get_top_features(contribs, n=3)
        assert top[0] == ('b', -0.2)

    def test_n_larger_than_features(self):
        contribs = {'a': 0.1, 'b': 0.2}
        top = get_top_features(contribs, n=5)
        assert len(top) == 2

    def test_returns_list_of_tuples(self):
        result = calc_feature_contribution(_BREAKDOWN, _COMPANY)
        top = get_top_features(result, n=3)
        for item in top:
            assert isinstance(item, tuple)
            assert len(item) == 2
            assert isinstance(item[0], str)
            assert isinstance(item[1], float)
