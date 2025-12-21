# Timeouts

[![Contributor Covenant](https://img.shields.io/badge/Contributor%20Covenant-v2.1%20adopted-ff69b4.svg)](../CODE_OF_CONDUCT.md)

Timeouts are primarily configured through **profiles** so most users never have to touch individual keys.
If needed, you can still override specific values under `timeouts` in `config.yaml`.

## Profiles

Available profiles:
- `fast`: Minimal waits, good for local dev when things are stable.
- `normal`: Balanced defaults (this is the default when nothing is set).
- `slow`: More forgiving for sluggish networks or machines.
- `ci`: Shorter waits and fewer retries for headless/CI runs.

Select a profile in `config.yaml`:

```yaml
timeout_profile: normal
```

Override the profile via environment variable (highest priority):

```bash
KLEINANZEIGEN_TIMEOUT_PROFILE=ci
```

Unknown profile names fall back to `normal` with a warning.

## Profile defaults

```yaml
fast:
  multiplier: 1.0
  default: 3.0
  page_load: 8.0
  captcha_detection: 1.5
  sms_verification: 3.0
  gdpr_prompt: 6.0
  login_detection: 4.0
  publishing_result: 120.0
  publishing_confirmation: 10.0
  image_upload: 18.0
  pagination_initial: 6.0
  pagination_follow_up: 3.0
  quick_dom: 1.0
  update_check: 6.0
  chrome_remote_probe: 1.5
  chrome_remote_debugging: 3.0
  chrome_binary_detection: 6.0
  retry_enabled: true
  retry_max_attempts: 1
  retry_backoff_factor: 1.3
  retry_max_backoff_factor: 2.0

normal:
  multiplier: 1.0
  default: 4.0
  page_load: 12.0
  captcha_detection: 2.0
  sms_verification: 4.0
  gdpr_prompt: 8.0
  login_detection: 6.0
  publishing_result: 180.0
  publishing_confirmation: 15.0
  image_upload: 25.0
  pagination_initial: 8.0
  pagination_follow_up: 4.0
  quick_dom: 1.5
  update_check: 8.0
  chrome_remote_probe: 2.0
  chrome_remote_debugging: 4.0
  chrome_binary_detection: 8.0
  retry_enabled: true
  retry_max_attempts: 2
  retry_backoff_factor: 1.5
  retry_max_backoff_factor: 3.0

slow:
  multiplier: 1.0
  default: 6.0
  page_load: 20.0
  captcha_detection: 3.0
  sms_verification: 6.0
  gdpr_prompt: 12.0
  login_detection: 12.0
  publishing_result: 300.0
  publishing_confirmation: 25.0
  image_upload: 40.0
  pagination_initial: 12.0
  pagination_follow_up: 6.0
  quick_dom: 2.5
  update_check: 12.0
  chrome_remote_probe: 3.0
  chrome_remote_debugging: 6.0
  chrome_binary_detection: 12.0
  retry_enabled: true
  retry_max_attempts: 3
  retry_backoff_factor: 1.6
  retry_max_backoff_factor: 4.0

ci:
  multiplier: 1.0
  default: 3.0
  page_load: 10.0
  captcha_detection: 1.5
  sms_verification: 3.0
  gdpr_prompt: 6.0
  login_detection: 4.0
  publishing_result: 120.0
  publishing_confirmation: 12.0
  image_upload: 20.0
  pagination_initial: 6.0
  pagination_follow_up: 3.0
  quick_dom: 1.0
  update_check: 6.0
  chrome_remote_probe: 1.5
  chrome_remote_debugging: 3.0
  chrome_binary_detection: 6.0
  retry_enabled: true
  retry_max_attempts: 1
  retry_backoff_factor: 1.3
  retry_max_backoff_factor: 2.0
```

## Optional per-key overrides

If a single area needs tuning, override only that key:

```yaml
timeout_profile: normal
timeouts:
  login_detection: 12.0
  image_upload: 40.0
```

## Timeout keys

These are the supported keys for per-key overrides:

- `multiplier`: Scales all timeouts (e.g., `2.0` doubles them).
- `default`: Baseline timeout for DOM interactions (web_find/web_click/etc.).
- `page_load`: Page load timeout for `web_open`.
- `captcha_detection`: Captcha iframe detection timeout.
- `sms_verification`: SMS verification prompt timeout.
- `gdpr_prompt`: GDPR/consent dialog timeout.
- `login_detection`: Logged-in session detection timeout.
- `publishing_result`: Publishing result check timeout.
- `publishing_confirmation`: Publish confirmation redirect timeout.
- `image_upload`: Image upload + server-side processing timeout.
- `pagination_initial`: Initial pagination lookup timeout.
- `pagination_follow_up`: Follow-up pagination navigation timeout.
- `quick_dom`: Short timeout for transient UI.
- `update_check`: GitHub update check timeout.
- `chrome_remote_probe`: Local remote-debugging probe timeout.
- `chrome_remote_debugging`: Remote debugging API timeout.
- `chrome_binary_detection`: Browser `--version` subprocess timeout.
- `retry_enabled`: Enable retry/backoff for DOM lookups.
- `retry_max_attempts`: Max retry attempts when retry is enabled.
- `retry_backoff_factor`: Exponential backoff factor for retries.
- `retry_max_backoff_factor`: Clamp for exponential backoff factor.
