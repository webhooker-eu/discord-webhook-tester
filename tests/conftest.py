import json

import httpx
import pytest

from discord_webhook_tester.client import DiscordWebhookClient

WEBHOOK_URL = "https://discord.com/api/webhooks/123456789012345678/abcDEF-token_value"


class FakeDiscord:
    """Records requests and answers them with queued responses."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.responses: list[httpx.Response] = []
        self.sleeps: list[float] = []

    def queue(self, status_code: int, body: dict | None = None, headers: dict | None = None) -> None:
        self.responses.append(httpx.Response(status_code, json=body, headers=headers))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.responses:
            return self.responses.pop(0)
        return httpx.Response(200, json={"id": "999", "channel_id": "555"})

    @property
    def last_json(self) -> dict:
        return json.loads(self.requests[-1].content)

    def build_client(self, **client_options) -> DiscordWebhookClient:
        return DiscordWebhookClient(
            transport=httpx.MockTransport(self.handle), sleep=self.sleeps.append, **client_options
        )


@pytest.fixture
def fake_discord() -> FakeDiscord:
    return FakeDiscord()
