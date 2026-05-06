from dotenv import load_dotenv
load_dotenv()

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import match, feedback

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

app = FastAPI(
    title="정책자금 네비게이터 API",
    description="중소기업 정책자금 AI 매칭 서비스 — LangGraph MAS 기반",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(match.router, prefix="/api/v1", tags=["매칭"])
app.include_router(feedback.router, prefix="/api/v1", tags=["피드백"])


@app.get("/health", tags=["헬스체크"])
async def health() -> dict:
    return {"status": "ok"}
