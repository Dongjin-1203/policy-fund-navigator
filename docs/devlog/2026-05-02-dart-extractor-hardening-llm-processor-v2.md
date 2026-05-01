# [에이전틱한 개발자 되기 #3] 중진공 AI 매칭 — DART 방어 코드 & LLM 파서 v2 병합

> 시리즈: 에이전틱한 개발자 되기  
> 태그: `DART` `LangGraph` `Gemini` `AWS S3` `git` `Python`  
> 날짜: 2026-05-02

---

## 1. 오늘의 목표

| 목표 | 결과 |
|---|---|
| DART extractor 방어 코드 추가 (ZIP 검증 + 재시도 개선) | ✅ |
| fetch_corp_list() 상장사 필터링 적용 | ✅ 117,496개 → ~2,500~3,000개 |
| feature/llm-processor 최신 코드 분석 및 병합 | ✅ Squash Merge #6 |
| 병합 후 Gemini API 실전 테스트 | ✅ HWP 1건 → 17개 세부사업 파싱 성공 |
| S3 업로드 확인 | ✅ `embeddings/requirements_db/2026-05-02/` |
| .env.example GEMINI_API_KEY 항목 추가 및 push | ✅ |

---

## 2. 주요 작업 내용

### 2-1. DART extractor 방어 코드 추가

`fetch_corp_list()`는 DART API에서 `corpCode.zip`을 내려받아 파싱한다. 기존 코드에는 두 가지 취약점이 있었다.

**① ZIP이 아닌 응답을 그대로 열려 할 경우**: API가 오류 XML을 반환해도 `zipfile.ZipFile()` 호출 직전까지 알 수 없다. `BadZipFile` 예외가 터지는 순간 원인을 알 수 없는 스택트레이스만 남는다.

**② 재시도 간격이 지수 백오프**: 기존 코드는 `time.sleep(2 ** attempt)` — 2초, 4초로 늘어나는 방식이었다. DART API 특성상 짧은 간격 재시도는 의미가 없으므로 고정 5초로 변경했다.

```python
# 추가된 ZIP 검증 코드
if not response.content.startswith(b'PK'):
    logger.error(
        'DART API 응답이 ZIP이 아님. status=%s, content=%s',
        response.status_code,
        response.content[:200],
    )
    raise ValueError('DART corpCode.zip 응답 오류')
```

수정 직후 실행해보니 DART API가 `status=020`(사용한도 초과) XML을 반환했고, 검증 코드가 이를 즉시 잡아내 `status=020, content=...` 형태의 명확한 에러 메시지를 출력했다. 기존 코드였다면 `BadZipFile`로 원인 파악에 시간이 걸렸을 것이다.

### 2-2. fetch_corp_list() 상장사 필터링

DART `corpCode.xml` 기업 수는 **약 117,496개**다. 이 중 비상장·비공개 기업은 `fnlttSinglAcntAll` API(재무제표)가 데이터를 반환하지 않는다. 전수 호출 시 일일 API 쿼터를 즉시 소진하고, 결과의 대부분이 재무 null이다.

