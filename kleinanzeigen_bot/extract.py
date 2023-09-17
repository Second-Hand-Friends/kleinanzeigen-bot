"""
Copyright (C) 2022 Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import json
from decimal import DecimalException
from typing import Any

from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
import selenium.webdriver.support.expected_conditions as EC

from .selenium_mixin import SeleniumMixin
from .utils import parse_decimal, pause


class AdExtractor(SeleniumMixin):
    """
    Wrapper class for ad extraction that uses an active bot´s web driver to extract specific elements from an ad page.
    """

    def __init__(self, driver:WebDriver):
        super().__init__()
        self.webdriver = driver

    def extract_category_from_ad_page(self) -> str:
        """
        Extracts a category of an ad in numerical form.
        Assumes that the web driver currently shows an ad page.

        :return: a category string of form abc/def, where a-f are digits
        """
        category_line = self.webdriver.find_element(By.XPATH, '//*[@id="vap-brdcrmb"]')
        category_first_part = category_line.find_element(By.XPATH, './/a[2]')
        category_second_part = category_line.find_element(By.XPATH, './/a[3]')
        cat_num_first = category_first_part.get_attribute('href').split('/')[-1][1:]
        cat_num_second = category_second_part.get_attribute('href').split('/')[-1][1:]
        category:str = cat_num_first + '/' + cat_num_second

        return category

    def extract_special_attributes_from_ad_page(self) -> dict[str, Any]:
        """
        Extracts the special attributes from an ad page.

        :return: a dictionary (possibly empty) where the keys are the attribute names, mapped to their values
        """
        belen_conf = self.webdriver.execute_script("return window.BelenConf")
        special_attributes_str = belen_conf["universalAnalyticsOpts"]["dimensions"]["dimension108"]
        special_attributes = json.loads(special_attributes_str)
        if not isinstance(special_attributes, dict):
            raise ValueError(
                "Failed to parse special attributes from ad page."
                f"Expected a dictionary, but got a {type(special_attributes)}"
            )
        special_attributes = {k: v for k, v in special_attributes.items() if not k.endswith('.versand_s')}
        return special_attributes

    def extract_pricing_info_from_ad_page(self) -> tuple[float | None, str]:
        """
        Extracts the pricing information (price and pricing type) from an ad page.

        :return: the price of the offer (optional); and the pricing type
        """
        try:
            price_str:str = self.webdriver.find_element(By.CLASS_NAME, 'boxedarticle--price').text
            price_type:str
            price:float | None = -1
            match price_str.split()[-1]:
                case '€':
                    price_type = 'FIXED'
                    price = float(parse_decimal(price_str.split()[0].replace('.', '')))
                case 'VB':  # can be either 'X € VB', or just 'VB'
                    price_type = 'NEGOTIABLE'
                    try:
                        price = float(parse_decimal(price_str.split()[0].replace('.', '')))
                    except DecimalException:
                        price = None
                case 'verschenken':
                    price_type = 'GIVE_AWAY'
                    price = None
                case _:
                    price_type = 'NOT_APPLICABLE'
            return price, price_type
        except NoSuchElementException:  # no 'commercial' ad, has no pricing box etc.
            return None, 'NOT_APPLICABLE'

    def extract_shipping_info_from_ad_page(self) -> tuple[str, float | None, list[str] | None]:
        """
        Extracts shipping information from an ad page.

        :return: the shipping type, and the shipping price (optional)
        """
        ship_type, ship_costs, shipping_options = 'NOT_APPLICABLE', None, None
        try:
            shipping_text = self.webdriver.find_element(By.CSS_SELECTOR, '.boxedarticle--details--shipping') \
                .text.strip()
            # e.g. '+ Versand ab 5,49 €' OR 'Nur Abholung'
            if shipping_text == 'Nur Abholung':
                ship_type = 'PICKUP'
            elif shipping_text == 'Versand möglich':
                ship_type = 'SHIPPING'
            elif '€' in shipping_text:
                shipping_price_parts = shipping_text.split(' ')
                ship_type = 'SHIPPING'
                ship_costs = float(parse_decimal(shipping_price_parts[-2]))

                # extract shipping options
                # It is only possible the extract the cheapest shipping option,
                # as the other options are not shown
                shipping_option_mapping = {
                    "DHL_2": "5,49",
                    "Hermes_Päckchen": "4,50",
                    "Hermes_S": "4,95",
                    "DHL_5": "6,99",
                    "Hermes_M": "5,95",
                    "DHL_10": "9,49",
                    "DHL_31,5": "16,49",
                    "Hermes_L": "10,95",
                }
                for shipping_option, shipping_price in shipping_option_mapping.items():
                    if shipping_price in shipping_text:
                        shipping_options = [shipping_option]
                        break
        except NoSuchElementException:  # no pricing box -> no shipping given
            ship_type = 'NOT_APPLICABLE'

        return ship_type, ship_costs, shipping_options

    def extract_sell_directly_from_ad_page(self) -> bool | None:
        """
        Extracts the sell directly option from an ad page.

        :return: a boolean indicating whether the sell directly option is active (optional)
        """
        try:
            buy_now_is_active = self.webdriver.find_element(By.ID, 'j-buy-now').text == "Direkt kaufen"
            return buy_now_is_active
        except NoSuchElementException:
            return None

    def extract_contact_from_ad_page(self) -> dict[str, (str | None)]:
        """
        Processes the address part involving street (optional), zip code + city, and phone number (optional).

        :return: a dictionary containing the address parts with their corresponding values
        """
        contact:dict[str, (str | None)] = {}
        address_element = self.webdriver.find_element(By.CSS_SELECTOR, '#viewad-locality')
        address_text = address_element.text.strip()
        # format: e.g. (Beispiel Allee 42,) 12345 Bundesland - Stadt
        try:
            street_element = self.webdriver.find_element(By.XPATH, '//*[@id="street-address"]')
            street = street_element.text[:-2]  # trailing comma and whitespace
            contact['street'] = street
        except NoSuchElementException:
            print('No street given in the contact.')
        # construct remaining address
        address_halves = address_text.split(' - ')
        address_left_parts = address_halves[0].split(' ')  # zip code and region/city
        contact['zipcode'] = address_left_parts[0]

        contact_person_element = self.webdriver.find_element(By.CSS_SELECTOR, '#viewad-contact')
        name_element = contact_person_element.find_element(By.CLASS_NAME, 'iconlist-text')
        try:
            name = name_element.find_element(By.TAG_NAME, 'a').text
        except NoSuchElementException:  # edge case: name without link
            name = name_element.find_element(By.TAG_NAME, 'span').text
        contact['name'] = name

        if 'street' not in contact:
            contact['street'] = None
        try:  # phone number is unusual for non-professional sellers today
            phone_element = self.webdriver.find_element(By.CSS_SELECTOR, '#viewad-contact-phone')
            phone_number = phone_element.find_element(By.TAG_NAME, 'a').text
            contact['phone'] = ''.join(phone_number.replace('-', ' ').split(' ')).replace('+49(0)', '0')
        except NoSuchElementException:
            contact['phone'] = None  # phone seems to be a deprecated feature (for non-professional users)
        # also see 'https://themen.kleinanzeigen.de/hilfe/deine-anzeigen/Telefon/

        return contact

    def extract_own_ads_references(self) -> list[str]:
        """
        Extracts the references to all own ads.

        :return: the links to your ad pages
        """
        # navigate to your ads page
        self.webdriver.get('https://www.kleinanzeigen.de/m-meine-anzeigen.html')
        self.web_await(EC.url_contains('meine-anzeigen'), 15)
        pause(2000, 3000)

        # collect ad references:

        pagination_section = self.webdriver.find_element(By.CSS_SELECTOR, '.l-splitpage')\
            .find_element(By.XPATH, './/section[4]')
        # scroll down to load dynamically
        self.web_scroll_page_down()
        pause(2000, 3000)
        # detect multi-page
        try:
            pagination = pagination_section.find_element(By.XPATH, './/div/div[2]/div[2]/div')  # Pagination
        except NoSuchElementException:  # 0 ads - no pagination area
            print('There currently seem to be no ads on your profile!')
            return []

        n_buttons = len(pagination.find_element(By.XPATH, './/div[1]').find_elements(By.TAG_NAME, 'button'))
        multi_page:bool
        if n_buttons > 1:
            multi_page = True
            print('It seems like you have many ads!')
        else:
            multi_page = False
            print('It seems like all your ads fit on one overview page.')

        refs:list[str] = []
        while True:  # loop reference extraction until no more forward page
            # extract references
            list_section = self.webdriver.find_element(By.XPATH, '//*[@id="my-manageads-adlist"]')
            list_items = list_section.find_elements(By.CLASS_NAME, 'cardbox')
            refs += [li.find_element(By.XPATH, 'article/section/section[2]/h2/div/a').get_attribute('href') for li in list_items]

            if not multi_page:  # only one iteration for single-page overview
                break
            # check if last page
            nav_button = self.webdriver.find_elements(By.CSS_SELECTOR, 'button.jsx-2828608826')[-1]
            if nav_button.get_attribute('title') != 'Nächste':
                print('Last ad overview page explored.')
                break
            # navigate to next overview page
            nav_button.click()
            pause(2000, 3000)
            self.web_scroll_page_down()

        return refs
