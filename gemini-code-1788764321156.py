import requests
import random
import time
import streamlit as st

st.set_page_config(
    page_title="لعبة تطابق طقس المدن | Memory Game",
    page_icon="🧩",
    layout="centered"
)

st.markdown("""
    <style>
    html, body, [class*="css"] {
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        direction: rtl;
        text-align: right;
    }
    .main-header {
        background: linear-gradient(135deg, #4b6cb7 0%, #182848 100%);
        padding: 2rem;
        border-radius: 15px;
        color: white;
        text-align: center;
        margin-bottom: 2rem;
        box-shadow: 0 4px 15px rgba(0,0,0,0.1);
    }
    .main-header h1 {
        color: white !important;
        margin-bottom: 0.5rem;
    }
    .stat-box {
        background-color: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 10px;
        padding: 0.8rem;
        text-align: center;
        font-weight: bold;
    }
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
CITIES = ["Abu Dhabi", "Riyadh", "Cairo", "London"]

@st.cache_data(ttl=600)
def fetch_weather_cards():
    cards_data = []
    for city in CITIES:
        url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={API_KEY}&units=metric&lang=ar"
        try:
            res = requests.get(url, timeout=5)
            if res.status_code == 200:
                data = res.json()
                temp = round(data["main"]["temp"])
                desc = data["weather"][0]["description"]
                icon = "☀️" if "مشمس" in desc or "صافي" in desc else "☁️"
                cards_data.append({
                    "city": data["name"],
                    "label": f"{icon} {data['name']}\n{temp}°C",
                    "details": f"{desc.capitalize()}"
                })
        except Exception:
            pass
    
    # مضاعفة المدن لتكوين أزواج (Pairs)
    game_cards = cards_data + cards_data
    random.shuffle(game_cards)
    return game_cards

# تهيئة حالة اللعبة (Session State)
if "cards" not in st.session_state:
    st.session_state.cards = fetch_weather_cards()
    st.session_state.flipped = []
    st.session_state.matched = []
    st.session_state.moves = 0

st.markdown("""
    <div class="main-header">
        <h1>🧩 لعبة الذاكرة: طقس المدن</h1>
        <p>اعثر على كل بطاقتين متشابهتين لحالة الطقس بأقل عدد من المحاولات!</p>
    </div>
""", unsafe_allow_html=True)

col_stat1, col_stat2 = st.columns(2)
with col_stat1:
    st.markdown(f'<div class="stat-box">🎯 المحاولات: {st.session_state.moves}</div>', unsafe_allow_html=True)
with col_stat2:
    st.markdown(f'<div class="stat-box">✅ الأزواج المكتشفة: {len(st.session_state.matched)} / {len(CITIES)}</div>', unsafe_allow_html=True)

st.write("")

# إعادة تشغيل اللعبة
if st.button("🔄 لعبة جديدة", use_container_width=True):
    st.session_state.cards = fetch_weather_cards()
    st.session_state.flipped = []
    st.session_state.matched = []
    st.session_state.moves = 0
    st.rerun()

# التحقق من المطابقة عند كشف بطاقتين
if len(st.session_state.flipped) == 2:
    st.session_state.moves += 1
    idx1, idx2 = st.session_state.flipped
    card1 = st.session_state.cards[idx1]
    card2 = st.session_state.cards[idx2]
    
    if card1["city"] == card2["city"]:
        st.session_state.matched.append(card1["city"])
        st.session_state.flipped = []
        st.rerun()
    else:
        time.sleep(1)
        st.session_state.flipped = []
        st.rerun()

# عرض شبكة البطاقات (4 أعمدة × 2 صفوف)
cols = st.columns(4)
for idx, card in enumerate(st.session_state.cards):
    col = cols[idx % 4]
    is_matched = card["city"] in st.session_state.matched
    is_flipped = idx in st.session_state.flipped
    
    with col:
        if is_matched or is_flipped:
            st.button(
                f"{card['label']}\n({card['details']})",
                key=f"card_{idx}",
                disabled=True,
                use_container_width=True
            )
        else:
            if st.button("❓", key=f"card_{idx}", use_container_width=True):
                if len(st.session_state.flipped) < 2:
                    st.session_state.flipped.append(idx)
                    st.rerun()

# نهاية اللعبة
if len(st.session_state.matched) == len(CITIES):
    st.balloons()
    st.success(f"🎉 مبروك! أنهيت اللعبة بنجاح في {st.session_state.moves} محاولة!")

st.markdown("""
    <div class="footer">
        تم التطوير بواسطة <b>abdullahalialhammadi-Aa</b> 🚀
    </div>
""", unsafe_allow_html=True)