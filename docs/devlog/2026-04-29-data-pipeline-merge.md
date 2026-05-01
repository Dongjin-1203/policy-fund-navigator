# [에이전틱한 개발자 되기 #2-1] 중진공 AI 매칭 — 전체 데이터 파이프라인 병합기 (상)

> 시리즈: 에이전틱한 개발자 되기  
> 태그: `LangChain` `Airflow` `ETL` `AWS S3` `git`
> 날짜: 2026-04-29

> 📌 이 글은 2편으로 구성된 시리즈의 1편입니다.  
> **#2-1**: 데이터 파이프라인 병합 — 브랜치 병합, ProgramFeatureMerger 구현  
> **#2-2**: 파이프라인 실전 검증 — end-to-end 테스트, 버그 수정, JOIN 키 재설계

---

## 1. 오늘의 목표

| 목표 | 결과 |
|---|---|
| 프로젝트 전체 현황 파악 (PRD, 브랜치, 팀원 코드) | ✅ |
| feature/llm-processor 출력 스키마 보완 | ✅ `debt_ratio_limit`, `interest_rate` 추가 |
| feature/data-crawler → develop Squash Merge (#2) | ✅ |
| feature/llm-processor → develop Squash Merge (#3) | ✅ 충돌 해결 포함 |
| ProgramFeatureMerger 구현 (metadata + parser JOIN) | ✅ |
| feature/merge-pipeline → develop Squash Merge (#4) | ✅ |
| 스태시 복원 및 PRD/devlog develop 반영 | ✅ |

---

## 2. 프로젝트 현황 — 오늘 파악한 핵심 변경사항

### 수혜이력 비공개 처분에 따른 설계 전환

정보공개포털에 청구한 중진공 정책자금 수혜이력 데이터가 **비공개 처분**됐다.  
(「공공기관의 정보공개에 관한 법률」 제9조제1항 제7호 — 경영·영업상 비밀)

이로 인해 아키텍처 전반이 변경됐다.

| 항목 | 변경 전 | 변경 후 |
|---|---|---|
| 스코어링 | LightGBM LambdaRank | **룰 기반** P = α·F + β·T + γ·G |
| XAI | SHAP TreeExplainer | 가중치 기여도 직접 계산 |
| 학습 데이터 | labels.parquet (0/1) | **미생성** (실데이터 확보 시 추가) |
| 실험 관리 | MLflow (모델 레지스트리) | MLflow (파라미터 버전 관리) |

룰 기반 수식은 도메인 전문가 협의 초기값(`α=0.4, β=0.3, γ=0.3`)으로 시작해,  
실데이터 확보 시 LightGBM → GNN 순으로 고도화할 예정이다.  
이의신청을 통한 익명 통계 데이터 재청구도 병행 진행한다.

---

## 3. 주요 작업 내용

### 3-1. 현황 파악 (작업 재개 온보딩)

```
로컬 브랜치: develop, feature/data-crawler, main
원격 미병합:  feature/data-crawler, feature/llm-processor
현재 워킹트리: feature/data-crawler
스태시: WIP: PRD and devlog updates
```

팀원(박지윤) `feature/llm-processor` 코드를 체크아웃 없이 분석했다.
`git show origin/feature/llm-processor:src/processor.py`로 LangChain LCEL 구현을 확인하고 아래 2가지 문제를 발견했다.

1. `program_features.parquet`에 필요한 `debt_ratio_limit`, `interest_rate` 필드 미포함
2. 파싱 결과(`analysis_results.json` 230KB)가 저장소에 커밋된 상태

### 3-2. feature/llm-processor 원격 수정 (체크아웃 없이)

`git worktree add`로 별도 디렉토리에 브랜치를 올려 수정 후 push했다.

```bash
git worktree add ../policy-fund-navigator-llm-temp origin/feature/llm-processor
# → 수정 → commit → push origin HEAD:feature/llm-processor
git worktree remove ../policy-fund-navigator-llm-temp
```

수정 내용:
- 시스템 프롬프트 규칙 14번(`debt_ratio_limit`), 15번(`interest_rate`) 추가
- JSON 출력 스키마에 두 필드 반영
- `upload_to_s3()` 함수 신설 → `embeddings/requirements_db/YYYY-MM-DD/{stem}.json`
- `langchain-core`, `langchain-google-genai`, `olefile` requirements.txt 추가
- `analysis_results.json` .gitignore 처리 + `git rm --cached`

### 3-3. Squash Merge 3회 (data-crawler #2 → llm-processor #3 → merge-pipeline #4)

```
develop
├── #2 feat: add data crawler and update pipeline components
│       crawler.py, kipris_extractor.py(실전 전환),
│       processor.py(PRD 정렬), PRD.md, CLAUDE.md
├── #3 feat: add LangChain-based LLM parser with S3 upload
│       processor.py(LangChain LCEL), requirements.txt, .gitignore
└── #4 feat: add program features merge pipeline
        transformers/merge.py(ProgramFeatureMerger), etl_pipeline.py
```

### 3-4. ProgramFeatureMerger 구현 (`transformers/merge.py`)

크롤러가 수집한 metadata.json과 LLM 파서가 생성한 requirements_db를  
`filename` 기준으로 LEFT JOIN해 `program_features.parquet`을 생성한다.

```
raw/announcements/*/metadata.json   (title, filename, slno, collected_at)
        + LEFT JOIN (filename stem)
embeddings/requirements_db/*/*.json (programs[], source_file)
        ↓
processed/program_features.parquet
```

다중 프로그램 공고(한 파일에 세부 트랙 N개) 처리:
- 단일: `program_id = slno`
- 복수: `program_id = slno_1`, `slno_2`, ...

JOIN 실패 시 requirements 관련 필드 전체 None + `logging.warning`.

---

## 4. 발견된 문제 및 해결 방법

### 🔴 Issue 1 — program_id JOIN 키 누락

**문제**: Task 5 스모크 테스트에서 `program_id` 필드가 processor.py 출력에 없음을 발견.  
`processor.py`는 `source_file`만 기록하고 `program_id`는 없었다.

**해결**: `ProgramFeatureMerger`에서 JOIN 시 `metadata.slno`를 `program_id`로 사용.  
processor.py의 `source_file`(파일명)과 metadata의 `filename`을 stem 기준 매칭.  
향후 `transformers/merge.py`의 JOIN 로직에서 `slno` ↔ `bizinfo.pblancId` 연결 필요.

### 🔴 Issue 2 — analysis_results.json 저장소 포함

**문제**: 팀원 브랜치에 230KB JSON 결과 파일이 커밋된 채로 있었다.

**해결**:
```bash
git rm --cached analysis_results.json
echo "analysis_results.json" >> .gitignore
```

### 🔴 Issue 3 — processor.py `if __name__` 블록 외부 코드 버그

**문제**: 원본 코드에서 `with open("analysis_results.json", "w") as f:` 블록과  
마지막 `print()` 가 `if __name__ == '__main__':` **바깥**에 위치해 있었다.  
→ 모듈을 import할 때마다 파일 쓰기가 실행되는 버그.

**해결**: 전체를 `if __name__` 블록 안으로 이동.

### 🔴 Issue 4 — Squash Merge 3방향 충돌

`feature/data-crawler`와 `feature/llm-processor` 모두 `dff35f9` 커밋(WIP)을 공유하며  
`develop`에 순차 병합하다 보니 `.gitignore`, `requirements.txt`, `src/processor.py` 3파일에서 충돌 발생.

**해결 방침**:

| 파일 | 해결 방식 |
|---|---|
| `src/processor.py` | llm-processor 버전 채택 (LangChain LCEL + S3) |
| `requirements.txt` | 양쪽 패키지 모두 보존 |
| `.gitignore` | 양쪽 항목 모두 보존 |

---

## 5. 팀원 협업 사항

팀원(박지윤) 담당 `feature/llm-processor` 코드를 체크아웃 없이 리뷰하고  
아래 항목을 직접 수정 후 push했다.

| 항목 | 상태 |
|---|---|
| `debt_ratio_limit` 추출 규칙 추가 | ✅ 완료 |
| `interest_rate` 추출 규칙 추가 | ✅ 완료 |
| S3 upload 코드 추가 | ✅ 완료 |
| analysis_results.json .gitignore 처리 | ✅ 완료 |
| langchain 의존성 requirements.txt 추가 | ✅ 완료 |

팀원과 공유 경로: `s3://[BUCKET]/embeddings/requirements_db/YYYY-MM-DD/{stem}.json`  
`ProgramFeatureMerger`는 이 경로를 자동 탐색하여 JOIN한다.

---

## 6. 남은 작업

### 단기 (다음 세션)

- [ ] LangGraph MAS 에이전트 구현 시작
  - `agents/orchestrator/` — State 스키마, 흐름 제어
  - `agents/scoring/` — 룰 기반 스코어링 Tool 구현
  - `agents/shap/` — 가중치 기여도 계산 Tool 구현
- [ ] FastAPI `/match`, `/feedback/{program_id}` 엔드포인트 구현

### 중기

- [ ] End-to-end 실전 테스트 (실제 S3 + Gemini API + Airflow DAG)
- [ ] MLflow 파라미터 버전 관리 환경 구축
- [ ] 프론트엔드 UI 구현

### 미결

- [ ] 수혜이력 이의신청 결과에 따라 스코어링 방식 재검토
- [ ] `merge.py`: bizinfo `pblancId` ↔ crawler `slno` 연결 키 확인 필요
- [ ] `transformers/merge.py`의 기존 `build_program_features(bizinfo)` 와  
      신규 `ProgramFeatureMerger(crawler+parser)` 통합 방안 설계

---

## 7. 배운 점 / 느낀 점

**git worktree**는 체크아웃 없이 다른 브랜치 파일을 수정·커밋할 때 유용하다.  
팀원 브랜치를 `git show`로 읽고, worktree로 수정 후 push하는 플로우를 처음 써봤다.  
feature 브랜치를 직접 넘겨받아 수정하는 협업 방식에서 필수 도구가 될 것 같다.

**LangChain LCEL** 체인(`prompt | llm | parser`)은 간결하지만, 출력 스키마 관리가  
프롬프트 문자열에만 의존하다 보니 필드 누락이 쉽게 생긴다.  
`JsonOutputParser` + 명시적 Pydantic 스키마로 보강하면 더 안전할 것이다.

**Squash Merge 전략**의 한계도 경험했다. 두 브랜치가 공통 조상 커밋을 공유하는 상태에서  
순차 Squash Merge하면 3방향 충돌이 불가피하다. 브랜치 분기 전 기반 커밋을 develop으로  
명확히 정리한 후 feature를 나누는 것이 맞다.

---

다음 편에서는 병합된 파이프라인을 실제 데이터로 돌려보고, 발견된 버그들을 수정하는 과정을 다룬다.  
→ [#2-2 파이프라인 실전 검증기 (하)](#)

---

*전체 코드: [github.com/Dongjin-1203/policy-fund-navigator](https://github.com/Dongjin-1203/policy-fund-navigator)*
