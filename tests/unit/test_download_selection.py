# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from kleinanzeigen_bot.download_selection import NUMERIC_IDS_RE, resolve_download_ad_activity


def test_resolve_download_ad_activity_reports_owned_active_ad() -> None:
    resolved = resolve_download_ad_activity(123, {123: {"id": 123, "state": "active"}})

    assert resolved.active is True
    assert resolved.owned is True


def test_resolve_download_ad_activity_reports_owned_inactive_ad_when_state_is_missing_or_not_active() -> None:
    resolved_with_state = resolve_download_ad_activity(123, {123: {"id": 123, "state": "paused"}})
    resolved_without_state = resolve_download_ad_activity(123, {123: {"id": 123}})

    assert resolved_with_state.active is False
    assert resolved_with_state.owned is True
    assert resolved_without_state.active is False
    assert resolved_without_state.owned is True


def test_resolve_download_ad_activity_reports_unknown_ad_as_not_owned() -> None:
    resolved = resolve_download_ad_activity(123, {})

    assert resolved.active is False
    assert resolved.owned is False


def test_numeric_ids_regex_accepts_comma_separated_numbers() -> None:
    assert NUMERIC_IDS_RE.match("123,456")


def test_numeric_ids_regex_rejects_mixed_selectors() -> None:
    assert NUMERIC_IDS_RE.match("123,abc") is None
