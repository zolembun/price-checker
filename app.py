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

@st.cache_data(ttl=600)
def merge_data(df_main, df_mem):
    if df_mem.empty: return df_main.copy()
    df_main_c = df_main.copy()
    df_mem_c = df_mem.copy()
    df_main_c['รหัสสินค้า'] = df_main_c['รหัสสินค้า'].astype(str).str.strip()
    df_mem_c['SKU'] = df_mem_c['SKU'].astype(str).str.strip()
    return pd.merge(df_main_c, df_mem_c, left_on='รหัสสินค้า', right_on='SKU', how='left')

# Helper Functions
def clean_text(text):
    if not isinstance(text, str): text = str(text)
    return re.sub(r'[^a-zA-Z0-9ก-๙]', '', text).lower()

# ---------------------------------------------------------
# 🔥 ฟังก์ชัน AI (Force JSON + Debug)
# ---------------------------------------------------------
# วางทับฟังก์ชัน def ask_gemini_extract(names): เดิม
def ask_gemini_extract(names):
    prompt = f"""
    Analyze product names. Return ONLY JSON Array.
    Input: {json.dumps(names, ensure_ascii=False)}
    Example: [{{"AI_Brand": "Samsung", "AI_Type": "TV", "AI_Spec": "55 Inch", "AI_Tags": "4K"}}]
    If unknown, use "Unknown" or "-".
    """
    try:
        response = ai_model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(response_mime_type="application/json")
        )
        data = json.loads(response.text.strip())
        
        normalized_data = []
        for item in data:
            new_item = {
                "AI_Brand": item.get("AI_Brand") or item.get("Brand") or "Unknown",
                "AI_Type": item.get("AI_Type") or item.get("Type") or "Other",
                "AI_Spec": item.get("AI_Spec") or item.get("Spec") or "-",
                "AI_Tags": item.get("AI_Tags") or item.get("Tags") or ""
            }
            if isinstance(new_item["AI_Tags"], list):
                new_item["AI_Tags"] = ", ".join(new_item["AI_Tags"])
            normalized_data.append(new_item)
            
        return normalized_data
    except: return []

def ask_gemini_filter(query, columns):
    prompt = f"""
    Role: Search Engine. Convert "{query}" to JSON. Cols: {columns}
    
    Rules:
    1. **Primary**: Filter 'AI_Type'/'AI_Brand' if mentioned.
    2. **Range**: "5-8" -> value: ["5", "6", "7", "8"] (Integers only).
    3. **Price Filter**: 
       - If user asks "price?" or "lowest/highest price" WITHOUT specific number -> DO NOT create filter for 'ราคาทุนต่อหน่วย'.
       - Create filter ONLY if number exists (e.g. "< 5000").
    4. **Sorting (สำคัญ)**:
       - "ถูกสุด", "ต่ำสุด", "น้อยสุด" -> sort_order: "asc"
       - "แพงสุด", "สูงสุด", "มากสุด" -> sort_order: "desc"
       - Default -> sort_order: null
    
    Output JSON:
    {{
        "filters": [ {{ "column": "...", "operator": "...", "value": "..." }} ],
        "sort_order": "asc" 
    }}
    """
    try:
        res = ai_model.generate_content(
            prompt, generation_config=genai.types.GenerationConfig(response_mime_type="application/json")
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
        
        if new_count > 0:
            if c_a2.button("🚀 สอน AI เดี๋ยวนี้", type="primary"):
                with st.status("🤖 AI กำลังเรียนรู้...", expanded=True) as status:
                    to_proc = new_items_df[['รหัสสินค้า', 'รายละเอียดสินค้า']].rename(columns={'รหัสสินค้า':'SKU', 'รายละเอียดสินค้า':'Name'}).to_dict('records')
                    BATCH = 10
                    res_save = []
                    total_batches = (len(to_proc) // BATCH) + 1
                    
                    for i in range(0, len(to_proc), BATCH):
                        chunk = to_proc[i:i+BATCH]
                        status.write(f"Batch {(i//BATCH)+1}/{total_batches} ({len(chunk)} รายการ)...")
                        names = [x['Name'] for x in chunk]
                        ai_res = ask_gemini_extract(names)
                        for idx, item in enumerate(chunk):
                            ar = ai_res[idx] if idx < len(ai_res) else {}
                            res_save.append([item['SKU'], ar.get('AI_Brand','Unknown'), ar.get('AI_Type','Other'), ar.get('AI_Spec','-'), ar.get('AI_Tags','')])
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
    
    df_search = merge_data(df_main, df_mem)
    
    col_q1, col_q2 = st.columns([4, 1])
    query2 = col_q1.text_input("พิมพ์คำค้นหาแบบธรรมชาติ", placeholder="เช่น ตู้เย็น 2 ประตู ราคาไม่เกิน 8000", key="search_tab2")
   # วางต่อจากบรรทัด: query2 = col_q1.text_input(...)
if col_q2.button("ค้นหา AI", type="primary"):
        if query2:
            with st.spinner('🤖 AI กำลังคิด...'):
                cols_ai = ['AI_Brand', 'AI_Type', 'AI_Spec', 'AI_Tags', 'ราคาทุนต่อหน่วย']
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
                                
                                # 🔥 แก้ไขจุดตาย: ถ้าค่ามาเป็น List ให้แตกออกมาเช็คทีละตัว
                                # (เช่น ['5', '6', '7'] -> เช็ค 5, เช็ค 6, เช็ค 7)
                                values_list = raw_val if isinstance(raw_val, list) else [raw_val]
                                
                                for val in values_list:
                                    if col == 'ราคาทุนต่อหน่วย':
                                        s_val = pd.to_numeric(df_search[col], errors='coerce').fillna(0)
                                        val = float(val)
                                    else:
                                        s_val = df_search[col].astype(str)
                                        val = str(val)
                                    
                                    if op == 'contains': sub_mask = s_val.str.contains(val, case=False, na=False)
                                    elif op == 'equals': sub_mask = (s_val == val)
                                    elif op == 'gt': sub_mask = (s_val > val)
                                    elif op == 'gte': sub_mask = (s_val >= val)
                                    elif op == 'lt': sub_mask = (s_val < val)
                                    elif op == 'lte': sub_mask = (s_val <= val)
                                    else: sub_mask = pd.Series([False] * len(df_search))
                                    
                                    col_mask |= sub_mask # รวมพลัง OR
                                    vals_log.append(f"{val}")
                            
                            final_mask &= col_mask
                            active_conds.append(f"{col}: {', '.join(vals_log)}")
                        
                        results = df_search[final_mask]
                        
                        # Logic เรียงลำดับ
                        if not results.empty and sort_order:
                            if sort_order == 'asc':
                                results = results.sort_values(by='ราคาทุนต่อหน่วย', ascending=True)
                            elif sort_order == 'desc':
                                results = results.sort_values(by='ราคาทุนต่อหน่วย', ascending=False)

                        if not results.empty:
                            st.success(f"✅ พบ {len(results)} รายการ")
                            st.dataframe(
                                results[['รหัสสินค้า', 'รายละเอียดสินค้า', 'ราคาทุนต่อหน่วย', 'จำนวนสต้อก', 'AI_Brand', 'AI_Spec']],
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
