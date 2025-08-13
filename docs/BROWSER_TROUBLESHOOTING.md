# Browser Connection Troubleshooting Guide

This guide helps you resolve common browser connection issues with the kleinanzeigen-bot.

## ⚠️ Important: Chrome 136+ Security Changes (March 2025)

**If you're using Chrome 136 or later and remote debugging stopped working, this is likely the cause.**

Google implemented security changes in Chrome 136 that require `--user-data-dir` to be specified when using `--remote-debugging-port`. This prevents attackers from accessing the default Chrome profile and stealing cookies/credentials.

**Quick Fix:**
```bash
# Start Chrome with custom user data directory
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

```
Chrome 136+ configuration validation failed: Chrome 136+ requires --user-data-dir
Please update your configuration to include --user-data-dir for remote debugging
```

The bot will also provide specific instructions on how to fix your configuration.

## Common Issues and Solutions

### Issue 1: "Failed to connect to browser" with "root" error

**Symptoms:**
- Error message mentions "One of the causes could be when you are running as root"
- Connection fails when using existing browser profiles

**Causes:**
1. Running the application as root user
2. Browser profile is locked or in use by another process
3. Insufficient permissions to access the browser profile
4. Browser is not properly started with remote debugging enabled

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

**⚠️ IMPORTANT: Chrome 136+ Security Requirement**

Starting with Chrome 136 (March 2025), Google has implemented security changes that require `--user-data-dir` to be specified when using `--remote-debugging-port`. This prevents attackers from accessing the default Chrome profile and stealing cookies/credentials. See [Chrome's security announcement](https://developer.chrome.com/blog/remote-debugging-port?hl=de) for more details.

### Issue 2: "Browser process not reachable at 127.0.0.1:9222"

**Symptoms:**
- Port check fails when trying to connect to existing browser
- Browser appears to be running but connection fails

**Causes:**
1. Browser not started with remote debugging port
2. Port is blocked by firewall
3. Browser crashed or closed
4. Timing issue - browser not fully started
5. Browser update changed remote debugging behavior
6. Existing Chrome instance conflicts with new debugging session
7. **Chrome 136+ security requirement not met** (most common cause since March 2025)

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

**⚠️ IMPORTANT: This is a Chrome/macOS security issue that requires a dedicated user data directory**

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

#### 5. Check firewall settings
- Windows: Check Windows Defender Firewall
- macOS: Check System Preferences > Security & Privacy > Firewall
- Linux: Check iptables or ufw settings

#### 6. Use different port
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

## Getting Help

If you're still experiencing issues:

1. Run the diagnostic command: `kleinanzeigen-bot diagnose` (binary) or `pdm run app diagnose` (source)
2. Check the log file for detailed error messages
3. Try the solutions above step by step
4. Create an issue on GitHub with:
   - Output from the diagnose command
   - Your `config.yaml` (remove sensitive information)
   - Error messages from the log file
   - Operating system and browser version

## Prevention

To avoid browser connection issues:

1. **Don't run as root** - Always use a regular user account
2. **Close other browser instances** - Ensure no other browser processes are running
3. **Use temporary profiles** - Avoid conflicts with existing browser sessions
4. **Keep browser updated** - Use the latest stable version
5. **Check permissions** - Ensure proper file and folder permissions
6. **Monitor system resources** - Ensure sufficient memory and disk space
