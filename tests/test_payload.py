import pytest

from discord_webhook_tester.payload import (
    InvalidColorError,
    build_default_test_payload,
    build_embed,
    parse_color,
    validate_payload,
)


@pytest.mark.parametrize(
    ("color_text", "expected_value"),
    [("#5865F2", 0x5865F2), ("5865f2", 0x5865F2), ("0x5865F2", 0x5865F2), ("5793266", 5793266), ("Blurple", 0x5865F2)],
)
def test_parse_color_accepts_common_notations(color_text, expected_value):
    assert parse_color(color_text) == expected_value


@pytest.mark.parametrize("color_text", ["#GGGGGG", "purple-ish", "#1234567", "-1", ""])
def test_parse_color_rejects_invalid_values(color_text):
    with pytest.raises(InvalidColorError):
        parse_color(color_text)


def test_build_embed_omits_empty_parts():
    embed = build_embed(title="Title", author_name="Bot", author_url="", footer_text=None, color=0)
    assert embed == {"title": "Title", "color": 0, "author": {"name": "Bot"}}


def test_build_embed_returns_none_without_visible_content():
    assert build_embed(color=0x57F287, timestamp="2026-01-01T00:00:00+00:00", url="https://example.com") is None


def test_default_test_payload_is_valid():
    assert validate_payload(build_default_test_payload()) == []


def test_empty_message_is_rejected_unless_it_has_files():
    assert validate_payload({"username": "Bot"}) == ["Message is empty: add content, an embed or a file"]
    assert validate_payload({"username": "Bot"}, has_files=True) == []


def test_length_limits_are_reported_with_location():
    payload = {
        "content": "x" * 2001,
        "embeds": [{"title": "t" * 257, "fields": [{"name": "n", "value": "v" * 1025}]}],
    }
    assert validate_payload(payload) == [
        "content is 2001 characters, the limit is 2000",
        "Embed 1 title is 257 characters, the limit is 256",
        "Embed 1 field 1 value is 1025 characters, the limit is 1024",
    ]


def test_total_embed_length_is_limited_across_embeds():
    payload = {"embeds": [{"description": "d" * 4000}, {"description": "d" * 2001}]}
    assert validate_payload(payload) == ["Embeds contain 6001 characters in total, the limit is 6000"]


def test_structure_problems_are_reported():
    payload = {
        "content": "hi",
        "avatar_url": "javascript:alert(1)",
        "embeds": [
            {
                "title": "ok",
                "color": 0x1000000,
                "timestamp": "yesterday",
                "image": {"url": "attachment://chart.png"},
                "fields": [{"name": "only a name"}],
            }
        ],
    }
    assert validate_payload(payload) == [
        "avatar_url must be an http(s) URL",
        "Embed 1 timestamp must be an ISO 8601 timestamp, for example 2026-01-31T12:00:00+00:00",
        "Embed 1 color must be an integer between 0 and 16777215",
        "Embed 1 field 1 needs both a name and a value",
    ]


def test_too_many_embeds_and_fields():
    too_many_fields = [{"name": "n", "value": "v"}] * 26
    problems = validate_payload({"embeds": [{"title": "t", "fields": too_many_fields}] + [{"title": "t"}] * 10})
    assert "Payload has 11 embeds, the limit is 10" in problems
    assert "Embed 1 has 26 fields, the limit is 25" in problems


def test_non_object_payload():
    assert validate_payload(["not", "an", "object"]) == ["Payload must be a JSON object"]


def test_three_digit_hex_shorthand_is_expanded():
    assert parse_color("#fff") == 0xFFFFFF
    assert parse_color("#f00") == 0xFF0000


def test_malformed_urls_are_reported_instead_of_raising():
    assert validate_payload({"content": "hi", "avatar_url": "http://[::1"}) == ["avatar_url must be an http(s) URL"]
