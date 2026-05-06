# ---------------------------------------------------------
# 🟢 [GREEN] 즉시 지원 가능 사업 템플릿
# ---------------------------------------------------------
SUCCESS_WRAPPER = """
[🟢 적격: 지원 가능]

분석 결과, 귀사의 현재 조건은 아래 사업의 지원 자격 가이드라인을 충족합니다. 
유사도 점수가 높은 순으로 정렬된 리스트입니다.

{matched_programs}

※ 주의: 본 결과는 입력된 메타데이터에 기반한 1차 판정이며, 상세한 내용은 공고문을 확인할 것을 권장합니다.
"""

PROGRAM_ITEM_FORMAT = "- [{score:.3f}] {program_name} / {category} / (마감일: {apply_end})"

# ---------------------------------------------------------
# 🟡 [YELLOW] 조건부 대기 / 보완 필요 사업 템플릿
# ---------------------------------------------------------
YELLOW_WRAPPER = """
[🟡 조건부 대기 / 보완 필요 사업]
현재 요건에 소폭 미달이거나 대기 중이지만, 약간의 보완이나 준비를 통해 지원을 노려볼 수 있는 사업입니다.

{pending_programs}
"""

YELLOW_ITEM_FORMAT = "- [{score:.3f}점] {program_name} / {category} / (마감일: {apply_end})"

# ---------------------------------------------------------
# 🔴 [RED] 즉시 탈락 사업 템플릿 (개별 사업 검증/검색용)
# ---------------------------------------------------------
RED_WRAPPER = """
[🔴 지원 불가 사업]
조회하신 사업은 귀사의 기본 정보와 원천적으로 부합하지 않아 검토 대상에서 제외되었습니다.

■ 대상 사업: {program_name}

■ 결격 사유 상세:
{reasons_list}

해당 사업은 당장 수정 불가능한 결격 사유를 포함하고 있으므로, 타 사업 검색을 권장합니다.
"""

# ---------------------------------------------------------
# 공통 서브 불릿 템플릿 (사유 및 주의사항)
# ---------------------------------------------------------
REASON_SUB_BULLET = "  ↳ 💡 상세 사유: {reason_message}"
CAUTION_SUB_BULLET = "  ↳ ⚠️ 주의사항: {caution_note}"

# ---------------------------------------------------------
# 사유별 맞춤형 메시지 딕셔너리 (YELLOW & RED 통합)
# ---------------------------------------------------------
REASON_MESSAGES = {
    # --- YELLOW (보완 가능) ---
    "age_shortage_minor": "요구 업력({required_val}년)에 1년 모자랍니다. 예외 조항이 있는지 확인해 보세요.",
    "revenue_shortage_minor": "매출액이 기준({required_val}원)에 살짝 미달합니다. 수출 실적 등으로 대체 가능한지 문의해 보세요.",
    "export_shortage_minor": "수출액이 기준({required_val}$)에 약간 부족합니다. 간접 수출 실적 인정 여부를 확인해 보세요.",
    "employees_shortage_minor": "상시 근로자 수가 기준({required_val}명)에 아깝게 미달합니다.",
    "debt_excess_minor": "부채비율({company_val}%)이 허용 기준({required_val}%)을 소폭 초과했습니다. 재무제표 개선이 필요합니다.",
    "not_started_yet": "접수 시작일({required_val})이 아직 도래하지 않았습니다. 캘린더에 메모해 두세요!",
    
    # --- RED (즉시 탈락 사유 - 에이전트 개별 검색 피드백용) ---
    "company_type_mismatch": "기업 형태({company_val})가 지원 대상({required_val})이 아닙니다.",
    "industry_mismatch": "주력 업종이 이 사업의 지원 대상 분야와 다릅니다.",
    "industry_limit_hit": "귀사의 업종은 이 사업에서 명시적으로 '지원 제외' 대상으로 분류되어 있습니다.",
    "region_mismatch": "사업장 소재지({company_val})가 해당 사업의 지원 대상 지역({required_val})에 포함되지 않습니다.",
    "age_excess": "업력({company_val}년)이 지원 상한선({required_val}년)을 초과했습니다.",
    "age_shortage_critical": "요구하는 최소 업력({required_val}년)에 크게 미달합니다.",
    "revenue_excess": "매출 규모가 소상공인/소기업 지원 상한선을 초과했습니다.",
    "revenue_shortage_critical": "요구하는 최소 매출액({required_val}원) 기준에 미달합니다.",
    "export_shortage_critical": "본격적인 수출 실적({required_val}$) 증빙이 필요한 사업입니다.",
    "employees_excess": "근로자 수가 상한선({required_val}명)을 초과했습니다.",
    "employees_shortage_critical": "최소 고용 인원({required_val}명) 요건을 충족하지 못했습니다.",
    "debt_excess_critical": "부채비율({company_val}%)이 허용 기준({required_val}%)을 크게 초과하여 지원이 불가합니다.",
    "deadline_passed": "이미 접수가 마감({required_val})된 사업입니다."
}

# ======================================================================
# 헬퍼 함수: 에러 코드를 문장으로 변환
# ======================================================================
def get_feedback_message(reason_code: str, context_data: dict) -> str:
    """
    embedder.py에서 발생한 사유 코드(예: 'region_mismatch (Required: [서울])')를 받아
    적절한 템플릿에 매핑하여 사람이 읽기 좋은 문장으로 반환합니다.
    """
    # ' (' 기준으로 잘라서 순수 베이스 코드만 추출 (예: 'region_mismatch')
    base_code = reason_code.split(" (")[0].strip()
    
    # 딕셔너리에서 매핑 (정의되지 않은 코드면 기본 메시지 출력)
    template = REASON_MESSAGES.get(base_code, f"요건 재검토 필요 ({reason_code})")
    
    # 변수 안전 삽입
    try:
        return template.format(**context_data)
    except KeyError as e:
        # 템플릿에 필요한 변수가 context_data에 없을 경우 에러 방지
        return template.replace(f"{{{e.args[0]}}}", "정보없음")