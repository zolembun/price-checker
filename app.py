import streamlit as st
import pandas as pd
import google.generativeai as genai
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import json
import urllib.parse
from datetime import datetime

# ตั้งค่าหน้าเว็บ
st.set_page_config(page_title="ระบบเช็คราคา & คู่แข่ง", page_icon="💰", layout="wide")

# CSS ตกแต่งให้สวยงาม (เน้นตัวเลขราคาใหญ่ๆ)
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
# 1. เชื่อมต่อ Google Services (Sheets & Gemini)
# ---------------------------------------------------------
@st.cache_resource
def init_services():
    # ดึงกุญแจจาก Secrets
    service_account_info = st.secrets["gcp_service_account"]
    gemini_key = st.secrets["gemini_api_key"]
    
    # เชื่อมต่อ Google Sheets & Drive
    scopes = ['https://www.googleapis.com/auth/spreadsheets.readonly', 
              'https://www.googleapis.com/auth/drive.metadata.readonly']
    creds = Credentials.from_service_account_info(service_account_info, scopes=scopes)
    
    sheets_service = build('sheets', 'v4', credentials=creds)
    drive_service = build('drive', 'v3', credentials=creds)
    
    # เชื่อมต่อ Gemini
    genai.configure(api_key=gemini_key)
    
    return sheets_service, drive_service

# ---------------------------------------------------------
# 2. ฟังก์ชันโหลดข้อมูล
# ---------------------------------------------------------
@st.cache_data(ttl=600) # เก็บข้อมูลไว้ 10 นาที (ไม่ต้องโหลดใหม่ทุกครั้ง)
def load_data(_sheets_service, _drive_service, spreadsheet_url):
    try:
        # แปลง URL เป็น ID
        spreadsheet_id = spreadsheet_url.split('/d/')[1].split('/')[0]
        
        # 1. ดึงวันที่แก้ไขล่าสุด (Last Modified) และชื่อไฟล์
        file_meta = _drive_service.files().get(fileId=spreadsheet_id, fields="name, modifiedTime").execute()
        file_name = file_meta.get('name')
        mod_time_str = file_meta.get('modifiedTime') # format: 2023-11-26T10:00:00.000Z
        
        # แปลงเวลาให้สวยงาม
        dt = datetime.strptime(mod_time_str, "%Y-%m-%dT%H:%M:%S.%fZ")
        last_update = dt.strftime("%d/%m/%Y เวลา %H:%M น.")
        
        # 2. ดึงข้อมูลสินค้า
        sheet = _sheets_service.spreadsheets()
        result = sheet.values().get(spreadsheetId=spreadsheet_id, range="A:H").execute() # ดึง A ถึง H
        values = result.get('values', [])
        
        if not values:
            return None, None, None
            
        # สร้าง DataFrame (ใช้บรรทัดแรกเป็นหัวตาราง)
        df = pd.DataFrame(values[1:], columns=values[0])
        return df, file_name, last_update
        
    except Exception as e:
        st.error(f"เกิดข้อผิดพลาดในการดึงข้อมูล: {str(e)}")
        return None, None, None

# ---------------------------------------------------------
# 3. ส่วนแสดงผล (User Interface)
# ---------------------------------------------------------
st.title("🔎 ระบบเช็คราคา & ตั้งราคาขาย")

