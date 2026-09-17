import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_HOSTS = {
    "discord.com",
    "discordapp.com",
    "canary.discord.com",
    "ptb.discord.com",
    "canary.discordapp.com",
    "ptb.discordapp.com",
}
WEBHOOK_PATH_PATTERN = re.compile(r"^/api(?:/v\d+)?/webhooks/(?P<webhook_id>\d+)/(?P<token>[A-Za-z0-9_-]+)/?$")
MAX_FILES = 10
MAX_RETRY_AFTER_SECONDS = 30.0
USER_AGENT = "discord-webhook-tester (https://github.com/webhooker-eu/discord-webhook-tester)"

ERROR_HINTS = {
    10015: "The webhook does not exist: it was deleted or the URL is wrong.",
    50027: "The webhook token is invalid: copy the URL again from Discord.",
    50006: "Discord treated the message as empty: add content, an embed or a file.",
    220001: "This webhook posts to a forum channel: pass a thread name or a thread id.",
    220003: "This webhook does not post to a forum channel: remove the thread name.",
    10003: "The thread does not exist or does not belong to the webhook's channel.",
    40005: "The attachment is larger than this server allows.",
}


class InvalidWebhookUrlError(ValueError):
    pass


@dataclass(frozen=True)
class WebhookAddress:
    webhook_id: str
    token: str = field(repr=False)
    thread_id: str | None = None

    @property
    def masked_url(self) -> str:
        return f"https://discord.com/api/webhooks/{self.webhook_id}/{self.token[:4]}…"


@dataclass(frozen=True)
class DiscordResult:
    ok: bool
    status_code: int | None
    data: dict | None
    error: str | None = None
    error_details: list[str] = field(default_factory=list)
    hint: str | None = None
    retry_after_seconds: float | None = None
    attempts: int = 1
    elapsed_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "status_code": self.status_code,
            "data": self.data,
            "error": self.error,
            "error_details": self.error_details,
            "hint": self.hint,
            "retry_after_seconds": self.retry_after_seconds,
            "attempts": self.attempts,
            "elapsed_ms": self.elapsed_ms,
        }


def parse_webhook_url(webhook_url: str) -> WebhookAddress:
    """Extracts the webhook id and token, accepting only genuine Discord webhook URLs.

    Requests are later rebuilt from these parts, so a crafted URL can never point the
    client at another host.
    """
    invalid_url_error = InvalidWebhookUrlError(
        "Not a Discord webhook URL. Expected https://discord.com/api/webhooks/<id>/<token>"
    )
    try:
        parsed_url = urlsplit(webhook_url.strip())
        has_explicit_port = parsed_url.port is not None
    except ValueError:
        raise invalid_url_error from None
    path_match = WEBHOOK_PATH_PATTERN.match(parsed_url.path)
    is_discord_url = parsed_url.scheme == "https" and parsed_url.hostname in DISCORD_HOSTS and not has_explicit_port
    if not is_discord_url or path_match is None:
        raise invalid_url_error
    thread_ids = parse_qs(parsed_url.query).get("thread_id", [])
    thread_id = thread_ids[0] if thread_ids and thread_ids[0].isdecimal() else None
    return WebhookAddress(
        webhook_id=path_match["webhook_id"],
        token=path_match["token"],
        thread_id=thread_id,
    )


def flatten_discord_errors(errors, path: str = "") -> list[str]:
    """Turns Discord's nested validation errors into lines like 'embeds.0.title: Must be ...'."""
    if isinstance(errors, list):
        return [f"{path}: {message}" if path else str(message) for message in errors]
    if not isinstance(errors, dict):
        return []
    lines: list[str] = []
    for key, value in errors.items():
        if key == "_errors" and isinstance(value, list):
            for error in value:
                message = error.get("message", "") if isinstance(error, dict) else str(error)
                lines.append(f"{path}: {message}" if path else message)
        else:
            lines.extend(flatten_discord_errors(value, f"{path}.{key}" if path else str(key)))
    return lines


def _parse_json_object(response: httpx.Response) -> dict | None:
    try:
        parsed_body = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return parsed_body if isinstance(parsed_body, dict) else None


