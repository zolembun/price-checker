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
# 🔐 ส่วนระบบ Login
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
    
    # CSS ตกแต่ง: เน้นราคาทุนให้เด่น (ตัวหนังสือใหญ่ สีแดง)
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
            # ดึง ID จาก URL
            spreadsheet_id = spreadsheet_url.split('/d/')[1].split('/')[0]
            
            # ดึง Metadata (ชื่อไฟล์, เวลาอัปเดต)
            file_meta = _drive_service.files().get(fileId=spreadsheet_id, fields="name, modifiedTime").execute()
            file_name = file_meta.get('name')
            mod_time_str = file_meta.get('modifiedTime')
            dt = datetime.strptime(mod_time_str, "%Y-%m-%dT%H:%M:%S.%fZ")
            last_update = dt.strftime("%d/%m/%Y %H:%M น.")
            
            # ดึงข้อมูลจาก Sheet
            sheet = _sheets_service.spreadsheets()
            result = sheet.values().get(spreadsheetId=spreadsheet_id, range="A:H").execute()
            values = result.get('values', [])
            
            if not values: return None, None, None
            
            # สร้าง DataFrame
            df = pd.DataFrame(values[1:], columns=values[0])
            
            # แปลงราคาทุนเป็นตัวเลข (ลบ comma ออก)
            if 'ราคาทุนต่อหน่วย' in df.columns:
                df['ราคาทุนต่อหน่วย'] = pd.to_numeric(
                    df['ราคาทุนต่อหน่วย'].astype(str).str.replace(',', ''), 
                    errors='coerce'
                ).fillna(0)
                
            if 'จำนวนสต้อก' in df.columns:
                df['จำนวนสต้อก'] = pd.to_numeric(
                    df['จำนวนสต้อก'].astype(str).str.replace(',', ''), 
                    errors='coerce'
                ).fillna(0)
                
            return df, file_name, last_update
            
        except Exception as e:
            st.error(f"เกิดข้อผิดพลาดในการโหลดข้อมูล: {str(e)}")
            return None, None, None

    # ฟังก์ชันทำความสะอาดข้อความ (หัวใจสำคัญของการค้นหาแบบไม่สนขีด)
    def clean_text(text):
        if not isinstance(text, str):
            text = str(text)
        # ลบทุกอย่างที่ไม่ใช่ตัวอักษรและตัวเลข แล้วแปลงเป็นตัวเล็ก
        return re.sub(r'[^a-zA-Z0-9ก-๙]', '', text).lower()

    # 3. ส่วนแสดงผลหลัก
    st.title("🔎 เช็คราคาทุน (Smart Search)")

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
                
                # --- LOGIC การค้นหาแบบประหยัด Token ---
                
                # 1. แปลงคำค้นหาให้สะอาด (rt-20 -> rt20)
                query_clean = clean_text(query)
                
                # 2. สร้างคอลัมน์ชั่วคราวใน Memory (สะอาดเหมือนกัน) เพื่อเทียบ
                # (ใช้ apply เพื่อ clean ทีละแถว)
                sku_clean_series = df['รหัสสินค้า'].astype(str).apply(clean_text)
                desc_clean_series = df['รายละเอียดสินค้า'].astype(str).apply(clean_text)
                
                # --- ด่านที่ 1: ค้นหาใน "รหัสสินค้า" (Column A) ---
                # เช็คว่า query_clean เป็นส่วนหนึ่งของ sku_clean หรือไม่
                sku_matches = df[sku_clean_series.str.contains(query_clean, na=False)]
                
                if not sku_matches.empty:
                    # ถ้าเจอในรหัสสินค้า เอาตัวแรกที่เจอเลย (แม่นยำสุด)
                    match_index = sku_matches.index[0]
                    found_by = "⚡ เจอรหัสสินค้า (Column A)"
                
                else:
                    # --- ด่านที่ 2: ค้นหาใน "รายละเอียดสินค้า" (Column B) ---
                    # เช็คในรายละเอียดสินค้าด้วย logic เดียวกัน
                    desc_matches = df[desc_clean_series.str.contains(query_clean, na=False)]
                    
                    if not desc_matches.empty:
                        # ถ้าเจอ ให้เลือกตัวที่มีรหัสสั้นที่สุด (มักจะเป็นตัวแม่) หรือตัวแรก
                        match_index = desc_matches.index[0]
                        found_by = "🔎 เจอในรายละเอียด (Column B)"
                        
                    else:
                        # --- ด่านที่ 3: ใช้ AI (ถ้าหาไม่เจอจริงๆ) ---
                        # ส่งข้อมูลไปให้ AI แค่บางส่วน (Candidates) เพื่อประหยัด Token
                        
                        # กรองสินค้าที่มี "บางส่วน" ของคำค้นหา เพื่อไม่ให้ส่งไปทั้ง 1000 รายการ
                        # เช่น ค้น "rt20" อย่างน้อยต้องมี "r" หรือ "t" หรือ "2"
                        keywords = list(filter(None, re.split(r'[^a-zA-Z0-9]', query))) # แยกคำ
                        if not keywords: keywords = [query]
                        
                        # กรองแบบหยาบๆ ด้วย Python ก่อน
                        candidates = df[df.astype(str).apply(lambda x: any(k.lower() in x.lower() for k in keywords), axis=1)]
                        
                        # จำกัดจำนวนที่จะส่งให้ AI (Max 30 ตัว) -> ประหยัด Token ชัวร์
                        if candidates.empty:
                             search_pool = df.sample(min(len(df), 15)) # สุ่มมานิดหน่อยเผื่อ AI เดาได้
                        else:
                             search_pool = candidates.head(30)
                        
                        product_list_str = search_pool[['รหัสสินค้า', 'รายละเอียดสินค้า']].to_string(index=True)
                        
                        model = genai.GenerativeModel('gemini-1.5-flash')
                        prompt = f"""
                        คำค้นหา: "{query}"
                        
                        จากรายการสินค้าด้านล่างนี้ ตัวไหนคือสินค้าที่ลูกค้าต้องการมากที่สุด?
                        (ดูทั้งรหัสและชื่อรุ่น พยายามจับคู่แม้ตัวสะกดจะผิดเพี้ยนเล็กน้อย)
                        
                        รายการสินค้า:
                        {product_list_str}
                        
                        ตอบกลับเฉพาะตัวเลข Index (ด้านซ้ายสุด) ของรายการที่ถูกที่สุดเพียงตัวเดียว
                        ถ้ามั่นใจว่าไม่มีเลย ให้ตอบ -1
                        """
                        
                        with st.spinner('ไม่เจอตรงๆ... กำลังให้ AI ช่วยแกะลายแทง...'):
                            try:
                                response = model.generate_content(prompt)
                                match_index = int(response.text.strip())
                                found_by = "🤖 AI ค้นพบ (Advanced Match)"
                            except:
                                match_index = -1

                # -----------------------------------------------------------
                # ส่วนแสดงผล (เน้นราคาทุน)
                # -----------------------------------------------------------
                if match_index != -1 and match_index in df.index:
                    item = df.loc[match_index]
                    
                    # ดึงข้อมูล
                    cost_price = item.get('ราคาทุนต่อหน่วย', 0)
                    stock = item.get('จำนวนสต้อก', 0)
                    model_id = item.get('รหัสสินค้า', '-')
                    product_name = item.get('รายละเอียดสินค้า', '-')
                    brand = item.get('ยี่ห้อ', '-')

                    # Header ชื่อสินค้า
                    st.success(f"{found_by}: {product_name}")
                    
                    # Layout 3 คอลัมน์ (ทุน - ขาย - ข้อมูล)
                    c1, c2, c3 = st.columns([1.3, 1.3, 1])
                    
                    # คำนวณราคาขาย (สมมติ 15%)
                    target_margin = 15
                    selling_price = cost_price * (1 + (target_margin/100))
                    profit = selling_price - cost_price

                    with c1:
                        st.markdown(f"""
                        <div class="cost-box">
                            <div class="price-label">🔴 ราคาทุน (COST)</div>
                            <div class="price-value-cost">{cost_price:,.0f}</div>
                            <div style="color: #b71c1c; font-size: 0.8em; margin-top:5px;">(ความลับร้านค้า)</div>
                        </div>
                        """, unsafe_allow_html=True)
                        
                    with c2:
                        st.markdown(f"""
                        <div class="selling-box">
                            <div class="price-label">🟢 ราคาขายแนะนำ (+{target_margin}%)</div>
                            <div class="price-value-sell">{selling_price:,.0f}</div>
                            <div style="color: #1b5e20; font-size: 0.9em; font-weight:bold; margin-top:5px;">กำไร {profit:,.0f} บาท</div>
                        </div>
                        """, unsafe_allow_html=True)

                    with c3:
                        st.markdown(f"""
                        <div class="info-box">
                            <div style="margin-bottom:8px;"><b>🆔 รหัส:</b> <span class="search-badge">{model_id}</span></div>
                            <div style="margin-bottom:8px;"><b>📦 สต้อก:</b> {stock} ชิ้น</div>
                            <div style="margin-bottom:8px;"><b>🏷️ ยี่ห้อ:</b> {brand}</div>
                        </div>
                        """, unsafe_allow_html=True)
                        
                        # ปุ่มกดค้นหา Google
                        clean_keyword = re.sub(r'[^a-zA-Z0-9 ]', '', str(model_id))
                        st.link_button(
                            "🔍 เช็ค Google", 
                            f"https://www.google.com/search?q={clean_keyword}",
                            use_container_width=True
                        )

                    st.divider()
                    
                    # ตาราง Margin (ซ่อนไว้ใน Expander จะได้ไม่รก)
                    with st.expander("ดูตารางราคา Margin อื่นๆ (5% - 30%)"):
                        margins = [5, 10, 15, 20, 25, 30]
                        price_data = []
                        for m in margins:
                            sp = cost_price * (1 + (m/100))
                            price_data.append({
                                "กำไร %": f"{m}%", 
                                "ราคาขาย": f"{sp:,.0f}", 
                                "กำไร (บาท)": f"{sp-cost_price:,.0f}"
                            })
                        st.dataframe(pd.DataFrame(price_data), hide_index=True, use_container_width=True)

                    # ลิงก์เช็คราคาคู่แข่ง
                    st.subheader("🛒 เช็คราคาตลาด")
                    
                    search_query = st.text_input("คีย์เวิร์ดสำหรับค้นหา:", value=clean_keyword)
                    if search_query:
                        enc = urllib.parse.quote(search_query)
                        cols = st.columns(5)
                        stores = [
                            ("Shopee", f"https://shopee.co.th/search?keyword={enc}"),
                            ("Lazada", f"https://www.lazada.co.th/catalog/?q={enc}"),
                            ("NocNoc", f"https://nocnoc.com/search?q={enc}"),
                            ("PowerBuy", f"https://www.powerbuy.co.th/th/search/{enc}"),
                            ("HomePro", f"https://www.homepro.co.th/search?q={enc}")
                        ]
                        for idx, (name, url) in enumerate(stores):
                            cols[idx].link_button(name, url, use_container_width=True)

                else:
                    if query:
                        st.warning(f"❌ ไม่พบสินค้า: '{query}'")
                        st.info("💡 คำแนะนำ: ลองพิมพ์ชื่อยี่ห้อ หรือรหัสรุ่นแค่บางส่วน (เช่น พิมพ์แค่ตัวเลข)")

    except Exception as e:
        st.error(f"เกิดข้อผิดพลาด: {str(e)}")
