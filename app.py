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
            model = genai.GenerativeModel('gemini-2.0-flash-lite-preview-02-05')
        except:
            # Fallback ถ้า latest ใช้ไม่ได้ ให้ลองตัวธรรมดาหรือ Pro
            model = genai.GenerativeModel('gemini-2.0-flash-lite-preview-02-05')
        
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
# 🔥 ฟังก์ชัน AI (Force JSON + Debug)
# ---------------------------------------------------------
# ---------------------------------------------------------
# 🔥 ฟังก์ชัน AI (เวอร์ชั่นนักแกะรอยขั้นเทพ)
# ---------------------------------------------------------
# ---------------------------------------------------------
# 🔥 ฟังก์ชัน AI (เวอร์ชั่นนักแกะรอยขั้นเทพ - อัปเกรด Prompt)
# ---------------------------------------------------------
# ---------------------------------------------------------
# 🔥 ฟังก์ชัน AI (เวอร์ชั่นกันกระสุน - แก้ Error JSON)
# ---------------------------------------------------------
# ---------------------------------------------------------
# 🔥 ฟังก์ชัน AI (เวอร์ชั่น Ultra-Safe: ไม่พังแน่นอน)
# ---------------------------------------------------------
# ---------------------------------------------------------
# 🔥 ฟังก์ชัน AI (โหมด DEBUG: แสดง Error ให้เห็นจะๆ)
# ---------------------------------------------------------
def ask_gemini_extract(names):
    # เตรียมค่า Default ไว้
    default_list = []
    for _ in names:
        default_list.append({
            "AI_Brand": "Unknown", "AI_Type": "Other", 
            "AI_Kind": "", "AI_Spec": "-", "AI_Tags": ""
        })

    if not names: return []

    # Prompt สั่งงาน
    prompt = f"""
    Extract product info from this list:
    {json.dumps(names, ensure_ascii=False)}

    Return JSON Array with these keys:
    - AI_Brand
    - AI_Type (Category in Thai)
    - AI_Kind (Sub-type in Thai e.g. 1 ประตู, ฝาบน. If unknown use "")
    - AI_Spec (Capacity/Size)
    - AI_Tags

    Response Format: JSON Array ONLY. No Markdown.
    """
    
    try:
        # เรียก AI
        response = ai_model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json"
            )
        )
        
        txt = response.text.strip()
        
        # ---------------------------------------------------
        # 🕵️‍♀️ ส่วน DEBUG: แสดงผลลัพธ์ดิบๆ บนหน้าจอ
        # ---------------------------------------------------
        with st.expander(f"🔍 X-Ray: ดูสิ่งที่ AI ตอบมา (Batch นี้)", expanded=True):
            if not txt:
                st.error("❌ AI ส่งค่าว่างกลับมา (Empty Response)")
            else:
                st.code(txt, language='json') # โชว์ข้อความที่ AI ส่งมา
        # ---------------------------------------------------

        # ล้าง Markdown ออก (ถ้ามี)
        txt_clean = re.sub(r"```json|```", "", txt).strip()
        
        try:
            data = json.loads(txt_clean)
        except json.JSONDecodeError as json_err:
            st.error(f"💥 แปลง JSON ไม่ได้: {json_err}")
            return default_list

        # จัดระเบียบข้อมูล
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
            
        return normalized_data

    except Exception as e:
        # แสดง Error ตัวแดงๆ บนหน้าจอ
        st.error(f"☠️ Critical Error: {e}")
        return default_list
