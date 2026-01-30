# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import json, re, secrets, shutil  # isort: skip
from pathlib import Path
from typing import Any, Final

from kleinanzeigen_bot.utils import loggers, misc

LOG:Final[loggers.Logger] = loggers.get_logger(__name__)


class CaptureResult:
    """Result of a diagnostics capture attempt."""

    def __init__(self) -> None:
        self.saved_artifacts:list[Path] = []

    def add_saved(self, path:Path) -> None:
        """Add a successfully saved artifact."""
        self.saved_artifacts.append(path)

    def has_any(self) -> bool:
        """Check if any artifacts were saved."""
        return bool(self.saved_artifacts)


async def capture_diagnostics(
    *,
    output_dir:Path,
    base_prefix:str,
    attempt:int | None = None,
    subject:str | None = None,
    page:Any | None = None,
    json_payload:dict[str, Any] | None = None,
    log_file_path:str | None = None,
    copy_log:bool = False,
) -> CaptureResult:
    """Capture diagnostics artifacts for the given operation.

    Args:
        output_dir: The output directory for diagnostics artifacts
        base_prefix: Base filename prefix (e.g., 'login_detection_unknown', 'publish_error')
        attempt: Optional attempt number for retry operations
        subject: Optional subject identifier (e.g., ad token)
        page: Optional page object with save_screenshot and get_content methods
        json_payload: Optional JSON data to save
        log_file_path: Optional log file path to copy
        copy_log: Whether to copy the log file

    Returns:
        CaptureResult containing the list of successfully saved artifacts
    """
    result = CaptureResult()

    try:
        output_dir.mkdir(parents = True, exist_ok = True)  # noqa: ASYNC240

        ts = misc.now().strftime("%Y%m%dT%H%M%S")
        suffix = secrets.token_hex(4)
        base = f"{base_prefix}_{ts}_{suffix}"

        if attempt is not None:
            base = f"{base}_attempt{attempt}"

        if subject:
            safe_subject = re.sub(r"[^A-Za-z0-9_-]", "_", subject)
            base = f"{base}_{safe_subject}"

        screenshot_path = output_dir / f"{base}.png"
        html_path = output_dir / f"{base}.html"
        json_path = output_dir / f"{base}.json"
        log_path = output_dir / f"{base}.log"

        if page:
            try:
                await page.save_screenshot(str(screenshot_path))
                result.add_saved(screenshot_path)
            except Exception as exc:  # noqa: BLE001
                LOG.debug("Diagnostics screenshot capture failed: %s", exc)

            try:
                html = await page.get_content()
                html_path.write_text(html, encoding = "utf-8")  # noqa: ASYNC240
                result.add_saved(html_path)
            except Exception as exc:  # noqa: BLE001
                LOG.debug("Diagnostics HTML capture failed: %s", exc)

        if json_payload is not None:
            try:
                with json_path.open("w", encoding = "utf-8") as handle:  # noqa: ASYNC240
                    json.dump(json_payload, handle, indent = 2, default = str)  # noqa: ASYNC240
                    handle.write("\n")  # noqa: ASYNC240
                result.add_saved(json_path)
            except Exception as exc:  # noqa: BLE001
                LOG.debug("Diagnostics JSON capture failed: %s", exc)

        if copy_log and log_file_path:
            try:
                log_source = Path(log_file_path)
                if log_source.exists():  # noqa: ASYNC240
                    loggers.flush_all_handlers()
                    shutil.copy2(log_source, log_path)  # noqa: ASYNC240
                    result.add_saved(log_path)
            except Exception as exc:  # noqa: BLE001
                LOG.debug("Diagnostics log copy failed: %s", exc)

        if result.has_any():
            artifacts_str = " ".join(map(str, result.saved_artifacts))
            LOG.info("Diagnostics saved: %s", artifacts_str)
        else:
            LOG.warning("Diagnostics capture enabled but no artifacts were saved")
    except Exception as exc:  # noqa: BLE001
        LOG.debug("Diagnostics capture failed: %s", exc)

    return result
