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
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

from ..config.models import ClientMeta
from .alerts import AlertSink, default_alert_factory
from .lead import AgentRegistry, parse_lead
from .signature import DEFAULT_TOLERANCE_MS, check_signature
from .sms import DEFAULT_LEDGER_PATH, SmsLedger, SmsSink, default_sms_sink, maybe_send_booking_sms

logger = logging.getLogger(__name__)

AlertFactory = Callable[[ClientMeta], AlertSink]


def create_app(
    *,
    webhook_key: str | None = None,
    registry: AgentRegistry | None = None,
    alert_factory: AlertFactory | None = None,
    sms_sink: SmsSink | None = None,
    sms_ledger: SmsLedger | None = None,
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
    sms = sms_sink if sms_sink is not None else default_sms_sink()
    ledger = sms_ledger if sms_ledger is not None else SmsLedger(DEFAULT_LEDGER_PATH)
    logger.info("webhook service starting; %d managed agent(s) registered", len(reg))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            # TwilioSms owns an httpx.Client; release it however the app exits —
            # a crash or cancellation during serving must not leak the connection
            # pool. Sinks without a close() (DebugSms, doubles) need no teardown.
            close = getattr(sms, "close", None)
            if callable(close):
                close()

    app = FastAPI(title="dma-deploy-kit post-call webhook", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "managed_agents": len(reg)}

    @app.post("/webhook/retell")
    async def retell_webhook(request: Request) -> dict:
        raw = await request.body()
        signature = request.headers.get("x-retell-signature")
        check = check_signature(raw, signature, key, tolerance_ms=tolerance_ms)
        if not check.valid:
            logger.warning(
                "webhook signature rejected: header_present=%s parsed_timestamp=%s "
                "skew_ms=%s digest_match=%s",
                check.header_present,
                check.parsed_timestamp,
                check.skew_ms,
                check.digest_match,
            )
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

        # Best-effort booking SMS (fully consent-gated + send-once); never let an
        # SMS problem fail the webhook acknowledgement.
        try:
            result = maybe_send_booking_sms(binding.config, lead, sms, ledger)
            if result is not None:
                logger.info("booking SMS %s for call %s", result.status, lead.call_id)
        except Exception:
            logger.exception("booking SMS failed for call %s", lead.call_id)

        return {"status": "processed", "slug": lead.slug, "call_id": lead.call_id}

    return app


def get_app() -> FastAPI:
    """ASGI factory entry point for uvicorn (--factory). Loads .env for the key."""
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    return create_app()
