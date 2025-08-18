# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import json
import re
import subprocess  # noqa: S404
import urllib.error
import urllib.request
from typing import Any, Final

from . import loggers

LOG:Final[loggers.Logger] = loggers.get_logger(__name__)

# Chrome 136 was released in March 2025 and introduced security changes
CHROME_136_VERSION = 136


class ChromeVersionInfo:
    """Information about a Chrome browser version."""

    def __init__(self, version_string:str, major_version:int, browser_name:str = "Unknown") -> None:
        self.version_string = version_string
        self.major_version = major_version
        self.browser_name = browser_name

    @property
    def is_chrome_136_plus(self) -> bool:
        """Check if this is Chrome version 136 or later."""
        return self.major_version >= CHROME_136_VERSION

    def __str__(self) -> str:
        return f"{self.browser_name} {self.version_string} (major: {self.major_version})"


def parse_version_string(version_string:str) -> int:
    """
    Parse a Chrome version string and extract the major version number.

    Args:
        version_string: Version string like "136.0.6778.0" or "136.0.6778.0 (Developer Build)"

    Returns:
        Major version number (e.g., 136)

    Raises:
        ValueError: If version string cannot be parsed
    """
    # Extract version number from strings like:
    # "136.0.6778.0"
    # "136.0.6778.0 (Developer Build)"
    # "136.0.6778.0 (Official Build) (x86_64)"
    # "Google Chrome 136.0.6778.0"
    # "Microsoft Edge 136.0.6778.0"
    # "Chromium 136.0.6778.0"
    match = re.search(r"(\d+)\.\d+\.\d+\.\d+", version_string)
    if not match:
        raise ValueError(f"Could not parse version string: {version_string}")

    return int(match.group(1))


def _normalize_browser_name(browser_name:str) -> str:
    """
    Normalize browser name for consistent detection.

    Args:
        browser_name: Raw browser name from detection

    Returns:
        Normalized browser name
    """
    browser_name_lower = browser_name.lower()
    if "edge" in browser_name_lower or "edg" in browser_name_lower:
        return "Edge"
    if "chromium" in browser_name_lower:
        return "Chromium"
    return "Chrome"


def detect_chrome_version_from_binary(binary_path:str) -> ChromeVersionInfo | None:
    """
    Detect Chrome version by running the browser binary.

    Args:
        binary_path: Path to the Chrome binary

    Returns:
        ChromeVersionInfo if successful, None if detection fails
    """
    try:
        # Run browser with --version flag
        result = subprocess.run(  # noqa: S603
            [binary_path, "--version"],
            check = False, capture_output = True,
            text = True,
            timeout = 10
        )

        if result.returncode != 0:
            LOG.debug("Browser version command failed: %s", result.stderr)
            return None

        output = result.stdout.strip()
        major_version = parse_version_string(output)

        # Extract just the version number for version_string
        version_match = re.search(r"(\d+\.\d+\.\d+\.\d+)", output)
        version_string = version_match.group(1) if version_match else output

        # Determine browser name from binary path
        browser_name = _normalize_browser_name(binary_path)

        return ChromeVersionInfo(version_string, major_version, browser_name)

    except subprocess.TimeoutExpired:
        LOG.debug("Browser version command timed out")
        return None
    except (subprocess.SubprocessError, ValueError) as e:
        LOG.debug("Failed to detect browser version: %s", str(e))
        return None