def _read_retry_after(response: httpx.Response, response_body: dict | None) -> float | None:
    candidates = [(response_body or {}).get("retry_after"), response.headers.get("Retry-After")]
    for candidate in candidates:
        try:
            return max(float(candidate), 0.0)
        except (TypeError, ValueError):
            continue
    return None


class DiscordWebhookClient:
    def __init__(
        self,
        *,
        api_base: str = DISCORD_API_BASE,
        timeout_seconds: float = 15.0,
        max_rate_limit_retries: int = 2,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._max_rate_limit_retries = max_rate_limit_retries
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=api_base,
            timeout=timeout_seconds,
            transport=transport,
            headers={"User-Agent": USER_AGENT},
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "DiscordWebhookClient":
        return self

    def __exit__(self, *exception_info) -> None:
        self.close()

    def send_message(
        self,
        address: WebhookAddress,
        payload: dict,
        *,
        thread_id: str | None = None,
        file_paths: list[Path] | None = None,
    ) -> DiscordResult:
        query = {"wait": "true"}
        effective_thread_id = thread_id or address.thread_id
        if effective_thread_id:
            query["thread_id"] = effective_thread_id

        request_arguments: dict = {"params": query}
        if file_paths:
            attachments = [(file_path.name, file_path.read_bytes()) for file_path in file_paths]
            request_arguments["data"] = {"payload_json": json.dumps(payload)}
            request_arguments["files"] = [
                (f"files[{file_index}]", attachment) for file_index, attachment in enumerate(attachments)
            ]
        else:
            request_arguments["json"] = payload
        return self._request("POST", address, **request_arguments)

    def get_webhook_info(self, address: WebhookAddress) -> DiscordResult:
        result = self._request("GET", address)
        if result.data is not None:
            # Discord echoes the secret token back; keep it out of terminals and logs.
            result.data.pop("token", None)
            result.data.pop("url", None)
        return result

    def _request(self, method: str, address: WebhookAddress, **request_arguments) -> DiscordResult:
        path = f"/webhooks/{address.webhook_id}/{address.token}"
        started_at = time.monotonic()
        attempts = 0
        while True:
            attempts += 1
            try:
                response = self._http.request(method, path, **request_arguments)
            except httpx.HTTPError as http_error:
                return DiscordResult(
                    ok=False,
                    status_code=None,
                    data=None,
                    error=f"Could not reach Discord: {str(http_error) or type(http_error).__name__}",
                    attempts=attempts,
                    elapsed_ms=round((time.monotonic() - started_at) * 1000),
                )
            response_body = _parse_json_object(response)
            retry_after_seconds = _read_retry_after(response, response_body) if response.status_code == 429 else None
            can_retry = (
                retry_after_seconds is not None
                and retry_after_seconds <= MAX_RETRY_AFTER_SECONDS
                and attempts <= self._max_rate_limit_retries
            )
            if not can_retry:
                break
            self._sleep(retry_after_seconds)

        elapsed_ms = round((time.monotonic() - started_at) * 1000)
        if response.is_success:
            return DiscordResult(
                ok=True, status_code=response.status_code, data=response_body, attempts=attempts, elapsed_ms=elapsed_ms
            )

        error_body = response_body or {}
        error_code = error_body.get("code")
        error_message = error_body.get("message") or response.reason_phrase or "Request failed"
        error_details = flatten_discord_errors(error_body.get("errors"))
        if not error_details and error_code is None:
            # Older validation responses are shaped like {"embeds": ["..."]} with no code.
            error_details = flatten_discord_errors(
                {key: value for key, value in error_body.items() if key not in ("message", "retry_after", "global")}
            )
        hint = ERROR_HINTS.get(error_code)
        if hint is None and response.status_code == 429:
            hint = f"Rate limited by Discord: wait {retry_after_seconds or 'a few'} seconds and try again."
        return DiscordResult(
            ok=False,
            status_code=response.status_code,
            data=response_body,
            error=f"{error_message} (code {error_code})" if error_code is not None else error_message,
            error_details=error_details,
            hint=hint,
            retry_after_seconds=retry_after_seconds,
            attempts=attempts,
            elapsed_ms=elapsed_ms,
        )
