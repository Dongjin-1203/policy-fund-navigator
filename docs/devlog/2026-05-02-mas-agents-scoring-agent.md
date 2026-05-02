# [에이전틱한 개발자 되기 #5] 중진공 AI 매칭 — MAS 에이전트 스캐폴딩 & 스코어링 에이전트 구현

> 시리즈: 에이전틱한 개발자 되기  
> 태그: `LangGraph` `MAS` `scoring` `Python` `pytest` `refactoring`  
> 날짜: 2026-05-02

---

## 1. 오늘의 목표

| 목표 | 결과 |
|---|---|
| PRD/CLAUDE.md 데이터 수집 전략 업데이트 (온디맨드 + 주간 자동화) | ✅ |
| feature/llm-processor → develop 최신 코드 병합 (--no-ff) | ✅ |
| LangGraph MAS 에이전트 디렉토리 구조 생성 + PolicyFundState 구현 | ✅ |
| 스코어링 에이전트 tools.py + agent.py 구현 | ✅ |
| 단위 테스트 작성 (tests/test_scoring.py) | ✅ 20개 통과 |
| 스코어링 도구 코드 품질 개선 (마법 상수 제거, 연속값 변환) | ✅ |

---

## 2. 주요 작업 내용

### 2-1. 데이터 수집 전략 설계 문서화

기존 PRD와 CLAUDE.md는 DART 수집을 단순 "주간 배치"로만 서술했다. 실제로는 두 가지 경로가 있다.

**온디맨드 흐름** (`/match` 요청 시):
1. `company_id`로 `company_features.parquet` 조회
2. 미등록 기업 → DART API 실시간 호출 → 임시 저장
3. `user_input_required: True` 플래그 → 비상장 기업은 사용자 직접 입력 요청
4. 이후 MAS 파이프라인 진행

**주간 자동화 배치**:
- 가능: DART(기등록 기업 갱신), KIPRIS, bizinfo, 크롤러
- 불가: 벤처·이노비즈 인증(API 미제공), 신용등급(KODATA 구독 필요)

이 두 흐름을 명시하지 않으면 `PolicyFundState`에서 `dart_found`, `user_input_required` 필드가 왜 존재하는지 이해할 수 없다. PRD 섹션 4.1/4.2와 CLAUDE.md에 반영했다.

### 2-2. feature/llm-processor 병합 분석

팀원(박지윤) 브랜치를 `--no-ff`로 병합했다. 이번에는 스쿼시 대신 히스토리를 보존하는 방식을 택했다. 이유는 팀원 브랜치가 지속 유지되며 다음 병합 때 베이스 커밋이 명확해야 하기 때문이다.

새로 추가된 파일:
- `src/embedder.py` — `PolicyVectorStore`: KR-SBERT + ChromaDB Soft Filter
- `src/templates.py` — `FEEDBACK_TEMPLATES`: 결과 분기별 문구 모음

**분석 결과 발견된 이슈:**

| 항목 | 내용 | 우선순위 |
|---|---|---|
| `print()` 사용 | logging 미적용 | 중 |
| S3 연동 없음 | 로컬 ChromaDB만 | 고 |
| Hard Filter 없음 | Soft Filter만 존재 | 고 |
| 입력 스키마 불일치 | `program_name`/`requirements` vs `program_features.parquet` 컬럼명 | 고 |

임베딩 에이전트 구현 시 `PolicyVectorStore`를 래핑하면서 이 문제들을 해결할 예정이다.

### 2-3. LangGraph MAS 에이전트 구조 생성

```
agents/
├── __init__.py
├── graph.py          # LangGraph 그래프 정의 (placeholder)
├── state.py          # PolicyFundState TypedDict
├── orchestrator/     # placeholder
├── embedding/        # placeholder
├── scoring/
│   ├── __init__.py
│   ├── agent.py
│   └── tools.py
└── shap/             # placeholder
```

`PolicyFundState`에서 핵심 설계 결정:

```python
dart_found: bool           # DART 데이터 존재 여부 → 온디맨드 흐름 분기
user_input_required: bool  # 비상장 기업 사용자 입력 요청 플래그
score_breakdown: dict      # {F, T, G, alpha, beta, gamma} — 디버그/SHAP 연동
```

### 2-4. 스코어링 에이전트 구현

**수식**: `P = α·F + β·T + γ·G`

| 점수 | 구성 | 가중치 근거 |
|---|---|---|
| F (재무) | 부채비율 0.5 + 현금흐름 0.3 + 영업이익 0.2 | 중진공 공고 Hard Filter 지표 순서 반영 |
| T (기술) | 특허수 1.0 (IPC/최신성 0.0) | KIPRIS 필드 미수집 → 가중치 임시 재배분 |
| G (정책) | 벤처 0.4 + 이노비즈 0.3 + 청년고용 0.2 + 신용 0.1 | 정책 중요도 순서 반영 |
| α, β, γ | 0.4, 0.3, 0.3 | MLflow 연동 전 임시값 |

