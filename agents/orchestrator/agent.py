import io
import logging
import os
from datetime import datetime
from typing import Optional

import boto3
import pandas as pd

from agents.state import PolicyFundState
from src.templates import (
    FEEDBACK_TEMPLATES,
    ORCH_SUCCESS_WRAPPER,
    ORCH_YELLOW_WRAPPER,
    ORCH_RED_WRAPPER,
    ORCH_PROGRAM_ITEM_FORMAT,
    ORCH_REASON_MESSAGES,
)
from dags.extractors.dart_extractor import (
    fetch_corp_list,
    fetch_financial_statements,
    fetch_company_info,
)

logger = logging.getLogger(__name__)

_GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
_SCORE_LOW_THRESHOLD = 0.3
_FEATURE_LABELS = {
    "debt_ratio": "부채비율",
    "patent_count": "특허 보유수",
    "is_venture": "벤처기업 인증",
    "is_innobiz": "이노비즈 인증",
    "cash_flow": "영업활동 현금흐름",
    "operating_profit": "영업이익",
}


# ── Gemini ────────────────────────────────────────────────────────────────────

def _get_gemini_model():
    try:
        import google.generativeai as genai
    except ImportError as exc:
        raise ImportError("google-generativeai 패키지가 설치되지 않았습니다.") from exc

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY 환경변수가 설정되지 않았습니다.")
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(_GEMINI_MODEL)


def _call_gemini_feedback(
    company: dict,
    ranked: list,
    improvable_features: list,
    score_breakdown: dict,
    base_message: str,
) -> str:
    """Gemini API 호출로 150자 이내 자연어 개선 가이드 생성. 실패 시 base_message 반환."""
    try:
        model = _get_gemini_model()
        alpha = score_breakdown.get("alpha", 0.4)
        beta = score_breakdown.get("beta", 0.3)
        gamma = score_breakdown.get("gamma", 0.3)
        F = score_breakdown.get("F", 0)
        T = score_breakdown.get("T", 0)
        G = score_breakdown.get("G", 0)
        total_score = round(alpha * F + beta * T + gamma * G, 2)

        prompt = (
            "당신은 중소기업 정책자금 전문 컨설턴트입니다.\n"
            "아래 기업 분석 결과를 바탕으로 150자 이내의 맞춤 개선 가이드를 작성하세요.\n"
            "현재 강점 1개 → 핵심 보완 항목 1~2개 순서로 작성하세요.\n\n"
            f"[기업 현황]\n"
            f"- 부채비율: {company.get('debt_ratio', '미상')}%\n"
            f"- 특허 보유수: {company.get('patent_count', 0)}건\n"
            f"- 벤처 인증: {'있음' if company.get('is_venture') else '없음'}\n"
            f"- 이노비즈 인증: {'있음' if company.get('is_innobiz') else '없음'}\n\n"
            f"[점수 분석]\n"
            f"- 재무 점수(F): {F:.2f}\n"
            f"- 기술 점수(T): {T:.2f}\n"
            f"- 정책 가점(G): {G:.2f}\n"
            f"- 종합 점수: {total_score:.2f}\n\n"
            f"[추천 사업 수]: {len(ranked)}건\n"
            f"[보완 가능 항목]: {', '.join(improvable_features) if improvable_features else '없음'}\n\n"
            f"[기본 피드백]\n{base_message}\n\n"
            "위 정보를 바탕으로 전문적이고 구체적인 개선 가이드 (150자 이내):"
        )
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as exc:
        logger.error("Gemini API 호출 실패: %s", exc)
        return base_message


# ── S3 기업 데이터 ─────────────────────────────────────────────────────────────

def _load_s3_company(company_id: str) -> Optional[dict]:
    """S3 processed/company_features.parquet에서 company_id 조회."""
    bucket = os.environ.get("S3_BUCKET_NAME")
    if not bucket:
        return None
    try:
        s3 = boto3.client("s3")
        obj = s3.get_object(Bucket=bucket, Key="processed/company_features.parquet")
        df = pd.read_parquet(io.BytesIO(obj["Body"].read()))
        row = df[df["company_id"] == company_id]
        if row.empty:
            return None
        return row.iloc[0].where(pd.notna(row.iloc[0]), None).to_dict()
    except Exception as exc:
        logger.warning("S3 company_features 조회 실패: %s", exc)
        return None


