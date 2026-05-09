# PRD — 중진공 AI 자금 네비게이터
**policy-fund-navigator**
버전: 1.1
작성일: 2026-05-07
최종 수정: 2026-05-09 (베타 테스트 1차 세션 반영)
작성자: 지동진
상태: 베타 테스트 진행 중

---

## 1. 서비스 개요

### 1.1 배경 및 목적

중소기업이 정책자금을 신청하려면 수십 개의 사업 공고를 직접 확인하고 자격 요건을 대조해야 한다. 이 과정에서 정보 비대칭으로 인해 자격이 되는 기업도 신청을 포기하거나 부적격 사업에 헛걸음하는 문제가 발생한다. 본 서비스는 기업의 재무·기술·인증 데이터를 자동 분석하여 적합한 정책자금을 AI가 매칭하고, 수혜 확률과 개선 가이드를 제공하는 컨설팅 솔루션이다.

### 1.2 서비스 목표

- 사업자번호 입력만으로 신청 가능한 정책자금 Top-N 자동 추천
- 룰 기반 스코어링을 통한 자금 적합도 점수 제공
- XAI(설명 가능한 AI) 기반 매칭 이유 및 개선 가이드 제시

### 1.3 타겟 사용자

중소기업진흥공단 실무자

### 1.4 대회 정보

- 대회명: AI, 공공데이터 활용 및 창업 경진대회 공모전
- 평가 기준: [확인 필요 — 세부 평가 지표 기입]
- 제출 형태: [확인 필요 — 서비스 소개서/개발 기술서/시연 영상 등]

---

## 2. 핵심 기능 정의

### 2.1 기업 프로필 자동완성

- 사업자번호 입력 시 OpenDART, KIPRIS API를 통해 기업 정보 자동 조회
- 조회 항목: 재무제표(매출액·영업이익·자본금·부채비율·당기순이익·현금흐름), 업종코드(KSIC), 설립일, 종업원수, 소재지, 특허 보유 현황

### 2.2 정책자금 매칭 (Top-N 추천)

- Hard Filter: 업종·부채비율·업력·소재지 자격 조건 자동 체크
- Soft Filter: 특허·기술력과 사업 공고문 간 의미적 유사도 매칭
- 룰 기반 스코어링 기반 적합도 순위 산출
- 결과: 적격 사업 목록 + 잠정 적격 사업 목록 분리 출력

### 2.3 XAI 기반 컨설팅 리포트

> **XAI(Explainable AI)**: AI 모델이 왜 이런 결과를 냈는지 사람이 이해할 수 있는
> 언어로 설명하는 기술. 본 서비스에서는 각 feature의 가중치 기여도를 직접 계산하여
> 자연어 개선 가이드를 생성한다.

- 매칭 이유 설명: feature별 가중치 기여도 기반 자연어 설명
- 개선 가이드: delta 분석 기반 "X만 보완하면 가능" 피드백
- 수혜 확률 변화 시뮬레이션: 지표 개선 시 점수 변화 제시

### 2.4 사후 관리

> **[확인 필요]** 아래 기능의 구현 여부를 결정해주세요.
> - 관심 사업 저장 및 마감 알림 기능
> - 보완 지표 업데이트 시 점수 실시간 변화 알림

---

## 3. 데이터 정의

### 3.1 데이터 소스

| 구분 | 소스 | 수집 방법 | 상태 |
|---|---|---|---|
| 기업 재무 (상장사·외감법인) | OpenDART | 온디맨드 실시간 조회 + 주간 갱신 | 완료 |
| 기업 재무 (비상장) | 사용자 직접 입력 | FastAPI 요청 바디 | 완료 |
| 특허·실용신안 | KIPRIS | 배치 수집 + 주간 갱신 | 완료 |
| 벤처기업 인증 | smes.go.kr | 배치 수집 + 주간 갱신 | 구현 예정 |
| 이노비즈 인증 | innobiz.net | 배치 수집 + 주간 갱신 | 구현 예정 |
| 사업 공고 목록 | 기업마당(bizinfo) | 배치 수집 | 완료 |
| 공고문 원본 | 중진공 홈페이지 | 크롤러 | **완료 (14건 수집, 11건 재파싱 완료)** |
| program_features.parquet | 병합·LLM 파싱 결과 | merge.py + processor.py | **502행, program_id 0-indexed 정렬 완료** |

> **[수혜이력 비공개 처분 관련]**
> 중진공 정책자금 수혜이력 데이터가 「공공기관의 정보공개에 관한 법률」
> 제9조제1항 제7호(경영·영업상 비밀)를 근거로 비공개 처분되었습니다.
> 이에 따라 학습 기반 스코어링 대신 룰 기반 스코어링으로 설계를 변경하며,
> 추후 실데이터 확보 시 LightGBM/GNN 기반으로 고도화할 예정입니다.
> 이의신청을 통한 익명 통계 데이터 재청구를 병행 진행합니다.

