import io
import json
import logging
import math
import os
import re
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
# 데이터 미수집 항목(ipc_codes, latest_patent_year)은
# calc_tech_score() 내에서 patent_score에 동적 재배분됨
_T_PATENT_WEIGHT  = 0.6
_T_IPC_WEIGHT     = 0.2
_T_RECENCY_WEIGHT = 0.2

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


def _safe_numeric(v, default=None):
    """NaN/None/비숫자 → default 반환. 정상값은 float 반환."""
    if v is None:
        return default
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def _fetch_patent_count_from_s3(company_features: dict) -> int | None:
    """S3 raw/kipris/ 에서 corp_name 기반 최신 patent_count 조회.

    patent_count가 company_features에 없을 때 폴백으로 호출.
    S3 키: raw/kipris/{date}/{safe_corp_name}.json
    """
    corp_name = (
        company_features.get('corp_name') or
        company_features.get('company_name')
    )
    if not corp_name:
        logger.debug('KIPRIS S3 조회 스킵: corp_name 없음')
        return None

    bucket = os.environ.get('S3_BUCKET_NAME', '')
    if not bucket:
        return None

    try:
        import boto3
        s3 = boto3.client('s3')
        safe_name = corp_name.replace('/', '_').replace(' ', '_')

        resp = s3.list_objects_v2(Bucket=bucket, Prefix='raw/kipris/', Delimiter='/')
        date_folders = sorted(
            [cp['Prefix'].rstrip('/').split('/')[-1] for cp in resp.get('CommonPrefixes', [])],
            reverse=True,
        )
        for date_str in date_folders:
            key = f'raw/kipris/{date_str}/{safe_name}.json'
            try:
                obj = s3.get_object(Bucket=bucket, Key=key)
                data = json.loads(obj['Body'].read())
                count = data.get('patent_count')
                if count is not None:
                    logger.info(
                        'KIPRIS S3 폴백 성공: corp=%s patent_count=%d (date=%s)',
                        corp_name, count, date_str,
                    )
                    return int(count)
            except Exception:
                continue
    except Exception as exc:
        logger.warning('KIPRIS S3 조회 실패 corp=%s: %s', corp_name, exc)
    return None


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

    구성 (기본 가중치):
    - 특허 보유수 (_T_PATENT_WEIGHT=0.6): _PATENT_NORM(5건) 기준 정규화
    - IPC 코드 존재 여부 (_T_IPC_WEIGHT=0.2): KIPRIS ipc_codes 필드
    - 특허 출원일 최신성 (_T_RECENCY_WEIGHT=0.2): 3년 이내 = 1.0

    데이터 미수집 처리:
    - patent_count None/NaN → S3 KIPRIS 폴백 조회 후에도 없으면 0.0
    - ipc_codes / latest_patent_year 없으면 해당 가중치를 patent_score에 재배분
    """
    # ── patent_count: NaN 방어 + S3 폴백 ──
    patent_count = _safe_numeric(company_features.get('patent_count'))
    if patent_count is None:
        patent_count = _fetch_patent_count_from_s3(company_features)
        if patent_count is not None:
            patent_count = float(patent_count)

    patent_score = (
        min(1.0, patent_count / _PATENT_NORM)
        if (patent_count is not None and patent_count >= 0) else 0.0
    )

    # ── IPC 코드 ──
    ipc_codes = company_features.get('ipc_codes')
    ipc_available = bool(ipc_codes)
    ipc_score = 1.0 if ipc_available else 0.0

    # ── 특허 최신성 ──
    latest_patent_year = _safe_numeric(company_features.get('latest_patent_year'))
    recency_available = latest_patent_year is not None
    recency_score = 0.0
    if recency_available:
        current_year = datetime.now().year
        recency_score = 1.0 if (current_year - int(latest_patent_year)) <= 3 else 0.0

    # ── 데이터 미수집 항목의 가중치를 patent_score에 재배분 ──
    effective_patent_weight = _T_PATENT_WEIGHT
    if not ipc_available:
        effective_patent_weight += _T_IPC_WEIGHT
    if not recency_available:
        effective_patent_weight += _T_RECENCY_WEIGHT

    score = (
        effective_patent_weight * patent_score +
        (_T_IPC_WEIGHT     * ipc_score     if ipc_available     else 0.0) +
        (_T_RECENCY_WEIGHT * recency_score if recency_available else 0.0)
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
    """MLflow → S3 → 초기값 순으로 α, β, γ 로드.

    Returns:
        (alpha, beta, gamma) — 합이 1.0
    """
    # 1. MLflow
    try:
        import mlflow
        tracking_uri = os.environ.get('MLFLOW_TRACKING_URI', 'http://localhost:5000')
        mlflow.set_tracking_uri(tracking_uri)
        client = mlflow.tracking.MlflowClient()
        experiment = client.get_experiment_by_name('scoring_params')
        if experiment:
            runs = client.search_runs(
                experiment_ids=[experiment.experiment_id],
                order_by=['start_time DESC'],
                max_results=1,
            )
            if runs:
                params = runs[0].data.params
                alpha = float(params['alpha'])
                beta  = float(params['beta'])
                gamma = float(params['gamma'])
                logger.info(
                    "MLflow 파라미터 로드: α=%.4f, β=%.4f, γ=%.4f (run_id=%s)",
                    alpha, beta, gamma, runs[0].info.run_id,
                )
                return alpha, beta, gamma
    except Exception as exc:
        logger.warning("MLflow 로드 실패: %s", exc)

    # 2. S3 fallback
    try:
        import boto3
        bucket = os.environ['S3_BUCKET_NAME']
        s3 = boto3.client('s3')
        obj = s3.get_object(Bucket=bucket, Key='processed/scoring_params.json')
        params = json.loads(obj['Body'].read())
        alpha = float(params['alpha'])
        beta  = float(params['beta'])
        gamma = float(params['gamma'])
        logger.info(
            "S3 파라미터 로드: α=%.4f, β=%.4f, γ=%.4f",
            alpha, beta, gamma,
        )
        return alpha, beta, gamma
    except Exception as exc:
        logger.warning("S3 파라미터 로드 실패: %s", exc)

    # 3. 초기값
    logger.warning("파라미터 로드 실패 — 초기값 사용 (α=0.4, β=0.3, γ=0.3)")
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
