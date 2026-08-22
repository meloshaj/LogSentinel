"""Drain3 parser wrapper for streaming log template mining."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import redis
import ulid
from drain3 import TemplateMiner
from drain3.redis_persistence import RedisPersistence
from drain3.template_miner_config import TemplateMinerConfig

from ..models import ParsedLog

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "drain3.ini"
# ``backend/app`` is writable by the non-root image user (the backend image
# chowns the application tree before switching users).  Using this directory
# also keeps native development state inside the backend application tree
# instead of accidentally resolving to the filesystem root in a container.
DEFAULT_STATE_PATH = Path(__file__).resolve().parents[1] / "state" / "drain3_state.bin"
DEFAULT_REDIS_STATE_KEY = "logsentinel:drain3:state"

logger = logging.getLogger("logsentinel.drain_parser")


def get_drain3_state_path() -> Path:
    """Return the configured, deterministic Drain3 file-state path.

    A deployment may mount a persistent directory by setting
    ``DRAIN3_STATE_PATH``.  When it is unset, the application-local default is
    writable by the backend's non-root user and does not require a volume.
    """
    configured_path = os.getenv("DRAIN3_STATE_PATH")
    return Path(configured_path) if configured_path else DEFAULT_STATE_PATH


def get_drain3_state_backend() -> str:
    """Return the optional shared state backend, defaulting to local disk."""
    backend = os.getenv("DRAIN3_STATE_BACKEND", "file").strip().lower()
    if backend not in {"file", "redis"}:
        raise ValueError("DRAIN3_STATE_BACKEND must be 'file' or 'redis'")
    return backend


def get_drain3_redis_settings() -> dict[str, Any]:
    """Resolve Drain3's synchronous persistence client from the Redis URL.

    Drain3's built-in ``RedisPersistence`` is synchronous, so it cannot reuse
    the application's async pool directly. It must nevertheless target the
    same host, database, password, and TLS mode as ``REDIS_URL``; falling back
    to a separate ``REDIS_HOST`` default silently loses parser state.
    """
    raw_url = os.getenv("REDIS_URL")
    if raw_url:
        parsed = urlparse(raw_url)
        scheme = parsed.scheme.lower()
        if scheme not in {"redis", "rediss"} or not parsed.hostname:
            raise ValueError("REDIS_URL must use redis:// or rediss://")
        database = parsed.path.strip("/") or "0"
        return {
            "redis_host": parsed.hostname,
            "redis_port": parsed.port or 6379,
            "redis_db": int(database),
            "redis_pass": unquote(parsed.password) if parsed.password else None,
            "is_ssl": scheme == "rediss",
        }

    return {
        "redis_host": os.getenv("REDIS_HOST", "localhost"),
        "redis_port": int(os.getenv("REDIS_PORT", "6379")),
        "redis_db": int(os.getenv("REDIS_DB", "0")),
        "redis_pass": os.getenv("REDIS_PASSWORD"),
        "is_ssl": os.getenv("REDIS_SSL", "false").lower() in {"1", "true", "yes", "on"},
    }


def build_drain3_redis_persistence() -> RedisPersistence:
    """Build a bounded synchronous Drain3 Redis persistence handler."""
    settings = get_drain3_redis_settings()
    persistence = RedisPersistence(**settings, redis_key=DEFAULT_REDIS_STATE_KEY)
    raw_url = os.getenv("REDIS_URL")
    if raw_url:
        persistence.r = redis.Redis.from_url(
            raw_url,
            decode_responses=False,
            socket_timeout=1.0,
            socket_connect_timeout=1.0,
        )
    else:
        persistence.r = redis.Redis(
            host=settings["redis_host"],
            port=settings["redis_port"],
            db=settings["redis_db"],
            password=settings["redis_pass"],
            ssl=settings["is_ssl"],
            decode_responses=False,
            socket_timeout=1.0,
            socket_connect_timeout=1.0,
        )
    return persistence


class DrainParser:
    """
    Small application-facing wrapper around Drain3's TemplateMiner.
    
    This class manages the configuration and state persistence of the Drain3
    log template mining algorithm.
    """

    def __init__(self, config_path: str | None = None, state_path: str | None = None, persistence: Any | None = None) -> None:
        """
        Initialize the Drain3 parser with optional custom paths.
        
        Args:
            config_path: Optional path to the drain3.ini configuration file.
            state_path: Optional path for the binary state persistence file.
            persistence: Optional persistence handler (RedisPersistence or FilePersistence).
        """
        self.config_path: Path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self.state_path: Path = Path(state_path) if state_path else get_drain3_state_path()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

        config = TemplateMinerConfig()
        config.load(str(self.config_path))
        config.parameter_extraction_cache_capacity = int(config.parameter_extraction_cache_capacity)

        if persistence is not None:
            self._miner = TemplateMiner(persistence_handler=persistence, config=config)
            self.redis_client = None
        else:
            from drain3.file_persistence import FilePersistence

            file_pers = FilePersistence(str(self.state_path))
            if get_drain3_state_backend() != "redis":
                self._miner = TemplateMiner(persistence_handler=file_pers, config=config)
                self.redis_client = None
                return

            try:
                redis_pers = build_drain3_redis_persistence()
                self.redis_client = redis_pers.r
                self._miner = TemplateMiner(persistence_handler=redis_pers, config=config)
            except Exception as exc:
                logger.warning(
                    "Drain3 Redis state unavailable; using local state file %s: %s",
                    self.state_path,
                    exc,
                )
                self._miner = TemplateMiner(persistence_handler=file_pers, config=config)
                self.redis_client = None

    def parse(self, raw_message: str, metadata: dict[str, Any] | None = None) -> ParsedLog:
        """
        Mine a log template and return a validated ParsedLog instance.
        
        Args:
            raw_message: The raw log message string to be parsed.
            metadata: Optional dictionary containing log metadata (service, level, timestamp).
            
        Returns:
            ParsedLog: A structured log entry containing the matched template and extracted parameters.
        """
        result = self._miner.add_log_message(raw_message)
        template_text: str = result["template_mined"]
        parameters: list[dict[str, Any]] = self._extract_parameters(template_text, raw_message)
        
        metadata_dict: dict[str, Any] = metadata or {}
        
        # Extract timestamp from metadata or use current time
        timestamp: Any = metadata_dict.get("timestamp")
        if not isinstance(timestamp, datetime):
            if isinstance(timestamp, str):
                try:
                    timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    timestamp = datetime.now(timezone.utc)
            else:
                timestamp = datetime.now(timezone.utc)
        
        # Ensure timezone-aware
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        
        log_id = ulid.from_timestamp(timestamp).str
        
        return ParsedLog(
            id=log_id,
            timestamp=timestamp,
            service=metadata_dict.get("service", "unknown"),
            level=metadata_dict.get("level", "info"),
            raw_message=raw_message,
            template_id=str(result["cluster_id"]),
            template_text=template_text,
            parameters=parameters,
            cluster_size=result["cluster_size"],
            change_type=result["change_type"],
            source=metadata_dict.get("source"),
            environment=metadata_dict.get("environment"),
            correlation_id=metadata_dict.get("correlation_id"),
            metadata=metadata_dict,
            parsed_at=datetime.now(timezone.utc),
        )

    def get_stats(self) -> dict[str, Any]:
        """
        Return lightweight parser state useful for diagnostics.
        
        Returns:
            dict[str, Any]: Dictionary containing cluster count, total size, and paths.
        """
        clusters = list(self._miner.drain.clusters)
        return {
            "cluster_count": len(clusters),
            "total_cluster_size": self._miner.drain.get_total_cluster_size(),
            "state_path": str(self.state_path),
            "config_path": str(self.config_path),
        }

    def get_templates(self) -> list[dict[str, Any]]:
        """
        Return the currently mined templates.
        
        Returns:
            list[dict[str, Any]]: List of templates containing ID, text, and cluster size.
        """
        return [
            {
                "template_id": str(cluster.cluster_id),
                "template_text": cluster.get_template(),
                "cluster_size": cluster.size,
            }
            for cluster in self._miner.drain.clusters
        ]

    def _extract_parameters(self, template_text: str, raw_message: str) -> list[dict[str, Any]]:
        """
        Extract dynamic parameters from a raw message based on its template.
        
        Args:
            template_text: The matched template string with wildcard tokens.
            raw_message: The original raw log message.
            
        Returns:
            list[dict[str, Any]]: A list of extracted parameters containing values and mask names.
        """
        extracted = self._miner.extract_parameters(template_text, raw_message)
        if not extracted:
            return []

        return [
            {
                "value": parameter.value,
                "mask_name": parameter.mask_name,
            }
            for parameter in extracted
        ]
