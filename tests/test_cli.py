import io
import json

import pytest

from conftest import WEBHOOK_URL
from discord_webhook_tester import cli


@pytest.fixture
def run_cli(fake_discord, monkeypatch, capsys):
    monkeypatch.delenv(cli.WEBHOOK_URL_ENVIRONMENT_VARIABLE, raising=False)
    monkeypatch.setattr(cli, "DiscordWebhookClient", lambda **client_options: fake_discord.build_client())

    def run(*argument_list: str) -> tuple[int, str, str]:
        exit_code = cli.main(list(argument_list))
        captured = capsys.readouterr()
        return exit_code, captured.out, captured.err

    return run


def test_send_without_options_posts_the_default_test_embed(run_cli, fake_discord):
    exit_code, output, _ = run_cli("send", WEBHOOK_URL)

    assert exit_code == 0
    assert fake_discord.last_json["embeds"][0]["title"] == "Webhook is working"
    assert "Message sent (HTTP 200" in output
    assert "token_value" not in output


def test_send_builds_message_and_embed_from_options(run_cli, fake_discord):
    exit_code, _, _ = run_cli(
        "send", WEBHOOK_URL,
        "--content", "Deploy finished", "--username", "CI", "--silent",
        "--title", "Build passed", "--color", "green", "--footer", "ci",
        "--field", "Branch|main|inline", "--field", "Duration | 3m",
    )

    assert exit_code == 0
    assert fake_discord.last_json == {
        "content": "Deploy finished",
        "username": "CI",
        "flags": 4096,
        "embeds": [
            {
                "title": "Build passed",
                "color": 0x57F287,
                "footer": {"text": "ci"},
                "fields": [
                    {"name": "Branch", "value": "main", "inline": True},
                    {"name": "Duration", "value": "3m"},
                ],
            }
        ],
    }


def test_webhook_url_is_read_from_the_environment(run_cli, fake_discord, monkeypatch):
    monkeypatch.setenv(cli.WEBHOOK_URL_ENVIRONMENT_VARIABLE, WEBHOOK_URL)
    exit_code, _, _ = run_cli("send", "-c", "hello")

    assert exit_code == 0
    assert fake_discord.last_json == {"content": "hello"}


def test_dry_run_prints_payload_without_sending_or_needing_a_url(run_cli, fake_discord):
    exit_code, output, _ = run_cli("send", "--title", "Hello", "--color", "#5865F2", "--dry-run")

    assert exit_code == 0
    assert json.loads(output) == {"embeds": [{"title": "Hello", "color": 0x5865F2}]}
    assert fake_discord.requests == []


def test_payload_file_from_stdin_is_extended_by_options(run_cli, fake_discord, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO('{"content": "from file", "embeds": [{"title": "first"}]}'))
    exit_code, _, _ = run_cli("send", WEBHOOK_URL, "--payload-file", "-", "--title", "second")

    assert exit_code == 0
    assert fake_discord.last_json == {"content": "from file", "embeds": [{"title": "first"}, {"title": "second"}]}


def test_payload_over_the_limits_is_not_sent(run_cli, fake_discord):
    exit_code, _, error_output = run_cli("send", WEBHOOK_URL, "--content", "x" * 2001)

    assert exit_code == 2
    assert "content is 2001 characters, the limit is 2000" in error_output
    assert fake_discord.requests == []


def test_no_validate_lets_discord_answer(run_cli, fake_discord):
    fake_discord.queue(400, {"message": "Invalid Form Body", "code": 50035, "errors": {"content": {"_errors": [{"message": "Too long."}]}}})
    exit_code, _, error_output = run_cli("send", WEBHOOK_URL, "--content", "x" * 2001, "--no-validate")

    assert exit_code == 1
    assert "Invalid Form Body (code 50035) (HTTP 400)" in error_output
    assert "content: Too long." in error_output


@pytest.mark.parametrize(
    ("argument_list", "expected_error"),
    [
        (["send"], "No webhook URL"),
        (["send", "https://example.com/hook"], "Not a Discord webhook URL"),
        (["send", WEBHOOK_URL, "--title", "t", "--color", "nope"], "Invalid color"),
        (["send", WEBHOOK_URL, "--title", "t", "--field", "missing-value"], "Invalid --field"),
        (["send", WEBHOOK_URL, "--file", "/nonexistent/file.txt"], "File not found"),
        (["send", WEBHOOK_URL, "--payload-file", "/nonexistent/payload.json"], "Cannot read payload file"),
    ],
)
def test_invalid_input_exits_with_code_2(run_cli, fake_discord, argument_list, expected_error):
    exit_code, _, error_output = run_cli(*argument_list)

    assert exit_code == 2
    assert expected_error in error_output
    assert fake_discord.requests == []


def test_json_output_is_machine_readable(run_cli, fake_discord):
    fake_discord.queue(404, {"message": "Unknown Webhook", "code": 10015})
    exit_code, output, _ = run_cli("send", WEBHOOK_URL, "--json")

    result = json.loads(output)
    assert exit_code == 1
    assert result["ok"] is False and result["status_code"] == 404


def test_info_shows_webhook_details(run_cli, fake_discord):
    fake_discord.queue(200, {"id": "1", "name": "Alerts", "channel_id": "2", "guild_id": "3", "token": "secret"})
    exit_code, output, _ = run_cli("info", WEBHOOK_URL)

    assert exit_code == 0
    assert "Webhook is valid" in output and "Alerts" in output
    assert "secret" not in output


def test_field_values_may_contain_pipes(run_cli, fake_discord):
    exit_code, _, _ = run_cli("send", WEBHOOK_URL, "--field", "Spoiler|||secret|||inline", "--field", "Plain|a|b")

    assert exit_code == 0
    assert fake_discord.last_json["embeds"][0]["fields"] == [
        {"name": "Spoiler", "value": "||secret||", "inline": True},
        {"name": "Plain", "value": "a|b"},
    ]


@pytest.mark.parametrize(
    ("payload_text", "extra_arguments", "expected_error"),
    [
        ('{"embeds": null}', ["--title", "t"], "embeds must be a list"),
        ('{"content": "x", "flags": "4096"}', ["--silent"], "flags must be an integer"),
    ],
)
def test_payload_file_with_wrong_types_is_rejected(run_cli, monkeypatch, payload_text, extra_arguments, expected_error):
    monkeypatch.setattr("sys.stdin", io.StringIO(payload_text))
    exit_code, _, error_output = run_cli("send", WEBHOOK_URL, "--payload-file", "-", *extra_arguments)

    assert exit_code == 2 and expected_error in error_output


def test_color_without_a_visible_embed_part_is_an_error(run_cli, fake_discord):
    exit_code, _, error_output = run_cli("send", WEBHOOK_URL, "--color", "red")

    assert exit_code == 2 and "need a visible embed part" in error_output
    assert fake_discord.requests == []


def test_unreadable_attachment_exits_with_code_2(run_cli, tmp_path):
    locked_path = tmp_path / "locked.txt"
    locked_path.write_text("secret")
    locked_path.chmod(0)
    exit_code, _, error_output = run_cli("send", WEBHOOK_URL, "--file", str(locked_path))

    assert exit_code == 2 and "Cannot read attachment" in error_output
