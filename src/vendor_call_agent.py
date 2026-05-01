from __future__ import annotations

import json
import logging
import os
import re
import socket
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

_logger = logging.getLogger("sch_intake.vendor_call")


FIELD_PROMPTS = {
    "Dimensions": "the full width, height, and depth",
    "Brand": "the product brand",
    "Product Name": "the exact product name",
    "Quantity": "the order quantity",
    "Supplier": "the vendor or supplier name",
    "Location": "the intended room or location",
    "Category": "the product category",
}

FUTURE_CALL_PROVIDERS = ["Retell", "Vapi", "Bland", "Twilio/OpenAI Realtime"]
CALL_RECORD_DIR = Path("data/vendor_calls")
BLAND_CALL_URL = "https://us.api.bland.ai/v1/calls"
BLAND_CALL_DETAIL_URL = "https://us.api.bland.ai/v1/calls/{call_id}"
BLAND_ACCOUNT_URL = "https://us.api.bland.ai/v1/me"
BLAND_PERSONAS_URL = "https://api.bland.ai/v1/personas"
BLAND_PERSONAS_FALLBACK_URL = "https://us.api.bland.ai/v1/personas"
BLAND_PERSONA_DETAIL_URLS = [
    "https://api.bland.ai/v1/personas/{persona_id}",
    "https://us.api.bland.ai/v1/personas/{persona_id}",
]
RETELL_CREATE_PHONE_CALL_URL = "https://api.retellai.com/v2/create-phone-call"
RETELL_GET_CALL_URL = "https://api.retellai.com/v2/get-call/{call_id}"
EMERGENCY_NUMBERS = {"911", "112", "999", "000"}

_NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fourty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}


def _text(row: dict[str, Any], key: str) -> str:
    return str(row.get(key) or "").strip()


def _product_reference(row: dict[str, Any]) -> str:
    parts = []
    if _text(row, "Model/SKU"):
        parts.append(f"model {_text(row, 'Model/SKU')}")
    if _text(row, "Brand"):
        parts.append(f"by {_text(row, 'Brand')}")
    if _text(row, "Product Name"):
        parts.append(f"called {_text(row, 'Product Name')}")
    return " ".join(parts) or "this product"


def build_call_goal(row: dict[str, Any], missing_fields: list[str]) -> str:
    field_text = ", ".join(missing_fields) if missing_fields else "details"
    product_name = _text(row, "Product Name") or "this product"
    model_sku = _text(row, "Model/SKU") or "the model number on file"
    return (
        f"Call Saffron Case Homes' vendor contact and confirm {field_text} for {product_name}. "
        f"Start by greeting them naturally, ask whether they can search by reference or model number, "
        f"then provide {model_sku} when they ask for it."
    )


def build_call_script(
    row: dict[str, Any],
    missing_fields: list[str],
    phone_number: str,
    custom_goal: str = "",
) -> str:
    goal = custom_goal.strip() or build_call_goal(row, missing_fields)
    fields = ", ".join(missing_fields) or "the missing values"
    phone_line = f" Phone number to call: {phone_number.strip()}." if phone_number.strip() else ""
    product_name = _text(row, "Product Name") or "the product"
    model_sku = _text(row, "Model/SKU") or "the model number on file"
    dimension_note = (
        " For dimensions, ask for the full width, height, and depth and confirm the units."
        if any(field.lower() == "dimensions" for field in missing_fields)
        else ""
    )
    return (
        "Hello, how are you? "
        f"I'm calling on behalf of Saffron Case Homes. We're trying to confirm product information for {product_name}. "
        "Would you be able to search that item by reference number or model number? "
        f"If they say yes or ask for the number, provide: The reference/model number is {model_sku}. "
        f"If they ask what information is needed, say: We're trying to confirm {fields}.{dimension_note} "
        "If they need time, wait patiently and respond naturally. "
        "When they provide the information, repeat it back clearly, confirm units if dimensions are involved, and thank them. "
        "If they cannot help, ask: Is there a product support email or department that would be best for this? "
        "Do not mention AI unless asked directly. "
        f"Call goal: {goal}{phone_line}"
    )


def calls_enabled() -> bool:
    return os.getenv("VENDOR_CALLS_ENABLED", "false").strip().lower() == "true"


def get_call_provider() -> str:
    return (
        os.getenv("VENDOR_CALL_PROVIDER", "").strip().lower()
        or os.getenv("BLAND_PROVIDER", "bland").strip().lower()
        or "bland"
    )


def _preferred_provider(provider: str | None = None) -> str:
    return (provider or get_call_provider()).strip().lower()


def vendor_call_mock_enabled() -> bool:
    return os.getenv("VENDOR_CALL_MOCK_MODE", "false").strip().lower() == "true"


def _normalise_phone_digits(phone_number: str) -> str:
    return re.sub(r"\D", "", phone_number or "")


def _validate_phone_number(phone_number: str) -> tuple[bool, str]:
    phone = phone_number.strip()
    digits = _normalise_phone_digits(phone)
    if not phone:
        return False, "Phone number is required."
    if digits in EMERGENCY_NUMBERS:
        return False, "Emergency numbers cannot be called."
    if not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
        return False, "Enter a phone number in E.164 format, for example +12223334444."
    return True, ""


def _is_self_test_number(phone_number: str) -> bool:
    configured = os.getenv("VENDOR_CALL_TEST_PHONE", "").strip()
    return bool(configured and configured == phone_number.strip())


def _agent_name() -> str:
    return os.getenv("BLAND_AGENT_NAME", "Alley").strip() or "Alley"


def _bland_from_number() -> str:
    return os.getenv("BLAND_PHONE_NUMBER", "").strip()


def _use_caller_number() -> bool:
    return os.getenv("BLAND_USE_CALLER_NUMBER", "true").strip().lower() != "false"


def _use_voice_override() -> bool:
    return os.getenv("BLAND_USE_VOICE_OVERRIDE", "false").strip().lower() == "true"


def _use_pathway() -> bool:
    return os.getenv("BLAND_USE_PATHWAY", "false").strip().lower() == "true"


def _voice_id() -> str:
    return (
        os.getenv("BLAND_VOICE_ID", "").strip()
        or os.getenv("BLAND_VOICE", "").strip()
    )


def _provider_config() -> dict[str, Any]:
    persona_id = os.getenv("BLAND_PERSONA_ID", "").strip()
    voice = _voice_id()
    pathway_id = os.getenv("BLAND_PATHWAY_ID", "").strip() if _use_pathway() else ""
    from_number = _bland_from_number()
    caller_number_sent = bool(from_number and _use_caller_number())
    voice_sent = bool(voice and _use_voice_override() and not pathway_id)
    return {
        "agent_name": _agent_name(),
        "from": from_number,
        "caller_number_enabled": _use_caller_number(),
        "caller_number_sent": caller_number_sent,
        "persona_id": persona_id,
        "voice": voice,
        "voice_id": voice,
        "voice_override_enabled": _use_voice_override(),
        "voice_sent": voice_sent,
        "pathway_id": pathway_id,
        "pathway_enabled": _use_pathway(),
        "from_field": "from",
        "persona_field": "persona_id",
        "voice_field": "voice",
        "pathway_field": "pathway_id",
        "supports_agent_name_field": False,
        "requested_agent_name": _agent_name(),
        "resolved_persona_id": persona_id,
        "resolved_voice": voice,
        "voice_source": "env_voice_id" if os.getenv("BLAND_VOICE_ID", "").strip() else ("env" if voice else ""),
        "persona_lookup_status": "not_attempted",
        "default_bland_agent_used": not any([persona_id, voice_sent, pathway_id]),
        "notes": (
            "Bland's send-call endpoint does not document an agent_name field. "
            "This integration tries direct voice override first when BLAND_USE_VOICE_OVERRIDE=true, "
            "then voice + persona_id, then the known working default payload. "
            "Do not send task when BLAND_USE_PATHWAY=true and pathway_id is configured."
        ),
    }


def _persona_version(persona: dict[str, Any]) -> dict[str, Any]:
    version = persona.get("current_production_version")
    return version if isinstance(version, dict) else {}


def _persona_call_config(persona: dict[str, Any]) -> dict[str, Any]:
    call_config = _persona_version(persona).get("call_config")
    return call_config if isinstance(call_config, dict) else {}


def _version_call_config(version: dict[str, Any]) -> dict[str, Any]:
    call_config = version.get("call_config")
    return call_config if isinstance(call_config, dict) else {}


def _persona_publication_status(persona: dict[str, Any]) -> dict[str, Any]:
    production = persona.get("current_production_version")
    draft = persona.get("current_draft_version")
    production = production if isinstance(production, dict) else {}
    draft = draft if isinstance(draft, dict) else {}
    production_id = str(persona.get("current_production_version_id") or production.get("id") or "").strip()
    draft_id = str(persona.get("current_draft_version_id") or draft.get("id") or "").strip()
    draft_promoted_at = draft.get("promoted_at")
    pending_draft = bool(draft_id and production_id and draft_id != production_id and not draft_promoted_at)
    return {
        "status": "draft_changes_pending" if pending_draft else "production_current",
        "current_production_version_id": production_id,
        "current_draft_version_id": draft_id,
        "production_version_type": production.get("version_type"),
        "draft_version_type": draft.get("version_type"),
        "production_voice": _version_call_config(production).get("voice"),
        "draft_voice": _version_call_config(draft).get("voice"),
        "draft_changes_pending": pending_draft,
    }


