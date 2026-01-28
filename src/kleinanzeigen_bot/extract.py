# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import asyncio
from gettext import gettext as _

import json, mimetypes, re, shutil  # isort: skip
import urllib.error as urllib_error
import urllib.request as urllib_request
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from kleinanzeigen_bot.model.ad_model import ContactPartial

from .model.ad_model import AdPartial
from .model.config_model import Config
from .utils import dicts, files, i18n, loggers, misc, reflect, xdg_paths
from .utils.web_scraping_mixin import Browser, By, Element, WebScrapingMixin

__all__ = [
    "AdExtractor",
]

LOG:Final[loggers.Logger] = loggers.get_logger(__name__)

_BREADCRUMB_MIN_DEPTH:Final[int] = 2
BREADCRUMB_RE = re.compile(r"/c(\d+)")


class AdExtractor(WebScrapingMixin):
    """
    Wrapper class for ad extraction that uses an active bot´s browser session to extract specific elements from an ad page.
    """

    def __init__(self, browser:Browser, config:Config, installation_mode:xdg_paths.InstallationMode = "portable") -> None:
        super().__init__()
        self.browser = browser
        self.config:Config = config
        if installation_mode not in {"portable", "xdg"}:
            raise ValueError(f"Unsupported installation mode: {installation_mode}")
        self.installation_mode:xdg_paths.InstallationMode = installation_mode

    async def download_ad(self, ad_id:int) -> None:
        """
        Downloads an ad to a specific location, specified by config and ad ID.
        NOTE: Requires that the driver session currently is on the ad page.

        :param ad_id: the ad ID
        """

        # create sub-directory for ad(s) to download (if necessary):
        download_dir = xdg_paths.get_downloaded_ads_path(self.installation_mode)
        LOG.info("Using download directory: %s", download_dir)
        # Note: xdg_paths.get_downloaded_ads_path() already creates the directory

        # Extract ad info and determine final directory path
        ad_cfg, final_dir = await self._extract_ad_page_info_with_directory_handling(download_dir, ad_id)

        # Save the ad configuration file (offload to executor to avoid blocking the event loop)
        ad_file_path = str(Path(final_dir) / f"ad_{ad_id}.yaml")
        header_string = (
            "# yaml-language-server: $schema=https://raw.githubusercontent.com/Second-Hand-Friends/kleinanzeigen-bot/refs/heads/main/schemas/ad.schema.json"
        )
        await asyncio.get_running_loop().run_in_executor(None, lambda: dicts.save_dict(ad_file_path, ad_cfg.model_dump(), header = header_string))

    @staticmethod
    def _download_and_save_image_sync(url:str, directory:str, filename_prefix:str, img_nr:int) -> str | None:
        try:
            with urllib_request.urlopen(url) as response:  # noqa: S310 Audit URL open for permitted schemes.
                content_type = response.info().get_content_type()
                file_ending = mimetypes.guess_extension(content_type) or ""
                # Use pathlib.Path for OS-agnostic path handling
                img_path = Path(directory) / f"{filename_prefix}{img_nr}{file_ending}"
                with open(img_path, "wb") as f:
                    shutil.copyfileobj(response, f)
                return str(img_path)
        except (urllib_error.URLError, urllib_error.HTTPError, OSError, shutil.Error) as e:
            # Narrow exception handling to expected network/filesystem errors
            LOG.warning("Failed to download image %s: %s", url, e)
            return None

    async def _download_images_from_ad_page(self, directory:str, ad_id:int) -> list[str]:
        """
        Downloads all images of an ad.

        :param directory: the path of the directory created for this ad
        :param ad_id: the ID of the ad to download the images from
        :return: the relative paths for all downloaded images
        """

        n_images:int
        img_paths = []
        try:
            # download all images from box
            image_box = await self.web_find(By.CLASS_NAME, "galleryimage-large")

            images = await self.web_find_all(By.CSS_SELECTOR, ".galleryimage-element[data-ix] > img", parent = image_box)
            n_images = len(images)
            LOG.info("Found %s.", i18n.pluralize("image", n_images))

            img_fn_prefix = "ad_" + str(ad_id) + "__img"
            img_nr = 1
            dl_counter = 0

            loop = asyncio.get_running_loop()

            for img_element in images:
                current_img_url = img_element.attrs["src"]  # URL of the image
                if current_img_url is None:
                    continue

                img_path = await loop.run_in_executor(None, self._download_and_save_image_sync, str(current_img_url), directory, img_fn_prefix, img_nr)

                if img_path:
                    dl_counter += 1
                    # Use pathlib.Path for OS-agnostic path handling
                    img_paths.append(Path(img_path).name)

                img_nr += 1
            LOG.info("Downloaded %s.", i18n.pluralize("image", dl_counter))

        except TimeoutError:  # some ads do not require images
            LOG.warning("No image area found. Continuing without downloading images.")

        return img_paths

    def extract_ad_id_from_ad_url(self, url:str) -> int:
        """
        Extracts the ID of an ad, given by its reference link.

        :param url: the URL to the ad page
        :return: the ad ID, a (ten-digit) integer number
        """

        try:
            path = url.split("?", maxsplit = 1)[0]  # Remove query string if present
            last_segment = path.rstrip("/").rsplit("/", maxsplit = 1)[-1]  # Get last path component
            id_part = last_segment.split("-", maxsplit = 1)[0]  # Extract part before first hyphen
            return int(id_part)
        except (IndexError, ValueError) as ex:
            LOG.warning("Failed to extract ad ID from URL '%s': %s", url, ex)
            return -1

    async def extract_own_ads_urls(self) -> list[str]:
        """
        Extracts the references to all own ads.

        :return: the links to your ad pages
        """
        refs:list[str] = []

        async def extract_page_refs(page_num:int) -> bool:
            """Extract ad reference URLs from the current page.

            :param page_num: The current page number being processed
            :return: True to stop pagination (e.g. ads container disappeared), False to continue to next page
            """
            try:
                ad_list_container = await self.web_find(By.ID, "my-manageitems-adlist")
                list_items = await self.web_find_all(By.CLASS_NAME, "cardbox", parent = ad_list_container)
                LOG.info("Found %s ad items on page %s.", len(list_items), page_num)

                page_refs:list[str] = [str((await self.web_find(By.CSS_SELECTOR, "div h3 a.text-onSurface", parent = li)).attrs["href"]) for li in list_items]
                refs.extend(page_refs)
                LOG.info("Successfully extracted %s refs from page %s.", len(page_refs), page_num)
                return False  # Continue to next page

            except TimeoutError:
                LOG.warning("Could not find ad list container or items on page %s.", page_num)
                return True  # Stop pagination (ads disappeared)
            except Exception as e:
                # Continue despite error for resilience against transient web scraping issues
                # (e.g., DOM structure changes, network glitches). LOG.exception ensures visibility.
                LOG.exception("Error extracting refs on page %s: %s", page_num, e)
                return False  # Continue to next page

        await self._navigate_paginated_ad_overview(extract_page_refs)

        if not refs:
            LOG.warning("No ad URLs were extracted.")

        return refs

    async def navigate_to_ad_page(self, id_or_url:int | str) -> bool:
        """
        Navigates to an ad page specified with an ad ID; or alternatively by a given URL.
        :return: whether the navigation to the ad page was successful
        """
        if reflect.is_integer(id_or_url):
            # navigate to search page
            await self.web_open("https://www.kleinanzeigen.de/s-suchanfrage.html?keywords={0}".format(id_or_url))
        else:
            await self.web_open(str(id_or_url))  # navigate to URL directly given
        await self.web_sleep()

        # handle the case that invalid ad ID given
        if self.page.url.endswith("k0"):
            LOG.error("There is no ad under the given ID.")
            return False

        # close (warning) popup, if given
        try:
            await self.web_find(By.ID, "vap-ovrly-secure")
            LOG.warning("A popup appeared!")
            await self.web_click(By.CLASS_NAME, "mfp-close")
            await self.web_sleep()
        except TimeoutError:
            # Popup did not appear within timeout.
            pass
        return True

    async def _extract_title_from_ad_page(self) -> str:
        """
        Extracts the title from an ad page.
        Assumes that the web driver currently shows an ad page.

        :return: the ad title
        """
        return await self.web_text(By.ID, "viewad-title")

    async def _extract_ad_page_info(self, directory:str, ad_id:int) -> AdPartial:
        """
        Extracts ad information and downloads images to the specified directory.
        NOTE: Requires that the driver session currently is on the ad page.

        :param directory: the directory to download images to
        :param ad_id: the ad ID
        :return: an AdPartial object containing the ad information
        """
        info:dict[str, Any] = {"active": True}

        # extract basic info
        info["type"] = "OFFER" if "s-anzeige" in self.page.url else "WANTED"

        # Extract title
        title = await self._extract_title_from_ad_page()

        belen_conf = await self.web_execute("window.BelenConf")

        info["category"] = await self._extract_category_from_ad_page()

        # append subcategory and change e.g. category "161/172" to "161/172/lautsprecher_kopfhoerer"
        # take subcategory from third_category_name as key 'art_s' sometimes is a special attribute (e.g. gender for clothes)
        # the subcategory isn't really necessary, but when set, the appropriate special attribute gets preselected
        if third_category_id := belen_conf["universalAnalyticsOpts"]["dimensions"].get("l3_category_id"):
            info["category"] += f"/{third_category_id}"

        info["title"] = title

        # Get raw description text
        raw_description = (await self.web_text(By.ID, "viewad-description-text")).strip()

        # Get prefix and suffix from config
        prefix = self.config.ad_defaults.description_prefix
        suffix = self.config.ad_defaults.description_suffix

        # Remove prefix and suffix if present
        description_text = raw_description
        if prefix and description_text.startswith(prefix.strip()):
            description_text = description_text[len(prefix.strip()):]
        if suffix and description_text.endswith(suffix.strip()):
            description_text = description_text[: -len(suffix.strip())]

        info["description"] = description_text.strip()

        info["special_attributes"] = await self._extract_special_attributes_from_ad_page(belen_conf)

        if "schaden_s" in info["special_attributes"]:
            # change f to  'nein' and 't' to 'ja'
            info["special_attributes"]["schaden_s"] = info["special_attributes"]["schaden_s"].translate(str.maketrans({"t": "ja", "f": "nein"}))
        info["price"], info["price_type"] = await self._extract_pricing_info_from_ad_page()
        info["shipping_type"], info["shipping_costs"], info["shipping_options"] = await self._extract_shipping_info_from_ad_page()
        info["sell_directly"] = await self._extract_sell_directly_from_ad_page()
        info["images"] = await self._download_images_from_ad_page(directory, ad_id)
        info["contact"] = await self._extract_contact_from_ad_page()
        info["id"] = ad_id

        try:  # try different locations known for creation date element
            creation_date = await self.web_text(By.XPATH, "/html/body/div[1]/div[2]/div/section[2]/section/section/article/div[3]/div[2]/div[2]/div[1]/span")
        except TimeoutError:
            creation_date = await self.web_text(By.CSS_SELECTOR, "#viewad-extra-info > div:nth-child(1) > span:nth-child(2)")

        # convert creation date to ISO format
        created_parts = creation_date.split(".")
        creation_date_str = created_parts[2] + "-" + created_parts[1] + "-" + created_parts[0] + " 00:00:00"
        creation_date_dt = datetime.fromisoformat(creation_date_str)
        info["created_on"] = creation_date_dt
        info["updated_on"] = None  # will be set later on

        ad_cfg = AdPartial.model_validate(info)

        # calculate the initial hash for the downloaded ad
        ad_cfg.content_hash = ad_cfg.to_ad(self.config.ad_defaults).update_content_hash().content_hash

        return ad_cfg

    async def _extract_ad_page_info_with_directory_handling(self, relative_directory:Path, ad_id:int) -> tuple[AdPartial, Path]:
        """
        Extracts ad information and handles directory creation/renaming.

        :param relative_directory: Base directory for downloads
        :param ad_id: The ad ID
        :return: AdPartial with directory information
        """
        # First, extract basic info to get the title
        info:dict[str, Any] = {"active": True}

        # extract basic info
        info["type"] = "OFFER" if "s-anzeige" in self.page.url else "WANTED"
        title = await self._extract_title_from_ad_page()
        LOG.info('Extracting title from ad %s: "%s"', ad_id, title)

        # Determine the final directory path
        sanitized_title = misc.sanitize_folder_name(title, self.config.download.folder_name_max_length)
        final_dir = relative_directory / f"ad_{ad_id}_{sanitized_title}"
        temp_dir = relative_directory / f"ad_{ad_id}"

        loop = asyncio.get_running_loop()

        # Handle existing directories
        if await files.exists(final_dir):
            # If the folder with title already exists, delete it
            LOG.info("Deleting current folder of ad %s...", ad_id)
            LOG.debug("Removing directory tree: %s", final_dir)
            await loop.run_in_executor(None, shutil.rmtree, str(final_dir))

        if await files.exists(temp_dir):
            if self.config.download.rename_existing_folders:
                # Rename the old folder to the new name with title
                LOG.info("Renaming folder from %s to %s for ad %s...", temp_dir.name, final_dir.name, ad_id)
                LOG.debug("Renaming: %s -> %s", temp_dir, final_dir)
                await loop.run_in_executor(None, temp_dir.rename, final_dir)
            else:
                # Use the existing folder without renaming
                final_dir = temp_dir
                LOG.info("Using existing folder for ad %s at %s.", ad_id, final_dir)
        else:
            # Create new directory with title
            LOG.debug("Creating new directory: %s", final_dir)
            await loop.run_in_executor(None, final_dir.mkdir)
            LOG.info("New directory for ad created at %s.", final_dir)

        # Now extract complete ad info (including images) to the final directory
        ad_cfg = await self._extract_ad_page_info(str(final_dir), ad_id)

        return ad_cfg, final_dir

    async def _extract_category_from_ad_page(self) -> str:
        """
        Extracts a category of an ad in numerical form.
        Assumes that the web driver currently shows an ad page.

        :return: a category string of form abc/def, where a-f are digits
        """
        try:
            category_line = await self.web_find(By.ID, "vap-brdcrmb")
        except TimeoutError as exc:
            LOG.warning("Breadcrumb container 'vap-brdcrmb' not found; cannot extract ad category: %s", exc)
            raise
        try:
            breadcrumb_links = await self.web_find_all(By.CSS_SELECTOR, "a", parent = category_line)
        except TimeoutError:
            breadcrumb_links = []

        category_ids:list[str] = []
        for link in breadcrumb_links:
            href = str(link.attrs.get("href", "") or "")
            matches = BREADCRUMB_RE.findall(href)
            if matches:
                category_ids.extend(matches)

        # Use the deepest two breadcrumb category codes when available.
        if len(category_ids) >= _BREADCRUMB_MIN_DEPTH:
            return f"{category_ids[-2]}/{category_ids[-1]}"
        if len(category_ids) == 1:
            return f"{category_ids[0]}/{category_ids[0]}"

        # Fallback to legacy selectors in case the breadcrumb structure is unexpected.
        LOG.debug("Falling back to legacy breadcrumb selectors; collected ids: %s", category_ids)
        fallback_timeout = self._effective_timeout()
        try:
            category_first_part = await self.web_find(By.CSS_SELECTOR, "a:nth-of-type(2)", parent = category_line)
            category_second_part = await self.web_find(By.CSS_SELECTOR, "a:nth-of-type(3)", parent = category_line)
        except TimeoutError as exc:
            LOG.error("Legacy breadcrumb selectors not found within %.1f seconds (collected ids: %s)", fallback_timeout, category_ids)
            raise TimeoutError(_("Unable to locate breadcrumb fallback selectors within %(seconds).1f seconds.") % {"seconds": fallback_timeout}) from exc
        href_first:str = str(category_first_part.attrs["href"])
        href_second:str = str(category_second_part.attrs["href"])
        cat_num_first_raw = href_first.rsplit("/", maxsplit = 1)[-1]
        cat_num_second_raw = href_second.rsplit("/", maxsplit = 1)[-1]
        cat_num_first = cat_num_first_raw[1:] if cat_num_first_raw.startswith("c") else cat_num_first_raw
        cat_num_second = cat_num_second_raw[1:] if cat_num_second_raw.startswith("c") else cat_num_second_raw
        category:str = cat_num_first + "/" + cat_num_second

        return category

    async def _extract_special_attributes_from_ad_page(self, belen_conf:dict[str, Any]) -> dict[str, str]:
        """
        Extracts the special attributes from an ad page.
        If no items are available then special_attributes is empty

        :return: a dictionary (possibly empty) where the keys are the attribute names, mapped to their values
        """

        # e.g. "art_s:lautsprecher_kopfhoerer|condition_s:like_new|versand_s:t"
        special_attributes_str = belen_conf["universalAnalyticsOpts"]["dimensions"].get("ad_attributes")
        if not special_attributes_str:
            return {}
        special_attributes = dict(item.split(":") for item in special_attributes_str.split("|") if ":" in item)
        special_attributes = {k: v for k, v in special_attributes.items() if not k.endswith(".versand_s") and k != "versand_s"}
        return special_attributes

    async def _extract_pricing_info_from_ad_page(self) -> tuple[float | None, str]:
        """
        Extracts the pricing information (price and pricing type) from an ad page.

        :return: the price of the offer (optional); and the pricing type
        """
        try:
            price_str:str = await self.web_text(By.ID, "viewad-price")
            price:int | None = None
            match price_str.rsplit(maxsplit = 1)[-1]:
                case "€":
                    price_type = "FIXED"
                    # replace('.', '') is to remove the thousands separator before parsing as int
                    price = int(price_str.replace(".", "").split(maxsplit = 1)[0])
                case "VB":
                    price_type = "NEGOTIABLE"
                    if price_str != "VB":  # can be either 'X € VB', or just 'VB'
                        price = int(price_str.replace(".", "").split(maxsplit = 1)[0])
                case "verschenken":
                    price_type = "GIVE_AWAY"
                case _:
                    price_type = "NOT_APPLICABLE"
            return price, price_type
        except TimeoutError:  # no 'commercial' ad, has no pricing box etc.
            return None, "NOT_APPLICABLE"

    async def _extract_shipping_info_from_ad_page(self) -> tuple[str, float | None, list[str] | None]:
        """
        Extracts shipping information from an ad page.

        :return: the shipping type, and the shipping price (optional)
        """
        ship_type, ship_costs, shipping_options = "NOT_APPLICABLE", None, None
        try:
            shipping_text = await self.web_text(By.CLASS_NAME, "boxedarticle--details--shipping")
            # e.g. '+ Versand ab 5,49 €' OR 'Nur Abholung'
            if shipping_text == "Nur Abholung":
                ship_type = "PICKUP"
            elif shipping_text == "Versand möglich":
                ship_type = "SHIPPING"
            elif "€" in shipping_text:
                shipping_price_parts = shipping_text.split(" ")
                ship_type = "SHIPPING"
                ship_costs = float(misc.parse_decimal(shipping_price_parts[-2]))

                # reading shipping option from kleinanzeigen
                # and find the right one by price
                shipping_costs = json.loads(
                    (await self.web_request("https://gateway.kleinanzeigen.de/postad/api/v1/shipping-options?posterType=PRIVATE"))["content"]
                )["data"]["shippingOptionsResponse"]["options"]

                # map to internal shipping identifiers used by kleinanzeigen-bot
                shipping_option_mapping = {
                    "DHL_001": "DHL_2",
                    "DHL_002": "DHL_5",
                    "DHL_003": "DHL_10",
                    "DHL_004": "DHL_31,5",
                    "DHL_005": "DHL_20",
                    "HERMES_001": "Hermes_Päckchen",
                    "HERMES_002": "Hermes_S",
                    "HERMES_003": "Hermes_M",
                    "HERMES_004": "Hermes_L",
                }

                # Convert Euro to cents and round to nearest integer
                price_in_cent = round(ship_costs * 100)

                # If include_all_matching_shipping_options is enabled, get all options for the same package size
                if self.config.download.include_all_matching_shipping_options:
                    # Find all options with the same price to determine the package size
                    matching_options = [opt for opt in shipping_costs if opt["priceInEuroCent"] == price_in_cent]
                    if not matching_options:
                        return "SHIPPING", ship_costs, None

                    # Use the package size of the first matching option
                    matching_size = matching_options[0]["packageSize"]

                    # Get all options of the same size
                    shipping_options = [
                        shipping_option_mapping[opt["id"]]
                        for opt in shipping_costs
                        if opt["packageSize"] == matching_size
                        and opt["id"] in shipping_option_mapping
                        and shipping_option_mapping[opt["id"]] not in self.config.download.excluded_shipping_options
                    ]
                else:
                    # Only use the matching option if it's not excluded
                    matching_option = next((x for x in shipping_costs if x["priceInEuroCent"] == price_in_cent), None)
                    if not matching_option:
                        return "SHIPPING", ship_costs, None

                    shipping_option = shipping_option_mapping.get(matching_option["id"])
                    if not shipping_option or shipping_option in self.config.download.excluded_shipping_options:
                        return "SHIPPING", ship_costs, None
                    shipping_options = [shipping_option]

        except TimeoutError:  # no pricing box -> no shipping given
            ship_type = "NOT_APPLICABLE"

        return ship_type, ship_costs, shipping_options

    async def _extract_sell_directly_from_ad_page(self) -> bool | None:
        """
        Extracts the sell directly option from an ad page using the JSON API.

        :return: bool | None - True if buyNowEligible, False if not eligible, None if unknown
        """
        try:
            # Extract current ad ID from the page URL first
            current_ad_id = self.extract_ad_id_from_ad_url(self.page.url)
            if current_ad_id == -1:
                LOG.warning("Could not extract ad ID from URL: %s", self.page.url)
                return None

            # Helper function to safely coerce values to int or None
            def _coerce_page_number(value:Any) -> int | None:
                if value is None:
                    return None
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None

            # Fetch the management JSON data using web_request with pagination support
            page = 1
            MAX_PAGE_LIMIT = 100

            while True:
                response = await self.web_request(f"https://www.kleinanzeigen.de/m-meine-anzeigen-verwalten.json?sort=DEFAULT&page={page}")

                try:
                    json_data = json.loads(response["content"])
                except json.JSONDecodeError as ex:
                    LOG.debug("Failed to parse JSON response on page %s: %s", page, ex)
                    break

                # Find the current ad in the ads list
                if isinstance(json_data, dict) and "ads" in json_data:
                    ads_list = json_data["ads"]
                    if isinstance(ads_list, list):
                        # Filter ads to find the current ad by ID
                        current_ad = next((ad for ad in ads_list if ad.get("id") == current_ad_id), None)
                        if current_ad and "buyNowEligible" in current_ad:
                            buy_now_eligible = current_ad["buyNowEligible"]
                            return buy_now_eligible if isinstance(buy_now_eligible, bool) else None

                # Check if we need to fetch more pages
                paging = json_data.get("paging") if isinstance(json_data, dict) else None
                if not isinstance(paging, dict):
                    break

                # Safety check: don't paginate beyond reasonable limit
                if page > MAX_PAGE_LIMIT:
                    LOG.warning("Stopping pagination after %s pages to avoid infinite loop", MAX_PAGE_LIMIT)
                    break

                # Parse pagination info with explicit None checks (not truthy checks) to handle 0-based indexing
                # Support multiple field name variations
                if paging.get("pageNum") is not None:
                    current_page_num = _coerce_page_number(paging.get("pageNum"))
                elif paging.get("page") is not None:
                    current_page_num = _coerce_page_number(paging.get("page"))
                elif paging.get("currentPage") is not None:
                    current_page_num = _coerce_page_number(paging.get("currentPage"))
                else:
                    current_page_num = page

                if paging.get("last") is not None:
                    total_pages = _coerce_page_number(paging.get("last"))
                elif paging.get("pages") is not None:
                    total_pages = _coerce_page_number(paging.get("pages"))
                elif paging.get("totalPages") is not None:
                    total_pages = _coerce_page_number(paging.get("totalPages"))
                elif paging.get("pageCount") is not None:
                    total_pages = _coerce_page_number(paging.get("pageCount"))
                elif paging.get("maxPages") is not None:
                    total_pages = _coerce_page_number(paging.get("maxPages"))
                else:
                    total_pages = None

                # Stop if we've reached the last page or there's no pagination info
                if total_pages is None or (current_page_num is not None and current_page_num >= total_pages):
                    break

                # Always increment page counter to avoid infinite loops
                page += 1

            # If the key doesn't exist or ad not found, return None (unknown)
            return None

        except (TimeoutError, json.JSONDecodeError, KeyError, TypeError) as e:
            LOG.debug("Could not determine sell_directly status: %s", e)
            return None

    async def _extract_contact_from_ad_page(self) -> ContactPartial:
        """
        Processes the address part involving street (optional), zip code + city, and phone number (optional).

        :return: a dictionary containing the address parts with their corresponding values
        """
        contact:dict[str, (str | None)] = {}
        address_text = await self.web_text(By.ID, "viewad-locality")
        # format: e.g. (Beispiel Allee 42,) 12345 Bundesland - Stadt
        try:
            street = (await self.web_text(By.ID, "street-address"))[:-1]  # trailing comma
            contact["street"] = street
        except TimeoutError:
            LOG.info("No street given in the contact.")

        (zipcode, location) = address_text.split(" ", maxsplit = 1)
        contact["zipcode"] = zipcode  # e.g. 19372
        contact["location"] = location  # e.g. Mecklenburg-Vorpommern - Steinbeck

        contact_person_element:Element = await self.web_find(By.ID, "viewad-contact")
        name_element = await self.web_find(By.CLASS_NAME, "iconlist-text", parent = contact_person_element)
        try:
            name = await self.web_text(By.TAG_NAME, "a", parent = name_element)
        except TimeoutError:  # edge case: name without link
            name = await self.web_text(By.TAG_NAME, "span", parent = name_element)
        contact["name"] = name

        if "street" not in contact:
            contact["street"] = None
        try:  # phone number is unusual for non-professional sellers today
            phone_element = await self.web_find(By.ID, "viewad-contact-phone")
            phone_number = await self.web_text(By.TAG_NAME, "a", parent = phone_element)
            contact["phone"] = "".join(phone_number.replace("-", " ").split(" ")).replace("+49(0)", "0")
        except TimeoutError:
            contact["phone"] = None  # phone seems to be a deprecated feature (for non-professional users)
        # also see 'https://themen.kleinanzeigen.de/hilfe/deine-anzeigen/Telefon/

        return ContactPartial.model_validate(contact)
