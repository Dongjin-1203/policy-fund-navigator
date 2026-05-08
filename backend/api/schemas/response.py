from typing import Optional, List
from pydantic import BaseModel, field_validator


def _to_float_or_none(v) -> Optional[float]:
    """'None' 문자열·빈값·비숫자를 None으로, 나머지는 float으로 변환."""
    if v is None or (isinstance(v, str) and v.strip().lower() in ('none', '')):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _to_int_or_none(v) -> Optional[int]:
    if v is None or (isinstance(v, str) and v.strip().lower() in ('none', '')):
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


class ScoreBreakdown(BaseModel):
    F: float
    T: float
    G: float
    alpha: float
    beta: float
    gamma: float


class Contribution(BaseModel):
    alpha_F: float
    beta_T: float
    gamma_G: float
    total: float


class TopFeature(BaseModel):
    name: str
    value: float


class ImprovableFeature(BaseModel):
    name: str
    label: str
    delta_pct: float


class ProgramItem(BaseModel):
    program_id: str
    program_name: str
    category: str
    score: float = 0.0
    max_support: Optional[int] = None
    interest_rate: Optional[float] = None
    apply_end: Optional[str] = None

    @field_validator('score', mode='before')
    @classmethod
    def _parse_score(cls, v):
        result = _to_float_or_none(v)
        return result if result is not None else 0.0

    @field_validator('max_support', mode='before')
    @classmethod
    def _parse_max_support(cls, v):
        return _to_int_or_none(v)

    @field_validator('interest_rate', mode='before')
    @classmethod
    def _parse_interest_rate(cls, v):
        return _to_float_or_none(v)


class MatchResponse(BaseModel):
    company_id: str
    status: str
    matched_count: int
    ranked_programs: List[ProgramItem]
    score_breakdown: Optional[ScoreBreakdown] = None
    contribution: Optional[Contribution] = None
    improvable_features: List[str]
    feedback: str


class FeedbackResponse(BaseModel):
    program_id: str
    program_name: str
    feedback: str
    top_features: List[TopFeature]
    improvable: List[ImprovableFeature]
    score_breakdown: Optional[ScoreBreakdown] = None
