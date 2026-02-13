# Browser Connection Troubleshooting Guide

This guide helps you resolve common browser connection issues with the kleinanzeigen-bot.

## ⚠️ Important: Chrome 136+ Security Changes (March 2025)

**If you're using Chrome 136 or later and remote debugging stopped working, this is likely the cause.**

Google implemented security changes in Chrome 136 that require `--user-data-dir` to be specified when using `--remote-debugging-port`. This prevents attackers from accessing the default Chrome profile and stealing cookies/credentials.

### Quick Fix

```bash
# Start Chrome with custom user data directory
chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-debug-profile
```

### In your config.yaml

```yaml
browser:
  arguments:
    - --remote-debugging-port=9222
    - --user-data-dir=/tmp/chrome-debug-profile  # Required for Chrome 136+
  user_data_dir: "/tmp/chrome-debug-profile"     # Must match the argument above
```

**The bot will automatically detect Chrome 136+ and provide clear error messages if your configuration is missing the required `--user-data-dir` setting.**

For more details, see [Chrome 136+ Security Changes](#5-chrome-136-security-changes-march-2025) below.

## Quick Diagnosis

Run the diagnostic command to automatically check your setup:

**For binary users:**

```bash
kleinanzeigen-bot diagnose
```

**For source users:**

```bash
pdm run app diagnose
```

This will check:

- Browser binary availability and permissions
- User data directory permissions
- Remote debugging port status
- Running browser processes
- Platform-specific issues
- **Chrome/Edge version detection and configuration validation**

**Automatic Chrome 136+ Validation:**
The bot automatically detects Chrome/Edge 136+ and validates your configuration. If you're using Chrome 136+ with remote debugging but missing the required `--user-data-dir` setting, you'll see clear error messages like:

```console
Chrome 136+ configuration validation failed: Chrome 136+ requires --user-data-dir
Please update your configuration to include --user-data-dir for remote debugging
```

The bot will also provide specific instructions on how to fix your configuration.

### Issue: Slow page loads or recurring TimeoutError

**Symptoms:**

- `_extract_category_from_ad_page` fails intermittently due to breadcrumb lookups timing out
- Captcha/SMS/GDPR prompts appear right after a timeout
- Requests to GitHub's API fail sporadically with timeout errors

**Solutions:**

1. Increase `timeouts.multiplier` in `config.yaml` (e.g., `2.0` doubles every timeout consistently).
1. Override specific keys under `timeouts` (e.g., `pagination_initial: 20.0`) if only a single selector is problematic.
1. For slow email verification prompts, raise `timeouts.email_verification`.
1. Keep `retry_enabled` on so that DOM lookups are retried with exponential backoff.
1. Attach `timing_data.json` when opening issues so maintainers can tune defaults from real-world timing evidence.
   - It is written automatically during runs when `diagnostics.timing_collection` is enabled (default: `true`, see `CONFIGURATION.md`).
   - Portable mode path: `./.temp/timing/timing_data.json`
   - User directories mode path: `~/.cache/kleinanzeigen-bot/timing/timing_data.json` (Linux), `~/Library/Caches/kleinanzeigen-bot/timing/timing_data.json` (macOS), or `%LOCALAPPDATA%\kleinanzeigen-bot\timing\timing_data.json` (Windows)
   - Which one applies depends on your installation mode: portable mode writes next to your config/current directory, user directories mode writes in OS-standard user paths. Check which path exists on your system, or see `CONFIGURATION.md#installation-modes` for mode selection details.

### Issue: Bot fails to detect existing login session

**Symptoms:**

- Bot re-logins despite being already authenticated
- Intermittent (50/50) login detection behavior
- More common with profiles unused for 20+ days

**How login detection works:**
The bot checks your login status using page elements first (to minimize bot-like behavior), with a fallback to a server-side request if needed.

The bot uses a **DOM-based check** as the primary method to detect login state:

1. **DOM check (preferred - stealthy)**: Checks for user profile elements in the page

   - Looks for `.mr-medium` element containing username
   - Falls back to `#user-email` ID
   - Uses the `login_detection` timeout (default: 10.0 seconds with effective timeout with retry/backoff)
   - Minimizes bot detection by avoiding JSON API requests that normal users wouldn't trigger

2. **Auth probe fallback (more reliable)**: Sends a GET request to `{root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT`

   - Returns `LOGGED_IN` if the response is HTTP 200 with valid JSON containing `"ads"` key
   - Returns `LOGGED_OUT` if response is HTTP 401/403 or HTML contains login markers
   - Returns `UNKNOWN` on timeouts, assertion failures, or unexpected response bodies
   - Only used when DOM check is inconclusive (UNKNOWN or timed out)

3. **Diagnostics capture**: If the state remains `UNKNOWN` and `diagnostics.login_detection_capture` is enabled

   - Captures a screenshot and HTML dump for troubleshooting
   - Pauses for manual inspection if `diagnostics.pause_on_login_detection_failure` is enabled and running in an interactive terminal

**What `login_detection` controls:**

- Maximum time (seconds) to wait for user profile DOM elements when checking if already logged in
- Default: `10.0` seconds (effective timeout with retry/backoff)
- Used at startup before attempting login
- Note: With DOM-first order, this timeout applies to the primary DOM check path

**When to increase `login_detection`:**

- Frequent unnecessary re-logins despite being authenticated
- Slow or unstable network connection
- Using browser profiles that haven't been active for weeks

> **⚠️ PII Warning:** HTML dumps captured by diagnostics may contain your account email or other personally identifiable information. Review files in the diagnostics output directory before sharing them publicly.

**Example:**

```yaml
timeouts:
  login_detection: 15.0  # For slower networks or old sessions

# Enable diagnostics when troubleshooting login detection issues
diagnostics:
  login_detection_capture: true  # Capture artifacts on UNKNOWN state
  pause_on_login_detection_failure: true  # Pause for inspection (interactive only)
  output_dir: "./diagnostics"  # Custom output directory (optional)
```

## Common Issues and Solutions

### Issue 1: "Failed to connect to browser" with "root" error

**Symptoms:**

- Error message mentions "One of the causes could be when you are running as root"
- Connection fails when using existing browser profiles

**Causes:**

1. Running the application as root user
1. Browser profile is locked or in use by another process
1. Insufficient permissions to access the browser profile
1. Browser is not properly started with remote debugging enabled

**Solutions:**

#### 1. Don't run as root

```bash
# ❌ Don't do this
sudo pdm run app publish

# ✅ Do this instead
pdm run app publish
```

#### 2. Close all browser instances

```bash
# On Linux/macOS
pkill -f chrome
pkill -f chromium
pkill -f msedge

# On Windows
taskkill /f /im chrome.exe
taskkill /f /im msedge.exe
```

#### 3. Remove user_data_dir temporarily

Edit your `config.yaml` and comment out or remove the `user_data_dir` line:

```yaml
browser:
  # user_data_dir: C:\Users\user\AppData\Local\Microsoft\Edge\User Data  # Comment this out
  profile_name: "Default"
```

#### 4. Start browser manually with remote debugging

```bash
# For Chrome (macOS)
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-debug-profile

# For Chrome (Linux)
google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-debug-profile

# For Chrome (Windows)
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir=C:\temp\chrome-debug-profile

# For Edge (macOS)
/Applications/Microsoft\ Edge.app/Contents/MacOS/Microsoft\ Edge --remote-debugging-port=9222 --user-data-dir=/tmp/edge-debug-profile

# For Edge (Linux/Windows)
msedge --remote-debugging-port=9222 --user-data-dir=/tmp/edge-debug-profile

# For Chromium (Linux)
chromium --remote-debugging-port=9222 --user-data-dir=/tmp/chromium-debug-profile
```

Then in your `config.yaml`:

```yaml
browser:
  arguments:
    - --remote-debugging-port=9222
    - --user-data-dir=/tmp/chrome-debug-profile  # Must match the command line
  user_data_dir: "/tmp/chrome-debug-profile"     # Must match the argument above
```

#### ⚠️ IMPORTANT: Chrome 136+ Security Requirement

Starting with Chrome 136 (March 2025), Google has implemented security changes that require `--user-data-dir` to be specified when using `--remote-debugging-port`. This prevents attackers from accessing the default Chrome profile and stealing cookies/credentials. See [Chrome's security announcement](https://developer.chrome.com/blog/remote-debugging-port?hl=de) for more details.

### Issue 2: "Browser process not reachable at 127.0.0.1:9222"

**Symptoms:**

- Port check fails when trying to connect to existing browser
- Browser appears to be running but connection fails

**Causes:**

1. Browser not started with remote debugging port
1. Port is blocked by firewall
1. Browser crashed or closed
1. Timing issue - browser not fully started
1. Browser update changed remote debugging behavior
1. Existing Chrome instance conflicts with new debugging session
1. **Chrome 136+ security requirement not met** (most common cause since March 2025)

**Solutions:**

#### 1. Verify browser is started with remote debugging

Make sure your browser is started with the correct flag:

```bash
# Check if browser is running with remote debugging
netstat -an | grep 9222  # Linux/macOS
netstat -an | findstr 9222  # Windows
```

#### 2. Start browser manually first

```bash
# Start browser with remote debugging
chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-debug

# Then run the bot
kleinanzeigen-bot publish  # For binary users
# or
pdm run app publish        # For source users
```

#### 3. macOS-specific: Chrome started but connection fails

If you're on macOS and Chrome is started with remote debugging but the bot still can't connect:

#### ⚠️ IMPORTANT: macOS Security Requirement

This is a Chrome/macOS security issue that requires a dedicated user data directory.

```bash
# Method 1: Use the full path to Chrome with dedicated user data directory
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-debug-profile \
  --disable-dev-shm-usage

# Method 2: Use open command with proper arguments
open -a "Google Chrome" --args \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-debug-profile \
  --disable-dev-shm-usage

# Method 3: Check if Chrome is actually listening on the port
lsof -i :9222
curl http://localhost:9222/json/version
```

**⚠️ CRITICAL: You must also configure the same user data directory in your config.yaml:**

```yaml
browser:
  arguments:
    - --remote-debugging-port=9222
    - --user-data-dir=/tmp/chrome-debug-profile
    - --disable-dev-shm-usage
  user_data_dir: "/tmp/chrome-debug-profile"
```

**Common macOS issues:**

- Chrome/macOS security restrictions require a dedicated user data directory
- The `--user-data-dir` flag is **mandatory** for remote debugging on macOS
- Use `--disable-dev-shm-usage` to avoid shared memory issues
- The user data directory must match between manual Chrome startup and config.yaml

#### 4. Browser update issues

If it worked before but stopped working after a browser update:

```bash
# Check your browser version
# macOS
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --version

# Linux
google-chrome --version

# Windows
"C:\Program Files\Google\Chrome\Application\chrome.exe" --version

# Close all browser instances first
pkill -f "Google Chrome"  # macOS/Linux
# or
taskkill /f /im chrome.exe  # Windows

# Start fresh with proper flags (see macOS-specific section above for details)
```

**After browser updates:**

- Chrome may have changed how remote debugging works
- Security restrictions may have been updated
- Try using a fresh user data directory to avoid conflicts
- Ensure you're using the latest version of the bot

#### 5. Chrome 136+ Security Changes (March 2025)

If you're using Chrome 136 or later and remote debugging stopped working:

**The Problem:**
Google implemented security changes in Chrome 136 that prevent `--remote-debugging-port` from working with the default user data directory. This was done to protect users from cookie theft attacks.

**The Solution:**
You must now specify a custom `--user-data-dir` when using remote debugging:

```bash
# ❌ This will NOT work with Chrome 136+
chrome --remote-debugging-port=9222

# ✅ This WILL work with Chrome 136+
chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-debug-profile
```

**In your config.yaml:**

```yaml
browser:
  arguments:
    - --remote-debugging-port=9222
    - --user-data-dir=/tmp/chrome-debug-profile  # Required for Chrome 136+
  user_data_dir: "/tmp/chrome-debug-profile"     # Must match the argument above
```

**Why this change was made:**

- Prevents attackers from accessing the default Chrome profile
- Protects cookies and login credentials
- Uses a different encryption key for the custom profile
- Makes debugging more secure

**For more information:**

- [Chrome's security announcement](https://developer.chrome.com/blog/remote-debugging-port?hl=de)
- [GitHub issue discussion](https://github.com/Second-Hand-Friends/kleinanzeigen-bot/issues/604)

#### 6. Check firewall settings

- Windows: Check Windows Defender Firewall
- macOS: Check System Preferences > Security & Privacy > Firewall
- Linux: Check iptables or ufw settings

#### 7. Use different port

Try a different port in case 9222 is blocked:

```yaml
browser:
  arguments:
    - --remote-debugging-port=9223
```

### Issue 3: Profile directory issues

**Symptoms:**

- Errors about profile directory not found
- Permission denied errors
- Profile locked errors

**Solutions:**

#### 1. Use temporary profile

```yaml
browser:
  user_data_dir: "/tmp/chrome-temp"  # Linux/macOS
  # user_data_dir: "C:\\temp\\chrome-temp"  # Windows
  profile_name: "Default"
```

#### 2. Check profile permissions

```bash
# Linux/macOS
ls -la ~/.config/google-chrome/
chmod 755 ~/.config/google-chrome/

# Windows
# Check folder permissions in Properties > Security
```

#### 3. Remove profile temporarily

```yaml
browser:
  # user_data_dir: ""  # Comment out or remove
  # profile_name: ""   # Comment out or remove
  use_private_window: true
```

### Issue 4: Platform-specific issues

#### Windows

- **Antivirus software**: Add browser executable to exclusions
- **Windows Defender**: Add folder to exclusions
- **UAC**: Run as administrator if needed (but not recommended)

#### macOS

- **Gatekeeper**: Allow browser in System Preferences > Security & Privacy
- **SIP**: System Integrity Protection might block some operations
- **Permissions**: Grant full disk access to terminal/IDE

#### Linux

- **Sandbox**: Add `--no-sandbox` to browser arguments
- **Root user**: Never run as root, use regular user
- **Display**: Ensure X11 or Wayland is properly configured

## Configuration Examples

### Basic working configuration

```yaml
browser:
  arguments:
    - --disable-dev-shm-usage
    - --no-sandbox
  use_private_window: true
```

### Using existing browser

```yaml
browser:
  arguments:
    - --remote-debugging-port=9222
    - --user-data-dir=/tmp/chrome-debug-profile  # Required for Chrome 136+
  user_data_dir: "/tmp/chrome-debug-profile"     # Must match the argument above
  binary_location: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
```

### Using existing browser on macOS (REQUIRED configuration)

```yaml
browser:
  arguments:
    - --remote-debugging-port=9222
    - --user-data-dir=/tmp/chrome-debug-profile
    - --disable-dev-shm-usage
  user_data_dir: "/tmp/chrome-debug-profile"
  binary_location: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
```

### Using specific profile

```yaml
browser:
  user_data_dir: "C:\\Users\\username\\AppData\\Local\\Google\\Chrome\\User Data"
  profile_name: "Profile 1"
  arguments:
    - --disable-dev-shm-usage
```

## Advanced Troubleshooting

### Check browser compatibility

```bash
# Test if browser can be started manually
# macOS
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --version
/Applications/Microsoft\ Edge.app/Contents/MacOS/Microsoft\ Edge --version

# Linux
google-chrome --version
msedge --version
chromium --version

# Windows
"C:\Program Files\Google\Chrome\Application\chrome.exe" --version
msedge --version
```

### Monitor browser processes

```bash
# Linux/macOS
ps aux | grep chrome
lsof -i :9222

# Windows
tasklist | findstr chrome
netstat -an | findstr 9222
```

### Debug with verbose logging

```bash
kleinanzeigen-bot -v publish  # For binary users
# or
pdm run app -v publish        # For source users
```

### Test browser connection manually

```bash
# Test if port is accessible
curl http://localhost:9222/json/version
```

## Using an Existing Browser Window

By default a new browser process will be launched. To reuse a manually launched browser window/process, follow these steps:

1. Manually launch your browser from the command line with the `--remote-debugging-port=<NUMBER>` flag.
   You are free to choose an unused port number between 1025 and 65535, for example:

   - `chrome --remote-debugging-port=9222`
   - `chromium --remote-debugging-port=9222`
   - `msedge --remote-debugging-port=9222`

   This runs the browser in debug mode which allows it to be remote controlled by the bot.

   **⚠️ IMPORTANT: Chrome 136+ Security Requirement**

   Starting with Chrome 136 (March 2025), Google has implemented security changes that require `--user-data-dir` to be specified when using `--remote-debugging-port`. This prevents attackers from accessing the default Chrome profile and stealing cookies/credentials.

   **You must now use:**

   ```bash
   chrome --remote-debugging-port=9222 --user-data-dir=/path/to/custom/directory
   ```

   **And in your config.yaml:**

   ```yaml
   browser:
     arguments:
       - --remote-debugging-port=9222
       - --user-data-dir=/path/to/custom/directory
     user_data_dir: "/path/to/custom/directory"
   ```

   **The bot will automatically detect Chrome 136+ and validate your configuration. If validation fails, you'll see clear error messages with specific instructions on how to fix your configuration.**

1. In your config.yaml specify the same flags as browser arguments, for example:

   ```yaml
   browser:
     arguments:
     - --remote-debugging-port=9222
     - --user-data-dir=/tmp/chrome-debug-profile  # Required for Chrome 136+
     user_data_dir: "/tmp/chrome-debug-profile"   # Must match the argument above
   ```

1. When now publishing ads the manually launched browser will be re-used.

> NOTE: If an existing browser is used all other settings configured under `browser` in your config.yaml file will be ignored
> because they are only used to programmatically configure/launch a dedicated browser instance.
>
> **Security Note:** This change was implemented by Google to protect users from cookie theft attacks. The custom user data directory uses a different encryption key than the default profile, making it more secure for debugging purposes.

## Getting Help

If you're still experiencing issues:

1. Run the diagnostic command: `kleinanzeigen-bot diagnose` (binary) or `pdm run app diagnose` (source)
1. Check the log file for detailed error messages
1. Try the solutions above step by step
1. Create an issue on GitHub with:
   - Output from the diagnose command
   - Your `config.yaml` (remove sensitive information)
   - Error messages from the log file
   - Operating system and browser version

## Prevention

To avoid browser connection issues:

1. **Don't run as root** - Always use a regular user account
1. **Close other browser instances** - Ensure no other browser processes are running
1. **Use temporary profiles** - Avoid conflicts with existing browser sessions
1. **Keep browser updated** - Use the latest stable version
1. **Check permissions** - Ensure proper file and folder permissions
1. **Monitor system resources** - Ensure sufficient memory and disk space
