"""
Copyright (C) 2022 Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import atexit, copy, getopt, importlib.metadata, json, logging, os, signal, sys, textwrap, time, urllib
import shutil
from collections.abc import Iterable
from datetime import datetime
from decimal import DecimalException
from logging.handlers import RotatingFileHandler
from typing import Any, Final, Dict
from wcmatch import glob

from overrides import overrides
from ruamel.yaml import YAML
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from urllib import request

from . import utils, resources
from .utils import abspath, apply_defaults, ensure, is_frozen, pause, pluralize, safe_get
from .selenium_mixin import SeleniumMixin

LOG_ROOT: Final[logging.Logger] = logging.getLogger()
LOG: Final[logging.Logger] = logging.getLogger("kleinanzeigen_bot")
LOG.setLevel(logging.INFO)


class KleinanzeigenBot(SeleniumMixin):

    def __init__(self) -> None:
        super().__init__()

        self.root_url = "https://www.ebay-kleinanzeigen.de"

        self.config: dict[str, Any] = {}
        self.config_file_path = abspath("config.yaml")

        self.categories: dict[str, str] = {}

        self.file_log: logging.FileHandler | None = None
        if is_frozen():
            log_file_basename = os.path.splitext(os.path.basename(sys.executable))[0]
        else:
            log_file_basename = self.__module__
        self.log_file_path: str | None = abspath(f"{log_file_basename}.log")

        self.command = "help"
        self.ads_selector = "due"
        self.delete_old_ads = True
        self.delete_ads_by_title = False
        self.ad_id = None  # attribute needed when downloading an ad

    def __del__(self) -> None:
        if self.file_log:
            LOG_ROOT.removeHandler(self.file_log)

    def get_version(self) -> str:
        return importlib.metadata.version(__package__)

    def run(self, args: list[str]) -> None:
        self.parse_args(args)
        match self.command:
            case "help":
                self.show_help()
            case "version":
                print(self.get_version())
            case "verify":
                self.configure_file_logging()
                self.load_config()
                self.load_ads()
                LOG.info("############################################")
                LOG.info("DONE: No configuration errors found.")
                LOG.info("############################################")
            case "publish":
                self.configure_file_logging()
                self.load_config()
                if ads := self.load_ads():
                    self.create_webdriver_session()
                    self.login()
                    self.publish_ads(ads)
                else:
                    LOG.info("############################################")
                    LOG.info("DONE: No new/outdated ads found.")
                    LOG.info("############################################")
            case "delete":
                self.configure_file_logging()
                self.load_config()
                if ads := self.load_ads():
                    self.create_webdriver_session()
                    self.login()
                    self.delete_ads(ads)
                else:
                    LOG.info("############################################")
                    LOG.info("DONE: No ads to delete found.")
                    LOG.info("############################################")
            case "download":
                self.configure_file_logging()
                # ad ID passed as value to download command
                if self.ad_id is None:
                    LOG.error('Provide the flag \'--ad\' with a valid ad ID to use the download command!')
                    sys.exit(2)
                if self.ad_id < 1:
                    LOG.error('The given ad ID must be valid!')
                    sys.exit(2)
                LOG.info('Start fetch task for ad with ID %s', str(self.ad_id))

                self.load_config()
                self.create_webdriver_session()
                self.login()
                # call download function
                exists = self.navigate_to_ad_page()
                if exists:
                    self.download_ad_page()
                else:
                    sys.exit(2)
            case _:
                LOG.error("Unknown command: %s", self.command)
                sys.exit(2)

    def show_help(self) -> None:
        if is_frozen():
            exe = sys.argv[0]
        elif os.getenv("PDM_PROJECT_ROOT", ""):
            exe = "pdm run app"
        else:
            exe = "python -m kleinanzeigen_bot"

        print(textwrap.dedent(f"""\
            Usage: {exe} COMMAND [OPTIONS]

            Commands:
              publish  - (re-)publishes ads
              verify   - verifies the configuration files
              delete   - deletes ads
              download - downloads an ad
              --
              help    - displays this help (default command)
              version - displays the application version

            Options:
              --ads=all|due|new - specifies which ads to (re-)publish (DEFAULT: due)
                    Possible values:
                    * all: (re-)publish all ads ignoring republication_interval
                    * due: publish all new ads and republish ads according the republication_interval
                    * new: only publish new ads (i.e. ads that have no id in the config file)
              --force           - alias for '--ads=all'
              --keep-old        - don't delete old ads on republication
              --ad <ID>         - provide the ad ID after this option when using the download command
              --config=<PATH>   - path to the config YAML or JSON file (DEFAULT: ./config.yaml)
              --logfile=<PATH>  - path to the logfile (DEFAULT: ./kleinanzeigen-bot.log)
              -v, --verbose     - enables verbose output - only useful when troubleshooting issues
        """))

    def parse_args(self, args: list[str]) -> None:
        try:
            options, arguments = getopt.gnu_getopt(args[1:], "hv", [
                "ads=",
                "config=",
                "force",
                "help",
                "keep-old",
                "ad=",
                "logfile=",
                "verbose"
            ])
        except getopt.error as ex:
            LOG.error(ex.msg)
            LOG.error("Use --help to display available options")
            sys.exit(2)

        for option, value in options:
            match option:
                case "-h" | "--help":
                    self.show_help()
                    sys.exit(0)
                case "--config":
                    self.config_file_path = abspath(value)
                case "--logfile":
                    if value:
                        self.log_file_path = abspath(value)
                    else:
                        self.log_file_path = None
                case "--ads":
                    self.ads_selector = value.strip().lower()
                case "--force":
                    self.ads_selector = "all"
                case "--keep-old":
                    self.delete_old_ads = False
                case "--ad":
                    try:
                        self.ad_id: int = int(value)
                    except ValueError:  # given value cannot be parsed as integer
                        LOG.error('The given ad ID (\"%s\") is not a valid number!', value)
                        sys.exit(2)
                case "-v" | "--verbose":
                    LOG.setLevel(logging.DEBUG)

        match len(arguments):
            case 0:
                self.command = "help"
            case 1:
                self.command = arguments[0]
            case _:
                LOG.error("More than one command given: %s", arguments)
                sys.exit(2)

    def configure_file_logging(self) -> None:
        if not self.log_file_path:
            return
        if self.file_log:
            return

        LOG.info("Logging to [%s]...", self.log_file_path)
        self.file_log = RotatingFileHandler(filename=self.log_file_path, maxBytes=10 * 1024 * 1024, backupCount=10,
                                            encoding="utf-8")
        self.file_log.setLevel(logging.DEBUG)
        self.file_log.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        LOG_ROOT.addHandler(self.file_log)

        LOG.info("App version: %s", self.get_version())

    def load_ads(self, *, ignore_inactive: bool = True) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
        LOG.info("Searching for ad config files...")

        ad_files = set()
        data_root_dir = os.path.dirname(self.config_file_path)
        for file_pattern in self.config["ad_files"]:
            for ad_file in glob.glob(file_pattern, root_dir=data_root_dir,
                                     flags=glob.GLOBSTAR | glob.BRACE | glob.EXTGLOB):
                ad_files.add(abspath(ad_file, relative_to=data_root_dir))
        LOG.info(" -> found %s", pluralize("ad config file", ad_files))
        if not ad_files:
            return []

        descr_prefix = self.config["ad_defaults"]["description"]["prefix"] or ""
        descr_suffix = self.config["ad_defaults"]["description"]["suffix"] or ""

        ad_fields = utils.load_dict_from_module(resources, "ad_fields.yaml")
        ads = []
        for ad_file in sorted(ad_files):

            ad_cfg_orig = utils.load_dict(ad_file, "ad")
            ad_cfg = copy.deepcopy(ad_cfg_orig)
            apply_defaults(ad_cfg, self.config["ad_defaults"], ignore=lambda k, _: k == "description",
                           override=lambda _, v: v == "")
            apply_defaults(ad_cfg, ad_fields)

            if ignore_inactive and not ad_cfg["active"]:
                LOG.info(" -> SKIPPED: inactive ad [%s]", ad_file)
                continue

            if self.ads_selector == "new" and ad_cfg["id"]:
                LOG.info(" -> SKIPPED: ad [%s] is not new. already has an id assigned.", ad_file)
                continue

            if self.ads_selector == "due":
                if ad_cfg["updated_on"]:
                    last_updated_on = datetime.fromisoformat(ad_cfg["updated_on"])
                elif ad_cfg["created_on"]:
                    last_updated_on = datetime.fromisoformat(ad_cfg["created_on"])
                else:
                    last_updated_on = None

                if last_updated_on:
                    ad_age = datetime.utcnow() - last_updated_on
                    if ad_age.days <= ad_cfg["republication_interval"]:
                        LOG.info(
                            " -> SKIPPED: ad [%s] was last published %d days ago. republication is only required every %s days",
                            ad_file,
                            ad_age.days,
                            ad_cfg["republication_interval"]
                        )
                        continue

            ad_cfg["description"] = descr_prefix + (ad_cfg["description"] or "") + descr_suffix
            ensure(len(ad_cfg["description"]) <= 4000, f"Length of ad description including prefix and suffix exceeds 4000 chars. @ [{ad_file}]")

            # pylint: disable=cell-var-from-loop
            def assert_one_of(path: str, allowed: Iterable[str]) -> None:
                ensure(safe_get(ad_cfg, *path.split(".")) in allowed,
                       f"-> property [{path}] must be one of: {allowed} @ [{ad_file}]")

            def assert_min_len(path: str, minlen: int) -> None:
                ensure(len(safe_get(ad_cfg, *path.split("."))) >= minlen,
                       f"-> property [{path}] must be at least {minlen} characters long @ [{ad_file}]")

            def assert_has_value(path: str) -> None:
                ensure(safe_get(ad_cfg, *path.split(".")), f"-> property [{path}] not specified @ [{ad_file}]")

            # pylint: enable=cell-var-from-loop

            assert_one_of("type", {"OFFER", "WANTED"})
            assert_min_len("title", 10)
            assert_has_value("description")
            assert_one_of("price_type", {"FIXED", "NEGOTIABLE", "GIVE_AWAY", "NOT_APPLICABLE"})
            if ad_cfg["price_type"] == "GIVE_AWAY":
                ensure(not safe_get(ad_cfg, "price"),
                       f"-> [price] must not be specified for GIVE_AWAY ad @ [{ad_file}]")
            elif ad_cfg["price_type"] == "FIXED":
                assert_has_value("price")
            assert_one_of("shipping_type", {"PICKUP", "SHIPPING", "NOT_APPLICABLE"})
            assert_has_value("contact.name")
            assert_has_value("republication_interval")

            if ad_cfg["id"]:
                ad_cfg["id"] = int(ad_cfg["id"])

            if ad_cfg["category"]:
                ad_cfg["category"] = self.categories.get(ad_cfg["category"], ad_cfg["category"])

            if ad_cfg["shipping_costs"]:
                ad_cfg["shipping_costs"] = str(round(utils.parse_decimal(ad_cfg["shipping_costs"]), 2))

            if ad_cfg["images"]:
                images = []
                for image_pattern in ad_cfg["images"]:
                    pattern_images = set()
                    ad_dir = os.path.dirname(ad_file)
                    for image_file in glob.glob(image_pattern, root_dir=ad_dir,
                                                flags=glob.GLOBSTAR | glob.BRACE | glob.EXTGLOB):
                        _, image_file_ext = os.path.splitext(image_file)
                        ensure(image_file_ext.lower() in {".gif", ".jpg", ".jpeg", ".png"},
                               f"Unsupported image file type [{image_file}]")
                        if os.path.isabs(image_file):
                            pattern_images.add(image_file)
                        else:
                            pattern_images.add(abspath(image_file, relative_to=ad_file))
                    images.extend(sorted(pattern_images))
                ensure(images or not ad_cfg["images"],
                       f"No images found for given file patterns {ad_cfg['images']} at {ad_dir}")
                ad_cfg["images"] = list(dict.fromkeys(images))

            ads.append((
                ad_file,
                ad_cfg,
                ad_cfg_orig
            ))

        LOG.info("Loaded %s", pluralize("ad", ads))
        return ads

    def load_config(self) -> None:
        config_defaults = utils.load_dict_from_module(resources, "config_defaults.yaml")
        config = utils.load_dict_if_exists(self.config_file_path, "config")

        if config is None:
            LOG.warning("Config file %s does not exist. Creating it with default values...", self.config_file_path)
            utils.save_dict(self.config_file_path, config_defaults)
            config = {}

        self.config = apply_defaults(config, config_defaults)

        self.categories = utils.load_dict_from_module(resources, "categories.yaml", "categories")
        if self.config["categories"]:
            self.categories.update(self.config["categories"])
        LOG.info(" -> found %s", pluralize("category", self.categories))

        ensure(self.config["login"]["username"], f"[login.username] not specified @ [{self.config_file_path}]")
        ensure(self.config["login"]["password"], f"[login.password] not specified @ [{self.config_file_path}]")

        self.browser_config.arguments = self.config["browser"]["arguments"]
        self.browser_config.binary_location = self.config["browser"]["binary_location"]
        self.browser_config.extensions = [abspath(item, relative_to=self.config_file_path) for item in
                                          self.config["browser"]["extensions"]]
        self.browser_config.use_private_window = self.config["browser"]["use_private_window"]
        if self.config["browser"]["user_data_dir"]:
            self.browser_config.user_data_dir = abspath(self.config["browser"]["user_data_dir"],
                                                        relative_to=self.config_file_path)
        self.browser_config.profile_name = self.config["browser"]["profile_name"]

    def login(self) -> None:
        LOG.info("Logging in as [%s]...", self.config["login"]["username"])
        self.web_open(f"{self.root_url}/m-einloggen.html?targetUrl=/")

        # accept privacy banner
        try:
            self.web_click(By.ID, "gdpr-banner-accept")
        except NoSuchElementException:
            pass

        self.web_input(By.ID, "login-email", self.config["login"]["username"])
        self.web_input(By.ID, "login-password", self.config["login"]["password"])

        self.handle_captcha_if_present("login-recaptcha", "but DON'T click 'Einloggen'.")

        self.web_click(By.ID, "login-submit")

        pause(800, 3000)

    def handle_captcha_if_present(self, captcha_element_id: str, msg: str) -> None:
        try:
            self.web_click(By.XPATH, f"//*[@id='{captcha_element_id}']")
        except NoSuchElementException:
            return

        LOG.warning("############################################")
        LOG.warning("# Captcha present! Please solve and close the captcha, %s", msg)
        LOG.warning("############################################")
        self.webdriver.switch_to.frame(self.web_find(By.CSS_SELECTOR, f"#{captcha_element_id} iframe"))
        self.web_await(
            lambda _: self.webdriver.find_element(By.ID, "recaptcha-anchor").get_attribute("aria-checked") == "true",
            timeout=5 * 60)
        self.webdriver.switch_to.default_content()

    def delete_ads(self, ad_cfgs: list[tuple[str, dict[str, Any], dict[str, Any]]]) -> None:
        count = 0

        for (ad_file, ad_cfg, _) in ad_cfgs:
            count += 1
            LOG.info("Processing %s/%s: '%s' from [%s]...", count, len(ad_cfgs), ad_cfg["title"], ad_file)
            self.delete_ad(ad_cfg)
            pause(2000, 4000)

        LOG.info("############################################")
        LOG.info("DONE: Deleting %s", pluralize("ad", count))
        LOG.info("############################################")

    def delete_ad(self, ad_cfg: dict[str, Any]) -> bool:
        LOG.info("Deleting ad '%s' if already present...", ad_cfg["title"])

        self.web_open(f"{self.root_url}/m-meine-anzeigen.html")
        csrf_token_elem = self.web_find(By.XPATH, "//meta[@name='_csrf']")
        csrf_token = csrf_token_elem.get_attribute("content")

        if self.delete_ads_by_title:
            published_ads = \
                json.loads(
                    self.web_request(f"{self.root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT")["content"])[
                    "ads"]

            for published_ad in published_ads:
                published_ad_id = int(published_ad.get("id", -1))
                published_ad_title = published_ad.get("title", "")
                if ad_cfg["id"] == published_ad_id or ad_cfg["title"] == published_ad_title:
                    LOG.info(" -> deleting %s '%s'...", published_ad_id, published_ad_title)
                    self.web_request(
                        url=f"{self.root_url}/m-anzeigen-loeschen.json?ids={published_ad_id}",
                        method="POST",
                        headers={"x-csrf-token": csrf_token}
                    )
        elif ad_cfg["id"]:
            self.web_request(
                url=f"{self.root_url}/m-anzeigen-loeschen.json?ids={ad_cfg['id']}",
                method="POST",
                headers={"x-csrf-token": csrf_token},
                valid_response_codes=[200, 404]
            )

        pause(1500, 3000)
        ad_cfg["id"] = None
        return True

    def publish_ads(self, ad_cfgs: list[tuple[str, dict[str, Any], dict[str, Any]]]) -> None:
        count = 0

        for (ad_file, ad_cfg, ad_cfg_orig) in ad_cfgs:
            count += 1
            LOG.info("Processing %s/%s: '%s' from [%s]...", count, len(ad_cfgs), ad_cfg["title"], ad_file)
            self.publish_ad(ad_file, ad_cfg, ad_cfg_orig)
            pause(3000, 5000)

        LOG.info("############################################")
        LOG.info("DONE: (Re-)published %s", pluralize("ad", count))
        LOG.info("############################################")

    def publish_ad(self, ad_file: str, ad_cfg: dict[str, Any], ad_cfg_orig: dict[str, Any]) -> None:
        self.assert_free_ad_limit_not_reached()

        if self.delete_old_ads:
            self.delete_ad(ad_cfg)

        LOG.info("Publishing ad '%s'...", ad_cfg["title"])

        if LOG.isEnabledFor(logging.DEBUG):
            LOG.debug(" -> effective ad meta:")
            YAML().dump(ad_cfg, sys.stdout)

        self.web_open(f"{self.root_url}/p-anzeige-aufgeben-schritt2.html")

        if ad_cfg["type"] == "WANTED":
            self.web_click(By.ID, "adType2")

        #############################
        # set title
        #############################
        self.web_input(By.ID, "postad-title", ad_cfg["title"])

        #############################
        # set category
        #############################
        self.__set_category(ad_file, ad_cfg)

        #############################
        # set shipping type/costs
        #############################
        if ad_cfg["shipping_type"] == "PICKUP":
            try:
                self.web_click(By.XPATH,
                               '//*[contains(@class, "ShippingPickupSelector")]//label[text()[contains(.,"Nur Abholung")]]/input[@type="radio"]')
            except NoSuchElementException as ex:
                LOG.debug(ex, exc_info=True)
        elif ad_cfg["shipping_costs"]:
            try:
                self.web_click(By.XPATH, '//*[contains(@class, "ShippingOption")]//input[@type="radio"]')
                self.web_click(By.XPATH,
                               '//*[contains(@class, "CarrierOptionsPopup")]//*[contains(@class, "IndividualPriceSection")]//input[@type="checkbox"]')
                self.web_input(By.XPATH, '//*[contains(@class, "IndividualShippingInput")]//input[@type="text"]',
                               str.replace(ad_cfg["shipping_costs"], ".", ","))
                self.web_click(By.XPATH,
                               '//*[contains(@class, "ReactModalPortal")]//button[.//*[text()[contains(.,"Weiter")]]]')
            except NoSuchElementException as ex:
                LOG.debug(ex, exc_info=True)

        #############################
        # set price
        #############################
        price_type = ad_cfg["price_type"]
        if price_type != "NOT_APPLICABLE":
            try:
                self.web_select(By.XPATH, "//select[@id='price-type-react']", price_type)
                if safe_get(ad_cfg, "price"):
                    self.web_click(By.ID, "post-ad-frontend-price")
                    self.web_input(By.ID, "post-ad-frontend-price", ad_cfg["price"])
            except NoSuchElementException as ex:
                # code for old HTML version can be removed at one point in future
                self.web_select(By.XPATH, "//select[@id='priceType']", price_type)
                if safe_get(ad_cfg, "price"):
                    self.web_input(By.ID, "pstad-price", ad_cfg["price"])

        #############################
        # set description
        #############################
        self.web_execute(
            "document.querySelector('#pstad-descrptn').value = `" + ad_cfg["description"].replace("`", "'") + "`")

        #############################
        # set contact zipcode
        #############################
        if ad_cfg["contact"]["zipcode"]:
            self.web_input(By.ID, "pstad-zip", ad_cfg["contact"]["zipcode"])

        #############################
        # set contact street
        #############################
        if ad_cfg["contact"]["street"]:
            try:
                if not self.webdriver.find_element(By.ID, "pstad-street").is_enabled():
                    self.webdriver.find_element(By.ID, "addressVisibility").click()
                    pause(2000)
            except NoSuchElementException:
                # ignore
                pass
            self.web_input(By.ID, "pstad-street", ad_cfg["contact"]["street"])

        #############################
        # set contact name
        #############################
        if ad_cfg["contact"]["name"]:
            self.web_input(By.ID, "postad-contactname", ad_cfg["contact"]["name"])

        #############################
        # set contact phone
        #############################
        if ad_cfg["contact"]["phone"]:
            if self.webdriver.find_element(By.ID, "postad-phonenumber").is_displayed():
                try:
                    if not self.webdriver.find_element(By.ID, "postad-phonenumber").is_enabled():
                        self.webdriver.find_element(By.ID, "phoneNumberVisibility").click()
                        pause(2000)
                except NoSuchElementException:
                    # ignore
                    pass
                self.web_input(By.ID, "postad-phonenumber", ad_cfg["contact"]["phone"])

        #############################
        # upload images
        #############################
        self.__upload_images(ad_cfg)

        #############################
        # submit
        #############################
        self.handle_captcha_if_present("postAd-recaptcha", "but DON'T click 'Anzeige aufgeben'.")
        try:
            self.web_click(By.ID, "pstad-submit")
        except NoSuchElementException:
            # https://github.com/Second-Hand-Friends/kleinanzeigen-bot/issues/40
            self.web_click(By.XPATH, "//fieldset[@id='postad-publish']//*[contains(text(),'Anzeige aufgeben')]")
            self.web_click(By.ID, "imprint-guidance-submit")

        self.web_await(EC.url_contains("p-anzeige-aufgeben-bestaetigung.html?adId="), 20)

        ad_cfg_orig["updated_on"] = datetime.utcnow().isoformat()
        if not ad_cfg["created_on"] and not ad_cfg["id"]:
            ad_cfg_orig["created_on"] = ad_cfg_orig["updated_on"]

        # extract the ad id from the URL's query parameter
        current_url_query_params = urllib.parse.parse_qs(urllib.parse.urlparse(self.webdriver.current_url).query)
        ad_id = int(current_url_query_params.get("adId", None)[0])
        ad_cfg_orig["id"] = ad_id

        LOG.info(" -> SUCCESS: ad published with ID %s", ad_id)

        utils.save_dict(ad_file, ad_cfg_orig)

    def __set_category(self, ad_file: str, ad_cfg: dict[str, Any]):
        # trigger and wait for automatic category detection
        self.web_click(By.ID, "pstad-price")
        try:
            self.web_find(By.XPATH, "//*[@id='postad-category-path'][text()]")
            is_category_auto_selected = True
        except NoSuchElementException:
            is_category_auto_selected = False

        if ad_cfg["category"]:
            utils.pause(2000)  # workaround for https://github.com/Second-Hand-Friends/kleinanzeigen-bot/issues/39
            self.web_click(By.ID, "pstad-lnk-chngeCtgry")
            self.web_find(By.ID, "postad-step1-sbmt")

            category_url = f"{self.root_url}/p-kategorie-aendern.html#?path={ad_cfg['category']}"
            self.web_open(category_url)
            self.web_click(By.XPATH, "//*[@id='postad-step1-sbmt']/button")
        else:
            ensure(is_category_auto_selected,
                   f"No category specified in [{ad_file}] and automatic category detection failed")

        if ad_cfg["special_attributes"]:
            LOG.debug('Found %i special attributes', len(ad_cfg["special_attributes"]))
            for special_attribute_key, special_attribute_value in ad_cfg["special_attributes"].items():
                LOG.debug("Setting special attribute [%s] to [%s]...", special_attribute_key, special_attribute_value)
                try:
                    self.web_select(By.XPATH, f"//select[@id='{special_attribute_key}']", special_attribute_value)
                except WebDriverException:
                    LOG.debug("Attribute field '%s' is not of kind dropdown, trying to input as plain text...",
                              special_attribute_key)
                    try:
                        self.web_input(By.ID, special_attribute_key, special_attribute_value)
                    except WebDriverException:
                        LOG.debug("Attribute field '%s' is not of kind plain text, trying to input as radio button...",
                                  special_attribute_key)
                        try:
                            self.web_click(By.XPATH,
                                           f"//*[@id='{special_attribute_key}']/option[@value='{special_attribute_value}']")
                        except WebDriverException as ex:
                            LOG.debug("Attribute field '%s' is not of kind radio button.", special_attribute_key)
                            raise NoSuchElementException(
                                f"Failed to set special attribute [{special_attribute_key}]") from ex
                LOG.debug("Successfully set attribute field [%s] to [%s]...", special_attribute_key,
                          special_attribute_value)

    def __upload_images(self, ad_cfg: dict[str, Any]):
        LOG.info(" -> found %s", pluralize("image", ad_cfg["images"]))
        image_upload = self.web_find(By.XPATH, "//input[@type='file']")

        def count_uploaded_images() -> int:
            return len(self.webdriver.find_elements(By.CLASS_NAME, "imagebox-new-thumbnail"))

        for image in ad_cfg["images"]:
            LOG.info(" -> uploading image [%s]", image)
            previous_uploaded_images_count = count_uploaded_images()
            image_upload.send_keys(image)
            start_at = time.time()
            while previous_uploaded_images_count == count_uploaded_images() and time.time() - start_at < 60:
                print(".", end="", flush=True)
                time.sleep(1)
            print(flush=True)

            ensure(previous_uploaded_images_count < count_uploaded_images(),
                   f"Couldn't upload image [{image}] within 60 seconds")
            LOG.debug("   => uploaded image within %i seconds", time.time() - start_at)
            pause(2000)

    def assert_free_ad_limit_not_reached(self) -> None:
        try:
            self.web_find(By.XPATH, '/html/body/div[1]/form/fieldset[6]/div[1]/header')
            raise AssertionError(
                f"Cannot publish more ads. The monthly limit of free ads of account {self.config['login']['username']} is reached.")
        except NoSuchElementException:
            pass

    @overrides
    def web_open(self, url: str, timeout: float = 15, reload_if_already_open: bool = False) -> None:
        start_at = time.time()
        super().web_open(url, timeout, reload_if_already_open)
        pause(2000)

        # reload the page until no fullscreen ad is displayed anymore
        while True:
            try:
                self.web_find(By.XPATH, "/html/body/header[@id='site-header']", 2)
                return
            except NoSuchElementException as ex:
                elapsed = time.time() - start_at
                if elapsed < timeout:
                    super().web_open(url, timeout - elapsed, True)
                else:
                    raise TimeoutException("Loading page failed, it still shows fullscreen ad.") from ex

    def navigate_to_ad_page(self) -> bool:
        """
        Navigates to an ad page specified with an ad ID.

        :return: whether the navigation to the ad page was successful
        """
        # enter the ad ID into the search bar
        self.web_input(By.XPATH, '//*[@id="site-search-query"]', str(self.ad_id))
        # navigate to ad page and wait
        self.web_click(By.XPATH, '//*[@id="site-search-submit"]')
        pause(1000, 2000)

        # handle the case that invalid ad ID given
        if self.webdriver.current_url.endswith('k0'):
            LOG.error('There is no ad under the given ID.')
            return False
        else:
            try:  # close (warning) popup
                self.webdriver.find_element(By.CSS_SELECTOR, '#vap-ovrly-secure')
                LOG.warning('A popup appeared.')
                close_button = self.webdriver.find_element(By.CLASS_NAME, 'mfp-close')
                close_button.click()
                time.sleep(1)
            except NoSuchElementException:
                print('(no popup given)')
            return True

    def extract_ad_page_info(self, directory: str) -> Dict:
        """
        Extracts all necessary information from an ad´s page.

        :param directory: the path of the ad´s previously created directory
        :return: a dictionary with the keys as given in an ad YAML, and their respective values
        """
        info = dict()

        # extract basic info
        info['active'] = True
        if 's-anzeige' in self.webdriver.current_url:
            o_type = 'OFFER'
        else:
            o_type = 'WANTED'
        info['type'] = o_type
        title: str = self.webdriver.find_element(By.CSS_SELECTOR, '#viewad-title').text
        LOG.info('Extracting information from ad with title \"%s\"', title)
        info['title'] = title
        descr: str = self.webdriver.find_element(By.XPATH, '//*[@id="viewad-description-text"]').text
        info['description'] = descr

        # extract category
        category_line = self.webdriver.find_element(By.XPATH, '//*[@id="vap-brdcrmb"]')
        category_first_part = category_line.find_element(By.XPATH, './/a[2]')
        category_second_part = category_line.find_element(By.XPATH, './/a[3]')
        cat_num_first = category_first_part.get_attribute('href').split('/')[-1][1:]
        cat_num_second = category_second_part.get_attribute('href').split('/')[-1][1:]
        category = cat_num_first + '/' + cat_num_second
        info['category'] = category

        # get special attributes
        try:
            details_box = self.webdriver.find_element(By.CSS_SELECTOR, '#viewad-details')
            if details_box:  # detail box exists depending on category
                details_list = details_box.find_element(By.XPATH, './/ul')
                list_items = details_list.find_elements(By.TAG_NAME, 'li')
                details = dict()
                for list_item in list_items:
                    detail_key = list_item.text.split('\n')[0]
                    detail_value = list_item.find_element(By.TAG_NAME, 'span').text
                    details[detail_key] = detail_value
                info['special_attributes'] = details
        except NoSuchElementException:
            info['special_attributes'] = dict()

        # process pricing
        try:
            price_str: str = self.webdriver.find_element(By.CLASS_NAME, 'boxedarticle--price').text
            price_type: str
            price: float | None = -1
            match price_str.split()[-1]:
                case '€':
                    price_type = 'FIXED'
                    price = float(utils.parse_decimal(price_str.split()[0].replace('.', '')))
                case 'VB':  # can be either 'X € VB', or just 'VB'
                    price_type = 'NEGOTIABLE'
                    try:
                        price = float(utils.parse_decimal(price_str.split()[0].replace('.', '')))
                    except DecimalException:
                        price = None
                case 'verschenken':
                    price_type = 'GIVE_AWAY'
                    price = None
                case _:
                    price_type = 'NOT_APPLICABLE'
            info['price'] = price
            info['price_type'] = price_type
        except NoSuchElementException:  # no 'commercial' ad, has no pricing box etc.
            info['price'] = None
            info['price_type'] = 'NOT_APPLICABLE'

        # process shipping
        ship_type, ship_costs = 'NOT_APPLICABLE', None
        try:
            shipping_text = self.webdriver.find_element(By.CSS_SELECTOR, '.boxedarticle--details--shipping')\
                .text.strip()
            # e.g. '+ Versand ab 5,49 €' OR 'Nur Abholung'
            if shipping_text == 'Nur Abholung':
                ship_type = 'PICKUP'
            elif shipping_text == 'Versand möglich':
                ship_type = 'SHIPPING'
            elif '€' in shipping_text:
                shipping_price_parts = shipping_text.split(' ')
                shipping_price = float(utils.parse_decimal(shipping_price_parts[-2]))
                ship_type = 'SHIPPING'
                ship_costs = shipping_price
        except NoSuchElementException:  # no pricing box -> no shipping given
            ship_type = 'NOT_APPLICABLE'
        info['shipping_type'] = ship_type
        info['shipping_costs'] = ship_costs

        # fetch images
        n_images: int = -1
        img_paths = []
        try:
            image_box = self.webdriver.find_element(By.CSS_SELECTOR, '.galleryimage-large')

            # if gallery image box exists, proceed with image fetching
            n_images = 1

            # determine number of images (1 ... N)
            next_button = None
            try:  # check if multiple images given
                image_counter = image_box.find_element(By.CSS_SELECTOR, '.galleryimage--info')
                n_images = int(image_counter.text[2:])
                LOG.info(f'Found {n_images} images.')
                next_button = self.webdriver.find_element(By.CSS_SELECTOR, '.galleryimage--navigation--next')
            except NoSuchElementException:
                LOG.info('Only one image found.')

            # download all images from box
            img_element = image_box.find_element(By.XPATH, './/div[1]/img')
            img_fn_prefix = 'ad_' + str(self.ad_id) + '__img'

            img_nr = 1
            dl_counter = 0
            while img_nr <= n_images:  # scrolling + downloading
                current_img_url = img_element.get_attribute('src')  # URL of the image
                file_ending = current_img_url.split('.')[-1].lower()
                img_path = directory + '/' + img_fn_prefix + str(img_nr) + '.' + file_ending
                if current_img_url.startswith('https'):  # verify https (for Bandit linter)
                    request.urlretrieve(current_img_url, img_path)  # nosec B310
                dl_counter += 1
                img_paths.append(img_path.split('/')[-1])

                # scroll to next image (if exists)
                if img_nr < n_images:
                    try:
                        # click next button, wait, and reestablish reference
                        next_button.click()
                        self.web_await(lambda _: EC.staleness_of(img_element))
                        new_div = self.webdriver.find_element(By.CSS_SELECTOR, f'div.galleryimage-element:nth-child'
                                                                               f'({img_nr + 1})')
                        img_element = new_div.find_element(By.XPATH, './/img')
                    except NoSuchElementException:
                        LOG.error('NEXT button in image gallery somehow missing, abort image fetching.')
                        break
                img_nr += 1
            LOG.info(f'Downloaded {dl_counter} image(s).')

        except NoSuchElementException:  # some ads do not require images
            LOG.warning('No image area found. Continue without downloading images.')
        info['images'] = img_paths

        # process address
        contact = dict()
        address_element = self.webdriver.find_element(By.CSS_SELECTOR, '#viewad-locality')
        address_text = address_element.text.strip()
        # format: e.g. (Beispiel Allee 42,) 12345 Bundesland - Stadt
        try:
            street_element = self.webdriver.find_element(By.XPATH, '//*[@id="street-address"]')
            street = street_element.text[:-2]  # trailing comma and whitespace
            contact['street'] = street
        except NoSuchElementException:
            LOG.info('No street given in the contact.')
        # construct remaining address
        address_halves = address_text.split(' - ')
        address_left_parts = address_halves[0].split(' ')  # zip code and region/city
        left_part_remaining = ' '.join(address_left_parts[1:])  # either a region or a city
        contact['zipcode'] = address_left_parts[0]
        contact['name'] = address_halves[1]
        if 'street' not in contact:
            contact['street'] = None
        try:  # phone number is unusual for non-professional sellers today
            phone_element = self.webdriver.find_element(By.CSS_SELECTOR, '#viewad-contact-phone')
            phone_number = phone_element.find_element(By.TAG_NAME, 'a').text
            contact['phone'] = ''.join(phone_number.replace('-', ' ').split(' ')).replace('+49(0)', '0')
        except NoSuchElementException:
            contact['phone'] = None  # phone seems to be a deprecated feature
        # also see 'https://themen.ebay-kleinanzeigen.de/hilfe/deine-anzeigen/Telefon/
        info['contact'] = contact

        # process meta info
        info['republication_interval'] = 7  # a default value for downloaded ads
        info['id'] = self.ad_id
        try:  # try different locations known for creation date element
            creation_date = self.webdriver.find_element(By.XPATH, '/html/body/div[1]/div[2]/div/section[2]/section/'
                                                                  'section/article/div[3]/div[2]/div[2]/'
                                                                  'div[1]/span').text
        except NoSuchElementException:
            creation_date = self.webdriver.find_element(By.CSS_SELECTOR,
                                                        '#viewad-extra-info > div:nth-child(1)'
                                                        ' > span:nth-child(2)').text

        # convert creation date to ISO format
        created_parts = creation_date.split('.')
        creation_date = created_parts[2] + '-' + created_parts[1] + '-' + created_parts[0] + ' 00:00:00'
        info['created_on'] = datetime.fromisoformat(creation_date)
        info['updated_on'] = None  # will be set later on

        return info

    def download_ad_page(self):
        """
        Downloads an ad to a specific location, specified by config and ad_id.
        """

        # create sub-directory for ad to download
        relative_directory = str(self.config["ad_files"][0]).split('**')[0]
        new_base_dir = os.path.join(relative_directory, f'ad_{self.ad_id}')
        if os.path.exists(new_base_dir):
            LOG.info('Deleting current folder of ad...')
            shutil.rmtree(new_base_dir)
        os.mkdir(new_base_dir)
        LOG.info('New directory for ad created at ' + new_base_dir + '.')

        # call extraction function
        info = self.extract_ad_page_info(new_base_dir)
        ad_file_path = new_base_dir + '/' + f'ad_{self.ad_id}.yaml'
        utils.save_dict(ad_file_path, info)


#############################
# main entry point
#############################
def main(args: list[str]) -> None:
    if "version" not in args:
        print(textwrap.dedent(r"""
         _    _      _                           _                       _           _
        | | _| | ___(_)_ __   __ _ _ __  _______(_) __ _  ___ _ __      | |__   ___ | |_
        | |/ / |/ _ \ | '_ \ / _` | '_ \|_  / _ \ |/ _` |/ _ \ '_ \ ____| '_ \ / _ \| __|
        |   <| |  __/ | | | | (_| | | | |/ /  __/ | (_| |  __/ | | |____| |_) | (_) | |_
        |_|\_\_|\___|_|_| |_|\__,_|_| |_/___\___|_|\__, |\___|_| |_|    |_.__/ \___/ \__|
                                                   |___/
                                                     https://github.com/kleinanzeigen-bot
        """), flush=True)

    utils.configure_console_logging()

    signal.signal(signal.SIGINT, utils.on_sigint)  # capture CTRL+C
    sys.excepthook = utils.on_exception
    atexit.register(utils.on_exit)

    KleinanzeigenBot().run(args)


if __name__ == "__main__":
    utils.configure_console_logging()
    LOG.error("Direct execution not supported. Use 'pdm run app'")
    sys.exit(1)
