import asyncio
import pandas as pd
import os
import base64
import hashlib
import sqlite3
import json
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import OpenAI
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
load_dotenv()
client = OpenAI(api_key=os.getenv("AI_API_KEY"))

INPUT_FILE = "propnumbers.xlsx" 
DB_FILE = "igr_data.db"
ALLOWED_DTYPES = ["36-अ-लिव्ह अॅड लायसन्सेस", "खरेदीखत", "करारनामा", "लिजडीड"]

# =========================================================
# DATABASE HELPERS
# =========================================================

def init_db():
    """Initializes the SQLite database with full Index II column support."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS property_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            input_dist TEXT, input_tahsil TEXT, input_village TEXT,
            input_year TEXT, input_prop_no TEXT,
            DocNo TEXT, DName TEXT, RDate TEXT, SROName TEXT,
            Seller_Name TEXT, Purchaser_Name TEXT, Property_Description TEXT,
            SROCode TEXT, Status TEXT,
            vilekha_type TEXT,        -- (1) विलेखाचा प्रकार
            consideration TEXT,       -- (2) मोबदला
            market_value TEXT,        -- (3) बाजारभाव
            bhmapan TEXT,             -- (4) भू-मापन
            area TEXT,                -- (5) क्षेत्रफळ
            aakarni TEXT,             -- (6) आकारणी
            seller_details TEXT,      -- (7) देणा-या पक्षकाराचे नाव
            purchaser_details TEXT,   -- (8) घेणा-या पक्षकाराचे नाव
            doc_date TEXT,            -- (9) दस्तऐवज दिनांक
            reg_date_inner TEXT,      -- (10) नोंदणी दिनांक
            serial_no TEXT,           -- (11) अनुक्रमांक
            stamp_duty TEXT,          -- (12) मुद्रांक शुल्क
            reg_fee TEXT,             -- (13) नोंदणी शुल्क
            remarks TEXT,             -- (14) शेरा
            valuation_details TEXT,   -- मुल्यांकनासाठी तपशील
            article_selected TEXT,    -- निवडलेला अनुच्छेद
            signature TEXT UNIQUE
        )
    """)
    conn.commit()
    conn.close()
    print(">>> Database initialized with all Index II columns.")

def get_processed_signatures():
    """Loads all existing signatures from the DB to skip duplicates."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT signature FROM property_records")
    hashes = {row[0] for row in cursor.fetchall()}
    conn.close()
    return hashes

def save_to_db(data_dict):
    """Inserts a single record into the database."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    columns = ', '.join(data_dict.keys())
    placeholders = ':' + ', :'.join(data_dict.keys())
    sql = f"INSERT OR IGNORE INTO property_records ({columns}) VALUES ({placeholders})"
    try:
        cursor.execute(sql, data_dict)
        conn.commit()
    except Exception as e:
        print(f"   [DB ERROR] {e}")
    finally:
        conn.close()

# =========================================================
# SCRAPING HELPERS
# =========================================================

def get_row_signature(doc_no, prop_desc):
    combined = f"{str(doc_no).strip()}_{str(prop_desc).strip()}"
    return hashlib.sha256(combined.encode('utf-8')).hexdigest()

def extract_data_from_detail_page(html):
    soup = BeautifulSoup(html, 'html.parser')
    table = soup.find('table', class_='tblmargin')
    inner_data = {}
    if not table: return inner_data
    
    # Matching exact Marathi labels from Index II image
    mapping = {
        "(1)": "vilekha_type", "(2)": "consideration", "(3)": "market_value",
        "(4)": "bhmapan", "(5)": "area", "(6)": "aakarni",
        "(7)": "seller_details", "(8)": "purchaser_details", "(9)": "doc_date",
        "(10)": "reg_date_inner", "(11)": "serial_no", "(12)": "stamp_duty",
        "(13)": "reg_fee", "(14)": "remarks", "मुल्यांकनासाठी": "valuation_details",
        "निवडलेला": "article_selected"
    }
    
    for row in table.find_all('tr'):
        cells = row.find_all('td')
        if len(cells) >= 2:
            raw_label = cells[0].get_text(strip=True)
            value = cells[1].get_text(separator=' ', strip=True)
            for key, db_field in mapping.items():
                if key in raw_label:
                    inner_data[db_field] = value
    return inner_data

async def wait_for_loading(page):
    try:
        await page.wait_for_selector("#UpdateProgress1", state="visible", timeout=800)
        await page.wait_for_selector("#UpdateProgress1", state="hidden", timeout=30000)
    except: pass

async def solve_captcha_with_ai(page):
    try:
        captcha_el = await page.wait_for_selector("#imgCaptcha_new", timeout=15000)
        await asyncio.sleep(1) 
        img_bytes = await captcha_el.screenshot()
        base64_image = base64.b64encode(img_bytes).decode('utf-8')
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "read the case sensitive text in this image and respond with only the text no spaces"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_image}"}}
            ]}],
            max_tokens=10,
        )
        return response.choices[0].message.content.strip().replace(" ", "")
    except: return None

