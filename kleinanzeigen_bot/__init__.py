"""
Copyright (C) 2022 Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import atexit, copy, getopt, glob, importlib.metadata, json, logging, os, signal, sys, textwrap, time, urllib
from collections.abc import Iterable
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Any, Final

from overrides import overrides
from ruamel.yaml import YAML
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

from . import utils, resources
from .utils import abspath, apply_defaults, ensure, is_frozen, pause, pluralize, safe_get
from .selenium_mixin import SeleniumMixin

LOG_ROOT:Final[logging.Logger] = logging.getLogger()
LOG:Final[logging.Logger] = logging.getLogger("kleinanzeigen_bot")
LOG.setLevel(logging.INFO)


class KleinanzeigenBot(SeleniumMixin):

    def __init__(self) -> None:
        super().__init__()

        self.root_url = "https://www.ebay-kleinanzeigen.de"

        self.config:dict[str, Any] = {}
        self.config_file_path = abspath("config.yaml")

        self.categories:dict[str, str] = {}

        self.file_log:logging.FileHandler | None = None
        if is_frozen():
            log_file_basename = os.path.splitext(os.path.basename(sys.executable))[0]
        else:
            log_file_basename = self.__module__
        self.log_file_path:str | None = abspath(f"{log_file_basename}.log")

        self.command = "help"
        self.ads_selector = "due"
        self.delete_old_ads = True
        self.delete_ads_by_title = False

    def __del__(self) -> None:
        if self.file_log:
            LOG_ROOT.removeHandler(self.file_log)

    def get_version(self) -> str:
        return importlib.metadata.version(__package__)

    def run(self, args:list[str]) -> None:
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
              publish - (re-)publishes ads
              verify  - verifies the configuration files
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
              --config=<PATH>   - path to the config YAML or JSON file (DEFAULT: ./config.yaml)
              --logfile=<PATH>  - path to the logfile (DEFAULT: ./kleinanzeigen-bot.log)
              -v, --verbose     - enables verbose output - only useful when troubleshooting issues
        """))

    def parse_args(self, args:list[str]) -> None:
        try:
            options, arguments = getopt.gnu_getopt(args[1:], "hv", [
                "ads=",
                "config=",
                "force",
                "help",
                "keep-old",
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
        self.file_log = RotatingFileHandler(filename = self.log_file_path, maxBytes = 10 * 1024 * 1024, backupCount = 10, encoding = "utf-8")
        self.file_log.setLevel(logging.DEBUG)
        self.file_log.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        LOG_ROOT.addHandler(self.file_log)

        LOG.info("App version: %s", self.get_version())

    def load_ads(self, *, ignore_inactive:bool = True) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
        LOG.info("Searching for ad config files...")

        ad_files = set()
        data_root_dir = os.path.dirname(self.config_file_path)
        for file_pattern in self.config["ad_files"]:
            for ad_file in glob.glob(file_pattern, root_dir = data_root_dir, recursive = True):
                ad_files.add(abspath(ad_file, relative_to = data_root_dir))
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
            apply_defaults(ad_cfg, self.config["ad_defaults"], ignore = lambda k, _: k == "description", override = lambda _, v: v == "")
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
                        LOG.info(" -> SKIPPED: ad [%s] was last published %d days ago. republication is only required every %s days",
                            ad_file,
                            ad_age.days,
                            ad_cfg["republication_interval"]
                        )
                        continue

            ad_cfg["description"] = descr_prefix + (ad_cfg["description"] or "") + descr_suffix

            # pylint: disable=cell-var-from-loop
            def assert_one_of(path:str, allowed:Iterable[str]) -> None:
                ensure(safe_get(ad_cfg, *path.split(".")) in allowed, f"-> property [{path}] must be one of: {allowed} @ [{ad_file}]")

            def assert_min_len(path:str, minlen:int) -> None:
                ensure(len(safe_get(ad_cfg, *path.split("."))) >= minlen, f"-> property [{path}] must be at least {minlen} characters long @ [{ad_file}]")

            def assert_has_value(path:str) -> None:
                ensure(safe_get(ad_cfg, *path.split(".")), f"-> property [{path}] not specified @ [{ad_file}]")
            # pylint: enable=cell-var-from-loop

            assert_one_of("type", {"OFFER", "WANTED"})
            assert_min_len("title", 10)
            assert_has_value("description")
            assert_one_of("price_type", {"FIXED", "NEGOTIABLE", "GIVE_AWAY", "NOT_APPLICABLE"})
            if ad_cfg["price_type"] == "GIVE_AWAY":
                ensure(not safe_get(ad_cfg, "price"), f"-> [price] must not be specified for GIVE_AWAY ad @ [{ad_file}]")
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
                ad_cfg["shipping_costs"] = str(utils.parse_decimal(ad_cfg["shipping_costs"]))

            if ad_cfg["images"]:
                images = []
                for image_pattern in ad_cfg["images"]:
                    pattern_images = set()
                    ad_dir = os.path.dirname(ad_file)
                    for image_file in glob.glob(image_pattern, root_dir = ad_dir, recursive = True):
                        _, image_file_ext = os.path.splitext(image_file)
                        ensure(image_file_ext.lower() in {".gif", ".jpg", ".jpeg", ".png"}, f"Unsupported image file type [{image_file}]")
                        if os.path.isabs(image_file):
                            pattern_images.add(image_file)
                        else:
                            pattern_images.add(abspath(image_file, relative_to = ad_file))
                    images.extend(sorted(pattern_images))
                ensure(images or not ad_cfg["images"], f"No images found for given file patterns {ad_cfg['images']} at {ad_dir}")
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
        self.browser_config.extensions = [abspath(item, relative_to = self.config_file_path) for item in self.config["browser"]["extensions"]]
        self.browser_config.use_private_window = self.config["browser"]["use_private_window"]
        if self.config["browser"]["user_data_dir"]:
            self.browser_config.user_data_dir = abspath(self.config["browser"]["user_data_dir"], relative_to = self.config_file_path)
        self.browser_config.profile_name = self.config["browser"]["profile_name"]

    def login(self) -> None:
        LOG.info("Logging in as [%s]...", self.config["login"]["username"])
        self.web_open(f"{self.root_url}/m-einloggen.html?targetUrl=/")

        # accept privacy banner
        self.web_click(By.ID, "gdpr-banner-accept")

        self.web_input(By.ID, "login-email", self.config["login"]["username"])
        self.web_input(By.ID, "login-password", self.config["login"]["password"])

        self.handle_captcha_if_present("login-recaptcha", "but DON'T click 'Einloggen'.")

        self.web_click(By.ID, "login-submit")

        pause(800, 3000)

    def handle_captcha_if_present(self, captcha_element_id:str, msg:str) -> None:
        try:
            self.web_click(By.XPATH, f"//*[@id='{captcha_element_id}']")
        except NoSuchElementException:
            return

        LOG.warning("############################################")
        LOG.warning("# Captcha present! Please solve and close the captcha, %s", msg)
        LOG.warning("############################################")
        self.webdriver.switch_to.frame(self.web_find(By.CSS_SELECTOR, f"#{captcha_element_id} iframe"))
        self.web_await(lambda _: self.webdriver.find_element(By.ID, "recaptcha-anchor").get_attribute("aria-checked") == "true", timeout = 5 * 60)
        self.webdriver.switch_to.default_content()

    def delete_ad(self, ad_cfg: dict[str, Any]) -> bool:
        LOG.info("Deleting ad '%s' if already present...", ad_cfg["title"])

        self.web_open(f"{self.root_url}/m-meine-anzeigen.html")
        csrf_token_elem = self.web_find(By.XPATH, "//meta[@name='_csrf']")
        csrf_token = csrf_token_elem.get_attribute("content")

        if self.delete_ads_by_title:
            published_ads = json.loads(self.web_request(f"{self.root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT")["content"])["ads"]

            for published_ad in published_ads:
                published_ad_id = int(published_ad.get("id", -1))
                published_ad_title = published_ad.get("title", "")
                if ad_cfg["id"] == published_ad_id or ad_cfg["title"] == published_ad_title:
                    LOG.info(" -> deleting %s '%s'...", published_ad_id, published_ad_title)
                    self.web_request(
                        url = f"{self.root_url}/m-anzeigen-loeschen.json?ids={published_ad_id}",
                        method = "POST",
                        headers = {"x-csrf-token": csrf_token}
                    )
        elif ad_cfg["id"]:
            self.web_request(
                url = f"{self.root_url}/m-anzeigen-loeschen.json?ids={ad_cfg['id']}",
                method = "POST",
                headers = {"x-csrf-token": csrf_token},
                valid_response_codes = [200, 404]
            )

        pause(1500, 3000)
        ad_cfg["id"] = None
        return True

    def publish_ads(self, ad_cfgs:list[tuple[str, dict[str, Any], dict[str, Any]]]) -> None:
        count = 0

        for (ad_file, ad_cfg, ad_cfg_orig) in ad_cfgs:
            count += 1
            LOG.info("Processing %s/%s: '%s' from [%s]...", count, len(ad_cfgs), ad_cfg["title"], ad_file)
            self.publish_ad(ad_file, ad_cfg, ad_cfg_orig)
            pause(3000, 5000)

        LOG.info("############################################")
        LOG.info("DONE: (Re-)published %s", pluralize("ad", count))
        LOG.info("############################################")

    def publish_ad(self, ad_file:str, ad_cfg: dict[str, Any], ad_cfg_orig: dict[str, Any]) -> None:
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
                self.web_click(By.XPATH, '//*[contains(@class, "ShippingPickupSelector")]//label[text()[contains(.,"Nur Abholung")]]/input[@type="radio"]')
            except NoSuchElementException as ex:
                LOG.debug(ex, exc_info = True)
        elif ad_cfg["shipping_costs"]:
            self.web_click(By.XPATH, '//*[contains(@class, "ShippingOption")]//input[@type="radio"]')
            self.web_click(By.XPATH, '//*[contains(@class, "CarrierOptionsPopup")]//*[contains(@class, "IndividualPriceSection")]//input[@type="checkbox"]')
            self.web_input(By.XPATH, '//*[contains(@class, "IndividualShippingInput")]//input[@type="text"]', str.replace(ad_cfg["shipping_costs"], ".", ","))
            self.web_click(By.XPATH, '//*[contains(@class, "ReactModalPortal")]//button[.//*[text()[contains(.,"Weiter")]]]')

        #############################
        # set price
        #############################
        price_type = ad_cfg["price_type"]
        if price_type != "NOT_APPLICABLE":
            self.web_select(By.XPATH, "//select[@id='priceType']", price_type)
            if safe_get(ad_cfg, "price"):
                self.web_input(By.ID, "pstad-price", ad_cfg["price"])

        #############################
        # set description
        #############################
        self.web_execute("document.querySelector('#pstad-descrptn').value = `" + ad_cfg["description"].replace("`", "'") + "`")

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

    def __set_category(self, ad_file:str, ad_cfg: dict[str, Any]):
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
            ensure(is_category_auto_selected, f"No category specified in [{ad_file}] and automatic category detection failed")

        if ad_cfg["special_attributes"]:
            LOG.debug('Found %i special attributes', len(ad_cfg["special_attributes"]))
            for special_attribute_key, special_attribute_value in ad_cfg["special_attributes"].items():
                LOG.debug("Setting special attribute [%s] to [%s]...", special_attribute_key, special_attribute_value)
                try:
                    self.web_select(By.XPATH, f"//select[@id='{special_attribute_key}']", special_attribute_value)
                except WebDriverException:
                    LOG.debug("Attribute field '%s' is not of kind dropdown, trying to input as plain text...", special_attribute_key)
                    try:
                        self.web_input(By.ID, special_attribute_key, special_attribute_value)
                    except WebDriverException:
                        LOG.debug("Attribute field '%s' is not of kind plain text, trying to input as radio button...", special_attribute_key)
                        try:
                            self.web_click(By.XPATH, f"//*[@id='{special_attribute_key}']/option[@value='{special_attribute_value}']")
                        except WebDriverException as ex:
                            LOG.debug("Attribute field '%s' is not of kind radio button.", special_attribute_key)
                            raise NoSuchElementException(f"Failed to set special attribute [{special_attribute_key}]") from ex
                LOG.debug("Successfully set attribute field [%s] to [%s]...", special_attribute_key, special_attribute_value)

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
                print(".", end = "", flush = True)
                time.sleep(1)
            print(flush = True)

            ensure(previous_uploaded_images_count < count_uploaded_images(), f"Couldn't upload image [{image}] within 60 seconds")
            LOG.debug("   => uploaded image within %i seconds", time.time() - start_at)
            pause(2000)

    def assert_free_ad_limit_not_reached(self) -> None:
        try:
            self.web_find(By.XPATH, '/html/body/div[1]/form/fieldset[6]/div[1]/header')
            raise AssertionError(f"Cannot publish more ads. The monthly limit of free ads of account {self.config['login']['username']} is reached.")
        except NoSuchElementException:
            pass

    @overrides
    def web_open(self, url:str, timeout:float = 15, reload_if_already_open:bool = False) -> None:
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


#############################
# main entry point
#############################
def main(args:list[str]) -> None:
    if "version" not in args:
        print(textwrap.dedent(r"""
         _    _      _                           _                       _           _
        | | _| | ___(_)_ __   __ _ _ __  _______(_) __ _  ___ _ __      | |__   ___ | |_
        | |/ / |/ _ \ | '_ \ / _` | '_ \|_  / _ \ |/ _` |/ _ \ '_ \ ____| '_ \ / _ \| __|
        |   <| |  __/ | | | | (_| | | | |/ /  __/ | (_| |  __/ | | |____| |_) | (_) | |_
        |_|\_\_|\___|_|_| |_|\__,_|_| |_/___\___|_|\__, |\___|_| |_|    |_.__/ \___/ \__|
                                                   |___/
                                                     https://github.com/kleinanzeigen-bot
        """), flush = True)

    utils.configure_console_logging()

    signal.signal(signal.SIGINT, utils.on_sigint)  # capture CTRL+C
    sys.excepthook = utils.on_exception
    atexit.register(utils.on_exit)

    KleinanzeigenBot().run(args)


if __name__ == "__main__":
    utils.configure_console_logging()
    LOG.error("Direct execution not supported. Use 'pdm run app'")
    sys.exit(1)
