from dotenv import load_dotenv
load_dotenv()

import io
import logging
import os
from contextlib import asynccontextmanager

import boto3
import pandas as pd
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import match, feedback

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작 시 PolicyVectorStore를 S3에서 로드해 싱글턴으로 주입한다."""
    try:
        import asyncio
        from src.embedder import PolicyVectorStore
        from agents.embedding.agent import set_vector_store

        bucket = os.environ.get("S3_BUCKET_NAME", "")
        if not bucket:
            raise ValueError("S3_BUCKET_NAME 환경변수 미설정")

        s3 = boto3.client("s3")
        obj = await asyncio.to_thread(
            s3.get_object, Bucket=bucket, Key="processed/program_features.parquet"
        )
        df = pd.read_parquet(io.BytesIO(obj["Body"].read()))

        store = await asyncio.to_thread(PolicyVectorStore, os.environ.get("CHROMA_DB_PATH", "./chroma_db"))
        await asyncio.to_thread(store.add_policies, df)

        set_vector_store(store)
        logger.info("PolicyVectorStore 초기화 완료: %d개 프로그램", len(df))
    except Exception as exc:
        logger.warning("PolicyVectorStore 초기화 실패: %s", exc)

    yield


app = FastAPI(
    title="정책자금 네비게이터 API",
    description="중소기업 정책자금 AI 매칭 서비스 — LangGraph MAS 기반",
    version="1.0.0",
    lifespan=lifespan,
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
