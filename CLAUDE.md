# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

중소기업진흥공단(중진공) 정책자금 AI 매칭 서비스 — 기업의 정형(재무/신용) 및 비정형(특허/공고문) 데이터를 통합 분석해 적합 정책자금을 추천하고 SHAP 기반 심사 가이드를 제공한다.

## 팀 구성
- 지동진(이 저장소): ETL 파이프라인, AI 모델링, 스코어링 엔진
- 팀원(별도 저장소): LLM 기반 공고문 파싱, 자격요건 DB 구축 → `s3://[BUCKET]/embeddings/` 경로 소유

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
| XAI | SHAP |
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
│   │   └── welfare_loader.py    # 수혜이력 CSV 로더
│   └── transformers/
│       └── merge.py             # Master DataFrame 병합
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
│   ├── dart/YYYY-MM-DD/         # OpenDART 원본 JSON
│   ├── kipris/YYYY-MM-DD/       # 특허 원본 JSON
│   ├── bizinfo/YYYY-MM-DD/      # 사업목록 원본 JSON
│   └── welfare/                 # 수혜이력 CSV (수동 업로드)
├── processed/
│   ├── company_features.parquet # 기업 feature 테이블
│   ├── program_features.parquet # 사업 feature 테이블
│   └── labels.parquet           # 선정여부 레이블 (0/1)
└── embeddings/                  # 팀원 담당 — LLM 파싱 결과물
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

## 참고사항
- 수혜이력 데이터 양이 많음 → 클래스 불균형 처리 필요 (`scale_pos_weight`)
- KODATA 신용등급은 샘플 수준 → `credit_grade=None` 처리 후 재무지표로 proxy
- HWP 파싱은 팀원 담당 — `embeddings/` 경로 변경 시 팀원과 사전 협의
- 모델은 GNN 확장 가능 구조로 설계하되 현재는 LightGBM LambdaRank 우선 구현