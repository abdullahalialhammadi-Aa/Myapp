# لعبة البطاقات المتشابهة 🃏

لعبة ذاكرة على الويب مبنية بـ Python و Streamlit: حسابات ولاعبين، أربعة مستويات صعوبة، سباق مع الوقت، ومضاعفة للسلاسل، ولوحة صدارة.

**الرابط المنشور:** https://kanlezwtbwmnx4xkq54rf7.streamlit.app/

## التشغيل على جهازك

```
pip install -r requirements.txt
python -m streamlit run streamlit_app.py
```

يفتح المتصفّح على http://localhost:8501

## أين تُحفظ البيانات؟ (مهم)

| الوضع | مكان الحفظ | هل تبقى البيانات؟ |
|---|---|---|
| بدون `database_url` | ملف `memory_game.db` بجانب التطبيق | على جهازك نعم. **على Streamlit Cloud تُمسح** عند كل إعادة تشغيل أو رفع تعديل |
| مع `database_url` | قاعدة بيانات Postgres خارجية | **دائمة** — لا تتأثر بتحديث الكود أو إعادة التشغيل |

صفحة الدخول تُظهر تنبيهاً إذا كان الحفظ مؤقتاً.

### تفعيل الحفظ الدائم (مرة واحدة)

1. أنشئ قاعدة بيانات Postgres مجانية، مثلاً على [Supabase](https://supabase.com) أو [Neon](https://neon.tech).
2. انسخ رابط الاتصال (Connection string / URI). يبدأ بـ `postgresql://` أو `postgres://`.
   - في Supabase: **Project Settings ← Database ← Connection string ← URI**، واختر **Session pooler**.
3. في [share.streamlit.io](https://share.streamlit.io) افتح التطبيق ← **⋮ ← Settings ← Secrets** والصق:
   ```toml
   database_url = "postgresql://USER:PASSWORD@HOST:5432/postgres"
   ```
4. احفظ. التطبيق يعيد التشغيل وينشئ الجداول بنفسه.

بعدها تقدر تعدّل الكود وترفعه براحتك، والحسابات والنتائج تبقى.

## نصائح لتجنّب ضياع البيانات

- لا تحذف قاعدة البيانات ولا تغيّر `database_url` لقاعدة ثانية — تبدو البيانات وكأنها اختفت.
- الكود ينشئ الجداول إذا لم تكن موجودة فقط، ولا يحذف أو يعدّل جداول قائمة.
- ملف `memory_game.db` وملف الأسرار `.streamlit/secrets.toml` لا يُرفعان إلى GitHub (موجودان في `.gitignore`).
- تسجيل الدخول يبقى بعد تحديث الصفحة لمدة 30 يوماً (رمز موقَّع في الرابط). زر **خروج** يلغيه.
