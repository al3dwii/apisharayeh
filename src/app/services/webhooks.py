import hmac, hashlib, json, uuid
from typing import Any, Dict
from urllib.parse import urlparse

from app.core.config import settings
from app.services.db import tenant_session
from app.models.webhook_delivery import WebhookDelivery

def _is_http_url(url: str) -> bool:
    try:
        p = urlparse(url or "")
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False

def sign_payload(payload: Dict[str, Any]) -> str:
    secret = getattr(settings, "WEBHOOK_HMAC_SECRET", "change-me")
    try:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    except TypeError as e:
        # Make it serializable (fallback to str), but keep a hint
        body = json.dumps(str(payload), ensure_ascii=False).encode()
    mac = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return "sha256=" + mac

async def enqueue_delivery(
    tenant_id: str,
    job_id: str,
    url: str,
    event_type: str,
    payload: Dict[str, Any],
) -> str:
    if not _is_http_url(url):
        raise ValueError("Invalid webhook URL; only http(s) is allowed")

    # Ensure payload is JSON-serializable (will raise if not)
    try:
        json.dumps(payload, ensure_ascii=False)
    except TypeError as e:
        raise ValueError(f"Payload must be JSON-serializable: {e}")

    delivery_id = str(uuid.uuid4())
    async with tenant_session(tenant_id) as session:
        d = WebhookDelivery(
            id=delivery_id,
            tenant_id=tenant_id,
            job_id=job_id,
            url=url,
            event_type=event_type,
            payload_json=payload,
            status="pending",
            attempts=0,
            last_error=None,
        )
        session.add(d)
        await session.commit()

    # Schedule Celery task (requires (tenant_id, delivery_id))
    from app.workers.celery_app import deliver_webhook
    deliver_webhook.delay(tenant_id, delivery_id)
    return delivery_id