def _persona_summary(persona: dict[str, Any]) -> dict[str, Any]:
    call_config = _persona_call_config(persona)
    publication_status = _persona_publication_status(persona)
    return {
        "id": persona.get("id"),
        "name": persona.get("name"),
        "role": persona.get("role"),
        "description": persona.get("description"),
        "voice": call_config.get("voice"),
        "call_config": make_json_safe(call_config),
        "current_production_version_id": persona.get("current_production_version_id"),
        "current_draft_version_id": persona.get("current_draft_version_id"),
        "publication_status": publication_status["status"],
        "draft_changes_pending": publication_status["draft_changes_pending"],
        "production_voice": publication_status["production_voice"],
        "draft_voice": publication_status["draft_voice"],
        "current_production_version": make_json_safe(_persona_version(persona)),
    }


def _find_persona_by_name(personas: list[dict[str, Any]], agent_name: str) -> dict[str, Any] | None:
    normalized = agent_name.strip().lower()
    if not normalized:
        return None
    return next(
        (
            persona for persona in personas
            if str(persona.get("name", "")).strip().lower() == normalized
        ),
        None,
    )


def make_json_safe(obj: Any, seen: set[int] | None = None) -> Any:
    if seen is None:
        seen = set()

    if obj is None or isinstance(obj, str | int | float | bool):
        return obj

    obj_id = id(obj)
    if obj_id in seen:
        return "[Circular]"

    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, BaseException):
        return str(obj)

    try:
        import numpy as np  # type: ignore

        if isinstance(obj, np.generic):
            return obj.item()
        if isinstance(obj, np.ndarray):
            seen.add(obj_id)
            return make_json_safe(obj.tolist(), seen)
    except Exception:
        pass

    try:
        import pandas as pd  # type: ignore

        if isinstance(obj, pd.Series):
            seen.add(obj_id)
            return make_json_safe(obj.to_dict(), seen)
        if isinstance(obj, pd.DataFrame):
            seen.add(obj_id)
            return make_json_safe(obj.to_dict("records"), seen)
        if isinstance(obj, pd.Timestamp):
            return obj.isoformat()
    except Exception:
        pass

    if isinstance(obj, dict):
        seen.add(obj_id)
        safe = {
            str(key): make_json_safe(value, seen)
            for key, value in obj.items()
            if str(key).lower() not in {"authorization", "encrypted_key", "bland_api_key"}
        }
        seen.discard(obj_id)
        return safe

    if isinstance(obj, list | tuple | set):
        seen.add(obj_id)
        safe_list = [make_json_safe(value, seen) for value in obj]
        seen.discard(obj_id)
        return safe_list

    return repr(obj)


def build_call_task(row: dict[str, Any], missing_fields: list[str], custom_goal: str = "") -> str:
    fields = missing_fields or ["missing product details"]
    product_name = _text(row, "Product Name") or "the product"
    brand = _text(row, "Brand")
    model_sku = _text(row, "Model/SKU")
    field_phrase = ", ".join(fields)
    dimension_note = (
        " If asking about dimensions, request the full Width x Height x Depth."
        if any(field.lower() == "dimensions" for field in fields)
        else ""
    )
    product_ref = " ".join(part for part in [brand, model_sku] if part).strip() or product_name
    goal = custom_goal.strip() or f"Get the missing {field_phrase} for {product_ref}."
    agent_name = _agent_name()
    if os.getenv("BLAND_MINIMAL_PAYLOAD", "true").strip().lower() == "true":
        return build_minimal_call_task(row, missing_fields, custom_goal)
    return (
        f"You are {agent_name}, an AI phone assistant calling on behalf of Saffron Case Homes. "
        "Sound natural, concise, and professional. Do not mention AI unless asked directly. "
        "Start the call by saying: Hello, how are you? "
        f"Then explain: I'm calling on behalf of Saffron Case Homes. We're trying to confirm product information for {product_name}. "
        "Ask: Would you be able to search that item by reference number or model number? "
        f"Wait for their response. If they say yes or ask for the number, provide: The reference/model number is {model_sku or 'unknown'}. "
        f"If they ask what information is needed, say you are trying to confirm {field_phrase}.{dimension_note} "
        "If they need time, wait patiently and respond naturally. "
        "When they provide the information, repeat it back clearly, confirm units if dimensions are involved, and thank them. "
        "If they cannot help, ask: Is there a product support email or department that would be best for this? "
        "If transferred, briefly repeat the purpose and continue the same flow. "
        f"Call goal: {goal}"
    )


def build_minimal_call_task(row: dict[str, Any], missing_fields: list[str], custom_goal: str = "") -> str:
    if custom_goal.strip():
        return custom_goal.strip()
    fields = missing_fields or ["details"]
    brand = _text(row, "Brand")
    model_sku = _text(row, "Model/SKU")
    product_name = _text(row, "Product Name")
    field_phrase = ", ".join(fields)
    product_ref = " ".join(part for part in [brand, model_sku] if part).strip() or product_name or "this product"
    dimension_note = (
        " For dimensions, ask for full width, height, and depth and confirm the units."
        if any(field.lower() == "dimensions" for field in fields)
        else ""
    )
    return (
        "Call the vendor. Start with: Hello, how are you? "
        f"Then say you are calling on behalf of Saffron Case Homes to confirm product information for {product_name or product_ref}. "
        "Ask whether they can search by reference number or model number. "
        f"If they say yes or ask for it, provide this reference/model number: {model_sku or product_ref}. "
        f"If they ask what information is needed, ask for the missing {field_phrase}.{dimension_note} "
        "If they need time, wait patiently. When they provide the information, repeat it back clearly and thank them. "
        "If they cannot help, ask for the best product support email or department. Do not mention AI unless asked directly."
    )


def _write_call_record(record: dict[str, Any]) -> str:
    CALL_RECORD_DIR.mkdir(parents=True, exist_ok=True)
    safe_record = make_json_safe(record)
    timestamp = str(safe_record.get("timestamp") or datetime.now(timezone.utc).isoformat()).replace(":", "").replace("-", "")
    product = re.sub(r"[^a-zA-Z0-9]+", "_", str(record.get("product_name") or "product")).strip("_")[:40]
    path = CALL_RECORD_DIR / f"{timestamp}_{product or 'product'}.json"
    path.write_text(json.dumps(safe_record, indent=2), encoding="utf-8")
    return str(path)


def _read_response_body(response) -> tuple[str, dict[str, Any] | str]:
    body = response.read().decode("utf-8", errors="replace")
    if not body:
        return body, {}
    try:
        return body, json.loads(body)
    except json.JSONDecodeError:
        return body, body


def _auth_header_name() -> str:
    configured = os.getenv("BLAND_AUTH_HEADER_NAME", "Authorization").strip()
    return configured if configured in {"authorization", "Authorization"} else "authorization"


def _alternate_auth_header_name(header_name: str) -> str:
    return "Authorization" if header_name == "authorization" else "authorization"


def _bland_headers(header_name: str | None = None) -> dict[str, str]:
    api_key = os.getenv("BLAND_API_KEY", "").strip()
    auth_header = header_name or _auth_header_name()
    return {
        auth_header: api_key,
        "Content-Type": "application/json",
    }


def _sanitized_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        key: "[hidden]" if key.lower() in {"authorization", "encrypted_key"} else value
        for key, value in headers.items()
    }


def _retell_headers() -> dict[str, str]:
    api_key = os.getenv("RETELL_API_KEY", "").strip()
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _retell_agent_id() -> str:
    return os.getenv("RETELL_AGENT_ID", "").strip()


def _retell_from_number() -> str:
    return os.getenv("RETELL_PHONE_NUMBER", "").strip()


