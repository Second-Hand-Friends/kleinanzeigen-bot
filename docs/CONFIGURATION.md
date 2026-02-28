# Configuration Reference

Complete reference for `config.yaml`, the main configuration file for kleinanzeigen-bot.

## Quick Start

To generate a default configuration file with all current defaults:

```bash
kleinanzeigen-bot create-config
```

For full JSON schema with IDE autocompletion support, see:

- [schemas/config.schema.json](https://raw.githubusercontent.com/Second-Hand-Friends/kleinanzeigen-bot/main/schemas/config.schema.json)

A reference snapshot of default values is available at [docs/config.default.yaml](https://raw.githubusercontent.com/Second-Hand-Friends/kleinanzeigen-bot/main/docs/config.default.yaml).

To enable IDE autocompletion in `config.yaml`, add this at the top of the file:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/Second-Hand-Friends/kleinanzeigen-bot/main/schemas/config.schema.json
```

For ad files, use the ad schema instead:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/Second-Hand-Friends/kleinanzeigen-bot/main/schemas/ad.schema.json
```

## Minimal Configuration Example

Here's the smallest viable `config.yaml` to get started. Only the **login** section is required—everything else uses sensible defaults:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/Second-Hand-Friends/kleinanzeigen-bot/main/schemas/config.schema.json

# REQUIRED: Your kleinanzeigen.de credentials
login:
  username: "your_username"
  password: "your_password"

# OPTIONAL: Where to find your ad files (default pattern shown)
# ad_files:
#   - "./**/ad_*.{json,yml,yaml}"

# OPTIONAL: Default values for all ads
# ad_defaults:
#   price_type: NEGOTIABLE
#   shipping_type: SHIPPING
#   republication_interval: 7
```

Run `kleinanzeigen-bot create-config` to generate a complete configuration with all available options and their default values.

The `ad_files` setting controls where the bot looks for your ad YAML files (default pattern: `./**/ad_*.{json,yml,yaml}`). The `ad_defaults` section lets you set default values that apply to all ads—things like price type, shipping options, and republication interval.

📖 **[Complete Ad Configuration Reference →](AD_CONFIGURATION.md)**

Full documentation for ad YAML files including automatic price reduction, description prefix/suffix, shipping options, category IDs, and special attributes.

## File Location

The bot looks for `config.yaml` in the current directory by default. You can specify a different location using `--config`:

```bash
kleinanzeigen-bot --config /path/to/config.yaml publish
```

`--config` selects the configuration file only. Workspace behavior is controlled by installation mode (`portable` or `xdg`) and can be overridden via `--workspace-mode=portable|xdg` (see [Installation Modes](#installation-modes)).

Valid file extensions: `.json`, `.yaml`, `.yml`

## Configuration Structure

### ad_files

Glob (wildcard) patterns to select ad configuration files. Use relative patterns so they resolve relative to `config.yaml`.

```yaml
ad_files:
  - "./**/ad_*.{json,yml,yaml}"
```

- Relative `ad_files` patterns are resolved relative to `config.yaml`.
- Absolute `ad_files` paths or glob patterns are not recommended and may behave differently across platforms.
- For portable configurations, prefer relative patterns.

### ad_defaults

Default values for ads that can be overridden in each ad configuration file.

```yaml
ad_defaults:
  active: true
  type: OFFER  # one of: OFFER, WANTED

  description_prefix: ""
  description_suffix: ""

  price_type: NEGOTIABLE  # one of: FIXED, NEGOTIABLE, GIVE_AWAY, NOT_APPLICABLE
  shipping_type: SHIPPING  # one of: PICKUP, SHIPPING, NOT_APPLICABLE
  # NOTE: shipping_costs and shipping_options must be configured per-ad, not as defaults
  sell_directly: false  # requires shipping_type SHIPPING to take effect
  contact:
    name: ""
    street: ""
    zipcode: ""
    phone: ""  # IMPORTANT: surround phone number with quotes to prevent removal of leading zeros
  republication_interval: 7  # every X days ads should be re-published
```

- `ad_defaults.republication_interval` controls when ads become due for republishing.
- Automatic price reductions (including `delay_reposts` and `delay_days`) are evaluated only during `publish` runs.
- Reductions do not run in the background between runs, and `update` does not evaluate or apply reductions.
- When auto price reduction is enabled, each `publish` run logs the reduction decision.
- `-v/--verbose` adds a detailed reduction calculation trace.
- For full behavior and examples (including timeline examples), see [AD_CONFIGURATION.md](./AD_CONFIGURATION.md).

> **Tip:** For current defaults of all timeout and diagnostic settings, run `kleinanzeigen-bot create-config` or see the [JSON schema](https://raw.githubusercontent.com/Second-Hand-Friends/kleinanzeigen-bot/main/schemas/config.schema.json).

### categories

Additional name to category ID mappings. See the default list at:
[https://github.com/Second-Hand-Friends/kleinanzeigen-bot/blob/main/src/kleinanzeigen_bot/resources/categories.yaml](https://github.com/Second-Hand-Friends/kleinanzeigen-bot/blob/main/src/kleinanzeigen_bot/resources/categories.yaml)

```yaml
categories:
  Verschenken & Tauschen > Tauschen: 272/273
  Verschenken & Tauschen > Verleihen: 272/274
  Verschenken & Tauschen > Verschenken: 272/192
```

### timeouts

Timeout tuning for various browser operations. Adjust these if you experience slow page loads or recurring timeouts.

```yaml
  timeouts:
    multiplier: 1.0                     # Scale all timeouts (e.g. 2.0 for slower networks)
    default: 5.0                        # Base timeout for web_find/web_click/etc.
    page_load: 15.0                     # Timeout for web_open page loads
    captcha_detection: 2.0              # Timeout for captcha iframe detection
    sms_verification: 4.0               # Timeout for SMS verification banners
    email_verification: 4.0             # Timeout for email verification prompts
    gdpr_prompt: 10.0                   # Timeout when handling GDPR dialogs
  login_detection: 10.0               # Timeout for DOM-based login detection (primary method)
  publishing_result: 300.0            # Timeout for publishing status checks
  publishing_confirmation: 20.0         # Timeout for publish confirmation redirect
  image_upload: 30.0                  # Timeout for image upload and server-side processing
  pagination_initial: 10.0            # Timeout for first pagination lookup
  pagination_follow_up: 5.0           # Timeout for subsequent pagination clicks
  quick_dom: 2.0                      # Generic short DOM timeout (shipping dialogs, etc.)
  update_check: 10.0                  # Timeout for GitHub update requests
  chrome_remote_probe: 2.0            # Timeout for local remote-debugging probes
  chrome_remote_debugging: 5.0         # Timeout for remote debugging API calls
  chrome_binary_detection: 10.0       # Timeout for chrome --version subprocess
  retry_enabled: true                 # Enables DOM retry/backoff when timeouts occur
  retry_max_attempts: 2
  retry_backoff_factor: 1.5
```

**Timeout tuning tips:**

- Slow networks or sluggish remote browsers often just need a higher `timeouts.multiplier`
- For truly problematic selectors, override specific keys directly under `timeouts`
- Keep `retry_enabled` on so DOM lookups are retried with exponential backoff

For more details on timeout configuration and troubleshooting, see [Browser Troubleshooting](./BROWSER_TROUBLESHOOTING.md).

### download

Download configuration for the `download` command.

```yaml
download:
  dir: "downloaded-ads"  # default keeps workspace-mode download folder; custom relative paths resolve relative to config.yaml
  include_all_matching_shipping_options: false  # if true, all shipping options matching the package size will be included
  excluded_shipping_options: []  # list of shipping options to exclude, e.g. ['DHL_2', 'DHL_5']
  folder_name_max_length: 100  # maximum length for folder names when downloading ads (default: 100)
  folder_name_template: "ad_{id}_{title}"  # folder naming template; placeholders: {id}, {title}
  ad_file_name_template: "ad_{id}"  # base name for ad.yaml and image prefixes; placeholder: {id}
  rename_existing_folders: false  # if true, rename existing folders without titles to include titles (default: false)
```

- `download.dir` controls only where `download` writes ads. It does not change `publish`; `publish` still uses `ad_files`.
- Leaving `download.dir` at the default `downloaded-ads` keeps the existing workspace-mode behavior: in portable mode it uses the portable workspace download folder, and in XDG mode it uses the XDG config workspace download folder.
- If you set a custom relative `download.dir`, it is resolved relative to `config.yaml`, not the current shell working directory.
- To use one folder for both workflows, point `download.dir` and `ad_files` at the same tree explicitly.
- Warning: if you point a custom `download.dir` and `ad_files` at the same tree, running `download` again for an already-downloaded ad can overwrite that ad's downloaded config file and refresh its images/folder contents. If you manually edit ads for publishing, keep them in a separate publish folder or use backups/version control.
- `download.folder_name_template` affects newly created download folders.
- `download.ad_file_name_template` defines the shared base name for downloaded files: the bot writes the ad config as `<base>.yaml` and images as `<base>__img1.<ext>`, `<base>__img2.<ext>`, and so on.

### publishing

Publishing configuration.

```yaml
publishing:
  delete_old_ads: "AFTER_PUBLISH"  # one of: AFTER_PUBLISH, BEFORE_PUBLISH, NEVER
  delete_old_ads_by_title: true   # only works if delete_old_ads is set to BEFORE_PUBLISH
```

### captcha

Captcha handling configuration. Enable automatic restart to avoid manual confirmation after captchas.

```yaml
captcha:
  auto_restart: true  # If true, the bot aborts when a Captcha appears and retries publishing later
                      # If false (default), the Captcha must be solved manually to continue
  restart_delay: 1h 30m  # Time to wait before retrying after a Captcha was encountered (default: 6h)
```

### browser

Browser configuration. These settings control how the bot launches and connects to Chromium-based browsers.

```yaml
browser:
  # See: https://peter.sh/experiments/chromium-command-line-switches/
  arguments:
    # Example arguments
    - --disable-dev-shm-usage
    - --no-sandbox
    # --headless
    # --start-maximized
  binary_location:  # path to custom browser executable, if not specified will be looked up on PATH
  extensions: []    # a list of .crx extension files to be loaded
  use_private_window: true
  user_data_dir: ""  # see https://github.com/chromium/chromium/blob/main/docs/user_data_dir.md
  profile_name: ""
```

**Common browser arguments:**

- `--disable-dev-shm-usage` - Avoids shared memory issues in Docker environments
- `--no-sandbox` - Required when running as root (not recommended)
- `--headless` - Run browser in headless mode (no GUI)
- `--start-maximized` - Start browser maximized

For detailed browser connection troubleshooting, including Chrome 136+ security requirements and remote debugging setup, see [Browser Troubleshooting](./BROWSER_TROUBLESHOOTING.md).

### update_check

Update check configuration to automatically check for newer versions on GitHub.

```yaml
update_check:
  enabled: true  # Enable/disable update checks
  channel: latest  # One of: latest, preview
  interval: 7d    # Check interval (e.g. 7d for 7 days)
```

**Interval format:**

- `s`: seconds, `m`: minutes, `h`: hours, `d`: days
- Examples: `7d` (7 days), `12h` (12 hours), `30d` (30 days)
- Validation: minimum 1 day, maximum 30 days

**Channels:**

- `latest`: Only final releases
- `preview`: Includes pre-releases

### login

Login credentials.

```yaml
login:
  username: ""
  password: ""
```

> **Security Note:** Never commit your credentials to version control. Keep your `config.yaml` secure and exclude it from git if it contains sensitive information.

### diagnostics

Diagnostics configuration for troubleshooting login detection issues and publish failures.

```yaml
diagnostics:
  capture_on:
    login_detection: false      # Capture screenshot + HTML when login state is UNKNOWN
    publish: false             # Capture screenshot + HTML + JSON on each failed publish attempt (timeouts/protocol errors)
  capture_log_copy: false       # Copy entire bot log file when diagnostics are captured (may duplicate log content)
  pause_on_login_detection_failure: false  # Pause for manual inspection (interactive only)
  timing_collection: true       # Collect timeout timing data locally for troubleshooting and tuning
  output_dir: ""               # Custom output directory (see "Output locations (default)" below)
```

**Migration Note:**

Old diagnostics keys have been renamed/moved. Update configs and CI/automation accordingly:

- `login_detection_capture` → `capture_on.login_detection`
- `publish_error_capture` → `capture_on.publish`

`capture_log_copy` is a new top-level flag. It may copy the same log multiple times during a single run if multiple diagnostic events are triggered.

**Login Detection Behavior:**

The bot uses a layered approach to detect login state, prioritizing stealth over reliability:

1. **DOM check (primary method - preferred for stealth)**: Checks for user profile elements

   - Looks for `.mr-medium` element containing username
   - Falls back to `#user-email` ID
   - Uses `login_detection` timeout (default: 10.0 seconds)
   - Minimizes bot-like behavior by avoiding JSON API requests

2. **Auth probe fallback (more reliable)**: Sends a GET request to `{root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT`

   - Returns `LOGGED_IN` if response is HTTP 200 with valid JSON containing `"ads"` key
   - Returns `LOGGED_OUT` if response is HTTP 401/403 or HTML contains login markers
   - Returns `UNKNOWN` on timeouts, assertion failures, or unexpected response bodies
   - Only used when DOM check is inconclusive (UNKNOWN or timed out)

**Optional diagnostics:**

- Enable `capture_on.login_detection` to capture screenshots and HTML dumps when state is `UNKNOWN`
- Enable `capture_on.publish` to capture screenshots, HTML dumps, and JSON payloads for each failed publish attempt (e.g., attempts 1–3).
- Enable `capture_log_copy` to copy the entire bot log file when a diagnostic event triggers (e.g., `capture_on.publish` or `capture_on.login_detection`):
  - If multiple diagnostics trigger in the same run, the log will be copied multiple times
  - Review or redact artifacts before sharing publicly
- Enable `pause_on_login_detection_failure` to pause the bot for manual inspection in interactive sessions. This requires `capture_on.login_detection=true`; if this is not enabled, the runtime will fail startup with a validation error.
- Use custom `output_dir` to specify where artifacts are saved

**Output locations (default):**

- **Portable mode + `--config /path/to/config.yaml`**: `/path/to/.temp/diagnostics/` (portable runtime files are placed next to the selected config file)
- **Portable mode without `--config`**: `./.temp/diagnostics/` (current working directory)
- **User directories mode**: `~/.cache/kleinanzeigen-bot/diagnostics/` (Linux), `~/Library/Caches/kleinanzeigen-bot/diagnostics/` (macOS), or `%LOCALAPPDATA%\kleinanzeigen-bot\Cache\diagnostics\` (Windows)
- **Custom**: Path resolved relative to your `config.yaml` if `output_dir` is specified

**Timing collection output (default):**

- **Portable mode**: `./.temp/timing/timing_data.json`
- **User directories mode**: `~/.cache/kleinanzeigen-bot/timing/timing_data.json` (Linux) or `~/Library/Caches/kleinanzeigen-bot/timing/timing_data.json` (macOS)
- Data is grouped by run/session and retained for 30 days via automatic cleanup during each data write

Example structure:

```json
[
  {
    "session_id": "abc12345",
    "command": "publish",
    "started_at": "2026-02-07T10:00:00+01:00",
    "ended_at": "2026-02-07T10:04:30+01:00",
    "records": [
      {
        "operation_key": "default",
        "operation_type": "web_find",
        "effective_timeout_sec": 5.0,
        "actual_duration_sec": 1.2,
        "attempt_index": 0,
        "success": true
      }
    ]
  }
]
```

How to read it quickly:

- Group by `command` and `session_id` first to compare slow vs fast runs
- Look for high `actual_duration_sec` values near `effective_timeout_sec` and repeated `success: false` entries
- `attempt_index` is zero-based (`0` first attempt, `1` first retry)
- Use `operation_key` + `operation_type` to identify which timeout bucket (`default`, `page_load`, etc.) needs tuning
- For deeper timeout tuning workflow, see [Browser Troubleshooting](./BROWSER_TROUBLESHOOTING.md)

> **⚠️ PII Warning:** HTML dumps, JSON payloads, timing data JSON files (for example `timing_data.json`), and log copies may contain PII. Typical examples include account email, ad titles/descriptions, contact info, and prices. Log copies are produced by `capture_log_copy` when diagnostics capture runs, such as `capture_on.publish` or `capture_on.login_detection`. Review or redact these artifacts before sharing them publicly.

## Installation Modes

On first run, when the `--workspace-mode` flag is not provided, the app may ask which installation mode to use. In non-interactive environments, it defaults to portable mode.

1. **Portable mode (recommended for most users, especially on Windows):**

   - Stores config, logs, downloads, and state in the current directory
   - No admin permissions required
   - Easy backup/migration; works from USB drives

2. **User directories mode (advanced users / multi-user setups):**

   - Stores files in OS-standard locations
   - Cleaner directory structure; better separation from working directory
   - Requires proper permissions for user data directories

**OS notes:**

- **Windows:** User directories mode uses AppData (Roaming/Local); portable keeps everything beside the `.exe`.
- **Linux:** User directories mode uses `~/.config/kleinanzeigen-bot/config.yaml`, `~/.local/state/kleinanzeigen-bot/`, and `~/.cache/kleinanzeigen-bot/`; portable stays in the current working directory (for example `./config.yaml`, `./.temp/`, `./downloaded-ads/`).
- **macOS:** User directories mode uses `~/Library/Application Support/kleinanzeigen-bot/config.yaml` (config), `~/Library/Application Support/kleinanzeigen-bot/` (state/runtime), and `~/Library/Caches/kleinanzeigen-bot/` (cache/diagnostics); portable stays in the current directory.

### Mixed footprint cleanup

If both portable and XDG footprints exist, `--config` without `--workspace-mode` is intentionally rejected to avoid silent behavior changes.

A footprint is the set of files/directories the bot creates for one mode (configuration file, runtime state/cache directories, and `downloaded-ads`).

Use one explicit run to choose a mode:

```bash
kleinanzeigen-bot --workspace-mode=portable --config /path/to/config.yaml verify
```

or

```bash
kleinanzeigen-bot --workspace-mode=xdg --config /path/to/config.yaml verify
```

Then remove the unused footprint directories/files to make auto-detection unambiguous for future runs.

- Remove **portable footprint** items in your working location: `config.yaml`, `.temp/` (Windows: `.temp\`), and `downloaded-ads/` (Windows: `downloaded-ads\`). Back up or move `config.yaml` to your desired location before deleting it.
- Remove **user directories footprint** items:
  Linux: `~/.config/kleinanzeigen-bot/`, `~/.local/state/kleinanzeigen-bot/`, `~/.cache/kleinanzeigen-bot/`.
  macOS: `~/Library/Application Support/kleinanzeigen-bot/`, `~/Library/Caches/kleinanzeigen-bot/`.
  Windows: `%APPDATA%\kleinanzeigen-bot\`, `%LOCALAPPDATA%\kleinanzeigen-bot\`, `%LOCALAPPDATA%\kleinanzeigen-bot\Cache\`.

## Getting Current Defaults

To see all current default values, run:

```bash
kleinanzeigen-bot create-config
```

This generates a config file with `exclude_none=True`, giving you all the non-None defaults.

For the complete machine-readable reference, see the [JSON schema](https://raw.githubusercontent.com/Second-Hand-Friends/kleinanzeigen-bot/main/schemas/config.schema.json).
