from typing import Optional, List
from pydantic import BaseModel


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
    score: float
    max_support: Optional[int] = None
    interest_rate: Optional[float] = None
    apply_end: Optional[str] = None


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