def _save_s3_company(company_id: str, features: dict) -> None:
    """기업 데이터를 S3 company_features.parquet에 upsert."""
    bucket = os.environ.get("S3_BUCKET_NAME")
    if not bucket:
        return
    try:
        s3 = boto3.client("s3")
        try:
            obj = s3.get_object(Bucket=bucket, Key="processed/company_features.parquet")
            df = pd.read_parquet(io.BytesIO(obj["Body"].read()))
        except Exception:
            df = pd.DataFrame()

        new_row = pd.DataFrame([{"company_id": company_id, **features}])
        existing = df[df["company_id"] != company_id] if "company_id" in df.columns else df
        df = pd.concat([existing, new_row], ignore_index=True)

        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        s3.put_object(
            Bucket=bucket,
            Key="processed/company_features.parquet",
            Body=buf.getvalue(),
            ContentType="application/octet-stream",
        )
        logger.info("S3 company_features 저장 완료: company_id=%s", company_id)
    except Exception as exc:
        logger.error("S3 company_features 저장 실패: %s", exc)


# ── DART 온디맨드 조회 ────────────────────────────────────────────────────────

def _has_financial_data(features: dict) -> bool:
    return any(
        features.get(k) is not None
        for k in ("revenue", "debt_ratio", "operating_profit")
    )


def _lookup_dart(company_features: dict) -> Optional[dict]:
    """DART API 온디맨드 조회.

    corp_code가 있으면 직접 사용.
    없으면 corp_name으로 전체 corp 목록에서 매핑 시도.
    재무 데이터가 없으면 None 반환 (비상장 기업).
    """
    corp_code = company_features.get("corp_code")

    if not corp_code:
        corp_name = company_features.get("corp_name")
        if not corp_name:
            return None
        try:
            corps = fetch_corp_list()
            matched = [c for c in corps if c.get("corp_name") == corp_name]
            if not matched:
                logger.info("DART corp 목록에서 이름 매칭 실패: corp_name=%s", corp_name)
                return None
            corp_code = matched[0]["corp_code"]
        except Exception as exc:
            logger.warning("DART corp 목록 조회 실패: %s", exc)
            return None

    year = datetime.now().year - 1
    try:
        financial = fetch_financial_statements(corp_code, year)
        company_info = fetch_company_info(corp_code)
        return {**financial, **company_info, "corp_code": corp_code}
    except Exception as exc:
        logger.warning("DART 재무 조회 실패 corp_code=%s: %s", corp_code, exc)
        return None


# ── 피드백 생성 헬퍼 ──────────────────────────────────────────────────────────

def _select_wrapper_and_base(
    company: dict,
    ranked: list,
    score: float,
) -> tuple[str, str]:
    """템플릿 래퍼 타입과 base 메시지 선택.

    Returns:
        (wrapper_type, base_message)
        wrapper_type: "success" | "yellow" | "red"
    """
    if not ranked:
        return "red", ORCH_REASON_MESSAGES["no_candidates"]

    if score < _SCORE_LOW_THRESHOLD:
        base = FEEDBACK_TEMPLATES["low_score"].format(score=round(score * 100, 1))
        return "yellow", base

    top = ranked[0]
    base = FEEDBACK_TEMPLATES["success"].format(
        announcement_title=top.get("program_name", ""),
        score=round(score * 100, 1),
    )
    return "success", base


def _build_program_list(ranked: list) -> str:
    """ORCH_PROGRAM_ITEM_FORMAT을 적용한 프로그램 목록 문자열 생성."""
    items = []
    for i, p in enumerate(ranked):
        max_support = p.get("max_support")
        support_str = f"{int(max_support):,}원" if max_support else "미정"
        interest_rate = p.get("interest_rate") or "-"
        items.append(
            ORCH_PROGRAM_ITEM_FORMAT.format(
                rank=i + 1,
                category=p.get("category", "기타"),
                program_name=p.get("program_name", ""),
                max_support=support_str,
                interest_rate=interest_rate,
                score=p.get("score", 0) * 100,
            )
        )
    return "\n".join(items)


def _build_improvable_guide(improvable_features: list, delta_analysis: dict) -> str:
    """보완 가능 항목별 delta 기반 가이드 문자열 생성."""
    if not improvable_features:
        return "현재 보완 가능 항목 없음"

    lines = []
    for feat in improvable_features:
        label = _FEATURE_LABELS.get(feat, feat)
        delta = (delta_analysis.get(feat) or {}).get("delta_pct", 0)
        if delta <= 10:
            lines.append(f"  - {label}: 소폭 개선으로 적격 요건 충족 가능 (개선 필요량 {delta:.1f}%)")
        else:
            lines.append(f"  - {label}: 개선 필요 (장기 과제, 개선 필요량 {delta:.1f}%)")
    return "\n".join(lines)


# ── Phase 핸들러 ──────────────────────────────────────────────────────────────

