"""Entry point: `python -m app.worker` (the same image as the API, a different command)."""

import asyncio
import logging
import signal

from app.config import get_settings
from app.db import create_engine, create_sessionmaker
from app.worker.consumer import ClickConsumer, create_worker_redis

logger = logging.getLogger("app.worker")


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    # The worker's database budget is 2 connections + 1 overflow (ARCHITECTURE §9.1, §9.2).
    engine = create_engine(settings, pool_size=2, max_overflow=1)
    redis = create_worker_redis(settings)
    consumer = ClickConsumer(redis, create_sessionmaker(engine), settings)

    # SIGTERM is what Kubernetes sends when it stops a pod. We only SET A FLAG: the batch in
    # progress finishes and is acknowledged, then the loop ends and the process exits with 0.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    logger.info("click worker %s starting", consumer.name)
    try:
        await consumer.run_forever(stop)
    finally:
        logger.info("click worker %s stopping", consumer.name)
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
