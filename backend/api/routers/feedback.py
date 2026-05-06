import logging

from fastapi import APIRouter, HTTPException

from api.schemas.response import FeedbackResponse, TopFeature, ImprovableFeature, ScoreBreakdown
from api.services.cache import feedback_cache

logger = logging.getLogger(__name__)
router = APIRouter()

_FEATURE_LABELS = {
    "debt_ratio": "부채비율",
    "patent_count": "특허 보유수",
    "is_venture": "벤처기업 인증",
    "is_innobiz": "이노비즈 인증",
    "cash_flow": "영업활동 현금흐름",
    "operating_profit": "영업이익",
    "business_age": "업력",
}


@router.get("/feedback/{program_id}", response_model=FeedbackResponse, summary="프로그램별 피드백 조회")
async def get_feedback(program_id: str) -> FeedbackResponse:
    """캐시된 매칭 결과에서 특정 프로그램의 SHAP 기반 피드백을 반환한다."""
    cached = feedback_cache.get(program_id)
    if cached is None:
        raise HTTPException(
            status_code=404,
            detail=f"program_id={program_id}에 대한 피드백 캐시가 없습니다. /match를 먼저 호출하세요.",
        )

    sb_raw = cached.get("score_breakdown") or {}
    delta_raw = cached.get("delta_analysis") or {}
    improvable = cached.get("improvable_features") or []
    contrib_raw = cached.get("contribution") or {}

    # top features from contribution
    top_features = []
    if contrib_raw:
        from agents.shap.tools import get_top_features
        feature_contrib = {
            "alpha_F": contrib_raw.get("alpha_F", 0.0),
            "beta_T": contrib_raw.get("beta_T", 0.0),
            "gamma_G": contrib_raw.get("gamma_G", 0.0),
        }
        for name, value in get_top_features(feature_contrib, n=3):
            top_features.append(TopFeature(name=name, value=round(value, 4)))

    # improvable features with delta info
    improvable_items = []
    for feat in improvable:
        delta_info = delta_raw.get(feat) or {}
        delta_pct = delta_info.get("delta_pct", 0.0) if isinstance(delta_info, dict) else float(delta_info or 0)
        improvable_items.append(
            ImprovableFeature(
                name=feat,
                label=_FEATURE_LABELS.get(feat, feat),
                delta_pct=round(delta_pct, 2),
            )
        )

    score_breakdown = None
    if sb_raw:
        score_breakdown = ScoreBreakdown(
            F=sb_raw.get("F", 0.0),
            T=sb_raw.get("T", 0.0),
            G=sb_raw.get("G", 0.0),
            alpha=sb_raw.get("alpha", 0.4),
            beta=sb_raw.get("beta", 0.3),
            gamma=sb_raw.get("gamma", 0.3),
        )

    return FeedbackResponse(
        program_id=program_id,
        program_name=cached.get("program_name", ""),
        feedback=cached.get("feedback") or "",
        top_features=top_features,
        improvable=improvable_items,
        score_breakdown=score_breakdown,
    )
