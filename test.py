import time
import logging
import os
import re
import math
import sys
import pandas as pd
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
from CloudflareBypasser import CloudflareBypasser
from DrissionPage import ChromiumPage, ChromiumOptions
from bs4 import BeautifulSoup
from datetime import datetime  # Added import

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('cloudflare_bypass.log', mode='w')
    ]
)

def get_chromium_options(browser_path: str, arguments: list) -> ChromiumOptions:
    """
    Configures and returns Chromium options.
    """
    options = ChromiumOptions()
    # Set the correct browser executable path
    options.set_paths(browser_path=browser_path)
    # Set additional arguments
    for argument in arguments:
        options.set_argument(argument)
    return options

def get_lat_long_from_google_maps(driver, address):
    """
    Fetch latitude and longitude for a given address using Google Maps.
    """
    search_url = f"https://www.google.com/maps/search/{address.replace(' ', '+')}"
    driver.get(search_url)
    time.sleep(5)  # Allow time for Google Maps to load
    current_url = driver.url
    match = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", current_url)
    if match:
        latitude = float(match.group(1))
        longitude = float(match.group(2))
        return latitude, longitude
    else:
        logging.warning(f"Could not find coordinates for address: {address}")
        return None, None

def handle_cloudflare(driver):
    """
    Checks for and bypasses Cloudflare challenges.

    The function now increases wait times and attempts to bypass the challenge.
    It also logs each attempt.
    """
    logging.info("Checking for Cloudflare CAPTCHA or challenges.")
    
    # If no verification indicator text is found, assume it is already cleared.
    if "Just a moment" not in driver.html and "cf-browser-verification" not in driver.html:
        logging.info("No Cloudflare challenge detected.")
        return

    max_attempts = 60  # Increase the number of attempts if needed.
    for attempt in range(1, max_attempts + 1):
        logging.info(f"Attempt {attempt}: Verification page detected. Trying to bypass...")
        # Wait longer before each attempt (adjust the sleep time as needed)
        time.sleep(3)
        try:
            cf_bypasser = CloudflareBypasser(driver)
            cf_bypasser.bypass()  # Attempt to click the button
            logging.info("Verification bypass attempted.")
        except Exception as e:
            logging.error(f"Error clicking verification button on attempt {attempt}: {e}")
        
        # Check if the verification has been cleared
        if "Just a moment" not in driver.html and "cf-browser-verification" not in driver.html:
            logging.info("Cloudflare verification passed.")
            return
    else:
        logging.error("Exceeded maximum bypass attempts. Verification could not be bypassed.")

def scrape_property_urls(driver, url):
    """
    Visits a listing page URL and extracts property detail page URLs.
    Checks both standard listing card and gallery group formats.
    """
    driver.get(url)
    handle_cloudflare(driver)
    time.sleep(3)  # Wait for the page to load
    soup = BeautifulSoup(driver.html, 'html.parser')
    urls = []
    
    listing_cards = soup.select("div[data-automation-id='regular-listing-card']")
    for card in listing_cards:
        a_tag = card.select_one("a.listing-card-link")
        if a_tag:
            href = a_tag.get("href")
            if href and href not in urls:
                urls.append(href)
    
    gallery_cards = soup.select("div.gallery-group[da-id='lc-gallery-div']")
    for card in gallery_cards:
        a_tag = card.select_one("a")
        if a_tag:
            href = a_tag.get("href")
            if href and href not in urls:
                urls.append(href)
    return urls

