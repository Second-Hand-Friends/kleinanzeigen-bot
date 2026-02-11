# kleinanzeigen-bot

[![Build Status](https://github.com/Second-Hand-Friends/kleinanzeigen-bot/actions/workflows/build.yml/badge.svg)](https://github.com/Second-Hand-Friends/kleinanzeigen-bot/actions/workflows/build.yml)
[![License](https://img.shields.io/github/license/Second-Hand-Friends/kleinanzeigen-bot.svg?color=blue)](LICENSE.txt)
[![Contributor Covenant](https://img.shields.io/badge/Contributor%20Covenant-v2.1%20adopted-ff69b4.svg)](CODE_OF_CONDUCT.md)
[![codecov](https://codecov.io/github/Second-Hand-Friends/kleinanzeigen-bot/graph/badge.svg?token=SKLDTVWHVK)](https://codecov.io/github/Second-Hand-Friends/kleinanzeigen-bot)

<!--[![Maintainability](https://qlty.sh/badges/69ff94b8-90e1-4096-91ed-3bcecf0b0597/maintainability.svg)](https://qlty.sh/gh/Second-Hand-Friends/projects/kleinanzeigen-bot)-->

**Feedback and high-quality pull requests are highly welcome!**

1. [About](#about)
1. [Installation](#installation)
1. [Usage](#usage)
1. [Configuration](#config)
   1. [Main configuration](#main-config)
   1. [Ad configuration](#ad-config)
   1. [Using an existing browser window](#existing-browser)
   1. [Browser Connection Issues](#browser-connection-issues)
1. [Development Notes](#development)
1. [Related Open-Source Projects](#related)
1. [License](#license)

## <a name="about"></a>About

**kleinanzeigen-bot** is a command-line application to **publish, update, delete, and republish listings** on kleinanzeigen.de.

### Key Features

- **Automated Publishing**: Publish new listings from YAML/JSON configuration files
- **Smart Republishing**: Automatically republish listings at configurable intervals to keep them at the top of search results
- **Bulk Management**: Update or delete multiple listings at once
- **Download Listings**: Download existing listings from your profile to local configuration files
- **Extend Listings**: Extend ads close to expiry to keep watchers/savers and preserve the monthly ad quota
- **Browser Automation**: Uses Chromium-based browsers (Chrome, Edge, Chromium) for reliable automation
- **Flexible Configuration**: Configure defaults once, override per listing as needed

### ⚠️ Legal Disclaimer

The use of this program could violate the terms of service of kleinanzeigen.de applicable at the time of use.
It is your responsibility to ensure the legal compliance of its use.
The developers assume no liability for any damages or legal consequences.
Use is at your own risk. Any unlawful use is strictly prohibited.

### ⚠️ Rechtliche Hinweise

Die Verwendung dieses Programms kann unter Umständen gegen die zum jeweiligen Zeitpunkt bei kleinanzeigen.de geltenden Nutzungsbedingungen verstoßen.
Es liegt in Ihrer Verantwortung, die rechtliche Zulässigkeit der Nutzung dieses Programms zu prüfen.
Die Entwickler übernehmen keinerlei Haftung für mögliche Schäden oder rechtliche Konsequenzen.
Die Nutzung erfolgt auf eigenes Risiko. Jede rechtswidrige Verwendung ist untersagt.

## <a name="installation"></a>Installation

### Installation using pre-compiled exe

1. The following components need to be installed:

   1. [Chromium](https://www.chromium.org/getting-involved/download-chromium), [Google Chrome](https://www.google.com/chrome/),
      or Chromium-based [Microsoft Edge](https://www.microsoft.com/edge) browser

1. Open a command/terminal window

1. Download and run the app by entering the following commands:

   1. On Windows:

      ```batch
      curl -L https://github.com/Second-Hand-Friends/kleinanzeigen-bot/releases/download/latest/kleinanzeigen-bot-windows-amd64.exe -o kleinanzeigen-bot.exe

      kleinanzeigen-bot --help
      ```

   1. On Linux:

      ```shell
      curl -L https://github.com/Second-Hand-Friends/kleinanzeigen-bot/releases/download/latest/kleinanzeigen-bot-linux-amd64 -o kleinanzeigen-bot

      chmod 755 kleinanzeigen-bot

      ./kleinanzeigen-bot --help
      ```

   1. On macOS:

      ```shell
      curl -L https://github.com/Second-Hand-Friends/kleinanzeigen-bot/releases/download/latest/kleinanzeigen-bot-darwin-amd64 -o kleinanzeigen-bot

      chmod 755 kleinanzeigen-bot

      ./kleinanzeigen-bot --help
      ```

### Installation using Docker

1. The following components need to be installed:
   1. [Docker](https://www.docker.com/)
   1. [Bash](https://www.gnu.org/software/bash/) (on Windows e.g. via [Cygwin](https://www.cygwin.com/), [MSys2](https://www.msys2.org/) or git)
   1. [X11 - X Window System](https://en.wikipedia.org/wiki/X_Window_System) display server (on Windows e.g. [Portable-X-Server](https://github.com/P-St/Portable-X-Server/releases/latest))

**Running the docker image:**

1. Ensure the X11 Server is running

1. Run the docker image:

   ```bash
   X11_DISPLAY=192.168.50.34:0.0 # replace with IP address of workstation where X11 server is running

   DATA_DIR=/var/opt/data/kleinanzeigen-bot # path to config

   # /mnt/data is the container's default working directory
   docker run --rm --interactive --tty \
     --shm-size=256m \
     -e DISPLAY=$X11_DISPLAY \
     -v $DATA_DIR:/mnt/data \
     ghcr.io/second-hand-friends/kleinanzeigen-bot \
     --help
   ```

### Installation from source

1. The following components need to be installed:

   1. [Chromium](https://www.chromium.org/getting-involved/download-chromium), [Google Chrome](https://www.google.com/chrome/),
      or Chromium-based [Microsoft Edge](https://www.microsoft.com/edge) browser
   1. [Python](https://www.python.org/) **3.10** or newer
   1. [pip](https://pypi.org/project/pip/)
   1. [git client](https://git-scm.com/downloads)

1. Open a command/terminal window

1. Clone the repo using

   ```bash
   git clone https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
   ```

1. Change into the directory:

   ```bash
   cd kleinanzeigen-bot
   ```

1. Install the Python dependencies using:

   ```bash
   pip install pdm

   pdm install
   ```

1. Run the app:

   ```bash
   pdm run app --help
   ```

### Installation from source using Docker

1. The following components need to be installed:

   1. [Docker](https://www.docker.com/)
   1. [git client](https://git-scm.com/downloads)
   1. [Bash](https://www.gnu.org/software/bash/) (on Windows e.g. via [Cygwin](https://www.cygwin.com/), [MSys2](https://www.msys2.org/) or git)
   1. [X11 - X Window System](https://en.wikipedia.org/wiki/X_Window_System) display server (on Windows e.g. [Portable-X-Server](https://github.com/P-St/Portable-X-Server/releases/latest))

1. Clone the repo using

   ```bash
   git clone https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
   ```

1. Open the cloned directory in a Bash terminal window and navigate to the [docker](docker) subdirectory

1. Execute `bash build-image.sh`

1. Ensure the image is built:

   ```text
   $ docker image ls
   REPOSITORY                            TAG      IMAGE ID       CREATED       SIZE
   second-hand-friends/kleinanzeigen-bot latest   c31fd256eeea   1 minute ago  590MB
   python                                3-slim   2052f0475488   5 days ago    123MB
   ```

**Running the docker image:**

1. Ensure the X11 Server is running

1. Run the docker image:

   ```bash
   X11_DISPLAY=192.168.50.34:0.0 # replace with IP address of workstation where X11 server is running

   DATA_DIR=/var/opt/data/kleinanzeigen-bot # path to config

   # /mnt/data is the container's default working directory
   docker run --rm --interactive --tty \
     --shm-size=256m \
     -e DISPLAY=$X11_DISPLAY \
     -v $DATA_DIR:/mnt/data \
     second-hand-friends/kleinanzeigen-bot \
     --help
   ```

## <a name="usage"></a>Usage

```console
Usage: kleinanzeigen-bot COMMAND [OPTIONS]

Commands:
  publish  - (re-)publishes ads
  verify   - verifies the configuration files
  delete   - deletes ads
  update   - updates published ads
  download - downloads one or multiple ads
  extend   - extends active ads that expire soon (keeps watchers/savers and does not count towards the monthly ad quota)
  update-check - checks for available updates
  update-content-hash – recalculates each ad's content_hash based on the current ad_defaults;
                      use this after changing config.yaml/ad_defaults to avoid every ad being marked "changed" and republished
  create-config - creates a new default configuration file if one does not exist
  diagnose - diagnoses browser connection issues and shows troubleshooting information
  --
  help     - displays this help (default command)
  version  - displays the application version

Options:
  --ads=all|due|new|changed|<id(s)> (publish) - specifies which ads to (re-)publish (DEFAULT: due)
        Possible values:
        * all: (re-)publish all ads ignoring republication_interval
        * due: publish all new ads and republish ads according the republication_interval
        * new: only publish new ads (i.e. ads that have no id in the config file)
        * changed: only publish ads that have been modified since last publication
        * <id(s)>: provide one or several ads by ID to (re-)publish, like e.g. "--ads=1,2,3" ignoring republication_interval
        * Combinations: You can combine multiple selectors with commas, e.g. "--ads=changed,due" to publish both changed and due ads
  --ads=all|new|<id(s)> (download) - specifies which ads to download (DEFAULT: new)
        Possible values:
        * all: downloads all ads from your profile
        * new: downloads ads from your profile that are not locally saved yet
        * <id(s)>: provide one or several ads by ID to download, like e.g. "--ads=1,2,3"
  --ads=all|<id(s)> (extend) - specifies which ads to extend (DEFAULT: all)
        Possible values:
        * all: extend all eligible ads in your profile
        * <id(s)>: provide one or several ads by ID to extend, like e.g. "--ads=1,2,3"
        * Note: kleinanzeigen.de only allows extending ads within 8 days of expiry; ads outside this window are skipped.
  --ads=changed|<id(s)> (update) - specifies which ads to update (DEFAULT: changed)
        Possible values:
        * changed: only update ads that have been modified since last publication
        * <id(s)>: provide one or several ads by ID to update, like e.g. "--ads=1,2,3"
  --force           - alias for '--ads=all'
  --keep-old        - don't delete old ads on republication
  --config=<PATH>   - path to the config YAML or JSON file (does not implicitly change workspace mode)
  --workspace-mode=portable|xdg - overrides workspace mode for this run
  --logfile=<PATH>  - path to the logfile (DEFAULT: depends on active workspace mode)
  --lang=en|de      - display language (STANDARD: system language if supported, otherwise English)
  -v, --verbose     - enables verbose output - only useful when troubleshooting issues
```

> **Note:** The output of `kleinanzeigen-bot help` is always the most up-to-date reference for available commands and options.

Limitation of `download`: It's only possible to extract the cheapest given shipping option.

## <a name="config"></a>Configuration

All configuration files can be in YAML or JSON format.

### Installation modes (portable vs. user directories)

On first run, the app may ask which installation mode to use. In non-interactive environments (CI/headless), it defaults to portable mode and will not prompt.

Path resolution rules:

- Runtime files are mode-dependent write locations (for example, logfile, update state, browser profile/cache, diagnostics, and downloaded ads).
- `--config` selects only the config file; it does not silently switch workspace mode.
- `--workspace-mode=portable`: runtime files are placed in the same directory as the active config file (or the current working directory if no `--config` is supplied).
- `--workspace-mode=xdg`: runtime files use OS-standard user directories.
- `--config` without `--workspace-mode`: mode is inferred from existing footprints; on ambiguity/unknown, the command fails with guidance (for example: `Could not infer workspace mode for --config ...`) and asks you to rerun with `--workspace-mode=portable` or `--workspace-mode=xdg`.

Examples:

- `kleinanzeigen-bot --config /sync/dropbox/config1.yaml verify` (no `--workspace-mode`): mode is inferred from detected footprints; if both portable and user-directories footprints are found (or none are found), the command fails and lists the found paths.
- `kleinanzeigen-bot --workspace-mode=portable --config /sync/dropbox/config1.yaml verify`: runtime files are rooted at `/sync/dropbox/` (for example `/sync/dropbox/.temp/` and `/sync/dropbox/downloaded-ads/`).
- `kleinanzeigen-bot --workspace-mode=xdg --config /sync/dropbox/config1.yaml verify`: config is read from `/sync/dropbox/config1.yaml`, while runtime files stay in user directories (on Linux: `~/.config/kleinanzeigen-bot/`, `~/.local/state/kleinanzeigen-bot/`, `~/.cache/kleinanzeigen-bot/`).

1. **Portable mode (recommended for most users, especially on Windows):**

   - Stores config, logs, downloads, and state in the current working directory
   - No admin permissions required
   - Easy backup/migration; works from USB drives

1. **User directories mode (advanced users / multi-user setups):**

   - Stores files in OS-standard locations
   - Cleaner directory structure; better separation from working directory
   - Requires proper permissions for user data directories

**OS notes (brief):**

- **Windows:** User directories mode uses AppData (Roaming/Local); portable keeps everything alongside the `.exe`.
- **Linux:** User directories mode uses `~/.config/kleinanzeigen-bot/config.yaml`, `~/.local/state/kleinanzeigen-bot/`, and `~/.cache/kleinanzeigen-bot/`; portable uses `./config.yaml`, `./.temp/`, and `./downloaded-ads/`.
- **macOS:** User directories mode uses `~/Library/Application Support/kleinanzeigen-bot/config.yaml` (config), `~/Library/Application Support/kleinanzeigen-bot/` (state/runtime), and `~/Library/Caches/kleinanzeigen-bot/` (cache/diagnostics); portable stays in the current working directory.

If you have footprints from both modes (portable + XDG), pass an explicit mode (for example `--workspace-mode=portable`) and then clean up unused files. See [Configuration: Installation Modes](docs/CONFIGURATION.md#installation-modes).

### <a name="main-config"></a>1) Main configuration ⚙️

The main configuration file (`config.yaml`) is **required** to run the bot. It contains your login credentials and controls all bot behavior.

**Quick start:**

```bash
# Generate a config file with all defaults
kleinanzeigen-bot create-config

# Or specify a custom location
kleinanzeigen-bot --config /path/to/config.yaml publish
```

**Minimal config.yaml:**

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/Second-Hand-Friends/kleinanzeigen-bot/main/schemas/config.schema.json
login:
  username: "your_username"
  password: "your_password"
```

📖 **[Complete Configuration Reference →](docs/CONFIGURATION.md)**

Full documentation including timeout tuning, browser settings, ad defaults, diagnostics, and all available options.

### <a name="ad-config"></a>2) Ad configuration 📝

Each ad is defined in a separate YAML/JSON file (default pattern: `ad_*.yaml`). These files specify the title, description, price, category, images, and other ad-specific settings.

**Quick example (`ad_laptop.yaml`):**

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/Second-Hand-Friends/kleinanzeigen-bot/main/schemas/ad.schema.json
active: true
title: "Gaming Laptop - RTX 3060"
description: |
  Powerful gaming laptop in excellent condition.
  Includes original box and charger.
category: "Elektronik > Notebooks"
price: 450
price_type: NEGOTIABLE
images:
  - "laptop/*.jpg"  # Relative to ad file location (or use absolute paths); glob patterns supported
```

📖 **[Complete Ad Configuration Reference →](docs/AD_CONFIGURATION.md)**

Full documentation including automatic price reduction, shipping options, category IDs, and special attributes.

### <a name="existing-browser"></a>3) Using an existing browser window (Optional)

By default a new browser process will be launched. To reuse a manually launched browser window/process, you can enable remote debugging. This is useful for debugging or when you want to keep your browser session open.

For detailed instructions on setting up remote debugging with Chrome 136+ security requirements, see [Browser Troubleshooting - Using an Existing Browser Window](docs/BROWSER_TROUBLESHOOTING.md#using-an-existing-browser-window).

### <a name="browser-connection-issues"></a>Browser Connection Issues

If you encounter browser connection problems, the bot includes a diagnostic command to help identify issues:

**For binary users:**

```bash
kleinanzeigen-bot diagnose
```

**For source users:**

```bash
pdm run app diagnose
```

This command will check your browser setup and provide troubleshooting information. For detailed solutions to common browser connection issues, see the [Browser Connection Troubleshooting Guide](docs/BROWSER_TROUBLESHOOTING.md).

## <a name="development"></a>Development Notes

> Please read [CONTRIBUTING.md](CONTRIBUTING.md) before contributing code. Thank you!

## <a name="related"></a>Related Open-Source projects

- [DanielWTE/ebay-kleinanzeigen-api](https://github.com/DanielWTE/ebay-kleinanzeigen-api) (Python) API interface to get random listings from kleinanzeigen.de
- [f-rolf/ebaykleinanzeiger](https://github.com/f-rolf/ebaykleinanzeiger) (Python) Discord bot that watches search results
- [r-unruh/kleinanzeigen-filter](https://github.com/r-unruh/kleinanzeigen-filter) (JavaScript) Chrome extension that filters out unwanted results from searches on kleinanzeigen.de
- [simonsagstetter/Feinanzeigen](https://github.com/simonsagstetter/feinanzeigen) (JavaScript) Chrome extension that improves search on kleinanzeigen.de
- [Superschnizel/Kleinanzeigen-Telegram-Bot](https://github.com/Superschnizel/Kleinanzeigen-Telegram-Bot) (Python) Telegram bot to scrape kleinanzeigen.de
- [tillvogt/KleinanzeigenScraper](https://github.com/tillvogt/KleinanzeigenScraper) (Python) Webscraper which stores scraped info from kleinanzeigen.de in an SQL database
- [TLINDEN/Kleingebäck](https://github.com/TLINDEN/kleingebaeck) (Go) kleinanzeigen.de Backup

## <a name="license"></a>License

All files in this repository are released under the [GNU Affero General Public License v3.0 or later](LICENSE.txt).

Individual files contain the following tag instead of the full license text:

```text
SPDX-License-Identifier: AGPL-3.0-or-later
```

This enables machine processing of license information based on the SPDX License Identifiers that are available here: <https://spdx.org/licenses/>.
