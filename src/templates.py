# 자격 요건 충족 시 (Success)
SUCCESS_MATCH = """
[검증 결과: 적격]

분석 결과, 귀사의 현재 데이터는 아래 사업의 지원 자격 가이드라인을 충족합니다. 
유사도 점수가 높은 순으로 정렬된 리스트입니다.

{matched_programs}

※ 주의: 본 결과는 입력된 메타데이터에 기반한 1차 판정이며, 실제 서류 심사 시 결과가 달라질 수 있음.
"""

# 예시: 수정하기
PROGRAM_ITEM_FORMAT = "- [{score:.3f}] {title} / {category}"

# 하드 필터 미달로 인한 즉시 탈락 (Hard Rejection)
HARD_REJECTION = """
[검증 결과: 부적격 - 지원 불가]

아래 사업은 귀사의 기본 정보(업종, 지역 등)가 공고 요건과 일치하지 않아 검토 대상에서 제외되었습니다.

■ 대상 사업: {announcement_title}

■ 결격 사유 요약:
{exclusion_reasons}

■ 상세 분석:
- 지역 요건: {region_status}
- 업종 적합성: {industry_status}
- 제외 대상 여부: {limit_status}

해당 사업은 수정 불가능한 원천적 결격 사유를 포함하고 있으므로, 타 사업 검색을 권장함.
"""
# 부채비율 등 개선 가능 항목에 대한 피드백 (Debt Feedback)
DEBT_ADVICE = """
[검증 결과: 반려 (조건부 재검토 가능)]

귀사가 요청한 사업에 대해 정밀 매칭을 수행했으나, 재무 건전성 지표가 기준치를 초과하여 '{announcement_title}' 사업에서 '탈락' 처리되었습니다.

■ 미달 항목: 부채비율 (Debt Ratio)
- 귀사 현재 수치: {company_ratio}%
- 사업 허용 기준: {limit_ratio}% 이하

■ 평가관 권고:
본 사업은 기술력보다 재무 안정성을 우선 검토함. 현재 수치로는 행정 절차 진행이 불가능함. 
재무제표 결산 또는 자본 확충을 통해 부채비율을 {limit_ratio}% 이하로 조정할 경우에만 재검토 가치가 있음.
"""

FEEDBACK_TEMPLATES = {
    "success": SUCCESS_MATCH,
    "hard_rejection": HARD_REJECTION,
    "debt_excess": DEBT_ADVICE
}