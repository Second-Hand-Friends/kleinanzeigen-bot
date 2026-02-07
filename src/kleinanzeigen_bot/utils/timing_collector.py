# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

"""Collect per-operation timeout timings and persist per-run JSON sessions.

`TimingCollector` records operation durations in seconds, grouped by a single bot run
(`session_id`). Call `record(...)` during runtime and `flush()` once at command end to
append the current session to `timing_data.json` with automatic 30-day retention.
The collector is best-effort and designed for troubleshooting, not strict telemetry.
"""

from __future__ import annotations

import json, uuid  # isort: skip
import os
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Final

from kleinanzeigen_bot.utils import loggers, misc

from . import xdg_paths

LOG:Final[loggers.Logger] = loggers.get_logger(__name__)

RETENTION_DAYS:Final[int] = 30
TIMING_FILE:Final[str] = "timing_data.json"


@dataclass
class TimingRecord:
    timestamp:str
    operation_key:str
    operation_type:str
    description:str
    configured_timeout_sec:float
    effective_timeout_sec:float
    actual_duration_sec:float
    attempt_index:int
    success:bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TimingCollector:
    def __init__(self, installation_mode:xdg_paths.InstallationMode, command:str) -> None:
        self.installation_mode = installation_mode
        self.command = command
        self.session_id = uuid.uuid4().hex[:8]
        self.started_at = misc.now().isoformat()
        self.records:list[TimingRecord] = []
        self._flushed = False

        LOG.debug("Timing collection initialized (session=%s, mode=%s, command=%s)", self.session_id, installation_mode, command)

    @property
    def output_dir(self) -> Path:
        if self.installation_mode == "portable":
            return (Path.cwd() / ".temp" / "timing").resolve()
        return (xdg_paths.get_xdg_base_dir("cache") / "timing").resolve()

    def record(
        self,
        *,
        key:str,
        operation_type:str,
        description:str,
        configured_timeout:float,
        effective_timeout:float,
        actual_duration:float,
        attempt_index:int,
        success:bool,
    ) -> None:
        self.records.append(
            TimingRecord(
                timestamp = misc.now().isoformat(),
                operation_key = key,
                operation_type = operation_type,
                description = description,
                configured_timeout_sec = configured_timeout,
                effective_timeout_sec = effective_timeout,
                actual_duration_sec = actual_duration,
                attempt_index = attempt_index,
                success = success,
            )
        )
        LOG.debug(
            "Timing captured: %s [%s] duration=%.3fs timeout=%.3fs success=%s",
            operation_type,
            key,
            actual_duration,
            effective_timeout,
            success,
        )

    def flush(self) -> Path | None:
        if self._flushed:
            LOG.debug("Timing collection already flushed for this run")
            return None
        if not self.records:
            LOG.debug("Timing collection enabled but no records captured in this run")
            return None

        try:
            self.output_dir.mkdir(parents = True, exist_ok = True)
            data = self._load_existing_sessions()
            data.append(
                {
                    "session_id": self.session_id,
                    "command": self.command,
                    "started_at": self.started_at,
                    "ended_at": misc.now().isoformat(),
                    "records": [record.to_dict() for record in self.records],
                }
            )

            cutoff = misc.now() - timedelta(days = RETENTION_DAYS)
            retained:list[dict[str, Any]] = []
            dropped = 0
            for session in data:
                try:
                    parsed = misc.parse_datetime(session.get("started_at"), add_timezone_if_missing = True)
                except ValueError:
                    parsed = None
                if parsed is None:
                    dropped += 1
                    continue
                if parsed >= cutoff:
                    retained.append(session)
                else:
                    dropped += 1

            if dropped > 0:
                LOG.debug("Timing collection pruned %d old or malformed sessions", dropped)

            output_file = self.output_dir / TIMING_FILE
            temp_file = self.output_dir / f".{TIMING_FILE}.{self.session_id}.tmp"
            with temp_file.open("w", encoding = "utf-8") as fd:
                json.dump(retained, fd, indent = 2)
                fd.write("\n")
                fd.flush()
                os.fsync(fd.fileno())
            temp_file.replace(output_file)

            LOG.debug(
                "Timing collection flushed to %s (%d sessions, %d current records, retention=%d days)",
                output_file,
                len(retained),
                len(self.records),
                RETENTION_DAYS,
            )
            self.records = []
            self._flushed = True
            return output_file
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Failed to flush timing collection data: %s", exc)
            return None

    def _load_existing_sessions(self) -> list[dict[str, Any]]:
        file_path = self.output_dir / TIMING_FILE
        if not file_path.exists():
            return []

        try:
            with file_path.open(encoding = "utf-8") as fd:
                payload = json.load(fd)
            if isinstance(payload, list):
                return [item for item in payload if isinstance(item, dict)]
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Unable to load timing collection data from %s: %s", file_path, exc)
        return []
