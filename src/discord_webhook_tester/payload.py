from datetime import datetime, timezone
from urllib.parse import urlsplit

CONTENT_MAX_LENGTH = 2000
USERNAME_MAX_LENGTH = 80
MAX_EMBEDS = 10
MAX_EMBED_FIELDS = 25
EMBED_TITLE_MAX_LENGTH = 256
EMBED_DESCRIPTION_MAX_LENGTH = 4096
EMBED_FIELD_NAME_MAX_LENGTH = 256
EMBED_FIELD_VALUE_MAX_LENGTH = 1024
EMBED_FOOTER_MAX_LENGTH = 2048
EMBED_AUTHOR_MAX_LENGTH = 256
EMBEDS_TOTAL_MAX_LENGTH = 6000
MAX_COLOR_VALUE = 0xFFFFFF

NAMED_COLORS = {
    "blurple": 0x5865F2,
    "green": 0x57F287,
    "yellow": 0xFEE75C,
    "fuchsia": 0xEB459E,
    "red": 0xED4245,
    "white": 0xFFFFFF,
    "black": 0x23272A,
}


class InvalidColorError(ValueError):
    pass


def parse_color(color_text: str) -> int:
    """Accepts a color name, #RRGGBB, 0xRRGGBB, RRGGBB or a decimal integer."""
    normalized = color_text.strip().lower()
    if normalized in NAMED_COLORS:
        return NAMED_COLORS[normalized]
    if normalized.startswith("#") or normalized.startswith("0x"):
        hex_digits = normalized.removeprefix("#").removeprefix("0x")
        if len(hex_digits) == 3:
            hex_digits = "".join(digit * 2 for digit in hex_digits)
        elif len(hex_digits) != 6:
            hex_digits = "invalid"
    elif len(normalized) == 6:
        # A bare six-character value is read as RRGGBB, the way colors are usually copied.
        hex_digits = normalized
    else:
        hex_digits = None
    try:
        color_value = int(hex_digits, 16) if hex_digits is not None else int(normalized)
    except ValueError:
        known_names = ", ".join(NAMED_COLORS)
        raise InvalidColorError(
            f"Invalid color {color_text!r}: use #RRGGBB, a decimal integer or one of: {known_names}"
        ) from None
    if not 0 <= color_value <= MAX_COLOR_VALUE:
        raise InvalidColorError(f"Invalid color {color_text!r}: must be between #000000 and #FFFFFF")
    return color_value


def current_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_embed(
    *,
    title: str | None = None,
    description: str | None = None,
    url: str | None = None,
    color: int | None = None,
    timestamp: str | None = None,
    author_name: str | None = None,
    author_url: str | None = None,
    author_icon_url: str | None = None,
    footer_text: str | None = None,
    footer_icon_url: str | None = None,
    image_url: str | None = None,
    thumbnail_url: str | None = None,
    fields: list[dict] | None = None,
) -> dict | None:
    """Builds a Discord embed object, omitting empty parts. Returns None when nothing was set."""
    embed: dict = {}
    if title:
        embed["title"] = title
    if description:
        embed["description"] = description
    if url:
        embed["url"] = url
    if color is not None:
        embed["color"] = color
    if timestamp:
        embed["timestamp"] = timestamp
    if author_name:
        author = {"name": author_name, "url": author_url, "icon_url": author_icon_url}
        embed["author"] = {key: value for key, value in author.items() if value}
    if footer_text:
        footer = {"text": footer_text, "icon_url": footer_icon_url}
        embed["footer"] = {key: value for key, value in footer.items() if value}
    if image_url:
        embed["image"] = {"url": image_url}
    if thumbnail_url:
        embed["thumbnail"] = {"url": thumbnail_url}
    if fields:
        embed["fields"] = fields

    has_visible_part = any(key not in ("color", "timestamp", "url") for key in embed)
    return embed if has_visible_part else None


def build_default_test_payload() -> dict:
    return {
        "embeds": [
            {
                "title": "Webhook is working",
                "description": "This test message was sent with **discord-webhook-tester**.",
                "color": NAMED_COLORS["green"],
                "timestamp": current_timestamp(),
                "footer": {"text": "discord-webhook-tester · webhooker.eu"},
            }
        ]
    }


def _is_text(value) -> bool:
    return isinstance(value, str)


def _check_length(problems: list[str], label: str, value, max_length: int) -> int:
    if value is None:
        return 0
    if not _is_text(value):
        problems.append(f"{label} must be a string")
        return 0
    if len(value) > max_length:
        problems.append(f"{label} is {len(value)} characters, the limit is {max_length}")
    return len(value)


def _check_url(problems: list[str], label: str, value, *, allow_attachment: bool = False) -> None:
    if value is None:
        return
    if not _is_text(value):
        problems.append(f"{label} must be a string")
        return
    if allow_attachment and value.startswith("attachment://"):
        return
    try:
        parsed_url = urlsplit(value)
        is_http_url = parsed_url.scheme in ("http", "https") and bool(parsed_url.netloc)
    except ValueError:
        is_http_url = False
    if not is_http_url:
        problems.append(f"{label} must be an http(s) URL")


