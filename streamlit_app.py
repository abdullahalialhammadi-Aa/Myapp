import requests
import streamlit as st

# ضبط إعدادات الصفحة
st.set_page_config(
    page_title="تطبيق الطقس | Weather App",
    page_icon="🌤️",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# تخصيص التصميم باستخدام CSS
st.markdown("""
    <style>
    /* تحسين اتجاه النص والخطوط */
    html, body, [class*="css"] {
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        direction: rtl;
        text-align: right;
    }
    
    /* بطاقة عنوان التطبيق */
    .header-card {
        background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
        padding: 2.5rem;
        border-radius: 18px;
        color: white;
        text-align: center;
        box-shadow: 0 10px 20px rgba(0,0,0,0.15);
        margin-bottom: 2rem;
    }
    .header-card h1 {
        color: #ffffff !important;
        font-size: 2.3rem;
        margin-bottom: 0.5rem;
    }
    .header-card p {
        color: #e0e6ed;
        font-size: 1.05rem;
        margin: 0;
    }

    /* بطاقات عرض نتائج الطقس */
    .metric-box {
        background: #f8fafc;
        border-radius: 12px;
        padding: 1.2rem;
        text-align: center;
        border: 1px solid #edf2f7;
    }
    .metric-label {
        font-size: 0.95rem;
        color: #64748b;
        margin-bottom: 0.3rem;
    }
    .metric-value {
        font-size: 1.6rem;
        font-weight: bold;
        color: #0f172a;
    }
    
    /* التذييل (Footer) */
    .footer {
        text-align: center;
        color: #94a3b8;
        font-size: 0.9rem;
        margin-top: 3rem;
        padding-top: 1rem;
        border-top: 1px solid #e2e8f0;
    }
    </style>
""", unsafe_allow_html=True)

API_KEY = "99a4f3525b68bcf09d5cae7a3bd9a1df"
BASE_URL = "https://api.openweathermap.org/data/2.5/weather"

# الهيدر الرئيسي
st.markdown("""
    <div class="header-card">
        <h1>🌤️ برنامج حالة الطقس</h1>
        <p>مرحباً بك! يمكنك البحث عن حالة الطقس لأي مدينة حول العالم مباشرة</p>
    </div>
""", unsafe_allow_html=True)

# نموذج إدخال المدينة
city = st.text_input("📍 أدخل اسم المدينة:", placeholder="مثال: Abu Dhabi, Riyadh, London")

if st.button("عرض حالة الطقس", type="primary", use_container_width=True):
    if city.strip():
        params = {
            "q": city,
            "appid": API_KEY,
            "units": "metric",
            "lang": "ar"
        }
        
        try:
            with st.spinner("جاري جلب البيانات..."):
                response = requests.get(BASE_URL, params=params, timeout=10)
                data = response.json()
            
            if response.status_code == 200:
                city_name = data["name"]
                country = data["sys"]["country"]
                temp = round(data["main"]["temp"], 1)
                feels_like = round(data["main"]["feels_like"], 1)
                humidity = data["main"]["humidity"]
                wind_speed = data["wind"]["speed"]
                description = data["weather"][0]["description"]
                
                st.success(f"📍 النتائج لمدينة: **{city_name}، {country}**")
                
                # عرض البيانات في شبكة من البطاقات المنسقة
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown(f"""
                        <div class="metric-box">
                            <div class="metric-label">🌡️ درجة الحرارة</div>
                            <div class="metric-value">{temp}°C</div>
                            <small style="color:#64748b;">الشعور الفعلي: {feels_like}°C</small>
                        </div>
                    """, unsafe_allow_html=True)
                
                with col2:
                    st.markdown(f"""
                        <div class="metric-box">
                            <div class="metric-label">☁️ الحالة الجوية</div>
                            <div class="metric-value">{description.capitalize()}</div>
                        </div>
                    """, unsafe_allow_html=True)

                st.write("") # مسافة فاصلة

                col3, col4 = st.columns(2)
                with col3:
                    st.markdown(f"""
                        <div class="metric-box">
                            <div class="metric-label">💧 نسبة الرطوبة</div>
                            <div class="metric-value">{humidity}%</div>
                        </div>
                    """, unsafe_allow_html=True)
                    
                with col4:
                    st.markdown(f"""
                        <div class="metric-box">
                            <div class="metric-label">💨 سرعة الرياح</div>
                            <div class="metric-value">{wind_speed} م/ث</div>
                        </div>
                    """, unsafe_allow_html=True)
                
            elif data.get("cod") == "404":
                st.error("❌ لم يتم العثور على المدينة، يرجى التأكد من كتابة الاسم بشكل صحيح.")
            else:
                st.warning(f"⚠️ حدث خطأ: {data.get('message', 'خطأ غير معروف')}")
                
        except requests.exceptions.RequestException as e:
            st.error(f"🌐 خطأ في الاتصال بالشبكة: {e}")
    else:
        st.info("يرجى إدخال اسم المدينة أولاً للبدء.")

# التذييل والتوقيع
st.markdown("""
    <div class="footer">
        تم التطوير بواسطة <b>abdullahalialhammadi-Aa</b> 🚀
    </div>
""", unsafe_allow_html=True)
