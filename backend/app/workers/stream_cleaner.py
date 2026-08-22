import asyncio
import logging

from redis.asyncio import Redis

from ..core.constants import LOG_WORKERS_GROUP

logger = logging.getLogger("logsentinel.workers.stream_cleaner")

class StreamCleanerWorker:
    """Compatibility lifecycle for the retired destructive PEL cleaner.

    ``DrainWorker.recover_pending_messages`` is the single owner of claiming
    and processing stale deliveries.  A second worker must not claim and ACK
    the same entries because that discards legitimate work.  The class remains
    available so existing startup wiring and tests retain their public shape,
    but it intentionally performs no recovery operation.
    """
    
    def __init__(
        self,
        stream_name: str = "logs:stream",
        group_name: str = LOG_WORKERS_GROUP,
        consumer_name: str = "orphan_cleaner",
        check_interval_seconds: float = 60.0,
        min_idle_time_ms: int = 120_000,
        batch_size: int = 100
    ):
        self.stream_name = stream_name
        self.group_name = group_name
        self.consumer_name = consumer_name
        self.check_interval_seconds = check_interval_seconds
        self.min_idle_time_ms = min_idle_time_ms
        self.batch_size = batch_size
        
        self.redis_client: Redis | None = None
        self._task: asyncio.Task[None] | None = None

    def set_redis_client(self, redis_client: Redis) -> None:
        self.redis_client = redis_client

    def start(self) -> None:
        if self._task and not self._task.done():
            return

        logger.info(
            "StreamCleanerWorker is passive; DrainWorker owns stale pending-entry recovery"
        )

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            finally:
                self._task = None
        logger.info("StreamCleanerWorker stopped")

    async def _run_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.check_interval_seconds)
                await self._clean_orphans()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Unexpected error in StreamCleanerWorker loop")
                
    async def _clean_orphans(self) -> None:
        # Deliberately do not call XAUTOCLAIM/XACK here.  Recovery and terminal
        # handling are performed by DrainWorker._process_stream_message.
        logger.debug(
            "Skipping passive stream-cleaner sweep for %s/%s; DrainWorker is the recovery owner",
            self.stream_name,
            self.group_name,
        )
