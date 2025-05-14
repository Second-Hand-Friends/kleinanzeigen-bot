# kleinanzeigen-bot

[![Build Status](https://github.com/Second-Hand-Friends/kleinanzeigen-bot/actions/workflows/build.yml/badge.svg)](https://github.com/Second-Hand-Friends/kleinanzeigen-bot/actions/workflows/build.yml)
[![License](https://img.shields.io/github/license/Second-Hand-Friends/kleinanzeigen-bot.svg?color=blue)](LICENSE.txt)
[![Contributor Covenant](https://img.shields.io/badge/Contributor%20Covenant-v2.1%20adopted-ff69b4.svg)](CODE_OF_CONDUCT.md)
[![Maintainability](https://api.codeclimate.com/v1/badges/77b4ed9cc0dd8cfe373c/maintainability)](https://codeclimate.com/github/Second-Hand-Friends/kleinanzeigen-bot/maintainability)

**Feedback and high-quality pull requests are highly welcome!**

1. [About](#about)
1. [Installation](#installation)
1. [Usage](#usage)
1. [Configuration](#config)
   1. [Main configuration](#main-config)
   1. [Ad configuration](#ad-config)
   1. [Using an existing browser window](#existing-browser)
1. [Development Notes](#development)
1. [Related Open-Source Projects](#related)
1. [License](#license)


## <a name="about"></a>About

**kleinanzeigen-bot** is a console-based application to simplify the process of publishing ads on kleinanzeigen.de.
It is a spiritual successor to [Second-Hand-Friends/ebayKleinanzeigen](https://github.com/Second-Hand-Friends/ebayKleinanzeigen).

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
      or Chromium based [Microsoft Edge](https://www.microsoft.com/edge) browser

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
   1. [X11 - X Window System](https://en.wikipedia.org/wiki/X_Window_System) display server (on Windows e.g. https://github.com/P-St/Portable-X-Server/releases/latest)

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
      or Chromium based [Microsoft Edge](https://www.microsoft.com/edge) browser
   1. [Python](https://www.python.org/) **3.10** or newer
   1. [pip](https://pypi.org/project/pip/)
   1. [git client](https://git-scm.com/downloads)

1. Open a command/terminal window
1. Clone the repo using
   ```
   git clone https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
   ```
1. Change into the directory:
   ```
   cd kleinanzeigen-bot
   ```
1. Install the Python dependencies using:
   ```bash
   pip install pdm

   pdm install
   ```
1. Run the app:
   ```
   pdm run app --help
   ```

### Installation from source using Docker

1. The following components need to be installed:
   1. [Docker](https://www.docker.com/)
   1. [git client](https://git-scm.com/downloads)
   1. [Bash](https://www.gnu.org/software/bash/) (on Windows e.g. via [Cygwin](https://www.cygwin.com/), [MSys2](https://www.msys2.org/) or git)
   1. [X11 - X Window System](https://en.wikipedia.org/wiki/X_Window_System) display server (on Windows e.g. https://github.com/P-St/Portable-X-Server/releases/latest)

1. Clone the repo using
   ```
   git clone https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
   ```

1. Open the cloned directory in a Bash terminal window and navigate to the [docker](docker) subdirectory

1. Execute `bash build-image.sh`

1. Ensure the image is build:

   ```
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

```
Usage: kleinanzeigen-bot COMMAND [OPTIONS]

Commands:
  publish  - (re-)publishes ads
  verify   - verifies the configuration files
  delete   - deletes ads
  download - downloads one or multiple ads
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
  --force           - alias for '--ads=all'
  --keep-old        - don't delete old ads on republication
  --config=<PATH>   - path to the config YAML or JSON file (DEFAULT: ./config.yaml)
  --logfile=<PATH>  - path to the logfile (DEFAULT: ./kleinanzeigen-bot.log)
  --lang=en|de      - display language (STANDARD: system language if supported, otherwise English)
  -v, --verbose     - enables verbose output - only useful when troubleshooting issues
```

Limitation of `download`: It's only possible to extract the cheapest given shipping option.

## <a name="config"></a>Configuration

All configuration files can be in YAML or JSON format.

### <a name="main-config"></a>1) Main configuration

When executing the app it by default looks for a `config.yaml` file in the current directory. If it does not exist it will be created automatically.

The configuration file to be used can also be specified using the `--config <PATH>` command line parameter. It must point to a YAML or JSON file.
Valid file extensions are `.json`, `.yaml` and `.yml`

The following parameters can be configured:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/Second-Hand-Friends/kleinanzeigen-bot/refs/heads/main/schemas/config.schema.json

# glob (wildcard) patterns to select ad configuration files
# if relative paths are specified, then they are relative to this configuration file
ad_files:
  - "./**/ad_*.{json,yml,yaml}"

# default values for ads, can be overwritten in each ad configuration file
ad_defaults:
  active: true
  type: OFFER # one of: OFFER, WANTED

  description_prefix: ""
  description_suffix: ""

  price_type: NEGOTIABLE # one of: FIXED, NEGOTIABLE, GIVE_AWAY, NOT_APPLICABLE
  shipping_type: SHIPPING # one of: PICKUP, SHIPPING, NOT_APPLICABLE
  shipping_costs: # e.g. 2.95
  sell_directly: false # requires shipping_options to take effect
  contact:
    name: ""
    street: ""
    zipcode:
    phone: "" # IMPORTANT: surround phone number with quotes to prevent removal of leading zeros
  republication_interval: 7 # every X days ads should be re-published

# additional name to category ID mappings, see default list at
# https://github.com/Second-Hand-Friends/kleinanzeigen-bot/blob/main/src/kleinanzeigen_bot/resources/categories.yaml
categories:
  Verschenken & Tauschen > Tauschen: 272/273
  Verschenken & Tauschen > Verleihen: 272/274
  Verschenken & Tauschen > Verschenken: 272/192

# publishing configuration
publishing:
  delete_old_ads: "AFTER_PUBLISH" # one of: AFTER_PUBLISH, BEFORE_PUBLISH, NEVER
  delete_old_ads_by_title: true # only works if delete_old_ads is set to BEFORE_PUBLISH

# captcha-Handling (optional)
# To ensure that the bot does not require manual confirmation after a captcha, but instead automatically pauses for a defined period and then restarts, you can enable the captcha section:

captcha:
  auto_restart: true  # If true, the bot aborts when a Captcha appears and retries publishing later
                      # If false (default), the Captcha must be solved manually to continue
  restart_delay: 1h 30m  # Time to wait before retrying after a Captcha was encountered (default: 6h)

# browser configuration
browser:
  # https://peter.sh/experiments/chromium-command-line-switches/
  arguments:
    # https://stackoverflow.com/a/50725918/5116073
    - --disable-dev-shm-usage
    - --no-sandbox
    # --headless
    # --start-maximized
  binary_location: # path to custom browser executable, if not specified will be looked up on PATH
  extensions: [] # a list of .crx extension files to be loaded
  use_private_window: true
  user_data_dir: "" # see https://github.com/chromium/chromium/blob/main/docs/user_data_dir.md
  profile_name: ""

# login credentials
login:
  username: ""
  password: ""
```

### <a name="ad-config"></a>2) Ad configuration

Each ad is described in a separate JSON or YAML file with prefix `ad_<filename>`. The prefix is configurable in config file.

Parameter values specified in the `ad_defaults` section of the `config.yaml` file don't need to be specified again in the ad configuration file.

The following parameters can be configured:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/Second-Hand-Friends/kleinanzeigen-bot/refs/heads/main/schemas/ad.schema.json
active: # true or false (default: true)
type: # one of: OFFER, WANTED (default: OFFER)
title:
description: # can be multiline, see syntax here https://yaml-multiline.info/

description_prefix: # optional prefix to be added to the description overriding the default prefix
description_suffix: # optional suffix to be added to the description overriding the default suffix

# built-in category name as specified in https://github.com/Second-Hand-Friends/kleinanzeigen-bot/blob/main/src/kleinanzeigen_bot/resources/categories.yaml
# or custom category name as specified in config.yaml
# or category ID (e.g. 161/278)
category: # e.g. "Elektronik > Notebooks"

price: # without decimals, e.g. 75
price_type: # one of: FIXED, NEGOTIABLE, GIVE_AWAY (default: NEGOTIABLE)

special_attributes:
  # haus_mieten.zimmer_d: value # Zimmer

shipping_type: # one of: PICKUP, SHIPPING, NOT_APPLICABLE (default: SHIPPING)
shipping_costs: # e.g. 2.95

# specify shipping options / packages
# it is possible to select multiple packages, but only from one size (S, M, L)!
# possible package types for size S:
# - DHL_2
# - Hermes_Päckchen
# - Hermes_S
# possible package types for size M:
# - DHL_5
# - Hermes_M
# possible package types for size L:
# - DHL_10
# - DHL_20
# - DHL_31,5
# - Hermes_L
shipping_options: []
sell_directly: # true or false, requires shipping_options to take effect (default: false)

# list of wildcard patterns to select images
# if relative paths are specified, then they are relative to this ad configuration file
images:
 #- laptop_*.{jpg,png}

contact:
  name:
  street:
  zipcode:
  phone: "" # IMPORTANT: surround phone number with quotes to prevent removal of leading zeros

republication_interval: # every X days the ad should be re-published (default: 7)

# The following fields are automatically managed by the bot:
id: # the ID assigned by kleinanzeigen.de
created_on: # ISO timestamp when the ad was first published
updated_on: # ISO timestamp when the ad was last published
content_hash: # hash of the ad content, used to detect changes
```

### <a name="description-prefix-suffix"></a>3) Description Prefix and Suffix

You can add prefix and suffix text to your ad descriptions in two ways:

#### New Format (Recommended)

In your config.yaml file you can specify a `description_prefix` and `description_suffix` under the `ad_defaults` section.

```yaml
ad_defaults:
  description_prefix: "Prefix text"
  description_suffix: "Suffix text"
```

#### Legacy Format

In your ad configuration file you can specify a `description_prefix` and `description_suffix` under the `description` section.

```yaml
description:
  prefix: "Prefix text"
  suffix: "Suffix text"
```

#### Precedence

The new format has precedence over the legacy format. If you specify both the new and the legacy format in your config, the new format will be used. We recommend using the new format as it is more flexible and easier to manage.

### <a name="existing-browser"></a>4) Using an existing browser window

By default a new browser process will be launched. To reuse a manually launched browser window/process follow these steps:

1. Manually launch your browser from the command line with the `--remote-debugging-port=<NUMBER>` flag.
   You are free to choose an unused port number 1025 and 65535, e.g.:
   - `chrome --remote-debugging-port=9222`
   - `chromium --remote-debugging-port=9222`
   - `msedge --remote-debugging-port=9222`

   This runs the browser in debug mode which allows it to be remote controlled by the bot.

1. In your config.yaml specify the same flag as browser argument, e.g.:
   ```yaml
   browser:
     arguments:
     - --remote-debugging-port=9222
   ```

1. When now publishing ads the manually launched browser will be re-used.

> NOTE: If an existing browser is used all other settings configured under `browser` in your config.yaml file will ignored
  because they are only used to programmatically configure/launch a dedicated browser instance.

## <a name="development"></a>Development Notes

> Please read [CONTRIBUTING.md](CONTRIBUTING.md) before contributing code. Thank you!

- Format source code: `pdm run format`
- Run tests:
  - unit tests: `pdm run utest` - with coverage: `pdm run utest:cov`
  - integration tests: `pdm run itest` - with coverage: `pdm run itest:cov`
  - all tests: `pdm run test` - with coverage: `pdm run test:cov`
- Run syntax checks: `pdm run lint`
- Linting issues found by ruff can be auto-fixed using `pdm run lint:fix`
- Derive JSON schema files from Pydantic data model: `pdm run generate-schemas`
- Create platform-specific executable: `pdm run compile`
- Application bootstrap works like this:
  ```python
  pdm run app
  |-> executes 'python -m kleinanzeigen_bot'
      |-> executes 'kleinanzeigen_bot/__main__.py'
          |-> executes main() function of 'kleinanzeigen_bot/__init__.py'
              |-> executes KleinanzeigenBot().run()
  ````


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
```
SPDX-License-Identifier: AGPL-3.0-or-later
```

This enables machine processing of license information based on the SPDX License Identifiers that are available here: https://spdx.org/licenses/.
