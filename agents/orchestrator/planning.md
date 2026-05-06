# 오케스트레이터 ReAct 사고 흐름

## 케이스 1: 정상 흐름 (상장사)

**Observe:** `company_id` 수신, `company_features` 비어 있음, `dart_found is None`

**Think:** S3·DART 조회가 필요한 최초 진입 단계. corp_code 또는 corp_name이 있으면 DART 직접 조회, 없으면 corp 목록 다운로드 후 이름 매핑 시도.

**Act:** S3 `processed/company_features.parquet` 조회 → 없음 → DART 실시간 조회
  - `fetch_financial_statements(corp_code, year)`: 재무제표 (매출·이익·현금흐름·부채비율)
  - `fetch_company_info(corp_code)`: 업종코드·설립일·종업원수·소재지

**Observe:** 재무 데이터 반환됨 (`revenue`, `debt_ratio` 등 not None)

**Think:** `dart_found = True` → `company_features` 업데이트 후 임베딩 에이전트로 라우팅 가능. S3에 저장해 다음 요청 시 캐시 활용.

**Act:** `dart_found = True` 설정, S3 저장, state 반환 → LangGraph가 임베딩 에이전트로 전달

---

*(임베딩 에이전트 → 스코어링 에이전트 → SHAP 에이전트 완료 후 오케스트레이터로 복귀)*

---

**Observe:** `ranked_programs` 있음 (not None), `score_breakdown` 있음, `improvable_features` 있음

**Think:** 피드백 생성 단계. 상위 1개 score 기준 → ORCH_SUCCESS_WRAPPER 또는 ORCH_YELLOW_WRAPPER 결정.
  - score ≥ 0.3 → ORCH_SUCCESS_WRAPPER + FEEDBACK_TEMPLATES["success"]
  - score < 0.3 → ORCH_YELLOW_WRAPPER + FEEDBACK_TEMPLATES["low_score"]

**Act:**
  1. `_build_program_list(ranked)` → ORCH_PROGRAM_ITEM_FORMAT 적용 목록 문자열 생성
  2. `_build_improvable_guide(improvable_features, delta_analysis)` → 보완 가이드 생성
  3. `_call_gemini_feedback(...)` → Gemini 2.5 Flash 호출 (150자 이내 가이드)
  4. 래퍼 포맷 적용 → `feedback` 문자열 완성
  5. `response` dict 조합 → `status: "success"`

**Observe:** 최종 response 완성

**Act:** state 업데이트 (`feedback`, `response`) → FastAPI로 반환

---

## 케이스 2: 비상장 기업

**Observe:** `company_id` 수신, `company_features` 비어 있음

**Think:** S3에 데이터 없음 → DART 조회 필요. corp_code/corp_name 모두 없으면 corp 목록 전체 다운로드 후 매핑 시도.

**Act:** DART 조회 시도 → `fetch_financial_statements` 실행

**Observe:** 재무 데이터 없음 (비상장, DART 미등록 또는 재무공시 없음)

**Think:** 공개 재무 데이터를 획득할 수 없음 → 사용자 직접 입력 필요.
  `dart_found = False`, `user_input_required = True` 반환.
  파이프라인 중단, 스코어링·SHAP 호출 불필요.

**Act:**
  1. `ORCH_REASON_MESSAGES["user_input_required"]` 를 feedback으로 사용
  2. `response` 조합 → `status: "user_input_required"`, `matched_count: 0`
  3. state 반환 (임베딩 에이전트로 라우팅하지 않음)

**Observe:** FastAPI가 `user_input_required: true` 응답 수신

**결과:** 프론트엔드가 재무 입력 폼을 표시하고, 사용자 입력 후 `/match` 재요청 (이번에는 `company_features`에 재무 데이터 포함)

---

## 케이스 3: 후보 없음 (임베딩 에이전트 조기 복귀)

**Observe:** `dart_found = True` (DART 조회 성공), `candidate_programs = []` (Hard/Soft Filter 통과 없음)

**Think:** 임베딩 에이전트가 LangGraph 조건부 엣지를 통해 오케스트레이터로 조기 복귀.
  `ranked_programs = []`, `score_breakdown = {}` 상태. 스코어링·SHAP 호출 불필요.
  오류가 아닌 정상 케이스 — `error` 필드 None 유지.

**Act:**
  1. `ranked_programs is not None` 조건 충족 → 피드백 생성 단계 진입
  2. `ranked` 비어 있음 → ORCH_RED_WRAPPER + ORCH_REASON_MESSAGES["no_candidates"] 선택
  3. `ORCH_RED_WRAPPER.format(reason=ORCH_REASON_MESSAGES["no_candidates"])` → feedback 문자열
  4. `response` 조합 → `status: "no_match"`, `matched_count: 0`

**Observe:** 안내 메시지 완성

**Act:** state 업데이트 → FastAPI로 반환

**결과:** 사용자에게 "현재 조건에 맞는 공고 없음" 안내, 향후 공고 갱신 후 재시도 유도
