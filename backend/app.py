from __future__ import annotations

import base64
import binascii
import logging
import os
import random
import re
import time
import traceback
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv
import httpx
from flask import Flask, g, jsonify, request, send_from_directory
from google import genai
from google.genai.errors import APIError
from google.genai import types
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from assistant_agents import AGENTS, AgentProfile, build_system_instruction, route_agent

# Project structure:
# - backend/app.py serves the frontend from the sibling frontend folder.
# - The .env file stores the Gemini API key so it is never exposed to the browser.
BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
ENV_FILE = BASE_DIR / "backend" / ".env"

load_dotenv(ENV_FILE)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")
app.logger.setLevel(logging.INFO)
app.config["MAX_CONTENT_LENGTH"] = 28 * 1024 * 1024

MODEL_CANDIDATES = (
    "gemini-3.6-flash",
    "gemini-3.5-flash",
)
GEMINI_TIMEOUT_MS = 15_000
MAX_GENERATION_ATTEMPTS = 2
MAX_RETRY_DELAY_SECONDS = 2.0
MAX_MESSAGE_LENGTH = 12_000
MAX_HISTORY_MESSAGES = 20
MAX_HISTORY_MESSAGE_LENGTH = 4_000
MAX_HISTORY_TOTAL_LENGTH = 24_000
MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_SIZE = 20 * 1024 * 1024
ATTACHMENT_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
}


class Attachment(TypedDict):
    name: str
    mime_type: str
    data: bytes


class ConfigurationError(ValueError):
    """Raised when required backend configuration is missing or invalid."""


class EmptyModelResponseError(RuntimeError):
    """Raised when Gemini did not return usable text."""


def current_request_id() -> str:
    try:
        return g.request_id
    except RuntimeError:
        return "unavailable"


@app.before_request
def start_request_log_context():
    g.request_id = uuid.uuid4().hex
    g.request_started_at = time.perf_counter()
    g.selected_agent = "-"
    g.error_category = None


@app.after_request
def log_request_completion(response):
    request_id = getattr(g, "request_id", "unavailable")
    response.headers["X-Request-ID"] = request_id
    started_at = getattr(g, "request_started_at", None)
    duration_ms = (time.perf_counter() - started_at) * 1000 if started_at else 0
    status_code = response.status_code
    log_method = app.logger.warning if status_code >= 400 else app.logger.info
    log_method(
        "timestamp=%s request_id=%s agent=%s status=%s duration_ms=%.1f "
        "category=%s endpoint=%s",
        datetime.now(timezone.utc).isoformat(),
        request_id,
        getattr(g, "selected_agent", "-"),
        status_code,
        duration_ms,
        getattr(g, "error_category", None) or "-",
        request.endpoint or "-",
    )
    return response


def json_error(category: str, message: str, status_code: int, retry_after: float | None = None):
    """Return the consistent, safe JSON error shape used by the frontend."""
    g.error_category = category
    response = jsonify(
        {
            "error": message,
            "code": category,
            "request_id": getattr(g, "request_id", None),
        }
    )
    if retry_after is not None:
        response.headers["Retry-After"] = str(max(0, int(retry_after)))
    return response, status_code


def get_api_key() -> str:
    """Return the Gemini API key from the backend/.env file."""
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key or api_key.lower() in {
        "your_key_here",
        "your_gemini_api_key",
        "replace_with_your_api_key",
        "paste_your_api_key_here",
    }:
        raise ConfigurationError(
            "Set a valid GEMINI_API_KEY in backend/.env. "
            "Create a key in Google AI Studio."
        )
    return api_key


def get_client() -> genai.Client:
    """Create the official Google Gemini SDK client using the API key in .env."""
    return genai.Client(
        api_key=get_api_key(),
        http_options=types.HttpOptions(
            timeout=GEMINI_TIMEOUT_MS,
            retry_options=types.HttpRetryOptions(attempts=0),
        ),
    )


def retry_after_seconds(error: APIError) -> float | None:
    """Read provider retry guidance from headers or google.rpc RetryInfo."""
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) or {}
    retry_after_ms = headers.get("retry-after-ms")
    if retry_after_ms:
        try:
            milliseconds = float(retry_after_ms)
            if milliseconds >= 0:
                return milliseconds / 1000
        except (TypeError, ValueError):
            pass

    retry_after = headers.get("retry-after")
    if retry_after:
        try:
            return max(0, float(retry_after))
        except (TypeError, ValueError):
            try:
                retry_date = parsedate_to_datetime(retry_after)
                return max(
                    0,
                    (retry_date - datetime.now(retry_date.tzinfo)).total_seconds(),
                )
            except (TypeError, ValueError, OverflowError):
                pass

    details = getattr(error, "details", None)
    if isinstance(details, dict):
        error_info = details.get("error")
        error_details = error_info.get("details", []) if isinstance(error_info, dict) else []
        if isinstance(error_details, list):
            for item in error_details:
                if not isinstance(item, dict) or not str(item.get("@type", "")).endswith("RetryInfo"):
                    continue
                delay = item.get("retryDelay")
                if isinstance(delay, str):
                    match = re.fullmatch(r"(\d+(?:\.\d+)?)s", delay)
                    if match:
                        return float(match.group(1))
    return None