**tools.py 5개 함수:**

```python
calc_financial_score(company_features) -> float   # 0.0 ~ 1.0
calc_tech_score(company_features) -> float        # 0.0 ~ 1.0
calc_policy_score(company_features) -> float      # 0.0 ~ 1.0
load_scoring_params() -> Tuple[float, float, float]  # (α, β, γ)
generate_synthetic_label(company_features, program_features) -> int  # 0 or 1
```

**agent.py:**

```python
def scoring_node(state: PolicyFundState) -> PolicyFundState:
    F = calc_financial_score(company)
    T = calc_tech_score(company)
    G = calc_policy_score(company)
    P = round(alpha * F + beta * T + gamma * G, 4)
    ranked = sorted([{**p, 'score': P} for p in candidates],
                    key=lambda x: x['score'], reverse=True)[:10]
    return {**state, 'ranked_programs': ranked, 'score_breakdown': breakdown}
```

현재는 모든 후보 사업에 동일한 기업 점수 P를 부여한다. 추후 사업별 요건 가중치를 반영해 사업마다 다른 P를 계산하는 구조로 확장할 예정이다.

### 2-5. 스코어링 도구 코드 품질 개선

초기 구현 후 코드 리뷰에서 세 가지 문제가 발견됐다.

**① 마법 상수 (Magic Numbers)**

```python
# 수정 전
score = max(0.0, 1.0 - debt_ratio / 300)

# 수정 후
_DEBT_RATIO_BENCHMARK = 300  # 중진공 공고에서 자주 등장하는 기준값
score = max(0.0, 1.0 - debt_ratio / _DEBT_RATIO_BENCHMARK)
```

가중치 10개, 정규화 기준값 2개를 파일 상단에 모두 추출했다. 각 상수에 근거 주석을 달아 도메인 지식이 없는 사람도 왜 이 값인지 알 수 있게 했다.

**② 현금흐름/영업이익 이진 → 연속값 변환**

```python
# 수정 전 (이진)
cash_score = 1.0 if cash_flow > 0 else 0.0

# 수정 후 (연속)
cash_score = min(cash_flow / revenue, 1.0)
```

기존 코드는 현금흐름 1원과 20억이 같은 점수였다. `debt_score`는 연속값인데 `cash_score`는 이진값이면 스케일이 맞지 않는다. 매출 대비 비율로 정규화해 부채비율 점수와 동일한 스케일로 맞췄다.

**③ 기술 점수 구조적 상한 문제**

IPC 코드와 특허 출원 최신성 필드(`ipc_codes`, `latest_patent_year`)가 `company_features.parquet` 스키마에 없다. KIPRIS 파이프라인이 이 필드를 아직 수집하지 않기 때문이다.

기존 가중치(`_T_PATENT_WEIGHT=0.6, _T_IPC_WEIGHT=0.2, _T_RECENCY_WEIGHT=0.2`)로는 T 점수 최댓값이 `0.6 × 1.0 = 0.6`으로 구조적으로 상한이 낮아졌다. 해결책으로 현재 수집되지 않는 항목 가중치를 0으로 설정하고 특허수에 재배분했다.

```python
_T_PATENT_WEIGHT  = 1.0  # 데이터 없는 항목 재배분
_T_IPC_WEIGHT     = 0.0  # KIPRIS ipc_codes 수집 후 복원
_T_RECENCY_WEIGHT = 0.0  # KIPRIS latest_patent_year 수집 후 복원
```

KIPRIS 파이프라인 확장 시 `_T_PATENT_WEIGHT=0.6, _T_IPC_WEIGHT=0.2, _T_RECENCY_WEIGHT=0.2`로 복원하고 `company_features.parquet` 스키마에 두 필드를 추가하면 된다.

---

## 3. 발견된 문제 및 해결

### 🔴 Issue 1 — T 점수 구조적 상한 0.6

**문제**: IPC/최신성 가중치가 0.2씩인데 해당 데이터가 없어 항상 0. `patent_count=5`인 기업의 T 점수 최댓값이 0.6.

**해결**: KIPRIS 연동 전까지 `_T_IPC_WEIGHT=_T_RECENCY_WEIGHT=0.0`으로 설정. 복원 시점과 방법을 주석으로 명시.

---

### 🔴 Issue 2 — F 점수 스케일 불일치

**문제**: `debt_score`는 [0, 1] 연속값, `cash_score`와 `profit_score`는 0 또는 1 이진값. 현금흐름이 1원이어도 `cash_score=1.0`이 나와 부채비율 점수를 압도할 수 있었다.

**해결**: 두 항목 모두 `min(value / revenue, 1.0)` 연속값으로 변환.

---

### 🟡 Issue 3 — pytest 미설치

**문제**: `tests/test_scoring.py` 최초 실행 시 `No module named pytest`.