def _phase_dart_lookup(state: PolicyFundState) -> PolicyFundState:
    """Phase 1: DART 조회 및 State 초기화."""
    company_id = state.get("company_id", "")
    company_features = state.get("company_features") or {}

    # 이미 재무 데이터 있음 (직접 입력 또는 캐시)
    if _has_financial_data(company_features):
        logger.info("orchestrator: company_id=%s 재무 데이터 존재 — DART 스킵", company_id)
        return {**state, "dart_found": True, "user_input_required": False}

    # S3 캐시 조회
    cached = _load_s3_company(company_id)
    if cached and _has_financial_data(cached):
        logger.info("orchestrator: company_id=%s S3 캐시 히트", company_id)
        return {
            **state,
            "company_features": {**company_features, **cached},
            "dart_found": True,
            "user_input_required": False,
        }

    # DART 실시간 조회
    dart_data = _lookup_dart(company_features)
    if dart_data and _has_financial_data(dart_data):
        logger.info("orchestrator: company_id=%s DART 조회 성공 (상장사)", company_id)
        updated = {**company_features, **dart_data}
        _save_s3_company(company_id, updated)
        return {
            **state,
            "company_features": updated,
            "dart_found": True,
            "user_input_required": False,
        }

    # 비상장 기업 — 사용자 입력 요청
    logger.info("orchestrator: company_id=%s 재무 데이터 없음 → 사용자 입력 요청", company_id)
    feedback = ORCH_REASON_MESSAGES["user_input_required"]
    response = {
        "company_id": company_id,
        "matched_count": 0,
        "ranked_programs": [],
        "score_breakdown": {},
        "feedback": feedback,
        "improvable_features": [],
        "status": "user_input_required",
    }
    return {
        **state,
        "dart_found": False,
        "user_input_required": True,
        "feedback": feedback,
        "response": response,
    }


def _phase_generate_feedback(state: PolicyFundState) -> PolicyFundState:
    """Phase 2: SHAP 완료 후 피드백 생성 및 최종 응답 조합."""
    company_id = state.get("company_id", "")
    company = state.get("company_features") or {}
    ranked = state.get("ranked_programs") or []
    score_breakdown = state.get("score_breakdown") or {}
    improvable_features = state.get("improvable_features") or []
    delta_analysis = state.get("delta_analysis") or {}

    score = ranked[0].get("score", 0) if ranked else 0
    wrapper_type, base_message = _select_wrapper_and_base(company, ranked, score)

    if wrapper_type == "success":
        gemini_feedback = _call_gemini_feedback(
            company, ranked, improvable_features, score_breakdown, base_message
        )
        feedback = ORCH_SUCCESS_WRAPPER.format(
            company_id=company_id,
            count=len(ranked),
            program_list=_build_program_list(ranked),
            gemini_feedback=gemini_feedback,
        )
        status = "success"

    elif wrapper_type == "yellow":
        improvable_guide = _build_improvable_guide(improvable_features, delta_analysis)
        gemini_reason = _call_gemini_feedback(
            company, ranked, improvable_features, score_breakdown, base_message
        )
        feedback = ORCH_YELLOW_WRAPPER.format(
            reason=gemini_reason,
            improvable_guide=improvable_guide,
        )
        status = "low_score"

    else:
        feedback = ORCH_RED_WRAPPER.format(reason=base_message)
        status = "no_match"

    response = {
        "company_id": company_id,
        "matched_count": len(ranked),
        "ranked_programs": ranked,
        "score_breakdown": score_breakdown,
        "feedback": feedback,
        "improvable_features": improvable_features,
        "status": status,
    }

    logger.info(
        "orchestrator: company_id=%s status=%s matched=%d score=%.4f",
        company_id, status, len(ranked), score,
    )
    return {**state, "feedback": feedback, "response": response}


# ── 메인 노드 ─────────────────────────────────────────────────────────────────

def orchestrator_node(state: PolicyFundState) -> PolicyFundState:
    """오케스트레이터 에이전트 LangGraph 노드.

    1. 최초 진입 (dart_found is None): DART API 온디맨드 조회
       - 재무 있음 → dart_found = True
       - 없음 → user_input_required = True 반환
    2. 임베딩 → 스코어링 → SHAP 완료 후 복귀 (ranked_programs is not None):
       - templates.py 기반 피드백 생성
       - Gemini API로 자연어 피드백 완성
       - 최종 response 구성
    3. 후보 없음 (ranked_programs = []): ORCH_RED_WRAPPER 안내 메시지
    """
    dart_found = state.get("dart_found")
    user_input_required = state.get("user_input_required", False)
    ranked_programs = state.get("ranked_programs")

    # Phase 1: 최초 진입 — DART 조회
    if dart_found is None:
        return _phase_dart_lookup(state)

    # 사용자 입력 대기 상태 — 이미 response 구성됨
    if user_input_required:
        return state

    # Phase 2: 파이프라인 완료 후 복귀 — 피드백 생성
    if ranked_programs is not None:
        return _phase_generate_feedback(state)

    # dart_found=True이지만 ranked_programs 미설정 → 파이프라인 진행 중
    return state
