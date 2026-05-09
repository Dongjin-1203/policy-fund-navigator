from typing import Optional
from pydantic import BaseModel, Field


class FinancialData(BaseModel):
    revenue: Optional[float] = Field(None, description="매출액 (원)")
    operating_profit: Optional[float] = Field(None, description="영업이익 (원)")
    capital: Optional[float] = Field(None, description="자본금 (원)")
    debt_ratio: Optional[float] = Field(None, description="부채비율 (%)")
    net_income: Optional[float] = Field(None, description="당기순이익 (원)")
    cash_flow: Optional[float] = Field(None, description="영업활동 현금흐름 (원)")
    is_venture: Optional[bool] = Field(None, description="벤처기업 인증 여부 (financial_data 내 입력 허용)")
    is_innobiz: Optional[bool] = Field(None, description="이노비즈 인증 여부 (financial_data 내 입력 허용)")
    patent_count: Optional[int] = Field(None, description="특허 보유수 (financial_data 내 입력 허용)")


class MatchRequest(BaseModel):
    company_id: str = Field(..., description="사업자등록번호")
    corp_name: Optional[str] = Field(None, description="기업명 (DART 매칭용)")
    corp_code: Optional[str] = Field(None, description="DART 고유번호 (있으면 직접 사용)")
    industry_code: Optional[str] = Field(None, description="업종코드 (KSIC)")
    region: Optional[str] = Field(None, description="소재지")
    employee_count: Optional[int] = Field(None, description="종업원수")
    business_age: Optional[int] = Field(None, description="업력 (년)")
    patent_count: Optional[int] = Field(None, description="특허 보유수")
    is_venture: Optional[bool] = Field(False, description="벤처기업 인증 여부")
    is_innobiz: Optional[bool] = Field(False, description="이노비즈 인증 여부")
    credit_grade: Optional[str] = Field(None, description="신용등급")
    financial_data: Optional[FinancialData] = Field(None, description="재무 데이터 (비상장 기업 직접 입력)")
