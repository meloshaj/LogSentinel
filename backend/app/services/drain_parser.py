"""Drain3 parser wrapper for streaming log template mining."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import ulid

from drain3 import TemplateMiner
from drain3.file_persistence import FilePersistence
from drain3.template_miner_config import TemplateMinerConfig

from ..models import ParsedLog


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "drain3.ini"
DEFAULT_STATE_PATH = Path(__file__).resolve().parents[3] / "state" / "drain3_state.bin"


class DrainParser:
    """
    Small application-facing wrapper around Drain3's TemplateMiner.
    
    This class manages the configuration and state persistence of the Drain3
    log template mining algorithm.
    """

    def __init__(self, config_path: str | None = None, state_path: str | None = None) -> None:
        """
        Initialize the Drain3 parser with optional custom paths.
        
        Args:
            config_path: Optional path to the drain3.ini configuration file.
            state_path: Optional path for the binary state persistence file.
        """
        self.config_path: Path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self.state_path: Path = Path(state_path) if state_path else DEFAULT_STATE_PATH
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

        config = TemplateMinerConfig()
        config.load(str(self.config_path))
        config.parameter_extraction_cache_capacity = int(config.parameter_extraction_cache_capacity)

        persistence = FilePersistence(str(self.state_path))
        self._miner = TemplateMiner(persistence_handler=persistence, config=config)

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