async def handle_postback_by_text(page, selector, label_name, text_value, retries=3):
    for attempt in range(retries):
        try:
            print(f"   [SYNC] Selecting {label_name}: {text_value}")
            await page.wait_for_selector(selector, state="visible", timeout=30000)
            option_value = await page.evaluate(f"""() => {{
                const sel = document.querySelector('{selector}');
                for (let opt of sel.options) {{
                    if (opt.text.trim() === '{str(text_value).strip()}') return opt.value;
                }}
                return null;
            }}""")
            if not option_value: raise Exception(f"{text_value} not found")
            await page.select_option(selector, value=option_value)
            await wait_for_loading(page)
            return 
        except Exception as e:
            if attempt == retries - 1: raise e
            await asyncio.sleep(2)

# =========================================================
# MAIN SCRAPE LOGIC
# =========================================================

async def scrape_entry(project, year, district, tahsil, village):
    # Duplicate Safety: Load current DB hashes to skip already processed rows
    existing_hashes = get_processed_signatures()

    for attempt in range(1, 3):
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                    headless=True, 
                    channel="chromium-headless-shell",
                    args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
                )
            context = await browser.new_context(no_viewport=True)
            page = await context.new_page()
            
            # PERFORMANCE: Abort CSS/Fonts loading
            await page.route("**/*.{woff,woff2,css}", lambda route: route.abort())

            try:
                await page.goto("https://freesearchigrservice.maharashtra.gov.in/", timeout=90000)
                try: await page.locator("a.btnclose.btn-danger").click(timeout=5000)
                except: pass

                await page.click("#btnOtherdistrictSearch")
                await handle_postback_by_text(page, "#ddlFromYear1", "Year", year)
                await handle_postback_by_text(page, "#ddlDistrict1", "District", district) 
                await handle_postback_by_text(page, "#ddltahsil", "Tahsil", tahsil)      
                await handle_postback_by_text(page, "#ddlvillage", "Village", village)
                
                await page.fill("#txtAttributeValue1", str(project))
                captcha_val = await solve_captcha_with_ai(page)
                if not captcha_val: raise Exception("Captcha Failure")
                await page.fill("#txtImg1", captcha_val)
                await page.click("#btnSearch_RestMaha")
                
                # TIMEOUT: 30 Seconds as requested
                try:
                    await page.wait_for_selector("#RegistrationGrid td", timeout=30000)
                except:
                    await browser.close()
                    if attempt == 2: return
                    continue

                current_page = 1
                while True:
                    await wait_for_loading(page)
                    rows_locator = page.locator("//table[@id='RegistrationGrid']//tr[td[input[@value='IndexII']]]")
                    total_rows = await rows_locator.count()
                    
                    for i in range(total_rows):
                        row = rows_locator.nth(i)
                        grid_data = await row.evaluate("r => Array.from(r.querySelectorAll('td')).slice(0, 9).map(c => c.innerText.trim())")
                        
                        if not any(dtype in grid_data[1] for dtype in ALLOWED_DTYPES): continue
                        
                        h = get_row_signature(grid_data[0], grid_data[6])
                        if h in existing_hashes: continue

                        try:
                            async with context.expect_page(timeout=45000) as popup_info:
                                await row.locator("input[value='IndexII']").click()
                            idx_page = await popup_info.value
                            await idx_page.wait_for_selector(".tblmargin", timeout=30000)
                            
                            # Prepare ALL columns for Database
                            details = extract_data_from_detail_page(await idx_page.content())
                            db_record = {
                                "input_dist": district, "input_tahsil": tahsil, "input_village": village,
                                "input_year": year, "input_prop_no": project,
                                "DocNo": grid_data[0], "DName": grid_data[1], "RDate": grid_data[2],
                                "SROName": grid_data[3], "Seller_Name": grid_data[4], "Purchaser_Name": grid_data[5],
                                "Property_Description": grid_data[6], "SROCode": grid_data[7], "Status": grid_data[8],
                                "signature": h,
                                **details # Expands all 16 pop-up fields automatically
                            }
                            
                            save_to_db(db_record)
                            existing_hashes.add(h)
                            await idx_page.close()
                        except: continue

                    next_btn = page.locator(f"//table[@id='RegistrationGrid']//a[text()='{current_page + 1}']")
                    if await next_btn.count() > 0:
                        await next_btn.click()
                        current_page += 1
                        await wait_for_loading(page)
                    else:
                        await browser.close(); return

            except Exception as e:
                print(f"[ERROR] {e}")
                await browser.close()
                if attempt == 2: return
                await asyncio.sleep(5)

# =========================================================
# RUNNER
# =========================================================

if __name__ == "__main__":
    init_db() 
    
    df = pd.read_excel(INPUT_FILE)
    df.columns = df.columns.str.strip() # Remove hidden spaces from headers
    
    prop_list = df['property_no'].dropna().apply(lambda x: str(int(float(x)))).tolist()
    year_list = df['year'].dropna().apply(lambda x: str(int(float(x)))).tolist()
    dist_list = df['district'].dropna().astype(str).tolist()

    for dist in dist_list:
        tahsil_col = f"{dist.strip()} tahsil"
        if tahsil_col in df.columns:
            for tahsil in df[tahsil_col].dropna().astype(str).tolist():
                village_col = f"{tahsil.strip()} village"
                if village_col in df.columns:
                    for village in df[village_col].dropna().astype(str).tolist():
                        for yr in year_list:
                            for prop in prop_list:
                                print(f"\n>>> Task: {dist} > {tahsil} > {village} | Year: {yr} | Prop: {prop}")
                                asyncio.run(scrape_entry(prop, yr, dist, tahsil, village))