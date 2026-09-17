import httpx
import pytest

from conftest import WEBHOOK_URL
from discord_webhook_tester.client import (
    InvalidWebhookUrlError,
    flatten_discord_errors,
    parse_webhook_url,
)


@pytest.mark.parametrize(
    "webhook_url",
    [
        WEBHOOK_URL,
        "https://discordapp.com/api/webhooks/123456789012345678/abcDEF-token_value",
        "https://canary.discord.com/api/v10/webhooks/123456789012345678/abcDEF-token_value/",
        f"  {WEBHOOK_URL}?wait=true  ",
    ],
)
def test_parse_webhook_url_accepts_discord_urls(webhook_url):
    address = parse_webhook_url(webhook_url)
    assert address.webhook_id == "123456789012345678"
    assert address.token == "abcDEF-token_value"


@pytest.mark.parametrize(
    "webhook_url",
    [
        "",
        "http://discord.com/api/webhooks/1/token",
        "https://discord.com.evil.example/api/webhooks/1/token",
        "https://evil.example/api/webhooks/1/token",
        "https://discord.com@evil.example/api/webhooks/1/token",
        "https://discord.com:8443/api/webhooks/1/token",
        "https://discord.com/api/webhooks/1/token/../../users/@me",
        "https://discord.com/api/webhooks/not-a-number/token",
        "https://discord.com/api/webhooks/1/token/slack",
    ],
)
def test_parse_webhook_url_rejects_everything_else(webhook_url):
    with pytest.raises(InvalidWebhookUrlError):
        parse_webhook_url(webhook_url)


def test_thread_id_is_read_from_the_url():
    assert parse_webhook_url(f"{WEBHOOK_URL}?thread_id=42").thread_id == "42"
    assert parse_webhook_url(f"{WEBHOOK_URL}?thread_id=abc").thread_id is None


def test_token_is_never_shown_in_repr_or_masked_url():
    address = parse_webhook_url(WEBHOOK_URL)
    assert "token_value" not in repr(address)
    assert "token_value" not in address.masked_url


def test_send_message_posts_json_to_discord(fake_discord):
    with fake_discord.build_client() as client:
        result = client.send_message(parse_webhook_url(WEBHOOK_URL), {"content": "hello"}, thread_id="42")

    sent_request = fake_discord.requests[0]
    assert sent_request.method == "POST"
    assert sent_request.url.host == "discord.com"
    assert sent_request.url.path == "/api/v10/webhooks/123456789012345678/abcDEF-token_value"
    assert dict(sent_request.url.params) == {"wait": "true", "thread_id": "42"}
    assert fake_discord.last_json == {"content": "hello"}
    assert result.ok and result.status_code == 200 and result.data["id"] == "999"


def test_send_message_uploads_files_as_multipart(fake_discord, tmp_path):
    report_path = tmp_path / "report.txt"
    report_path.write_text("report body")
    with fake_discord.build_client() as client:
        result = client.send_message(parse_webhook_url(WEBHOOK_URL), {"content": "see file"}, file_paths=[report_path])

    sent_request = fake_discord.requests[0]
    assert sent_request.headers["content-type"].startswith("multipart/form-data")
    assert b'name="payload_json"' in sent_request.content
    assert b'name="files[0]"; filename="report.txt"' in sent_request.content
    assert b"report body" in sent_request.content
    assert result.ok


def test_rate_limit_is_retried_after_the_requested_delay(fake_discord):
    fake_discord.queue(429, {"message": "You are being rate limited.", "retry_after": 0.75, "global": False})
    with fake_discord.build_client() as client:
        result = client.send_message(parse_webhook_url(WEBHOOK_URL), {"content": "hello"})

    assert result.ok and result.attempts == 2
    assert fake_discord.sleeps == [0.75]


def test_rate_limit_is_reported_when_retries_are_disabled(fake_discord):
    fake_discord.queue(429, {"message": "You are being rate limited.", "retry_after": 2})
    with fake_discord.build_client(max_rate_limit_retries=0) as client:
        result = client.send_message(parse_webhook_url(WEBHOOK_URL), {"content": "hello"})

    assert not result.ok and result.status_code == 429
    assert result.retry_after_seconds == 2
    assert "wait 2.0 seconds" in result.hint
    assert fake_discord.sleeps == []


def test_long_rate_limits_are_not_slept_through(fake_discord):
    fake_discord.queue(429, {"message": "You are being rate limited.", "retry_after": 600})
    with fake_discord.build_client() as client:
        result = client.send_message(parse_webhook_url(WEBHOOK_URL), {"content": "hello"})

    assert not result.ok and fake_discord.sleeps == []


def test_unknown_webhook_gets_a_hint(fake_discord):
    fake_discord.queue(404, {"message": "Unknown Webhook", "code": 10015})
    with fake_discord.build_client() as client:
        result = client.send_message(parse_webhook_url(WEBHOOK_URL), {"content": "hello"})

    assert not result.ok
    assert result.error == "Unknown Webhook (code 10015)"
    assert "deleted or the URL is wrong" in result.hint


def test_validation_errors_are_flattened(fake_discord):
    fake_discord.queue(
        400,
        {
            "message": "Invalid Form Body",
            "code": 50035,
            "errors": {
                "embeds": {"0": {"title": {"_errors": [{"code": "BASE_TYPE_MAX_LENGTH", "message": "Too long."}]}}}
            },
        },
    )
    with fake_discord.build_client() as client:
        result = client.send_message(parse_webhook_url(WEBHOOK_URL), {"content": "hello"})

    assert result.error == "Invalid Form Body (code 50035)"
    assert result.error_details == ["embeds.0.title: Too long."]


def test_legacy_error_shape_is_flattened():
    assert flatten_discord_errors({"embeds": ["Must be 10 or fewer in length."]}) == [
        "embeds: Must be 10 or fewer in length."
    ]


def test_network_failure_is_reported_without_raising():
    def refuse_connection(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    from discord_webhook_tester.client import DiscordWebhookClient

    with DiscordWebhookClient(transport=httpx.MockTransport(refuse_connection)) as client:
        result = client.send_message(parse_webhook_url(WEBHOOK_URL), {"content": "hello"})

    assert not result.ok and result.status_code is None
    assert result.error == "Could not reach Discord: connection refused"


def test_webhook_info_never_returns_the_token(fake_discord):
    fake_discord.queue(
        200,
        {"id": "1", "name": "Alerts", "channel_id": "2", "guild_id": "3", "token": "secret", "url": "https://secret"},
    )
    with fake_discord.build_client() as client:
        result = client.get_webhook_info(parse_webhook_url(WEBHOOK_URL))

    assert fake_discord.requests[0].method == "GET"
    assert result.data == {"id": "1", "name": "Alerts", "channel_id": "2", "guild_id": "3"}


@pytest.mark.parametrize(
    "webhook_url",
    ["https://discord.com:99999/api/webhooks/1/token", "https://[discord.com/api/webhooks/1/token"],
)
def test_unparseable_urls_raise_the_domain_error(webhook_url):
    with pytest.raises(InvalidWebhookUrlError):
        parse_webhook_url(webhook_url)


def test_negative_retry_after_never_reaches_sleep(fake_discord):
    fake_discord.queue(429, {"message": "You are being rate limited.", "retry_after": -1})
    with fake_discord.build_client() as client:
        result = client.send_message(parse_webhook_url(WEBHOOK_URL), {"content": "hello"})

    assert result.ok and fake_discord.sleeps == [0.0]