def normalize_history(history: list) -> list[types.Content]:
    """Convert completed user/model turns into bounded Gemini SDK content."""
    complete_turns: list[tuple[str, str]] = []
    pending_user: str | None = None
    for item in history[-MAX_HISTORY_MESSAGES:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        raw_content = item.get("content", item.get("text", ""))
        if role not in {"user", "assistant", "ai", "model"} or not isinstance(raw_content, str):
            continue
        content = raw_content.strip()[:MAX_HISTORY_MESSAGE_LENGTH]
        if not content:
            continue
        if role == "user":
            pending_user = content
        elif pending_user is not None:
            complete_turns.append((pending_user, content))
            pending_user = None

    selected_turns: list[tuple[str, str]] = []
    total_length = 0
    for user_text, model_text in reversed(complete_turns):
        turn_length = len(user_text) + len(model_text)
        if total_length + turn_length > MAX_HISTORY_TOTAL_LENGTH:
            continue
        selected_turns.append((user_text, model_text))
        total_length += turn_length
    selected_turns.reverse()

    normalized: list[types.Content] = []
    for user_text, model_text in selected_turns:
        normalized.extend(
            (
                types.Content(role="user", parts=[types.Part(text=user_text)]),
                types.Content(role="model", parts=[types.Part(text=model_text)]),
            )
        )
    return normalized


def decode_attachments(value: object) -> list[Attachment]:
    """Validate and decode the small set of inline image/document types we support."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Attachments must be sent as a list of files.")
    if len(value) > MAX_ATTACHMENTS:
        raise ValueError(f"Please attach no more than {MAX_ATTACHMENTS} files.")

    attachments: list[Attachment] = []
    total_size = 0
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("One of the attached files is invalid.")
        raw_name = item.get("name")
        encoded_data = item.get("data")
        if not isinstance(raw_name, str) or not raw_name.strip() or len(raw_name) > 255:
            raise ValueError("One of the attached files has an invalid name.")
        if not isinstance(encoded_data, str) or len(encoded_data) > (MAX_ATTACHMENT_SIZE * 4 // 3) + 8:
            raise ValueError("Each file must be smaller than 10 MB.")

        name = Path(raw_name.replace("\\", "/")).name
        mime_type = ATTACHMENT_MIME_TYPES.get(Path(name).suffix.lower())
        if not mime_type or item.get("mimeType") != mime_type:
            raise ValueError("Supported files are PNG, JPG, WebP, PDF, TXT, and Markdown.")

        try:
            data = base64.b64decode(encoded_data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"The attached file {name} could not be decoded.") from exc
        if not data:
            raise ValueError(f"The attached file {name} is empty.")
        if len(data) > MAX_ATTACHMENT_SIZE:
            raise ValueError("Each file must be smaller than 10 MB.")

        total_size += len(data)
        if total_size > MAX_TOTAL_ATTACHMENT_SIZE:
            raise ValueError("Please keep all attached files under 20 MB total.")

        if mime_type == "image/png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"{name} is not a valid PNG image.")
        if mime_type == "image/jpeg" and not data.startswith(b"\xff\xd8\xff"):
            raise ValueError(f"{name} is not a valid JPG image.")
        if mime_type == "image/webp" and not (data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
            raise ValueError(f"{name} is not a valid WebP image.")
        if mime_type == "application/pdf" and not data.startswith(b"%PDF-"):
            raise ValueError(f"{name} is not a valid PDF document.")
        if mime_type.startswith("text/"):
            try:
                data.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ValueError(f"{name} must be a UTF-8 text file.") from exc

        attachments.append({"name": name, "mime_type": mime_type, "data": data})
    return attachments


def generate_reply(
    message: str,
    history: list | None = None,
    agent: AgentProfile | None = None,
    attachments: list[Attachment] | None = None,
) -> str:
    """Send the user's question and prior conversation to Gemini and return the answer."""
    selected_agent = agent or route_agent(message)
    client = get_client()
    base_contents = normalize_history(history)
    parts: list[types.Part] = [types.Part(text=message.strip())]
    for attachment in attachments or []:
        name = str(attachment["name"])
        mime_type = str(attachment["mime_type"])
        data = attachment["data"]
        if mime_type.startswith("text/"):
            document_text = data.decode("utf-8-sig")
            parts.append(types.Part(text=f"Attached document ({name}):\n{document_text}"))
        else:
            parts.append(types.Part(text=f"Attached file: {name}"))
            parts.append(types.Part.from_bytes(data=data, mime_type=mime_type))
    base_contents.append(types.Content(role="user", parts=parts))

    try:
        model_name = MODEL_CANDIDATES[0]
        for attempt in range(MAX_GENERATION_ATTEMPTS):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=base_contents,
                    config=types.GenerateContentConfig(
                        system_instruction=build_system_instruction(selected_agent)
                    ),
                )
            except APIError as exc:
                status_code = int(getattr(exc, "code", 0) or 0)
                retry_after = retry_after_seconds(exc)
                retryable_status = status_code in {408, 500, 502, 503, 504}
                if status_code == 429:
                    retryable_status = retry_after is not None
                if not retryable_status or attempt + 1 >= MAX_GENERATION_ATTEMPTS:
                    raise
                if retry_after is not None:
                    if retry_after > MAX_RETRY_DELAY_SECONDS:
                        raise
                    delay = retry_after
                else:
                    delay = random.uniform(0.25, min(1.0, 0.5 * (2**attempt)))
                app.logger.warning(
                    "request_id=%s category=upstream_transient status=%s "
                    "attempt=%s retry_delay_seconds=%.2f",
                    current_request_id(),
                    status_code,
                    attempt + 1,
                    delay,
                )
                time.sleep(delay)
                if status_code in {500, 502, 503, 504} and len(MODEL_CANDIDATES) > 1:
                    model_name = MODEL_CANDIDATES[1]
                continue
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt + 1 >= MAX_GENERATION_ATTEMPTS:
                    raise
                delay = random.uniform(0.25, min(1.0, 0.5 * (2**attempt)))
                app.logger.warning(
                    "request_id=%s category=network_transient error_type=%s "
                    "attempt=%s retry_delay_seconds=%.2f",
                    current_request_id(),
                    type(exc).__name__,
                    attempt + 1,
                    delay,
                )
                time.sleep(delay)
                continue

            text = getattr(response, "text", None)
            if not text:
                candidates = getattr(response, "candidates", None) or []
                if candidates:
                    candidate = candidates[0]
                    parts = getattr(getattr(candidate, "content", None), "parts", []) or []
                    text = "".join(
                        part_text
                        for part in parts
                        if (part_text := getattr(part, "text", None))
                    )
                    if not text:
                        finish_reason = str(getattr(candidate, "finish_reason", "")).upper()
                        if finish_reason and finish_reason not in {
                            "STOP",
                            "FINISHREASON.STOP",
                            "MAX_TOKENS",
                            "FINISHREASON.MAX_TOKENS",
                        }:
                            raise EmptyModelResponseError(
                                "The AI could not provide a response to that prompt. "
                                "Try rephrasing it."
                            )
            if not isinstance(text, str) or not text.strip():
                raise EmptyModelResponseError(
                    "The AI returned an empty response. Please try again."
                )
            return text.strip()
        raise EmptyModelResponseError(
            "The AI could not provide a response. Please try again."
        )
    finally:
        client.close()