이전 세션(#2-2)에서 이미 이 취약성을 발견했고, 오늘 수정했다.

```python
listed_corps = [c for c in corps if c.get('stock_code')]
logger.info('전체 기업: %d개 → 상장사 필터링 후: %d개', len(corps), len(listed_corps))
return listed_corps
```

`stock_code`가 있는 기업(KOSPI + KOSDAQ 상장사)만 반환한다. 약 2,500~3,000개로 줄어들어 일일 쿼터 소진 없이 수집이 가능해진다.

### 2-3. feature/llm-processor 병합 분석 및 Squash Merge

팀원(박지윤)의 `feature/llm-processor` 브랜치를 체크아웃 없이 분석했다.

```bash
git show origin/feature/llm-processor:src/processor.py
```

이전 버전 대비 주요 변경점:

| 항목 | 이전 (#2-2 병합본) | 이번 (#3) |
|---|---|---|
| 파이프라인 구조 | 단순 LangChain LCEL 체인 | LangGraph 4노드 그래프 |
| 검증 로직 | 없음 | validate_node (날짜 논리 오류, 필수 필드 검사) |
| 재시도 | 없음 | 최대 3회 (validate 실패 시 parse_node로 복귀) |
| 피드백 생성 | 없음 | feedback_node + FEEDBACK_TEMPLATES 분기 |
| 수치 피처 추출 | 없음 | extract_numerical_features() — LightGBM 연동 준비 |
| 신규 파일 | 없음 | src/templates.py (피드백 문구 모음) |
| Gemini 모델 | 암묵적 | `gemini-2.5-flash` 명시 |

스키마 필드(`industry_limit`, `debt_ratio_limit`, `interest_rate`, `requirements`) 전부 유지 확인.

병합은 `--squash`로 진행했다. `requirements.txt`에 이미 이전 merge에서 발생한 충돌 마커(`<<<<<<< HEAD`)가 포함되어 있었다. develop 버전(pdfplumber + google-genai 포함)이 이미 완전한 상태이므로 충돌 마커만 제거했다.

### 2-4. Gemini API 실전 테스트

S3 `raw/announcements/2026-05-01/`에서 2021년 HWP 파일(108,032 bytes)을 내려받아 LangGraph 파이프라인을 돌렸다.

```
Node 1 — 텍스트 추출 (HWP olefile 파싱)
Node 2 — LLM 파싱 (Gemini API 호출: ~2분 30초)
Node 3 — 데이터 정합성 검증 → is_valid: True
Node 4 — 피드백 템플릿 생성
```

결과: **17개 세부사업** (청년전용창업자금, 창업기반지원자금, 미래기술육성자금 등) 정상 추출.

```json
{
  "sub_title": "미래기술육성자금",
  "program_category": "기술",
  "industry_major_category": ["제조업(C)", "정보통신업(J)", "전문, 과학 및 기술 서비스업(M)"],
  "max_support": 10000000000,
  "interest_rate": "(업력 3년 이상 7년 미만) 정책자금 기준금리(변동) - 0.2%p, (업력 7년 이상 10년 미만) 정책자금 기준금리(변동)",
  "requirements": ["혁신성장분야 영위", "업력 3년 이상 10년 미만인 중소기업"],
  "debt_ratio_limit": null,
  "apply_start": "2020-12-24",
  "apply_end": "9999-12-31"
}
```

S3 업로드: `embeddings/requirements_db/2026-05-02/test_2021.json` 완료.

---

## 3. 발견된 문제 및 해결

### 🔴 Issue 1 — DART API 사용한도 초과 감지 실패 (기존)

**문제**: DART API가 `status=020`(사용한도 초과) XML을 반환해도 기존 코드는 `BadZipFile`로만 실패했다. 어느 API를, 왜 실패했는지 로그에서 즉시 확인 불가.

**해결**: `b'PK'` 체크 + `logger.error`로 상태코드·응답본문 출력. 수정 직후 테스트에서 `status=020` 즉시 감지 성공.

---

### 🔴 Issue 2 — requirements.txt 충돌 마커 잔존

**문제**: `feature/llm-processor`의 `requirements.txt`에 이전 merge 시 해결하지 않은 `<<<<<<< HEAD` 마커가 남아 있었다. Squash Merge 시 그대로 스테이징됐다.

**해결**: develop 버전(pdfplumber + google-genai 포함)이 이미 완전한 상태임을 확인하고, 충돌 마커만 제거 후 스테이징.

---

### 🔴 Issue 3 — 터미널 CP949 인코딩 (한글 JSON 출력 불가)

**문제**: Windows 터미널 기본 인코딩(CP949)에서 `json.dumps(..., ensure_ascii=False)` 결과를 stdout에 직접 출력하면 `UnicodeEncodeError`가 발생했다. 파싱은 성공했지만 결과 확인이 불가했다.

**해결**: 결과를 `analysis_results.json`에 UTF-8로 저장한 뒤 Read 툴로 읽는 방식으로 우회. 터미널 인코딩 문제와 파싱 성공 여부를 분리해서 검증했다.

---

## 4. 파이프라인 현황

| 컴포넌트 | 상태 | 비고 |
|---|---|---|
| OpenDART extractor | ✅ | ZIP 검증 + 재시도 5초 + 상장사 필터 적용 |
| bizinfo extractor | ✅ | jsonArray 파서 수정 완료 (이전 세션) |
| KIPRIS extractor | ✅ | mock 모드 (키 승인 대기) |
| 크롤러 (crawler.py) | ✅ | slno 기반 파일명 저장 (이전 세션) |
| LLM 파서 (processor.py) | ✅ | LangGraph 4노드, v2 병합 완료 |
| merge.py | ✅ | slno 기반 JOIN (이전 세션) |
| company_features.parquet | ⚠️ | 상장사 필터 적용 후 재수집 필요 |
| labels.parquet | ❌ | 수혜이력 비공개 — 미생성 |

---

## 5. develop 커밋 이력

```
a780138  chore: add GEMINI_API_KEY to .env.example
68c4e2f  feat: update LLM parser with latest changes (#6)
76ebccb  fix: filter DART corp list to listed companies only
40262db  docs: update README
498d6ea  fix: replace filename-based JOIN key with slno (#5)
37a5de0  fix: bizinfo API 응답 파싱 키 수정
e018dd8  feat: add program features merge pipeline (#4)
fc7d6ce  feat: add LangChain-based LLM parser with S3 upload (#3)
59638c7  feat: add data crawler and update pipeline components (#2)
3793b04  feat: ETL 파이프라인 구현 (#1)
```

---

## 6. 남은 작업

### 단기 (다음 세션)

- [ ] DART API 일일 쿼터 리셋 후 상장사 필터 적용된 `fetch_corp_list()` 실전 실행
- [ ] LangGraph MAS 에이전트 구현
  - `agents/orchestrator/` — PolicyFundState, 조건부 엣지
  - `agents/scoring/` — 룰 기반 스코어링 Tool (P = α·F + β·T + γ·G)
  - `agents/shap/` — 가중치 기여도 계산 Tool
- [ ] FastAPI `/match`, `/feedback/{program_id}` 엔드포인트 구현

### 중기

- [ ] company_features.parquet 재수집 (상장사 필터 적용 후)
- [ ] End-to-end Airflow DAG 전체 실행
- [ ] MLflow 파라미터 버전 관리 환경 구축

### 미결

- [ ] 수혜이력 이의신청 결과에 따라 스코어링 방식 재검토
- [ ] KIPRIS API 키 승인 후 mock → 실전 전환 테스트

---

## 7. 배운 점 / 느낀 점

**방어 코드는 실패했을 때 얼마나 빨리 원인을 알 수 있느냐가 핵심이다.** ZIP 검증 코드를 추가하기 전까지, DART API가 오류 XML을 반환해도 코드는 아무런 단서 없이 `BadZipFile`만 던졌다. 추가한 코드는 단 5줄이지만, 실패 즉시 `status=020, content=...` 형태의 정확한 원인이 로그에 찍혔다. 방어 코드의 가치는 정상 케이스에서는 보이지 않는다. 실패 케이스에서 디버깅 시간을 얼마나 줄이느냐로 측정해야 한다.

**117,496개 전수 호출은 설계가 아니라 실수다.** DART API 일일 쿼터가 있다는 건 처음부터 알고 있었다. 그런데 `fetch_corp_list()`가 전체 기업을 그대로 반환하고 있었다. 이전 세션(#2-2)에서 재무 null 원인으로 이 문제를 이미 파악했음에도 수정이 늦어졌다. 설계 문서에 "이후 개선"으로 남겨두면 실전에서 API 쿼터를 모두 태운 뒤에야 떠오른다. 발견 즉시 수정하는 게 맞다.

**LangGraph로 전환한 팀원의 설계가 인상적이었다.** 단순 체인에서 extract → parse → validate → feedback 4노드 그래프로 바뀌었다. 특히 `validate_node`에서 날짜 논리 오류를 감지하면 `parse_node`로 되돌아가는 순환 엣지가 포인트다. LLM 출력은 언제나 잘못된 날짜를 낼 수 있고, 규칙 기반 검증 + 자동 재시도 구조가 그 불확실성을 흡수한다. MAS 에이전트 설계에서도 동일한 패턴을 그대로 가져올 것이다.

**터미널 인코딩 문제는 Windows 환경에서 반복된다.** CP949 기본 인코딩은 한글 `json.dumps`를 stdout에 직접 출력할 때마다 `UnicodeEncodeError`를 낸다. 이번에는 파일로 우회했지만, 앞으로 한글이 포함된 결과를 확인할 때는 처음부터 UTF-8 파일 저장 → Read 방식을 쓰는 것이 맞다.

---

*전체 코드: [github.com/Dongjin-1203/policy-fund-navigator](https://github.com/Dongjin-1203/policy-fund-navigator)*
