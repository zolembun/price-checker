import streamlit as st
import pandas as pd
import google.generativeai as genai
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import urllib.parse
from datetime import datetime
import re

# ตั้งค่าหน้าเว็บ
st.set_page_config(page_title="ระบบเช็คราคา & คู่แข่ง", page_icon="💰", layout="wide")

# =========================================================
# 🔐 ส่วนระบบ Login (คงเดิม)
# =========================================================
def check_password():
    """Returns `True` if the user had the correct password."""
    def password_entered():
        if st.session_state["password"] == st.secrets["app_password"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.header("🔒 กรุณาเข้าสู่ระบบ")
        st.text_input("ใส่รหัสผ่านเพื่อใช้งาน", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        st.header("🔒 กรุณาเข้าสู่ระบบ")
        st.text_input("ใส่รหัสผ่านเพื่อใช้งาน", type="password", on_change=password_entered, key="password")
        st.error("❌ รหัสผ่านไม่ถูกต้อง")
        return False
    else:
        return True

if check_password():
    
    # CSS ตกแต่ง: เน้นราคาทุนให้เด่น
    st.markdown("""
    <style>
        .cost-box { 
            background-color: #ffebee; 
            padding: 15px; 
            border-radius: 10px; 
            border: 2px solid #ef5350;
            text-align: center;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        .selling-box { 
            background-color: #e8f5e9; 
            padding: 15px; 
            border-radius: 10px; 
            border: 2px solid #66bb6a;
            text-align: center;
        }
        .info-box {
            background-color: #f8f9fa;
            padding: 15px;
            border-radius: 10px;
            border: 1px solid #ddd;
        }
        .price-label { font-size: 16px; color: #555; margin-bottom: 5px; font-weight: bold;}
        .price-value-cost { font-size: 48px !important; font-weight: 900; color: #c62828; line-height: 1.2;}
        .price-value-sell { font-size: 48px !important; font-weight: 900; color: #2e7d32; line-height: 1.2;}
        div.stButton > button { width: 100%; border-radius: 8px; font-weight: bold;}
        .search-badge {
            background-color: #e3f2fd; color: #1565c0; padding: 4px 8px; 
            border-radius: 4px; font-size: 0.85em; font-weight: bold;
        }
    </style>
    """, unsafe_allow_html=True)

    # 1. เชื่อมต่อ Services
    @st.cache_resource
    def init_services():
        service_account_info = st.secrets["gcp_service_account"]
        gemini_key = st.secrets["gemini_api_key"]
        
        scopes = ['https://www.googleapis.com/auth/spreadsheets.readonly', 
                  'https://www.googleapis.com/auth/drive.metadata.readonly']
        creds = Credentials.from_service_account_info(service_account_info, scopes=scopes)
        
        sheets_service = build('sheets', 'v4', credentials=creds)
        drive_service = build('drive', 'v3', credentials=creds)
        genai.configure(api_key=gemini_key)
        
        return sheets_service, drive_service

    # 2. โหลดข้อมูล
    @st.cache_data(ttl=600)
    def load_data(_sheets_service, _drive_service, spreadsheet_url):
        try:
            spreadsheet_id = spreadsheet_url.split('/d/')[1].split('/')[0]
            
            file_meta = _drive_service.files().get(fileId=spreadsheet_id, fields="name, modifiedTime").execute()
            file_name = file_meta.get('name')
            mod_time_str = file_meta.get('modifiedTime')
            dt = datetime.strptime(mod_time_str, "%Y-%m-%dT%H:%M:%S.%fZ")
            last_update = dt.strftime("%d/%m/%Y %H:%M น.")
            
            sheet = _sheets_service.spreadsheets()
            result = sheet.values().get(spreadsheetId=spreadsheet_id, range="A:H").execute()
            values = result.get('values', [])
            
            if not values: return None, None, None
            
            df = pd.DataFrame(values[1:], columns=values[0])
            
            # แปลงตัวเลข
            cols_to_numeric = ['ราคาทุนต่อหน่วย', 'จำนวนสต้อก']
            for col in cols_to_numeric:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                
            return df, file_name, last_update
            
        except Exception as e:
            st.error(f"เกิดข้อผิดพลาดในการโหลดข้อมูล: {str(e)}")
            return None, None, None

    # ฟังก์ชันช่วยทำความสะอาดข้อความ (สำหรับการค้นหาแบบประหยัด Token)
    def clean_text(text):
        if not isinstance(text, str): text = str(text)
        return re.sub(r'[^a-zA-Z0-9ก-๙]', '', text).lower()

    # 3. ส่วนแสดงผลหลัก
    st.title("🔎 เช็คราคาทุน & คู่แข่ง")

    try:
        sheets_svc, drive_svc = init_services()
        SHEET_URL = st.secrets["sheet_url"]
        
        with st.spinner('กำลังเชื่อมต่อฐานข้อมูล...'):
            df, file_name, last_update = load_data(sheets_svc, drive_svc, SHEET_URL)

        if df is not None:
            st.caption(f"📂 ฐานข้อมูล: {file_name} | 🕒 อัปเดตล่าสุด: {last_update}")
            st.divider()

            # Input Search
            query = st.text_input("พิมพ์รหัสสินค้า หรือ ชื่อรุ่น", placeholder="เช่น rt20, parsr5lae (ไม่ต้องใส่ขีดก็ได้)")
            
            if query:
                match_index = -1
                found_by = ""
                
                # --- LOGIC การค้นหาแบบประหยัด Token (Smart Search) ---
                query_clean = clean_text(query)
                sku_clean_series = df['รหัสสินค้า'].astype(str).apply(clean_text)
                desc_clean_series = df['รายละเอียดสินค้า'].astype(str).apply(clean_text)
                
                # ด่าน 1: ค้นหารหัสสินค้า (Exact match with clean text)
                sku_matches = df[sku_clean_series.str.contains(query_clean, na=False)]
                
                if not sku_matches.empty:
                    match_index = sku_matches.index[0]
                    found_by = "⚡ เจอรหัสสินค้า"
                else:
                    # ด่าน 2: ค้นหารายละเอียด
                    desc_matches = df[desc_clean_series.str.contains(query_clean, na=False)]
                    if not desc_matches.empty:
                        match_index = desc_matches.index[0]
                        found_by = "🔎 เจอในรายละเอียด"
                    else:
                        # ด่าน 3: AI Fallback (ส่งไปน้อยๆ ประหยัด Token)
                        keywords = list(filter(None, re.split(r'[^a-zA-Z0-9]', query)))
                        if not keywords: keywords = [query]
                        candidates = df[df.astype(str).apply(lambda x: any(k.lower() in x.lower() for k in keywords), axis=1)]
                        
                        if candidates.empty: search_pool = df.sample(min(len(df), 15))
                        else: search_pool = candidates.head(30)
                        
                        product_list_str = search_pool[['รหัสสินค้า', 'รายละเอียดสินค้า']].to_string(index=True)
                        model = genai.GenerativeModel('gemini-1.5-flash')
                        prompt = f"คำค้นหา: '{query}'\nหา Index สินค้าที่ตรงที่สุดจาก:\n{product_list_str}\nตอบแค่ตัวเลข Index. ถ้าไม่มีตอบ -1"
                        
                        with st.spinner('กำลังให้ AI ช่วยแกะรหัส...'):
                            try:
                                response = model.generate_content(prompt)
                                match_index = int(response.text.strip())
                                found_by = "🤖 AI ค้นพบ"
                            except:
                                match_index = -1

                # -----------------------------------------------------------
                # แสดงผล (ปรับปรุงตาม Requirement)
                # -----------------------------------------------------------
                if match_index != -1 and match_index in df.index:
                    item = df.loc[match_index]
                    cost_price = item.get('ราคาทุนต่อหน่วย', 0)
                    stock = item.get('จำนวนสต้อก', 0)
                    model_id = item.get('รหัสสินค้า', '-')
                    product_name = item.get('รายละเอียดสินค้า', '-')
                    brand = item.get('ยี่ห้อ', '-')

                    st.success(f"{found_by}: {product_name}")
                    
                    # 1. ราคาแนะนำ (12% ตามที่ขอ)
                    target_margin = 12
                    selling_price = cost_price * (1 + (target_margin/100))
                    profit = selling_price - cost_price

                    c1, c2, c3 = st.columns([1.3, 1.3, 1])
                    with c1:
                        st.markdown(f"""
                        <div class="cost-box">
                            <div class="price-label">🔴 ราคาทุน (COST)</div>
                            <div class="price-value-cost">{cost_price:,.0f}</div>
                        </div>
                        """, unsafe_allow_html=True)
                    with c2:
                        st.markdown(f"""
                        <div class="selling-box">
                            <div class="price-label">🟢 ราคาขายแนะนำ (+{target_margin}%)</div>
                            <div class="price-value-sell">{selling_price:,.0f}</div>
                            <div style="color: #1b5e20; font-weight:bold;">กำไร {profit:,.0f} บาท</div>
                        </div>
                        """, unsafe_allow_html=True)
                    with c3:
                        st.markdown(f"""
                        <div class="info-box">
                            <b>🆔 รหัส:</b> {model_id}<br>
                            <b>📦 สต้อก:</b> {stock} ชิ้น<br>
                            <b>🏷️ ยี่ห้อ:</b> {brand}
                        </div>
                        """, unsafe_allow_html=True)
                        # --- [เพิ่มใหม่] ปุ่มค้นหา Google อัตโนมัติ ---
                        st.write("") # เว้นบรรทัดนิดนึง
                        google_q = urllib.parse.quote(product_name) # ใช้ชื่อสินค้าค้นหา
                        st.link_button("🌐 ค้นหารูป/ข้อมูลใน Google", f"https://www.google.com/search?q={google_q}", use_container_width=True)

                    st.divider()
                    
                    # 2. ตาราง Margin (ตามสเต็ปที่ขอ: 3, 5, 8, 10, 12, 15, 18, 25, 30)
                    with st.expander("ดูตารางราคาขายอื่นๆ (3% - 30%)", expanded=True):
                        margins = [3, 5, 8, 10, 12, 15, 18, 25, 30]
                        price_data = []
                        for m in margins:
                            sp = cost_price * (1 + (m/100))
                            price_data.append({
                                "กำไร %": f"{m}%", 
                                "ราคาขาย": f"{sp:,.0f}", 
                                "กำไร (บาท)": f"{sp-cost_price:,.0f}"
                            })
                        st.dataframe(pd.DataFrame(price_data), hide_index=True, use_container_width=True)

                    # 3. Hot Search (Logic เดิมของคุณเป๊ะๆ)
                    st.divider()
                    st.subheader("🛒 เช็คราคาคู่แข่ง (Hot Search)")

                    # ตัดภาษาไทยออก เอาเฉพาะรหัส/อังกฤษ
                    default_search_code = re.sub(r'[\u0E00-\u0E7F]', '', str(model_id)).strip('-').strip()
                    
                    # ช่องให้แก้ไขคำค้นหา
                    final_search_keyword = st.text_input("🎯 คำค้นหาสำหรับเช็คราคา (แก้ไขได้):", value=default_search_code)
                    
                    if final_search_keyword:
                        encoded_name = urllib.parse.quote(final_search_keyword.strip())
                        
                        # รายชื่อร้านเดิมของคุณ
                        stores = [
                            {"name": "HomePro", "url": f"https://www.homepro.co.th/search?q={encoded_name}"},
                            {"name": "PowerBuy", "url": f"https://www.powerbuy.co.th/th/search/{encoded_name}"},
                            {"name": "ThaiWatsadu", "url": f"https://www.thaiwatsadu.com/th/search/{encoded_name}"},
                            {"name": "Big C", "url": f"https://www.bigc.co.th/search?q={encoded_name}"},
                            {"name": "Global", "url": f"https://globalhouse.co.th/search?keyword={encoded_name}"},
                            {"name": "Makro", "url": f"https://www.makro.pro/c/search?q={encoded_name}"},
                            {"name": "Dohome", "url": f"https://www.dohome.co.th/search?q={encoded_name}"}
                            {"name": "Shopee", "url": f"https://shopee.co.th/search?keyword={encoded_name}"},
                            {"name": "Lazada", "url": f"https://www.lazada.co.th/catalog/?q={encoded_name}"},
                        ]
                        
                        # สร้างปุ่ม
                        cols = st.columns(4) # แบ่ง 4 คอลัมน์ให้ดูสวย
                        for i, store in enumerate(stores):
                            with cols[i % 4]:
                                st.link_button(f"🔍 {store['name']}", store['url'], use_container_width=True)
                    else:
                        st.info("กรุณาพิมพ์คำค้นหาเพื่อสร้างปุ่มเช็คราคา")

                else:
                    if query:
                        st.warning(f"❌ ไม่พบสินค้า: '{query}'")
                        st.info("💡 ลองพิมพ์แค่ตัวเลขรุ่น หรือชื่อยี่ห้อบางส่วน")

    except Exception as e:
        st.error(f"เกิดข้อผิดพลาด: {str(e)}")