@app.route("/")
def index():
    """Serve the frontend entry page for the application."""
    return send_from_directory(str(FRONTEND_DIR), "index.html")


@app.errorhandler(RequestEntityTooLarge)
def request_too_large(error):
    app.logger.warning(
        "request_id=%s category=request_too_large error_type=%s",
        current_request_id(),
        type(error).__name__,
    )
    return json_error("request_too_large", "The request is too large. Please shorten your message.", 413)


@app.errorhandler(HTTPException)
def handle_http_exception(error):
    if request.path == "/chat":
        return json_error(
            "http_error",
            error.description or "The request could not be completed.",
            error.code or 500,
        )
    return error


@app.route("/chat", methods=["POST"])
def chat():
    """API endpoint: receives the prompt and conversation history, then returns Gemini output."""
    if not request.is_json:
        return json_error("invalid_request", "Invalid request format. Send a JSON request.", 400)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return json_error("invalid_json", "Invalid JSON payload.", 400)

    unexpected_fields = set(payload) - {"message", "history", "attachments", "agent"}
    if unexpected_fields:
        return json_error("invalid_request", "The request contains unsupported fields.", 400)

    message = payload.get("message")
    if not isinstance(message, str):
        return json_error("invalid_message", "Please enter a valid text message.", 400)
    message = message.strip()
    if not message:
        return json_error("empty_message", "Please enter a message before sending.", 400)
    if len(message) > MAX_MESSAGE_LENGTH:
        return json_error("message_too_long", "Please keep messages under 12,000 characters.", 400)

    history = payload.get("history", [])
    if not isinstance(history, list):
        return json_error("invalid_history", "Conversation history is invalid.", 400)
    if len(history) > MAX_HISTORY_MESSAGES:
        return json_error(
            "history_too_long",
            "Please keep conversation history to the 20 most recent messages.",
            400,
        )
    if any(
        not isinstance(item, dict)
        or not isinstance(item.get("role"), str)
        or item.get("role") not in {"user", "assistant", "model", "ai"}
        or not isinstance(item.get("content", item.get("text", "")), str)
        for item in history
    ):
        return json_error("invalid_history", "Conversation history is invalid.", 400)

    try:
        attachments = decode_attachments(payload.get("attachments", []))
    except ValueError as exc:
        return json_error("invalid_attachment", str(exc), 400)

    requested_agent = payload.get("agent")
    if requested_agent is None:
        agent = route_agent(message)
    elif isinstance(requested_agent, str):
        normalized_agent = requested_agent.strip().lower()
        agent_key = next(
            (
                key
                for key, profile in AGENTS.items()
                if normalized_agent in {key.lower(), profile.name.lower()}
            ),
            None,
        )
        if agent_key is None:
            return json_error("invalid_agent", "The requested assistant agent is invalid.", 400)
        agent = AGENTS[agent_key]
    else:
        return json_error("invalid_agent", "The requested assistant agent is invalid.", 400)

    g.selected_agent = agent.name
    try:
        reply = generate_reply(message, history, agent, attachments)
        return jsonify(
            {
                "reply": reply,
                "agent": agent.name,
                "request_id": g.request_id,
            }
        )
    except ConfigurationError:
        return json_error(
            "configuration_error",
            "Gemini is not configured. Add a valid GEMINI_API_KEY to backend/.env and restart Flask.",
            503,
        )
    except APIError as exc:
        status_code = int(getattr(exc, "code", 0) or 0)
        retry_after = retry_after_seconds(exc)
        app.logger.warning(
            "request_id=%s category=gemini_api_error status=%s provider_status=%s "
            "error_type=%s traceback_frames=%s",
            g.request_id,
            status_code or "unknown",
            getattr(exc, "status", None) or "unknown",
            type(exc).__name__,
            "".join(traceback.format_list(traceback.extract_tb(exc.__traceback__))),
        )
        messages = {
            400: (400, "invalid_upstream_request", "Gemini rejected the request parameters. Check the request or configured model."),
            401: (401, "ai_authentication", "Gemini authentication failed. Check the API key in backend/.env."),
            403: (403, "ai_permission", "The Gemini API key does not have permission to use this service."),
            404: (404, "model_not_found", "The configured Gemini model or resource was not found. Check its name and API availability."),
            429: (429, "rate_or_quota_limit", "Gemini's current request or quota limit has been reached. Wait before retrying or check usage and billing."),
            408: (504, "upstream_timeout", "Gemini took too long to respond. Please try again."),
            500: (500, "upstream_server_error", "Gemini encountered a temporary server error. Please try again shortly."),
            502: (502, "upstream_gateway_error", "Gemini's gateway had a temporary error. Please try again shortly."),
            503: (503, "upstream_unavailable", "Gemini is temporarily unavailable. Please try again shortly."),
            504: (504, "upstream_timeout", "Gemini timed out. Please try again shortly."),
        }
        status, category, message = messages.get(
            status_code,
            (502, "upstream_error", "Gemini could not complete the request. Please try again later."),
        )
        return json_error(category, message, status, retry_after=retry_after)
    except (httpx.TimeoutException, TimeoutError) as exc:
        app.logger.warning(
            "request_id=%s category=network_timeout status=504 error_type=%s "
            "traceback_frames=%s",
            g.request_id,
            type(exc).__name__,
            "".join(traceback.format_list(traceback.extract_tb(exc.__traceback__))),
        )
        return json_error(
            "network_timeout",
            "The connection to Gemini timed out. Please try again.",
            504,
        )
    except (httpx.RequestError, ConnectionError) as exc:
        app.logger.warning(
            "request_id=%s category=network_failure status=503 error_type=%s "
            "traceback_frames=%s",
            g.request_id,
            type(exc).__name__,
            "".join(traceback.format_list(traceback.extract_tb(exc.__traceback__))),
        )
        return json_error(
            "network_failure",
            "Could not connect to Gemini. Check the network and try again.",
            503,
        )
    except EmptyModelResponseError as exc:
        app.logger.warning(
            "request_id=%s category=empty_model_response status=502",
            g.request_id,
        )
        return json_error("empty_model_response", str(exc), 502)
    except Exception as exc:
        frames = "".join(traceback.format_list(traceback.extract_tb(exc.__traceback__)))
        app.logger.error(
            "request_id=%s category=unexpected_error status=500 error_type=%s "
            "traceback_frames=%s",
            g.request_id,
            type(exc).__name__,
            frames,
        )
        return json_error(
            "internal_error",
            "Something went wrong while contacting the AI. Please try again later.",
            500,
        )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
