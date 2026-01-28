# Configuration Reference

Complete reference for `config.yaml`, the main configuration file for kleinanzeigen-bot.

## Quick Start

To generate a default configuration file with all current defaults:

```bash
kleinanzeigen-bot create-config
```

For full JSON schema with IDE autocompletion support, see:

- [schemas/config.schema.json](../schemas/config.schema.json)

To enable IDE autocompletion in `config.yaml`, add this at the top of the file:

```yaml
# yaml-language-server: $schema=schemas/config.schema.json
```

For ad files, use the ad schema instead:

```yaml
# yaml-language-server: $schema=schemas/ad.schema.json
```

## File Location

The bot looks for `config.yaml` in the current directory by default. You can specify a different location using the `--config` command line option:

```bash
kleinanzeigen-bot --config /path/to/config.yaml publish
```

Valid file extensions: `.json`, `.yaml`, `.yml`

## Configuration Structure

### ad_files

Glob (wildcard) patterns to select ad configuration files. If relative paths are specified, they are relative to this configuration file.

```yaml
ad_files:
  - "./**/ad_*.{json,yml,yaml}"
```

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

> **Tip:** For current defaults of all timeout and diagnostic settings, run `kleinanzeigen-bot create-config` or see the [JSON schema](../schemas/config.schema.json).

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
  login_detection: 10.0               # Timeout for DOM-based login detection fallback (auth probe is tried first)
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
  include_all_matching_shipping_options: false  # if true, all shipping options matching the package size will be included
  excluded_shipping_options: []  # list of shipping options to exclude, e.g. ['DHL_2', 'DHL_5']
  folder_name_max_length: 100  # maximum length for folder names when downloading ads (default: 100)
  rename_existing_folders: false  # if true, rename existing folders without titles to include titles (default: false)
```

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

For more details on state file format and error handling, see [Update Check Feature](./UPDATE_CHECK.md).

### login

Login credentials.

```yaml
login:
  username: ""
  password: ""
```

> **Security Note:** Never commit your credentials to version control. Keep your `config.yaml` secure and exclude it from git if it contains sensitive information.

### diagnostics

Diagnostics configuration for troubleshooting login detection issues.

```yaml
diagnostics:
  login_detection_capture: false       # Capture screenshot + HTML when login state is UNKNOWN
  pause_on_login_detection_failure: false  # Pause for manual inspection (interactive only)
  output_dir: ""                       # Custom output directory (default: portable .temp/diagnostics, xdg cache/diagnostics)
```

**Login Detection Behavior:**

The bot uses a server-side auth probe to detect login state more reliably:

1. **Auth probe (primary method)**: Sends a GET request to `{root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT`

   - Returns `LOGGED_IN` if response is HTTP 200 with valid JSON containing `"ads"` key
   - Returns `LOGGED_OUT` if response is HTTP 401/403 or HTML contains login markers
   - Returns `UNKNOWN` on timeouts, assertion failures, or unexpected response bodies

2. **DOM fallback**: Only consulted when auth probe returns `UNKNOWN`

   - Looks for `.mr-medium` element containing username
   - Falls back to `#user-email` ID
   - Uses `login_detection` timeout (default: 10.0 seconds)

**Optional diagnostics:**

- Enable `login_detection_capture` to capture screenshots and HTML dumps when state is `UNKNOWN`
- Enable `pause_on_login_detection_failure` to pause the bot for manual inspection (interactive sessions only; requires `login_detection_capture=true`)
- Use custom `output_dir` to specify where artifacts are saved

**Output locations (default):**

- **Portable mode**: `./.temp/diagnostics/`
- **System-wide mode (XDG)**: `~/.cache/kleinanzeigen-bot/diagnostics/` (Linux) or `~/Library/Caches/kleinanzeigen-bot/diagnostics/` (macOS)
- **Custom**: Path resolved relative to your `config.yaml` if `output_dir` is specified

> **⚠️ PII Warning:** HTML dumps may contain your account email or other personally identifiable information. Review files in the diagnostics output directory before sharing them publicly.

## Installation Modes

On first run, the app may ask which installation mode to use.

1. **Portable mode (recommended for most users, especially on Windows):**

   - Stores config, logs, downloads, and state in the current directory
   - No admin permissions required
   - Easy backup/migration; works from USB drives

2. **System-wide mode (advanced users / multi-user setups):**

   - Stores files in OS-standard locations
   - Cleaner directory structure; better separation from working directory
   - Requires proper permissions for user data directories

**OS notes:**

- **Windows:** System-wide uses AppData (Roaming/Local); portable keeps everything beside the `.exe`.
- **Linux:** System-wide follows XDG Base Directory spec; portable stays in the current working directory.
- **macOS:** System-wide uses `~/Library/Application Support/kleinanzeigen-bot` (and related dirs); portable stays in the current directory.

## Getting Current Defaults

To see all current default values, run:

```bash
kleinanzeigen-bot create-config
```

This generates a config file with `exclude_none=True`, giving you all the non-None defaults.

For the complete machine-readable reference, see the [JSON schema](../schemas/config.schema.json).
