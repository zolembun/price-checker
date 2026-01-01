import streamlit as st
import pandas as pd
import google.generativeai as genai
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import urllib.parse
from datetime import datetime
import re
import json
import time
import streamlit.components.v1 as components

# ---------------------------------------------------------
# 1. ตั้งค่าหน้าเว็บ (บรรทัดแรกสุด ห้ามย้าย)
# ---------------------------------------------------------
st.set_page_config(page_title="ระบบเช็คราคา & AI", page_icon="💰", layout="wide")

# ---------------------------------------------------------
# 2. ระบบ Login
# ---------------------------------------------------------
def check_password():
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

if not check_password():
    st.stop()

# ---------------------------------------------------------
# 3. CSS Styling
# ---------------------------------------------------------
st.markdown("""
<style>
    /* กล่องราคาทุน */
    .cost-box { 
        background-color: #ffebee; padding: 15px; border-radius: 10px; 
        border: 2px solid #ef5350; text-align: center; box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    .price-value-cost { font-size: 42px !important; font-weight: 900; color: #c62828; line-height: 1.2;}
    
    /* กล่องราคาขาย */
    .selling-box { 
        background-color: #e8f5e9; padding: 15px; border-radius: 10px; 
        border: 2px solid #66bb6a; text-align: center;
    }
    .price-value-sell { font-size: 42px !important; font-weight: 900; color: #2e7d32; line-height: 1.2;}
    
    /* กล่องข้อมูล */
    .info-box {
        background-color: #f8f9fa; padding: 15px; border-radius: 10px; border: 1px solid #ddd;
    }
    
    /* ปุ่มกด */
    div.stButton > button { width: 100%; border-radius: 8px; font-weight: bold; height: 3em; }
    
    /* ซ่อน Header */
    header {visibility: hidden;}
    
    /* Status Widget */
    .stStatusWidget { border-radius: 10px; }
    .stock-box { 
        background-color: #e3f2fd; padding: 15px; border-radius: 10px; 
        border: 2px solid #2196f3; text-align: center; box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    .price-value-stock { font-size: 42px !important; font-weight: 900; color: #1565c0; line-height: 1.2;}

    /* ปรับปรุงกล่อง Info ด้านล่างให้เรียบร้อยขึ้น */
    .detail-bar {
        margin-top: 10px; padding: 10px; background-color: #f1f3f4; 
        border-radius: 8px; text-align: center; font-size: 14px; color: #333;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# 4. เชื่อมต่อ Services
# ---------------------------------------------------------
@st.cache_resource
def init_services():
    try:
        service_account_info = st.secrets["gcp_service_account"]
        gemini_key = st.secrets["gemini_api_key"]
        
        scopes = ['https://www.googleapis.com/auth/spreadsheets', 
                  'https://www.googleapis.com/auth/drive.metadata.readonly']
        creds = Credentials.from_service_account_info(service_account_info, scopes=scopes)
        
        sheets_service = build('sheets', 'v4', credentials=creds)
        drive_service = build('drive', 'v3', credentials=creds)
        
        # Config Gemini
        genai.configure(api_key=gemini_key)
        
        # 🔥 แก้ไขจุดที่ Error: เปลี่ยนชื่อโมเดลเป็นรุ่น Latest
        try:
            model = genai.GenerativeModel('gemini-2.0-flash')
        except:
            # Fallback ถ้า latest ใช้ไม่ได้ ให้ลองตัวธรรมดาหรือ Pro
            model = genai.GenerativeModel('gemini-2.5-flash')
        
        return sheets_service, drive_service, model
    except Exception as e:
        st.error(f"Config Error: {e}")
        return None, None, None

sheets_svc, drive_svc, ai_model = init_services()
if not sheets_svc: st.stop()

try:
    SHEET_URL = st.secrets["sheet_url"]
    SPREADSHEET_ID = SHEET_URL.split('/d/')[1].split('/')[0]
except:
    st.error("ไม่พบ sheet_url ใน Secrets")
    st.stop()

# ---------------------------------------------------------
# 5. ฟังก์ชันโหลด/บันทึกข้อมูล
# ---------------------------------------------------------
@st.cache_data(ttl=600)
def load_data_master():
    try:
        # Metadata
        file_meta = drive_svc.files().get(fileId=SPREADSHEET_ID, fields="name, modifiedTime").execute()
        file_name = file_meta.get('name')
        dt = datetime.strptime(file_meta.get('modifiedTime'), "%Y-%m-%dT%H:%M:%S.%fZ")
        last_update = dt.strftime("%d/%m/%Y %H:%M น.")

        # Main Data
        res_main = sheets_svc.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range="A:H").execute()
        vals_main = res_main.get('values', [])
        
        if vals_main:
            df_main = pd.DataFrame(vals_main[1:], columns=vals_main[0])
            for col in ['ราคาทุนต่อหน่วย', 'จำนวนสต้อก']:
                if col in df_main.columns:
                    df_main[col] = pd.to_numeric(df_main[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
        else:
            df_main = pd.DataFrame()

        # AI Memory Data
        try:
            # 🔥 แก้ไขจุดที่ 1: ดึงข้อมูลถึงคอลัมน์ F (เพื่อให้ได้ AI_Kind)
            res_mem = sheets_svc.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range="AI_Memory!A:F").execute()
            vals_mem = res_mem.get('values', [])
            
            # 🔥 แก้ไขจุดที่ 2: เพิ่ม AI_Kind ในหัวข้อคอลัมน์
            cols_mem = ['SKU', 'AI_Brand', 'AI_Type', 'AI_Spec', 'AI_Tags', 'AI_Kind']

            if vals_mem and len(vals_mem) > 1:
                headers = vals_mem[0]
                rows = vals_mem[1:]
                # เติมค่าว่างให้ครบทุกคอลัมน์ถ้ามันแหว่ง
                fixed_rows = [r + [None]*(len(headers)-len(r)) for r in rows]
                df_mem = pd.DataFrame(fixed_rows, columns=headers)
                
                # ถ้าโหลดมาแล้วไม่มีคอลัมน์ AI_Kind ให้เติมเข้าไป
                if 'AI_Kind' not in df_mem.columns:
                    df_mem['AI_Kind'] = ''
            else:
                # สร้างตารางเปล่าแบบมี AI_Kind รอไว้
                df_mem = pd.DataFrame(columns=cols_mem)
        except Exception as e:
            # กรณี Error ก็สร้างตารางเปล่าที่มี AI_Kind ไว้ก่อน
            print(f"Load Mem Error: {e}")
            df_mem = pd.DataFrame(columns=['SKU', 'AI_Brand', 'AI_Type', 'AI_Spec', 'AI_Tags', 'AI_Kind'])

        return df_main, df_mem, file_name, last_update

    except Exception as e:
        st.error(f"Load Data Error: {e}")
        return pd.DataFrame(), pd.DataFrame(), "Error", "-"

def append_to_sheet(data_values):
    body = {'values': data_values}
    try:
        sheets_svc.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID, range="AI_Memory!A:A", 
            valueInputOption="USER_ENTERED", body=body
        ).execute()
        return True
    except Exception as e: 
        st.error(f"Save Error: {e}")
        return False

def overwrite_memory_sheet(df_new_mem):
    try:
        # 1. แปลง DataFrame เป็น List เพื่อเตรียมบันทึก
        # ต้องจัดการ NaN ให้เป็น empty string ไม่งั้น Error
        df_new_mem = df_new_mem.fillna('') 
        values = [df_new_mem.columns.tolist()] + df_new_mem.values.tolist()
        
        # 2. ล้างข้อมูลเก่าทั้งหมดใน AI_Memory
        sheets_svc.spreadsheets().values().clear(
            spreadsheetId=SPREADSHEET_ID, range="AI_Memory!A:E"
        ).execute()

        # 3. บันทึกข้อมูลใหม่ลงไป
        body = {'values': values}
        sheets_svc.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID, range="AI_Memory!A1",
            valueInputOption="USER_ENTERED", body=body
        ).execute()
        return True
    except Exception as e:
        st.error(f"Cleanup Error: {e}")
        return False

@st.cache_data(ttl=600)
def merge_data(df_main, df_mem):
    # ถ้าไม่มีข้อมูล AI ให้คืนค่าเดิมไปก่อน
    if df_mem.empty: return df_main.copy()
    
    # Copy เพื่อไม่ให้กระทบตารางหลัก
    df_main_c = df_main.copy()
    df_mem_c = df_mem.copy()
    
    # 🔥 จุดสำคัญ 1: แปลงเป็นตัวหนังสือ + ตัวพิมพ์ใหญ่ + ตัดช่องว่าง (Normalize)
    # เพื่อแก้ปัญหา "sku01" ไม่เท่ากับ "SKU01" หรือ " SKU01 "
    df_main_c['join_key'] = df_main_c['รหัสสินค้า'].astype(str).str.strip().str.upper()
    df_mem_c['join_key'] = df_mem_c['SKU'].astype(str).str.strip().str.upper()
    
    # 2. จับคู่ (Merge) ด้วยคอลัมน์พิเศษที่สร้างขึ้น (join_key)
    merged = pd.merge(df_main_c, df_mem_c, on='join_key', how='left')
    
    # 3. ถมช่องว่าง (สำคัญมาก: ถ้า AI ยังไม่รู้จัก ให้ใส่ค่าว่าง อย่าให้เป็น NaN)
    cols_to_fix = ['AI_Brand', 'AI_Type', 'AI_Spec', 'AI_Tags', 'AI_Kind'] 
    for col in cols_to_fix:
        if col in merged.columns:
            # fillna('') จะทำให้ค่าว่างเป็น string ว่างๆ ไม่ error เวลาค้นหา
            merged[col] = merged[col].fillna('').astype(str)
    
    # ลบคอลัมน์ช่วย (join_key) ทิ้ง
    if 'join_key' in merged.columns:
        del merged['join_key']
            
    return merged
# ---------------------------------------------------------
# ฟังก์ชันแกะข้อมูลสินค้า (สำหรับปุ่ม "สอน AI")
# ---------------------------------------------------------
# ---------------------------------------------------------
# ฟังก์ชันแกะข้อมูลสินค้า (ฉบับเสถียร: Retry 3 ครั้ง + พักนานขึ้น)
# ---------------------------------------------------------
def ask_gemini_extract(names):
    # ค่า Default กรณีพังจริงๆ
    default_list = []
    for _ in names:
        default_list.append({
            "AI_Brand": "Unknown", "AI_Type": "Other", 
            "AI_Kind": "", "AI_Spec": "-", "AI_Tags": ""
        })

    if not names: return []

    prompt = f"""
    Extract product info from this list:
    {json.dumps(names, ensure_ascii=False)}

    Return JSON Array with these keys:
    - AI_Brand (Use Uppercase e.g. SAMSUNG)
    - AI_Type (Category in Thai e.g. เครื่องซักผ้า, ทีวี)
    - AI_Kind (Sub-type in Thai e.g. ฝาบน, 2 ถัง. If unknown use "")
    - AI_Spec (Capacity/Size e.g. 10 kg, 55 นิ้ว)
    - AI_Tags (Features e.g. inverter, smart tv)

    Response Format: JSON Array ONLY. No Markdown.
    """
    
    # 🔥 ระบบตื้อ 3 รอบ (Retry Logic) 🔥
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # เรียก AI
            response = ai_model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    response_mime_type="application/json"
                )
            )
            
            txt = response.text.strip()
            # ล้าง Markdown
            txt_clean = re.sub(r"```json|```", "", txt).strip()
            
            data = json.loads(txt_clean)

            # เช็คความถูกต้องของข้อมูล
            normalized_data = []
            for item in data:
                new_item = {
                    "AI_Brand": item.get("AI_Brand") or "Unknown",
                    "AI_Type": item.get("AI_Type") or "Other",
                    "AI_Kind": item.get("AI_Kind") or "", 
                    "AI_Spec": item.get("AI_Spec") or "-",
                    "AI_Tags": item.get("AI_Tags") or ""
                }
                if isinstance(new_item["AI_Tags"], list):
                    new_item["AI_Tags"] = ", ".join(new_item["AI_Tags"])
                normalized_data.append(new_item)
            
            # ถ้าจำนวนข้อมูลไม่ครบ (เช่นส่งไป 10 กลับมา 5) ให้ถือว่า Error แล้วลองใหม่
            if len(normalized_data) != len(names):
                print(f"⚠️ จำนวนไม่ครบ ({len(normalized_data)}/{len(names)}) ลองใหม่...")
                raise ValueError("Data mismatch")
                
            return normalized_data # สำเร็จ! ส่งค่ากลับเลย

        except Exception as e:
            # สูตรคำนวณเวลารอ: รอบ 1=5วิ, รอบ 2=10วิ, รอบ 3=15วิ
            wait_time = (attempt + 1) * 5 
            print(f"⚠️ AI Error (รอบ {attempt+1}): {e} ... รอ {wait_time} วินาที")
            time.sleep(wait_time)
    
    # ถ้าครบ 3 รอบแล้วยังไม่ได้จริงๆ ค่อยยอมแพ้
    return default_list
# ---------------------------------------------------------
# 🔥 ฟังก์ชัน AI (โหมด DEBUG: แสดง Error ให้เห็นจะๆ)
# ---------------------------------------------------------
def ask_gemini_filter(query, columns, df_lookup=None):
    # ---------------------------------------------------------
    # PART 1: เตรียม Context (ส่งโพย Top 50 ให้ AI รู้จักสินค้าในร้าน)
    # ---------------------------------------------------------
    context_str = ""
    if df_lookup is not None:
        try:
            # เรียงตามความนิยม (Most Popular)
            brands = df_lookup['AI_Brand'].value_counts().index.tolist()
            types = df_lookup['AI_Type'].value_counts().index.tolist()
            kinds = df_lookup['AI_Kind'].value_counts().index.tolist()
            
            # Limit Token: ส่งไปแค่ตัวท็อปๆ
            brand_list = json.dumps(brands[:60], ensure_ascii=False)
            type_list = json.dumps(types[:40], ensure_ascii=False)
            kind_list = json.dumps(kinds[:60], ensure_ascii=False)
            
            context_str = f"""
            [Database Context - Use these exact values for mapping]
            - Known Brands: {brand_list}
            - Known Types: {type_list}
            - Known Kinds: {kind_list}
            """
        except: pass

    # ---------------------------------------------------------
    # PART 2: Prompt สั่งงาน (ผสานกฎเรื่องทศนิยมและช่วงตัวเลข)
    # ---------------------------------------------------------
    prompt = f"""
    Role: คุณคือ Search Engine อัจฉริยะ แปลงคำค้นหา "{query}" เป็น JSON Filter
    Target Columns: {columns}
    
    {context_str}

    Instruction (Strict Rules):
    1. **Context Mapping (สำคัญที่สุด)**: 
       - ก่อนจะตัดสินใจ ให้ดูใน [Database Context] ด้านบนก่อน
       - ถ้าคำค้นหาตรงกับ Known Brands/Types ให้ใช้คำนั้นเป๊ะๆ (เช่น User พิมพ์ "Mitsu" -> ต้องแก้เป็น "MITSUBISHI" ตามในลิสต์)
       - **Thai Splitting Rule (เพิ่ม)**: ถ้าเจอชื่อแบรนด์ภาษาไทยหลายคำเว้นวรรค (เช่น "ไฮเออร์ แอลจี") **ต้องแยก (SPLIT)** เป็น Filter หลายตัว
         * ตัวอย่าง: "ไฮเออร์ แอลจี" -> ให้สร้าง 2 filters:
           {{ "column": "AI_Brand", "operator": "contains", "value": "HAIER" }},
           {{ "column": "AI_Brand", "operator": "contains", "value": "LG" }}

    2. **Price & Spec Logic**: 
       - ถ้าเจอตัวเลขราคา ให้ใช้ 'lte' (ไม่เกิน) หรือ 'gte' (ตั้งแต่)
       - ห้ามใช้ 'contains' กับตัวเลขราคา

    3. **Decimal Range Strategy (Spec Only)**: 
       - ถ้า User ระบุช่วงขนาด/สเปค (เช่น "5.5 - 6 คิว", "9000-12000 btu") 
       - **ให้ใช้ operator 'gte' (>=) และ 'lte' (<=) กับคอลัมน์ AI_Spec**
       - ตัวอย่าง: "5.5 - 6 คิว" -> 
         {{ "column": "AI_Spec", "operator": "gte", "value": "5.5" }},
         {{ "column": "AI_Spec", "operator": "lte", "value": "6.0" }}
       - ห้ามใช้ 'contains' หรือ 'in' สำหรับช่วงตัวเลขสเปค

    4. **Single Number Spec**: 
       - ถ้าค้นหาเลขเดียว (เช่น "5 คิว") ให้ใช้ 'contains' เหมือนเดิม
       - แต่ถ้าเป็นทศนิยม (เช่น "10.5 kg") ให้ใช้ 'contains' หรือ 'eq' ที่ระบุค่า "10.5" ชัดเจน

    Output Format (JSON ONLY):
    {{
        "filters": [ ... ],
        "sort_order": "asc"
    }}
    """
    
    try:
        res = ai_model.generate_content(prompt, generation_config=genai.types.GenerationConfig(response_mime_type="application/json"))
        return json.loads(res.text.strip())
    except: return None
def clean_text(text):
    # 👇 ต้องมีการเว้นวรรค (Indentation) ตรงนี้
    if not text: return ""
    return re.sub(r'[^a-zA-Z0-9]', '', str(text)).lower()
# ---------------------------------------------------------
# 6. MAIN APP UI (TABS)
# ---------------------------------------------------------

# โหลดข้อมูล
df_main, df_mem, file_name, last_update = load_data_master()

st.title("💰 ระบบเช็คราคาสินค้า & AI")
st.caption(f"📂 ฐานข้อมูล: {file_name} | 🕒 อัปเดตล่าสุด: {last_update}")

# สร้าง TAB เมนู
tab1, tab2 = st.tabs(["🏠 เช็คราคารายตัว (Code/Name)", "🤖 ค้นหาอัจฉริยะ (AI Search)"])

# =========================================================
# TAB 1: เช็คราคารายตัว
# =========================================================
with tab1:
    st.info("💡 เหมาะสำหรับ: ค้นหาเมื่อรู้ 'รหัสสินค้า' หรือ 'ชื่อรุ่น' ที่แน่นอน")
    
    query1 = st.text_input("พิมพ์รหัสสินค้า หรือ ชื่อรุ่น", placeholder="เช่น rt20, parsr5lae (ไม่ต้องใส่ขีด)", key="search_tab1")
    
    if query1:
        match_index = -1
        found_by = ""
        
        query_clean = clean_text(query1)
        sku_clean_series = df_main['รหัสสินค้า'].astype(str).apply(clean_text)
        desc_clean_series = df_main['รายละเอียดสินค้า'].astype(str).apply(clean_text)
        
        sku_matches = df_main[sku_clean_series.str.contains(query_clean, na=False)]
        if not sku_matches.empty:
            match_index = sku_matches.index[0]
            found_by = "⚡ เจอรหัสสินค้า"
        else:
            desc_matches = df_main[desc_clean_series.str.contains(query_clean, na=False)]
            if not desc_matches.empty:
                match_index = desc_matches.index[0]
                found_by = "🔎 เจอในรายละเอียด"
            else:
                keywords = list(filter(None, re.split(r'[^a-zA-Z0-9]', query1)))
                if not keywords: keywords = [query1]
                # แก้เป็นบรรทัดนี้ครับ
                candidates = df_main[df_main.astype(str).apply(lambda x: any(k.lower() in ' '.join(x).lower() for k in keywords), axis=1)]
                
                if candidates.empty: search_pool = df_main.sample(min(len(df_main), 15))
                else: search_pool = candidates.head(30)
                
                prod_str = search_pool[['รหัสสินค้า', 'รายละเอียดสินค้า']].to_string(index=True)
                with st.spinner('🤖 AI กำลังช่วยแกะลายแทง...'):
                    try:
                        res = ai_model.generate_content(f"หา index สินค้าที่ตรงกับ '{query1}' จาก:\n{prod_str}\nตอบแค่ตัวเลข index. ถ้าไม่มี -1")
                        match_index = int(res.text.strip())
                        found_by = "🤖 AI ค้นพบ"
                    except: match_index = -1

        if match_index != -1 and match_index in df_main.index:
            item = df_main.loc[match_index]
            cost = item.get('ราคาทุนต่อหน่วย', 0)
            stock = item.get('จำนวนสต้อก', 0)
            mid = item.get('รหัสสินค้า', '-')
            name = item.get('รายละเอียดสินค้า', '-')
            brand = item.get('ยี่ห้อ', '-')

            st.success(f"{found_by}: {name}")
            
            target_margin = 12
            sell_price = cost * (1 + (target_margin/100))
            profit = sell_price - cost

            # -------------------------------------------------------
            # ✨ [แก้ไขใหม่] แบ่งเป็น 3 คอลัมน์เท่ากัน (ทุน | ขาย | สต้อก)
            # -------------------------------------------------------
            c1, c2, c3 = st.columns([1, 1, 1]) 
            
            with c1:
                st.markdown(f"""
                <div class="cost-box">
                    <div style="color:#555;font-weight:bold;">🔴 ราคาทุน</div>
                    <div class="price-value-cost">{cost:,.0f}</div>
                </div>
                """, unsafe_allow_html=True)

            with c2:
                st.markdown(f"""
                <div class="selling-box">
                    <div style="color:#555;font-weight:bold;">🟢 ราคาขาย (+{target_margin}%)</div>
                    <div class="price-value-sell">{sell_price:,.0f}</div>
                    <div style="font-size:12px; color:#2e7d32;">(กำไร {profit:,.0f} บ.)</div>
                </div>
                """, unsafe_allow_html=True)
                
            with c3:
                # ตกแต่ง: ถ้าสต้อกเป็น 0 ให้เลขเป็นสีแดง, ถ้ามีของให้เป็นสีฟ้า
                stock_color = "#c62828" if stock == 0 else "#1565c0"
                st.markdown(f"""
                <div class="stock-box">
                    <div style="color:#555;font-weight:bold;">📦 สต้อกคงเหลือ</div>
                    <div class="price-value-stock" style="color: {stock_color};">{stock:,.0f}</div>
                    <div style="font-size:12px; color:#555;">หน่วย</div>
                </div>
                """, unsafe_allow_html=True)

           # ... (โค้ดส่วนแสดงกล่อง 3 กล่อง: ทุน/ขาย/สต้อก อยู่ด้านบนเหมือนเดิม) ...

            # -------------------------------------------------------
            # ✨ [แก้ไข] 1. แถบโชว์รหัสและยี่ห้อ (เอาลิงก์ Google ออกไปแล้ว)
            # -------------------------------------------------------
            st.markdown(f"""
            <div class="detail-bar">
                <b>🆔 รหัส:</b> {mid} &nbsp;&nbsp;|&nbsp;&nbsp; 
                <b>🏷️ ยี่ห้อ:</b> {brand}
            </div>
            """, unsafe_allow_html=True)

            # -------------------------------------------------------
            # ✨ [เพิ่มใหม่] 2. ปุ่มค้นหา Google แยกออกมาต่างหาก (กดง่ายขึ้น)
            # -------------------------------------------------------
            st.write("") # เว้นช่องว่างนิดนึง
            google_q = urllib.parse.quote(name)
            st.link_button(
                "🌐 ค้นหารูป/ข้อมูลเพิ่มเติมใน Google", 
                f"https://www.google.com/search?q={google_q}", 
                use_container_width=True  # ให้ปุ่มกว้างเต็มจอ
            )
            
            # ... (หลังจากนี้เป็นเส้นกั้น st.divider() และตาราง Margin เหมือนเดิม) ...

            st.divider()
            with st.expander("ดูตาราง Margin (3% - 30%)", expanded=True):
                margins = [3, 5, 8, 10, 12, 15, 18, 25, 30]
                p_data = [{"กำไร %": f"{m}%", "ราคาขาย": f"{cost*(1+m/100):,.0f}", "กำไร (บาท)": f"{(cost*(1+m/100))-cost:,.0f}"} for m in margins]
                st.dataframe(pd.DataFrame(p_data), hide_index=True, use_container_width=True)

            st.divider()
            st.subheader("🛒 เช็คราคาคู่แข่ง (Hot Search)")
            default_search = re.sub(r'[\u0E00-\u0E7F]', '', str(mid)).strip('-').strip()
            final_kw = st.text_input("🎯 คำค้นหา:", value=default_search, key=f"hot_kw_{match_index}")
            
            if final_kw:
                enc = urllib.parse.quote(final_kw.strip())
                stores = [
                    {"name": "Shopee", "url": f"https://shopee.co.th/search?keyword={enc}"},
                    {"name": "Lazada", "url": f"https://www.lazada.co.th/catalog/?q={enc}"},
                    {"name": "HomePro", "url": f"https://www.homepro.co.th/search?q={enc}"},
                    {"name": "PowerBuy", "url": f"https://www.powerbuy.co.th/th/search/{enc}"},
                    {"name": "ThaiWatsadu", "url": f"https://www.thaiwatsadu.com/th/search/{enc}"},
                    {"name": "Big C", "url": f"https://www.bigc.co.th/search?q={enc}"},
                    {"name": "Global", "url": f"https://globalhouse.co.th/search?keyword={enc}"},
                    {"name": "Makro", "url": f"https://www.makro.pro/c/search?q={enc}"},
                    {"name": "Dohome", "url": f"https://www.dohome.co.th/search?q={enc}"}
                ]
               # ... (โค้ดส่วน stores = [...] เหมือนเดิม) ...

            # -------------------------------------------------------
            # ✅ แก้ไขใหม่: ใช้ components.html เพื่อเลี่ยง Error ของ React
            # -------------------------------------------------------
            
            # 1. เตรียม Script เปิดลิ้งค์
            js_items = [f"window.open('{s['url']}', '_blank');" for s in stores]
            js_command = "".join(js_items)

            # 2. เขียน HTML + CSS + JS รวมกันในก้อนเดียว
            html_button = f"""
            <!DOCTYPE html>
            <html>
            <head>
            <style>
                body {{ margin: 0; padding: 0; font-family: sans-serif; }}
                .mobile-launch-btn {{
                    background: linear-gradient(90deg, #ff4b4b 0%, #ff0000 100%);
                    color: white; 
                    border: none; 
                    padding: 15px 20px; 
                    border-radius: 12px; 
                    font-weight: bold; 
                    font-size: 16px;
                    cursor: pointer; 
                    width: 100%; 
                    box-shadow: 0 4px 6px rgba(0,0,0,0.2);
                    touch-action: manipulation;
                    display: block;
                }}
                .mobile-launch-btn:active {{ transform: scale(0.98); background: #d60000; }}
                .warning-text {{
                    font-size: 12px; color: #856404; background-color: #fff3cd;
                    padding: 8px; border-radius: 6px; margin-bottom: 8px;
                    border: 1px solid #ffeeba; text-align: center;
                }}
            </style>
            </head>
            <body>
                <div class="warning-text">
                    📱 <b>iPhone:</b> ต้องปิด Block Pop-ups ใน Settings > Safari ก่อน
                </div>
                
                <button class="mobile-launch-btn" onclick="{js_command}">
                    🚀 เปิด 9 แอปเทียบราคา (กดทีเดียว)
                </button>
            </body>
            </html>
            """

            # 3. แสดงผลด้วย components.html (กำหนดความสูงให้พอดีกับปุ่ม)
            components.html(html_button, height=110)

            # -------------------------------------------------------
            # จบส่วนแก้ไข (ปุ่มย่อยด้านล่างปล่อยไว้เหมือนเดิม)
            # -------------------------------------------------------
            cols = st.columns(2)
            # ...
                
            cols = st.columns(2)
            for i, s in enumerate(stores):
                    with cols[i%2]: st.link_button(f"🔍 {s['name']}", s['url'], use_container_width=True)
        else:
            if query1: st.warning(f"❌ ไม่พบสินค้า: '{query1}'")

# =========================================================
# TAB 2: ค้นหาอัจฉริยะ AI
# =========================================================
with tab2:
    st.info("💡 เหมาะสำหรับ: ค้นหาแบบประโยค เช่น 'ทีวี Samsung ไม่เกินหมื่น', 'แอร์ inverter'")
    
    # คำนวณสินค้าใหม่
    processed_skus = df_mem['SKU'].astype(str).str.strip().tolist() if not df_mem.empty else []
    new_items_df = df_main[~df_main['รหัสสินค้า'].astype(str).str.strip().isin(processed_skus)]
    new_count = len(new_items_df)
    
    # --- ส่วนจัดการสมอง AI ---
    with st.expander(f"⚙️ จัดการสมอง AI ({len(df_mem)} รายการเรียนรู้แล้ว)"):
        c_a1, c_a2 = st.columns([3, 1])
        c_a1.write(f"สินค้าใหม่ที่ AI ยังไม่รู้จัก: **{new_count}** รายการ")
        
        # ปุ่มสอน AI
        # ปุ่มสอน AI
        if new_count > 0:
            if c_a2.button("🚀 สอน AI เดี๋ยวนี้", type="primary"):
                with st.status("🤖 AI กำลังทำงาน...", expanded=True) as status:
                    
                    # 1. เตรียมข้อมูล
                    if 'ชนิด' not in new_items_df.columns: 
                        new_items_df['ชนิด'] = ''
                    
                    to_proc = new_items_df[['รหัสสินค้า', 'รายละเอียดสินค้า', 'ชนิด']].rename(
                        columns={'รหัสสินค้า':'SKU', 'รายละเอียดสินค้า':'Name', 'ชนิด':'Original_Kind'}
                    ).to_dict('records')

                    # ✅ สูตรเสถียร: Batch 10
                    BATCH = 10
                    total_batches = (len(to_proc) // BATCH) + 1
                    
                    # 2. เริ่มวนลูป
                    for i in range(0, len(to_proc), BATCH):
                        chunk = to_proc[i:i+BATCH]
                        status.write(f"⏳ กำลังประมวลผล Batch {(i//BATCH)+1}/{total_batches} ({len(chunk)} รายการ)...")
                        
                        # รวมชื่อส่ง AI
                        names_for_ai = [f"{x['Name']} {x['Original_Kind']}" for x in chunk]
                        
                        # เรียก AI (ตัว Retry 3 รอบ)
                        ai_res = ask_gemini_extract(names_for_ai)
                        
                        # เตรียมข้อมูล (บังคับ String)
                        res_save = []
                        for idx, item in enumerate(chunk):
                            ar = ai_res[idx] if idx < len(ai_res) else {}
                            res_save.append([
                                str(item['SKU']).strip(),
                                str(ar.get('AI_Brand','Unknown')),
                                str(ar.get('AI_Type','Other')),
                                str(ar.get('AI_Spec','-')),
                                str(ar.get('AI_Tags','')),
                                str(ar.get('AI_Kind',''))
                            ])
                        
                        # 3. บันทึกและล้าง Cache ทันที
                        if res_save:
                            try:
                                result = append_to_sheet(res_save)
                                if result:
                                    status.write(f"✅ บันทึก Batch {(i//BATCH)+1} สำเร็จ!")
                                    st.cache_data.clear() # ล้างความจำทันทีที่บันทึกได้
                                else:
                                    status.error("❌ บันทึกไม่สำเร็จ (Google Sheet Error)")
                                    time.sleep(5) # พักยาวหน่อยถ้า Error
                            except Exception as e:
                                status.error(f"❌ Error: {e}")
                        
                        # ✅ พัก 3 วินาที (สูตรเสถียร)
                        time.sleep(3)

                    # 4. จบการทำงาน (อยู่นอกลูป)
                    status.update(label="🎉 เสร็จสิ้นภารกิจ!", state="complete")
                    st.success("บันทึกข้อมูลเรียบร้อย! กำลังรีโหลด...")
                    time.sleep(2)
                    st.rerun()
        else:
            c_a2.button("🔄 รีโหลด", on_click=lambda: st.cache_data.clear())

        st.divider()
        st.write("🔧 **เครื่องมือดูแลรักษาฐานข้อมูล**")
        
        # ปุ่มล้างขยะ (ใส่ key กันซ้ำ และจัดย่อหน้าให้ตรง)
        if st.button("🧹 ล้างข้อมูลขยะ (ลบ AI ที่ไม่มีสินค้าจริง)", type="secondary", key="btn_cleanup_final"):
            with st.status("กำลังตรวจสอบความสะอาด...", expanded=True) as status:
                valid_skus = df_main['รหัสสินค้า'].astype(str).str.strip().str.upper().unique()
                df_mem['check_key'] = df_mem['SKU'].astype(str).str.strip().str.upper()
                
                # กรองเอาเฉพาะที่มีใน Main
                df_mem_clean = df_mem[df_mem['check_key'].isin(valid_skus)].copy()
                # ลบตัวซ้ำ
                df_mem_clean = df_mem_clean.drop_duplicates(subset=['check_key'], keep='last')
                
                del df_mem_clean['check_key']
                
                deleted_count = len(df_mem) - len(df_mem_clean)
                
                if deleted_count > 0:
                    status.write(f"🗑️ พบข้อมูลขยะ/ตัวซ้ำ {deleted_count} รายการ... กำลังลบ")
                    success = overwrite_memory_sheet(df_mem_clean)
                    if success:
                        status.update(label="✅ ลบเสร็จสิ้น!", state="complete")
                        st.cache_data.clear()
                        time.sleep(2)
                        st.rerun()
                else:
                    status.update(label="✅ ฐานข้อมูลสะอาดอยู่แล้ว", state="complete")

    st.divider()
    
    # --- ส่วนค้นหา AI ---
    # -------------------------------------------------------------
    # ส่วนค้นหา AI (ฉบับอัปเกรด: ตัดช่องว่าง + Debug Mode)
    # -------------------------------------------------------------
    
    # 1. โหลดข้อมูล (เคลียร์ Cache ถ้ารู้สึกว่าข้อมูลไม่อัปเดต)
    df_search = merge_data(df_main, df_mem)
    
    # กันเหนียว: ถ้าไม่มีคอลัมน์ AI_Kind ให้สร้างไว้ (แต่ถ้า Cache ค้าง มันจะเป็นค่าว่างนะ)
    if 'AI_Kind' not in df_search.columns:
        df_search['AI_Kind'] = ''

    col_q1, col_q2 = st.columns([4, 1])
    query2 = col_q1.text_input("พิมพ์คำค้นหาแบบธรรมชาติ", placeholder="เช่น ตู้เย็น 2 ประตู ราคาไม่เกิน 8000", key="search_tab2")
    # -------------------------------------------------------------
    # ส่วนค้นหา AI (ฉบับ Universal 100%: ใช้สเกลตัวเลขคัดกรอง)
    # -------------------------------------------------------------
    # -------------------------------------------------------------
    # 🔥 ส่วนปุ่มค้นหา (ฉบับแก้ไข: ไม่เงียบหายแน่นอน)
    # -------------------------------------------------------------
    if col_q2.button("ค้นหา AI", type="primary"):
        if query2:
            with st.spinner('🤖 AI กำลังคิด...'):
                # 1. ตั้งค่าเริ่มต้นให้ "ผ่านหมด" ไว้ก่อน (กันเหนียว)
                final_mask = pd.Series([True] * len(df_search))
                active_conds = [] 
                
                try:
                    cols_ai = ['AI_Brand', 'AI_Type', 'AI_Spec', 'AI_Tags', 'ราคาทุนต่อหน่วย', 'AI_Kind']
                    result_json = ask_gemini_filter(query2, cols_ai, df_lookup=df_search)
                    
                    # ถ้าได้ JSON กลับมา ให้เริ่มการกรอง
                    if result_json and 'filters' in result_json:
                        filters = result_json['filters']
                        sort_order = result_json.get('sort_order')
                        
                        # กำหนดคอลัมน์ที่จะค้นหาข้อความ
                        text_search_cols = ['AI_Type', 'AI_Kind', 'AI_Tags', 'AI_Brand', 'รายละเอียดสินค้า']
                        
                        from collections import defaultdict
                        grouped_filters = defaultdict(list)
                        for f in filters: grouped_filters[f['column']].append(f)

                        # --- ฟังก์ชันช่วย (Inner Functions) ---
                        def extract_numbers_universal(text):
                            try:
                                clean_text = str(text).replace(',', '')
                                nums = re.findall(r'(\d+\.?\d*)', clean_text)
                                if not nums: return []
                                return [float(n) for n in nums if n and n != '.']
                            except: return []

                        def validate_row(extracted_val, conditions):
                            if isinstance(extracted_val, list):
                                for num in extracted_val:
                                    pass_all = True
                                    for cond in conditions:
                                        op = cond['operator']
                                        limit = float(str(cond['value']).replace(',',''))
                                        if op == 'gt' and not (num > limit): pass_all = False; break
                                        if op == 'gte' and not (num >= limit): pass_all = False; break
                                        if op == 'lt' and not (num < limit): pass_all = False; break
                                        if op == 'lte' and not (num <= limit): pass_all = False; break
                                    if pass_all: return True
                                return False
                            else:
                                num = extracted_val
                                for cond in conditions:
                                    op = cond['operator']
                                    limit = float(str(cond['value']).replace(',',''))
                                    if op == 'gt' and not (num > limit): return False
                                    if op == 'gte' and not (num >= limit): return False
                                    if op == 'lt' and not (num < limit): return False
                                    if op == 'lte' and not (num <= limit): return False
                                return True

                       # --- เริ่มวางตรงนี้ (ต่อจาก return True ของ validate_row) ---

                        # --- เริ่มวนลูปกรอง (Logic ใหม่: แยก Range=AND, Text=OR) ---
                        for col_ai_suggested, conditions in grouped_filters.items():
                            if col_ai_suggested not in df_search.columns: continue

                            # 1. แยกประเภทเงื่อนไข
                            numeric_conds = [f for f in conditions if f['operator'] in ['gt', 'gte', 'lt', 'lte']]
                            choice_conds = [f for f in conditions if f['operator'] not in ['gt', 'gte', 'lt', 'lte']]
                            
                            vals_log = [] 

                            # A. กรองตัวเลขช่วง (AND)
                            range_mask = pd.Series([True] * len(df_search))
                            if numeric_conds:
                                if col_ai_suggested == 'AI_Spec':
                                     vals = df_search[col_ai_suggested].apply(extract_numbers_universal)
                                else:
                                     vals = df_search[col_ai_suggested].apply(lambda x: extract_numbers_universal(x))
                                range_mask = vals.apply(lambda x: validate_row(x, numeric_conds))

                            # B. กรองข้อความ/ยี่ห้อ (OR)
                            choice_mask = pd.Series([True] * len(df_search))
                            if choice_conds:
                                choice_mask = pd.Series([False] * len(df_search))
                                for f in choice_conds:
                                    t_val = str(f['value']).lower().strip().replace(" ", "")
                                    found_any = pd.Series([False] * len(df_search))
                                    for sc in text_search_cols:
                                        if sc in df_search.columns:
                                            d_clean = df_search[sc].fillna('').astype(str).str.lower().str.replace(" ", "")
                                            found_any |= d_clean.str.contains(t_val, na=False)
                                    choice_mask |= found_any
                                    vals_log.append(f"{t_val}")

                            # รวมผลลัพธ์สุดท้าย
                            final_mask &= (range_mask & choice_mask)
                            
                            if numeric_conds: active_conds.append(f"Range({col_ai_suggested})")
                            if choice_conds:  active_conds.append(f"Text({','.join(vals_log)})")
                        
                        # --- จบการวางตรงนี้ (บรรทัดต่อไปต้องเป็น else:) ---

                    else:
                        # กรณี AI ไม่ตอบ JSON (Fallback) -> หาแบบธรรมดา
                        final_mask = df_search.astype(str).apply(lambda x: x.str.contains(query2, case=False)).any(axis=1)
                        active_conds.append("Keyword Search (Fallback)")

                except Exception as e:
                    st.error(f"เกิดข้อผิดพลาด: {e}")
                    # ถ้า Error ให้แสดงทั้งหมดไปเลย จะได้รู้ว่าโค้ดยังวิ่งอยู่
                    final_mask = pd.Series([True] * len(df_search))

                # ---------------------------------------------------------
                # ส่วนแสดงผล (อยู่นอก try/except เพื่อให้ทำงานเสมอ)
                # ---------------------------------------------------------
                results = df_search[final_mask]
                
                # Debug เล็กๆ: ถ้าไม่เจอ ให้บอกว่า mask เหลือ 0
                if results.empty:
                    st.warning(f"❌ ไม่พบสินค้าตามเงื่อนไข: {'; '.join(active_conds)}")
                    st.caption("🔍 คำแนะนำ: ลองลดเงื่อนไข หรือใช้คำค้นหาที่กว้างขึ้น")
                else:
                    st.success(f"✅ พบ {len(results)} รายการ (เงื่อนไข: {'; '.join(active_conds)})")
                    st.dataframe(
                        results[['รหัสสินค้า', 'รายละเอียดสินค้า', 'ราคาทุนต่อหน่วย', 'จำนวนสต้อก', 'AI_Brand', 'AI_Spec', 'AI_Kind', 'AI_Tags']],
                        column_config={
                            "ราคาทุนต่อหน่วย": st.column_config.NumberColumn("ราคาทุน", format="฿%d"), 
                            "จำนวนสต้อก": st.column_config.ProgressColumn("สต้อก", format="%d", max_value=100)
                        },
                        use_container_width=True, hide_index=True
                    )
