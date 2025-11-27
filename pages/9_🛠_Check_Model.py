import streamlit as st
import google.generativeai as genai

st.set_page_config(page_title="Model Checker", page_icon="🛠")

st.title("🛠️ เช็คชื่อโมเดล Gemini ที่ใช้ได้")

# ดึง API Key จาก Secrets (ใช้ตัวเดียวกับ app.py)
try:
    api_key = st.secrets["gemini_api_key"]
    genai.configure(api_key=api_key)
    
    if st.button("เริ่มตรวจสอบโมเดล (Scan Models)"):
        with st.spinner("กำลังเชื่อมต่อ Google AI..."):
            try:
                available_models = []
                # ดึงรายชื่อโมเดลทั้งหมด
                for m in genai.list_models():
                    # กรองเฉพาะตัวที่ใช้ Chat ได้ (generateContent)
                    if 'generateContent' in m.supported_generation_methods:
                        clean_name = m.name.replace('models/', '')
                        available_models.append(clean_name)
                
                if available_models:
                    st.success(f"✅ พบ {len(available_models)} โมเดลที่ใช้ได้:")
                    
                    # แสดงรายชื่อและโค้ดสำหรับก๊อปปี้
                    for model_name in available_models:
                        col1, col2 = st.columns([3, 1])
                        with col1:
                            st.code(f"model = genai.GenerativeModel('{model_name}')")
                        with col2:
                            if "flash" in model_name:
                                st.caption("⚡ เร็ว/ถูก")
                            elif "pro" in model_name:
                                st.caption("🧠 ฉลาด")
                else:
                    st.error("❌ ไม่พบโมเดลที่รองรับ generateContent เลย")
                    
            except Exception as e:
                st.error(f"เกิดข้อผิดพลาดตอนดึงรายชื่อ: {e}")

except Exception as e:
    st.error("⚠️ ไม่พบ API Key ใน Secrets")
    st.info("กรุณาตรวจสอบไฟล์ .streamlit/secrets.toml หรือตั้งค่าใน Streamlit Cloud")
