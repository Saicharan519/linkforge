"""Entry point: `python -m app.worker` (the same image as the API, a different command)."""

import asyncio
import logging

from app.config import get_settings
from app.db import create_engine, create_sessionmaker
from app.worker.consumer import ClickConsumer, create_worker_redis

logger = logging.getLogger("app.worker")

# After an error (Redis or Postgres unreachable) wait a moment instead of spinning.
ERROR_PAUSE_SECONDS = 1.0


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    # The worker's database budget is 2 connections + 1 overflow (ARCHITECTURE §9.1, §9.2).
    engine = create_engine(settings, pool_size=2, max_overflow=1)
    redis = create_worker_redis(settings)
    consumer = ClickConsumer(redis, create_sessionmaker(engine), settings)
    logger.info("click worker %s starting", consumer.name)
    try:
        await consumer.ensure_group()
        while True:
            try:
                await consumer.run_once()
            except Exception:  # noqa: BLE001 - one bad batch must not kill the worker
                # The batch stays unacknowledged, so it is delivered again. (Per-event fallback
                # and poison handling are task P3.3.)
                logger.exception("batch failed; it will be retried")
                await asyncio.sleep(ERROR_PAUSE_SECONDS)
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
