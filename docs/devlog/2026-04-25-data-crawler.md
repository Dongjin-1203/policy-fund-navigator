# [에이전틱한 개발자 되기 #2] 중진공 정책자금 AI 매칭 — 크롤러 구현기

> 시리즈: 에이전틱한 개발자 되기  
> 태그: `Crawler` `KOSMES` `AWS S3` `HWP` `Python` `KIPRIS` `Gemini`  
> 날짜: 2026-04-25

---

## 1. 프로젝트 목표

지난 주(#1)에 Airflow ETL 파이프라인의 뼈대를 세웠다. OpenDART, bizinfo, KIPRIS API에서 기업·사업 데이터를 S3로 적재하는 흐름을 완성했지만, 한 가지가 빠져 있었다. 중진공 홈페이지에 올라오는 **PDF/HWP 공고문**이다.

팀원 박지윤이 담당하는 LLM 파서(`feature/llm-processor`)는 이 공고문을 입력으로 받아 자격요건(업종 제한, 부채비율, 기타 요건)을 구조화한다. 공고문이 S3에 없으면 LLM 파서도 돌아갈 수 없다. **이번 주 목표는 중진공 공고문 크롤러를 완성해 S3 적재까지 end-to-end를 검증하는 것**이었다.

```
[중진공 홈페이지]
     │  HWP/PDF 공고문
     ▼
crawler.py  ──────────────────────────────────► S3 raw/announcements/YYYY-MM-DD/
                                                   ├── 공고문.hwp
                                                   └── metadata.json
                                                        │
                                                        ▼
                                              팀원(feature/llm-processor)
                                              LLM 파싱 → requirements_db
```

---

## 2. 오늘의 목표

| 목표 | 결과 |
|---|---|
| 중진공 공고문 크롤러 구현 (crawler.py) | ✅ 실전 다운로드 + S3 업로드 검증 |
| HWP 매직 바이트 검증 로직 추가 | ✅ OLE2 / HWPX(ZIP) / PDF 분기 |
| 메타데이터 병합 로직 (덮어쓰기 방지) | ✅ slno 기준 병합, skipped 항목 원본 유지 |
| KIPRIS extractor mock → 실전 전환 | ✅ XML 파싱, 3회 재시도 구현 |
| processor.py 출력 스키마 PRD 기준 통일 | ✅ program_features 스키마와 필드명 일치 |
| CLAUDE.md PRD v0.3 기준 갱신 | ✅ LangGraph MAS, 스코어링 수식, SHAP 기준 추가 |

---

## 3. 구현 상세

### 3-1. crawler.py — 중진공 공고문 크롤러

#### 중진공 홈페이지 구조 분석

처음에는 Selenium으로 접근했다. 버튼을 클릭하고 파일 다운로드 대화상자를 제어하는 방식이었는데, 중진공 홈페이지가 JavaScript SPA로 구성되어 있어서 DOM이 렌더링되기까지 타이밍 제어가 복잡했다.

브라우저 개발자 도구 네트워크 탭을 열어서 실제 요청을 분석했다. 결과는 예상보다 단순했다.

```
1. GET /nsh/SH/SIT/SHSIT044M0.do   → JSESSIONID_SBC 쿠키 획득
2. POST /sh/sit/selectSHSIT032.json  → 게시판 목록 JSON
3. POST /fileDown2.do                → 파일 바이너리 (토큰 기반)
```

Selenium을 버리고 `requests.Session`으로 전면 교체했다. 세션 쿠키만 제대로 유지하면 AJAX API를 직접 호출할 수 있었다.

#### 세션 관리

```python
def init_session(session: requests.Session) -> None:
    url = urljoin(KOSMES_BASE_URL, SESSION_INIT_PATH)
    resp = session.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    # JSESSIONID_SBC, WMONID 쿠키 자동 저장
```

`requests.Session`은 쿠키를 자동으로 유지하기 때문에 첫 GET 요청으로 세션만 초기화하면 이후 POST 요청에서 별도 처리 없이 인증이 통과된다.

#### 토큰 기반 파일 다운로드

목록 API 응답에서 각 파일의 마스킹 토큰(`DCLR_DATA_FL_MSK_TXT`)을 추출해 POST 요청에 실어 보낸다.

```python
data = {'name': token, 'Rname': filename, 'path': 'down'}
resp = session.post(FILE_DOWNLOAD_PATH, data=data, headers=headers, timeout=60, stream=True)
```

다운로드 실패 시 지수 백오프(`2^attempt` 초)로 3회 재시도한다. 응답 크기가 100 bytes 미만이면 실패로 간주한다. 실제로 세션 만료 시 HTML 에러 페이지가 내려오는데, 이때 바이너리가 아닌 HTML이 반환되므로 크기 체크로 걸러낼 수 있었다.

#### HWP 매직 바이트 검증

파일이 진짜 HWP/PDF인지 확인하는 로직을 추가했다. Content-Type 헤더는 믿을 수 없기 때문에 파일 앞 4바이트로 판별한다.

```python
_MAGIC_MAP = {
    b'\xd0\xcf\x11\xe0': 'hwp',   # OLE2 컨테이너 (HWP 97~2007)
    b'HWP ': 'hwpx',               # HWP 문서 파일
    b'%PDF': 'pdf',
}
# HWPX는 ZIP 기반 (PK\x03\x04)
if content[:4] == b'PK\x03\x04':
    return 'hwpx'
```

실전 테스트에서 수집된 공고문은 `\xd0\xcf\x11\xe0`(OLE2 포맷)으로 정상 HWP 파일임을 확인했다. 파일 크기는 229,376 bytes였다.

#### 중복 방지

S3에 같은 경로의 파일이 있으면 다운로드를 건너뛴다.

```python
def _s3_key_exists(s3_client, bucket: str, key: str) -> bool:
    try:
        s3_client.head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False
```

`list_objects`가 아닌 `head_object`를 쓴 이유는 단순하다. 존재 여부만 확인하는 데 목록 조회는 과하다. `head_object`는 단일 네트워크 왕복으로 끝난다.

### 3-2. KIPRIS extractor — mock → 실전 전환

1주차에 API 키 승인 대기로 mock 모드로만 구현했던 KIPRIS extractor를 실전으로 전환했다.

KIPRIS API는 REST가 아닌 **XML 응답** 방식이다. `xml.etree.ElementTree`로 파싱했다.

```python
for item in root.iter('item'):
    def _text(tag: str) -> str | None:
        el = item.find(tag)
        return el.text.strip() if el is not None and el.text else None

    patents.append({
        'application_number': _text('applicationNumber'),
        'title':              _text('inventionTitle'),
        'abstract':           _text('astrtCont'),
        'ipc_code':           _text('ipcNumber'),
        ...
    })
```

mock 모드 분기는 `KIPRIS_API_KEY` 환경변수 유무로 자동 처리된다. 키가 없으면 경고 로그를 남기고 mock 데이터로 전환한다.

### 3-3. processor.py — 출력 스키마 통일

팀원 박지윤이 `feature/llm-processor` 브랜치에서 작성한 processor.py(WIP)의 출력 스키마가 PRD v0.3 기준과 달랐다. 기존 WIP 코드는 아래와 같은 필드를 반환했다.

| WIP 필드명 | PRD v0.3 / program_features 스키마 |
|---|---|
| `target_sector` | `industry_limit` |
| `target_location` | — (없는 필드) |
| `required_tech` | `requirements` |
| — | `debt_ratio_limit` |

통합 시 병합 오류가 발생하는 것을 방지하기 위해 스키마를 맞췄다.

```python
# 수정 후 Gemini API 응답 스키마
'response_schema': {
    'type': 'OBJECT',
    'properties': {
        'industry_limit':   {'type': 'STRING'},
        'debt_ratio_limit': {'type': 'STRING'},
        'requirements':     {'type': 'STRING'},
    },
    'required': ['industry_limit', 'debt_ratio_limit', 'requirements'],
}
```

`print` → `logging` 교체, Gemini 모델도 `gemini-2.0-flash` → `gemini-2.5-flash`로 업데이트했다. `requirements.txt`에 `pdfplumber>=0.11.0`, `google-genai>=1.0.0` 의존성도 추가했다.

---

## 4. 트러블슈팅

### 🔴 Issue 1 — JavaScript SPA 대응: Selenium → requests 전환

중진공 홈페이지는 Single Page Application 구조라 Selenium으로 접근하면 `WebDriverWait`, `EC.element_to_be_clickable` 등을 조합해야 했다. 브라우저 드라이버 버전 관리 부담도 있었다.

**해결**: 브라우저 네트워크 탭 분석으로 AJAX 엔드포인트(`/sh/sit/selectSHSIT032.json`)와 파일 다운로드 엔드포인트(`/fileDown2.do`)를 직접 파악했다. `requests.Session`으로 쿠키를 유지하면 JavaScript 렌더링 없이 동일한 결과를 얻을 수 있었다. 코드가 단순해졌고 속도도 빠르다.

---

### 🔴 Issue 2 — 메타데이터 덮어쓰기 문제

**증상**: 1차 실행(신규 수집)에서 `file_size`, `file_format`이 포함된 metadata.json이 S3에 저장됐다. 2차 실행(파일 이미 존재 → 스킵)에서 `skipped: true` 항목이 저장되며 `file_size`, `file_format`이 유실됐다.

**원인**: `upload_metadata_to_s3()`가 기존 파일을 읽지 않고 매번 덮어썼기 때문이다. 스킵 항목에는 다운로드 단계가 없으므로 파일 크기·형식 정보가 없다.

**해결**: 업로드 전에 S3에서 기존 metadata.json을 읽어 `slno`(공고번호) 기준으로 병합한다. `skipped: true` 항목은 기존 레코드를 그대로 유지한다.

```python
# 기존 파일 로드
existing: dict[str, dict] = {}
try:
    resp = s3_client.get_object(Bucket=bucket, Key=key)
    for item in json.loads(resp['Body'].read()):
        if slno := item.get('slno'):
            existing[slno] = item
except s3_client.exceptions.NoSuchKey:
    pass  # 첫 실행이면 새로 생성

# 병합
for item in metadata:
    slno = item.get('slno')
    if item.get('skipped') and slno in existing:
        existing[slno] = existing[slno]  # 기존 데이터 유지
    else:
        existing[slno] = item            # 신규/갱신
```

재실행 테스트에서 2차 실행 후 metadata.json에 1차 수집의 `file_size: 229376`, `file_format: "hwp"`가 그대로 유지되는 것을 확인했다.

---

### 🟡 Issue 3 — S3 경로 내 파일명 공백·괄호

수집된 파일명이 `1. 2026년 중소기업 정책자금 융자계획 공고문(제2026-287호).hwp`로 공백과 괄호를 포함한다. S3 key로는 동작하지만, boto3 presigned URL이나 HTTP 직접 접근 시 URL 인코딩 처리가 필요하다. 팀원과 S3 경로 접근 방식을 협의할 필요가 있다.

---

## 5. 팀원 협업 사항

### processor.py 스키마 협의

팀원(박지윤) WIP 코드의 출력 스키마를 PRD v0.3 기준으로 수정했다. 변경 내역을 아래와 같이 공유한다.

| 항목 | 이전 | 이후 | 비고 |
|---|---|---|---|
| 지원 제외 업종 필드명 | `target_sector` | `industry_limit` | program_features 스키마 기준 |
| 부채비율 상한 | 없음 | `debt_ratio_limit` | 신규 추가 |
| 기타 자격요건 | `required_tech` | `requirements` | 명칭 변경 |
| 지원 지역 | `target_location` | 제거 | program_features에 없는 필드 |
| Gemini 모델 | `gemini-2.0-flash` | `gemini-2.5-flash` | 최신 모델 전환 |

### S3 경로 공백 이슈

crawler.py가 수집한 파일은 `raw/announcements/YYYY-MM-DD/{원본파일명}`으로 저장된다. 원본 파일명에 공백과 특수문자가 포함될 수 있다. 팀원 측 LLM 파서가 이 경로를 읽을 때 boto3 `get_object(Key=...)` 방식으로 접근하면 문제없지만, URL 직접 조합 방식이라면 퍼센트 인코딩 처리가 필요하다. 통합 전 협의 예정.

---

## 6. 결론

```
S3 적재 현황 (2026-04-25)
└── raw/
    └── announcements/
        └── 2026-04-25/
            ├── 1. 2026년 중소기업 정책자금 융자계획 공고문(제2026-287호).hwp  (224 KB)
            └── metadata.json  (slno=9629, file_format=hwp, file_size=229376)
```

crawler.py end-to-end 검증 완료:

- WE17(정책자금융자사업연간계획) 게시판 14건 수집 확인 (페이지 2개)
- POST /fileDown2.do 토큰 기반 다운로드 → OLE2 HWP 형식 확인
- S3 업로드 성공, 중복 방지 재실행 스킵 확인
- 메타데이터 병합 로직: 2차 실행 후에도 file_size, file_format 유지 확인

---

## 7. 다음 작업

- [ ] 공공데이터포털 수혜이력 CSV 확보 → S3 업로드 → `welfare_loader` 실행 → `labels.parquet` 생성
- [ ] DART + KIPRIS + bizinfo + welfare 전체 데이터 `merge.py` 통합 테스트
- [ ] processor.py S3 연동: `raw/announcements/`를 읽어 Gemini API 파싱 후 `processed/` 또는 `embeddings/` 경로에 저장
- [ ] 팀원과 S3 경로 공백 이슈 확인 및 처리 방식 합의
- [ ] LightGBM LambdaRank 학습 데이터 준비 (company_features × program_features × labels 병합)

---

## 8. 배운 점

**SPA는 무조건 Selenium이 아니다.** 브라우저 네트워크 탭을 먼저 분석하면 AJAX 엔드포인트를 직접 호출할 수 있는 경우가 많다. Selenium은 브라우저 자동화가 진짜 필요한 경우(캡차, 마우스 인터랙션)에 아껴두는 게 낫다.

**멱등성은 설계 단계에서 결정한다.** 메타데이터 덮어쓰기 버그는 "재실행하면 어떻게 되는가"를 처음부터 고려했다면 나오지 않을 문제였다. S3에 무언가를 쓰는 함수를 만들 때는 항상 기존 데이터가 있을 때의 동작을 명시적으로 설계해야 한다.

**팀 인터페이스는 스키마로 맺는다.** 팀원 WIP 코드의 필드명이 달랐던 것은 작은 이슈지만, 통합 시점이 되면 누군가의 하루를 날릴 수 있다. PRD 스키마를 먼저 정의하고 양쪽이 그것에 맞춰 개발하는 것이 맞다.

---

*전체 코드: [github.com/Dongjin-1203/policy-fund-navigator](https://github.com/Dongjin-1203/policy-fund-navigator)*
