import asyncio
import logging
import time

import httpx
from redis.asyncio import Redis

from ..config import get_webhook_settings
from ..schemas.alerting import IncidentAlertPayload

logger = logging.getLogger("logsentinel.alerting")

# Configuration
WINDOW_SECONDS = 15.0


def _get_fallback_redis() -> Redis | None:
    """Return a Redis client from the global pool when no client was injected.

    This is used exclusively by the graph scorer path which dispatches alerts
    from a synchronous context and cannot easily inject a client.
    """
    from ..core.redis import (
        _redis_pool,  # noqa: WPS433 — late import to avoid circular deps
    )

    if _redis_pool is not None:
        return Redis(connection_pool=_redis_pool)
    return None


async def dispatch_incident_alert(
    incident_data: IncidentAlertPayload,
    *,
    redis_client: Redis | None = None,
) -> None:
    """
    Non-blocking, intelligent alert dispatcher with Valkey-backed sliding window deduplication.

    Buffers events related to the same root_cause_service for a configurable window.
    Only the first event in the window triggers the background timer task. After the
    window elapses, all buffered events are coalesced into a single consolidated
    notification sent to configured webhooks.

    Args:
        incident_data: The incident payload to buffer and eventually dispatch.
        redis_client: A shared Redis/Valkey client.  When ``None`` the function
            falls back to the global connection pool (for callers that cannot
            inject a client, e.g. the graph scorer).
    """
    try:
        client = redis_client or _get_fallback_redis()
        if client is None:
            logger.error("No Redis client available for alerting — alert dropped")
            return

        # Use root_cause_service as the aggregation key
        tracking_id = incident_data.root_cause_service
        buffer_key = f"alert_buffer:{tracking_id}"
        lock_key = f"alert_lock:{tracking_id}"

        # Add the incident payload to the sorted set with current time as score
        current_time = time.time()
        payload_json = incident_data.model_dump_json()

        await client.zadd(buffer_key, {payload_json: current_time})

        # Attempt to acquire a lock to trigger the dispatch task
        # nx=True means set only if it does not exist (atomic lock)
        # ex=WINDOW_SECONDS + a small buffer to ensure the lock holds during sleep
        lock_acquired = await client.set(
            lock_key, "1", nx=True, ex=int(WINDOW_SECONDS + 5)
        )

        if lock_acquired:
            # We are the first anomaly in this window, spawn the background task
            logger.info(
                "Acquired lock for %s, spawning coalescing task for %s seconds",
                tracking_id,
                WINDOW_SECONDS,
            )
            asyncio.create_task(
                _coalesce_and_dispatch(tracking_id, buffer_key, lock_key, client)
            )
        else:
            logger.debug("Alert for %s buffered (lock already held)", tracking_id)

    except Exception as e:
        logger.exception("Failed to buffer incident alert: %s", str(e))


async def _coalesce_and_dispatch(
    tracking_id: str,
    buffer_key: str,
    lock_key: str,
    redis_client: Redis,
) -> None:
    """Wait for the sliding window, aggregate buffered anomalies, and fire webhooks."""
    try:
        # Sleep for the deduplication window
        await asyncio.sleep(WINDOW_SECONDS)

        # Retrieve all buffered payloads from the sorted set
        raw_items = await redis_client.zrange(buffer_key, 0, -1)

        if not raw_items:
            logger.warning(
                "No anomalies found in buffer %s after window elapsed", buffer_key
            )
            return

        # Parse items
        payloads = [
            IncidentAlertPayload.model_validate_json(item)
            for item in raw_items  # type: ignore
        ]

        # Coalesce the payloads
        consolidated = _coalesce_payloads(tracking_id, payloads)

        # Fire webhooks
        await _fire_webhooks(consolidated)

    except Exception as e:
        logger.exception(
            "Error during alert coalescing and dispatch for %s: %s", tracking_id, str(e)
        )
    finally:
        try:
            # Clear the buffer
            await redis_client.delete(buffer_key)
            # The lock will expire on its own, but we can proactively delete it if desired.
            # In this case, we let it expire or delete it based on exact windowing semantics.
            # To strictly follow a sliding window, we delete the lock so the next anomaly starts a new window.
            await redis_client.delete(lock_key)
        except Exception:
            logger.warning("Failed to clean up Valkey keys for %s", tracking_id)


