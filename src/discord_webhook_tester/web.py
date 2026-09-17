from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from discord_webhook_tester.client import (
    DiscordResult,
    DiscordWebhookClient,
    InvalidWebhookUrlError,
    WebhookAddress,
    parse_webhook_url,
)
from discord_webhook_tester.payload import validate_payload

STATIC_DIRECTORY = Path(__file__).parent / "static"
INDEX_PAGE_PATH = STATIC_DIRECTORY / "index.html"


class WebhookRequest(BaseModel):
    webhook_url: str = Field(max_length=500)


class SendRequest(WebhookRequest):
    payload: dict
    thread_id: str | None = Field(default=None, pattern=r"^\d{1,25}$")


def invalid_input_response(error: str, error_details: list[str] | None = None) -> JSONResponse:
    result = DiscordResult(ok=False, status_code=None, data=None, error=error, error_details=error_details or [])
    return JSONResponse(result.to_dict(), status_code=400)


def parse_address_or_none(webhook_url: str) -> WebhookAddress | None:
    try:
        return parse_webhook_url(webhook_url)
    except InvalidWebhookUrlError:
        return None


router = APIRouter()


@router.get("/", include_in_schema=False)
def index_page() -> FileResponse:
    return FileResponse(INDEX_PAGE_PATH, headers={"Cache-Control": "no-cache"})


@router.get("/healthz", include_in_schema=False)
def health_check() -> dict:
    return {"status": "ok"}


@router.post("/api/send")
def send_message(send_request: SendRequest, request: Request):
    address = parse_address_or_none(send_request.webhook_url)
    if address is None:
        return invalid_input_response("Not a Discord webhook URL")
    problems = validate_payload(send_request.payload)
    if problems:
        return invalid_input_response("Payload breaks Discord's limits and was not sent", problems)
    discord_client: DiscordWebhookClient = request.app.state.discord_client
    return discord_client.send_message(address, send_request.payload, thread_id=send_request.thread_id).to_dict()


@router.post("/api/info")
def get_webhook_info(webhook_request: WebhookRequest, request: Request):
    address = parse_address_or_none(webhook_request.webhook_url)
    if address is None:
        return invalid_input_response("Not a Discord webhook URL")
    discord_client: DiscordWebhookClient = request.app.state.discord_client
    return discord_client.get_webhook_info(address).to_dict()


def create_app(discord_client: DiscordWebhookClient | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        # The browser shows the rate limit instead of the server sleeping through it.
        application.state.discord_client = discord_client or DiscordWebhookClient(max_rate_limit_retries=0)
        try:
            yield
        finally:
            application.state.discord_client.close()

    application = FastAPI(title="Discord Webhook Tester", lifespan=lifespan)
    application.include_router(router)
    application.mount("/static", StaticFiles(directory=STATIC_DIRECTORY), name="static")
    return application
