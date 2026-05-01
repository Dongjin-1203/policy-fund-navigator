# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

중소기업진흥공단(중진공) 정책자금 AI 매칭 서비스 — 기업의 정형(재무/신용) 및 비정형(특허/공고문) 데이터를 통합 분석해 적합 정책자금을 추천하고 SHAP 기반 심사 가이드를 제공한다.

## 팀 구성
- 지동진(이 저장소): ETL 파이프라인, 크롤러, 자격요건 DB 구축, MAS 구현, FastAPI 서버
- 박지윤(feature/llm-processor): LLM 파서 (공고문 → 자격요건 구조화) → `s3://[BUCKET]/embeddings/` 경로 소유

---

## Commands

```bash
# 환경 시작
docker compose up -d

# Airflow DAG 수동 트리거
airflow dags trigger etl_pipeline

# 각 extractor 단독 실행 (독립 실행 가능 구조)
python dags/extractors/dart_extractor.py
python dags/extractors/kipris_extractor.py
python dags/extractors/bizinfo_extractor.py
python dags/extractors/welfare_loader.py
python dags/extractors/crawler.py

# 모델 학습 / 추론
python models/train.py
python models/predict.py

# API 서버 실행
uvicorn api.main:app --reload

# 테스트 전체 / 단일 파일
pytest tests/
pytest tests/test_dart_extractor.py
```

---

## Architecture

데이터 흐름: 외부 API → S3 raw → S3 processed → ML 모델 → FastAPI

```
[OpenDART API]──┐
[KIPRIS API]────┼──(Airflow DAG: 병렬 extract)──► S3 raw/
[중소벤처24 API]─┘                                  │
[수혜이력 CSV]──────────────────────────────────────┘
                                                    │
                                          transformers/merge.py
                                                    │
                                                    ▼
                                           S3 processed/
                                     (company_features.parquet
                                      program_features.parquet
                                      labels.parquet)
                                                    │
                              ┌─────────────────────┤
                              │   S3 embeddings/     │  ← 팀원 소유
                              │ (requirements_db)    │
                              └──────────┬───────────┘
                                         │
                                         ▼
                               models/ (3단계 필터링)
                         ① Hard Filter  — 자격요건 DB 기반 제외
                         ② Soft Filter  — 임베딩 유사도
                         ③ LightGBM LambdaRank 스코어링
                                + SHAP XAI 피드백 생성
                                         │
                                         ▼
                                  api/main.py (FastAPI)
```

---

## 기술 스택

| 레이어 | 기술 |
|---|---|
| ETL 오케스트레이션 | Apache Airflow 2.9 (Docker Compose) |
| 스토리지 | AWS S3 |
| 데이터 처리 | pandas, pyarrow |
| ML 모델 | LightGBM (LambdaRank) |
| 실험 관리 | MLflow (Experiment Tracking + Model Registry) |
| XAI | SHAP |
| MAS 프레임워크 | LangGraph |
| LLM API | Gemini API |
| API 서버 | FastAPI |
| 크롤링 | BeautifulSoup4, Selenium |
| 임베딩 | ko-sentence-transformers |

---

## 디렉토리 구조

```
project-root/
├── CLAUDE.md
├── docker-compose.yaml
├── .env.example
├── dags/
│   ├── etl_pipeline.py          # 메인 DAG
│   ├── extractors/
│   │   ├── dart_extractor.py    # OpenDART API
│   │   ├── kipris_extractor.py  # KIPRIS API
│   │   ├── bizinfo_extractor.py # 중소벤처24 API
│   │   ├── welfare_loader.py    # 수혜이력 CSV 로더
│   │   └── crawler.py           # 중진공 홈페이지 공고문 크롤러
│   └── transformers/
│       └── merge.py             # Master DataFrame 병합
├── agents/                      # LangGraph MAS 에이전트
│   ├── orchestrator/
│   │   ├── system_prompt.md     # 역할·흐름 제어·피드백 생성 규칙
│   │   └── planning.md          # ReAct 기반 피드백 우선순위 결정
│   ├── embedding/
│   │   └── system_prompt.md     # Hard/Soft Filter 역할 정의
│   ├── scoring/
│   │   └── system_prompt.md     # 스코어링 수식·MLflow 로드 방식 정의
│   └── shap/
│       └── system_prompt.md     # delta 기준·feature 선별 규칙 정의
├── models/
│   ├── train.py                 # LightGBM 학습
│   ├── predict.py               # 추론
│   └── explainer.py             # SHAP 기반 XAI
├── api/
│   └── main.py                  # FastAPI 서버
└── tests/
```

