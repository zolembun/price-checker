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
            model = genai.GenerativeModel('gemini-2.0-flash')
        except:
            # Fallback ถ้า latest ใช้ไม่ได้ ให้ลองตัวธรรมดาหรือ Pro
            model = genai.GenerativeModel('gemini-2.0-flash')
        
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
            res_mem = sheets_svc.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range="AI_Memory!A:E").execute()
            vals_mem = res_mem.get('values', [])
            
            if vals_mem and len(vals_mem) > 1:
                headers = vals_mem[0]
                rows = vals_mem[1:]
                fixed_rows = [r + [None]*(len(headers)-len(r)) for r in rows]
                df_mem = pd.DataFrame(fixed_rows, columns=headers)
            else:
                df_mem = pd.DataFrame(columns=['SKU', 'AI_Brand', 'AI_Type', 'AI_Spec', 'AI_Tags'])
        except:
            df_mem = pd.DataFrame(columns=['SKU', 'AI_Brand', 'AI_Type', 'AI_Spec', 'AI_Tags'])

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
def ask_gemini_extract(names):
    prompt = f"""
    Role: Product Data Expert.
    Task: Extract attributes from product names.
    Input List: {json.dumps(names, ensure_ascii=False)}
    
    Strict Rules:
    1. **Fix Messy Text**: Separate glued words (e.g., "Refrigerator5.2" -> "Refrigerator" + "5.2").
    2. **Standardize Type**: 'AI_Type' MUST be in Thai (e.g., "Refrigerator" -> "ตู้เย็น", "TV" -> "ทีวี").
    3. **Extract Kind (NEW)**: 'AI_Kind' is Sub-Category/Feature.
       - Fridge: 1 ประตู, 2 ประตู, Side by Side
       - Washer: ฝาบน, ฝาหน้า, 2 ถัง
       - Air: Inverter, Fixed Speed, แขวน, ติดผนัง
    4. **Extract Spec**: Numbers for size/capacity (Q, kg, BTU).
    Output JSON Array ONLY:
   Output JSON Array ONLY:
    [
      {{
        "AI_Brand": "Brand",
        "AI_Type": "Main Category (Thai)",
        "AI_Kind": "Sub Type (Thai) or empty string", 
        "AI_Spec": "Spec",
        "AI_Tags": "Keywords"
      }}
    ]
    """
    
    try:
        response = ai_model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json"
            )
        )
        
        # Clean & Parse
        data = json.loads(response.text.strip())
        
        normalized_data = []
        for item in data:
            new_item = {
                "AI_Brand": item.get("AI_Brand") or "Unknown",
                "AI_Type": item.get("AI_Type") or "Other",
                "AI_Kind": item.get("AI_Kind") or "",  # <--- เพิ่มช่องนี้
                "AI_Spec": item.get("AI_Spec") or "-",
                "AI_Tags": item.get("AI_Tags") or ""
            }
            # แปลง Tags เป็น string ถ้ามาเป็น list
            if isinstance(new_item["AI_Tags"], list):
                new_item["AI_Tags"] = ", ".join(new_item["AI_Tags"])
                
            normalized_data.append(new_item)
            
        return normalized_data

    except Exception as e:
        print(f"AI Error: {e}")
        return []
