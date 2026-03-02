# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

import json
import logging
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from kleinanzeigen_bot.utils import misc
from kleinanzeigen_bot.utils.timing_collector import RETENTION_DAYS, TimingCollector

pytestmark = pytest.mark.unit


class TestTimingCollector:
    def test_output_dir_resolves_to_given_path(self, tmp_path:Path) -> None:
        collector = TimingCollector(tmp_path / "xdg-cache" / "timing", "publish")

        assert collector.output_dir == (tmp_path / "xdg-cache" / "timing").resolve()

    def test_flush_writes_session_data(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        collector = TimingCollector(tmp_path / ".temp" / "timing", "publish")
        collector.record(
            key = "default",
            timeout_source_key = "default",
            timeout_origin = "operation_key",
            timeout_override_sec = None,
            operation_type = "web_find",
            description = "web_find(ID, submit)",
            configured_timeout = 5.0,
            effective_timeout = 5.0,
            actual_duration = 0.4,
            attempt_index = 0,
            success = True,
        )

        file_path = collector.flush()

        assert file_path is not None
        assert file_path.exists()

        data = json.loads(file_path.read_text(encoding = "utf-8"))
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["command"] == "publish"
        assert len(data[0]["records"]) == 1
        assert data[0]["records"][0]["operation_key"] == "default"
        assert data[0]["records"][0]["timeout_source_key"] == "default"
        assert data[0]["records"][0]["timeout_origin"] == "operation_key"
        assert data[0]["records"][0]["timeout_override_sec"] is None

    def test_flush_prunes_old_and_malformed_sessions(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)

        output_dir = tmp_path / ".temp" / "timing"
        output_dir.mkdir(parents = True, exist_ok = True)
        data_path = output_dir / "timing_data.json"

        old_started = (misc.now() - timedelta(days = RETENTION_DAYS + 1)).isoformat()
        recent_started = (misc.now() - timedelta(days = 2)).isoformat()

        existing_payload = [
            {
                "session_id": "old-session",
                "command": "publish",
                "started_at": old_started,
                "ended_at": old_started,
                "records": [],
            },
            {
                "session_id": "recent-session",
                "command": "publish",
                "started_at": recent_started,
                "ended_at": recent_started,
                "records": [],
            },
            {
                "session_id": "malformed-session",
                "command": "publish",
                "started_at": "not-a-datetime",
                "ended_at": "not-a-datetime",
                "records": [],
            },
        ]
        data_path.write_text(json.dumps(existing_payload), encoding = "utf-8")

        collector = TimingCollector(tmp_path / ".temp" / "timing", "verify")
        collector.record(
            key = "default",
            timeout_source_key = "default",
            timeout_origin = "operation_key",
            timeout_override_sec = None,
            operation_type = "web_find",
            description = "web_find(ID, submit)",
            configured_timeout = 5.0,
            effective_timeout = 5.0,
            actual_duration = 0.2,
            attempt_index = 0,
            success = True,
        )

        file_path = collector.flush()

        assert file_path is not None
        data = json.loads(file_path.read_text(encoding = "utf-8"))
        session_ids = [session["session_id"] for session in data]
        assert "old-session" not in session_ids
        assert "malformed-session" not in session_ids
        assert "recent-session" in session_ids
        assert collector.session_id in session_ids

    def test_flush_returns_none_when_already_flushed(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        collector = TimingCollector(tmp_path / ".temp" / "timing", "publish")
        collector.record(
            key = "default",
            timeout_source_key = "default",
            timeout_origin = "operation_key",
            timeout_override_sec = None,
            operation_type = "web_find",
            description = "web_find(ID, submit)",
            configured_timeout = 5.0,
            effective_timeout = 5.0,
            actual_duration = 0.1,
            attempt_index = 0,
            success = True,
        )

        first = collector.flush()
        second = collector.flush()

        assert first is not None
        assert second is None

    def test_flush_returns_none_when_no_records(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        collector = TimingCollector(tmp_path / ".temp" / "timing", "publish")

        assert collector.flush() is None

    def test_flush_recovers_from_corrupted_json(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)

        output_dir = tmp_path / ".temp" / "timing"
        output_dir.mkdir(parents = True, exist_ok = True)
        data_path = output_dir / "timing_data.json"
        data_path.write_text("{ this is invalid json", encoding = "utf-8")

        collector = TimingCollector(tmp_path / ".temp" / "timing", "verify")
        collector.record(
            key = "default",
            timeout_source_key = "default",
            timeout_origin = "operation_key",
            timeout_override_sec = None,
            operation_type = "web_find",
            description = "web_find(ID, submit)",
            configured_timeout = 5.0,
            effective_timeout = 5.0,
            actual_duration = 0.1,
            attempt_index = 0,
            success = True,
        )

        file_path = collector.flush()

        assert file_path is not None
        payload = json.loads(file_path.read_text(encoding = "utf-8"))
        assert isinstance(payload, list)
        assert len(payload) == 1
        assert payload[0]["session_id"] == collector.session_id

    def test_flush_ignores_non_list_payload(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)

        output_dir = tmp_path / ".temp" / "timing"
        output_dir.mkdir(parents = True, exist_ok = True)
        data_path = output_dir / "timing_data.json"
        data_path.write_text(json.dumps({"unexpected": "shape"}), encoding = "utf-8")

        collector = TimingCollector(tmp_path / ".temp" / "timing", "verify")
        collector.record(
            key = "default",
            timeout_source_key = "default",
            timeout_origin = "operation_key",
            timeout_override_sec = None,
            operation_type = "web_find",
            description = "web_find(ID, submit)",
            configured_timeout = 5.0,
            effective_timeout = 5.0,
            actual_duration = 0.1,
            attempt_index = 0,
            success = True,
        )

        file_path = collector.flush()

        assert file_path is not None
        payload = json.loads(file_path.read_text(encoding = "utf-8"))
        assert isinstance(payload, list)
        assert len(payload) == 1
        assert payload[0]["session_id"] == collector.session_id

    def test_flush_returns_none_when_write_raises(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        collector = TimingCollector(tmp_path / ".temp" / "timing", "verify")
        collector.record(
            key = "default",
            timeout_source_key = "default",
            timeout_origin = "operation_key",
            timeout_override_sec = None,
            operation_type = "web_find",
            description = "web_find(ID, submit)",
            configured_timeout = 5.0,
            effective_timeout = 5.0,
            actual_duration = 0.1,
            attempt_index = 0,
            success = True,
        )

        with patch.object(Path, "mkdir", side_effect = OSError("cannot create dir")):
            assert collector.flush() is None

    def test_flush_appends_new_format_records_to_legacy_payload(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)

        output_dir = tmp_path / ".temp" / "timing"
        output_dir.mkdir(parents = True, exist_ok = True)
        data_path = output_dir / "timing_data.json"
        data_path.write_text(
            json.dumps(
                [
                    {
                        "session_id": "legacy",
                        "command": "publish",
                        "started_at": (misc.now() - timedelta(days = 1)).isoformat(),
                        "ended_at": (misc.now() - timedelta(days = 1)).isoformat(),
                        "records": [
                            {
                                "timestamp": (misc.now() - timedelta(days = 1)).isoformat(),
                                "operation_key": "default",
                                "operation_type": "web_find",
                                "description": "web_find(ID, legacy)",
                                "configured_timeout_sec": 5.0,
                                "effective_timeout_sec": 5.0,
                                "actual_duration_sec": 0.2,
                                "attempt_index": 0,
                                "success": True,
                            }
                        ],
                    }
                ]
            ),
            encoding = "utf-8",
        )

        collector = TimingCollector(output_dir, "publish")
        collector.record(
            key = "default",
            timeout_source_key = "quick_dom",
            timeout_origin = "named_timeout",
            timeout_override_sec = None,
            operation_type = "web_find",
            description = "web_find(ID, submit)",
            configured_timeout = 1.0,
            effective_timeout = 1.0,
            actual_duration = 0.1,
            attempt_index = 0,
            success = True,
        )

        file_path = collector.flush()

        assert file_path is not None
        payload = json.loads(file_path.read_text(encoding = "utf-8"))
        assert len(payload) == 2
        assert "timeout_source_key" not in payload[0]["records"][0]
        assert payload[1]["records"][0]["timeout_source_key"] == "quick_dom"

    def test_record_logs_named_timeout_label(self, tmp_path:Path, caplog:pytest.LogCaptureFixture) -> None:
        collector = TimingCollector(tmp_path / ".temp" / "timing", "publish")
        caplog.set_level(logging.DEBUG)

        collector.record(
            key = "default",
            timeout_source_key = "quick_dom",
            timeout_origin = "named_timeout",
            timeout_override_sec = None,
            operation_type = "web_find",
            description = "web_find(ID, submit)",
            configured_timeout = 1.0,
            effective_timeout = 1.5,
            actual_duration = 0.7,
            attempt_index = 0,
            success = True,
        )

        assert "[default via quick_dom]" in caplog.text

    def test_record_logs_override_label(self, tmp_path:Path, caplog:pytest.LogCaptureFixture) -> None:
        collector = TimingCollector(tmp_path / ".temp" / "timing", "publish")
        caplog.set_level(logging.DEBUG)

        collector.record(
            key = "default",
            timeout_source_key = "default",
            timeout_origin = "inline_override",
            timeout_override_sec = 1.0,
            operation_type = "web_find",
            description = "web_find(ID, submit)",
            configured_timeout = 1.0,
            effective_timeout = 1.5,
            actual_duration = 0.7,
            attempt_index = 0,
            success = False,
        )

        assert "[default via override=1.000s]" in caplog.text
