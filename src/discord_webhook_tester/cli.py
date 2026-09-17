import argparse
import json
import os
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from discord_webhook_tester.client import (
    MAX_FILES,
    DiscordResult,
    DiscordWebhookClient,
    InvalidWebhookUrlError,
    WebhookAddress,
    parse_webhook_url,
)
from discord_webhook_tester.payload import (
    InvalidColorError,
    build_default_test_payload,
    build_embed,
    current_timestamp,
    parse_color,
    validate_payload,
)

PROGRAM_NAME = "discord-webhook-tester"
WEBHOOK_URL_ENVIRONMENT_VARIABLE = "DISCORD_WEBHOOK_URL"
SUPPRESS_NOTIFICATIONS_FLAG = 1 << 12
INLINE_FIELD_SUFFIX = "|inline"
EXIT_OK = 0
EXIT_REQUEST_FAILED = 1
EXIT_INVALID_INPUT = 2


class InvalidInputError(Exception):
    pass


class Terminal:
    def __init__(self, stream):
        self._stream = stream
        self._use_color = stream.isatty() and "NO_COLOR" not in os.environ

    def _paint(self, text: str, color_code: str) -> str:
        return f"\033[{color_code}m{text}\033[0m" if self._use_color else text

    def success(self, text: str) -> None:
        print(self._paint(f"✓ {text}", "32"), file=self._stream)

    def failure(self, text: str) -> None:
        print(self._paint(f"✗ {text}", "31"), file=self._stream)

    def detail(self, label: str, value) -> None:
        print(f"  {self._paint(label.ljust(11), '2')} {value}", file=self._stream)

    def line(self, text: str = "") -> None:
        print(text, file=self._stream)


def get_program_version() -> str:
    try:
        return version(PROGRAM_NAME)
    except PackageNotFoundError:
        return "0.0.0+local"


def parse_field_argument(field_argument: str) -> dict:
    # Only the first "|" separates name and value, so values may contain pipes (Discord spoilers: ||text||).
    field_text = field_argument.strip()
    is_inline = field_text.lower().endswith(INLINE_FIELD_SUFFIX)
    if is_inline:
        field_text = field_text[: -len(INLINE_FIELD_SUFFIX)]
    field_name, separator, field_value = field_text.partition("|")
    field_name, field_value = field_name.strip(), field_value.strip()
    if not separator or not field_name or not field_value:
        raise InvalidInputError(f"Invalid --field {field_argument!r}: expected 'NAME|VALUE' or 'NAME|VALUE|inline'")
    embed_field = {"name": field_name, "value": field_value}
    if is_inline:
        embed_field["inline"] = True
    return embed_field


def load_payload_file(payload_file: str) -> dict:
    try:
        payload_text = sys.stdin.read() if payload_file == "-" else Path(payload_file).read_text(encoding="utf-8")
        payload = json.loads(payload_text)
    except OSError as os_error:
        raise InvalidInputError(f"Cannot read payload file: {os_error}") from None
    except json.JSONDecodeError as json_error:
        raise InvalidInputError(f"Payload file is not valid JSON: {json_error}") from None
    if not isinstance(payload, dict):
        raise InvalidInputError("Payload file must contain a JSON object")
    return payload


def build_payload_from_arguments(arguments: argparse.Namespace) -> dict:
    payload = load_payload_file(arguments.payload_file) if arguments.payload_file else {}
    if not isinstance(payload.get("embeds", []), list):
        raise InvalidInputError("Payload file: embeds must be a list")
    payload_flags = payload.get("flags", 0)
    if not isinstance(payload_flags, int) or isinstance(payload_flags, bool):
        raise InvalidInputError("Payload file: flags must be an integer")

    if arguments.content:
        payload["content"] = arguments.content
    if arguments.username:
        payload["username"] = arguments.username
    if arguments.avatar_url:
        payload["avatar_url"] = arguments.avatar_url
    if arguments.thread_name:
        payload["thread_name"] = arguments.thread_name
    if arguments.tts:
        payload["tts"] = True
    if arguments.silent:
        payload["flags"] = payload.get("flags", 0) | SUPPRESS_NOTIFICATIONS_FLAG

    try:
        embed_color = parse_color(arguments.color) if arguments.color else None
    except InvalidColorError as color_error:
        raise InvalidInputError(str(color_error)) from None
    embed = build_embed(
        title=arguments.title,
        description=arguments.description,
        url=arguments.url,
        color=embed_color,
        timestamp=current_timestamp() if arguments.timestamp == "now" else arguments.timestamp,
        author_name=arguments.author,
        author_url=arguments.author_url,
        author_icon_url=arguments.author_icon,
        footer_text=arguments.footer,
        footer_icon_url=arguments.footer_icon,
        image_url=arguments.image,
        thumbnail_url=arguments.thumbnail,
        fields=[parse_field_argument(field_argument) for field_argument in arguments.field],
    )
    if embed is not None:
        payload.setdefault("embeds", []).append(embed)
    elif arguments.color or arguments.timestamp or arguments.url:
        raise InvalidInputError(
            "--color, --timestamp and --url need a visible embed part: add --title, --description or another embed option"
        )

    has_message_body = payload.get("content") or payload.get("embeds") or arguments.file
    if not arguments.payload_file and not has_message_body:
        payload.update(build_default_test_payload())
    return payload


