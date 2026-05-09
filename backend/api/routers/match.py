import logging
from typing import Optional

from fastapi import APIRouter, HTTPException

from api.schemas.request import MatchRequest
from api.schemas.response import MatchResponse, ProgramItem, ScoreBreakdown, Contribution
from api.services.cache import feedback_cache

logger = logging.getLogger(__name__)
router = APIRouter()


def _build_initial_state(req: MatchRequest) -> dict:
    """MatchRequest → PolicyFundState 초기값 변환."""
    company_features: dict = {
        "company_id": req.company_id,
        "industry_code": req.industry_code,
        "region": req.region,
        "employee_count": req.employee_count,
        "business_age": req.business_age,
        "patent_count": req.patent_count,
        "is_venture": req.is_venture or False,
        "is_innobiz": req.is_innobiz or False,
        "credit_grade": req.credit_grade,
    }
    if req.corp_name:
        company_features["corp_name"] = req.corp_name
    if req.corp_code:
        company_features["corp_code"] = req.corp_code

    if req.financial_data:
        fd = req.financial_data
        company_features.update({
            "revenue": fd.revenue,
            "operating_profit": fd.operating_profit,
            "capital": fd.capital,
            "debt_ratio": fd.debt_ratio,
            "net_income": fd.net_income,
            "cash_flow": fd.cash_flow,
        })

    return {
        "company_id": req.company_id,
        "company_features": company_features,
        "dart_found": None,
        "user_input_required": False,
        "candidate_programs": None,
        "ranked_programs": None,
        "score_breakdown": None,
        "contribution": None,
        "delta_analysis": None,
        "improvable_features": None,
        "feedback": None,
        "response": None,
        "error": None,
    }


_INVALID_DATE_STRINGS = frozenset({'none', 'nan', ''})


def _clean_apply_end(v) -> Optional[str]:
    """'None'·'nan'·빈값을 None으로 정규화. 유효한 날짜 문자열은 그대로 반환."""
    if v is None:
        return None
    s = str(v).strip()
    if s.lower() in _INVALID_DATE_STRINGS:
        return None
    return s


def _parse_response(result: dict) -> MatchResponse:
    """MAS 최종 state → MatchResponse 변환."""
    resp = result.get("response") or {}
    ranked_raw = resp.get("ranked_programs") or []
    sb_raw = resp.get("score_breakdown") or {}
    contrib_raw = result.get("contribution") or {}

    # program_id 기준 중복 제거 (score 높은 순으로 이미 정렬되어 있으므로 첫 번째 유지)
    seen_ids: set[str] = set()
    deduped_raw = []
    for p in ranked_raw:
        pid = p.get("program_id", "")
        if pid and pid not in seen_ids:
            seen_ids.add(pid)
            deduped_raw.append(p)

    ranked_programs = [
        ProgramItem(
            program_id=p.get("program_id", ""),
            program_name=p.get("program_name", ""),
            category=p.get("category", "기타"),
            score=p.get("score", 0.0),
            max_support=p.get("max_support"),
            interest_rate=p.get("interest_rate"),
            apply_end=_clean_apply_end(p.get("apply_end")),
        )
        for p in deduped_raw
    ]

    score_breakdown: Optional[ScoreBreakdown] = None
    if sb_raw:
        score_breakdown = ScoreBreakdown(
            F=sb_raw.get("F", 0.0),
            T=sb_raw.get("T", 0.0),
            G=sb_raw.get("G", 0.0),
            alpha=sb_raw.get("alpha", 0.4),
            beta=sb_raw.get("beta", 0.3),
            gamma=sb_raw.get("gamma", 0.3),
        )

    contribution: Optional[Contribution] = None
    if contrib_raw:
        contribution = Contribution(
            alpha_F=contrib_raw.get("alpha_F", 0.0),
            beta_T=contrib_raw.get("beta_T", 0.0),
            gamma_G=contrib_raw.get("gamma_G", 0.0),
            total=contrib_raw.get("total", 0.0),
        )

    return MatchResponse(
        company_id=resp.get("company_id", ""),
        status=resp.get("status", "unknown"),
        matched_count=len(ranked_programs),
        ranked_programs=ranked_programs,
        score_breakdown=score_breakdown,
        contribution=contribution,
        improvable_features=resp.get("improvable_features") or [],
        feedback=resp.get("feedback") or "",
    )


@router.post("/match", response_model=MatchResponse, summary="정책자금 매칭")
async def match(req: MatchRequest) -> MatchResponse:
    """기업 정보를 받아 LangGraph MAS를 실행하고 정책자금 매칭 결과를 반환한다."""
    import asyncio
    from agents.graph import app as mas_app

    state = _build_initial_state(req)

    try:
        result = await asyncio.to_thread(mas_app.invoke, state)
    except Exception as exc:
        logger.error("MAS invoke 실패 company_id=%s: %s", req.company_id, exc)
        raise HTTPException(status_code=500, detail=f"매칭 처리 중 오류가 발생했습니다: {exc}")

    if not result.get("response"):
        raise HTTPException(status_code=500, detail="응답 생성 실패: MAS가 response를 반환하지 않았습니다.")

    response = _parse_response(result)

    # 피드백 캐시에 저장 (program_id 기반 개별 조회용)
    for p in result.get("response", {}).get("ranked_programs") or []:
        pid = p.get("program_id")
        if pid:
            feedback_cache.set(pid, {
                "program_id": pid,
                "program_name": p.get("program_name", ""),
                "score_breakdown": result.get("score_breakdown"),
                "contribution": result.get("contribution"),
                "delta_analysis": result.get("delta_analysis"),
                "improvable_features": result.get("improvable_features") or [],
                "feedback": result.get("feedback") or "",
            })

    return response