def scrape_property_details(driver, url):
    """
    Visits a property detail page and scrapes required fields.
    """
    driver.get(url)
    handle_cloudflare(driver)
    time.sleep(2)  # Allow time for the page to load
    soup = BeautifulSoup(driver.html, 'html.parser')
    data = {}
    data['URL'] = url

    name_elem = soup.select_one("h1.title[data-automation-id='overview-property-title-txt']")
    data['name'] = name_elem.get_text(strip=True) if name_elem else None

    desc_elem = soup.select_one("div.description-block-root div.description.trimmed")
    if desc_elem:
        data['description'] = desc_elem.get_text(separator="\n", strip=True)
    else:
        alt_desc_elem = soup.select_one("div.description-block-root")
        if alt_desc_elem:
            for tag in alt_desc_elem.find_all(['h2', 'h3']):
                tag.decompose()
            data['description'] = alt_desc_elem.get_text(separator="\n", strip=True)
        else:
            data['description'] = None

    address_elem = soup.select_one("span.full-address__address")
    data['address'] = address_elem.get_text(strip=True) if address_elem else None

    price_elem = soup.select_one("h2.amount[data-automation-id='overview-price-txt']")
    data['price'] = price_elem.get_text(strip=True) if price_elem else None

    data['amenities'] = []
    amenity_elems = soup.select("div.property-amenities__row-item p.property-amenities__row-item__value")
    for amenity in amenity_elems:
        text = amenity.get_text(strip=True)
        if text:
            data['amenities'].append(text)

    data['characteristics'] = []
    try:
        see_more_btn = driver.ele('css:button.meta-table__button')
        if see_more_btn:
            see_more_btn.click()
            time.sleep(2)
            modal_loaded = False
            for _ in range(10):
                if "property-modal-body-wrapper" in driver.html:
                    modal_loaded = True
                    break
                time.sleep(0.5)
            if modal_loaded:
                soup = BeautifulSoup(driver.html, 'html.parser')
                modal_items = soup.select("div.property-modal-body-wrapper")
                for item in modal_items:
                    value = item.select_one("p.property-modal-body-value")
                    if value:
                        val = value.get_text(strip=True)
                        if val:
                            data['characteristics'].append(val)
            else:
                raise Exception("Modal content did not load.")
        else:
            raise Exception("See all details button not found.")
    except Exception as e:
        logging.error(f"⚠️ Failed to click 'See all details' or scrape modal: {e}")
        table = soup.select_one("table.row")
        if table:
            cells = table.select("td.meta-table__item-wrapper")
            for cell in cells:
                value_elem = cell.select_one("div.meta-table__item__wrapper__value")
                if value_elem:
                    value_text = value_elem.get_text(strip=True)
                    if value_text:
                        data['characteristics'].append(value_text)

    breadcrumb_items = soup.select("nav[aria-label='breadcrumb'] li")
    data['property_type'] = breadcrumb_items[1].get_text(strip=True) if len(breadcrumb_items) >= 2 else None
    data['transaction_type'] = breadcrumb_items[-1].get_text(strip=True) if breadcrumb_items else None

    features_section = soup.find("div", {"data-automation-id": "overview-property-meta-data-section"})
    data['features'] = {}
    if features_section:
        for feature in features_section.find_all("div", class_="amenity"):
            icon_img = feature.find("img")
            feature_type = icon_img.get("alt", "").strip() if icon_img else None
            feature_text_elem = feature.find("h4", class_="amenity__text")
            feature_value = feature_text_elem.get_text(separator=" ", strip=True) if feature_text_elem else None
            if feature_type and feature_value:
                data['features'][feature_type] = feature_value
    else:
        data['features'] = None

    if data.get('address'):
        lat, lon = get_lat_long_from_google_maps(driver, data['address'])
        data['latitude'] = lat
        data['longitude'] = lon
    else:
        data['latitude'] = None
        data['longitude'] = None

    logging.info(data)
    return data

def save_to_excel(all_data, base_url, start_page, end_page):
    """
    Saves the scraped data into an Excel file.
    The file is saved in the "output" folder (for GitHub Actions artifact collection).
    """
    df = pd.DataFrame(all_data)
    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename_base = re.sub(r'[^\w\-]', '_', base_url)
    if len(filename_base) > 100:
        filename_base = filename_base[:100]
    filename = f"{filename_base}_pages_{start_page}_{end_page}_{now_str}.xlsx"
    output_dir = "output"
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    filepath = os.path.join(output_dir, filename)
    df.to_excel(filepath, index=False)
    logging.info(f"Data saved to {filepath}")