def detect_chrome_version_from_remote_debugging(host:str = "127.0.0.1", port:int = 9222) -> ChromeVersionInfo | None:
    """
    Detect Chrome version from remote debugging API.

    Args:
        host: Remote debugging host
        port: Remote debugging port

    Returns:
        ChromeVersionInfo if successful, None if detection fails
    """
    try:
        # Query the remote debugging API
        url = f"http://{host}:{port}/json/version"
        response = urllib.request.urlopen(url, timeout = 5)  # noqa: S310
        version_data = json.loads(response.read().decode())

        # Extract version information
        user_agent = version_data.get("User-Agent", "")
        browser_name = _normalize_browser_name(version_data.get("Browser", "Unknown"))

        # Parse version from User-Agent string
        # Example: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.6778.0 Safari/537.36"
        match = re.search(r"Chrome/(\d+)\.\d+\.\d+\.\d+", user_agent)
        if not match:
            LOG.debug("Could not parse Chrome version from User-Agent: %s", user_agent)
            return None

        major_version = int(match.group(1))
        version_string = match.group(0).replace("Chrome/", "")

        return ChromeVersionInfo(version_string, major_version, browser_name)

    except urllib.error.URLError as e:
        LOG.debug("Remote debugging API not accessible: %s", e)
        return None
    except json.JSONDecodeError as e:
        LOG.debug("Invalid JSON response from remote debugging API: %s", e)
        return None
    except Exception as e:
        LOG.debug("Failed to detect browser version from remote debugging: %s", str(e))
        return None


def validate_chrome_136_configuration(browser_arguments:list[str], user_data_dir:str | None) -> tuple[bool, str]:
    """
    Validate configuration for Chrome/Edge 136+ security requirements.

    Chrome/Edge 136+ requires --user-data-dir to be specified for security reasons.

    Args:
        browser_arguments: List of browser arguments
        user_data_dir: User data directory configuration

    Returns:
        Tuple of (is_valid, error_message)
    """
    # Check if user-data-dir is specified in arguments
    has_user_data_dir_arg = any(
        arg.startswith("--user-data-dir=")
        for arg in browser_arguments
    )

    # Check if user_data_dir is configured
    has_user_data_dir_config = user_data_dir is not None and user_data_dir.strip()

    if not has_user_data_dir_arg and not has_user_data_dir_config:
        return False, (
            "Chrome/Edge 136+ requires --user-data-dir to be specified. "
            "Add --user-data-dir=/path/to/directory to your browser arguments and "
            'user_data_dir: "/path/to/directory" to your configuration.'
        )

    return True, ""


def get_chrome_version_diagnostic_info(
    binary_path:str | None = None,
    remote_host:str = "127.0.0.1",
    remote_port:int | None = None
) -> dict[str, Any]:
    """
    Get comprehensive Chrome version diagnostic information.

    Args:
        binary_path: Path to Chrome binary (optional)
        remote_host: Remote debugging host
        remote_port: Remote debugging port (optional)

    Returns:
        Dictionary with diagnostic information
    """
    diagnostic_info:dict[str, Any] = {
        "binary_detection": None,
        "remote_detection": None,
        "chrome_136_plus_detected": False,
        "configuration_valid": True,
        "recommendations": []
    }

    # Try binary detection
    if binary_path:
        version_info = detect_chrome_version_from_binary(binary_path)
        if version_info:
            diagnostic_info["binary_detection"] = {
                "version_string": version_info.version_string,
                "major_version": version_info.major_version,
                "browser_name": version_info.browser_name,
                "is_chrome_136_plus": version_info.is_chrome_136_plus
            }
            diagnostic_info["chrome_136_plus_detected"] = version_info.is_chrome_136_plus

    # Try remote debugging detection
    if remote_port:
        version_info = detect_chrome_version_from_remote_debugging(remote_host, remote_port)
        if version_info:
            diagnostic_info["remote_detection"] = {
                "version_string": version_info.version_string,
                "major_version": version_info.major_version,
                "browser_name": version_info.browser_name,
                "is_chrome_136_plus": version_info.is_chrome_136_plus
            }
            diagnostic_info["chrome_136_plus_detected"] = version_info.is_chrome_136_plus

    # Add recommendations based on detected version
    if diagnostic_info["chrome_136_plus_detected"]:
        diagnostic_info["recommendations"].append(
            "Chrome 136+ detected - ensure --user-data-dir is configured for remote debugging"
        )

    return diagnostic_info
