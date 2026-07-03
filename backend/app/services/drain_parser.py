"""Drain3 parser wrapper for streaming log template mining."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from drain3 import TemplateMiner
from drain3.file_persistence import FilePersistence
from drain3.template_miner_config import TemplateMinerConfig


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "drain3.ini"
DEFAULT_STATE_PATH = Path(__file__).resolve().parents[3] / "state" / "drain3_state.bin"


class DrainParser:
    """Small application-facing wrapper around Drain3's TemplateMiner."""

    def __init__(self, config_path: str | None = None, state_path: str | None = None) -> None:
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self.state_path = Path(state_path) if state_path else DEFAULT_STATE_PATH
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

        config = TemplateMinerConfig()
        config.load(str(self.config_path))
        config.parameter_extraction_cache_capacity = int(config.parameter_extraction_cache_capacity)

        persistence = FilePersistence(str(self.state_path))
        self._miner = TemplateMiner(persistence_handler=persistence, config=config)

    def parse(self, raw_message: str, metadata: dict | None = None) -> dict:
        """Mine a log template and return the normalized parser result."""
        result = self._miner.add_log_message(raw_message)
        template_text = result["template_mined"]

        parameters = self._extract_parameters(template_text, raw_message)

        return {
            "raw_message": raw_message,
            "template_id": str(result["cluster_id"]),
            "template_text": template_text,
            "cluster_size": result["cluster_size"],
            "change_type": result["change_type"],
            "parameters": parameters,
            "metadata": metadata or {},
            "parsed_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_stats(self) -> dict:
        """Return lightweight parser state useful for diagnostics."""
        clusters = list(self._miner.drain.clusters)
        return {
            "cluster_count": len(clusters),
            "total_cluster_size": self._miner.drain.get_total_cluster_size(),
            "state_path": str(self.state_path),
            "config_path": str(self.config_path),
        }

    def get_templates(self) -> list[dict]:
        """Return the currently mined templates."""
        return [
            {
                "template_id": str(cluster.cluster_id),
                "template_text": cluster.get_template(),
                "cluster_size": cluster.size,
            }
            for cluster in self._miner.drain.clusters
        ]

    def _extract_parameters(self, template_text: str, raw_message: str) -> list[dict[str, Any]]:
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