**해결**: `pip install pytest -q` 후 재실행. `requirements.txt`에 `pytest>=7.0` 추가.

---

## 4. 파이프라인 현황

| 컴포넌트 | 상태 | 비고 |
|---|---|---|
| OpenDART extractor | ✅ | ZIP 검증 + 상장사 필터 |
| bizinfo extractor | ✅ | jsonArray 파서 수정 완료 |
| KIPRIS extractor | ✅ | 실전 전환 완료 |
| 크롤러 (crawler.py) | ✅ | slno 기반 파일명 저장 |
| LLM 파서 (processor.py) | ✅ | LangGraph 4노드, v2 병합 완료 |
| agents/state.py | ✅ | PolicyFundState TypedDict |
| agents/scoring/ | ✅ | tools.py + agent.py, 테스트 20개 통과 |
| agents/embedding/ | ❌ | PolicyVectorStore 래핑 필요 |
| agents/shap/ | ❌ | 미구현 |
| agents/orchestrator/ | ❌ | 미구현 |
| agents/graph.py | ❌ | LangGraph 그래프 배선 미구현 |
| api/main.py | ❌ | FastAPI 엔드포인트 미구현 |

---

## 5. feature/scoring-agent 커밋 이력 (오늘)

```
95da15b  refactor: improve scoring tools
e0567da  feat: implement scoring agent
cf40834  feat: initialize MAS agent structure
0e278d3  chore: add missing packages for embedder
0e1868f  merge: integrate llm-processor updates
0b9f0e3  docs: update data collection strategy
3f83544  Merge origin/develop: LLM processor v2 + DART hardening
```

---

## 6. 남은 작업

### 단기 (다음 세션)

- [ ] agents/shap/ — SHAP TreeExplainer, feature 기여도, delta 계산
- [ ] agents/embedding/ — PolicyVectorStore 래핑, Hard Filter, S3 데이터 로드
- [ ] agents/orchestrator/ + agents/graph.py — LangGraph 그래프 배선
- [ ] feature/scoring-agent → develop PR 생성 및 병합

### 중기

- [ ] FastAPI `/match`, `/feedback/{program_id}` 구현
- [ ] MLflow 연동: `load_scoring_params()` 스텁 → 실제 run 조회
- [ ] KIPRIS 파이프라인 확장: `ipc_codes`, `latest_patent_year` 필드 추가 후 T 가중치 복원
- [ ] embedder.py 코드 품질: `print()` → `logging`, S3 연동, Hard Filter 추가

### 미결

- [ ] 수혜이력 확보 불가 → `generate_synthetic_label()` 기반 합성 레이블로 α, β, γ 추정 예정

---

## 7. 배운 점 / 느낀 점

**상수에 이름을 붙이는 것은 코드가 아니라 설계 결정을 기록하는 행위다.** `0.5`라는 숫자는 의미가 없다. `_F_DEBT_WEIGHT = 0.5  # 중진공 공고 핵심 Hard Filter 지표`는 "왜 부채비율이 현금흐름보다 가중치가 높은가"에 대한 답이다. 도메인 지식이 코드 외부에서 사라지더라도 이 한 줄이 있으면 복원할 수 있다.

**구조적 상한을 만드는 데이터 부재를 즉시 드러내야 한다.** KIPRIS 필드가 없어서 T 점수가 최대 0.6이 되는 문제를 발견했을 때, 가능한 선택지는 두 가지였다. 첫째, 현재 상태로 두고 나중에 고친다. 둘째, 가중치를 0으로 설정하고 복원 조건을 명시한다. 첫 번째는 테스트에서 잡히지 않는 버그다 — T 점수가 항상 낮게 나와도 코드 자체는 정상처럼 보인다. 두 번째는 "현재 데이터로 이 기능을 활성화할 수 없음"을 코드 레벨에서 선언하는 것이다.

**스코어링 수식은 단순하지만 각 항목의 스케일이 맞아야 의미가 있다.** `P = α·F + β·T + γ·G`는 선형 가중합이다. 각 항목이 동일한 [0, 1] 스케일에서 비교 가능해야 가중치가 의도한 대로 작동한다. `debt_score`가 연속값인데 `cash_score`가 이진값이면 `cash_score=1.0`이 연속 `debt_score=0.7`보다 항상 높아진다. 수식이 단순할수록 입력 스케일에 더 민감하다.

**테스트는 구현 검증이 아니라 설계 의도 기록이다.** `test_financial_negative_cash_zero`는 "음수 현금흐름은 null과 동일하게 처리한다"는 결정을 코드로 표현한 것이다. 이 결정이 나중에 바뀌면 테스트가 실패한다. 테스트가 실패할 때 "버그"가 아닌 "설계 변경 여부 판단"이 필요하다는 신호로 읽어야 한다.

---

*전체 코드: [github.com/Dongjin-1203/policy-fund-navigator](https://github.com/Dongjin-1203/policy-fund-navigator)*
