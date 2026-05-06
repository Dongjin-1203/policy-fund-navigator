# [에이전틱한 개발자 되기 #1] 중진공 정책자금 AI 매칭 — ETL 파이프라인 구축기

> 시리즈: 에이전틱한 개발자 되기  
> 태그: `Airflow` `ETL` `AWS S3` `Docker` `Python`
> 날짜: 2026-04-18

---

## 1. 프로젝트 목표

중소기업진흥공단(중진공)의 정책자금은 융자·보조·보증 등 수백 개 사업이 존재하지만, 기업이 자신에게 맞는 사업을 직접 찾기는 어렵다. **policy-fund-navigator**는 이 문제를 AI로 푼다.

기업의 재무·신용 데이터(정형)와 특허·공고문 데이터(비정형)를 통합 분석해 적합 정책자금을 추천하고, SHAP 기반 심사 가이드를 제공하는 것이 최종 목표다.

```
외부 API (DART, KIPRIS, bizinfo)
        │
        ▼
  S3 raw/  ─────────────── 오늘 구축한 구간
        │
        ▼
  S3 processed/ (parquet)
        │
        ▼
  3단계 필터링 (Hard → Soft → LightGBM LambdaRank)
        │
        ▼
  FastAPI 추천 서비스
```

---

## 2. 오늘의 목표

| 목표 | 결과 |
|---|---|
| Airflow 2.9 Docker 환경 구성 | ✅ |
| OpenDART extractor 구현 및 S3 적재 | ✅ 327개 기업 수집 |
| bizinfo extractor 구현 및 S3 적재 | ✅ |
| KIPRIS extractor 구현 | ✅ (mock 모드, 키 승인 대기) |
| welfare_loader 구현 | ✅ |
| Airflow DAG 구성 및 동작 확인 | ✅ |
| feature/etl-pipeline → develop Merge | ✅ |

---

## 3. 수집 데이터 정의

### 데이터 소스별 역할

| 소스 | 수집 항목 | 용도 | 상태 |
|---|---|---|---|
| **OpenDART** | 재무제표, 업종코드, 설립일, 종업원수, 소재지 | 기업 feature (Primary) | ✅ 실전 |
| **bizinfo(기업마당)** | 사업명, 공고번호, 분야, 신청기간, 소관기관, URL | 사업 feature | ✅ 실전 |
| **KIPRIS** | 특허/실용신안 명칭, 요약문, IPC 코드, 권리자 | 기술력 feature | ⏳ mock (키 승인 대기) |
| **수혜이력 CSV** | 사업자번호, 공고ID, 선정여부, 연도 | 학습 레이블 (0/1) | ✅ 로더 구현 완료 |

### S3 저장 경로 설계

```
s3://[BUCKET]/
├── raw/
│   ├── dart/2026-04-18/     ← 오늘 수집 (327건 JSON)
│   ├── bizinfo/2026-04-18/  ← 오늘 수집
│   ├── kipris/2026-04-18/   ← mock 2건
│   └── welfare/             ← CSV 수동 업로드
└── processed/
    ├── company_features.parquet
    ├── program_features.parquet
    └── labels.parquet
```

날짜 파티셔닝(`YYYY-MM-DD`)을 적용한 이유는 두 가지다.
1. 재실행 시 덮어쓰기 없이 이력 보존
2. 팀원과 경로 규약만으로 데이터 공유 (XCom, DB 불필요)

---

## 4. 기술 스택 선정 이유

### Apache Airflow 2.9

배치 ETL의 핵심은 **재시도·의존성·모니터링**이다. Airflow는 이 셋을 DAG 코드 한 파일로 관리할 수 있다.

```python
# 병렬 extract → 순차 transform/load
[extract_dart, extract_kipris, extract_bizinfo] >> transform_merge >> load_welfare
```

`retries=2`, `retry_delay=timedelta(minutes=5)`를 default_args에 박아두면 API 간헐적 장애에도 자동 복구된다.

### Docker Compose

팀원과 환경 차이로 생기는 버그를 없애기 위해 도입했다. `docker compose up -d` 한 줄로 postgres, redis, webserver, scheduler, worker 전체가 뜬다. 나중에 EC2로 올릴 때도 Compose 파일 그대로 쓸 수 있다.

### AWS S3

팀원이 별도 저장소에서 LLM 파싱 결과물을 `s3://[BUCKET]/embeddings/`에 올린다. S3를 중간 저장소로 쓰면 팀 간 인터페이스가 **경로 문자열**로 단순화된다.

### Parquet

가공 데이터를 parquet으로 저장하는 이유는 단순하다. LightGBM 학습 시 필요한 컬럼만 읽을 수 있어서 메모리와 I/O가 크게 줄어든다. 원본 JSON은 재현성을 위해 raw/에 그대로 보존한다.

---

## 5. 구현 상세

### 5-1. dart_extractor.py

OpenDART API는 기업 목록 → 재무제표 → 기업정보 순으로 3번 호출해야 한다.

```python
def fetch_financial_statements(corp_code: str, year: int) -> dict:
    """fnlttSinglAcntAll 엔드포인트로 단일회사 전체 재무제표 조회."""
    params = {
        'crtfc_key': _get_api_key(),
        'corp_code': corp_code,
        'bsns_year': str(year),
        'reprt_code': '11011',  # 사업보고서
        'fs_div': 'CFS',        # 연결재무제표 우선
    }
    ...
```

결측값은 `None`으로 유지한다. 모델 단에서 처리하는 것이 원칙이다.