---

## S3 경로 규칙

```
s3://[BUCKET_NAME]/
├── raw/
│   ├── dart/YYYY-MM-DD/              # OpenDART 원본 JSON
│   ├── kipris/YYYY-MM-DD/            # 특허 원본 JSON
│   ├── bizinfo/YYYY-MM-DD/           # 사업목록 원본 JSON
│   ├── announcements/YYYY-MM-DD/     # 중진공 공고문 PDF/HWP (crawler.py 적재)
│   │   └── metadata.json             # 공고명·URL·파일경로·수집일시
│   └── welfare/                      # 수혜이력 CSV (수동 업로드)
├── processed/
│   ├── company_features.parquet # 기업 feature 테이블
│   ├── program_features.parquet # 사업 feature 테이블
│   └── labels.parquet           # 선정여부 레이블 (0/1) — LightGBM LambdaRank 학습용
└── embeddings/                  # 팀원(박지윤) 담당 — LLM 파싱 결과물
    ├── announcements/           # 공고문 임베딩 벡터
    └── requirements_db/         # 자격요건 구조화 DB
```

---

## 데이터 소스 및 역할

| 소스 | 데이터 | 용도 |
|---|---|---|
| OpenDART | 재무제표, 업종코드, 설립일, 종업원수 | 기업 feature (Primary) |
| KIPRIS | 특허/실용신안 명칭·요약문 | 기술력 feature |
| 중소벤처24 | 사업목록, 카테고리, 신청기간, 예산 | 사업 feature |
| 정보공개포털 | 과거 수혜 이력 | 학습 레이블 (선정여부 0/1) |
| 중진공 홈페이지 | PDF/HWP 공고문 | 팀원 담당 (LLM 파싱) |

---

## 코딩 컨벤션

### 필수 규칙
- 모든 API 키 및 시크릿은 `.env` 파일로 관리, **하드코딩 절대 금지**
- 환경변수 접근은 `os.environ.get('KEY')` 사용
- 각 extractor 모듈은 독립 실행 가능하도록 설계 (`if __name__ == '__main__'` 포함)
- 로깅은 `logging` 모듈 사용 (`print` 금지)
- S3 적재 시 날짜 파티셔닝 적용: `raw/{source}/YYYY-MM-DD/`
- 데이터 저장 포맷: 원본은 JSON, 가공 데이터는 Parquet

### Airflow DAG 규칙
- `retries=2`, `retry_delay=timedelta(minutes=5)` 기본 적용
- 각 extract 태스크는 병렬 실행 가능하도록 설계
- XCom 사용 최소화 — 태스크 간 데이터 전달은 S3 경로로

### 에러 처리
- API 호출 실패 시 최대 3회 재시도 후 Airflow 태스크 실패 처리
- 결측값은 `None`으로 유지 (임의 대체 금지 — 모델에서 처리)

---

## 주요 데이터 스키마

### 기업 feature (company_features.parquet)
```
company_id       # 사업자등록번호
revenue          # 매출액
operating_profit # 영업이익
capital          # 자본금
debt_ratio       # 부채비율
net_income       # 당기순이익
cash_flow        # 영업활동 현금흐름
industry_code    # 업종코드 (KSIC)
region           # 소재지
employee_count   # 종업원수
business_age     # 업력 (설립일 기준)
patent_count     # 특허 보유 수
is_venture       # 벤처기업 인증 여부
is_innobiz       # 이노비즈 인증 여부
credit_grade     # 신용등급 (없으면 None)
```