def resolve_webhook_address(arguments: argparse.Namespace) -> WebhookAddress:
    webhook_url = arguments.webhook_url or os.environ.get(WEBHOOK_URL_ENVIRONMENT_VARIABLE)
    if not webhook_url:
        raise InvalidInputError(
            f"No webhook URL: pass it as an argument or set {WEBHOOK_URL_ENVIRONMENT_VARIABLE}"
        )
    try:
        return parse_webhook_url(webhook_url)
    except InvalidWebhookUrlError as url_error:
        raise InvalidInputError(str(url_error)) from None


def resolve_file_paths(file_arguments: list[str]) -> list[Path]:
    if len(file_arguments) > MAX_FILES:
        raise InvalidInputError(f"Too many files: Discord accepts at most {MAX_FILES} per message")
    file_paths = [Path(file_argument) for file_argument in file_arguments]
    for file_path in file_paths:
        if not file_path.is_file():
            raise InvalidInputError(f"File not found: {file_path}")
    return file_paths


def report_result(result: DiscordResult, *, success_text: str, details: list[tuple[str, object]]) -> int:
    if result.ok:
        terminal = Terminal(sys.stdout)
        terminal.success(f"{success_text} (HTTP {result.status_code}, {result.elapsed_ms} ms)")
        for label, value in details:
            if value is not None:
                terminal.detail(label, value)
        if result.attempts > 1:
            terminal.detail("attempts", f"{result.attempts} (retried after a rate limit)")
        return EXIT_OK

    terminal = Terminal(sys.stderr)
    status_text = f"HTTP {result.status_code}" if result.status_code else "no response"
    terminal.failure(f"{result.error} ({status_text})")
    for error_detail in result.error_details:
        terminal.detail("detail", error_detail)
    if result.hint:
        terminal.detail("hint", result.hint)
    return EXIT_REQUEST_FAILED


def run_send(arguments: argparse.Namespace) -> int:
    payload = build_payload_from_arguments(arguments)
    file_paths = resolve_file_paths(arguments.file)

    if not arguments.no_validate:
        problems = validate_payload(payload, has_files=bool(file_paths))
        if problems:
            terminal = Terminal(sys.stderr)
            terminal.failure("Payload breaks Discord's limits and was not sent:")
            for problem in problems:
                terminal.detail("problem", problem)
            terminal.line("  Use --no-validate to send it anyway and see Discord's own answer.")
            return EXIT_INVALID_INPUT

    if arguments.dry_run:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return EXIT_OK

    address = resolve_webhook_address(arguments)
    try:
        with DiscordWebhookClient(timeout_seconds=arguments.timeout) as client:
            result = client.send_message(address, payload, thread_id=arguments.thread_id, file_paths=file_paths)
    except OSError as os_error:
        raise InvalidInputError(f"Cannot read attachment: {os_error}") from None

    if arguments.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return EXIT_OK if result.ok else EXIT_REQUEST_FAILED
    message = result.data or {}
    return report_result(
        result,
        success_text="Message sent",
        details=[
            ("message id", message.get("id")),
            ("channel id", message.get("channel_id")),
            ("webhook", address.masked_url),
        ],
    )


def run_info(arguments: argparse.Namespace) -> int:
    address = resolve_webhook_address(arguments)
    with DiscordWebhookClient(timeout_seconds=arguments.timeout) as client:
        result = client.get_webhook_info(address)

    if arguments.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return EXIT_OK if result.ok else EXIT_REQUEST_FAILED
    webhook = result.data or {}
    return report_result(
        result,
        success_text="Webhook is valid",
        details=[
            ("name", webhook.get("name")),
            ("webhook id", webhook.get("id")),
            ("channel id", webhook.get("channel_id")),
            ("guild id", webhook.get("guild_id")),
        ],
    )