def build_page_url(base_url, page):
    """
    Builds the page URL based on the base URL and page number.
    """
    if "{page}" in base_url:
        return base_url.format(page=page)
    
    parsed = urlparse(base_url)
    path = parsed.path
    path_stripped = path.rstrip('/')
    path_segments = path_stripped.split('/')
    
    if path_segments and path_segments[-1].isdigit():
        new_path = '/'.join(path_segments[:-1]) if page == 1 else '/'.join(path_segments[:-1] + [str(page)])
    else:
        new_path = path_stripped if page == 1 else path_stripped + '/' + str(page)
    
    new_url = urlunparse((parsed.scheme, parsed.netloc, new_path, parsed.params, parsed.query, parsed.fragment))
    return new_url

def collect_all_property_urls(driver, base_url, start_page, end_page):
    """
    Collects property URLs from multiple listing pages.
    """
    all_urls = []
    for page in range(start_page, end_page + 1):
        page_url = build_page_url(base_url, page)
        logging.info(f"Processing listing page: {page_url}")
        property_urls = scrape_property_urls(driver, page_url)
        logging.info(f"Found {len(property_urls)} property URLs on page {page}")
        all_urls.extend(property_urls)
    unique_urls = list(set(all_urls))
    logging.info(f"Total unique property URLs collected: {len(unique_urls)}")
    return unique_urls

def scrape_all_property_details(driver, property_urls):
    """
    Scrapes property details for each collected property URL.
    """
    all_data = []
    for prop_url in property_urls:
        logging.info(f"Scraping detail page: {prop_url}")
        property_data = scrape_property_details(driver, prop_url)
        all_data.append(property_data)
        time.sleep(2)
    return all_data

def main():
    isHeadless = os.getenv('HEADLESS', 'false').lower() == 'true'
    
    # Determine browser path from environment or defaults based on OS.
    if os.getenv('BROWSER_PATH'):
        browser_path = os.getenv('BROWSER_PATH')
    else:
        if sys.platform.startswith('win'):
            browser_path = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
        elif sys.platform.startswith('linux'):
            browser_path = "/usr/bin/chromium-browser"
        else:
            logging.error("Unsupported OS.")
            return

    if not os.path.exists(browser_path):
        logging.error(f"Browser executable not found at: {browser_path}")
        return

    # Start virtual display for headless systems if required.
    if isHeadless:
        from pyvirtualdisplay import Display
        display = Display(visible=0, size=(1920, 1080))
        display.start()

    # Define arguments with additional flags to help bypass detection.
    arguments = [
        "--no-first-run",
        "--force-color-profile=srgb",
        "--metrics-recording-only",
        "--disable-gpu",
        "--accept-lang=en-US",
        "--remote-debugging-port=9222",
        "--headless=new",
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.0.0 Safari/537.36'
    ]
    
    options = get_chromium_options(browser_path, arguments)
    driver = ChromiumPage(addr_or_opts=options)

    try:
        base_urls = [
            "https://www.propertyguru.com.my/property-for-sale?listingType=sale&isCommercial=false&maxPrice=300000&propertyTypeGroup=N&propertyTypeCode=APT&propertyTypeCode=CONDO&propertyTypeCode_DUPLX&propertyTypeCode=FLAT&propertyTypeCode=PENT&propertyTypeCode=SRES&propertyTypeCode=STDIO&propertyTypeCode=TOWNC&search=true&locale=en"
        ]
        start_page = 1
        end_page = 1  # Adjust pages as necessary
        for base_url in base_urls:
            logging.info(f"Starting to scrape listing URLs from: {base_url}")
            property_urls = collect_all_property_urls(driver, base_url, start_page, end_page)
            if not property_urls:
                logging.warning("No property URLs found. Skipping detail scraping.")
                continue
            logging.info("Starting to scrape property details for collected URLs.")
            all_data = scrape_all_property_details(driver, property_urls)
            save_to_excel(all_data, base_url, start_page, end_page)
    except Exception as e:
        logging.error(f"An error occurred: {e}")
    finally:
        driver.quit()
        if isHeadless:
            display.stop()

if __name__ == '__main__':
    main()