### 3.2 데이터 스키마

![alt text](data-schema.png)
data_schema.svg — 데이터 스키마 다이어그램

#### 기업 feature (company_features.parquet)

| 필드 | 설명 |
|---|---|
| company_id | 사업자등록번호 (PK) |
| revenue | 매출액 |
| operating_profit | 영업이익 |
| capital | 자본금 |
| debt_ratio | 부채비율 |
| net_income | 당기순이익 |
| cash_flow | 영업활동 현금흐름 |
| industry_code | 업종코드 (KSIC) |
| region | 소재지 |
| employee_count | 종업원수 |
| business_age | 업력 (설립일 기준) |
| patent_count | 특허 보유 수 |
| is_venture | 벤처기업 인증 여부 |
| is_innobiz | 이노비즈 인증 여부 |
| credit_grade | 신용등급 (없으면 None) |

#### 사업 feature (program_features.parquet)

| 필드 | 설명 |
|---|---|
| program_id | 사업공고 ID (PK) |
| program_name | 사업명 |
| category | 금융/기술/인력/수출/내수/창업/경영/기타 |
| max_support | 지원 한도 |
| interest_rate | 금리 |
| apply_start | 신청 시작일 |
| apply_end | 신청 종료일 |
| industry_limit | 지원 제외 업종 (LLM 파서) |
| debt_ratio_limit | 부채비율 상한 (LLM 파서) |
| requirements | 기타 자격요건 (LLM 파서) |

> **[변경]** 수혜이력 비공개 처분으로 labels.parquet은 현재 생성하지 않습니다.
> 추후 실데이터 확보 시 추가 예정입니다.

### 3.3 수혜 이력 데이터

> **[비공개 처분]** 정보공개포털 청구 결과 비공개 처분되었습니다.
> 추후 이의신청 또는 익명 통계 데이터 재청구를 통해 확보 예정입니다.

---

## 4. 데이터 파이프라인 아키텍처

### 4.1 전체 흐름

![alt text](data_pipeline_architecture.png)data_pipeline_architecture.png — 데이터 파이프라인 전체 흐름도

- 기업 데이터 소스 (OpenDART·KIPRIS·KODATA·공공데이터포털) → API 수집기 → 기업 feature
- 공고문 소스 (기업마당·중진공 홈페이지) → 크롤러 + LLM 파서 → 사업 feature + 자격요건 DB
- 전체 → S3 processed/ Master DataFrame → 학습 데이터셋 · 자격요건 DB · 임베딩 저장소

> **[변경]** 수혜이력 → welfare_loader → labels.parquet 흐름은 현재 제외됩니다.

#### 온디맨드 수집 (FastAPI /match 요청 시)

```
POST /match 요청 (사업자번호)
  → company_features DB 조회
  → 없으면 DART API 실시간 조회
    → 재무 있음 (상장사·외감법인): 자동 저장
    → 재무 없음 (비상장): 사용자 입력 요청 → 저장
  → 스코어링 진행
```

#### 주기적 자동 수집 (Airflow DAG @weekly)

자동 수집 가능:
- extract_dart: 기존 저장 상장사 재무 갱신
- extract_kipris: 특허 데이터 갱신
- extract_bizinfo: 공고 목록 갱신
- extract_venture (예정): 벤처인증 여부 갱신 (smes.go.kr API)
- extract_innobiz (예정): 이노비즈 인증 갱신 (innobiz.net API)

자동 수집 불가 (사용자 입력으로 대체):
- 비상장 중소기업 재무 데이터 (공시 의무 없어 공개 DB 없음)
- 추후 KODATA 등 유료 데이터 연동으로 고도화 예정

### 4.2 Airflow DAG 구조

- extract_dart: 기존 저장 기업만 주간 갱신 (신규 기업은 /match 온디맨드 수집)
- extract_kipris · extract_bizinfo (병렬) → transform_merge → load_to_s3
- extract_venture (예정): smes.go.kr 벤처인증 여부 갱신
- extract_innobiz (예정): innobiz.net 이노비즈 인증 갱신
- 스케줄: @weekly
- 환경: Apache Airflow 2.9 + Docker Compose
- 스토리지: AWS S3

---

## 5. AI 아키텍처

### 5.1 AI 파이프라인 전체 흐름

![alt text](mas_agent_architecture.png)
mas_agent_architecture.png — LangGraph MAS 에이전트 아키텍처

