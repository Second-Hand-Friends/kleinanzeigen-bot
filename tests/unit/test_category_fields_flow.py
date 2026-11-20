# SPDX-FileCopyrightText: © albert0815 and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for the read-only ``fields`` command using fake browser responses."""

from __future__ import annotations

import json
from typing import Any

import pytest

from kleinanzeigen_bot import category_fields_flow, publishing_form
from kleinanzeigen_bot.app import KleinanzeigenBot
from kleinanzeigen_bot.cli import help_text, parse_args
from kleinanzeigen_bot.runtime_config import VALID_COMMANDS

pytestmark = pytest.mark.unit

_FIELDS:list[dict[str, Any]] = [
    {
        "key": "fahrraeder.type", "attribute": "type_s", "label": "Typ", "required": True, "preset": "",
        "options": [{"value": "city", "label": "Cityräder"}],
    },
]


class FakeFieldsWeb:
    """Record the browser calls of the fields flow without a real browser."""

    def __init__(self, result:object) -> None:
        """Remember the canned script result."""
        self.result = result
        self.calls:list[str] = []

    async def web_open(self, url:str, *, reload_if_already_open:bool = False) -> None:
        """Record opening of the ad form."""
        self.calls.append(f"open {url} reload={reload_if_already_open}")

    async def dismiss_consent_banner(self) -> None:
        """Record dismissal of the consent banner."""
        self.calls.append("consent")

    async def web_sleep(self) -> None:
        """Skip the humanized pause."""

    async def web_execute(self, script:str) -> object:
        """Return the canned result for the fields script."""
        self.calls.append("execute")
        assert "attributeMap[" in script
        return self.result


class FakeFieldsBot(KleinanzeigenBot):
    """Run the real command dispatch without browser, login or configuration."""

    def __init__(self, result:object = None) -> None:
        """Remember what the fake web session should report."""
        super().__init__()
        self.result = result if result is not None else _FIELDS
        self.logged_in = False
        self.closed = False

    def _bootstrap_runtime(self) -> None:
        """Provide a category mapping instead of loading the configuration."""
        self.categories = {"Zubehör": "210/217/zubehoer"}

    async def _open_logged_in_browser(self) -> None:
        """Record the simulated login."""
        self.logged_in = True

    async def close_browser_session(self) -> None:
        """Record browser cleanup."""
        self.closed = True


def test_fields_is_accepted_and_documented() -> None:
    """Expose the command and its option in argument parsing and both help languages."""
    assert "fields" in VALID_COMMANDS
    parsed = parse_args(["app", "fields", "--category=210/217/zubehoer"])
    assert (parsed.command, parsed.category_arg) == ("fields", "210/217/zubehoer")
    assert "fields   -" in help_text(language = "en")
    assert "fields   -" in help_text(language = "de")
    assert "--category=<name or path> (fields)" in help_text(language = "en")
    assert "--category=<Name oder Pfad> (fields)" in help_text(language = "de")


@pytest.mark.asyncio
@pytest.mark.parametrize(("category", "expected_path"), [("Zubehör", "210/217/zubehoer"), ("210/217/zubehoer", "210/217/zubehoer")])
async def test_read_category_fields_selects_category_without_submitting(
    category:str, expected_path:str, monkeypatch:pytest.MonkeyPatch,
) -> None:
    """Resolve names via categories.yaml, select the category and only read the form."""
    selected:list[str | None] = []

    async def fake_set_category(web:object, *, category:str | None, ad_file:str, product_title:str | None = None) -> None:
        selected.append(category)

    monkeypatch.setattr(publishing_form, "set_category", fake_set_category)
    web = FakeFieldsWeb(_FIELDS)

    fields = await category_fields_flow.read_category_fields(
        web, "https://www.kleinanzeigen.de", category, {"Zubehör": "210/217/zubehoer"},  # type: ignore[arg-type]
    )

    assert fields == _FIELDS
    assert selected == [expected_path]
    assert web.calls == ["open https://www.kleinanzeigen.de/p-anzeige-aufgeben-schritt2.html reload=True", "consent", "execute"]


@pytest.mark.asyncio
async def test_read_category_fields_ignores_unexpected_script_result(monkeypatch:pytest.MonkeyPatch) -> None:
    """Return no fields when the page script yields something other than a list."""

    async def fake_set_category(web:object, *, category:str | None, ad_file:str, product_title:str | None = None) -> None:
        return

    monkeypatch.setattr(publishing_form, "set_category", fake_set_category)

    assert await category_fields_flow.read_category_fields(FakeFieldsWeb(None), "https://x", "1/2", {}) == []  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_read_category_fields_logs_options_without_api_value(monkeypatch:pytest.MonkeyPatch, caplog:pytest.LogCaptureFixture) -> None:
    """Make options with an unreadable API value visible in the debug log."""

    async def fake_set_category(web:object, *, category:str | None, ad_file:str, product_title:str | None = None) -> None:
        assert ad_file == "fields --category=1/2"

    monkeypatch.setattr(publishing_form, "set_category", fake_set_category)
    fields = [{"key": "a.b", "options": [{"value": "", "label": "Eins"}]}, {"key": "a.c", "options": [{"value": "x", "label": "Zwei"}]}]

    with caplog.at_level("DEBUG"):
        assert await category_fields_flow.read_category_fields(FakeFieldsWeb(fields), "https://x", "1/2", {}) == fields  # type: ignore[arg-type]

    assert "[a.b]" in caplog.text
    assert "[a.c]" not in caplog.text


def test_render_fields_is_json_with_umlauts() -> None:
    """Print machine-readable JSON without escaping umlauts."""
    rendered = category_fields_flow.render_fields(_FIELDS)

    assert json.loads(rendered) == _FIELDS
    assert category_fields_flow.render_fields([{"label": "Größe"}]).count("Größe") == 1


@pytest.mark.asyncio
async def test_fields_command_prints_json_and_closes_browser(
    monkeypatch:pytest.MonkeyPatch, capsys:pytest.CaptureFixture[str],
) -> None:
    """Log in, read the category and print the JSON on stdout."""

    async def fake_read(web:object, root_url:str, category:str, categories:dict[str, str]) -> list[dict[str, Any]]:
        assert (category, categories) == ("Zubehör", {"Zubehör": "210/217/zubehoer"})
        return _FIELDS

    monkeypatch.setattr(category_fields_flow, "read_category_fields", fake_read)
    bot = FakeFieldsBot()

    await bot.run(["app", "fields", "--category=Zubehör"])

    assert json.loads(capsys.readouterr().out) == _FIELDS
    assert bot.logged_in
    assert bot.closed


@pytest.mark.asyncio
async def test_fields_command_requires_category(capsys:pytest.CaptureFixture[str]) -> None:
    """Fail before opening a browser when --category is missing."""
    bot = FakeFieldsBot()

    with pytest.raises(SystemExit) as exit_info:
        await bot.run(["app", "fields"])

    assert exit_info.value.code == 2
    assert not bot.logged_in
    assert not capsys.readouterr().out