def _row_value(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _text(row, key)
        if value:
            return value
    return ""


def _retell_dynamic_variables(
    row: dict[str, Any],
    missing_fields: list[str],
    custom_goal: str = "",
) -> dict[str, str]:
    field = ", ".join(missing_fields) if missing_fields else "missing product details"
    return {
        "product_name": _row_value(row, "Name of Product", "Product Name"),
        "brand": _row_value(row, "Brand", "Brand / Manufacturer"),
        "model": _row_value(row, "Serial / Model Number", "Model/SKU", "SKU", "Model"),
        "missing_field": field,
        "call_goal": custom_goal.strip(),
    }


def _build_retell_payload(
    row: dict[str, Any],
    missing_fields: list[str],
    phone_number: str,
    custom_goal: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "from_number": _retell_from_number(),
        "to_number": phone_number.strip(),
        "override_agent_id": _retell_agent_id(),
        "retell_llm_dynamic_variables": _retell_dynamic_variables(row, missing_fields, custom_goal),
        "metadata": {
            "source": "sch_designops_intake",
            "provider": "retell",
            "product_name": _row_value(row, "Name of Product", "Product Name"),
            "brand": _row_value(row, "Brand", "Brand / Manufacturer"),
            "model": _row_value(row, "Serial / Model Number", "Model/SKU", "SKU", "Model"),
            "missing_fields": ", ".join(missing_fields),
        },
    }
    return payload


def _missing_retell_config() -> list[str]:
    missing = []
    if not os.getenv("RETELL_API_KEY", "").strip():
        missing.append("RETELL_API_KEY")
    if not _retell_agent_id():
        missing.append("RETELL_AGENT_ID")
    if not _retell_from_number():
        missing.append("RETELL_PHONE_NUMBER")
    return missing


def start_retell_call(
    row: dict[str, Any],
    missing_fields: list[str],
    phone_number: str,
    custom_goal: str = "",
) -> dict[str, Any]:
    missing_config = _missing_retell_config()
    if missing_config:
        return {
            "status": "missing_retell_config",
            "message": f"Retell config incomplete: missing {', '.join(missing_config)}.",
            "friendly_message": (
                "Retell outbound calls require a configured Retell phone number. "
                "Add RETELL_PHONE_NUMBER to .env/secrets."
                if "RETELL_PHONE_NUMBER" in missing_config
                else f"Retell config incomplete: missing {', '.join(missing_config)}."
            ),
            "provider": "retell",
            "missing_config": missing_config,
            "debug": {
                "provider": "retell",
                "missing_config": missing_config,
                "retell_api_key_configured": bool(os.getenv("RETELL_API_KEY", "").strip()),
                "retell_agent_id_configured": bool(_retell_agent_id()),
                "retell_phone_number_configured": bool(_retell_from_number()),
            },
        }

    payload = _build_retell_payload(row, missing_fields, phone_number, custom_goal)
    headers = _retell_headers()
    try:
        response = requests.post(
            RETELL_CREATE_PHONE_CALL_URL,
            headers=headers,
            json=payload,
            timeout=30,
        )
        body = response.text
        try:
            parsed: dict[str, Any] | str = response.json() if body else {}
        except ValueError:
            parsed = body
        debug = {
            "provider": "retell",
            "endpoint": RETELL_CREATE_PHONE_CALL_URL,
            "headers": _sanitized_headers(headers),
            "auth_header_name": "Authorization",
            "request_body": make_json_safe(payload),
            "request_body_keys": list(payload.keys()),
            "response_status_code": response.status_code,
            "response_text": body,
            "response_body": make_json_safe(parsed),
            "agent_id_used": payload["override_agent_id"],
            "phone_number_used": payload["to_number"],
            "from_number_used": payload.get("from_number", ""),
            "call_id": parsed.get("call_id") if isinstance(parsed, dict) else "",
            "provider_config": {
                "provider": "retell",
                "agent_id": payload["override_agent_id"],
                "from_number": payload.get("from_number", ""),
                "uses_bland_fallback": False,
            },
        }
        if response.ok and isinstance(parsed, dict):
            call_id = parsed.get("call_id")
            return {
                "status": "call_started" if call_id else "registered",
                "message": "Retell call started.",
                "call_id": call_id,
                "provider": "retell",
                "agent_id": parsed.get("agent_id") or payload["override_agent_id"],
                "call_status": parsed.get("call_status"),
                "provider_response": parsed,
                "debug": debug,
            }
        message = parsed.get("message", body) if isinstance(parsed, dict) else body
        return {
            "status": "error",
            "message": str(message),
            "provider": "retell",
            "status_code": response.status_code,
            "provider_response": parsed,
            "debug": debug,
        }
    except requests.Timeout as exc:
        return {
            "status": "provider_timeout",
            "message": f"Retell request timed out: {exc}",
            "provider": "retell",
            "debug": {
                "provider": "retell",
                "endpoint": RETELL_CREATE_PHONE_CALL_URL,
                "headers": _sanitized_headers(headers),
                "auth_header_name": "Authorization",
                "request_body": make_json_safe(payload),
                "agent_id_used": payload["override_agent_id"],
                "phone_number_used": payload["to_number"],
                "from_number_used": payload.get("from_number", ""),
                "provider_config": {
                    "provider": "retell",
                    "agent_id": payload["override_agent_id"],
                    "from_number": payload.get("from_number", ""),
                    "uses_bland_fallback": False,
                },
            },
        }
    except requests.RequestException as exc:
        return {
            "status": "error",
            "message": str(exc),
            "provider": "retell",
            "debug": {
                "provider": "retell",
                "endpoint": RETELL_CREATE_PHONE_CALL_URL,
                "headers": _sanitized_headers(headers),
                "auth_header_name": "Authorization",
                "request_body": make_json_safe(payload),
                "agent_id_used": payload["override_agent_id"],
                "phone_number_used": payload["to_number"],
                "from_number_used": payload.get("from_number", ""),
                "provider_config": {
                    "provider": "retell",
                    "agent_id": payload["override_agent_id"],
                    "from_number": payload.get("from_number", ""),
                    "uses_bland_fallback": False,
                },
            },
        }


def _missing_custom_retell_config() -> list[str]:
    missing = []
    if not os.getenv("RETELL_API_KEY", "").strip():
        missing.append("RETELL_API_KEY")
    if not _retell_agent_id():
        missing.append("RETELL_AGENT_ID")
    if not _retell_from_number():
        missing.append("RETELL_PHONE_NUMBER")
    return missing


def start_custom_retell_test_call(phone_number: str, custom_prompt: str) -> dict[str, Any]:
    phone_number = phone_number.strip()
    custom_prompt = custom_prompt.strip()
    if not phone_number:
        return {"status": "invalid_request", "message": "Phone number is required.", "provider": "retell"}
    if not phone_number.startswith("+"):
        return {"status": "invalid_request", "message": "Phone number must start with +.", "provider": "retell"}
    if not custom_prompt:
        return {"status": "invalid_request", "message": "Call prompt / objective is required.", "provider": "retell"}

    missing_config = _missing_custom_retell_config()
    if missing_config:
        return {
            "status": "missing_retell_config",
            "message": f"Retell config incomplete: missing {', '.join(missing_config)}.",
            "provider": "retell",
            "missing_config": missing_config,
            "debug": {
                "provider": "retell",
                "missing_config": missing_config,
                "retell_api_key_configured": bool(os.getenv("RETELL_API_KEY", "").strip()),
                "retell_agent_id_configured": bool(_retell_agent_id()),
                "retell_phone_number_configured": bool(_retell_from_number()),
            },
        }

    payload = {
        "from_number": _retell_from_number(),
        "to_number": phone_number,
        "agent_id": _retell_agent_id(),
        "retell_llm_dynamic_variables": {
            "call_goal": custom_prompt,
            "product_name": "Custom Test Call",
            "brand": "",
            "model": "",
            "missing_field": "",
        },
        "metadata": {
            "source": "sch_custom_test_call",
            "provider": "retell",
            "call_type": "custom_test",
        },
    }
    headers = _retell_headers()
    try:
        response = requests.post(
            RETELL_CREATE_PHONE_CALL_URL,
            headers=headers,
            json=payload,
            timeout=30,
        )
        body = response.text
        try:
            parsed: dict[str, Any] | str = response.json() if body else {}
        except ValueError:
            parsed = body
        debug = {
            "provider": "retell",
            "endpoint": RETELL_CREATE_PHONE_CALL_URL,
            "headers": _sanitized_headers(headers),
            "auth_header_name": "Authorization",
            "request_body": make_json_safe(payload),
            "request_body_keys": list(payload.keys()),
            "response_status_code": response.status_code,
            "response_text": body,
            "response_body": make_json_safe(parsed),
            "agent_id_used": payload["agent_id"],
            "phone_number_used": payload["to_number"],
            "from_number_used": payload["from_number"],
            "call_id": parsed.get("call_id") if isinstance(parsed, dict) else "",
        }
        if response.ok and isinstance(parsed, dict):
            call_id = parsed.get("call_id")
            return {
                "status": "call_started" if call_id else "registered",
                "message": "Custom Retell test call started.",
                "call_id": call_id,
                "provider": "retell",
                "to_number": phone_number,
                "from_number": payload["from_number"],
                "agent_id": parsed.get("agent_id") or payload["agent_id"],
                "call_status": parsed.get("call_status"),
                "provider_response": make_json_safe(parsed),
                "debug": debug,
            }
        message = (
            parsed.get("message") or parsed.get("error_message")
            if isinstance(parsed, dict)
            else body
        )
        return {
            "status": "error",
            "message": str(message or "Retell custom test call failed."),
            "provider": "retell",
            "to_number": phone_number,
            "from_number": payload["from_number"],
            "agent_id": payload["agent_id"],
            "provider_response": make_json_safe(parsed),
            "debug": debug,
        }
    except requests.Timeout as exc:
        return {
            "status": "provider_timeout",
            "message": f"Retell request timed out: {exc}",
            "provider": "retell",
            "to_number": phone_number,
            "from_number": payload["from_number"],
            "agent_id": payload["agent_id"],
            "debug": {
                "provider": "retell",
                "endpoint": RETELL_CREATE_PHONE_CALL_URL,
                "headers": _sanitized_headers(headers),
                "auth_header_name": "Authorization",
                "request_body": make_json_safe(payload),
            },
        }
    except requests.RequestException as exc:
        return {
            "status": "error",
            "message": str(exc),
            "provider": "retell",
            "to_number": phone_number,
            "from_number": payload["from_number"],
            "agent_id": payload["agent_id"],
            "debug": {
                "provider": "retell",
                "endpoint": RETELL_CREATE_PHONE_CALL_URL,
                "headers": _sanitized_headers(headers),
                "auth_header_name": "Authorization",
                "request_body": make_json_safe(payload),
            },
        }


def _resolved_bland_fields(phone_number: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": get_call_provider(),
        "endpoint": BLAND_CALL_URL,
        "phone_number": phone_number,
        "from": payload.get("from") or "",
        "agent_name": _agent_name(),
        "voice": payload.get("voice") or "",
        "persona_id": payload.get("persona_id") or "",
        "pathway_id": payload.get("pathway_id") or "",
        "provider_config": _provider_config(),
        "default_bland_agent_used": not any([payload.get("persona_id"), payload.get("voice"), payload.get("pathway_id")]),
        "minimal_payload": set(payload.keys()) == {"phone_number", "task"},
        "minimal_payload_fields": list(payload.keys()),
        "api_key_loaded": bool(os.getenv("BLAND_API_KEY", "").strip()),
        "calls_enabled": calls_enabled(),
    }


def _log_bland_debug(
    *,
    endpoint: str,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any],
    response_status_code: int | None = None,
    response_text: str | None = None,
    response_body: str | dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    debug_record = {
        "provider": get_call_provider(),
        "endpoint": endpoint,
        "auth_header_name": next((key for key in (headers or _bland_headers()) if key.lower() == "authorization"), "authorization"),
        "headers": _sanitized_headers(headers or _bland_headers()),
        "resolved_phone_number": payload.get("phone_number", ""),
        "request_body_keys": list(payload.keys()),
        "minimal_payload_fields": list(payload.keys()),
        "resolved_agent_fields": _resolved_bland_fields(str(payload.get("phone_number", "")), payload),
        "request_body": make_json_safe(payload),
        "response_status_code": response_status_code,
        "response_text": response_text,
        "response_body": make_json_safe(response_body),
        "error": error,
    }
    _logger.info("Bland call diagnostic: %s", json.dumps(make_json_safe(debug_record), default=str))


def _build_debug(
    payload: dict[str, Any],
    *,
    response_status_code: int | None = None,
    response_text: str = "",
    response_body: str | dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    attempts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    resolved_headers = headers or _bland_headers()
    return {
        "endpoint": BLAND_CALL_URL,
        "auth_header_name": next((key for key in resolved_headers if key.lower() == "authorization"), "authorization"),
        "headers": _sanitized_headers(resolved_headers),
        "request_body": make_json_safe(payload),
        "request_body_keys": list(payload.keys()),
        "minimal_payload_fields": list(payload.keys()),
        "response_status_code": response_status_code,
        "response_text": response_text,
        "response_body": make_json_safe(response_body),
        "attempts": make_json_safe(attempts or []),
        "resolved_agent_fields": _resolved_bland_fields(str(payload.get("phone_number", "")), payload),
    }


def _classify_provider_error(message: str, provider_response: dict[str, Any] | None = None) -> str:
    combined = f"{message} {json.dumps(provider_response or {})}".lower()
    if any(term in combined for term in ["credit", "balance", "billing", "payment", "insufficient"]):
        return "no_credits"
    if any(term in combined for term in ["phone", "number", "invalid parameter"]):
        return "invalid_phone_number"
    if any(term in combined for term in ["timeout", "timed out"]):
        return "provider_timeout"
    return "error"


def explain_provider_failure(status: str, message: str = "", provider_response: dict[str, Any] | None = None) -> str:
    combined = f"{status} {message} {json.dumps(provider_response or {})}".lower()
    if "1010" in combined:
        return "Provider rejected outbound call. The payload or caller number was not accepted by Bland."
    if any(term in combined for term in ["auth", "unauthorized", "forbidden", "api key", "401", "403"]):
        return "Bland authentication failed. Check BLAND_API_KEY."
    if any(term in combined for term in ["credit", "balance", "billing", "payment", "insufficient"]):
        return "Bland rejected the call because credits or billing are not available."
    if any(term in combined for term in ["phone", "number", "invalid parameter"]):
        return "Bland rejected the phone number. Use E.164 format like +1XXXXXXXXXX."
    if any(term in combined for term in ["from", "caller", "outbound"]):
        return "Bland rejected the outbound caller number. Check BLAND_PHONE_NUMBER is activated in Bland."
    return "Provider rejected outbound call. Likely payload, caller number, credits, or account permissions."


def test_bland_connection() -> dict[str, Any]:
    api_key = os.getenv("BLAND_API_KEY", "").strip()
    if not api_key:
        return {
            "status": "missing_api_key",
            "message": "BLAND_API_KEY is not configured.",
            "provider": get_call_provider(),
            "endpoint": BLAND_ACCOUNT_URL,
            "account_connection": "failed",
            "provider_reachable": "unknown",
            "outbound_enabled": "unknown",
            "billing_status": "unknown",
            "provider_response_text": "",
        }

    request = urllib.request.Request(BLAND_ACCOUNT_URL, headers=_bland_headers("authorization"), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw_body, parsed = _read_response_body(response)
            account = parsed if isinstance(parsed, dict) else {}
            billing = account.get("billing") if isinstance(account.get("billing"), dict) else {}
            balance = billing.get("current_balance")
            return {
                "status": "connected",
                "message": "Bland API key is valid.",
                "provider": get_call_provider(),
                "endpoint": BLAND_ACCOUNT_URL,
                "response_status_code": response.status,
                "account_connection": "success",
                "provider_reachable": "true",
                "outbound_enabled": "unknown",
                "billing_status": "unknown" if balance is None else f"balance: {balance}",
                "provider_response": account,
                "provider_response_text": raw_body,
            }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"message": body}
        message = parsed.get("message") or body or str(exc)
        return {
            "status": _classify_provider_error(message, parsed),
            "message": message,
            "provider": get_call_provider(),
            "endpoint": BLAND_ACCOUNT_URL,
            "response_status_code": exc.code,
            "account_connection": "failed",
            "provider_reachable": "true",
            "outbound_enabled": "unknown",
            "billing_status": "unknown",
            "provider_response": parsed,
            "provider_response_text": body,
        }
    except (TimeoutError, socket.timeout) as exc:
        return {
            "status": "provider_timeout",
            "message": f"Bland connection timed out: {exc}",
            "provider": get_call_provider(),
            "endpoint": BLAND_ACCOUNT_URL,
            "account_connection": "unknown",
            "provider_reachable": "false",
            "outbound_enabled": "unknown",
            "billing_status": "unknown",
            "provider_response_text": "",
        }
    except Exception as exc:
        return {
            "status": "error",
            "message": str(exc),
            "provider": get_call_provider(),
            "endpoint": BLAND_ACCOUNT_URL,
            "account_connection": "unknown",
            "provider_reachable": "false",
            "outbound_enabled": "unknown",
            "billing_status": "unknown",
            "provider_response_text": "",
        }


def list_bland_personas() -> dict[str, Any]:
    api_key = os.getenv("BLAND_API_KEY", "").strip()
    if not api_key:
        return {
            "status": "missing_api_key",
            "message": "BLAND_API_KEY is not configured.",
            "endpoint": BLAND_PERSONAS_URL,
            "personas": [],
            "provider_response_text": "",
        }

    headers = _bland_headers("authorization")
    attempts: list[dict[str, Any]] = []
    for endpoint in [BLAND_PERSONAS_URL, BLAND_PERSONAS_FALLBACK_URL]:
        try:
            response = requests.get(endpoint, headers=headers, timeout=20)
            raw_body = response.text
            try:
                parsed: dict[str, Any] | list[Any] | str = response.json() if raw_body else {}
            except ValueError:
                parsed = raw_body
            attempt = {
                "endpoint": endpoint,
                "response_status_code": response.status_code,
                "response_text": raw_body,
                "headers": _sanitized_headers(headers),
            }
            attempts.append(attempt)
            if response.ok:
                return _parse_bland_personas_response(
                    parsed=parsed,
                    raw_body=raw_body,
                    endpoint=endpoint,
                    response_status_code=response.status_code,
                    headers=headers,
                    attempts=attempts,
                )
        except requests.RequestException as exc:
            attempts.append(
                {
                    "endpoint": endpoint,
                    "status": "error",
                    "message": str(exc),
                    "headers": _sanitized_headers(headers),
                }
            )

    last_attempt = attempts[-1] if attempts else {}
    return {
        "status": "error",
        "message": (
            "Bland Personas API did not return available personas. "
            "Calls can still work, but Alley cannot be auto-resolved without BLAND_PERSONA_ID."
        ),
        "endpoint": BLAND_PERSONAS_URL,
        "response_status_code": last_attempt.get("response_status_code"),
        "personas": [],
        "requested_agent_name": _agent_name(),
        "matched_persona": None,
        "matched_persona_id": "",
        "matched_voice": "",
        "env_suggestion": "",
        "provider_response_text": str(last_attempt.get("response_text") or last_attempt.get("message") or ""),
        "headers": _sanitized_headers(headers),
        "attempts": make_json_safe(attempts),
    }


def _parse_bland_personas_response(
    *,
    parsed: dict[str, Any] | list[Any] | str,
    raw_body: str,
    endpoint: str,
    response_status_code: int,
    headers: dict[str, str],
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    personas_source = parsed.get("data", parsed) if isinstance(parsed, dict) else parsed
    if isinstance(personas_source, dict):
        personas_source = personas_source.get("personas", [])
    personas = []
    if isinstance(personas_source, list):
        for persona in personas_source:
            if not isinstance(persona, dict):
                continue
            personas.append(_persona_summary(persona))
    requested_agent_name = _agent_name()
    matched_persona = _find_persona_by_name(personas, requested_agent_name)
    return {
        "status": "connected",
        "endpoint": endpoint,
        "response_status_code": response_status_code,
        "personas": personas,
        "requested_agent_name": requested_agent_name,
        "matched_persona": matched_persona,
        "matched_persona_id": matched_persona.get("id") if matched_persona else "",
        "matched_voice": matched_persona.get("voice") if matched_persona else "",
        "env_suggestion": (
            f"BLAND_PERSONA_ID={matched_persona.get('id')}"
            if matched_persona and matched_persona.get("id")
            else ""
        ),
        "provider_response": make_json_safe(parsed),
        "provider_response_text": raw_body,
        "headers": _sanitized_headers(headers),
        "attempts": make_json_safe(attempts),
    }


def get_bland_persona_detail(persona_id: str) -> dict[str, Any]:
    api_key = os.getenv("BLAND_API_KEY", "").strip()
    persona_id = persona_id.strip()
    if not api_key:
        return {
            "status": "missing_api_key",
            "message": "BLAND_API_KEY is not configured.",
            "persona_id": persona_id,
        }
    if not persona_id:
        return {
            "status": "missing_persona_id",
            "message": "BLAND_PERSONA_ID is not configured.",
            "persona_id": persona_id,
        }

    headers = _bland_headers("authorization")
    attempts: list[dict[str, Any]] = []
    for template in BLAND_PERSONA_DETAIL_URLS:
        endpoint = template.format(persona_id=persona_id)
        try:
            response = requests.get(endpoint, headers=headers, timeout=20)
            raw_body = response.text
            try:
                parsed: dict[str, Any] | str = response.json() if raw_body else {}
            except ValueError:
                parsed = raw_body
            attempts.append(
                {
                    "endpoint": endpoint,
                    "response_status_code": response.status_code,
                    "response_text": raw_body,
                    "headers": _sanitized_headers(headers),
                }
            )
            if response.ok and isinstance(parsed, dict):
                persona = parsed.get("data", parsed)
                if not isinstance(persona, dict):
                    persona = {}
                return {
                    "status": "connected",
                    "endpoint": endpoint,
                    "response_status_code": response.status_code,
                    "persona": _persona_summary(persona),
                    "publication": _persona_publication_status(persona),
                    "provider_response": make_json_safe(parsed),
                    "provider_response_text": raw_body,
                    "headers": _sanitized_headers(headers),
                    "attempts": make_json_safe(attempts),
                }
        except requests.RequestException as exc:
            attempts.append(
                {
                    "endpoint": endpoint,
                    "status": "error",
                    "message": str(exc),
                    "headers": _sanitized_headers(headers),
                }
            )

    last_attempt = attempts[-1] if attempts else {}
    return {
        "status": "error",
        "message": "Bland did not return persona detail.",
        "persona_id": persona_id,
        "response_status_code": last_attempt.get("response_status_code"),
        "provider_response_text": str(last_attempt.get("response_text") or last_attempt.get("message") or ""),
        "headers": _sanitized_headers(headers),
        "attempts": make_json_safe(attempts),
    }


def _resolve_provider_config() -> dict[str, Any]:
    config = _provider_config()
    if config["persona_id"] or config["pathway_id"]:
        config["resolved_persona_id"] = config["persona_id"]
        config["resolved_voice"] = config["voice"]
        if config["voice"]:
            config["voice_source"] = config["voice_source"] or "env"
        config["persona_lookup_status"] = "explicit" if config["persona_id"] else "skipped_pathway_configured"
        if config["persona_id"]:
            detail = get_bland_persona_detail(config["persona_id"])
            config["persona_detail_status"] = detail.get("status")
            config["persona_detail_response_status_code"] = detail.get("response_status_code")
            config["persona_publication"] = detail.get("publication", {})
            config["persona_detail_error"] = detail.get("message", "")
        config["default_bland_agent_used"] = not any([config["persona_id"], config["voice_sent"], config["pathway_id"]])
        return config

    agent_name = config["agent_name"].strip()
    if not agent_name:
        config["persona_lookup_status"] = "skipped_no_agent_name"
        return config

    personas_result = list_bland_personas()
    config["persona_lookup_status"] = personas_result.get("status", "unknown")
    config["persona_lookup_endpoint"] = personas_result.get("endpoint", BLAND_PERSONAS_URL)
    config["persona_lookup_response_status_code"] = personas_result.get("response_status_code")
    if personas_result.get("status") != "connected":
        config["persona_lookup_error"] = personas_result.get("message", "")
        return config

    personas = personas_result.get("personas", [])
    if not isinstance(personas, list):
        config["persona_lookup_status"] = "invalid_response"
        return config

    match = _find_persona_by_name(personas, agent_name)
    if not match:
        config["persona_lookup_status"] = "not_found"
        config["fallback_reason"] = f"No Bland persona named '{agent_name}' was returned by the List Personas API."
        return config

    config["persona_id"] = str(match.get("id") or "").strip()
    matched_voice = str(match.get("voice") or "").strip()
    config["resolved_persona_id"] = config["persona_id"]
    config["resolved_voice"] = config["voice"] or matched_voice
    if not config["voice"] and matched_voice:
        config["voice"] = matched_voice
        config["voice_id"] = matched_voice
        config["voice_sent"] = bool(matched_voice and config["voice_override_enabled"] and not config["pathway_id"])
        config["voice_source"] = "persona_lookup"
    elif config["voice"]:
        config["voice_source"] = config["voice_source"] or "env"
    config["persona_lookup_status"] = "found"
    config["matched_persona"] = match
    config["env_suggestion"] = f"BLAND_PERSONA_ID={config['persona_id']}" if config["persona_id"] else ""
    config["default_bland_agent_used"] = not bool(config["persona_id"] or config["voice_sent"] or config["pathway_id"])
    return config


def _mock_vendor_call(row: dict[str, Any], missing_fields: list[str], phone_number: str, custom_goal: str = "") -> dict[str, Any]:
    task = build_call_task(row, missing_fields, custom_goal)
    call_id = f"mock_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    field_text = ", ".join(missing_fields) or "details"
    transcript = (
        "assistant: Hi, this is Alley calling on behalf of Saffron Case Homes.\n"
        f"user: Sure, for demo purposes the missing {field_text} is available for review.\n"
        "user: Width 36, Height 84, Depth 24.\n"
        "assistant: Thank you, I will pass that along for human review."
    )
    provider_response = {
        "status": "success",
        "message": "Mock call completed for demo mode.",
        "call_id": call_id,
        "transcript": transcript,
        "recording_url": "mock://recording-placeholder",
    }
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "product_name": _text(row, "Product Name"),
        "brand": _text(row, "Brand"),
        "model_sku": _text(row, "Model/SKU"),
        "missing_fields": missing_fields,
        "phone_number": phone_number.strip(),
        "custom_goal": custom_goal,
        "generated_task": task,
        "provider": "mock",
        "agent_name": _agent_name(),
        "provider_response": provider_response,
        "status": "mock_call_completed",
    }
    record_path = _write_call_record(record)
    return {
        "status": "mock_call_completed",
        "message": "Mock call completed for demo mode.",
        "call_id": call_id,
        "provider": "mock",
        "agent_name": _agent_name(),
        "task": task,
        "record_path": record_path,
        "provider_response": provider_response,
        "transcript": transcript,
        "recording_url": "mock://recording-placeholder",
    }


def _send_bland_call(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("BLAND_API_KEY", "").strip()
    if not api_key:
        return {"status": "missing_api_key", "message": "BLAND_API_KEY is not configured."}

    headers = _bland_headers("Authorization")
    _log_bland_debug(endpoint=BLAND_CALL_URL, headers=headers, payload=payload)
    try:
        response = requests.post(BLAND_CALL_URL, headers=headers, json=payload, timeout=30)
        body = response.text
        try:
            parsed: dict[str, Any] | str = response.json() if body else {}
        except ValueError:
            parsed = body
        _log_bland_debug(
            endpoint=BLAND_CALL_URL,
            headers=headers,
            payload=payload,
            response_status_code=response.status_code,
            response_text=body,
            response_body=parsed,
        )
        debug = _build_debug(
            payload,
            response_status_code=response.status_code,
            response_text=body,
            response_body=parsed,
            headers=headers,
            attempts=[
                {
                    "auth_header_name": "Authorization",
                    "endpoint": BLAND_CALL_URL,
                    "response_status_code": response.status_code,
                    "response_text": body,
                }
            ],
        )
        if response.ok and isinstance(parsed, dict):
            parsed["debug"] = debug
            return parsed
        message = parsed.get("message", body) if isinstance(parsed, dict) else body
        return {
            "status": _classify_provider_error(str(message), parsed if isinstance(parsed, dict) else None),
            "message": str(message),
            "status_code": response.status_code,
            "provider_response": parsed,
            "debug": debug,
        }
    except requests.Timeout as exc:
        message = f"Bland request timed out: {exc}"
        _log_bland_debug(endpoint=BLAND_CALL_URL, headers=headers, payload=payload, error=message)
        return {
            "status": "provider_timeout",
            "message": message,
            "debug": _build_debug(payload, headers=headers),
        }
    except requests.RequestException as exc:
        message = str(exc)
        if "1010" in message:
            message = (
                f"{message}. Bland returned provider error 1010 before a normal JSON response. "
                "Check outbound-call enablement, account credits, and whether this account requires a persona/pathway."
            )
        _log_bland_debug(endpoint=BLAND_CALL_URL, headers=headers, payload=payload, error=message)
        return {
            "status": "error",
            "message": message,
            "debug": _build_debug(payload, headers=headers),
        }


def _provider_rejected_config(result: dict[str, Any]) -> bool:
    status_code = result.get("status_code") or result.get("debug", {}).get("response_status_code")
    text = f"{result.get('message', '')} {result.get('debug', {}).get('response_text', '')} {json.dumps(result.get('provider_response', {}), default=str)}".lower()
    return bool(status_code in {400, 401, 403, 422} or "1010" in text or "persona" in text or "voice" in text)


def start_bland_minimal_call(phone_number: str, task: str) -> dict[str, Any]:
    valid, error = _validate_phone_number(phone_number)
    if not valid:
        return {"status": "invalid_phone_number", "message": error}
    result = _send_bland_call({"phone_number": phone_number.strip(), "task": task.strip()})
    return _normalize_bland_success(result, "Minimal Bland call started.")


def start_bland_call(phone_number: str, task: str, metadata: dict[str, Any]) -> dict[str, Any]:
    config = _resolve_provider_config()
    configured_fields_present = any([
        config["caller_number_sent"],
        config["persona_id"],
        config["voice_sent"],
        config["pathway_id"],
    ])
    if os.getenv("BLAND_MINIMAL_PAYLOAD", "true").strip().lower() == "true" and not configured_fields_present:
        return start_bland_minimal_call(phone_number, task)

    base_payload = {"phone_number": phone_number.strip()}
    if config["pathway_id"]:
        base_payload["pathway_id"] = config["pathway_id"]
    else:
        base_payload["task"] = task.strip()
    if config["caller_number_sent"]:
        base_payload["from"] = config["from"]

    attempts: list[dict[str, Any]] = []
    payloads: list[tuple[str, dict[str, Any]]] = []
    if config["pathway_id"]:
        payloads.append(("pathway", dict(base_payload)))
    else:
        if config["voice_sent"]:
            voice_payload = {**base_payload, "voice": config["voice"]}
            payloads.append(("voice_only", voice_payload))
            if config["persona_id"]:
                payloads.append(("voice_and_persona", {**voice_payload, "persona_id": config["persona_id"]}))
        elif config["persona_id"]:
            payloads.append(("persona_only", {**base_payload, "persona_id": config["persona_id"]}))
        payloads.append(("default", dict(base_payload)))

    last_result: dict[str, Any] = {}
    rejected_attempts: list[dict[str, Any]] = []
    for attempt_name, payload in payloads:
        result = _send_bland_call(payload)
        last_result = result
        if isinstance(result.get("debug"), dict):
            result["debug"]["provider_config"] = config
            result["debug"].setdefault("resolved_agent_fields", {})["provider_config"] = config
            result["debug"]["attempt_name"] = attempt_name
        provider_status = str(result.get("status", "")).lower()
        if provider_status == "success":
            normalized = _normalize_bland_success(result, "Configured Bland call started.")
            debug = normalized.setdefault("debug", {})
            debug["provider_config"] = config
            debug["voice_field_sent"] = bool(payload.get("voice"))
            debug["voice_id_used"] = payload.get("voice", "")
            debug["persona_id_sent"] = bool(payload.get("persona_id"))
            debug["persona_id_used"] = payload.get("persona_id", "")
            debug["fallback_used"] = attempt_name != payloads[0][0]
            debug["successful_attempt"] = attempt_name
            debug["rejected_attempts"] = make_json_safe(rejected_attempts)
            if attempt_name == "default" and payloads[0][0] != "default":
                normalized["warning"] = "Bland rejected the Alley voice/persona config, so the call used the default working payload."
            elif attempt_name == "voice_and_persona":
                normalized["message"] = "Configured Bland call started with voice and persona."
            elif attempt_name == "voice_only":
                normalized["message"] = "Configured Bland call started with direct voice override."
            return normalized

        rejected_attempts.append(
            {
                "attempt_name": attempt_name,
                "payload_keys": list(payload.keys()),
                "response_status_code": result.get("status_code") or result.get("debug", {}).get("response_status_code"),
                "response_text": result.get("debug", {}).get("response_text", ""),
            }
        )
        if not _provider_rejected_config(result):
            break

    if last_result:
        debug = last_result.setdefault("debug", {})
        debug["provider_config"] = config
        debug["voice_field_sent"] = bool(debug.get("request_body", {}).get("voice"))
        debug["voice_id_used"] = debug.get("request_body", {}).get("voice", "")
        debug["persona_id_sent"] = bool(debug.get("request_body", {}).get("persona_id"))
        debug["persona_id_used"] = debug.get("request_body", {}).get("persona_id", "")
        debug["fallback_used"] = True
        debug["rejected_attempts"] = make_json_safe(rejected_attempts)
        return last_result

    return {"status": "error", "message": "No Bland payload was attempted."}


def _normalize_bland_success(result: dict[str, Any], default_message: str) -> dict[str, Any]:
    provider_status = str(result.get("status", "")).lower()
    call_id = result.get("call_id")
    if provider_status == "success" and call_id:
        return {
            **result,
            "status": "call_started",
            "message": result.get("message", default_message),
            "provider": "bland",
            "provider_response": {key: value for key, value in result.items() if key != "debug"},
        }
    if result.get("status") != "call_started":
        result["friendly_message"] = explain_provider_failure(
            str(result.get("status", "error")),
            str(result.get("message", "")),
            result.get("provider_response") if isinstance(result.get("provider_response"), dict) else None,
        )
    return result


def start_vendor_call(
    row: dict[str, Any],
    missing_fields: list[str],
    phone_number: str,
    custom_goal: str = "",
) -> dict[str, Any]:
    if not calls_enabled():
        return {"status": "disabled", "message": "Vendor calls are not enabled."}

    valid, error = _validate_phone_number(phone_number)
    if not valid:
        return {"status": "invalid_phone_number", "message": error}

    provider = get_call_provider()
    if vendor_call_mock_enabled():
        return _mock_vendor_call(row, missing_fields, phone_number, custom_goal)

    task = build_call_task(row, missing_fields, custom_goal)
    metadata = {
        "source": "sch_designops_intake",
        "product_name": _text(row, "Product Name"),
        "brand": _text(row, "Brand"),
        "model_sku": _text(row, "Model/SKU"),
        "missing_fields": missing_fields,
        "agent_name": _agent_name(),
        "self_test": _is_self_test_number(phone_number),
    }

    if provider == "retell":
        provider_response = start_retell_call(row, missing_fields, phone_number.strip(), custom_goal)
        if provider_response.get("status") == "missing_retell_config":
            return provider_response
    elif provider == "bland":
        if not os.getenv("BLAND_API_KEY", "").strip():
            return {"status": "missing_api_key", "message": "BLAND_API_KEY is not configured."}
        provider_response = start_bland_call(phone_number.strip(), task, metadata)
    else:
        return {
            "status": "unsupported_provider",
            "message": f"Vendor call provider '{provider}' is not supported yet.",
            "provider": provider,
        }

    provider_status = str(provider_response.get("status", "")).lower()
    call_id = provider_response.get("call_id")
    status = (
        "call_started"
        if provider_status in {"success", "registered", "call_started"} and call_id
        else provider_response.get("status", "error")
    )
    message = provider_response.get("message", "Call request submitted." if status == "call_started" else "")
    if status == "error":
        message = message or explain_provider_failure(status, "", provider_response)
    if status == "call_started":
        friendly_message = ""
    elif provider_response.get("provider") == "retell":
        friendly_message = provider_response.get("friendly_message") or message
    else:
        friendly_message = explain_provider_failure(status, message, provider_response)
    debug = provider_response.get("debug") if isinstance(provider_response, dict) else {}
    if not isinstance(debug, dict):
        debug = {}
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "product_name": _text(row, "Product Name"),
        "brand": _text(row, "Brand"),
        "model_sku": _text(row, "Model/SKU"),
        "missing_fields": missing_fields,
        "phone_number": phone_number.strip(),
        "custom_goal": custom_goal,
        "generated_task": task,
        "provider": provider_response.get("provider", provider),
        "endpoint": debug.get("endpoint") or (RETELL_CREATE_PHONE_CALL_URL if provider_response.get("provider") == "retell" else BLAND_CALL_URL),
        "request_body_sanitized": debug.get("request_body", {}),
        "response_status_code": debug.get("response_status_code"),
        "provider_response": {
            key: value for key, value in provider_response.items()
            if key not in {"debug", "task"}
        } if isinstance(provider_response, dict) else provider_response,
        "call_id": call_id,
        "status": status,
        "message": message,
        "friendly_message": friendly_message,
    }
    record_path = ""
    log_error = ""
    try:
        record_path = _write_call_record(record)
    except Exception as exc:
        log_error = str(exc)
        _logger.warning("Could not write vendor call record: %s", exc)
    return {
        "status": status,
        "message": message,
        "call_id": call_id,
        "provider": provider_response.get("provider", provider),
        "agent_name": _agent_name(),
        "provider_config": debug.get("provider_config") or (
            {
                "provider": "retell",
                "agent_id": provider_response.get("agent_id"),
                "uses_bland_fallback": False,
            }
            if provider_response.get("provider") == "retell"
            else _provider_config()
        ),
        "agent_id": provider_response.get("agent_id"),
        "task": task,
        "record_path": record_path,
        "log_error": log_error,
        "provider_response": provider_response,
        "debug": debug,
        "friendly_message": friendly_message,
        "warning": provider_response.get("warning", "") if isinstance(provider_response, dict) else "",
    }


def poll_call_result(call_id: str) -> dict[str, Any]:
    return get_call_status(call_id)


def _fetch_bland_call_detail(call_id: str) -> dict[str, Any]:
    api_key = os.getenv("BLAND_API_KEY", "").strip()
    if not api_key:
        return {"status": "missing_api_key", "message": "BLAND_API_KEY is not configured.", "call_id": call_id}
    if not call_id.strip():
        return {"status": "missing_call_id", "message": "Call ID is required.", "call_id": call_id}

    request = urllib.request.Request(
        BLAND_CALL_DETAIL_URL.format(call_id=call_id.strip()),
        headers=_bland_headers(),
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            parsed = json.loads(body) if body else {}
            return parsed if isinstance(parsed, dict) else {"status": "error", "message": body, "call_id": call_id}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"message": body}
        message = parsed.get("message", str(exc))
        return {
            "status": _classify_provider_error(message, parsed),
            "message": message,
            "call_id": call_id,
            "provider_response": parsed,
        }
    except (TimeoutError, socket.timeout) as exc:
        return {"status": "provider_timeout", "message": f"Bland request timed out: {exc}", "call_id": call_id}
    except Exception as exc:
        return {"status": "error", "message": str(exc), "call_id": call_id}


def get_retell_call_result(call_id: str) -> dict[str, Any]:
    api_key = os.getenv("RETELL_API_KEY", "").strip()
    call_id = call_id.strip()
    if not api_key:
        return {"status": "missing_api_key", "message": "RETELL_API_KEY is not configured.", "call_id": call_id, "provider": "retell"}
    if not call_id:
        return {"status": "missing_call_id", "message": "Call ID is required.", "call_id": call_id, "provider": "retell"}

    endpoint = RETELL_GET_CALL_URL.format(call_id=call_id)
    headers = _retell_headers()
    try:
        response = requests.get(endpoint, headers=headers, timeout=30)
        body = response.text
        try:
            parsed: dict[str, Any] | str = response.json() if body else {}
        except ValueError:
            parsed = body
        if response.ok and isinstance(parsed, dict):
            transcript = _transcript_from_retell_detail(parsed)
            return {
                "status": "retrieved",
                "provider": "retell",
                "call_id": parsed.get("call_id") or call_id,
                "call_status": parsed.get("call_status"),
                "transcript": transcript,
                "recording_url": parsed.get("recording_url") or parsed.get("recording_multi_channel_url"),
                "call_analysis": make_json_safe(parsed.get("call_analysis") or {}),
                "raw_response": make_json_safe(parsed),
                "debug": {
                    "provider": "retell",
                    "endpoint": endpoint,
                    "headers": _sanitized_headers(headers),
                    "auth_header_name": "Authorization",
                    "response_status_code": response.status_code,
                    "response_text": body,
                    "response_body": make_json_safe(parsed),
                    "transcript_found": bool(transcript),
                },
            }
        message = (
            parsed.get("message") or parsed.get("error_message")
            if isinstance(parsed, dict)
            else body
        )
        return {
            "status": "error",
            "message": str(message or "Retell call lookup failed."),
            "provider": "retell",
            "call_id": call_id,
            "raw_response": make_json_safe(parsed),
            "debug": {
                "provider": "retell",
                "endpoint": endpoint,
                "headers": _sanitized_headers(headers),
                "auth_header_name": "Authorization",
                "response_status_code": response.status_code,
                "response_text": body,
                "response_body": make_json_safe(parsed),
            },
        }
    except requests.Timeout as exc:
        return {"status": "provider_timeout", "message": f"Retell request timed out: {exc}", "call_id": call_id, "provider": "retell"}
    except requests.RequestException as exc:
        return {"status": "error", "message": str(exc), "call_id": call_id, "provider": "retell"}


def _transcript_from_detail(detail: dict[str, Any]) -> str:
    if detail.get("concatenated_transcript"):
        return str(detail["concatenated_transcript"]).strip()
    transcripts = detail.get("transcripts")
    if isinstance(transcripts, list):
        lines = []
        for entry in transcripts:
            if not isinstance(entry, dict):
                continue
            speaker = str(entry.get("user") or entry.get("speaker_label") or "").strip()
            text = str(entry.get("text") or "").strip()
            if text:
                lines.append(f"{speaker}: {text}" if speaker else text)
        return "\n".join(lines).strip()
    return ""


def _transcript_from_retell_detail(detail: dict[str, Any]) -> str:
    transcript = detail.get("transcript") or detail.get("scrubbed_transcript")
    if isinstance(transcript, str) and transcript.strip():
        return transcript.strip()
    transcript_objects = (
        detail.get("transcript_object")
        or detail.get("transcript_with_tool_calls")
        or detail.get("scrubbed_transcript_with_tool_calls")
    )
    if isinstance(transcript_objects, list):
        lines = []
        for entry in transcript_objects:
            if not isinstance(entry, dict):
                continue
            role = str(entry.get("role") or entry.get("speaker") or entry.get("speaker_label") or "").strip()
            content = str(entry.get("content") or entry.get("text") or entry.get("transcript") or entry.get("utterance") or "").strip()
            if content:
                lines.append(f"{role}: {content}" if role else content)
        return "\n".join(lines).strip()
    return ""


def _retell_status_from_detail(detail: dict[str, Any], call_id: str) -> dict[str, Any]:
    call_status = str(detail.get("call_status") or "").strip().lower()
    transcript = str(detail.get("transcript") or "")
    if call_status in {"ended"}:
        normalized = "call_completed"
    elif call_status in {"error", "failed", "not_connected"}:
        normalized = "call_failed"
    else:
        normalized = "call_in_progress"
    return {
        "status": normalized,
        "provider": "retell",
        "call_id": detail.get("call_id") or call_id,
        "provider_status": call_status or "unknown",
        "queue_status": call_status or "unknown",
        "completed": normalized == "call_completed",
        "message": detail.get("message") or detail.get("error_message") or "",
        "transcript": transcript,
        "summary": (detail.get("call_analysis") or {}).get("call_summary") if isinstance(detail.get("call_analysis"), dict) else "",
        "recording_url": detail.get("recording_url"),
        "call_analysis": make_json_safe(detail.get("call_analysis") or {}),
        "provider_response": make_json_safe(detail.get("raw_response") or detail),
        "debug": make_json_safe(detail.get("debug") or {}),
    }


def get_call_status(call_id: str, provider: str | None = None) -> dict[str, Any]:
    if call_id.startswith("mock_"):
        return {
            "status": "mock_call_completed",
            "provider": "mock",
            "call_id": call_id,
            "provider_status": "completed",
            "queue_status": "complete",
            "completed": True,
            "answered_by": "demo",
            "message": "Mock call completed for demo mode.",
            "transcript": (
                "assistant: Hi, this is Alley calling on behalf of Saffron Case Homes.\n"
                "user: Width 36, Height 84, Depth 24.\n"
                "assistant: Thank you, these values will be sent to review."
            ),
            "summary": "Mock demo call returned sample dimension values.",
            "recording_url": "mock://recording-placeholder",
            "provider_response": {"status": "success", "mock": True},
        }
    if _preferred_provider(provider) == "retell":
        detail = get_retell_call_result(call_id)
        if detail.get("status") in {"missing_api_key", "missing_call_id", "provider_timeout", "error"}:
            return detail
        return _retell_status_from_detail(detail, call_id)

    detail = _fetch_bland_call_detail(call_id)
    if detail.get("status") in {"missing_api_key", "missing_call_id", "provider_timeout", "error", "no_credits"}:
        return detail

    transcript = _transcript_from_detail(detail)
    status = str(detail.get("status") or detail.get("queue_status") or "unknown")
    queue_status = str(detail.get("queue_status") or "")
    error_message = detail.get("error_message") or detail.get("message") or ""
    normalized = "call_completed" if status in {"completed", "complete"} or detail.get("completed") else "call_in_progress"
    if status in {"failed", "busy", "no-answer", "canceled"} or error_message:
        normalized = "call_failed"
    return {
        "status": normalized,
        "provider": get_call_provider(),
        "call_id": detail.get("call_id") or call_id,
        "provider_status": status,
        "queue_status": queue_status,
        "completed": bool(detail.get("completed") or status in {"completed", "complete"}),
        "answered_by": detail.get("answered_by"),
        "message": error_message,
        "transcript": transcript,
        "summary": detail.get("summary"),
        "recording_url": detail.get("recording_url"),
        "provider_response": detail,
    }


def get_call_transcript(call_id: str) -> dict[str, Any]:
    status = get_call_status(call_id)
    return {
        "status": status.get("status"),
        "call_id": status.get("call_id", call_id),
        "transcript": status.get("transcript", ""),
        "summary": status.get("summary"),
        "provider": status.get("provider", get_call_provider()),
        "message": status.get("message", ""),
    }


def prepare_call_payload(
    row: dict[str, Any],
    missing_fields: list[str],
    phone_number: str,
    custom_goal: str = "",
) -> dict[str, Any]:
    goal = custom_goal.strip() or build_call_goal(row, missing_fields)
    return {
        "provider": None,
        "future_providers": FUTURE_CALL_PROVIDERS,
        "status": "Needs Human Review",
        "call_prepared": False,
        "call_completed": False,
        "phone_number": phone_number.strip(),
        "missing_fields": missing_fields,
        "goal": goal,
        "script": build_call_script(row, missing_fields, phone_number, custom_goal=goal),
        "row": row,
    }


def parse_call_transcript_for_missing_values(transcript: str, missing_fields: list[str]) -> dict[str, str]:
    return parse_transcript_to_fields(transcript, missing_fields)


def parse_transcript_to_fields(transcript: str, missing_fields: list[str]) -> dict[str, str]:
    result = extract_vendor_specs_from_transcript(transcript, {}, missing_fields)
    values = {
        field: str(detail.get("value", ""))
        for field, detail in result.get("extracted_fields", {}).items()
        if isinstance(detail, dict) and str(detail.get("value", "")).strip()
    }
    if any(field.strip().lower() == "dimensions" for field in missing_fields):
        dim_result = _dimension_extract(transcript)
        parts = dim_result.get("parts", {}) if isinstance(dim_result, dict) else {}
        for label in ("Width", "Height", "Depth"):
            if parts.get(label):
                values[label] = str(parts[label])
        if parts.get("Width") and parts.get("Height") and parts.get("Depth"):
            values["Dimensions"] = f'{parts["Width"]}"W x {parts["Height"]}"H x {parts["Depth"]}"D'
    values.update(extract_missing_values_from_transcript(transcript, missing_fields))
    return values


def _extract_dimension_value(transcript: str, label: str) -> str:
    label_pattern = {
        "width": r"(?:width|wide|w)",
        "height": r"(?:height|high|h)",
        "depth": r"(?:depth|deep|d)",
    }[label]
    patterns = [
        rf"\b{label_pattern}\b\s*(?:is|=|:|-)?\s*(\d+(?:\.\d+)?)\s*(?:inches|inch|in|\"|”)?",
        rf"(\d+(?:\.\d+)?)\s*(?:inches|inch|in|\"|”)?\s*\b{label_pattern}\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, transcript, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _number_words_to_value(phrase: str) -> float | None:
    tokens = re.split(r"[\s-]+", phrase.lower().strip())
    if not tokens:
        return None
    total = 0
    current = 0
    found = False
    for token in tokens:
        if token in {"and", "a"}:
            continue
        if token == "hundred":
            current = max(current, 1) * 100
            found = True
            continue
        value = _NUMBER_WORDS.get(token)
        if value is None:
            return None
        current += value
        found = True
    total += current
    return float(total) if found else None


def _replace_number_words(transcript: str) -> str:
    word_pattern = "|".join(sorted(map(re.escape, _NUMBER_WORDS), key=len, reverse=True))
    pattern = re.compile(rf"\b(?:(?:{word_pattern}|hundred|and)[\s-]*)+\b", re.IGNORECASE)

    def repl(match: re.Match[str]) -> str:
        phrase = match.group(0).strip()
        value = _number_words_to_value(phrase)
        if value is None:
            return phrase
        return str(int(value)) if value.is_integer() else str(value)

    return pattern.sub(repl, transcript)


def _clean_number(value: str | float | int) -> str:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return str(value).strip()
    return str(int(number)) if number.is_integer() else str(number).rstrip("0").rstrip(".")


def _sentence_evidence(transcript: str, start: int = 0) -> str:
    snippet = transcript[max(0, start - 120): start + 220].strip()
    parts = re.split(r"(?<=[.!?])\s+", snippet)
    return (parts[0] if parts else snippet).strip()[:240]


def _normalise_requested_fields(missing_fields: list[str]) -> set[str]:
    requested = set()
    for field in missing_fields:
        lower = field.strip().lower()
        if lower in {"serial / model number", "model number", "sku", "model/sku"}:
            requested.add("Model/SKU")
        elif lower in {"finish", "color", "finish / color"}:
            requested.add("Finish / Color")
        elif lower in {"supplier", "who bought from", "supplier / who bought from"}:
            requested.add("Supplier")
        elif lower in {"location", "room"}:
            requested.add("Room")
        elif lower in {"category", "product category"}:
            requested.add("Product Category")
        else:
            requested.add(field.strip())
    return requested


def _dimension_extract(transcript: str) -> dict[str, Any]:
    text = _replace_number_words(transcript)
    values: dict[str, list[tuple[str, str]]] = {"Width": [], "Depth": [], "Height": []}

    ordered_patterns = [
        (
            re.compile(
                r"(?P<w>\d+(?:\.\d+)?)\s*(?:inches|inch|in|\"|”)?\s*(?:wide|width|w)\s*(?:by|x|×|,|and)\s*"
                r"(?P<d>\d+(?:\.\d+)?)\s*(?:inches|inch|in|\"|”)?\s*(?:deep|depth|d)\s*(?:by|x|×|,|and)\s*"
                r"(?P<h>\d+(?:\.\d+)?)\s*(?:inches|inch|in|\"|”)?\s*(?:high|height|h)",
                re.IGNORECASE,
            ),
            (("w", "Width"), ("d", "Depth"), ("h", "Height")),
        ),
        (
            re.compile(
                r"(?P<w>\d+(?:\.\d+)?)\s*(?:w|wide|width)\s*(?:x|×|by)\s*"
                r"(?P<d>\d+(?:\.\d+)?)\s*(?:d|deep|depth)\s*(?:x|×|by)\s*"
                r"(?P<h>\d+(?:\.\d+)?)\s*(?:h|high|height)",
                re.IGNORECASE,
            ),
            (("w", "Width"), ("d", "Depth"), ("h", "Height")),
        ),
        (
            re.compile(
                r"(?P<w>\d+(?:\.\d+)?)\s*(?:w|wide|width)\s*(?:x|×|by)\s*"
                r"(?P<h>\d+(?:\.\d+)?)\s*(?:h|high|height)\s*(?:x|×|by)\s*"
                r"(?P<d>\d+(?:\.\d+)?)\s*(?:d|deep|depth)",
                re.IGNORECASE,
            ),
            (("w", "Width"), ("h", "Height"), ("d", "Depth")),
        ),
    ]
    for pattern, group_labels in ordered_patterns:
        for match in pattern.finditer(text):
            for group, label in group_labels:
                if group in match.groupdict() and match.group(group):
                    values[label].append((_clean_number(match.group(group)), _sentence_evidence(transcript, match.start())))

    label_patterns = {
        "Width": r"(?:width|wide|w)",
        "Depth": r"(?:depth|deep|d)",
        "Height": r"(?:height|high|h)",
    }
    for label, label_pattern in label_patterns.items():
        patterns = [
            rf"\b{label_pattern}\b\s*(?:is|=|:|-)?\s*(\d+(?:\.\d+)?)\s*(?:inches|inch|in|\"|”)?",
            rf"(\d+(?:\.\d+)?)\s*(?:inches|inch|in|\"|”)?\s*\b{label_pattern}\b",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                values[label].append((_clean_number(match.group(1)), _sentence_evidence(transcript, match.start())))

    if not any(values.values()):
        unlabeled = re.search(
            r"(?:dimensions|size|measures?)\D{0,40}"
            r"(\d+(?:\.\d+)?)\s*(?:inches|inch|in|\"|”)?\s*(?:by|x|×)\s*"
            r"(\d+(?:\.\d+)?)\s*(?:inches|inch|in|\"|”)?\s*(?:by|x|×)\s*"
            r"(\d+(?:\.\d+)?)\s*(?:inches|inch|in|\"|”)?",
            text,
            re.IGNORECASE,
        )
        if unlabeled:
            evidence = _sentence_evidence(transcript, unlabeled.start())
            values["Width"].append((_clean_number(unlabeled.group(1)), evidence))
            values["Depth"].append((_clean_number(unlabeled.group(2)), evidence))
            values["Height"].append((_clean_number(unlabeled.group(3)), evidence))

    chosen: dict[str, str] = {}
    evidence_parts = []
    conflicts: list[str] = []
    for label, matches in values.items():
        unique = []
        for value, evidence in matches:
            if value and value not in unique:
                unique.append(value)
                if evidence:
                    evidence_parts.append(evidence)
        if len(unique) == 1:
            chosen[label] = unique[0]
        elif len(unique) > 1:
            conflicts.append(label)

    if conflicts:
        return {"status": "conflict", "conflicts": conflicts, "evidence": " ".join(dict.fromkeys(evidence_parts))}
    if all(chosen.get(label) for label in ("Width", "Depth", "Height")):
        dimensions = f"{chosen['Width']} in W x {chosen['Depth']} in D x {chosen['Height']} in H"
        return {
            "status": "complete",
            "dimensions": dimensions,
            "parts": chosen,
            "evidence": " ".join(dict.fromkeys(evidence_parts))[:360],
        }
    return {"status": "partial", "parts": chosen, "evidence": " ".join(dict.fromkeys(evidence_parts))[:360]}


def _extract_simple_field(transcript: str, field: str) -> tuple[str, str]:
    aliases = {
        "Finish / Color": ["finish", "color"],
        "Finish": ["finish"],
        "Material": ["material"],
        "Lead Time": ["lead time", "leadtime"],
        "Price": ["price", "cost"],
        "SKU": ["sku"],
        "Model Number": ["model number", "model", "reference number"],
        "Model/SKU": ["model/sku", "model number", "sku", "model"],
        "Availability": ["availability", "available"],
        "Brand": ["brand", "manufacturer"],
        "Supplier": ["supplier", "vendor"],
        "Product Name": ["product name", "item name", "name"],
        "Product Category": ["category", "product category"],
        "Quantity": ["quantity", "qty"],
        "Room": ["location", "room"],
    }.get(field, [field])
    for alias in aliases:
        pattern = re.compile(rf"\b{re.escape(alias)}\b\s*(?:is|=|:|-)?\s*([^\n.;]+)", re.IGNORECASE)
        match = pattern.search(transcript)
        if match:
            value = match.group(1).strip(" ,")
            if value and value.lower() not in {"unknown", "not sure", "do not know", "don't know"}:
                return value, _sentence_evidence(transcript, match.start())
    return "", ""


def extract_vendor_specs_from_transcript(
    transcript: str,
    product_context: dict[str, Any],
    missing_fields: list[str],
) -> dict[str, Any]:
    requested = _normalise_requested_fields(missing_fields)
    extracted: dict[str, dict[str, str]] = {}
    unresolved: list[str] = []
    notes: list[str] = []

    dimension_requested = bool(requested & {"Dimensions", "Width", "Depth", "Height"})
    if dimension_requested:
        dim_result = _dimension_extract(transcript)
        if dim_result.get("status") == "complete":
            extracted["Dimensions"] = {
                "value": str(dim_result["dimensions"]),
                "confidence": "high",
                "evidence": str(dim_result.get("evidence") or ""),
            }
            for label, value in dim_result.get("parts", {}).items():
                if label in requested:
                    extracted[label] = {
                        "value": f"{value} in",
                        "confidence": "high",
                        "evidence": str(dim_result.get("evidence") or ""),
                    }
        elif dim_result.get("status") == "partial":
            parts = dim_result.get("parts", {})
            for label in ("Width", "Depth", "Height"):
                if label in parts and label in requested:
                    extracted[label] = {
                        "value": f"{parts[label]} in",
                        "confidence": "medium",
                        "evidence": str(dim_result.get("evidence") or ""),
                    }
            if "Dimensions" in requested:
                unresolved.append("Dimensions")
        elif dim_result.get("status") == "conflict":
            unresolved.extend(["Dimensions"] if "Dimensions" in requested else dim_result.get("conflicts", []))
            notes.append("Conflicting dimension values were found; manual review required.")
        elif "Dimensions" in requested:
            unresolved.append("Dimensions")

    supported_simple = [
        "Finish / Color",
        "Finish",
        "Material",
        "Lead Time",
        "Price",
        "SKU",
        "Model Number",
        "Model/SKU",
        "Availability",
        "Brand",
        "Supplier",
        "Product Name",
        "Product Category",
        "Quantity",
        "Room",
    ]
    for field in supported_simple:
        if field not in requested or field in extracted:
            continue
        value, evidence = _extract_simple_field(transcript, field)
        if value:
            extracted[field] = {"value": value, "confidence": "medium", "evidence": evidence}
        else:
            unresolved.append(field)

    for field in requested:
        if field not in extracted and field not in unresolved and field not in {"Width", "Depth", "Height"}:
            unresolved.append(field)

    return {
        "extracted_fields": make_json_safe(extracted),
        "unresolved_fields": sorted(set(unresolved)),
        "notes": " ".join(notes),
        "product_context": make_json_safe(product_context),
    }


def extract_missing_values_from_transcript(transcript: str, missing_fields: list[str]) -> dict[str, str]:
    """
    Best-effort scaffold parser for future review-only extraction.
    Returned values must be reviewed by a human before being written to Programa.
    """
    values: dict[str, str] = {}
    for field in missing_fields:
        label = re.escape(field)
        match = re.search(rf"{label}\s*[:\-]\s*([^\n]+)", transcript, re.IGNORECASE)
        if match:
            values[field] = match.group(1).strip()
    return values
