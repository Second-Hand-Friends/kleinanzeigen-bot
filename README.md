# kleinanzeigen-bot

[![Build Status](https://github.com/Second-Hand-Friends/kleinanzeigen-bot/actions/workflows/build.yml/badge.svg)](https://github.com/Second-Hand-Friends/kleinanzeigen-bot/actions/workflows/build.yml)
[![License](https://img.shields.io/github/license/Second-Hand-Friends/kleinanzeigen-bot.svg?color=blue)](LICENSE.txt)
[![Contributor Covenant](https://img.shields.io/badge/Contributor%20Covenant-v3.0%20adopted-ff69b4.svg)](CODE_OF_CONDUCT.md)
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
- **Vehicle Package Selection**: Configure `publishing.package_selection: BASIS` to automatically select the verified free Basis package and submit the listing. Without this explicit setting, the default `MANUAL` leaves package selection and publication to you in the browser.
- **Smart Republishing**: Automatically republish listings at configurable intervals to keep them at the top of search results
- **Bulk Management**: Update or delete multiple listings at once
- **Download Listings**: Download existing listings from your profile to local configuration files
- **List Online Listings**: View current account listings with IDs, status, and expiry dates
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

   1. On Linux/macOS:

      ```shell
      curl -L "https://github.com/Second-Hand-Friends/kleinanzeigen-bot/releases/download/latest/kleinanzeigen-bot-$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m | sed 's/x86_64/amd64/')" -o kleinanzeigen-bot

      chmod 755 kleinanzeigen-bot

      ./kleinanzeigen-bot --help
      ```

### Installation using Docker

1. [Docker](https://www.docker.com/) needs to be installed.

The image contains Chromium with a full font set and the locales `de_DE`, `en_US` and `en_GB` (default `de_DE`, time zone `Europe/Berlin`), plus a browser desktop ([KasmVNC](https://github.com/kasmtech/KasmVNC)) you open in your own web browser.

**Running the docker image:**

1. Run the docker image:

   ```bash
   DATA_DIR=/var/opt/data/kleinanzeigen-bot # path to config

   # /mnt/data is the container's default working directory
   docker run --rm --interactive --tty --init \
     --shm-size=1g \
     -p 127.0.0.1:6080:6080 \
     -v $DATA_DIR:/mnt/data \
     ghcr.io/second-hand-friends/kleinanzeigen-bot:latest \
     publish
   ```

   Use `ghcr.io/second-hand-friends/kleinanzeigen-bot:preview` for the preview channel.

   The browser desktop exists while the command runs; the container (and the desktop) ends together with it.
   Commands that only print something, like `--help`, finish immediately:
   `docker run --rm ghcr.io/second-hand-friends/kleinanzeigen-bot:latest --help`.

   The bot runs as the unprivileged user `user` (UID 1000), or as the owner of the mounted data directory if that is not root. A new, empty data directory is handed over to `user` automatically.
   Data directories of existing installations that contain root-owned files keep working: the bot then still runs as root and logs a hint. Run `chown -R 1000:1000 $DATA_DIR` to switch to the unprivileged user.
   `docker run --user <uid>:<gid>` selects the account explicitly and `-e RUN_AS_ROOT=1` forces root.

1. Open the browser desktop at <http://localhost:6080>. The login is `user` and the password is printed in the container log on the first start
   (`docker logs <container>`). It is saved to `$DATA_DIR/.vnc-password`.
   The desktop adapts to the size of your browser window. The bot's own browser appears there while a command runs;
   right-click the desktop background and choose *Chromium* to open an additional browser (with a separate profile) for manual use.

**Settings** (all optional, pass with `-e NAME=value`):

| Variable            | Meaning                                                                                                                                  |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `VNC_PASSWORD`      | Your own desktop password (at least 6 characters). `VNC_PASSWORD_FILE` reads it from a file instead, e.g. a Docker secret.                |
| `VNC_DISABLE`       | `1` starts no desktop (use together with your own `DISPLAY`).                                                                            |
| `DISPLAY`           | If set, the bot uses that X11 server (e.g. `192.168.50.34:0.0` on your workstation) and no desktop is started.                           |
| `VNC_GEOMETRY`      | Desktop size, default `1440x900`.                                                                                                        |
| `LANG`, `TZ`        | Locale and time zone of the browser, e.g. `en_US.UTF-8` and `America/New_York`. Only the locales listed above are installed.               |
| `GPU_ACCELERATION`  | `auto` (default) uses the GPU if a render node is usable, `on` aborts if it is not, `off` forces software rendering.                    |
| `GPU_RENDER_NODE`   | Render node to use, default is the first `/dev/dri/renderD*`.                                                                            |

**3D acceleration** depends on your host. Pass the GPU into the container with `--device /dev/dri --group-add $(getent group render | cut -d: -f3)` on Linux hosts with an Intel or AMD GPU.
Without a GPU (VPS, Docker Desktop on Windows/macOS, NVIDIA's proprietary driver) the desktop is rendered in software and Chromium renders WebGL with SwiftShader, so WebGL stays available (slower, and the renderer name reveals SwiftShader).
The startup log states which mode is used.

### Installation from source

1. The following components need to be installed:

   1. [Chromium](https://www.chromium.org/getting-involved/download-chromium), [Google Chrome](https://www.google.com/chrome/),
      or Chromium-based [Microsoft Edge](https://www.microsoft.com/edge) browser
   1. [Python](https://www.python.org/) **3.12 through 3.15**
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

   > **Pip-only side note:** `pip install .` skips PDM's nodriver patch; prefer
   > `pdm install` or run `python scripts/fix_nodriver.py` if needed.

1. Run the app:

   ```bash
   pdm run app --help
   ```

### Installation from source using Docker

1. The following components need to be installed:

   1. [Docker](https://www.docker.com/)
   1. [git client](https://git-scm.com/downloads)
   1. [Bash](https://www.gnu.org/software/bash/) (on Windows e.g. via [Cygwin](https://www.cygwin.com/), [MSys2](https://www.msys2.org/) or git)

1. Clone the repo using

   ```bash
   git clone https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
   ```

1. Open the cloned directory in a Bash terminal window and navigate to the [docker](docker) subdirectory

1. Execute `bash build-image.sh`

   Additional arguments are passed to `docker build`. To choose the locales installed in the image, the default locale or the container user's UID, use e.g.:

   ```bash
   bash build-image.sh --build-arg LOCALES="fr_FR.UTF-8 en_US.UTF-8" --build-arg DEFAULT_LANG=fr_FR.UTF-8 --build-arg USER_UID=1001
   ```

1. Ensure the image is built:

   ```text
   $ docker image ls
   REPOSITORY                            TAG      IMAGE ID       CREATED       SIZE
   second-hand-friends/kleinanzeigen-bot latest   c31fd256eeea   1 minute ago  590MB
   python                                3-slim   2052f0475488   5 days ago    123MB
   ```

**Running the docker image:**

Run it as described in [Installation using Docker](#installation-using-docker), using the image name `second-hand-friends/kleinanzeigen-bot`:

```bash
DATA_DIR=/var/opt/data/kleinanzeigen-bot # path to config

# /mnt/data is the container's default working directory
docker run --rm --interactive --tty --init \
  --shm-size=1g \
  -p 127.0.0.1:6080:6080 \
  -v $DATA_DIR:/mnt/data \
  second-hand-friends/kleinanzeigen-bot \
  publish
```

## <a name="usage"></a>Usage

<!-- readme-usage:generated:start -->
```console
Usage: kleinanzeigen-bot COMMAND [OPTIONS]

Commands:
  publish  - (re-)publishes ads
  verify   - verifies the configuration files and ad integrity
  delete   - deletes ads
  update   - updates published ads
  extend   - extends ads within the 8-day window before expiry (keeps watchers/savers and does not count towards the monthly ad quota)
  reserve  - reserves ads: removes them from search results while keeping the ad ID, age, view count and watchers/savers
  activate - re-activates reserved ads
  download - downloads one or multiple ads
  update-check - checks for available updates
  update-content-hash – recalculates each ad's content_hash based on the current ad_defaults;
                        use this after changing config.yaml/ad_defaults to avoid every ad being marked "changed" and republished
  create-config - creates a new default configuration file if one does not exist
  diagnose - diagnoses browser connection issues and shows troubleshooting information
  status   - shows ad status and APR preview details
  list     - lists current online ads with ID, title, state, and expiry date
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
  --ads=all|changed|<id(s)> (update) - specifies which ads to update (DEFAULT: changed)
        Possible values:
        * all: update all ads
        * changed: only update ads that have been modified since last publication
        * <id(s)>: provide one or several ads by ID to update, like e.g. "--ads=1,2,3"
  --ads=all|<id(s)> (extend) - specifies which ads to extend (DEFAULT: all)
        Possible values:
        * all: extend all ads expiring within 8 days
        * <id(s)>: specify ad IDs to extend, e.g. "--ads=1,2,3"
        * Note: ads outside the 8-day window are skipped.
  --ads=all|<id(s)> (reserve, activate) - specifies which ads to switch (DEFAULT: all)
        Possible values:
        * all: switch all eligible ads
        * <id(s)>: specify ad IDs, e.g. "--ads=1,2,3"
        * Note: ads already in the target state are skipped.
  --force           - alias for '--ads=all'
  --keep-old        - don't delete old ads on republication
  --preserve-local-settings - force-enable preservation of local-only settings on re-download (overrides config value of false)
  --config=<PATH>   - path to the config YAML or JSON file (does not implicitly change workspace mode)
  --workspace-mode=portable|xdg - overrides workspace mode for this run
  --logfile=<PATH>  - path to the logfile (DEFAULT: depends on active workspace mode)
  --lang=en|de      - display language (STANDARD: system language if supported, otherwise English)
  -v, --verbose     - enables verbose output - only useful when troubleshooting issues
```
<!-- readme-usage:generated:end -->

`kleinanzeigen-bot list` logs in and reads your current online listings, showing
each listing's ID, title, status, and expiry date when available. It does not
download listings or change them. In a source checkout, use `pdm run app list`.
This differs from `status`, which describes only local ad files. If the online
list cannot be fetched completely, `list` fails instead of showing a partial list.

`publish` and `update` process the remaining ads when an individual ad fails, then
exit with status `1` if any ads failed. Visible form-validation errors include the
field labels and messages; correct the affected ad configuration before retrying.
An uncertain submission is never automatically retried because it may already be
online. Successful batches and batches containing only skipped ads exit with status `0`.

> **Note:** The output of `kleinanzeigen-bot help` is always the most up-to-date reference for available commands and options.

Shipping inference during `download`: the bot reads the public ad shipping state. Pickup becomes `PICKUP`; `Versand möglich` without a price becomes `SHIPPING` without costs/options. Visible "shipping from" prices are kept as deprecated `shipping_costs` metadata and, when they match a current gateway option, infer one [`shipping_options`](docs/AD_CONFIGURATION.md#shipping-options-reference) entry by default. Set `download.include_all_matching_shipping_options: true` to include all non-excluded options with the same package size. `sell_directly` is resolved only for current-profile ads from cached manage-ads data.

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

To avoid storing credentials in the config file, use environment variable substitution:

```yaml
login:
  username: "${KLEINANZEIGEN_BOT_USERNAME}"
  password: "${KLEINANZEIGEN_BOT_PASSWORD}"
```

```bash
export KLEINANZEIGEN_BOT_USERNAME=your@email.com
export KLEINANZEIGEN_BOT_PASSWORD=your_password
```

Plain-text values in config.yaml are still fully supported.

📖 **[Complete Configuration Reference →](docs/CONFIGURATION.md)**

Full documentation including timeout tuning, browser settings, ad defaults, diagnostics, and all available options.

### <a name="ad-config"></a>2) Ad configuration 📝

Each ad is defined in a separate YAML/JSON file. The `publish` workflow reads files matched by the `ad_files` glob pattern. The `download` workflow writes files into `download.dir` (default: the literal `downloaded-ads/` directory in the workspace root).

In XDG mode, the bare default `downloaded-ads/` stays in the workspace default download location. If you want a config-relative shared tree, set `download.dir` to `./downloaded-ads` and point `ad_files` at that tree, for example `./downloaded-ads/**/*.yaml`.

Downloaded folder/file names are configured separately with `download.folder_name_template` and `download.ad_file_name_template`. For example: `ad_{id} {title}` or `{title} ({id})`.

When an ad is published or republished and receives a new ID, the bot updates the `id` inside the existing local ad file; it does not rename local files or folders automatically.

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
- [bkd3sign/kleinanzeigen-bot-ui](https://github.com/bkd3sign/kleinanzeigen-bot-ui) (TypeScript) A self-hosted web interface for kleinanzeigen-bot

## <a name="license"></a>License

All files in this repository are released under the [GNU Affero General Public License v3.0 or later](LICENSE.txt).

Individual files contain the following tag instead of the full license text:

```text
SPDX-License-Identifier: AGPL-3.0-or-later
```

This enables machine processing of license information based on the SPDX License Identifiers that are available here: <https://spdx.org/licenses/>.
