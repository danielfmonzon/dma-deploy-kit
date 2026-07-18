"""FastAPI post-call webhook service for Retell call_analyzed events.

Fail-closed by design:
  * No RETELL_WEBHOOK_KEY at startup -> the app refuses to build (RuntimeError).
  * Absent/invalid signature -> 401, logged, no processing.
  * Non call_analyzed events -> acknowledged and ignored.
  * call_analyzed for an agent we don't manage -> logged warning, acknowledged,
    no alert.

Run with:  uvicorn dma_deploy_kit.postcall.service:get_app --factory
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable

from fastapi import FastAPI, HTTPException, Request

from ..config.models import ClientMeta
from .alerts import AlertSink, default_alert_factory
from .lead import AgentRegistry, parse_lead
from .signature import DEFAULT_TOLERANCE_MS, verify_signature

logger = logging.getLogger(__name__)

AlertFactory = Callable[[ClientMeta], AlertSink]


def create_app(
    *,
    webhook_key: str | None = None,
    registry: AgentRegistry | None = None,
    alert_factory: AlertFactory | None = None,
    tolerance_ms: int = DEFAULT_TOLERANCE_MS,
) -> FastAPI:
    """Build the webhook app. Raises RuntimeError if no webhook key is available."""
    raw_key = webhook_key if webhook_key is not None else os.environ.get("RETELL_WEBHOOK_KEY", "")
    key = raw_key.strip()
    if not key:
        raise RuntimeError(
            "RETELL_WEBHOOK_KEY is missing — refusing to start the webhook service "
            "(fail closed). Set it in .env."
        )

    reg = registry if registry is not None else AgentRegistry.from_clients_dir()
    make_alert = alert_factory or default_alert_factory
    logger.info("webhook service starting; %d managed agent(s) registered", len(reg))

    app = FastAPI(title="dma-deploy-kit post-call webhook")

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "managed_agents": len(reg)}

    @app.post("/webhook/retell")
    async def retell_webhook(request: Request) -> dict:
        raw = await request.body()
        signature = request.headers.get("x-retell-signature")
        if not verify_signature(raw, signature, key, tolerance_ms=tolerance_ms):
            logger.warning("rejected webhook: absent or invalid X-Retell-Signature")
            raise HTTPException(status_code=401, detail="invalid signature")

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("rejected webhook: body is not valid JSON")
            raise HTTPException(status_code=400, detail="invalid JSON") from None

        event = payload.get("event")
        if event != "call_analyzed":
            logger.info("acknowledged non-processed event: %s", event)
            return {"status": "ignored", "event": event}

        call = payload.get("call") or {}
        agent_id = call.get("agent_id")
        binding = reg.resolve(agent_id)
        if binding is None:
            logger.warning("call_analyzed for unmanaged agent_id %s — acknowledged", agent_id)
            return {"status": "unmanaged", "agent_id": agent_id}

        lead = parse_lead(payload, binding)
        make_alert(binding.config.client).send(lead)
        logger.info("processed lead for %s (call %s)", lead.business_name, lead.call_id)
        return {"status": "processed", "slug": lead.slug, "call_id": lead.call_id}

    return app


def get_app() -> FastAPI:
    """ASGI factory entry point for uvicorn (--factory)."""
    return create_app()