# ส่วนโหลดข้อมูล
try:
    sheets_svc, drive_svc = init_services()
    # ดึง URL จาก Secret หรือใช้ค่า Default
    SHEET_URL = st.secrets["sheet_url"]
    
    with st.spinner('กำลังเชื่อมต่อฐานข้อมูล...'):
        df, file_name, last_update = load_data(sheets_svc, drive_svc, SHEET_URL)

    if df is not None:
        # แสดงสถานะมุมขวาบน
        st.markdown(f"<div class='update-time'>📂 ไฟล์: {file_name} | 🕒 อัปเดตล่าสุด: {last_update}</div>", unsafe_allow_html=True)
        st.divider()

        # --- ช่องค้นหา ---
        query = st.text_input("พิมพ์ชื่อสินค้า, รหัสรุ่น หรือลักษณะสินค้า", placeholder="เช่น ตู้เย็น rt20, แอร์ 12000 btu")
        
        if query:
            # ใช้ AI ค้นหา (Gemini Search)
            model = genai.GenerativeModel('gemini-1.5-flash')
            
            # เตรียมข้อมูลส่งให้ AI (ส่งแค่รหัสกับชื่อ เพื่อประหยัด Token)
            product_list = df[['รหัสสินค้า', 'รายละเอียดสินค้า']].to_string(index=True)
            
            prompt = f"""
            คุณคือผู้ช่วยค้นหาสินค้า หน้าที่คือหาแถวข้อมูลที่ตรงกับคำค้นหามากที่สุด
            
            คำค้นหา: "{query}"
            
            รายการสินค้า:
            {product_list}
            
            คำสั่ง: ตอบกลับมาแค่หมายเลข Index (ตัวเลขด้านหน้าสุด) ของแถวที่ถูกต้องที่สุดเพียงเลขเดียว ถ้าไม่เจอให้ตอบ -1
            """
            
            with st.spinner('AI กำลังค้นหาข้อมูลที่ตรงที่สุด...'):
                response = model.generate_content(prompt)
                
            try:
                match_index = int(response.text.strip())
                
                if match_index != -1 and match_index in df.index:
                    # เจอข้อมูล! ดึงแถวนั้นมาแสดง
                    item = df.loc[match_index]
                    
                    # แปลงข้อมูลเป็นตัวเลข
                    try:
                        cost_price = float(str(item['ราคาทุนต่อหน่วย']).replace(',', ''))
                    except:
                        cost_price = 0
                        st.error("ราคาทุนไม่ใช่ตัวเลข ตรวจสอบไฟล์ต้นฉบับ")

                    stock = item['จำนวนสต้อก']
                    model_id = item['รหัสสินค้า']
                    product_name = item['รายละเอียดสินค้า']

                    # --- ส่วนแสดงผลหลัก (HERO SECTION) ---
                    st.success(f"✅ พบข้อมูล: {product_name}")
                    
                    col_main, col_info = st.columns([2, 1])
                    
                    with col_main:
                        # คำนวณราคาแนะนำ 12%
                        target_margin = 12
                        selling_price_12 = cost_price * (1 + (target_margin/100))
                        profit_12 = selling_price_12 - cost_price
                        
                        st.markdown(f"<div class='price-label'>🏷️ ราคาขายแนะนำ (กำไร {target_margin}%)</div>", unsafe_allow_html=True)
                        st.markdown(f"<div class='big-price'>{selling_price_12:,.0f} บาท</div>", unsafe_allow_html=True)
                        st.caption(f"(จะได้กำไรประมาณ {profit_12:,.0f} บาท)")
                    
                    with col_info:
                        st.markdown(f"""
                        <div class='stock-box'>
                            <b>📦 สต้อกคงเหลือ:</b> {stock}<br>
                            <b>💰 ราคาทุน:</b> {cost_price:,.0f} บาท<br>
                            <b>🆔 รหัส:</b> {model_id}
                        </div>
                        """, unsafe_allow_html=True)

                    # --- ตารางราคาละเอียด ---
                    with st.expander("ดูตารางราคาขายระดับอื่นๆ (3% - 15%)"):
                        margins = [3, 5, 7, 9, 12, 15]
                        price_data = []
                        for m in margins:
                            sp = cost_price * (1 + (m/100))
                            pf = sp - cost_price
                            price_data.append({
                                "กำไร (%)": f"{m}%",
                                "ราคาขาย": f"{sp:,.0f}",
                                "กำไร (บาท)": f"{pf:,.0f}"
                            })
                        st.table(pd.DataFrame(price_data))

                    # --- ทางด่วนเช็คราคาคู่แข่ง ---
                    st.subheader("🔍 ทางด่วนเช็คราคาคู่แข่ง")
                    encoded_name = urllib.parse.quote(model_id) # ใช้รหัสรุ่นไปค้น
                    
                    stores = [
                        {"name": "HomePro", "url": f"https://www.homepro.co.th/search?q={encoded_name}", "color": "#0099cc"},
                        {"name": "PowerBuy", "url": f"https://www.powerbuy.co.th/th/search/{encoded_name}", "color": "#ed1c24"},
                        {"name": "ThaiWatsadu", "url": f"https://www.thaiwatsadu.com/th/search/{encoded_name}", "color": "#ed1c24"},
                        {"name": "Big C", "url": f"https://www.bigc.co.th/search?q={encoded_name}", "color": "#94c11f"},
                        {"name": "Global House", "url": f"https://globalhouse.co.th/search?keyword={encoded_name}", "color": "#fdb913"},
                        {"name": "Makro", "url": f"https://www.makro.pro/c/search?q={encoded_name}", "color": "#ff0000"},
                        {"name": "Dohome", "url": f"https://www.dohome.co.th/search?q={encoded_name}", "color": "#ff6600"}
                    ]
                    
                    cols = st.columns(3) # แถวละ 3 ปุ่ม
                    for i, store in enumerate(stores):
                        with cols[i % 3]:
                            st.link_button(f"{store['name']}", store['url'], use_container_width=True)

                else:
                    st.warning("❌ ไม่พบสินค้าที่ใกล้เคียง ลองพิมพ์ชื่อรุ่นให้ชัดเจนขึ้นครับ")
            except Exception as e:
                st.error(f"เกิดข้อผิดพลาดในการแปลผล AI: {e}")

except Exception as e:
    st.error(f"กรุณาตั้งค่า Secrets ก่อนใช้งาน: {str(e)}")
