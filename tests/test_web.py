import pytest
from fastapi.testclient import TestClient

from conftest import WEBHOOK_URL
from discord_webhook_tester.web import create_app


@pytest.fixture
def web_client(fake_discord):
    with TestClient(create_app(fake_discord.build_client(max_rate_limit_retries=0))) as test_client:
        yield test_client


def test_index_page_and_health_check(web_client):
    index_response = web_client.get("/")
    assert index_response.status_code == 200
    assert "Discord Webhook Tester" in index_response.text
    assert web_client.get("/healthz").json() == {"status": "ok"}


def test_send_forwards_the_payload_to_discord(web_client, fake_discord):
    response = web_client.post(
        "/api/send",
        json={"webhook_url": WEBHOOK_URL, "payload": {"content": "hello"}, "thread_id": "42"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert fake_discord.last_json == {"content": "hello"}
    assert fake_discord.requests[0].url.params["thread_id"] == "42"


@pytest.mark.parametrize(
    "webhook_url",
    ["http://127.0.0.1:8000/api/webhooks/1/token", "https://evil.example/api/webhooks/1/token"],
)
def test_send_refuses_urls_that_are_not_discord_webhooks(web_client, fake_discord, webhook_url):
    response = web_client.post("/api/send", json={"webhook_url": webhook_url, "payload": {"content": "hello"}})

    assert response.status_code == 400
    assert response.json()["error"] == "Not a Discord webhook URL"
    assert fake_discord.requests == []


def test_send_reports_validation_problems(web_client, fake_discord):
    response = web_client.post("/api/send", json={"webhook_url": WEBHOOK_URL, "payload": {"content": "x" * 2001}})

    assert response.status_code == 400
    assert response.json()["error_details"] == ["content is 2001 characters, the limit is 2000"]
    assert fake_discord.requests == []


def test_send_passes_discord_errors_through(web_client, fake_discord):
    fake_discord.queue(404, {"message": "Unknown Webhook", "code": 10015})
    result = web_client.post("/api/send", json={"webhook_url": WEBHOOK_URL, "payload": {"content": "hello"}}).json()

    assert result["ok"] is False and result["status_code"] == 404
    assert "deleted or the URL is wrong" in result["hint"]


def test_info_hides_the_token(web_client, fake_discord):
    fake_discord.queue(200, {"id": "1", "name": "Alerts", "channel_id": "2", "guild_id": "3", "token": "secret"})
    result = web_client.post("/api/info", json={"webhook_url": WEBHOOK_URL}).json()

    assert result["ok"] is True
    assert result["data"] == {"id": "1", "name": "Alerts", "channel_id": "2", "guild_id": "3"}