def ask_gemini_filter(query, columns):
    # ปรับ Prompt ให้ฉลาดขึ้น รู้จักแยก "ชนิด" (1 ประตู) ออกจาก "ประเภท" (ตู้เย็น)
    prompt = f"""
    Role: คุณคือ Search Engine อัจฉริยะ แปลงคำค้นหา "{query}" เป็น JSON Filter
    Columns: {columns}
    
    Instruction (Strict Rules):
    1. **Category vs Kind Strategy (สำคัญมาก!)**:
       - ให้แยกแยะ "ประเภทหลัก" (AI_Type) กับ "ลักษณะย่อย/ชนิด" (AI_Kind)
       - ถ้า User พิมพ์ "ตู้เย็น" เฉยๆ -> กรองแค่ AI_Type="ตู้เย็น"
       - ถ้า User พิมพ์ "ตู้เย็น 1 ประตู" -> ต้องกรองทั้ง AI_Type="ตู้เย็น" **และ** AI_Kind="1 ประตู"
       - ตัวอย่าง: "เครื่องซักผ้า ฝาบน" -> AI_Type="เครื่องซักผ้า", AI_Kind="ฝาบน"
       - ตัวอย่าง: "แอร์ Inverter" -> AI_Type="แอร์", AI_Kind="Inverter"
    
    2. **Decimal Range Strategy**: 
       - หากเจอช่วงที่มีทศนิยม (เช่น "5.2 - 7.3 คิว") 
       - **ให้แปลงเป็นเลขจำนวนเต็ม (Integer) ทุกตัวที่ครอบคลุมช่วงนั้น**
       - ตัวอย่าง: "5.2 - 7.3" -> value: ["5", "6", "7"] 
       
    3. **Price Logic**: 
       - ห้ามกรองราคาถ้าไม่มีตัวเลข
       - มีตัวเลข -> ใช้ lte (ไม่เกิน), gte (ตั้งแต่)
    
    Output Format (JSON):
    {{
        "filters": [
            {{ "column": "AI_Type", "operator": "contains", "value": "ตู้เย็น" }},
            {{ "column": "AI_Kind", "operator": "contains", "value": "1 ประตู" }},
            {{ "column": "AI_Spec", "operator": "contains", "value": "5" }}
        ],
        "sort_order": "asc"
    }}
    """
    try:
        res = ai_model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json"
            )
        )
        return json.loads(res.text.strip())
    except: return None
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
                candidates = df_main[df_main.astype(str).apply(lambda x: any(k.lower() in x.lower() for k in keywords), axis=1)]
                
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
            final_kw = st.text_input("🎯 คำค้นหา:", value=default_search, key="hot_kw")
            
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
    
    processed_skus = df_mem['SKU'].astype(str).str.strip().tolist() if not df_mem.empty else []
    new_items_df = df_main[~df_main['รหัสสินค้า'].astype(str).str.strip().isin(processed_skus)]
    new_count = len(new_items_df)
    
    with st.expander(f"⚙️ จัดการสมอง AI ({len(df_mem)} รายการเรียนรู้แล้ว)"):
        c_a1, c_a2 = st.columns([3, 1])
        c_a1.write(f"สินค้าใหม่ที่ AI ยังไม่รู้จัก: **{new_count}** รายการ")
        
        # --- ส่วนปุ่มสอน AI (แก้ไข Indent เรียบร้อย) ---
        if new_count > 0:
            if c_a2.button("🚀 สอน AI เดี๋ยวนี้", type="primary"):
                with st.status("🤖 AI กำลังเรียนรู้...", expanded=True) as status:
                    
                    # 1. เช็คคอลัมน์ชนิด
                    if 'ชนิด' not in new_items_df.columns:
                        new_items_df['ชนิด'] = ''
                        
                    # 2. เตรียมข้อมูล (ชื่อ + ชนิดเดิม)
                    to_proc = new_items_df[['รหัสสินค้า', 'รายละเอียดสินค้า', 'ชนิด']].rename(
                        columns={'รหัสสินค้า':'SKU', 'รายละเอียดสินค้า':'Name', 'ชนิด':'Original_Kind'}
                    ).to_dict('records')

                    BATCH = 10
                    res_save = []
                    total_batches = (len(to_proc) // BATCH) + 1
                    
                    for i in range(0, len(to_proc), BATCH):
                        chunk = to_proc[i:i+BATCH]
                        status.write(f"Batch {(i//BATCH)+1}/{total_batches} ({len(chunk)} รายการ)...")
                        
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

        # --- ส่วนปุ่มล้างขยะ (รวมอยู่ใน Expander เดียวกัน) ---
        st.divider()
        st.write("🔧 **เครื่องมือดูแลรักษาฐานข้อมูล**")
        
        if st.button("🧹 ล้างข้อมูลขยะ (ลบ AI ที่ไม่มีสินค้าจริง)", type="secondary"):
            with st.status("กำลังตรวจสอบความสะอาด...", expanded=True) as status:
                valid_skus = df_main['รหัสสินค้า'].astype(str).str.strip().str.upper().unique()
                df_mem['check_key'] = df_mem['SKU'].astype(str).str.strip().str.upper()
                
                df_mem_clean = df_mem[df_mem['check_key'].isin(valid_skus)].copy()
                df_mem_clean = df_mem_clean.drop_duplicates(subset=['check_key'], keep='last')
                del df_mem_clean['check_key']
                
                deleted_count = len(df_mem) - len(df_mem_clean)
                
                if deleted_count > 0:
                    status.write(f"🗑️ พบข้อมูลขยะ {deleted_count} รายการ... กำลังลบ")
                    success = overwrite_memory_sheet(df_mem_clean)
                    if success:
                        status.update(label="✅ ลบเสร็จสิ้น!", state="complete")
                        st.cache_data.clear()
                        time.sleep(2)
                        st.rerun()
                else:
                    status.update(label="✅ ฐานข้อมูลสะอาดอยู่แล้ว", state="complete")

            # ========================================================
        # 🔥 ส่วนที่เพิ่มใหม่: ปุ่มล้างขยะ (วางต่อท้าย แต่อยู่ใน expander)
        # ========================================================
        st.divider()
        st.write("🔧 **เครื่องมือดูแลรักษาฐานข้อมูล**")
        
        if st.button("🧹 ล้างข้อมูลขยะ (ลบ AI ที่ไม่มีสินค้าจริง)", type="secondary"):
            with st.status("กำลังตรวจสอบความสะอาด...", expanded=True) as status:
                # 1. หาสินค้าที่มีอยู่จริงใน Main
                valid_skus = df_main['รหัสสินค้า'].astype(str).str.strip().str.upper().unique()
                
                # 2. กรอง df_mem ให้เหลือเฉพาะที่มีใน valid_skus
                # สร้าง column ชั่วคราวเพื่อเทียบ
                df_mem['check_key'] = df_mem['SKU'].astype(str).str.strip().str.upper()

                # -------------------------------------------------------
                # 🔥 แก้ไขจุดนี้: กรองสินค้าที่มีจริง AND ลบตัวที่ SKU ซ้ำกัน
                # -------------------------------------------------------
                
                # Step A: เอาเฉพาะที่มีใน Main (เหมือนเดิม)
                df_mem_clean = df_mem[df_mem['check_key'].isin(valid_skus)].copy()
                
                # Step B: ลบตัวซ้ำ (เพิ่มใหม่!) 
                # keep='last' หมายถึงถ้าเจอซ้ำ ให้เก็บตัวล่าสุดไว้ (เผื่อมีการอัปเดตข้อมูลใหม่กว่า)
                df_mem_clean = df_mem_clean.drop_duplicates(subset=['check_key'], keep='last')
                
                # -------------------------------------------------------
                
                # คัดเอาเฉพาะที่ key ตรงกัน
                df_mem_clean = df_mem[df_mem['check_key'].isin(valid_skus)].copy()
                
                # ลบ column ช่วยเหลือทิ้ง
                del df_mem_clean['check_key']
                
                deleted_count = len(df_mem) - len(df_mem_clean)
                
                if deleted_count > 0:
                    status.write(f"🗑️ พบข้อมูลขยะ {deleted_count} รายการ... กำลังลบ")
                    # เรียกใช้ฟังก์ชัน overwrite ที่เราสร้างไว้ข้างบน
                    success = overwrite_memory_sheet(df_mem_clean) 
                    if success:
                        status.update(label=f"✅ ลบเสร็จสิ้น! (เหลือ {len(df_mem_clean)} รายการ)", state="complete")
                        st.cache_data.clear()
                        time.sleep(2)
                        st.rerun()
                else:
                    status.update(label="✅ ฐานข้อมูลสะอาดอยู่แล้ว ไม่ต้องลบ", state="complete")

    st.divider()
    
    df_search = merge_data(df_main, df_mem)
    
    col_q1, col_q2 = st.columns([4, 1])
    query2 = col_q1.text_input("พิมพ์คำค้นหาแบบธรรมชาติ", placeholder="เช่น ตู้เย็น 2 ประตู ราคาไม่เกิน 8000", key="search_tab2")
   # วางต่อจากบรรทัด: query2 = col_q1.text_input(...)
if col_q2.button("ค้นหา AI", type="primary"):
        with st.spinner('🤖 AI กำลังคิด...'):
                # 1. เพิ่ม AI_Kind เข้าไปในลิสต์คอลัมน์
                cols_ai = ['AI_Brand', 'AI_Type', 'AI_Spec', 'AI_Tags', 'ราคาทุนต่อหน่วย', 'AI_Kind']
                result_json = ask_gemini_filter(query2, cols_ai)
                
                if result_json and 'filters' in result_json:
                    filters = result_json['filters']
                    sort_order = result_json.get('sort_order')
                    
                    final_mask = pd.Series([True] * len(df_search))
                    active_conds = []
                    
                    from collections import defaultdict
                    grouped_filters = defaultdict(list)
                    for f in filters:
                        grouped_filters[f['column']].append(f)
                    
                    try:
                        for col, conditions in grouped_filters.items():
                            if col not in df_search.columns: continue
                            
                            col_mask = pd.Series([False] * len(df_search))
                            vals_log = []
                            
                            for f in conditions:
                                op = f['operator']
                                raw_val = f['value']
                                values_list = raw_val if isinstance(raw_val, list) else [raw_val]
                                
                                for val in values_list:
                                    if col == 'ราคาทุนต่อหน่วย':
                                        s_val = pd.to_numeric(df_search[col], errors='coerce').fillna(0)
                                        val = float(val)
                                    else:
                                        s_val = df_search[col].astype(str)
                                        # ตัดทศนิยม .0
                                        try:
                                            if isinstance(val, (int, float)) and val == int(val):
                                                val = str(int(val))
                                            else:
                                                val = str(val)
                                        except: val = str(val)

                                    if op == 'contains': sub_mask = s_val.str.contains(val, case=False, na=False)
                                    elif op == 'equals': sub_mask = (s_val == val)
                                    elif op == 'gt': sub_mask = (s_val > val)
                                    elif op == 'gte': sub_mask = (s_val >= val)
                                    elif op == 'lt': sub_mask = (s_val < val)
                                    elif op == 'lte': sub_mask = (s_val <= val)
                                    else: sub_mask = pd.Series([False] * len(df_search))
                                    
                                    col_mask |= sub_mask
                                    vals_log.append(f"{val}")
                            
                            final_mask &= col_mask
                            active_conds.append(f"{col}: {'|'.join(vals_log)}")
                        
                        results = df_search[final_mask]
                        
                        if not results.empty and sort_order:
                            if sort_order == 'asc': results = results.sort_values(by='ราคาทุนต่อหน่วย', ascending=True)
                            elif sort_order == 'desc': results = results.sort_values(by='ราคาทุนต่อหน่วย', ascending=False)

                        if not results.empty:
                            st.success(f"✅ พบ {len(results)} รายการ")
                            
                            # 2. เพิ่ม AI_Kind เข้าไปในตารางแสดงผล
                            st.dataframe(
                                results[['รหัสสินค้า', 'รายละเอียดสินค้า', 'ราคาทุนต่อหน่วย', 'จำนวนสต้อก', 'AI_Brand', 'AI_Spec', 'AI_Kind']],
                                column_config={
                                    "ราคาทุนต่อหน่วย": st.column_config.NumberColumn("ราคาทุน", format="฿%d"), 
                                    "จำนวนสต้อก": st.column_config.ProgressColumn("สต้อก", format="%d", max_value=int(df_search['จำนวนสต้อก'].max()))
                                },
                                use_container_width=True, hide_index=True
                            )
                        else: 
                            st.warning(f"❌ ไม่พบสินค้า (เงื่อนไข: {'; '.join(active_conds)})")
                            
                    except Exception as e: st.error(f"Error: {e}")
                else:
                    simple = df_search.astype(str).apply(lambda x: x.str.contains(query2, case=False)).any(axis=1)
                    st.dataframe(df_search[simple], use_container_width=True)
