"""
Copyright (C) 2022 Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import atexit, copy, getopt, glob, json, logging, os, signal, sys, textwrap, time, urllib
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Final, Iterable

from ruamel.yaml import YAML
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

from . import utils, resources
from .utils import apply_defaults, ensure, is_frozen, pause, pluralize, safe_get
from .selenium_mixin import SeleniumMixin

LOG_ROOT:Final[logging.Logger] = logging.getLogger()
LOG:Final[logging.Logger] = logging.getLogger("kleinanzeigen_bot")
LOG.setLevel(logging.INFO)

try:
    from .version import version as VERSION
except ModuleNotFoundError:
    VERSION = "unknown"


class KleinanzeigenBot(SeleniumMixin):

    def __init__(self):
        super().__init__()

        self.root_url = "https://www.ebay-kleinanzeigen.de"

        self.config:Dict[str, Any] = {}
        self.config_file_path = os.path.join(os.getcwd(), "config.yaml")

        self.categories:Dict[str, str] = {}

        self.file_log:logging.FileHandler = None
        if is_frozen():
            log_file_basename = os.path.splitext(os.path.basename(sys.executable))[0]
        else:
            log_file_basename = self.__module__
        self.log_file_path = os.path.join(os.getcwd(), f"{log_file_basename}.log")

        self.command = "help"

    def __del__(self):
        if self.file_log:
            LOG_ROOT.removeHandler(self.file_log)
        super().__del__()

    def run(self, args:Iterable[str]) -> None:
        self.parse_args(args)
        match self.command:
            case "help":
                self.show_help()
            case "version":
                print(VERSION)
            case "verify":
                self.configure_file_logging()
                self.load_config()
                self.load_ads()
                LOG.info("############################################")
                LOG.info("No configuration errors found.")
                LOG.info("############################################")
            case "publish":
                self.configure_file_logging()
                self.load_config()
                ads = self.load_ads()
                if len(ads) == 0:
                    LOG.info("############################################")
                    LOG.info("No ads to (re-)publish found.")
                    LOG.info("############################################")
                else:
                    self.create_webdriver_session()
                    self.login()
                    self.publish_ads(ads)
            case _:
                LOG.error("Unknown command: %s", self.command)
                sys.exit(2)

    def show_help(self) -> None:
        if is_frozen():
            exe = sys.argv[0]
        else:
            exe = f"python -m {os.path.relpath(os.path.join(__file__, '..'))}"

        print(textwrap.dedent(f"""\
            Usage: {exe} COMMAND [-v|--verbose] [--config=<PATH>] [--logfile=<PATH>]

            Commands:
              publish - (re-)publishes ads
              verify  - verifies the configuration files
              --
              help    - displays this help (default command)
              version - displays the application version
        """))

    def parse_args(self, args:Iterable[str]) -> None:
        try:
            options, arguments = getopt.gnu_getopt(args[1:], "hv", ["help", "verbose", "logfile=", "config="])  # pylint: disable=unused-variable
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
                    self.config_file_path = os.path.abspath(value)
                case "--logfile":
                    if value:
                        self.log_file_path = os.path.abspath(value)
                    else:
                        self.log_file_path = None
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
        self.file_log.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
        LOG_ROOT.addHandler(self.file_log)

    def load_ads(self, exclude_inactive = True, exclude_undue = True) -> Iterable[Dict[str, Any]]:
        LOG.info("Searching for ad files...")

        ad_files = set()
        for file_pattern in self.config["ad_files"]:
            for ad_file in glob.glob(file_pattern, root_dir = os.getcwd(), recursive = True):
                ad_files.add(os.path.abspath(ad_file))
        LOG.info(" -> found %s", pluralize("ad file", ad_files))
        if not ad_files:
            return []

        descr_prefix = self.config["ad_defaults"]["description"]["prefix"] or ""
        descr_suffix = self.config["ad_defaults"]["description"]["suffix"] or ""

        ad_fields = utils.load_dict_from_module(resources, "ad_fields.yaml")
        ads = []
        for ad_file in sorted(ad_files):

            ad_cfg_orig = utils.load_dict(ad_file, "ad file")
            ad_cfg = copy.deepcopy(ad_cfg_orig)
            apply_defaults(ad_cfg, self.config["ad_defaults"], ignore = lambda k, _: k == "description", override = lambda _, v: v == "")
            apply_defaults(ad_cfg, ad_fields)

            if exclude_inactive and not ad_cfg["active"]:
                LOG.info(" -> excluding inactive ad [%s]", ad_file)
                continue

            if exclude_undue:
                if ad_cfg["updated_on"]:
                    last_updated_on = datetime.fromisoformat(ad_cfg["updated_on"])
                elif ad_cfg["created_on"]:
                    last_updated_on = datetime.fromisoformat(ad_cfg["created_on"])
                else:
                    last_updated_on = None

                if last_updated_on:
                    ad_age = datetime.utcnow() - last_updated_on
                    if ad_age.days <= ad_cfg["republication_interval"]:
                        LOG.info(" -> skipping. last published %d days ago. republication is only required every %s days",
                            ad_age.days,
                            ad_cfg["republication_interval"]
                        )
                        continue

            ad_cfg["description"] = descr_prefix + (ad_cfg["description"] or "") + descr_suffix

            # pylint: disable=cell-var-from-loop
            def assert_one_of(path:str, allowed:Iterable):
                ensure(safe_get(ad_cfg, *path.split(".")) in allowed, f'-> property [{path}] must be one of: {allowed} @ [{ad_file}]')

            def assert_min_len(path:str, minlen:int):
                ensure(len(safe_get(ad_cfg, *path.split("."))) >= minlen, f'-> property [{path}] must be at least {minlen} characters long @ [{ad_file}]')

            def assert_has_value(path:str):
                ensure(safe_get(ad_cfg, *path.split(".")), f'-> property [{path}] not specified @ [{ad_file}]')
            # pylint: enable=cell-var-from-loop

            assert_one_of("type", ("OFFER", "WANTED"))
            assert_min_len("title", 10)
            assert_has_value("description")
            assert_has_value("price")
            assert_one_of("price_type", ("FIXED", "NEGOTIABLE", "GIVE_AWAY"))
            assert_one_of("shipping_type", ("PICKUP", "SHIPPING", "NOT_APPLICABLE"))
            assert_has_value("contact.name")
            assert_has_value("republication_interval")

            if ad_cfg["id"]:
                ad_cfg["id"] = int(ad_cfg["id"])

            if ad_cfg["category"]:
                ad_cfg["category"] = self.categories.get(ad_cfg["category"], ad_cfg["category"])

            if ad_cfg["images"]:
                images = set()
                for image_pattern in ad_cfg["images"]:
                    for image_file in glob.glob(image_pattern, root_dir = os.path.dirname(ad_file), recursive = True):
                        _, image_file_ext = os.path.splitext(image_file)
                        ensure(image_file_ext.lower() in (".gif", ".jpg", ".jpeg", ".png"), f'Unsupported image file type [{image_file}]')
                        if os.path.isabs(image_file):
                            images.add(image_file)
                        else:
                            images.add(os.path.join(os.path.dirname(ad_file), image_file))
                ensure(images or not ad_cfg["images"], f'No images found for given file patterns {ad_cfg["images"]} at {os.getcwd()}')
                ad_cfg["images"] = sorted(images)

            ads.append((
                ad_file,
                ad_cfg,
                ad_cfg_orig
            ))

        LOG.info(" -> loaded %s", pluralize("ad", ads))
        return ads

    def load_config(self) -> None:
        config_defaults = utils.load_dict_from_module(resources, "config_defaults.yaml")
        config = utils.load_dict(self.config_file_path, "config", must_exist = False)

        if config is None:
            LOG.warning("Config file %s does not exist. Creating it with default values...", self.config_file_path)
            utils.save_dict(self.config_file_path, config_defaults)
            config = {}

        self.config = apply_defaults(config, config_defaults)

        self.categories = utils.load_dict_from_module(resources, "categories.yaml", "categories")
        if self.config["categories"]:
            self.categories.update(self.config["categories"])
        LOG.info(" -> found %s", pluralize("category", self.categories))

        ensure(self.config["login"]["username"], f'[login.username] not specified @ [{self.config_file_path}]')
        ensure(self.config["login"]["password"], f'[login.password] not specified @ [{self.config_file_path}]')

        self.browser_arguments = self.config["browser"]["arguments"]
        self.browser_binary_location = self.config["browser"]["binary_location"]

    def login(self) -> None:
        LOG.info("Logging in as [%s]...", self.config["login"]["username"])
        self.web_open(f'{self.root_url}/m-einloggen.html')

        # accept privacy banner
        self.web_click(By.ID, 'gdpr-banner-accept')

        self.web_input(By.ID, 'login-email', self.config["login"]["username"])
        self.web_input(By.ID, 'login-password', self.config["login"]["password"])

        self.handle_captcha_if_present("login-recaptcha", "but DON'T click 'Einloggen'.")

        self.web_click(By.ID, 'login-submit')

        pause(800, 3000)

    def handle_captcha_if_present(self, captcha_element_id:str, msg:str) -> None:
        try:
            self.web_click(By.XPATH, f'//*[@id="{captcha_element_id}"]')
        except NoSuchElementException:
            return

        LOG.warning("############################################")
        LOG.warning("# Captcha present! Please solve and close the captcha, %s", msg)
        LOG.warning("############################################")
        self.webdriver.switch_to.frame(self.web_find(By.CSS_SELECTOR, f'#{captcha_element_id} iframe'))
        self.web_await(lambda _: self.webdriver.find_element(By.ID, 'recaptcha-anchor').get_attribute('aria-checked') == "true", timeout = 5 * 60)
        self.webdriver.switch_to.default_content()

    def delete_ad(self, ad_cfg: Dict[str, Any]) -> bool:
        LOG.info("Deleting ad '%s' if already present...", ad_cfg["title"])

        self.web_open(f"{self.root_url}/m-meine-anzeigen.html")
        csrf_token_elem = self.web_find(By.XPATH, '//meta[@name="_csrf"]')
        csrf_token = csrf_token_elem.get_attribute("content")

        published_ads = json.loads(self.web_request(f"{self.root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT")["content"])["ads"]

        for published_ad in published_ads:
            published_ad_id = int(published_ad.get("id", -1))
            published_ad_title = published_ad.get("title", "")
            if ad_cfg["id"] == published_ad_id or ad_cfg["title"] == published_ad_title:
                LOG.info(" -> deleting %s '%s'...", published_ad_id, published_ad_title)
                self.web_request(
                    url = f"{self.root_url}/m-anzeigen-loeschen.json?ids={published_ad_id}",
                    method = "POST",
                    headers = {'x-csrf-token': csrf_token}
                )
                pause(1500, 3000)

        ad_cfg["id"] = None
        return True

    def publish_ads(self, ad_cfgs:Iterable[Dict[str, Any]]) -> None:
        count = 0

        for (ad_file, ad_cfg, ad_cfg_orig) in ad_cfgs:
            count += 1
            LOG.info("Processing %s/%s: '%s' from [%s]...", count, len(ad_cfgs), ad_cfg["title"], ad_file)
            self.publish_ad(ad_file, ad_cfg, ad_cfg_orig)
            pause(3000, 5000)

        LOG.info("############################################")
        LOG.info("(Re-)published %s", pluralize("ad", count))
        LOG.info("############################################")

    def publish_ad(self, ad_file, ad_cfg: Dict[str, Any], ad_cfg_orig: Dict[str, Any]) -> None:
        self.delete_ad(ad_cfg)

        LOG.info("Publishing ad '%s'...", ad_cfg["title"])

        if LOG.isEnabledFor(logging.DEBUG):
            LOG.debug(" -> effective ad meta:")
            YAML().dump(ad_cfg, sys.stdout)

        self.web_open(f'{self.root_url}/p-anzeige-aufgeben-schritt2.html')

        if ad_cfg["type"] == "WANTED":
            self.web_click(By.ID, 'adType2')

        #############################
        # set title
        #############################
        self.web_input(By.ID, 'postad-title', ad_cfg["title"])

        #############################
        # set category
        #############################
        # trigger and wait for automatic category detection
        self.web_click(By.ID, 'pstad-price')
        try:
            self.web_find(By.XPATH, "//*[@id='postad-category-path'][text()]")
            is_category_auto_selected = True
        except:
            is_category_auto_selected = False

        if ad_cfg["category"]:
            self.web_click(By.ID, 'pstad-lnk-chngeCtgry')
            self.web_find(By.ID, 'postad-step1-sbmt')

            category_url = f'{self.root_url}/p-kategorie-aendern.html#?path={ad_cfg["category"]}'
            self.web_open(category_url)
            self.web_click(By.XPATH, "//*[@id='postad-step1-sbmt']/button")
        else:
            ensure(is_category_auto_selected, f'No category specified in [{ad_file}] and automatic category detection failed')

        #############################
        # set price
        #############################
        self.web_select(By.XPATH, "//select[@id='priceType']", ad_cfg["price_type"])
        if ad_cfg["price_type"] != 'GIVE_AWAY':
            self.web_input(By.ID, 'pstad-price', ad_cfg["price"])

        #############################
        # set description
        #############################
        self.web_execute("document.querySelector('#pstad-descrptn').value = `" + ad_cfg["description"].replace("`", "'") + "`")

        #############################
        # set contact zipcode
        #############################
        if ad_cfg["contact"]["zipcode"]:
            self.web_input(By.ID, 'pstad-zip', ad_cfg["contact"]["zipcode"])

        #############################
        # set contact street
        #############################
        if ad_cfg["contact"]["street"]:
            self.web_input(By.ID, 'pstad-street', ad_cfg["contact"]["street"])

        #############################
        # set contact name
        #############################
        if ad_cfg["contact"]["name"]:
            self.web_input(By.ID, 'postad-contactname', ad_cfg["contact"]["name"])

        #############################
        # set contact phone
        #############################
        if ad_cfg["contact"]["phone"]:
            self.web_input(By.ID, 'postad-phonenumber', ad_cfg["contact"]["phone"])

        #############################
        # upload images
        #############################
        LOG.info(" -> found %s", pluralize("image", ad_cfg["images"]))
        image_upload = self.web_find(By.XPATH, "//input[@type='file']")

        def count_uploaded_images():
            return len(self.webdriver.find_elements(By.CLASS_NAME, "imagebox-new-thumbnail"))

        for image in ad_cfg["images"]:
            LOG.info(" -> uploading image [%s]", image)
            previous_uploaded_images_count = count_uploaded_images()
            image_upload.send_keys(image)
            start_at = time.time()
            while previous_uploaded_images_count == count_uploaded_images() and time.time() - start_at < 60:
                print(".", end = '', flush = True)
                time.sleep(1)
            print(flush = True)

            ensure(previous_uploaded_images_count < count_uploaded_images(), f"Couldn't upload image [{image}] within 60 seconds")
            LOG.debug("   => uploaded image within %i seconds", time.time() - start_at)

        #############################
        # submit
        #############################
        self.web_click(By.ID, 'pstad-submit')
        self.web_await(EC.url_contains("p-anzeige-aufgeben-bestaetigung.html?adId="), 20)

        ad_cfg_orig["updated_on"] = datetime.utcnow().isoformat()
        if not ad_cfg_orig["created_on"] and not ad_cfg_orig["id"]:
            ad_cfg_orig["created_on"] = ad_cfg_orig["updated_on"]

        # extract the ad id from the URL's query parameter
        current_url_query_params = urllib.parse.parse_qs(urllib.parse.urlparse(self.webdriver.current_url).query)
        ad_id = int(current_url_query_params.get('adId', None)[0])
        ad_cfg_orig["id"] = ad_id

        LOG.info(" -> SUCCESS: ad published with ID %s", ad_id)

        utils.save_dict(ad_file, ad_cfg_orig)


#############################
# main entry point
#############################
def main(args:Iterable[str]):
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


if __name__ == '__main__':
    utils.configure_console_logging()
    LOG.error("Direct execution not supported. Use 'python -m kleinanzeigen_bot'")
    sys.exit(1)
