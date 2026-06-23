# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
Browser diagnostics extracted from WebScrapingMixin.

Contains diagnostic functions for browser binary, remote debugging,
Chrome version issues, and process inspection.
"""
import ipaddress
import os
import platform
import subprocess  # noqa: S404
from collections.abc import Callable, Iterable
from typing import Final

from kleinanzeigen_bot.utils import loggers

from .chrome_version_detector import (
    get_chrome_version_diagnostic_info,
    validate_chrome_136_configuration,
)
from .net import is_port_open
from .web_scraping_mixin import (
    BrowserConfig,
    _find_relevant_browser_processes,
    _format_url_host,
    _is_admin,
    _remote_debugging_api_browser,
)

LOG:Final[loggers.Logger] = loggers.get_logger(__name__)


def _diagnostic_remote_debugging_endpoint(arguments:Iterable[str]) -> tuple[str, int]:
    """Parse remote debugging host and port from browser arguments for diagnostics.

    Uses first-wins semantics for both host and port.

    Args:
        arguments: Browser argument strings (e.g. --remote-debugging-port=9222)

    Returns:
        (remote_host, remote_port) with defaults ("127.0.0.1", 0).

    Raises:
        ValueError for invalid port values.
    """
    remote_host = "127.0.0.1"
    remote_port = 0
    host_assigned = False
    port_assigned = False
    for arg in arguments:
        if not host_assigned and arg.startswith("--remote-debugging-host="):
            remote_host = arg.split("=", maxsplit = 1)[1]
            host_assigned = True
        if not port_assigned and arg.startswith("--remote-debugging-port="):
            remote_port = int(arg.split("=", maxsplit = 1)[1])
            port_assigned = True
        if host_assigned and port_assigned:
            break
    return remote_host, remote_port


def _target_browser_name(binary_location:str | None, get_compatible_browser:Callable[[], str]) -> str:
    """Return the lowercased target browser basename for process matching.

    Falls back to auto-detection if binary_location is not set.
    Detection failure is silent (no log output).

    Args:
        binary_location: Path to browser binary, or None for auto-detection
        get_compatible_browser: Callable that returns a compatible browser path

    Returns:
        Lowercased browser basename, or empty string on failure.
    """
    if binary_location:
        return os.path.basename(binary_location).lower()
    try:
        target_browser_path = get_compatible_browser()
        return os.path.basename(target_browser_path).lower()
    except (AssertionError, TypeError):
        return ""


def _diagnose_chrome_version_issues(
    browser_config:BrowserConfig,
    get_timeout:Callable[[str], float],
    remote_port:int,
    remote_host:str = "127.0.0.1",
) -> None:
    """Diagnose Chrome version issues and provide specific recommendations.

    Args:
        browser_config: Browser configuration
        get_timeout: Callable returning effective timeout for a given key
        remote_port: Remote debugging port (0 if not configured)
        remote_host: Remote debugging host (default "127.0.0.1")
    """
    # Skip diagnostics in test environments to avoid subprocess calls
    if os.environ.get("PYTEST_CURRENT_TEST"):
        LOG.debug(" -> Skipping browser version diagnostics in test environment")
        return

    try:
        # Get diagnostic information
        binary_path = browser_config.binary_location
        diagnostic_info = get_chrome_version_diagnostic_info(
            binary_path = binary_path,
            remote_host = _format_url_host(remote_host),
            remote_port = remote_port if remote_port > 0 else None,
            remote_timeout = get_timeout("chrome_remote_debugging"),
            binary_timeout = get_timeout("chrome_binary_detection"),
        )

        # Report binary detection results
        if diagnostic_info["binary_detection"]:
            binary_info = diagnostic_info["binary_detection"]
            LOG.info(
                "(info) %s version from binary: %s (major: %d)",
                binary_info["browser_name"],
                binary_info["version_string"],
                binary_info["major_version"],
            )

            if binary_info["is_chrome_136_plus"]:
                LOG.info("(info) %s 136+ detected - security validation required", binary_info["browser_name"])
            else:
                LOG.info("(info) %s pre-136 detected - no special security requirements", binary_info["browser_name"])

        # Report remote detection results
        if diagnostic_info["remote_detection"]:
            remote_info = diagnostic_info["remote_detection"]
            LOG.info(
                "(info) %s version from remote debugging: %s (major: %d)",
                remote_info["browser_name"],
                remote_info["version_string"],
                remote_info["major_version"],
            )

            if remote_info["is_chrome_136_plus"]:
                LOG.info("(info) Remote %s 136+ detected - validating configuration", remote_info["browser_name"])

                # Validate configuration for Chrome/Edge 136+
                is_valid, error_message = validate_chrome_136_configuration(
                    list(browser_config.arguments), browser_config.user_data_dir
                )

                if not is_valid:
                    LOG.error("(fail) %s 136+ configuration validation failed: %s", remote_info["browser_name"], error_message)
                    LOG.info("  Solution: Add --user-data-dir=/path/to/directory to browser arguments")
                    LOG.info('  And user_data_dir: "/path/to/directory" to your configuration')
                else:
                    LOG.info("(ok) %s 136+ configuration validation passed", remote_info["browser_name"])

        # Add general recommendations
        if diagnostic_info["chrome_136_plus_detected"]:
            LOG.info("(info) Chrome/Edge 136+ security changes require --user-data-dir for remote debugging")
            LOG.info("  See: https://developer.chrome.com/blog/remote-debugging-port")
    except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
        LOG.warning(" -> Browser version diagnostics failed: %s", e)
        # Continue without diagnostics rather than failing
    except Exception as e:
        LOG.warning(" -> Unexpected error during browser version diagnostics: %s", e)
        # Continue without diagnostics rather than failing


def _run_browser_diagnostics(
    browser_config:BrowserConfig,
    get_timeout:Callable[[str], float],
    get_compatible_browser:Callable[[], str],
) -> None:
    """Diagnose common browser connection issues and provide troubleshooting information.

    Args:
        browser_config: Browser configuration to inspect
        get_timeout: Callable returning effective timeout for a given key
        get_compatible_browser: Callable that returns a compatible browser path
    """
    LOG.info("=== Browser Connection Diagnostics ===")

    # Check browser binary
    if browser_config.binary_location:
        if os.path.exists(browser_config.binary_location):
            LOG.info("(ok) Browser binary exists: %s", browser_config.binary_location)
            if os.access(browser_config.binary_location, os.X_OK):
                LOG.info("(ok) Browser binary is executable")
            else:
                LOG.error("(fail) Browser binary is not executable")
        else:
            LOG.error("(fail) Browser binary not found: %s", browser_config.binary_location)
    else:
        try:
            browser_path = get_compatible_browser()
            LOG.info("(ok) Auto-detected browser: %s", browser_path)
            # Set the binary location for Chrome version detection
            browser_config.binary_location = browser_path
        except AssertionError:
            LOG.error("(fail) No compatible browser found")

    # Check user data directory
    if browser_config.user_data_dir:
        if os.path.exists(browser_config.user_data_dir):
            LOG.info("(ok) User data directory exists: %s", browser_config.user_data_dir)
            if os.access(browser_config.user_data_dir, os.R_OK | os.W_OK):
                LOG.info("(ok) User data directory is readable and writable")
            else:
                LOG.error("(fail) User data directory permissions issue")
        else:
            LOG.info("(info) User data directory does not exist (will be created): %s", browser_config.user_data_dir)

    # Check for remote debugging port - use diagnostics parser
    remote_host, remote_port = _diagnostic_remote_debugging_endpoint(browser_config.arguments)

    if remote_port > 0:
        # Security/documentation guard: warn when probing non-loopback host
        _probed_host_normalized = remote_host.strip().strip("[]")
        _is_non_loopback_host = True
        if _probed_host_normalized.lower() == "localhost":
            _is_non_loopback_host = False
        else:
            try:
                _addr = ipaddress.ip_address(_probed_host_normalized)
                _is_non_loopback_host = not _addr.is_loopback
            except ValueError:
                # DNS name - treat as potentially remote
                pass
        if _is_non_loopback_host:
            LOG.warning(
                "(warn) Remote debugging diagnostics will probe configured host: %s:%d",
                remote_host,
                remote_port,
            )

        LOG.info("(info) Remote debugging port configured: %d on host %s", remote_port, remote_host)
        if is_port_open(_probed_host_normalized, remote_port):
            LOG.info("(ok) Remote debugging port is open on %s:%d", remote_host, remote_port)
            # Try to get more information about the debugging endpoint
            try:
                probe_timeout = get_timeout("chrome_remote_probe")
                browser_fact, exc = _remote_debugging_api_browser(_probed_host_normalized, remote_port, probe_timeout)
            except Exception as e:
                exc = e
                browser_fact = None
            if exc is None:
                LOG.info("(ok) Remote debugging API accessible - Browser: %s", browser_fact)
            else:
                LOG.warning("(fail) Remote debugging port is open but API not accessible: %s", str(exc))
                LOG.info("  This might indicate a browser update issue or configuration problem")
        else:
            LOG.info("(info) Remote debugging port is not open on %s:%d", remote_host, remote_port)

    # Check for running browser processes
    target_browser_name_val = _target_browser_name(browser_config.binary_location, get_compatible_browser)
    browser_processes, proc_exc = _find_relevant_browser_processes(target_browser_name_val)

    if proc_exc is not None:
        LOG.warning("(warn) Unable to inspect browser processes: %s", proc_exc)
        browser_processes = []

    if browser_processes:
        LOG.info("(info) Found %d browser processes running", len(browser_processes))
        for proc in browser_processes[:3]:  # Show first 3
            has_debugging = proc.get("has_remote_debugging", False)
            if has_debugging:
                LOG.info("  - PID %d: %s (remote debugging enabled)", proc["pid"], proc["name"])
            else:
                LOG.warning("  - PID %d: %s (remote debugging NOT enabled)", proc["pid"], proc["name"])
    else:
        LOG.info("(info) No browser processes currently running")

    if platform.system() == "Linux":
        if _is_admin():
            LOG.error("(fail) Running as root - this can cause browser issues")

    # Chrome version detection and validation
    _diagnose_chrome_version_issues(browser_config, get_timeout, remote_port, remote_host)

    LOG.info("=== End Diagnostics ===")
