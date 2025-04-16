import time
import logging
import os
import re
import math
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
    
    :param browser_path: Path to the Chromium browser executable.
    :param arguments: List of arguments for the Chromium browser.
    :return: Configured ChromiumOptions instance.
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

    # Extract the current URL after page load
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
    """
    logging.info("Checking for Cloudflare CAPTCHA or challenges.")
    try:
        if "Just a moment" in driver.html or 'cf-browser-verification' in driver.html:
            logging.info("Cloudflare protection detected. Attempting to bypass.")
            cf_bypasser = CloudflareBypasser(driver)
            cf_bypasser.bypass()
            logging.info("Cloudflare bypassed.")
    except Exception as e:
        logging.error(f"Error while checking or bypassing Cloudflare: {e}")

def scrape_property_urls(driver, url):
    """
    Visit a listing page URL and extract property detail page URLs.
    This function now checks both the standard listing card format and the alternative
    gallery group format.
    """
    driver.get(url)
    handle_cloudflare(driver)
    time.sleep(3)  # Wait for the page to load
    soup = BeautifulSoup(driver.html, 'html.parser')
    urls = []
    
    # Look for the listing cards using the provided data-automation-id
    listing_cards = soup.select("div[data-automation-id='regular-listing-card']")
    for card in listing_cards:
        a_tag = card.select_one("a.listing-card-link")
        if a_tag:
            href = a_tag.get("href")
            if href and href not in urls:
                urls.append(href)
    
    # Look for alternative property cards in the gallery group format
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
    Visit a property detail page and scrape required fields.
    """
    driver.get(url)
    handle_cloudflare(driver)
    time.sleep(2)  # Allow time for the page to load

    soup = BeautifulSoup(driver.html, 'html.parser')
    data = {}

    data['URL'] = url

    # Property name
    name_elem = soup.select_one("h1.title[data-automation-id='overview-property-title-txt']")
    data['name'] = name_elem.get_text(strip=True) if name_elem else None

    # Description
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

    # Address
    address_elem = soup.select_one("span.full-address__address")
    data['address'] = address_elem.get_text(strip=True) if address_elem else None

    # Price
    price_elem = soup.select_one("h2.amount[data-automation-id='overview-price-txt']")
    data['price'] = price_elem.get_text(strip=True) if price_elem else None

    # Amenities
    data['amenities'] = []
    amenity_elems = soup.select("div.property-amenities__row-item p.property-amenities__row-item__value")
    for amenity in amenity_elems:
        text = amenity.get_text(strip=True)
        if text:
            data['amenities'].append(text)

    # Characteristics (with modal click)
    data['characteristics'] = []
    try:
        # Click the button to open the modal
        see_more_btn = driver.ele('css:button.meta-table__button')
        if see_more_btn:
            see_more_btn.click()
            time.sleep(2)  # Allow modal to render

            # Wait for modal content to load
            modal_loaded = False
            for _ in range(10):
                if 'property-modal-body-wrapper' in driver.html:
                    modal_loaded = True
                    break
                time.sleep(0.5)

            if modal_loaded:
                soup = BeautifulSoup(driver.html, 'html.parser')
                modal_items = soup.select("div.property-modal-body-wrapper")
                for item in modal_items:
                    # Updated selector from div to p, reflecting the structure of your HTML snippet.
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
        print(f"⚠️ Failed to click 'See all details' or scrape modal. Falling back. Reason: {e}")
        table = soup.select_one("table.row")
        if table:
            cells = table.select("td.meta-table__item-wrapper")
            for cell in cells:
                value_elem = cell.select_one("div.meta-table__item__wrapper__value")
                if value_elem:
                    value_text = value_elem.get_text(strip=True)
                    if value_text:
                        data['characteristics'].append(value_text)

    # Breadcrumbs
    breadcrumb_items = soup.select("nav[aria-label='breadcrumb'] li")
    data['property_type'] = breadcrumb_items[1].get_text(strip=True) if len(breadcrumb_items) >= 2 else None
    data['transaction_type'] = breadcrumb_items[-1].get_text(strip=True) if breadcrumb_items else None

    # Features
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

    # Lat/Long
    if data.get('address'):
        lat, lon = get_lat_long_from_google_maps(driver, data['address'])
        data['latitude'] = lat
        data['longitude'] = lon
    else:
        data['latitude'] = None
        data['longitude'] = None

    print(data)
    return data