def run_serve(arguments: argparse.Namespace) -> int:
    import uvicorn

    from discord_webhook_tester.web import create_app

    is_wildcard_host = arguments.host in ("0.0.0.0", "::")
    browser_host = "127.0.0.1" if is_wildcard_host else arguments.host
    if ":" in browser_host:
        browser_host = f"[{browser_host}]"
    page_url = f"http://{browser_host}:{arguments.port}"
    if arguments.open:
        import threading
        import webbrowser

        threading.Timer(1.0, webbrowser.open, args=(page_url,)).start()
    print(f"Discord Webhook Tester is running at {page_url} (Ctrl+C to stop)")
    uvicorn.run(create_app(), host=arguments.host, port=arguments.port, log_level="warning")
    return EXIT_OK


def add_connection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "webhook_url",
        nargs="?",
        help=f"Discord webhook URL (default: ${WEBHOOK_URL_ENVIRONMENT_VARIABLE})",
    )
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    parser.add_argument("--timeout", type=float, default=15.0, metavar="SECONDS", help="request timeout (default: 15)")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM_NAME,
        description="Send a test message to a Discord webhook, from the terminal or a local web form.",
        epilog="Created by Webhooker — https://webhooker.eu/",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {get_program_version()}")
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    send_parser = subparsers.add_parser(
        "send",
        help="send a message (a default test embed when no content is given)",
        description="Send a message to a Discord webhook. Without content or embed options a default test embed is sent.",
    )
    add_connection_arguments(send_parser)
    message_group = send_parser.add_argument_group("message")
    message_group.add_argument("-c", "--content", help="message text (up to 2000 characters)")
    message_group.add_argument("--username", help="override the webhook's name")
    message_group.add_argument("--avatar-url", help="override the webhook's avatar")
    message_group.add_argument("--tts", action="store_true", help="send as a text-to-speech message")
    message_group.add_argument("--silent", action="store_true", help="do not trigger push or desktop notifications")
    message_group.add_argument("--thread-id", help="post into an existing thread")
    message_group.add_argument("--thread-name", help="create a thread with this name (forum and media channels)")
    message_group.add_argument(
        "--file", action="append", default=[], metavar="PATH", help=f"attach a file (repeatable, up to {MAX_FILES})"
    )

    embed_group = send_parser.add_argument_group("embed")
    embed_group.add_argument("--title", help="embed title")
    embed_group.add_argument("--description", help="embed description (Markdown)")
    embed_group.add_argument("--url", help="link opened by the embed title")
    embed_group.add_argument("--color", help="#RRGGBB, a decimal integer or a name: blurple, green, yellow, red…")
    embed_group.add_argument("--timestamp", metavar="now|ISO8601", help="embed timestamp")
    embed_group.add_argument("--author", help="author name")
    embed_group.add_argument("--author-url", help="link opened by the author name")
    embed_group.add_argument("--author-icon", metavar="URL", help="author icon")
    embed_group.add_argument("--footer", help="footer text")
    embed_group.add_argument("--footer-icon", metavar="URL", help="footer icon")
    embed_group.add_argument("--image", metavar="URL", help="large image")
    embed_group.add_argument("--thumbnail", metavar="URL", help="small image in the top right corner")
    embed_group.add_argument(
        "--field", action="append", default=[], metavar="NAME|VALUE[|inline]", help="embed field (repeatable)"
    )

    advanced_group = send_parser.add_argument_group("advanced")
    advanced_group.add_argument(
        "--payload-file", metavar="PATH", help="raw JSON payload to start from ('-' reads stdin); other options add to it"
    )
    advanced_group.add_argument("--dry-run", action="store_true", help="print the JSON payload and exit without sending")
    advanced_group.add_argument("--no-validate", action="store_true", help="skip local checks against Discord's limits")
    send_parser.set_defaults(handler=run_send)

    info_parser = subparsers.add_parser(
        "info",
        help="check that a webhook exists without posting anything",
        description="Look up a webhook: confirms the URL is valid and shows its name, channel and server.",
    )
    add_connection_arguments(info_parser)
    info_parser.set_defaults(handler=run_info)

    serve_parser = subparsers.add_parser(
        "serve",
        help="start the local web form with the embed builder",
        description="Start the web form with a visual embed builder and live preview.",
    )
    serve_parser.add_argument("--host", default="127.0.0.1", help="address to listen on (default: 127.0.0.1)")
    serve_parser.add_argument("--port", type=int, default=8080, help="port to listen on (default: 8080)")
    serve_parser.add_argument("--open", action="store_true", help="open the form in the default browser")
    serve_parser.set_defaults(handler=run_serve)
    return parser


def main(argument_list: list[str] | None = None) -> int:
    arguments = build_argument_parser().parse_args(argument_list)
    try:
        return arguments.handler(arguments)
    except InvalidInputError as input_error:
        Terminal(sys.stderr).failure(str(input_error))
        return EXIT_INVALID_INPUT
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
