import streamlit as st
import pandas as pd
import google.generativeai as genai
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import urllib.parse
from datetime import datetime

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
        # แปลงราคาเป็นตัวเลขรอไว้เลย เพื่อลด error
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

        query = st.text_input("พิมพ์ชื่อสินค้า หรือ รหัสรุ่น", placeholder="เช่น ตู้เย็น rt20")
        
        if query:
            # --- [LOGIC ใหม่: ประหยัด Token] ---
            # 1. กรองเบื้องต้นด้วย Python (หาคำที่ตรงกันบ้าง)
            keywords = query.lower().split()
            # สร้างเงื่อนไข: แถวไหนที่มีคำค้นหาโผล่มาบ้าง (ในรหัส หรือ ชื่อ) เก็บไว้หมด
            mask = pd.Series([False] * len(df))
            for word in keywords:
                mask |= df['รหัสสินค้า'].astype(str).str.contains(word, case=False, na=False)
                mask |= df['รายละเอียดสินค้า'].astype(str).str.contains(word, case=False, na=False)
            
            filtered_df = df[mask]
            
            # ถ้าไม่เจอเลย หรือเจอเยอะไป ให้เอาตัวอย่างมาสัก 50 ตัวพอ (กัน Token เต็ม)
            if len(filtered_df) == 0:
                candidates = df.head(50) # ถ้าหาไม่เจอเลย เอา 50 ตัวแรกให้ AI ดูเผื่อฟลุ๊ค
            elif len(filtered_df) > 50:
                candidates = filtered_df.head(50) # ถ้าเจอเยอะเกิน ตัดมาแค่ 50 ตัว
            else:
                candidates = filtered_df
            
            # 2. ส่งให้ AI (Gemini Pro) เลือกจากรายการที่คัดมาแล้ว
            # ใช้ gemini-pro (ที่เสถียรกว่า) ได้เลย เพราะข้อมูลส่งไปน้อยแล้ว
            model = genai.GenerativeModel('gemini-pro')
            
            product_list = candidates[['รหัสสินค้า', 'รายละเอียดสินค้า']].to_string(index=True)
            
            prompt = f"""
            ช่วยหาสินค้าที่ตรงกับคำค้นหา: "{query}"
            
            จากรายการนี้ (เลือก index ด้านหน้าสุดมา 1 เลข):
            {product_list}
            
            ถ้าเจอที่ตรงหรือใกล้เคียงมากๆ ให้ตอบเลข Index มาตัวเดียว
            ถ้าไม่เจออะไรเลย ให้ตอบ -1
            """
            
            with st.spinner('AI กำลังค้นหา...'):
                try:
                    response = model.generate_content(prompt)
                    match_index = int(response.text.strip())
                except:
                    match_index = -1
                
            if match_index != -1 and match_index in df.index:
                item = df.loc[match_index]
                cost_price = item['ราคาทุนต่อหน่วย']
                stock = item['จำนวนสต้อก']
                model_id = item['รหัสสินค้า']
                product_name = item['รายละเอียดสินค้า']

                st.success(f"✅ พบข้อมูล: {product_name}")
                
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

                st.subheader("🔍 เช็คราคาคู่แข่ง")
                encoded_name = urllib.parse.quote(model_id)
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
                st.warning("❌ ไม่พบสินค้า หรือสินค้าไม่ใกล้เคียงพอ")

except Exception as e:
    st.error(f"กรุณาตั้งค่า Secrets ก่อนใช้งาน: {str(e)}")
