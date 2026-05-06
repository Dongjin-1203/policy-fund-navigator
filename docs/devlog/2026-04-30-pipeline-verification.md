# [에이전틱한 개발자 되기 #2-2] 중진공 AI 매칭 — 파이프라인 실전 검증기 (하)

> 시리즈: 에이전틱한 개발자 되기  
> 태그: `LangChain` `Gemini` `ETL` `AWS S3` `git` `Python`
> 날짜: 2026-04-30

> 📌 이 글은 2편으로 구성된 시리즈의 2편입니다.  
> **#2-1**: 데이터 파이프라인 병합 — 브랜치 병합, ProgramFeatureMerger 구현  
> **#2-2**: 파이프라인 실전 검증 — end-to-end 테스트, 버그 수정, JOIN 키 재설계  
> → [#2-1 전체 데이터 파이프라인 병합기 (상)](#) 에서 이어지는 내용입니다.

---

## 1. 오늘의 목표

전 편(#2-1)에서 세 개의 feature 브랜치를 병합하고 ProgramFeatureMerger를 구현했다. 이번 편에서는 실제 데이터를 파이프라인에 흘려보내며 설계의 전제를 검증하고, 발견된 버그들을 수정한다.

| 목표 | 결과 |
|---|---|
| 환경 인계 및 작업 재개 (노트북 → 데스크탑) | ✅ |
| feature/llm-parser 코드 분석 및 스키마 불일치 수정 | ✅ `debt_ratio_limit`, `interest_rate` 추가 |
| feature/llm-parser → develop Squash Merge | ✅ 3방향 충돌 해결 포함 |
| processor.py GEMINI_API_KEY 설정 및 실전 실행 | ✅ HWP 1건 → 18개 세부 사업 파싱 |
| bizinfo API 파서 버그 수정 (0건 → 정상 수집) | ✅ |
| ProgramFeatureMerger 실전 JOIN 검증 | ✅ JOIN 성공 0건 → 18건 |
| JOIN 키 취약성 수정 (파일명 → slno 기반 전환) | ✅ |

---

## 2. 주요 작업 내용

### 2-1. 환경 인계 및 현황 파악

작업을 노트북에서 데스크탑으로 이전했다. pull 완료 후 PRD를 읽고 팀원(박지윤)의 `feature/llm-parser` 브랜치를 체크아웃 없이 분석했다.

`git show origin/feature/llm-parser:src/processor.py`로 확인한 결과 두 가지 불일치를 발견했다.

1. `program_features.parquet` 스키마에 필요한 `debt_ratio_limit`, `interest_rate` 필드가 출력 JSON에 없음
2. S3 연동 없음 — 결과를 로컬 `analysis_results.json`에만 저장

추가로 코드 레벨 버그도 발견했다.

3. `with open("analysis_results.json", "w")` 블록이 `if __name__ == '__main__':` **바깥**에 위치 → 모듈 import 시마다 파일 쓰기가 실행되는 버그

### 2-2. feature/llm-parser 수정 및 병합

`git worktree`를 활용해 별도 디렉토리에서 수정 후 push했다.

수정 내용:

| 항목 | 처리 |
|---|---|
| `debt_ratio_limit` 추출 규칙 추가 | 시스템 프롬프트 규칙 14번 |
| `interest_rate` 추출 규칙 추가 | 시스템 프롬프트 규칙 15번 |
| `upload_to_s3()` 신설 | `embeddings/requirements_db/YYYY-MM-DD/{slno}.json` |
| `analysis_results.json` .gitignore | `git rm --cached` + `.gitignore` 추가 |
| langchain 의존성 추가 | `requirements.txt`에 langchain-core, langchain-google-genai, olefile |
| `if __name__` 블록 버그 수정 | with open 블록 블록 안으로 이동 |

이후 `feature/data-crawler` → `feature/llm-parser` 순으로 develop에 Squash Merge했다. 두 브랜치가 공통 조상(`dff35f9`)을 공유해 `.gitignore`, `requirements.txt`, `src/processor.py` 3파일에서 충돌이 발생했다.

충돌 해결 방침:

| 파일 | 채택 버전 |
|---|---|
| `src/processor.py` | llm-parser (LangChain LCEL + S3) |
| `requirements.txt` | 양쪽 패키지 모두 보존 |
| `.gitignore` | 양쪽 항목 모두 보존 |

### 2-3. ProgramFeatureMerger — 실전 JOIN 검증

`#4` 커밋에서 구현한 `ProgramFeatureMerger`를 실전 환경에서 처음으로 돌렸다.

결과는 **JOIN 성공 0건 / 실패 1건**이었다.

원인을 추적해보니 파일명 불일치였다. crawler가 S3에 저장한 파일명은 `1. 2026년 중소기업 정책자금 융자계획 변경공고(제2026-287호).hwp`인데, 테스트 시 파일을 로컬에 `2026_policy_fund.hwp`로 다운로드해서 processor가 `source_file = "2026_policy_fund"`로 저장했기 때문이다. 두 stem이 달라 JOIN에 실패했다.

파일명을 원본 그대로 유지해 재실행하니 JOIN은 성공했지만, 이 경험이 다음 작업인 JOIN 키 전면 재설계로 이어졌다.

### 2-4. processor.py 실전 실행 — Gemini 2.5-flash 파싱 품질 확인

`.env`에 `GEMINI_API_KEY`를 추가하고 실전 실행했다. 대상은 중진공 홈페이지에서 크롤링한 **2026년도 중소기업 정책자금 융자계획 변경공고(제2026-287호)** HWP 1건이다.

```
텍스트 추출: 153,893자 (HWP olefile 파싱)
Gemini API 호출: 약 4분
파싱 결과: 18개 세부 사업
```

파싱 품질이 예상보다 훨씬 좋았다. 한 공고문에서 세부 사업 18개를 모두 분리해냈고, 각 사업의 `industry_limit`(37개 항목), `requirements`, `interest_rate`, `apply_start/end`까지 정확하게 추출했다.

샘플 출력:

```json
{
  "sub_title": "창업기반지원자금(일반)",
  "program_category": "창업",
  "max_support": 6000000000,
  "interest_rate": "정책자금 기준금리(변동) - 0.3%p",
  "apply_start": "2026-01-05",
  "apply_end": "9999-12-31",
  "industry_limit": ["불건전 영상게임기 제조업(KSIC 33402)", "주점업", ...],
  "requirements": ["업력 7년 미만 (예비창업자 포함)", ...]
}
```

### 2-5. bizinfo API 파서 버그 수정

S3의 `raw/bizinfo/2026-04-18/programs.json`을 확인하니 `programs: []`였다. 실제 API 응답을 찍어보니 최상위 키가 `jsonArray`였는데, 코드는 `result` → `response.body.items.item` 순으로 탐색하고 있었다.

```python
# 수정 전
raw_items = data.get('result') or data.get('response', {}).get('body', ...) or []

# 수정 후
raw_items = data.get('jsonArray') or data.get('result') or data.get('response', ...) or []
```

한 줄 수정으로 bizinfo 공고 수집이 정상화됐다.

### 2-6. JOIN 키 취약성 수정 — 파일명 기반 → slno 기반 전환

파일명으로 JOIN하는 설계는 파일명이 한 글자라도 달라지면 즉시 실패한다. 이번 세션에서 그 취약성을 직접 경험했다.

설계를 다음과 같이 전면 변경했다.

**변경 전 흐름:**
```
crawler → S3: raw/announcements/{date}/{원본파일명.hwp}
processor → S3: embeddings/requirements_db/{date}/{원본파일명_stem}.json
merge → JOIN: os.path.splitext(filename)[0] == os.path.splitext(source_file)[0]
```

**변경 후 흐름:**
```
crawler → S3: raw/announcements/{date}/{slno}_{원본파일명.hwp}
              metadata.filename = "{slno}_{원본파일명.hwp}"
processor → S3: embeddings/requirements_db/{date}/{slno}.json
                result['source_file'] = slno
merge → JOIN: metadata['slno'] == req_map 키(S3 파일명 stem = slno)
```

수정 지점:

| 파일 | 변경 내용 |
|---|---|
| `crawler.py` | `saved_filename = f'{slno}_{filename}'`, S3 키 및 metadata.filename 교체 |
| `processor.py` | basename에서 `{digits}_` 접두사 파싱 후 `source_file = slno` 저장 |
| `merge.py` | `_load_all_requirements`: S3 키 파일명 stem으로 keying, `_build_rows`: `req_map.get(slno)` 직접 사용, stem 비교 로직 제거 |

mock 검증 → 실전 검증 순으로 통과했다.

---

## 3. 발견된 문제 및 해결

### 🔴 Issue 1 — processor.py `if __name__` 블록 외부 버그

**문제**: `with open("analysis_results.json", "w") as f:` 블록이 `if __name__ == '__main__':` 바깥에 있어, 모듈 import 시마다 파일 쓰기가 실행됐다.

**해결**: 전체 저장·업로드 블록을 `if __name__` 안으로 이동.

---

### 🔴 Issue 2 — GEMINI_API_KEY 미설정

**문제**: `.env` 파일에 `GEMINI_API_KEY`가 없어 processor.py 실전 실행 불가.

**해결**: `.env`에 키 추가 후 재실행.

---

### 🔴 Issue 3 — bizinfo API 응답 키 불일치

**문제**: 실제 API 응답 최상위 키가 `jsonArray`인데 코드는 `result`를 탐색 → 0건 수집.

**해결**: `fetch_programs()` JSON 파싱 분기에 `jsonArray`를 1순위로 추가. 커밋 `37a5de0`.

---

### 🔴 Issue 4 — 파일명 기반 JOIN 취약성

**문제**: crawler가 S3에 저장한 파일명과 processor가 `source_file`로 기록한 파일명이 달라 JOIN 실패.

**해결**: slno 기반 전면 전환. 커밋 `498d6ea`.

---

### 🟡 Issue 5 — company_features.parquet 재무 필드 전체 null

**문제**: DART `corpCode.xml`은 비상장·비공개 기업 포함. `fnlttSinglAcntAll` API는 외감법인·상장사만 재무데이터를 반환해 4,474건 중 재무 필드 non-null = 0건.

**현재 방침**: 룰 기반 스코어링(P = α·F + β·T + γ·G)에서 F 점수에 기본값을 부여해 우회. 근본 해결은 `fetch_corp_list()`를 `stock_code` 보유 기업 필터로 대체하거나, DART `search_corp` API로 중소기업을 직접 탐색하는 방향으로 이후 개선.

---

## 4. 최종 파이프라인 현황

| 컴포넌트 | 상태 | 비고 |
|---|---|---|
| OpenDART extractor | ⚠️ | 재무 null (비상장사 문제) |
| bizinfo extractor | ✅ | `jsonArray` 파서 수정 완료 |
| KIPRIS extractor | ✅ | mock 모드 (키 승인 대기) |
| 크롤러 (crawler.py) | ✅ | slno 기반 파일명 저장 |
| LLM 파서 (processor.py) | ✅ | HWP 1건 → 18개 세부사업 파싱 성공 |
| merge.py (ProgramFeatureMerger) | ✅ | slno 기반 JOIN, 18행 전 필드 정상 |
| program_features.parquet | ✅ | 18행, requirements·interest_rate 등 정상 |
| company_features.parquet | ⚠️ | 4,474행, 재무 필드 전체 null |
| labels.parquet | ❌ | 수혜이력 비공개 처분 — 미생성 |

---

## 5. develop 커밋 이력

```
498d6ea  fix: replace filename-based JOIN key with slno (#5)
37a5de0  fix: bizinfo API 응답 파싱 키 수정 (result → jsonArray)
a3ecc38  docs: restore PRD and ETL pipeline devlog
e018dd8  feat: add program features merge pipeline (#4)
fc7d6ce  feat: add LangChain-based LLM parser with S3 upload (#3)
59638c7  feat: add data crawler and update pipeline components (#2)
43411d4  docs: CLAUDE.md 및 브랜치 전략 문서 추가
b021fee  docs: ETL 파이프라인 개발일지 작성 (2026-04-18)
3793b04  feat: ETL 파이프라인 구현 (DART/KIPRIS/bizinfo extractor + Airflow DAG) (#1)
6a58acd  Initial commit
```

---

## 6. 남은 작업

### 단기 (다음 세션)

- [ ] LangGraph MAS 에이전트 구현
  - `agents/orchestrator/` — PolicyFundState 스키마, 조건부 엣지
  - `agents/scoring/` — 룰 기반 스코어링 Tool (P = α·F + β·T + γ·G)
  - `agents/shap/` — 가중치 기여도 계산 Tool
- [ ] FastAPI `/match`, `/feedback/{program_id}` 엔드포인트 구현

### 중기

- [ ] DART 재무 null 처리 — `fetch_corp_list()` 필터링 또는 `search_corp` API 대체
- [ ] End-to-end 실전 테스트 (Airflow DAG 전체 실행)
- [ ] MLflow 파라미터 버전 관리 환경 구축
- [ ] 프론트엔드 UI 구현

### 미결

- [ ] 수혜이력 이의신청 결과에 따라 스코어링 방식 재검토
- [ ] KIPRIS API 키 승인 후 mock → 실전 전환 테스트
- [ ] bizinfo 전체 분야(01~09) 수집 및 `build_program_features()` 연동 검토

---

## 7. 배운 점 / 느낀 점

**JOIN 키 설계는 파이프라인 설계의 핵심이다.** 파일명으로 JOIN하는 게 간단해 보이지만, 파일명은 다운로드 경로, 로컬 저장 이름, S3 키 이름 등 여러 레이어를 거치면서 얼마든지 달라질 수 있다. 이번에 정확히 그 취약점을 경험했다. 테스트 편의상 파일명을 바꿔 저장하는 것만으로도 파이프라인 전체가 조용히 실패했다. 비즈니스 키(slno)를 공유 JOIN 키로 명시하는 것이 맞다.

**end-to-end 테스트는 설계가 완성된 뒤가 아니라, 설계의 전제를 검증하기 위해 먼저 해야 한다.** ProgramFeatureMerger를 구현하고 mock 검증까지 통과했지만, 실전 환경에서는 JOIN 키가 달라 바로 실패했다. API 응답 키가 예상과 다른 것도 실전 실행 전까지는 알 수 없었다. 설계가 아무리 완벽해도 실제 데이터를 흘려보내기 전까지는 가설에 불과하다.

**Gemini 2.5-flash의 파싱 품질이 놀라웠다.** 153,893자짜리 HWP 공고문에서 세부 사업 18개를 정확히 분리하고, 각 사업의 제외 업종을 37개 항목까지 추출했다. `[Table Start]`, `[Row/Cell Boundary]` 같은 HWP 구조 지시어가 섞인 비정형 텍스트였는데도 문맥을 잘 이해했다. 프롬프트 엔지니어링이 충분히 잘 된 상태라면 LLM이 구조화 파싱에서 규칙 기반을 이미 앞서고 있다고 느꼈다.

**에이전틱 개발의 실감.** 오늘 작업의 많은 부분이 `git show`로 브랜치를 체크아웃 없이 읽고, 실제 API를 찍어보고, 버그를 발견하면 즉시 수정 → 검증하는 루프였다. 사람이 판단하고 AI가 실행하는 방식이 아니라, 코드를 읽고 판단하고 수정하는 과정 자체를 대화 흐름 안에서 하게 됐다. 이 방식은 디버깅 속도가 빠르다. 다음 세션의 MAS 에이전트 구현에서도 이 리듬을 그대로 유지할 것이다.

---

이 시리즈(#2-1, #2-2)를 통해 데이터 파이프라인이 완성됐다. 다음 시리즈에서는 LangGraph 기반 MAS 에이전트 구현을 다룬다.

---

*전체 코드: [github.com/Dongjin-1203/policy-fund-navigator](https://github.com/Dongjin-1203/policy-fund-navigator)*
