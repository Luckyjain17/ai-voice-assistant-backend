import os
from typing import Any, Dict

import httpx

from app.env import load_backend_env

load_backend_env()

VAPI_BASE_URL = os.getenv("VAPI_BASE_URL") or "https://api.vapi.ai"
VAPI_API_KEY = os.getenv("VAPI_API_KEY")
VAPI_ASSISTANT_ID = os.getenv("VAPI_ASSISTANT_ID")
VAPI_PHONE_NUMBER_ID = os.getenv("VAPI_PHONE_NUMBER_ID")
VAPI_SERVER_URL = os.getenv("VAPI_SERVER_URL")
VAPI_SERVER_MESSAGES = [
    message.strip()
    for message in os.getenv("VAPI_SERVER_MESSAGES", "end-of-call-report,transcript").split(",")
    if message.strip()
]


def _call_endpoint(call_id: str | None = None) -> str:
    """Return the Vapi calls endpoint, accepting either base API URL style."""
    base_url = VAPI_BASE_URL.rstrip("/")
    if not base_url.endswith("/call"):
        base_url = f"{base_url}/call"
    return f"{base_url}/{call_id}" if call_id else base_url


def initiate_call(phone: str, name: str) -> Dict[str, Any]:
    """Initiate a phone call through VAPI AI.

    The payload and endpoint are configurable via environment variables.
    """
    if not VAPI_API_KEY:
        return {"ok": False, "error": "Missing VAPI_API_KEY in environment"}
    if not VAPI_ASSISTANT_ID:
        return {"ok": False, "error": "Missing VAPI_ASSISTANT_ID in environment"}
    if not VAPI_PHONE_NUMBER_ID:
        return {"ok": False, "error": "Missing VAPI_PHONE_NUMBER_ID in environment"}

    payload = {
        "name": name,
        "assistantId": VAPI_ASSISTANT_ID,
        "phoneNumberId": VAPI_PHONE_NUMBER_ID,
        "customer": {
            "number": phone,
            "name": name,
        },
    }

    if VAPI_SERVER_URL:
        payload["assistantOverrides"] = {
            "server": {
                "url": VAPI_SERVER_URL,
                "timeoutSeconds": 20,
            },
            "serverMessages": VAPI_SERVER_MESSAGES,
        }

    headers = {
        "Authorization": f"Bearer {VAPI_API_KEY}",
        "Content-Type": "application/json",
    }

    endpoint = _call_endpoint()

    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.post(endpoint, json=payload, headers=headers)
            response.raise_for_status()
            return {"ok": True, "body": response.json()}
    except httpx.HTTPStatusError as exc:
        body_text = exc.response.text if exc.response is not None else str(exc)
        return {"ok": False, "error": f"HTTP {exc.response.status_code}: {body_text}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def get_call_details(call_id: str) -> Dict[str, Any]:
    if not VAPI_API_KEY:
        return {"ok": False, "error": "Missing VAPI_API_KEY in environment"}

    headers = {
        "Authorization": f"Bearer {VAPI_API_KEY}",
        "Content-Type": "application/json",
    }
    endpoint = _call_endpoint(call_id)

    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.get(endpoint, headers=headers)
            response.raise_for_status()
            return {"ok": True, "body": response.json()}
    except httpx.HTTPStatusError as exc:
        body_text = exc.response.text if exc.response is not None else str(exc)
        return {"ok": False, "error": f"HTTP {exc.response.status_code}: {body_text}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def end_call(call_id: str) -> Dict[str, Any]:
    """End or cancel an active call through VAPI AI."""
    if not VAPI_API_KEY:
        return {"ok": False, "error": "Missing VAPI_API_KEY in environment"}

    headers = {
        "Authorization": f"Bearer {VAPI_API_KEY}",
        "Content-Type": "application/json",
    }
    endpoint = _call_endpoint(call_id)

    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.delete(endpoint, headers=headers)
            response.raise_for_status()
            return {"ok": True, "body": response.json()}
    except httpx.HTTPStatusError as exc:
        body_text = exc.response.text if exc.response is not None else str(exc)
        return {"ok": False, "error": f"HTTP {exc.response.status_code}: {body_text}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
