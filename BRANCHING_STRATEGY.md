# 브랜치 전략

## 브랜치 구조

```
main
└── develop
    ├── feature/etl-dart
    ├── feature/etl-kipris
    ├── feature/llm-parser
    ├── feature/scoring-model
    └── fix/dart-extractor
```

---

## 브랜치 역할

| 브랜치 | 역할 | 직접 커밋 |
|---|---|---|
| `main` | 배포/제출용 최종 코드 | 금지 |
| `develop` | 통합 개발 브랜치 | 금지 |
| `feature/*` | 기능 단위 개발 | 허용 |
| `fix/*` | 버그 수정 | 허용 |

---

## 브랜치 네이밍 규칙

```
feature/{작업내용}   # 신규 기능
fix/{버그내용}       # 버그 수정
```

예시:
```
feature/etl-dart
feature/etl-kipris
feature/llm-parser
feature/scoring-model
feature/shap-explainer
feature/fastapi-server
fix/dart-extractor
fix/s3-upload
```

---

## 작업 흐름

### 1. 작업 시작
```bash
git checkout develop
git pull origin develop
git checkout -b feature/{작업내용}
```

### 2. 작업 중 커밋
커밋 메시지는 Conventional Commits 형식을 따른다.

```
feat: OpenDART extractor 구현
fix: KIPRIS API 호출 오류 수정
refactor: S3 업로드 로직 모듈화
chore: requirements.txt 업데이트
docs: CLAUDE.md 스키마 보완
```

```bash
git add .
git commit -m "feat: OpenDART extractor 구현"
```

### 3. 작업 완료 후 PR
```bash
git push origin feature/{작업내용}
# GitHub에서 feature → develop PR 생성
# 팀원 리뷰 후 Merge
```

### 4. develop → main 병합
```bash
# 주요 마일스톤 완료 시 (공모 접수, PT 발표 등)
git checkout main
git merge develop
git tag v1.0.0
git push origin main --tags
```

---

## PR 규칙

- `feature/*` → `develop` : 팀원 리뷰 1명 이상
- `develop` → `main` : 양쪽 확인 후 Merge
- Merge 방식: **Squash and Merge** (커밋 히스토리 간결하게 유지)

---

## 태그 규칙

| 태그 | 시점 |
|---|---|
| `v0.1.0` | ETL 파이프라인 완료 |
| `v0.2.0` | AI 모델링 완료 |
| `v0.3.0` | XAI + API 완료 |
| `v1.0.0` | 공모 제출 버전 |