def _check_timestamp(problems: list[str], label: str, value) -> None:
    if value is None:
        return
    try:
        datetime.fromisoformat(value)
    except (TypeError, ValueError):
        problems.append(f"{label} must be an ISO 8601 timestamp, for example 2026-01-31T12:00:00+00:00")


def _section(embed: dict, problems: list[str], label: str, key: str) -> dict:
    section = embed.get(key)
    if section is None:
        return {}
    if not isinstance(section, dict):
        problems.append(f"{label} {key} must be an object")
        return {}
    return section


def _validate_embed(problems: list[str], label: str, embed) -> int:
    if not isinstance(embed, dict):
        problems.append(f"{label} must be an object")
        return 0

    author = _section(embed, problems, label, "author")
    footer = _section(embed, problems, label, "footer")
    image = _section(embed, problems, label, "image")
    thumbnail = _section(embed, problems, label, "thumbnail")

    character_count = 0
    character_count += _check_length(problems, f"{label} title", embed.get("title"), EMBED_TITLE_MAX_LENGTH)
    character_count += _check_length(
        problems, f"{label} description", embed.get("description"), EMBED_DESCRIPTION_MAX_LENGTH
    )
    character_count += _check_length(problems, f"{label} author name", author.get("name"), EMBED_AUTHOR_MAX_LENGTH)
    character_count += _check_length(problems, f"{label} footer text", footer.get("text"), EMBED_FOOTER_MAX_LENGTH)

    _check_url(problems, f"{label} url", embed.get("url"))
    _check_url(problems, f"{label} author url", author.get("url"))
    _check_url(problems, f"{label} author icon_url", author.get("icon_url"), allow_attachment=True)
    _check_url(problems, f"{label} footer icon_url", footer.get("icon_url"), allow_attachment=True)
    _check_url(problems, f"{label} image url", image.get("url"), allow_attachment=True)
    _check_url(problems, f"{label} thumbnail url", thumbnail.get("url"), allow_attachment=True)
    _check_timestamp(problems, f"{label} timestamp", embed.get("timestamp"))

    color = embed.get("color")
    if color is not None:
        is_valid_color = isinstance(color, int) and not isinstance(color, bool) and 0 <= color <= MAX_COLOR_VALUE
        if not is_valid_color:
            problems.append(f"{label} color must be an integer between 0 and {MAX_COLOR_VALUE}")

    fields = embed.get("fields") or []
    if not isinstance(fields, list):
        problems.append(f"{label} fields must be a list")
        fields = []
    if len(fields) > MAX_EMBED_FIELDS:
        problems.append(f"{label} has {len(fields)} fields, the limit is {MAX_EMBED_FIELDS}")
    for field_number, field in enumerate(fields, start=1):
        field_label = f"{label} field {field_number}"
        if not isinstance(field, dict):
            problems.append(f"{field_label} must be an object")
            continue
        if not field.get("name") or not field.get("value"):
            problems.append(f"{field_label} needs both a name and a value")
        character_count += _check_length(
            problems, f"{field_label} name", field.get("name"), EMBED_FIELD_NAME_MAX_LENGTH
        )
        character_count += _check_length(
            problems, f"{field_label} value", field.get("value"), EMBED_FIELD_VALUE_MAX_LENGTH
        )
    return character_count


def validate_payload(payload, *, has_files: bool = False) -> list[str]:
    """Checks a webhook payload against Discord's documented limits.

    Returns human-readable problems; an empty list means the payload is safe to send.
    """
    if not isinstance(payload, dict):
        return ["Payload must be a JSON object"]

    problems: list[str] = []
    _check_length(problems, "content", payload.get("content"), CONTENT_MAX_LENGTH)
    _check_length(problems, "username", payload.get("username"), USERNAME_MAX_LENGTH)
    _check_url(problems, "avatar_url", payload.get("avatar_url"))

    embeds = payload.get("embeds") or []
    if not isinstance(embeds, list):
        problems.append("embeds must be a list")
        embeds = []
    if len(embeds) > MAX_EMBEDS:
        problems.append(f"Payload has {len(embeds)} embeds, the limit is {MAX_EMBEDS}")

    total_embed_characters = sum(
        _validate_embed(problems, f"Embed {embed_number}", embed)
        for embed_number, embed in enumerate(embeds, start=1)
    )
    if total_embed_characters > EMBEDS_TOTAL_MAX_LENGTH:
        problems.append(
            f"Embeds contain {total_embed_characters} characters in total, the limit is {EMBEDS_TOTAL_MAX_LENGTH}"
        )

    has_sendable_part = bool(
        payload.get("content") or embeds or payload.get("components") or payload.get("poll") or has_files
    )
    if not has_sendable_part:
        problems.append("Message is empty: add content, an embed or a file")
    return problems