def ask_gemini_filter(query, columns):
    # Prompt ปรับปรุงใหม่: รองรับช่วงตัวเลขสเปค (Spec Range)
    prompt = f"""
    Role: คุณคือ Search Engine อัจฉริยะ แปลงคำค้นหา "{query}" เป็น JSON Filter
    Columns: {columns}
    
    Instruction (Strict Rules):
    1. **Category/Kind**: แยก AI_Type (ประเภท) กับ AI_Kind (ชนิด) ให้ชัดเจน
    2. **Price Logic**: ถ้ามีตัวเลขราคา ให้ใช้ lte (ไม่เกิน), gte (ตั้งแต่)
    
    3. **Decimal Range Strategy (Spec Only)**: 
       - ถ้า User ระบุช่วงขนาด/สเปค (เช่น "5.5 - 6 คิว", "9000-12000 btu") 
       - **ให้ใช้ operator 'gte' (>=) และ 'lte' (<=) กับคอลัมน์ AI_Spec**
       - ตัวอย่าง: "5.5 - 6 คิว" -> 
         {{ "column": "AI_Spec", "operator": "gte", "value": "5.5" }},
         {{ "column": "AI_Spec", "operator": "lte", "value": "6.0" }}
       - ห้ามใช้ 'contains' หรือ 'in' สำหรับช่วงตัวเลขสเปค
       
    4. **Single Number Spec**: ถ้าค้นหาเลขเดียว (เช่น "5 คิว") ให้ใช้ 'contains' เหมือนเดิม
    
    Output Format (JSON):
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

            c1, c2, c3 = st.columns([1.3, 1.3, 1])
            with c1:
                st.markdown(f"""<div class="cost-box"><div style="color:#555;font-weight:bold;">🔴 ราคาทุน</div><div class="price-value-cost">{cost:,.0f}</div></div>""", unsafe_allow_html=True)
            with c2:
                st.markdown(f"""<div class="selling-box"><div style="color:#555;font-weight:bold;">🟢 ขายแนะนำ (+{target_margin}%)</div><div class="price-value-sell">{sell_price:,.0f}</div><div style="color:#1b5e20;">กำไร {profit:,.0f} บาท</div></div>""", unsafe_allow_html=True)
            with c3:
                st.markdown(f"""<div class="info-box"><b>🆔 รหัส:</b> {mid}<br><b>📦 สต้อก:</b> {stock}<br><b>🏷️ ยี่ห้อ:</b> {brand}</div>""", unsafe_allow_html=True)
                
                st.write("")
                g_q = urllib.parse.quote(name)
                st.link_button("🌐 ค้นหารูป/ข้อมูลใน Google", f"https://www.google.com/search?q={g_q}", use_container_width=True)

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
        if new_count > 0:
            if c_a2.button("🚀 สอน AI เดี๋ยวนี้", type="primary"):
                with st.status("🤖 AI กำลังเรียนรู้...", expanded=True) as status:
                    
                    # 1. เช็คคอลัมน์ชนิด (ถ้าไม่มีสร้างใหม่)
                    if 'ชนิด' not in new_items_df.columns:
                        new_items_df['ชนิด'] = ''
                        
                    # 2. เตรียมข้อมูล (ชื่อ + ชนิดเดิม)
                    to_proc = new_items_df[['รหัสสินค้า', 'รายละเอียดสินค้า', 'ชนิด']].rename(
                        columns={'รหัสสินค้า':'SKU', 'รายละเอียดสินค้า':'Name', 'ชนิด':'Original_Kind'}
                    ).to_dict('records')

                    BATCH = 30
                    res_save = []
                    total_batches = (len(to_proc) // BATCH) + 1
                    
                    for i in range(0, len(to_proc), BATCH):
                        chunk = to_proc[i:i+BATCH]
                        status.write(f"Batch {(i//BATCH)+1}/{total_batches} ({len(chunk)} รายการ)...")
                        
                        # รวมชื่อ + ชนิด ส่งให้ AI
                        names_for_ai = [f"{x['Name']} {x['Original_Kind']}" for x in chunk]
                        ai_res = ask_gemini_extract(names_for_ai)
                        
                        for idx, item in enumerate(chunk):
                            ar = ai_res[idx] if idx < len(ai_res) else {}
                            res_save.append([
                                item['SKU'], 
                                ar.get('AI_Brand','Unknown'), 
                                ar.get('AI_Type','Other'), 
                                ar.get('AI_Spec','-'), 
                                ar.get('AI_Tags',''),
                                ar.get('AI_Kind','') 
                            ])
                        time.sleep(4)
                    
                    if res_save:
                        append_to_sheet(res_save)
                        status.update(label="เสร็จสิ้น!", state="complete")
                        st.balloons()
                        st.cache_data.clear()
                        time.sleep(1)
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
    if col_q2.button("ค้นหา AI", type="primary"):
        if query2:
            with st.spinner('🤖 AI กำลังคิด...'):
                cols_ai = ['AI_Brand', 'AI_Type', 'AI_Spec', 'AI_Tags', 'ราคาทุนต่อหน่วย', 'AI_Kind']
                result_json = ask_gemini_filter(query2, cols_ai)
                
                with st.expander("🕵️ Debug: ดูเบื้องหลังการคิด"):
                    st.json(result_json)

                if result_json and 'filters' in result_json:
                    filters = result_json['filters']
                    sort_order = result_json.get('sort_order')
                    
                    final_mask = pd.Series([True] * len(df_search))
                    active_conds = []
                    
                    from collections import defaultdict
                    grouped_filters = defaultdict(list)
                    for f in filters:
                        grouped_filters[f['column']].append(f)
                    
                    # --- เริ่มต้นส่วนที่ Copy ไปวาง ---
                    try:
                        # กำหนดคอลัมน์ที่จะค้นหาแบบข้อความ (เพิ่ม AI_Tags แล้ว)
                        text_search_cols = ['AI_Type', 'AI_Kind', 'AI_Tags', 'AI_Brand', 'รายละเอียดสินค้า']

                        # ฟังก์ชันแกะตัวเลข (Universal)
                        def extract_numbers_universal(text):
                            try:
                                clean_text = str(text).replace(',', '')
                                nums = re.findall(r'(\d+\.?\d*)', clean_text)
                                if not nums: return []
                                return [float(n) for n in nums if n and n != '.']
                            except: return []

                        # ฟังก์ชันตรวจสอบเงื่อนไขตัวเลข
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

                        # เริ่มวนลูปกรองข้อมูล
                        for col_ai_suggested, conditions in grouped_filters.items():
                            if col_ai_suggested not in df_search.columns: continue

                            # แยกเงื่อนไข ตัวเลข vs ข้อความ
                            numeric_conds = [f for f in conditions if f['operator'] in ['gt', 'gte', 'lt', 'lte']]
                            choice_conds = [f for f in conditions if f['operator'] not in ['gt', 'gte', 'lt', 'lte']]
                            
                            vals_log = [] # เก็บ Log การค้นหา

                            # -------------------------------------------------
                            # A. จัดการเงื่อนไขตัวเลข (Spec, Price) -> เช็คตรงคอลัมน์เดิม (Strict)
                            # -------------------------------------------------
                            range_mask = pd.Series([True] * len(df_search))
                            if numeric_conds:
                                if col_ai_suggested == 'AI_Spec':
                                     vals_extracted = df_search[col_ai_suggested].apply(extract_numbers_universal)
                                else:
                                     vals_extracted = df_search[col_ai_suggested].apply(lambda x: extract_numbers_universal(x))
                                
                                range_mask = vals_extracted.apply(lambda x: validate_row(x, numeric_conds))

                            # -------------------------------------------------
                            # B. จัดการเงื่อนไขข้อความ -> เช็คทุกคอลัมน์ + กัน Error ช่องว่าง 🔥
                            # -------------------------------------------------
                            choice_mask = pd.Series([True] * len(df_search))
                            
                            if choice_conds:
                                # เริ่มต้นถือว่าไม่เจอ (False) จนกว่าจะหาเจอ
                                choice_mask = pd.Series([False] * len(df_search))
                                
                                for f in choice_conds:
                                    # แปลงคำค้นหาเป็นตัวเล็ก + ตัดเว้นวรรค
                                    target_val = str(f['value']).lower().strip().replace(" ", "")
                                    
                                    # ตัวแปรเก็บผลลัพธ์ว่า "เจอในคอลัมน์ไหนก็ได้"
                                    found_in_any_col = pd.Series([False] * len(df_search))
                                    
                                    for search_col in text_search_cols:
                                        if search_col in df_search.columns:
                                            # 🔥 จุดแก้ Error: .fillna('') คือถ้าช่องว่าง ให้เป็นข้อความเปล่า อย่าให้เป็น NaN
                                            col_data_clean = df_search[search_col].fillna('').astype(str).str.lower().str.replace(" ", "")
                                            
                                            # ค้นหาคำ (na=False คือถ้าข้อมูลพัง ให้ถือว่าหาไม่เจอ แต่อย่า Error)
                                            found_in_any_col |= col_data_clean.str.contains(target_val, na=False)
                                    
                                    # Logic AND: ถ้า User หาหลายคำ ต้องเจอครบทุกคำ
                                    if f is choice_conds[0]:
                                        choice_mask = found_in_any_col
                                    else:
                                        choice_mask &= found_in_any_col
                                        
                                    vals_log.append(f"{target_val}")

                            # รวมผลลัพธ์ (AND Logic ระหว่าง เลข และ ข้อความ)
                            final_mask &= (range_mask & choice_mask)
                            
                            # Log เพื่อดูผลลัพธ์หลังบ้าน
                            log_text = ""
                            if numeric_conds: log_text += f"NumCheck({col_ai_suggested}) "
                            if choice_conds:  log_text += f"TextCheck(Match '{'|'.join(vals_log)}' in {text_search_cols})"
                            active_conds.append(log_text)

                    except Exception as e:
                        st.error(f"Filter Logic Error: {e}")
                    # --- จบส่วนที่ Copy ---
                            
                    except Exception as e: st.error(f"Error: {e}")
                else:
                    simple = df_search.astype(str).apply(lambda x: x.str.contains(query2, case=False)).any(axis=1)
                    st.dataframe(df_search[simple], use_container_width=True)