- 오케스트레이터 → 임베딩 에이전트 → 스코어링 에이전트 → SHAP 에이전트 → 오케스트레이터 (복귀)
- 후보 없을 경우 임베딩 에이전트에서 오케스트레이터로 조기 복귀 (조건부 엣지)
- 오케스트레이터가 피드백 생성 및 최종 응답 조합 후 FastAPI로 전달

### 5.2 스코어링 방식

```
P = α·F + β·T + γ·G
```

- F: 재무 점수 (부채비율, 매출성장률, 현금흐름 등 정규화)
- T: 기술 점수 (특허수, IPC 코드 일치, 출원일 등)
- G: 정책 가점 (벤처인증, 이노비즈, 청년고용 등)
- α, β, γ: 도메인 전문가 설정 고정값 (초기값 α=0.4, β=0.3, γ=0.3)

> **[변경]** 수혜이력 데이터 비공개 처분으로 LightGBM LambdaRank 학습 방식에서
> 룰 기반 스코어링으로 변경합니다.
>
> **고도화 계획**: 실데이터 확보 시 아래 순서로 고도화 예정
> 1. LightGBM LambdaRank — labels.parquet 기반 NDCG 최적화
> 2. GNN — 기업·사업 노드 + 수혜이력 엣지 기반 그래프 학습

### 5.3 XAI 기준

> **[변경]** SHAP TreeExplainer → 가중치 기여도 직접 계산 방식으로 변경합니다.
> 룰 기반 스코어링이므로 각 feature의 기여도를 수식에서 직접 산출합니다.

- feature별 가중치 기여도 직접 계산 (α·F, β·T, γ·G 분해)
- 기여도 상위 3개 항목 선별
- delta 10% 이내 항목에만 "보완 가능" 피드백 생성
- 음수 기여(감점) 항목 우선 선별

### 5.4 에이전트별 역할 및 Tools

#### 오케스트레이터
- system_prompt: 역할·흐름 제어·피드백 생성 규칙 정의
- planning (ReAct): delta 크기·feature 기여도 기반 피드백 우선순위 결정
- Skills: State 초기화, 흐름 판단, 피드백 템플릿 선택, Gemini API 호출, 최종 응답 조합

#### 임베딩 에이전트
- system_prompt: 역할·필터링 규칙 정의
- Skills: S3 데이터 로드, 하이브리드 임베딩 생성, Hard Filter 실행, Soft Filter 실행, State 업데이트

#### 스코어링 에이전트
- system_prompt: 역할·스코어링 수식·가중치 설정 정의
- Skills (룰 기반 Tool로 정의): feature 엔지니어링 Tool, 재무점수 계산 Tool, 기술점수 계산 Tool, 정책가점 계산 Tool, Top-N 정렬 Tool, State 업데이트

#### SHAP 에이전트
- system_prompt: 역할·delta 기준·feature 선별 규칙 정의
- Skills: 가중치 기여도 계산 Tool, feature 기여도 추출 Tool, 임계치 delta 계산 Tool, 보완 가능 플래그 설정 Tool, State 업데이트

---

## 6. 기술 스택

| 레이어 | 기술 | 비고 |
|---|---|---|
| ETL 오케스트레이션 | Apache Airflow 2.9 + Docker Compose | 완료 |
| 스토리지 | AWS S3 | 완료 |
| 데이터 처리 | pandas, pyarrow | 완료 |
| 임베딩 | ko-sentence-transformers (snunlp/KR-SBERT-V40K-klueNLI-augSTS) | 완료 |
| 벡터 DB | ChromaDB | 완료 |
| 스코어링 | 룰 기반 (α·F + β·T + γ·G) | 실데이터 확보 시 LightGBM 전환 예정 |
| 실험 관리 | MLflow v2.11.1 — Docker 서비스로 운영, scoring_params v1 등록 완료 | 완료 |
| XAI | 가중치 기여도 직접 계산 | 실데이터 확보 시 SHAP 전환 예정 |
| MAS 프레임워크 | LangGraph | 완료 |
| LLM API | Gemini 2.5-flash | 완료 |
| API 서버 | FastAPI | 완료 |
| 프론트엔드 | Next.js 15 + Zustand | 완료 |
| 크롤링 | BeautifulSoup4, requests | JS 렌더링 불필요 확인 |
| 컨테이너 | Docker Compose (mlflow + airflow + backend + frontend) | 완료 |

---

## 7. 시스템 아키텍처

### 7.1 S3 경로 구조

```
s3://[BUCKET_NAME]/
├── raw/
│   ├── dart/YYYY-MM-DD/
│   ├── kipris/YYYY-MM-DD/
│   ├── bizinfo/YYYY-MM-DD/
│   └── announcements/YYYY-MM-DD/   ← 크롤러 수집 공고문
├── processed/
│   ├── company_features.parquet
│   ├── program_features.parquet
│   └── labels.parquet              ← 실데이터 확보 시 추가 예정
└── embeddings/
    ├── announcements/
    └── requirements_db/
```

