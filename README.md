# kleinanzeigen-bot

[![Build Status](https://github.com/kleinanzeigen-bot/kleinanzeigen-bot/workflows/Build/badge.svg "GitHub Actions")](https://github.com/kleinanzeigen-bot/kleinanzeigen-bot/actions?query=workflow%3A%22Build%22)
[![License](https://img.shields.io/github/license/kleinanzeigen-bot/kleinanzeigen-bot.svg?color=blue)](LICENSE.txt)
[![Maintainability](https://api.codeclimate.com/v1/badges/8d488c3a229bfb5091a3/maintainability)](https://codeclimate.com/github/kleinanzeigen-bot/kleinanzeigen-bot/maintainability)

**Feedback and high-quality pull requests are  highly welcome!**

1. [About](#about)
1. [Installation](#installation)
1. [Usage](#usage)
1. [Development Notes](#development)
1. [License](#license)

## <a name="about"></a>About

**kleinanzeigen-bot** is a console based application to ease publishing of ads to ebay-kleinanzeigen.de.


It is a spiritual successor to [AnzeigenOrg/ebayKleinanzeigen](https://github.com/AnzeigenOrg/ebayKleinanzeigen) with the following advantages:
- supports Microsoft Edge browser (Chromium based)
- compatible chromedriver is installed automatically
- better captcha handling
- config:
  - use YAML or JSON for config files
  - one config file per ad
  - use globbing (wildcards) to select images from local disk
  - reference categories by name (looked up from [categories.yaml](https://github.com/kleinanzeigen-bot/kleinanzeigen-bot/blob/main/kleinanzeigen_bot/resources/categories.yaml))
- logging is configurable and colorized
- provided as self-contained Windows executable [kleinanzeigen-bot.exe](https://github.com/kleinanzeigen-bot/kleinanzeigen-bot/releases/download/latest/kleinanzeigen-bot.exe)
- source code is pylint checked and uses Python type hints
- CI builds


## <a name="installation"></a>Installation

### Installation on Windows using self-containing exe

1. The following components need to be installed:
   1. [Chromium](https://www.chromium.org/getting-involved/download-chromium), [Google Chrome](https://www.google.com/chrome/),
      or Chromium based [Microsoft Edge](https://www.microsoft.com/edge) browser

1. Open a command/terminal window
1. Download the app using
   ```
   curl https://github.com/kleinanzeigen-bot/kleinanzeigen-bot/releases/download/latest/kleinanzeigen-bot.exe -o kleinanzeigen-bot.exe
   ```
1. Run the app:
   ```
   kleinanzeigen-bot --help
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
   git clone https://github.com/kleinanzeigen-bot/kleinanzeigen-bot/
   ```
1. Change into the directory:
   ```
   cd kleinanzeigen-bot
   ```
1. Install the Python dependencies using:
   ```
   pip install .
   ```
1. Run the app:
   ```
   python -m kleinanzeigen_bot --help
   ```

## <a name="usage"></a>Usage

```yaml
Usage: kleinanzeigen-bot COMMAND [-v|--verbose] [--config=<PATH>] [--logfile=<PATH>]

Commands:
  publish - (re-)publishes ads
  verify  - verifies the configuration files
  --
  help    - displays this help (default command)
  version - displays the application version
```

### Configuration

All configuration files can be in YAML or JSON format.

#### 1) Main configuration

When executing the app it by default looks for a `config.yaml` file in the current directory. If it does not exist it will be created automatically.

The configuration file to be used can also be specified using the `--config <PATH>` command line parameter. It must point to a YAML or JSON file.
Valid file extensions are `.json`, `.yaml` and `.yml`

The following parameters can be configured:

```yaml
# wild card patterns to select ad configuration files
# if relative paths are specified, then they are relative to this configuration file
ad_files:
  - "my_ads/**/ad_*.json"
  - "my_ads/**/ad_*.yml"
  - "my_ads/**/ad_*.yaml"

# default values for ads, can be overwritten in each ad configuration file
ad_defaults:
  active: true
  type: # one of: OFFER, WANTED
  description:
    prefix:
    suffix:
  price_type: # one of: FIXED, NEGOTIABLE, GIVE_AWAY
  shipping_type: # one of: PICKUP, SHIPPING, NOT_APPLICABLE
  contact:
    name:
    street:
    zipcode:
    phone:
  republication_interval: # every X days ads should be re-published

# additional name to category ID mappings, see default list at
# https://github.com/kleinanzeigen-bot/kleinanzeigen-bot/blob/main/kleinanzeigen_bot/resources/categories.yaml
categories:
 #Notebooks: 161/27
 #PCs: 161/228

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

# login credentials
login:
  username:
  password:

```

#### 2) Ad configuration

Each ad is described in a separate JSON or YAML file.

Parameter values specified in the `ad_defaults` section of the `config.yaml` file don't need to be specified again in the ad configuration file.

The following parameters can be configured:

```yaml
active: # true or false
type: # one of: OFFER, WANTED
title:
description: # can be multiline, see syntax here https://yaml-multiline.info/

# built-in category name as specified in https://github.com/kleinanzeigen-bot/kleinanzeigen-bot/blob/main/kleinanzeigen_bot/resources/categories.yaml
# or custom category name as specified in config.yaml
# or category ID (e.g. 161/27)
category: Notebooks

price:
price_type: # one of: FIXED, NEGOTIABLE, GIVE_AWAY

shipping_type: # one of: PICKUP, SHIPPING, NOT_APPLICABLE

# list of wildcard patterns to select images
# if relative paths are specified, then they are relative to this ad configuration file
images:
 #- laptop_*.jpg
 #- laptop_*.png

contact:
  name:
  street:
  zipcode:
  phone:

republication_interval: # every X days the ad should be re-published

id: # set automatically
created_on: # set automatically
updated_on: # set automatically
```

## <a name="development"></a> Development Notes

- Installing dev dependencies: `pip install .[dev]`
- Running unit tests: `python -m pytest` or `pytest`
- Running linter: `python -m pylint kleinanzeigen_bot` or `pylint kleinanzeigen_bot`
- Displaying effective version:`python setup.py --version`
- Creating Windows executable: `python setup.py py2exe`
- Application bootstrap works like this:
  ```python
  python -m kleinanzeigen_bot
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
