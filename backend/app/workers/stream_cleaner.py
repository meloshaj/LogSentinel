import asyncio
import logging

from redis.asyncio import Redis

from ..core.constants import LOG_WORKERS_GROUP

logger = logging.getLogger("logsentinel.workers.stream_cleaner")

class StreamCleanerWorker:
    """Background worker that reclaims orphaned stream messages and trims the PEL."""
    
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
        
        if not self.redis_client:
            logger.warning("StreamCleanerWorker started without Redis client")
            return
            
        self._task = asyncio.create_task(self._run_loop(), name="stream-cleaner")
        logger.info("StreamCleanerWorker started")

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
        if not self.redis_client:
            return
            
        start_id = "0-0"
        total_claimed = 0
        
        while True:
            try:
                # XAUTOCLAIM key group consumer min-idle-time start [COUNT count] [JUSTID]
                result = await self.redis_client.xautoclaim(
                    name=self.stream_name,
                    groupname=self.group_name,
                    consumername=self.consumer_name,
                    min_idle_time=self.min_idle_time_ms,
                    start_id=start_id,
                    count=self.batch_size,
                    justid=True
                )
                
                # Redis-py 5.0.0+ returns a tuple: (next_start_id, [message_id, ...])
                if isinstance(result, tuple) and len(result) >= 2:
                    next_start_id, claimed_ids = result[0], result[1]
                else:
                    logger.warning("Unexpected XAUTOCLAIM response format: %s", result)
                    break
                    
                if not claimed_ids:
                    break
                    
                # We simply acknowledge these dead messages to clear them from the PEL
                await self.redis_client.xack(self.stream_name, self.group_name, *claimed_ids)
                total_claimed += len(claimed_ids)
                
                # If next_start_id is "0-0", there are no more pending messages to check
                if next_start_id == b"0-0" or next_start_id == "0-0":
                    break
                    
                start_id = next_start_id
                
            except Exception:
                logger.exception("Failed to run XAUTOCLAIM")
                break
                
        if total_claimed > 0:
            logger.info("Cleaned %d orphaned messages from %s PEL", total_claimed, self.stream_name)
