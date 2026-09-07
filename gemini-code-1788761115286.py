import requests
import streamlit as st

# ضبط إعدادات الصفحة
st.set_page_config(
    page_title="تطبيق الطقس",
    page_icon="🌤️",
    layout="centered"
)

API_KEY = "99a4f3525b68bcf09d5cae7a3bd9a1df"
BASE_URL = "https://api.openweathermap.org/data/2.5/weather"

# عنوان التطبيق
st.title("🌤️ تطبيق حالة الطقس")
st.write("أدخل اسم المدينة لمعرفة حالة الطقس الحالية")

# إدخال اسم المدينة
city = st.text_input("اسم المدينة:", placeholder="مثال: Riyadh, Cairo, London")

if st.button("عرض الطقس", type="primary"):
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
                temp = data["main"]["temp"]
                feels_like = data["main"]["feels_like"]
                humidity = data["main"]["humidity"]
                wind_speed = data["wind"]["speed"]
                description = data["weather"][0]["description"]
                
                st.success(f"الطقس في {city_name}، {country}")
                
                # عرض البيانات في أرقام رئيسية (Metrics)
                col1, col2 = st.columns(2)
                col1.metric("درجة الحرارة", f"{temp}°C", delta=f"الشعور: {feels_like}°C")
                col2.metric("سرعة الرياح", f"{wind_speed} م/ث")
                
                col3, col4 = st.columns(2)
                col3.metric("الحالة", description.capitalize())
                col4.metric("الرطوبة", f"{humidity}%")
                
            elif data.get("cod") == "404":
                st.error("❌ لم يتم العثور على المدينة، يرجى التأكد من الاسم.")
            else:
                st.warning(f"⚠️ حدث خطأ: {data.get('message', 'خطأ غير معروف')}")
                
        except requests.exceptions.RequestException as e:
            st.error(f"🌐 خطأ في الاتصال بالشبكة: {e}")
    else:
        st.info("يرجى كتابة اسم المدينة أولاً.")