### 사업 feature (program_features.parquet)
```
program_id       # 사업공고 ID
program_name     # 사업명
category         # 자금/수출/인력/기타
max_support      # 지원 한도
interest_rate    # 금리
apply_start      # 신청 시작일
apply_end        # 신청 종료일
industry_limit   # 지원 제외 업종 (팀원 파싱)
debt_ratio_limit # 부채비율 상한 (팀원 파싱)
requirements     # 기타 자격요건 (팀원 파싱)
```

### 레이블 (labels.parquet)
```
company_id   # 사업자등록번호
program_id   # 사업공고 ID
selected     # 선정여부 (1: 선정, 0: 미선정)
year         # 수혜 연도
```

---

## AI 파이프라인 (LangGraph MAS)

### 전체 흐름
오케스트레이터 → 임베딩 에이전트 → 스코어링 에이전트 → SHAP 에이전트 → 오케스트레이터 (복귀)
- 후보 없을 경우 임베딩 에이전트에서 조기 복귀 (LangGraph 조건부 엣지)
- 오케스트레이터가 피드백 생성 및 최종 응답 조합 후 FastAPI로 전달

### 에이전트별 역할
| 에이전트 | system_prompt 위치 | 주요 역할 |
|---|---|---|
| 오케스트레이터 | `agents/orchestrator/system_prompt.md` | State 초기화, 흐름 판단, Gemini API 호출, 최종 응답 조합 |
| 임베딩 에이전트 | `agents/embedding/system_prompt.md` | S3 데이터 로드, Hard Filter, Soft Filter (임베딩 유사도) |
| 스코어링 에이전트 | `agents/scoring/system_prompt.md` | feature 엔지니어링, MLflow 모델 로드, LambdaRank 추론, Top-N 정렬 |
| SHAP 에이전트 | `agents/shap/system_prompt.md` | SHAP TreeExplainer, feature 기여도 추출, delta 계산, 보완 플래그 설정 |

### 공유 State 스키마 (PolicyFundState)
```python
class PolicyFundState(TypedDict):
    company_id: str              # 사업자등록번호
    company_features: dict       # 기업 feature (company_features.parquet 행)
    candidate_programs: list     # Hard Filter 통과 후보 사업 목록
    ranked_programs: list        # LambdaRank 점수 정렬 결과
    shap_values: dict            # feature별 SHAP 기여도
    feedback: str                # Gemini가 생성한 자연어 개선 가이드
    error: str | None            # 에러 메시지 (있을 경우)
```

### 스코어링 수식
```
P(Selection) = α·F + β·T + γ·G
```
- F: 재무 점수 (부채비율, 매출성장률, 현금흐름)
- T: 기술 점수 (특허수, 출원일, IPC 코드)
- G: 정책 가점 (벤처인증, 이노비즈, 고용창출)
- α, β, γ: LightGBM LambdaRank NDCG 최적화로 자동 결정

### SHAP XAI 기준
- SHAP TreeExplainer 적용, feature 기여도 상위 3개 선별
- delta 10% 이내 항목에만 "보완 가능" 피드백 생성
- 음수 기여(감점) 항목 우선 선별

---

## 참고사항
- 수혜이력 데이터 양이 많음 → 클래스 불균형 처리 필요 (`scale_pos_weight`)
- KODATA 신용등급은 샘플 수준 → `credit_grade=None` 처리 후 재무지표로 proxy
- HWP 파싱은 팀원(박지윤) 담당 — `embeddings/` 경로 변경 시 팀원과 사전 협의
- 모델은 GNN 확장 가능 구조로 설계하되 현재는 LightGBM LambdaRank 우선 구현
- processor.py(팀원) 출력 스키마(`target_sector`, `target_location`, `required_tech`)는 program_features의 `industry_limit`, `debt_ratio_limit`, `requirements` 필드와 매핑 필요 — 통합 시 협의

---

## 로컬 스킬

Claude Code에서 사용할 수 있는 스킬 파일 목록.
트리거 조건에 해당하는 요청 시 자동으로 적용된다.

| 스킬 | 경로 | 트리거 |
|---|---|---|
| 프로젝트 온보딩 | .claude/skills/project-onboarding.md | "작업 재개", "컨텍스트 파악", "어디까지 했지" |
| 개발 회고 작성 | .claude/skills/devlog-writer.md | "회고 작성", "devlog 써줘", "마무리하자" |