def _coalesce_payloads(
    tracking_id: str, payloads: list[IncidentAlertPayload]
) -> IncidentAlertPayload:
    """Combine multiple anomalies into a single incident payload."""
    # Find the max confidence
    best_payload = max(payloads, key=lambda p: p.confidence_score)

    # Union of all affected services across payloads
    all_affected = set()
    for p in payloads:
        all_affected.update(p.affected_services)

    return IncidentAlertPayload(
        incident_id=best_payload.incident_id,
        root_cause_service=tracking_id,
        triggering_template=best_payload.triggering_template,
        affected_services=sorted(list(all_affected)),
        propagation_chain=best_payload.propagation_chain,
        confidence_score=best_payload.confidence_score,
        is_critical=any(p.is_critical for p in payloads),
    )


async def _fire_webhooks(payload: IncidentAlertPayload) -> None:
    """Send notifications to all configured channels."""
    settings = get_webhook_settings()

    # We use a single httpx AsyncClient for the dispatches
    async with httpx.AsyncClient(timeout=10.0) as client:
        tasks = []
        if settings.discord_webhook_url:
            tasks.append(_send_discord(client, settings.discord_webhook_url, payload))

        if settings.slack_webhook_url:
            tasks.append(_send_slack(client, settings.slack_webhook_url, payload))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.error("Failed to fire webhook: %s", str(result))


async def _send_discord(
    client: httpx.AsyncClient, url: str, payload: IncidentAlertPayload
) -> None:
    """Format and send a Discord Rich Embed."""

    # Crimson Red for critical, Amber for degraded
    color = 0xEF4444 if payload.is_critical else 0xF59E0B
    severity = "CRITICAL" if payload.is_critical else "DEGRADED"
    title = f"🚨 [{severity}] Cascading Failure: {payload.root_cause_service}"

    # Calculate propagation chain string
    chain_str = (
        " ➔ ".join(payload.propagation_chain)
        if payload.propagation_chain
        else "Unknown"
    )

    # Direct deep-link URL
    incident_url = f"http://localhost:5173/incidents/{payload.incident_id}"

    confidence_pct = f"{payload.confidence_score * 100:.1f}%"

    embed = {
        "title": title,
        "color": color,
        "fields": [
            {
                "name": "Suspected Root Cause",
                "value": f"{payload.root_cause_service}\n`{payload.triggering_template or 'No template available'}`",
                "inline": False,
            },
            {
                "name": "Blast Radius",
                "value": f"{len(payload.affected_services)} downstream services affected\n**Chain:** {chain_str}",
                "inline": False,
            },
            {"name": "Confidence Score", "value": confidence_pct, "inline": True},
        ],
    }

    data = {
        "embeds": [embed],
        "components": [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 5,
                        "label": "Inspect Active Anomaly",
                        "url": incident_url,
                    }
                ],
            }
        ],
    }

    resp = await client.post(url, json=data)
    resp.raise_for_status()


async def _send_slack(
    client: httpx.AsyncClient, url: str, payload: IncidentAlertPayload
) -> None:
    """Format and send a Slack Block Kit message."""

    severity = "CRITICAL" if payload.is_critical else "DEGRADED"
    title = f"🚨 *[{severity}] Cascading Failure: {payload.root_cause_service}*"

    chain_str = (
        " ➔ ".join(payload.propagation_chain)
        if payload.propagation_chain
        else "Unknown"
    )
    incident_url = f"http://localhost:5173/incidents/{payload.incident_id}"
    confidence_pct = f"{payload.confidence_score * 100:.1f}%"

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Cascading Failure: {payload.root_cause_service}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{title}\n\n*Suspected Root Cause:*\n`{payload.triggering_template or 'N/A'}`",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Confidence Score:*\n{confidence_pct}"},
                {
                    "type": "mrkdwn",
                    "text": f"*Affected Downstream:*\n{len(payload.affected_services)} services",
                },
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Propagation Chain:*\n{chain_str}"},
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Investigate in LogSentinel",
                        "emoji": True,
                    },
                    "url": incident_url,
                    "action_id": "investigate_incident",
                }
            ],
        },
    ]

    data = {"blocks": blocks}

    resp = await client.post(url, json=data)
    resp.raise_for_status()