def save_to_excel(all_data, base_url, start_page, end_page):
    """
    Saves the scraped data into an Excel file with a valid Windows filename.
    The file is saved in the "output" folder for GitHub Actions.
    """
    df = pd.DataFrame(all_data)
    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Sanitize filename: allow only alphanumerics, underscores, hyphens
    filename_base = re.sub(r'[^\w\-]', '_', base_url)
    if len(filename_base) > 100:
        filename_base = filename_base[:100]

    filename = f"{filename_base}_pages_{start_page}_{end_page}_{now_str}.xlsx"

    # Ensure the "output" directory exists
    output_dir = "output"
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    filepath = os.path.join(output_dir, filename)
    df.to_excel(filepath, index=False)
    logging.info(f"Data saved to {filepath}")

def build_page_url(base_url, page):
    """
    Dynamically builds the page URL based on the user provided base URL.
    
    - If the base URL contains a "{page}" placeholder, it will be replaced with the page number.
    - Otherwise, if the path already ends with a number, that number will be removed (for page 1)
      or replaced (for subsequent pages).
    - If no page number exists in the path, then for page 1 the base URL is returned and for
      pages greater than 1, the page number is appended to the path.
    """
    # Use the placeholder if available
    if "{page}" in base_url:
        return base_url.format(page=page)
    
    parsed = urlparse(base_url)
    path = parsed.path
    # Remove trailing slash for uniformity
    path_stripped = path.rstrip('/')
    path_segments = path_stripped.split('/')
    
    if path_segments and path_segments[-1].isdigit():
        # The URL already has a numeric segment in the path
        if page == 1:
            new_path = '/'.join(path_segments[:-1])
        else:
            new_path = '/'.join(path_segments[:-1] + [str(page)])
    else:
        # No numeric segment exists in the base URL path
        if page == 1:
            new_path = path_stripped
        else:
            new_path = path_stripped + '/' + str(page)
    
    # Reconstruct URL with the new path and original query & fragment
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
    # Remove duplicates
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
        time.sleep(2)  # Delay between requests
    return all_data

def main():
    # Use default Edge Browser path (modify if necessary)
    isHeadless = os.getenv('HEADLESS', 'false').lower() == 'true'
    browser_path = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"

    if not os.path.exists(browser_path):
        logging.error(f"Browser executable not found at: {browser_path}")
        return

    if isHeadless:
        from pyvirtualdisplay import Display
        display = Display(visible=0, size=(1920, 1080))
        display.start()

    arguments = [
        "--no-first-run",
        "--force-color-profile=srgb",
        "--metrics-recording-only",
        "--disable-gpu",
        "--accept-lang=en-US",
    ]

    options = get_chromium_options(browser_path, arguments)
    driver = ChromiumPage(addr_or_opts=options)

    try:
        base_urls = [
            "https://www.propertyguru.com.my/property-for-sale?listingType=sale&isCommercial=false&maxPrice=300000&propertyTypeGroup=N&propertyTypeCode=APT&propertyTypeCode=CONDO&propertyTypeCode_DUPLX&propertyTypeCode=FLAT&propertyTypeCode=PENT&propertyTypeCode=SRES&propertyTypeCode=STDIO&propertyTypeCode=TOWNC&search=true&locale=en"
        ]
        # Adjust start_page and end_page as needed
        start_page = 1
        end_page = 1  # Change as needed
        for base_url in base_urls:
            logging.info(f"Starting to scrape listing URLs from: {base_url}")
            property_urls = collect_all_property_urls(driver, base_url, start_page, end_page)
            
            if not property_urls:
                logging.warning("No property URLs found. Skipping detail scraping.")
                continue
            
            logging.info("Starting to scrape property details for collected URLs.")
            all_data = scrape_all_property_details(driver, property_urls)
            # Pass start_page and end_page to save_to_excel for filename generation and saving in the "output" folder
            save_to_excel(all_data, base_url, start_page, end_page)
    except Exception as e:
        logging.error(f"An error occurred: {e}")
    finally:
        driver.quit()
        if isHeadless:
            display.stop()

if __name__ == '__main__':
    main()
