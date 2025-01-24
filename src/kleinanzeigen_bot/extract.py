"""
SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
import logging, os, shutil
import urllib.request as urllib_request
import mimetypes
from datetime import datetime
from typing import Any, Final
import json

from .i18n import get_translating_logger, pluralize
from .utils import is_integer, parse_decimal, save_dict, calculate_content_hash
from .web_scraping_mixin import Browser, By, Element, Is, WebScrapingMixin

__all__ = [
    "AdExtractor",
]

LOG:Final[logging.Logger] = get_translating_logger(__name__)


class AdExtractor(WebScrapingMixin):
    """
    Wrapper class for ad extraction that uses an active bot´s browser session to extract specific elements from an ad page.
    """

    def __init__(self, browser:Browser, config:dict[str, Any]):
        super().__init__()
        self.browser = browser
        self.config = config

    async def download_ad(self, ad_id:int) -> None:
        """
        Downloads an ad to a specific location, specified by config and ad ID.
        NOTE: Requires that the driver session currently is on the ad page.

        :param ad_id: the ad ID
        """

        # create sub-directory for ad(s) to download (if necessary):
        relative_directory = 'downloaded-ads'
        # make sure configured base directory exists
        if not os.path.exists(relative_directory) or not os.path.isdir(relative_directory):
            os.mkdir(relative_directory)
            LOG.info('Created ads directory at ./%s.', relative_directory)

        new_base_dir = os.path.join(relative_directory, f'ad_{ad_id}')
        if os.path.exists(new_base_dir):
            LOG.info('Deleting current folder of ad %s...', ad_id)
            shutil.rmtree(new_base_dir)
        os.mkdir(new_base_dir)
        LOG.info('New directory for ad created at %s.', new_base_dir)

        # call extraction function
        info = await self._extract_ad_page_info(new_base_dir, ad_id)
        ad_file_path = new_base_dir + '/' + f'ad_{ad_id}.yaml'
        save_dict(ad_file_path, info)

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
            image_box = await self.web_find(By.CLASS_NAME, 'galleryimage-large')

            n_images = len(await self.web_find_all(By.CSS_SELECTOR, '.galleryimage-element[data-ix]', parent = image_box))
            LOG.info('Found %s.', pluralize("image", n_images))

            img_element:Element = await self.web_find(By.CSS_SELECTOR, 'div:nth-child(1) > img', parent = image_box)
            img_fn_prefix = 'ad_' + str(ad_id) + '__img'

            img_nr = 1
            dl_counter = 0
            while img_nr <= n_images:  # scrolling + downloading
                current_img_url = img_element.attrs['src']  # URL of the image
                if current_img_url is None:
                    continue

                with urllib_request.urlopen(current_img_url) as response:  # nosec B310
                    content_type = response.info().get_content_type()
                    file_ending = mimetypes.guess_extension(content_type)
                    img_path = f"{directory}/{img_fn_prefix}{img_nr}{file_ending}"
                    with open(img_path, 'wb') as f:
                        shutil.copyfileobj(response, f)
                    dl_counter += 1
                    img_paths.append(img_path.rsplit('/', maxsplit = 1)[-1])

                # navigate to next image (if exists)
                if img_nr < n_images:
                    try:
                        # click next button, wait, and re-establish reference
                        await (await self.web_find(By.CLASS_NAME, 'galleryimage--navigation--next')).click()
                        new_div = await self.web_find(By.CSS_SELECTOR, f'div.galleryimage-element:nth-child({img_nr + 1})')
                        img_element = await self.web_find(By.TAG_NAME, 'img', parent = new_div)
                    except TimeoutError:
                        LOG.error('NEXT button in image gallery somehow missing, aborting image fetching.')
                        break
                img_nr += 1
            LOG.info('Downloaded %s.', pluralize("image", dl_counter))

        except TimeoutError:  # some ads do not require images
            LOG.warning('No image area found. Continuing without downloading images.')

        return img_paths

    def extract_ad_id_from_ad_url(self, url: str) -> int:
        """
        Extracts the ID of an ad, given by its reference link.

        :param url: the URL to the ad page
        :return: the ad ID, a (ten-digit) integer number
        """
        num_part = url.split('/')[-1]  # suffix
        id_part = num_part.split('-')[0]

        try:
            return int(id_part)
        except ValueError:
            LOG.warning('The ad ID could not be extracted from the given URL %s', url)
            return -1

    async def extract_own_ads_urls(self) -> list[str]:
        """
        Extracts the references to all own ads.

        :return: the links to your ad pages
        """
        # navigate to "your ads" page
        await self.web_open('https://www.kleinanzeigen.de/m-meine-anzeigen.html')
        await self.web_sleep(2000, 3000)

        # collect ad references:
        pagination_section = await self.web_find(By.CSS_SELECTOR, 'section:nth-of-type(4)',
                parent = await self.web_find(By.CSS_SELECTOR, '.l-splitpage'))

        # scroll down to load dynamically
        await self.web_scroll_page_down()
        await self.web_sleep(2000, 3000)

        # detect multi-page
        try:
            pagination = await self.web_find(By.CSS_SELECTOR, 'div > div:nth-of-type(2) > div:nth-of-type(2) > div',
                    parent = pagination_section)
        except TimeoutError:  # 0 ads - no pagination area
            LOG.warning('There are currently no ads on your profile!')
            return []

        n_buttons = len(await self.web_find_all(By.CSS_SELECTOR, 'button',
                parent = await self.web_find(By.CSS_SELECTOR, 'div:nth-of-type(1)', parent = pagination)))
        if n_buttons > 1:
            multi_page = True
            LOG.info('It looks like you have many ads!')
        else:
            multi_page = False
            LOG.info('It looks like all your ads fit on one overview page.')

        refs:list[str] = []
        while True:  # loop reference extraction until no more forward page
            # extract references
            list_items = await self.web_find_all(By.CLASS_NAME, 'cardbox',
                    parent = await self.web_find(By.ID, 'my-manageitems-adlist'))
            refs += [
                (await self.web_find(By.CSS_SELECTOR, 'article > section > section:nth-of-type(2) > h2 > div > a', parent = li)).attrs['href']
                for li in list_items
            ]

            if not multi_page:  # only one iteration for single-page overview
                break
            # check if last page
            nav_button:Element = (await self.web_find_all(By.CSS_SELECTOR, 'button.jsx-1553636621'))[-1]
            if nav_button.attrs['title'] != 'Nächste':
                LOG.info('Last ad overview page explored.')
                break
            # navigate to next overview page
            await nav_button.click()
            await self.web_sleep(2000, 3000)
            await self.web_scroll_page_down()

        return refs

    async def naviagte_to_ad_page(self, id_or_url:int | str) -> bool:
        """
        Navigates to an ad page specified with an ad ID; or alternatively by a given URL.
        :return: whether the navigation to the ad page was successful
        """
        if is_integer(id_or_url):
            # navigate to start page, otherwise page can be None!
            await self.web_open('https://www.kleinanzeigen.de/')
            # enter the ad ID into the search bar
            await self.web_input(By.ID, "site-search-query", id_or_url)
            # navigate to ad page and wait
            await self.web_check(By.ID, 'site-search-submit', Is.CLICKABLE)
            submit_button = await self.web_find(By.ID, 'site-search-submit')
            await submit_button.click()
        else:
            await self.web_open(str(id_or_url))  # navigate to URL directly given
        await self.web_sleep()

        # handle the case that invalid ad ID given
        if self.page.url.endswith('k0'):
            LOG.error('There is no ad under the given ID.')
            return False

        # close (warning) popup, if given
        try:
            await self.web_find(By.ID, 'vap-ovrly-secure')
            LOG.warning('A popup appeared!')
            await self.web_click(By.CLASS_NAME, 'mfp-close')
            await self.web_sleep()
        except TimeoutError:
            pass
        return True

    async def _extract_ad_page_info(self, directory:str, ad_id:int) -> dict[str, Any]:
        """
        Extracts all necessary information from an ad´s page.

        :param directory: the path of the ad´s previously created directory
        :param ad_id: the ad ID, already extracted by a calling function
        :return: a dictionary with the keys as given in an ad YAML, and their respective values
        """
        info:dict[str, Any] = {'active': True}

        # extract basic info
        info['type'] = 'OFFER' if 's-anzeige' in self.page.url else 'WANTED'
        title:str = await self.web_text(By.ID, 'viewad-title')
        LOG.info('Extracting information from ad with title \"%s\"', title)

        info['category'] = await self._extract_category_from_ad_page()
        info['title'] = title
        info['description'] = (await self.web_text(By.ID, 'viewad-description-text')).strip() \
            .removeprefix((self.config["ad_defaults"]["description"]["prefix"] or "").strip()) \
            .removesuffix((self.config["ad_defaults"]["description"]["suffix"] or "").strip())
        info['special_attributes'] = await self._extract_special_attributes_from_ad_page()
        if "art_s" in info['special_attributes']:
            # change e.g. category "161/172" to "161/172/lautsprecher_kopfhoerer"
            info['category'] = f"{info['category']}/{info['special_attributes']['art_s']}"
            del info['special_attributes']['art_s']
        if "schaden_s" in info['special_attributes']:
            # change f to  'nein' and 't' to 'ja'
            info['special_attributes']['schaden_s'] = info['special_attributes']['schaden_s'].translate(str.maketrans({'t': 'ja', 'f': 'nein'}))
        info['price'], info['price_type'] = await self._extract_pricing_info_from_ad_page()
        info['shipping_type'], info['shipping_costs'], info['shipping_options'] = await self._extract_shipping_info_from_ad_page()
        info['sell_directly'] = await self._extract_sell_directly_from_ad_page()
        info['images'] = await self._download_images_from_ad_page(directory, ad_id)
        info['contact'] = await self._extract_contact_from_ad_page()
        info['id'] = ad_id

        try:  # try different locations known for creation date element
            creation_date = await self.web_text(By.XPATH,
                '/html/body/div[1]/div[2]/div/section[2]/section/section/article/div[3]/div[2]/div[2]/div[1]/span')
        except TimeoutError:
            creation_date = await self.web_text(By.CSS_SELECTOR, '#viewad-extra-info > div:nth-child(1) > span:nth-child(2)')

        # convert creation date to ISO format
        created_parts = creation_date.split('.')
        creation_date = created_parts[2] + '-' + created_parts[1] + '-' + created_parts[0] + ' 00:00:00'
        creation_date = datetime.fromisoformat(creation_date).isoformat()
        info['created_on'] = creation_date
        info['updated_on'] = None  # will be set later on

        # Berechne den initialen Hash für die heruntergeladene Anzeige
        info['content_hash'] = calculate_content_hash(info)

        return info

    async def _extract_category_from_ad_page(self) -> str:
        """
        Extracts a category of an ad in numerical form.
        Assumes that the web driver currently shows an ad page.

        :return: a category string of form abc/def, where a-f are digits
        """
        category_line = await self.web_find(By.ID, 'vap-brdcrmb')
        category_first_part = await self.web_find(By.CSS_SELECTOR, 'a:nth-of-type(2)', parent = category_line)
        category_second_part = await self.web_find(By.CSS_SELECTOR, 'a:nth-of-type(3)', parent = category_line)
        cat_num_first = category_first_part.attrs['href'].split('/')[-1][1:]
        cat_num_second = category_second_part.attrs['href'].split('/')[-1][1:]
        category:str = cat_num_first + '/' + cat_num_second

        return category

    async def _extract_special_attributes_from_ad_page(self) -> dict[str, Any]:
        """
        Extracts the special attributes from an ad page.
        If no items are available then special_attributes is empty

        :return: a dictionary (possibly empty) where the keys are the attribute names, mapped to their values
        """
        belen_conf = await self.web_execute("window.BelenConf")

        # e.g. "art_s:lautsprecher_kopfhoerer|condition_s:like_new|versand_s:t"
        special_attributes_str = belen_conf["universalAnalyticsOpts"]["dimensions"]["dimension108"]

        special_attributes = dict(item.split(":") for item in special_attributes_str.split("|") if ":" in item)
        special_attributes = {k: v for k, v in special_attributes.items() if not k.endswith('.versand_s') and k != "versand_s"}
        return special_attributes

    async def _extract_pricing_info_from_ad_page(self) -> tuple[float | None, str]:
        """
        Extracts the pricing information (price and pricing type) from an ad page.

        :return: the price of the offer (optional); and the pricing type
        """
        try:
            price_str:str = await self.web_text(By.ID, 'viewad-price')
            price:int | None = None
            match price_str.split()[-1]:
                case '€':
                    price_type = 'FIXED'
                    # replace('.', '') is to remove the thousands separator before parsing as int
                    price = int(price_str.replace('.', '').split()[0])
                case 'VB':
                    price_type = 'NEGOTIABLE'
                    if not price_str == "VB":  # can be either 'X € VB', or just 'VB'
                        price = int(price_str.replace('.', '').split()[0])
                case 'verschenken':
                    price_type = 'GIVE_AWAY'
                case _:
                    price_type = 'NOT_APPLICABLE'
            return price, price_type
        except TimeoutError:  # no 'commercial' ad, has no pricing box etc.
            return None, 'NOT_APPLICABLE'

    async def _extract_shipping_info_from_ad_page(self) -> tuple[str, float | None, list[str] | None]:
        """
        Extracts shipping information from an ad page.

        :return: the shipping type, and the shipping price (optional)
        """
        ship_type, ship_costs, shipping_options = 'NOT_APPLICABLE', None, None
        try:
            shipping_text = await self.web_text(By.CLASS_NAME, 'boxedarticle--details--shipping')
            # e.g. '+ Versand ab 5,49 €' OR 'Nur Abholung'
            if shipping_text == 'Nur Abholung':
                ship_type = 'PICKUP'
            elif shipping_text == 'Versand möglich':
                ship_type = 'SHIPPING'
            elif '€' in shipping_text:
                shipping_price_parts = shipping_text.split(' ')
                ship_type = 'SHIPPING'
                ship_costs = float(parse_decimal(shipping_price_parts[-2]))

                # reading shipping option from kleinanzeigen
                # and find the right one by price
                shipping_costs = json.loads(
                    (await self.web_request("https://gateway.kleinanzeigen.de/postad/api/v1/shipping-options?posterType=PRIVATE"))
                    ["content"])["data"]["shippingOptionsResponse"]["options"]

                internal_shipping_opt = [x for x in shipping_costs if x["priceInEuroCent"] == ship_costs * 100]

                if not internal_shipping_opt:
                    return 'NOT_APPLICABLE', ship_costs, shipping_options

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
                    "HERMES_004": "Hermes_L"
                }

                shipping_option = shipping_option_mapping.get(internal_shipping_opt[0]['id'])
                if not shipping_option:
                    return 'NOT_APPLICABLE', ship_costs, shipping_options

                shipping_options = [shipping_option]
        except TimeoutError:  # no pricing box -> no shipping given
            ship_type = 'NOT_APPLICABLE'

        return ship_type, ship_costs, shipping_options

    async def _extract_sell_directly_from_ad_page(self) -> bool | None:
        """
        Extracts the sell directly option from an ad page.

        :return: a boolean indicating whether the sell directly option is active (optional)
        """
        try:
            buy_now_is_active:bool = 'Direkt kaufen' in (await self.web_text(By.ID, 'payment-buttons-sidebar'))
            return buy_now_is_active
        except TimeoutError:
            return None

    async def _extract_contact_from_ad_page(self) -> dict[str, (str | None)]:
        """
        Processes the address part involving street (optional), zip code + city, and phone number (optional).

        :return: a dictionary containing the address parts with their corresponding values
        """
        contact:dict[str, (str | None)] = {}
        address_text = await self.web_text(By.ID, 'viewad-locality')
        # format: e.g. (Beispiel Allee 42,) 12345 Bundesland - Stadt
        try:
            street = (await self.web_text(By.ID, 'street-address'))[:-1]  # trailing comma
            contact['street'] = street
        except TimeoutError:
            LOG.info('No street given in the contact.')

        (zipcode, location) = address_text.split(" ", 1)
        contact['zipcode'] = zipcode  # e.g. 19372
        contact['location'] = location  # e.g. Mecklenburg-Vorpommern - Steinbeck

        contact_person_element:Element = await self.web_find(By.ID, 'viewad-contact')
        name_element = await self.web_find(By.CLASS_NAME, 'iconlist-text', parent = contact_person_element)
        try:
            name = await self.web_text(By.TAG_NAME, 'a', parent = name_element)
        except TimeoutError:  # edge case: name without link
            name = await self.web_text(By.TAG_NAME, 'span', parent = name_element)
        contact['name'] = name

        if 'street' not in contact:
            contact['street'] = None
        try:  # phone number is unusual for non-professional sellers today
            phone_element = await self.web_find(By.ID, 'viewad-contact-phone')
            phone_number = await self.web_text(By.TAG_NAME, 'a', parent = phone_element)
            contact['phone'] = ''.join(phone_number.replace('-', ' ').split(' ')).replace('+49(0)', '0')
        except TimeoutError:
            contact['phone'] = None  # phone seems to be a deprecated feature (for non-professional users)
        # also see 'https://themen.kleinanzeigen.de/hilfe/deine-anzeigen/Telefon/

        return contact
