"""
WebSocket endpoint — live claim status relay via Redis Pub/Sub.

WS /ws/claims/{claim_id}

Architecture:
  Celery worker publishes stage events → Redis Pub/Sub channel "claim:{claim_id}"
  FastAPI WS handler subscribes → forwards to connected browser client

This decouples the API process from the worker process.
Multiple FastAPI replicas can run behind a load balancer; each subscribes
to the same Redis channel and forwards to whichever client is connected to it.
"""
from __future__ import annotations

import asyncio
import json
import logging

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.config import get_settings

log = logging.getLogger(__name__)
router = APIRouter(tags=["websocket"])


@router.websocket("/ws/claims/{claim_id}")
async def claim_status_ws(websocket: WebSocket, claim_id: str):
    """
    WebSocket endpoint for live claim status.

    Events pushed by server:
      {"stage": "DOC_CLASSIFICATION", "status": "IN_PROGRESS", "ts": "..."}
      {"stage": "EXTRACTION", "status": "DEGRADED", "detail": "...", "ts": "..."}
      {"stage": "COMPLETE", "decision": "APPROVED", "approved_amount": 1350, "ts": "..."}
    """
    await websocket.accept()
    settings = get_settings()

    # Check if result is already in Redis (claim may have completed before WS connected)
    try:
        kwargs = {"ssl_cert_reqs": "none"} if settings.redis_url.startswith("rediss://") else {}
        r_sync = __import__("redis").from_url(settings.redis_url, **kwargs)
        cached = r_sync.get(f"claim_result:{claim_id}")
        if cached:
            data = json.loads(cached)
            await websocket.send_json({
                "stage": "COMPLETE",
                "status": data.get("status", "COMPLETE"),
                "decision": data.get("decision", {}).get("decision") if data.get("decision") else None,
                "trace_url": f"/claims/{claim_id}/trace",
                "ts": data.get("trace", {}).get("completed_at", ""),
            })
            await websocket.close()
            return
    except Exception:
        pass

    # Subscribe to Redis Pub/Sub
    try:
        kwargs = {"ssl_cert_reqs": "none"} if settings.redis_url.startswith("rediss://") else {}
        redis_client = aioredis.from_url(settings.redis_url, **kwargs)
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(f"claim:{claim_id}")

        try:
            # Keep alive for up to 5 minutes waiting for pipeline completion
            async def receive_messages():
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        try:
                            event = json.loads(message["data"])
                            await websocket.send_json(event)

                            # Close after COMPLETE stage
                            if event.get("stage") == "COMPLETE":
                                break
                        except Exception as e:
                            log.warning(f"WS send failed for {claim_id}: {e}")
                            break

            await asyncio.wait_for(receive_messages(), timeout=300)

        except WebSocketDisconnect:
            log.info(f"WebSocket disconnected for claim {claim_id}")
        except asyncio.TimeoutError:
            log.warning(f"WebSocket timeout for claim {claim_id} after 5 minutes")
        finally:
            await pubsub.unsubscribe(f"claim:{claim_id}")
            await redis_client.aclose()

    except Exception as e:
        log.error(f"WebSocket error for claim {claim_id}: {e}")
        try:
            await websocket.send_json({"stage": "ERROR", "detail": str(e)})
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass

