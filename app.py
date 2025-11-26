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

# CSS ตกแต่ง
st.markdown("""
<style>
    .big-price { font-size: 40px !important; font-weight: bold; color: #28a745; }
    .price-label { font-size: 20px; color: #555; }
    .stock-box { background-color: #f0f2f6; padding: 10px; border-radius: 5px; border-left: 5px solid #ffc107; }
    .update-time { font-size: 14px; color: #888; text-align: right; }
    div.stButton > button { width: 100%; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# 1. เชื่อมต่อ Services
# ---------------------------------------------------------
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

# ---------------------------------------------------------
# 2. โหลดข้อมูล
# ---------------------------------------------------------
@st.cache_data(ttl=600)
def load_data(_sheets_service, _drive_service, spreadsheet_url):
    try:
        spreadsheet_id = spreadsheet_url.split('/d/')[1].split('/')[0]
        
        file_meta = _drive_service.files().get(fileId=spreadsheet_id, fields="name, modifiedTime").execute()
        file_name = file_meta.get('name')
        mod_time_str = file_meta.get('modifiedTime')
        dt = datetime.strptime(mod_time_str, "%Y-%m-%dT%H:%M:%S.%fZ")
        last_update = dt.strftime("%d/%m/%Y เวลา %H:%M น.")
        
        sheet = _sheets_service.spreadsheets()
        result = sheet.values().get(spreadsheetId=spreadsheet_id, range="A:H").execute()
        values = result.get('values', [])
        
        if not values: return None, None, None
        
        df = pd.DataFrame(values[1:], columns=values[0])
        df['ราคาทุนต่อหน่วย'] = pd.to_numeric(df['ราคาทุนต่อหน่วย'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
        return df, file_name, last_update
        
    except Exception as e:
        st.error(f"เกิดข้อผิดพลาด: {str(e)}")
        return None, None, None

# ---------------------------------------------------------
# 3. ส่วนแสดงผล
# ---------------------------------------------------------
st.title("🔎 ระบบเช็คราคา & ตั้งราคาขาย")

try:
    sheets_svc, drive_svc = init_services()
    SHEET_URL = st.secrets["sheet_url"]
    
    with st.spinner('กำลังเชื่อมต่อฐานข้อมูล...'):
        df, file_name, last_update = load_data(sheets_svc, drive_svc, SHEET_URL)

    if df is not None:
        st.markdown(f"<div class='update-time'>📂 ไฟล์: {file_name} | 🕒 อัปเดตล่าสุด: {last_update}</div>", unsafe_allow_html=True)
        st.divider()

        query = st.text_input("พิมพ์รหัสรุ่น หรือ ชื่อสินค้า", placeholder="เช่น RT20, EMG20, ไมโครเวฟ")
        
        if query:
            match_index = -1
            found_by = ""

            # --- ด่านที่ 1: ค้นหาด้วยรหัสรุ่น ---
            search_term = query.strip()
            direct_match = df[df['รหัสสินค้า'].astype(str).str.contains(search_term, case=False, na=False)]
            
            if not direct_match.empty:
                match_index = direct_match.index[0]
                found_by = "⚡ เจอรหัสรุ่นนี้ในระบบ"
            
            else:
                # --- ด่านที่ 2: ใช้ AI ช่วยหา ---
                model = genai.GenerativeModel('gemini-1.5-flash')
                product_list = df[['รหัสสินค้า', 'รายละเอียดสินค้า']].to_string(index=True)
                
                prompt = f"""
                ค้นหาสินค้าจากคำว่า: "{query}"
                ในรายการนี้:
                {product_list}
                
                ตอบกลับเฉพาะตัวเลข Index รายการที่ถูกต้องที่สุดตัวเดียว ถ้าไม่เจอตอบ -1
                """
                
                with st.spinner('AI กำลังค้นหา...'):
                    try:
                        response = model.generate_content(prompt)
                        match_index = int(response.text.strip())
                        found_by = "🤖 AI ค้นพบจากการวิเคราะห์"
                    except:
                        match_index = -1

            # --- แสดงผลลัพธ์ ---
            if match_index != -1 and match_index in df.index:
                item = df.loc[match_index]
                cost_price = item['ราคาทุนต่อหน่วย']
                stock = item['จำนวนสต้อก']
                model_id = item['รหัสสินค้า']
                product_name = item['รายละเอียดสินค้า']

                st.success(f"✅ {found_by}: {product_name}")
                
                col_main, col_info = st.columns([2, 1])
                with col_main:
                    target_margin = 12
                    selling_price_12 = cost_price * (1 + (target_margin/100))
                    profit_12 = selling_price_12 - cost_price
                    st.markdown(f"<div class='price-label'>🏷️ ราคาขายแนะนำ (กำไร {target_margin}%)</div>", unsafe_allow_html=True)
                    st.markdown(f"<div class='big-price'>{selling_price_12:,.0f} บาท</div>", unsafe_allow_html=True)
                    st.caption(f"(กำไร {profit_12:,.0f} บาท)")
                
                with col_info:
                    st.markdown(f"""
                    <div class='stock-box'>
                        <b>📦 สต้อก:</b> {stock}<br>
                        <b>💰 ทุน:</b> {cost_price:,.0f}<br>
                        <b>🆔 รหัส:</b> {model_id}
                    </div>
                    """, unsafe_allow_html=True)

                with st.expander("ดูตารางราคาขายอื่นๆ"):
                    margins = [3, 5, 7, 9, 12, 15]
                    price_data = []
                    for m in margins:
                        sp = cost_price * (1 + (m/100))
                        price_data.append({"กำไร %": f"{m}%", "ราคาขาย": f"{sp:,.0f}", "กำไร (บาท)": f"{sp-cost_price:,.0f}"})
                    st.table(pd.DataFrame(price_data))

                st.divider()
                st.subheader("🔍 เช็คราคาคู่แข่ง")

                # --- [ส่วนที่แก้ไขใหม่] ---
                # 1. เตรียมคำค้นหาตั้งต้น (ลบภาษาไทยออกเหมือนเดิม)
                default_search_code = re.sub(r'[\u0E00-\u0E7F]', '', str(model_id)).strip('-').strip()
                
                # 2. สร้างช่องให้ผู้ใช้แก้ไขได้ (Text Input)
                # ถ้าผู้ใช้แก้คำในช่องนี้ ตัวแปร final_search_keyword จะเปลี่ยนตามทันที
                final_search_keyword = st.text_input("🎯 คำค้นหาสำหรับเช็คราคา (แก้ไขได้):", value=default_search_code)
                
                if final_search_keyword:
                    encoded_name = urllib.parse.quote(final_search_keyword.strip())
                    
                    stores = [
                        {"name": "HomePro", "url": f"https://www.homepro.co.th/search?q={encoded_name}"},
                        {"name": "PowerBuy", "url": f"https://www.powerbuy.co.th/th/search/{encoded_name}"},
                        {"name": "ThaiWatsadu", "url": f"https://www.thaiwatsadu.com/th/search/{encoded_name}"},
                        {"name": "Big C", "url": f"https://www.bigc.co.th/search?q={encoded_name}"},
                        {"name": "Global", "url": f"https://globalhouse.co.th/search?keyword={encoded_name}"},
                        {"name": "Makro", "url": f"https://www.makro.pro/c/search?q={encoded_name}"},
                        {"name": "Dohome", "url": f"https://www.dohome.co.th/search?q={encoded_name}"}
                    ]
                    
                    cols = st.columns(3)
                    for i, store in enumerate(stores):
                        with cols[i % 3]:
                            st.link_button(f"{store['name']}", store['url'], use_container_width=True)
                else:
                    st.info("กรุณาพิมพ์คำค้นหาเพื่อสร้างปุ่มเช็คราคา")

            else:
                st.warning(f"❌ ไม่พบข้อมูลสำหรับ: '{query}'")

except Exception as e:
    st.error(f"กรุณาตั้งค่า Secrets ก่อนใช้งาน: {str(e)}")
