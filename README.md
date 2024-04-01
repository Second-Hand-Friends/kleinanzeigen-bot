# kleinanzeigen-bot

[![Build Status](https://github.com/Second-Hand-Friends/kleinanzeigen-bot/workflows/Build/badge.svg "GitHub Actions")](https://github.com/Second-Hand-Friends/kleinanzeigen-bot/actions?query=workflow%3A%22Build%22)
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
1. [License](#license)


## <a name="about"></a>About

**kleinanzeigen-bot** is a console based application to ease publishing of ads to [kleinanzeigen.de](https://kleinanzeigen.de).

It is the spiritual successor to [Second-Hand-Friends/ebayKleinanzeigen](https://github.com/Second-Hand-Friends/ebayKleinanzeigen) with the following advantages:
- supports Microsoft Edge browser (Chromium based)
- does not require selenium and chromedrivers
- better captcha handling
- config:
  - use YAML or JSON for config files
  - one config file per ad
  - use globbing (wildcards) to select images from local disk via [wcmatch](https://facelessuser.github.io/wcmatch/glob/#syntax)
  - reference categories by name (looked up from [categories.yaml](https://github.com/Second-Hand-Friends/kleinanzeigen-bot/blob/main/src/kleinanzeigen_bot/resources/categories.yaml))
- logging is configurable and colorized
- provided as self-contained executable for Windows, Linux and macOS
- source code is pylint/bandit/mypy checked and uses Python type hints
- CI builds


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
  --ads=all|due|new|<id(s)> (publish) - specifies which ads to (re-)publish (DEFAULT: due)
        Possible values:
        * all: (re-)publish all ads ignoring republication_interval
        * due: publish all new ads and republish ads according the republication_interval
        * new: only publish new ads (i.e. ads that have no id in the config file)
        * <id(s)>: provide one or several ads by ID to (re-)publish, like e.g. "--ads=1,2,3" ignoring republication_interval
  --ads=all|new|<id(s)> (download) - specifies which ads to download (DEFAULT: new)
        Possible values:
        * all: downloads all ads from your profile
        * new: downloads ads from your profile that are not locally saved yet
        * <id(s)>: provide one or several ads by ID to download, like e.g. "--ads=1,2,3"
  --force           - alias for '--ads=all'
  --keep-old        - don't delete old ads on republication
  --config=<PATH>   - path to the config YAML or JSON file (DEFAULT: ./config.yaml)
  --logfile=<PATH>  - path to the logfile (DEFAULT: ./kleinanzeigen-bot.log)
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
# wild card patterns to select ad configuration files
# if relative paths are specified, then they are relative to this configuration file
ad_files:
  - "./**/ad_*.{json,yml,yaml}"

# default values for ads, can be overwritten in each ad configuration file
ad_defaults:
  active: true
  type: OFFER # one of: OFFER, WANTED
  description:
    prefix: ""
    suffix: ""
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
 #Notebooks: 161/278 # Elektronik > Notebooks
 #Autoteile: 210/223/sonstige_autoteile # Auto, Rad & Boot > Autoteile & Reifen > Weitere Autoteile

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
active: # true or false
type: # one of: OFFER, WANTED
title:
description: # can be multiline, see syntax here https://yaml-multiline.info/

# built-in category name as specified in https://github.com/Second-Hand-Friends/kleinanzeigen-bot/blob/main/src/kleinanzeigen_bot/resources/categories.yaml
# or custom category name as specified in config.yaml
# or category ID (e.g. 161/27)
category: Notebooks

price: # without decimals, e.g. 75
price_type: # one of: FIXED, NEGOTIABLE, GIVE_AWAY

special_attributes:
  # haus_mieten.zimmer_d: value # Zimmer

shipping_type: # one of: PICKUP, SHIPPING, NOT_APPLICABLE
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
# - DHL_31,5
# - Hermes_L
shipping_options: []
sell_directly: # true or false, requires shipping_options to take effect

# list of wildcard patterns to select images
# if relative paths are specified, then they are relative to this ad configuration file
images:
 #- laptop_*.{jpg,png}

contact:
  name:
  street:
  zipcode:
  phone: "" # IMPORTANT: surround phone number with quotes to prevent removal of leading zeros

republication_interval: # every X days the ad should be re-published

id: # set automatically
created_on: # set automatically
updated_on: # set automatically
```

### <a name="existing-browser"></a>3) Using an existing browser window

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
  - unit tests: `pdm run utest`
  - integration tests: `pdm run itest`
  - all tests: `pdm run test`
- Run syntax checks: `pdm run lint`
- Create platform-specific executable: `pdm run compile`
- Application bootstrap works like this:
  ```python
  pdm run app
  |-> executes 'python -m kleinanzeigen_bot'
      |-> executes 'kleinanzeigen_bot/__main__.py'
          |-> executes main() function of 'kleinanzeigen_bot/__init__.py'
              |-> executes KleinanzeigenBot().run()
  ````


## <a name="license"></a>License

All files in this repository are released under the [GNU Affero General Public License v3.0 or later](LICENSE.txt).

Individual files contain the following tag instead of the full license text:
```
SPDX-License-Identifier: AGPL-3.0-or-later
```

This enables machine processing of license information based on the SPDX License Identifiers that are available here: https://spdx.org/licenses/.
