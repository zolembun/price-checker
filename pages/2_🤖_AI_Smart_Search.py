import streamlit as st
import pandas as pd
import google.generativeai as genai
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import json
import time

# ตั้งค่าหน้าเว็บ (ย้ายมาบรรทัดแรกสุดเสมอ)
st.set_page_config(page_title="AI Smart Search", page_icon="🤖", layout="wide")

# ==========================================
# 🔐 1. ตรวจสอบ Login
# ==========================================
if "password_correct" not in st.session_state or not st.session_state["password_correct"]:
    st.warning("⛔ กรุณาเข้าสู่ระบบที่หน้าแรกก่อน (Home Page)")
    st.stop() 

# ==========================================
# 🎨 CSS Styling (แต่งสวย)
# ==========================================
st.markdown("""
<style>
    .metric-card {
        background-color: #f0f2f6;
        border-radius: 10px;
        padding: 15px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    .stButton>button {
        width: 100%;
        border-radius: 8px;
        height: 3em;
    }
    /* ปรับแต่ง Status Container */
    .stStatusWidget {
        border-radius: 10px;
    }
</style>
""", unsafe_allow_html=True)

# ==========================================
# ⚙️ 2. เชื่อมต่อ Services
# ==========================================
@st.cache_resource
def init_services():
    try:
        service_account_info = st.secrets["gcp_service_account"]
        gemini_key = st.secrets["gemini_api_key"]
        
        scopes = ['https://www.googleapis.com/auth/spreadsheets'] 
        creds = Credentials.from_service_account_info(service_account_info, scopes=scopes)
        
        sheets_service = build('sheets', 'v4', credentials=creds)
        genai.configure(api_key=gemini_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        return sheets_service, model
    except Exception as e:
        st.error(f"Config Error: {e}")
        return None, None

sheets_svc, ai_model = init_services()
if not sheets_svc: st.stop()

try:
    SHEET_URL = st.secrets["sheet_url"]
    SPREADSHEET_ID = SHEET_URL.split('/d/')[1].split('/')[0]
except:
    st.error("ไม่พบ sheet_url ใน Secrets")
    st.stop()

# ==========================================
# 📥 3. ฟังก์ชันจัดการข้อมูล
# ==========================================
@st.cache_data(ttl=600)
def load_all_data():
    try:
        # 3.1 ดึงข้อมูลสินค้าหลัก
        res_main = sheets_svc.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range="A:H").execute()
        vals_main = res_main.get('values', [])
        
        if vals_main:
            df_main = pd.DataFrame(vals_main[1:], columns=vals_main[0])
            # แปลงตัวเลข
            cols_to_num = ['ราคาทุนต่อหน่วย', 'จำนวนสต้อก']
            for col in cols_to_num:
                if col in df_main.columns:
                    df_main[col] = pd.to_numeric(df_main[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
        else:
            df_main = pd.DataFrame()

        # 3.2 ดึงข้อมูล AI Memory
        try:
            res_mem = sheets_svc.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range="AI_Memory!A:E").execute()
            vals_mem = res_mem.get('values', [])
            if vals_mem:
                df_mem = pd.DataFrame(vals_mem[1:], columns=vals_mem[0])
            else:
                df_mem = pd.DataFrame(columns=['SKU', 'AI_Brand', 'AI_Type', 'AI_Spec', 'AI_Tags'])
        except:
            df_mem = pd.DataFrame(columns=['SKU', 'AI_Brand', 'AI_Type', 'AI_Spec', 'AI_Tags'])

        return df_main, df_mem
    except Exception as e:
        st.error(f"Load Data Error: {e}")
        return pd.DataFrame(), pd.DataFrame()

def append_to_sheet(new_data_df):
    values = new_data_df.values.tolist()
    body = {'values': values}
    try:
        sheets_svc.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range="AI_Memory!A:A", 
            valueInputOption="USER_ENTERED",
            body=body
        ).execute()
        return True
    except Exception as e:
        st.error(f"Save Error: {e}")
        return False

# Cache การรวมตารางเพื่อความเร็ว
@st.cache_data(ttl=600)
def merge_data(df_main, df_mem):
    if df_mem.empty:
        return df_main.copy()
    
    # แปลงเป็น String เพื่อการ Join ที่แม่นยำ
    df_main_c = df_main.copy()
    df_mem_c = df_mem.copy()
    df_main_c['รหัสสินค้า'] = df_main_c['รหัสสินค้า'].astype(str).str.strip()
    df_mem_c['SKU'] = df_mem_c['SKU'].astype(str).str.strip()
    
    return pd.merge(df_main_c, df_mem_c, left_on='รหัสสินค้า', right_on='SKU', how='left')

# ==========================================
# 🧠 4. ฟังก์ชัน AI
# ==========================================
def ask_gemini_extract(product_list):
    prompt = f"""
    วิเคราะห์รายชื่อสินค้าและสกัดข้อมูลสำคัญ:
    {json.dumps(product_list, ensure_ascii=False)}
    
    ตอบเป็น JSON Array เท่านั้น:
    [
      {{
        "AI_Brand": "ยี่ห้อ (เช่น Samsung, Toshiba)",
        "AI_Type": "ประเภท (เช่น ตู้เย็น, ทีวี, เครื่องซักผ้า)",
        "AI_Spec": "สเปคเด่น (เช่น 12คิว, 55นิ้ว, ฝาบน)",
        "AI_Tags": "คำค้นหา (เช่น ประหยัดไฟ, 2ประตู, 4K)"
      }}
    ]
    """
    try:
        response = ai_model.generate_content(prompt)
        text = response.text.strip().replace('```json', '').replace('```', '')
        # เพิ่มการ Clean ข้อมูลเบื้องต้น
        if text.startswith('[') and text.endswith(']'):
            return json.loads(text)
        else:
            # บางที AI เผลอพูดเยอะ ให้พยายามหา JSON block
            start = text.find('[')
            end = text.rfind(']') + 1
            if start != -1 and end != -1:
                return json.loads(text[start:end])
            return []
    except:
        return []

def ask_gemini_filter(query, columns):
    prompt = f"""
    คุณคือ Data Analyst. จงแปลงคำถาม: "{query}"
    เป็น JSON Filter สำหรับ Pandas DataFrame
    
    Columns ที่มี: {columns}
    
    Output Format (JSON Only):
    {{
        "filters": [
            {{ "column": "col_name", "operator": "contains/equals/gt/lt", "value": "val" }}
        ]
    }}
    *หมายเหตุ*: 
    - ถ้าเกี่ยวกับราคา/ตัวเลข ให้ใช้ gt (>) หรือ lt (<)
    - operator "contains" ใช้สำหรับข้อความ
    """
    try:
        response = ai_model.generate_content(prompt)
        text = response.text.strip().
