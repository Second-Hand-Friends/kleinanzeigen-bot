# kleinanzeigen-bot

[![Build Status](https://github.com/kleinanzeigen-bot/kleinanzeigen-bot/workflows/Build/badge.svg "GitHub Actions")](https://github.com/kleinanzeigen-bot/kleinanzeigen-bot/actions?query=workflow%3A%22Build%22)
[![License](https://img.shields.io/github/license/kleinanzeigen-bot/kleinanzeigen-bot.svg?color=blue)](LICENSE.txt)

**Feedback and high-quality pull requests are  highly welcome!**

1. [About](#about)
1. [Installation](#installation)
1. [License](#license)

## <a name="about"></a>About

**kleinanzeigen-bot** is a console based application to ease publishing of ads to ebay-kleinanzeigen.de.


It is a spiritual successor to [donowayo/ebayKleinanzeigen](https://github.com/donwayo/ebayKleinanzeigen) with the following advantages:
- supports Microsoft Edge browser (Chromium based)
- necessary chromedriver is installed automatically
- better captcha handling
- config:
  - use YAML or JSON for config files
  - one config file per ad
  - use globbing (wildcards) to select images from local disk
  - reference categories by name (looked up from categories.yaml)
- logging is configurable and colorized
- provided as self-contained Windows exe
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
   curl https://github.com/vegardit/copycat/releases/download/latest/kleinanzeigen_bot.exe -o kleinanzeigen_bot.exe
   ```
1. Run the app:
   ```
   kleinanzeigen_bot --help
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

## Development

- Installing dev dependencies:
  ```
  pip install .[dev]
  ```

- Displaying effective version
  ```
  python setup.py --version
  ```

- Creating Windows executable:
  ```
  python setup.py py2exe
  ```


## <a name="license"></a>License

All files in this repository are released under the [GNU Affero General Public License v3.0 or later](LICENSE.txt).

Individual files contain the following tag instead of the full license text:
```
SPDX-License-Identifier: AGPL-3.0-or-later
```

This enables machine processing of license information based on the SPDX License Identifiers that are available here: https://spdx.org/licenses/.
