import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def get_company_info(company_id: str) -> Optional[dict]:
    """DART API 온디맨드 조회 (비동기 래퍼).

    agents/orchestrator/agent.py의 _lookup_dart / _load_s3_company와
    동일한 조회 로직을 asyncio.to_thread로 감싸 비동기 컨텍스트에서 사용.
    재무 데이터가 없으면 None 반환.
    """
    try:
        from agents.orchestrator.agent import _load_s3_company, _lookup_dart, _has_financial_data

        cached = await asyncio.to_thread(_load_s3_company, company_id)
        if cached and _has_financial_data(cached):
            logger.info("dart service: S3 캐시 히트 company_id=%s", company_id)
            return cached

        dart_data = await asyncio.to_thread(_lookup_dart, {"company_id": company_id})
        if dart_data and _has_financial_data(dart_data):
            logger.info("dart service: DART 조회 성공 company_id=%s", company_id)
            return dart_data

        return None
    except Exception as exc:
        logger.error("dart service: 조회 실패 company_id=%s: %s", company_id, exc)
        return None