### 5-2. bizinfo_extractor.py

기업마당 API는 분야 코드(`searchLclasId`) 별로 따로 호출해야 전체 데이터를 가져올 수 있다. `01`(금융)~`09`(기타)를 순회하면서 중복을 `pblancId` 기준으로 제거했다.

```python
LCLASS_CATEGORY_MAP = {
    '01': '금융', '02': '기술', '03': '인력',
    '04': '수출', '05': '내수', '06': '창업',
    '07': '경영', '09': '기타',
}

def fetch_all_programs() -> list[dict]:
    seen_ids: set[str] = set()
    for lclass_id in LCLASS_CATEGORY_MAP:
        items = fetch_programs(lclass_id=lclass_id, search_cnt=500)
        # 중복 제거 후 누적
        ...
```

신청기간(`reqstBeginEndDe`)은 `'2026-01-01 ~ 2026-03-31'` 형태의 단일 문자열로 내려온다. `~` 기준으로 파싱해 `apply_start`, `apply_end`로 분리했다.

### 5-3. kipris_extractor.py — mock 모드 설계

KIPRIS API 키 승인이 아직 안 됐다. `KIPRIS_API_KEY` 유무로 자동 분기하도록 설계해서 키만 넣으면 즉시 실전 전환된다.

```python
def fetch_patents(corp_name: str) -> list[dict]:
    api_key = os.environ.get('KIPRIS_API_KEY')
    if not api_key:
        logger.warning('KIPRIS_API_KEY 없음 — mock 데이터 반환')
        return fetch_patents_mock(corp_name)
    return fetch_patents_real(corp_name, api_key)
```

---

## 6. 트러블슈팅

### 🔴 Issue 1 — Airflow import 경로 오류

```
ModuleNotFoundError: No module named 'dags'
```

**원인**: 로컬 경로 기준으로 `from dags.extractors.dart_extractor import ...` 작성  
**해결**: Airflow 컨테이너 내부에서 `/opt/airflow/dags`가 루트이므로 `dags.` 제거

```python
# 변경 전
from dags.extractors.dart_extractor import extract_dart_task

# 변경 후
from extractors.dart_extractor import extract_dart_task
```

---

### 🔴 Issue 2 — bizinfo API 404

```
404 Client Error: https://www.bizinfo.go.kr/uss/rss/bizinfo.do
```

**원인**: 엔드포인트 URL과 인증키 파라미터명이 실제 명세와 달랐다.

| 항목 | 잘못된 값 | 실제 명세 |
|---|---|---|
| URL | `bizinfo.do` | `bizinfoApi.do` |
| 인증키 파라미터 | `serviceKey` | `crtfcKey` |
| 페이지네이션 | `pageNo` / `numOfRows` | `searchLclasId` 분야별 순회 |
| 신청기간 필드 | `aply_bgng_dt`, `aply_end_dt` | `reqstBeginEndDe` (단일 문자열) |

**해결**: `https://www.bizinfo.go.kr/apiDetail.do?id=bizinfoApi` 명세 재확인 후 전면 수정

---

### 🔴 Issue 3 — OPENDART_API_KEY 미인식 (핵심)

```
ValueError: OPENDART_API_KEY 환경변수가 설정되지 않았습니다.
```

처음에는 `docker-compose.yaml`의 `env_file`과 `environment` 블록 중복이 문제라고 판단해 `environment`에서 시크릿을 제거했다. 그런데도 에러가 계속됐다.

컨테이너 내부에서 직접 확인해보니:

```bash
docker compose exec airflow-worker env | grep OPENDART
# 아무것도 출력되지 않음
```

`.env` 파일의 변수명 목록을 뽑아보니 발견됐다:

```
OPENDAT_API_KEY=...   # ← 'R' 누락 오타
```

**해결**: 값은 건드리지 않고 키 이름만 수정

```bash
sed -i 's/^OPENDAT_API_KEY=/OPENDART_API_KEY=/' .env
docker compose down && docker compose up -d
```

```bash
# 수정 후 확인
docker compose exec airflow-worker env | grep OPENDART
OPENDART_API_KEY=87450d5f...  # 정상 주입
```

`env_file`과 `environment` 블록 중복 문제는 실제로 존재한다. `environment`의 `${VAR}` 표현식은 호스트 셸에 export된 값을 참조하므로, 호스트에 export가 안 된 상태에서는 빈 문자열로 치환되어 `env_file` 값을 덮어쓴다. 이번 케이스는 오타가 근본 원인이었지만, **시크릿은 `env_file` 단일 경로로만 관리**하는 것이 맞다.

---

## 7. 결론

```
S3 적재 현황 (2026-04-18)
├── raw/dart/2026-04-18/     327개 기업 JSON
├── raw/bizinfo/2026-04-18/  프로그램 목록 JSON
└── raw/kipris/2026-04-18/   mock 2건 JSON
```

Airflow 웹서버(localhost:8080)에서 `etl_pipeline` DAG가 정상 인식되고, 전체 태스크가 성공했다. `feature/etl-pipeline` → `develop` Squash and Merge로 PR #1 병합 완료.

---

## 8. 다음 작업

- [ ] KIPRIS API 키 발급 후 mock → 실전 전환 테스트
- [ ] 공공데이터포털 수혜이력 CSV S3 업로드 → `welfare_loader` 실행 → `labels.parquet` 생성

---

*전체 코드: [github.com/Dongjin-1203/policy-fund-navigator](https://github.com/Dongjin-1203/policy-fund-navigator)*
