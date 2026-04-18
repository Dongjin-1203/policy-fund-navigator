"""FastAPI 서버 메인 모듈."""
from fastapi import FastAPI

app = FastAPI(title='중진공 정책자금 AI 매칭 API')


@app.get('/health')
def health_check():
    pass