### 7.2 API 엔드포인트

| 엔드포인트 | 설명 |
|---|---|
| POST /match | 사업자번호 입력 → DART 온디맨드 조회 → 상장사: 자동 저장 → 비상장: 사용자 재무 정보 입력 요청 → Top-N 매칭 결과 반환 |
| GET /feedback/{program_id} | 특정 사업 XAI 피드백 반환 |

> **[확인 필요]** 위 엔드포인트 외 추가로 필요한 엔드포인트가 있으면 기입해주세요.

---

## 8. 비기능 요구사항

| 항목 | 목표 | 비고 |
|---|---|---|
| 응답 시간 | [확인 필요] | 매칭 API 기준 |
| 동시 사용자 | [확인 필요] | 대회 데모 기준 |
| 모델 성능 | 적합도 점수 정확도 | 룰 기반 기준, 실데이터 확보 시 NDCG 추가 |
| 데이터 갱신 주기 | 주 1회 (@weekly) | Airflow 기준 |

---

## 9. 제약 사항 및 리스크

| 항목 | 내용 | 대응 |
|---|---|---|
| 수혜이력 비공개 | 정보공개 비공개 처분 | 룰 기반 스코어링으로 변경, 이의신청 병행 |
| KODATA 신용등급 | 샘플 수준, 커버리지 낮음 | 재무지표로 proxy 처리 |
| GNN 적용 | 수혜이력 없어 엣지 데이터 없음 | 실데이터 확보 시 확장 아키텍처로 |
| HWP 파싱 | pdfplumber 미지원 | Gemini API 직접 바이너리 전송으로 대응 |
| 클래스 불균형 | 실데이터 없어 해당 없음 | 실데이터 확보 시 재검토 |
| S3 경로 공백 | 공고문 파일명 공백·괄호 포함 | LLM 파서 URL 인코딩 처리 필요, 팀원 협의 |
| 비상장 기업 재무 | 공개 DB 없음 | 사용자 직접 입력 |
| 벤처·이노비즈 인증 | smes.go.kr, innobiz.net | 배치 수집 구현 예정 |
| 신용등급 | KODATA 유료 | 추후 고도화 예정 |
| 변경공고 파싱 실패 | 2026-287호 등 변경공고 processor.py 파싱 실패 | **팀원(박지윤) 확인 필요 — GovernmentNoticeLoader 예외 처리 보완 요청** |
| apply_end 만료 | program_features.parquet의 apply_end가 과거 날짜인 경우 Hard Filter에서 전체 탈락 | apply_end 필터 로직 재검토 필요 (2026년 이전 구 공고 필터링 미구현) |
| slno JOIN 실패 3건 | slno=9307/6730/5305 requirements_db 미생성 | processor.py 재파싱 필요 |
| KIPRIS 실제 데이터 미수집 | T점수 patent_count 직접 입력에만 의존 | Airflow KIPRIS DAG 재실행 필요 |

---

## 10. 팀 구성 및 역할

| 역할 | 담당자 | 담당 영역 |
|---|---|---|
| AI 엔지니어 · 데이터 엔지니어 | 지동진 | ETL 파이프라인, 크롤러, 자격요건 DB 구축, MAS 구현 (State 스키마·오케스트레이터·스코어링·SHAP 에이전트·LangGraph 연결), FastAPI 서버, 프론트엔드 UI |
| AI 엔지니어 | 박지윤 | LLM 파서 (공고문 → 자격요건 구조화), 임베딩 에이전트 설계, 피드백 템플릿 DB 구축 |

### 미정 과업 (팀원과 논의 후 확정 필요)

- MLflow 실험 관리 환경 구축 담당
- 모델 성능 평가 및 검증 담당
- 발표 자료 제작 담당

---

## 11. 타임라인

> **[추후 작성]** 공모 일정 확정 후 작성 예정.

---

## 12. 추가 확인 필요 항목 요약

| 번호 | 항목 | 내용 |
|---|---|---|
| 1 | 대회 평가 기준 | 세부 평가 지표 확인 필요 |
| 2 | 제출 형태 | 서비스 소개서/개발 기술서/시연 영상 등 확인 필요 |
| 3 | 사후 관리 기능 | 알림·실시간 점수 변화 기능 구현 여부 결정 필요 |
| 4 | 비기능 요구사항 수치 | 응답 시간, 동시 사용자 목표 수치 결정 필요 |
| 5 | 추가 API 엔드포인트 | /match, /feedback 외 필요 엔드포인트 확인 필요 |
| 6 | 수혜이력 이의신청 | 익명 통계 데이터 재청구 결과에 따라 스코어링 방식 재검토 |