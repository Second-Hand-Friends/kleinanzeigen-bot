from . import KleinanzeigenBot
from selenium.webdriver.common.by import By

# TODO go to homepage, enter ad ID, and confirm to open ad site
# TODO extract all content from ad site


def navigate_to_ad_page(ad_id: int, bot: KleinanzeigenBot):
    """
    Navigates to an ad page specified with an ad ID.

    :param ad_id: the ad ID, an integer number with 10 digits
    :param bot: a bot object
    """
    # goto homepage and enter the ad ID into the search bar
    bot.web_open('https://www.ebay-kleinanzeigen.de')
    bot.web_input(By.XPATH, '//*[@id="site-search-query"]', str(ad_id))
    bot.web_click(By.XPATH, '//*[@id="site-search-submit"]')

