"""لعبة العثور على البطاقات المتشابهة — ملف واحد مكتفٍ بذاته.

    python memory_game.py

المتطلب الوحيد: Python 3.10 أو أحدث. تعمل اللعبة بمكتبات بايثون القياسية
فقط (tkinter · sqlite3 · hashlib · winsound)، وتُنشئ بجانبها عند أول تشغيل
ملفَّي memory_game.db و settings.json. حذفهما يعيد كل شيء إلى نقطة الصفر.

المزايا:
    · حسابات بأسماء عربية أو إنجليزية مع 12 صورة رمزية، وكلمات المرور
      مُجزَّأة بـ PBKDF2-SHA256 ولا تُخزَّن كنص صريح. ووضع ضيف بلا حساب.
    · أربعة مستويات: سهل 12 بطاقة · متوسط 24 · صعب 32 · خبير 40.
    · المؤقّت لا يبدأ إلا عند قلب أول بطاقة.
    · لوحة صدارة بترتيب حسب مجموع النقاط مع تصفية بالمستوى، وملف شخصي
      بإحصاءات مفصَّلة وسجلّ آخر الجولات.
    · اختصارات: Esc إيقاف مؤقت · F2 إعادة الجولة.

النقاط:
    +100 لكل زوج صحيح · +25 لكل مطابقة متتالية (بحد أقصى +100)
    −10 لكل محاولة خاطئة · +5 لكل ثانية متبقّية عند الفوز
    +500 لجولة مثالية بلا أخطاء · ثم مضاعف المستوى (×1 · ×1.5 · ×2 · ×2.5)
    التقييم بالنجوم حسب الدقة: ثلاث نجوم من 80% ونجمتان من 55%.

محتويات الملف:
    1. الإعدادات        المستويات، قيم النقاط، الرموز، الصور الرمزية
    2. المظهر           الألوان والخطوط والتحجيم حسب دقة الشاشة
    3. التفضيلات        حفظ خيارات المستخدم محلياً
    4. الصوت            مؤثرات مُولَّدة رياضياً بلا ملفات صوت
    5. قاعدة البيانات   الحسابات والجولات ولوحة الصدارة
    6. الحسابات         تجزئة كلمات المرور والتحقق من المدخلات
    7. منطق اللعبة      توزيع البطاقات والقواعد وحساب النتيجة
    8. عناصر الواجهة    أزرار وحقول ولوحات مرسومة يدوياً على Canvas
    9. الشاشات          الدخول، القائمة، اللعب، الصدارة، الملف الشخصي
   10. التطبيق          النافذة الرئيسية ونقطة التشغيل
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import math
import os
import random
import re
import sqlite3
import struct
import sys
import time
import wave
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

try:
    import tkinter as tk
    import tkinter.font as tkfont
except ImportError:  # بايثون مثبَّت بدون tcl/tk
    raise SystemExit(
        "تعذّر العثور على tkinter.\n"
        "على ويندوز: أعد تثبيت بايثون مع تفعيل خيار tcl/tk.\n"
        "على لينكس: sudo apt install python3-tk"
    )


# ==============================================================================
# 1. الإعدادات
# ==============================================================================

APP_NAME = "لعبة البطاقات المتشابهة"
APP_TAGLINE = "درّب ذاكرتك · اجمع النقاط · تصدّر القائمة"
APP_VERSION = "1.0"

ROOT_DIR = Path(__file__).resolve().parent
DB_PATH = ROOT_DIR / "memory_game.db"
SETTINGS_PATH = ROOT_DIR / "settings.json"


# ---------------------------------------------------------------- الصعوبة

@dataclass(frozen=True)
class Difficulty:
    """تعريف مستوى صعوبة واحد."""

    key: str
    label: str
    cols: int
    rows: int
    seconds: int          # الوقت المتاح
    multiplier: float     # مضاعف النقاط النهائي
    color: str            # اللون المميّز للمستوى

    @property
    def total_cards(self) -> int:
        return self.cols * self.rows

    @property
    def pairs(self) -> int:
        return self.total_cards // 2

    @property
    def grid_label(self) -> str:
        return f"{self.cols} × {self.rows}"


DIFFICULTIES: dict[str, Difficulty] = {
    d.key: d
    for d in (
        Difficulty("easy",   "سهل",   4, 3,  90, 1.0, "#34D399"),
        Difficulty("medium", "متوسط", 6, 4, 150, 1.5, "#22D3EE"),
        Difficulty("hard",   "صعب",   8, 4, 180, 2.0, "#FBBF24"),
        Difficulty("expert", "خبير",  8, 5, 210, 2.5, "#F87171"),
    )
}

DIFFICULTY_ORDER = ["easy", "medium", "hard", "expert"]


def difficulty_label(key: str) -> str:
    d = DIFFICULTIES.get(key)
    return d.label if d else key


# ---------------------------------------------------------------- النقاط

POINTS_PER_MATCH = 100      # نقاط كل زوج صحيح
COMBO_STEP = 25             # زيادة لكل مطابقة متتالية
COMBO_MAX = 100             # سقف مكافأة السلسلة
PENALTY_PER_MISS = 10       # خصم كل محاولة خاطئة
TIME_BONUS_PER_SECOND = 5   # مكافأة كل ثانية متبقية عند الفوز
PERFECT_BONUS = 500         # مكافأة الجولة المثالية (بلا أخطاء)

# عتبات النجوم حسب الدقة (الأزواج ÷ عدد الحركات)
STAR_THRESHOLDS = (0.80, 0.55)

# مهلة عرض بطاقتين غير متطابقتين قبل قلبهما (بالمللي ثانية)
MISMATCH_DELAY_MS = 750


# ---------------------------------------------------------------- الرموز

# (الرمز، لونه). تُرسم الرموز كصور ظلّية ملوّنة داخل البطاقة.
SYMBOLS: list[tuple[str, str]] = [
    ("\U0001F34E", "#FF5A5F"),  # تفاحة
    ("\U0001F680", "#8B7BFF"),  # صاروخ
    ("\U0001F3B8", "#F59E0B"),  # جيتار
    ("\U0001F419", "#22D3EE"),  # أخطبوط
    ("\U0001F338", "#F472B6"),  # زهرة
    ("\U0001F955", "#FB923C"),  # جزرة
    ("\U0001F31E", "#FBBF24"),  # شمس
    ("\U0001F319", "#93C5FD"),  # هلال
    ("⚡",     "#FACC15"),  # برق
    ("\U0001F3AF", "#EF4444"),  # هدف
    ("\U0001F3B2", "#34D399"),  # نرد
    ("\U0001F41D", "#EAB308"),  # نحلة
    ("\U0001F420", "#38BDF8"),  # سمكة
    ("\U0001F344", "#F87171"),  # فطر
    ("\U0001F3C0", "#FB7185"),  # كرة سلة
    ("\U0001F4A7", "#60A5FA"),  # قطرة
    ("\U0001F525", "#F97316"),  # نار
    ("⭐",     "#FDE047"),  # نجمة
    ("\U0001F349", "#4ADE80"),  # بطيخة
    ("\U0001F511", "#FCD34D"),  # مفتاح
    ("\U0001F48E", "#67E8F9"),  # جوهرة
    ("\U0001F3AA", "#C084FC"),  # خيمة سيرك
    ("\U0001F41E", "#F43F5E"),  # خنفساء
    ("\U0001F33F", "#22C55E"),  # ورقة
]

# الصور الرمزية المتاحة عند إنشاء الحساب
AVATARS: list[str] = [
    "\U0001F98A",  # ثعلب
    "\U0001F431",  # قطة
    "\U0001F436",  # كلب
    "\U0001F43C",  # باندا
    "\U0001F981",  # أسد
    "\U0001F438",  # ضفدع
    "\U0001F435",  # قرد
    "\U0001F427",  # بطريق
    "\U0001F984",  # وحيد القرن
    "\U0001F989",  # بومة
    "\U0001F42F",  # نمر
    "\U0001F430",  # أرنب
]

DEFAULT_AVATAR = AVATARS[0]
GUEST_AVATAR = "\U0001F464"


def format_duration(seconds: float | None) -> str:
    """تحويل عدد الثواني إلى صيغة m:ss."""
    if seconds is None:
        return "—"
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60}:{seconds % 60:02d}"


# ==============================================================================
# 2. المظهر: الألوان والخطوط والتحجيم
# ==============================================================================

class Palette:
    """ألوان الواجهة (نمط داكن)."""

    bg = "#080C1B"           # خلفية النافذة
    bg_alt = "#0C1226"       # خلفية بديلة
    surface = "#131A33"      # سطح البطاقات واللوحات
    surface2 = "#1B2444"     # سطح أفتح (حقول، أزرار هادئة)
    surface3 = "#27325C"     # سطح عند المرور بالفأرة
    stroke = "#2C3765"       # حدود
    stroke_hi = "#465393"    # حدود مضيئة

    text = "#EDF2FF"
    muted = "#8E9BC6"
    faint = "#5C6899"

    primary = "#7C5CFF"
    primary_hi = "#9179FF"
    primary_lo = "#5B3FE0"
    accent = "#22D3EE"
    accent_lo = "#0E9DB8"

    success = "#34D399"
    warning = "#FBBF24"
    danger = "#F87171"

    gold = "#FFC857"
    silver = "#C7D2E5"
    bronze = "#DC8A4E"

    card_back = "#2A2170"        # وجه البطاقة المقلوب
    card_back_hi = "#3B2E9E"     # عند المرور بالفأرة
    card_back_edge = "#5B49D6"
    card_motif = "#6C5CE0"       # الزخرفة على ظهر البطاقة
    card_face = "#F3F6FF"        # وجه البطاقة المكشوف
    card_face_edge = "#C6D0EE"
    card_matched = "#1E3D36"     # بطاقة تمّت مطابقتها
    card_matched_edge = "#34D399"

    scrim = "#05070F"            # طبقة تعتيم خلف النوافذ المنبثقة


P = Palette


def _pick_family(root: tk.Misc, candidates: list[str], fallback: str) -> str:
    """اختيار أول خط متاح على النظام من قائمة مرشّحين."""
    available = {name.lower() for name in tkfont.families(root)}
    for name in candidates:
        if name.lower() in available:
            return name
    return fallback


class Theme:
    """يحسب التحجيم ويوفّر الخطوط. يُنشأ مرة واحدة بعد إنشاء نافذة Tk."""

    def __init__(self, root: tk.Misc) -> None:
        # عدد البكسلات في البوصة → معامل التحجيم مقارنةً بـ 96 نقطة/بوصة
        dpi = float(root.winfo_fpixels("1i"))
        self.dpi = dpi
        self.scale = max(1.0, dpi / 96.0)

        self.family = _pick_family(
            root,
            ["Segoe UI", "Tahoma", "Dubai", "Noto Sans Arabic", "Arial"],
            "Arial",
        )
        self.family_bold = self.family
        self.emoji = _pick_family(
            root,
            ["Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji", "Segoe UI Symbol"],
            self.family,
        )
        self.mono = _pick_family(
            root, ["Consolas", "Cascadia Mono", "DejaVu Sans Mono", "Courier New"], "Courier New"
        )

    # -------------------------------------------------------- قياسات

    def px(self, value: float) -> int:
        """تحويل قياس مصمَّم على 96 نقطة/بوصة إلى بكسلات الشاشة الفعلية."""
        return int(round(value * self.scale))

    # -------------------------------------------------------- خطوط

    def font(self, size: float = 11, weight: str = "normal") -> tuple:
        # Tk يقبل أحجام الخطوط كأعداد صحيحة فقط
        return (self.family, int(round(size)), weight)

    def title(self, size: float = 22) -> tuple:
        return (self.family, int(round(size)), "bold")

    def emoji_font(self, pixels: int) -> tuple:
        """خط الرموز بحجم محدَّد بالبكسل (القيمة السالبة تعني بكسل في Tk)."""
        return (self.emoji, -max(8, int(pixels)))

    def num_font(self, size: float = 14, weight: str = "bold") -> tuple:
        return (self.family, int(round(size)), weight)


# ------------------------------------------------------------ أدوات ألوان

def mix(color_a: str, color_b: str, ratio: float) -> str:
    """مزج لونين بنسبة ratio (0 = اللون الأول، 1 = اللون الثاني)."""
    ratio = min(1.0, max(0.0, ratio))
    a = tuple(int(color_a[i : i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(color_b[i : i + 2], 16) for i in (1, 3, 5))
    out = tuple(int(round(x + (y - x) * ratio)) for x, y in zip(a, b))
    return "#%02X%02X%02X" % out


def with_alpha_on(color: str, background: str, alpha: float) -> str:
    """محاكاة الشفافية بمزج اللون مع الخلفية (Tk لا يدعم قناة ألفا)."""
    return mix(background, color, alpha)


# ==============================================================================
# 3. التفضيلات المحلية
# ==============================================================================

_DEFAULTS = {
    "sound": True,          # تشغيل المؤثرات الصوتية
    "last_username": "",    # تعبئة اسم المستخدم تلقائياً
    "last_difficulty": "easy",
}


class Settings:
    def __init__(self, path: Path = SETTINGS_PATH) -> None:
        self.path = Path(path)
        self.data = dict(_DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                self.data.update({k: v for k, v in saved.items() if k in _DEFAULTS})
        except (OSError, ValueError):
            pass  # ملف مفقود أو تالف → نبقى على القيم الافتراضية

    def save(self) -> None:
        try:
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass  # تعذّر الحفظ (قرص للقراءة فقط) — لا يمنع اللعب

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        self.data[key] = value
        self.save()


# ==============================================================================
# 4. المؤثرات الصوتية
# ==============================================================================

SAMPLE_RATE = 22050


def _envelope(position: float, length: int, attack: float = 0.01, release: float = 0.25) -> float:
    """غلاف صوتي بسيط لتفادي الطقطقة في البداية والنهاية."""
    t = position / length
    if t < attack:
        return t / attack
    if t > 1 - release:
        return max(0.0, (1 - t) / release)
    return 1.0


def _render(notes: list[tuple[float, float]], volume: float = 0.35,
            wave_shape: str = "sine") -> bytes:
    """توليد ملف WAV من قائمة نغمات (التردد بالهرتز، المدة بالثواني)."""
    frames = bytearray()
    for freq, duration in notes:
        count = max(1, int(SAMPLE_RATE * duration))
        for n in range(count):
            phase = 2 * math.pi * freq * n / SAMPLE_RATE
            if wave_shape == "square":
                raw = 1.0 if math.sin(phase) >= 0 else -1.0
            elif wave_shape == "saw":
                raw = 2.0 * ((freq * n / SAMPLE_RATE) % 1.0) - 1.0
            else:
                raw = math.sin(phase)
            # تخفيف تدريجي داخل النغمة يعطي إحساس "النقرة" الطبيعية
            decay = math.exp(-3.0 * n / count)
            amp = raw * decay * _envelope(n, count) * volume
            frames += struct.pack("<h", int(max(-1.0, min(1.0, amp)) * 32767))

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(bytes(frames))
    return buffer.getvalue()


# نغمات المؤثرات (سلّم موسيقي متناغم حتى لا تكون الأصوات مزعجة)
_RECIPES: dict[str, tuple[list[tuple[float, float]], float, str]] = {
    "flip":     ([(880, 0.05)], 0.22, "sine"),
    "click":    ([(660, 0.04)], 0.18, "sine"),
    "match":    ([(659, 0.09), (988, 0.14)], 0.30, "sine"),
    "mismatch": ([(196, 0.10), (147, 0.14)], 0.26, "saw"),
    "combo":    ([(784, 0.07), (988, 0.07), (1319, 0.12)], 0.30, "sine"),
    "win":      ([(523, 0.11), (659, 0.11), (784, 0.11), (1047, 0.28)], 0.34, "sine"),
    "lose":     ([(392, 0.14), (330, 0.14), (247, 0.30)], 0.30, "sine"),
}


class SoundEngine:
    """يولّد المؤثرات مرة واحدة عند الحاجة ثم يعيد استخدامها."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._cache: dict[str, bytes] = {}
        self._backend = self._detect_backend()

    # ---------------------------------------------------------- المشغّل

    def _detect_backend(self) -> str:
        if sys.platform == "win32":
            try:
                import winsound  # noqa: F401
                return "winsound"
            except ImportError:
                pass
        try:
            import pygame
            pygame.mixer.init(frequency=SAMPLE_RATE, size=-16, channels=1, buffer=512)
            return "pygame"
        except Exception:
            return "none"

    @property
    def available(self) -> bool:
        return self._backend != "none"

    def toggle(self) -> bool:
        self.enabled = not self.enabled
        return self.enabled

    # ---------------------------------------------------------- التشغيل

    def _data(self, name: str) -> bytes | None:
        if name not in self._cache:
            recipe = _RECIPES.get(name)
            if recipe is None:
                return None
            notes, volume, shape = recipe
            self._cache[name] = _render(notes, volume, shape)
        return self._cache[name]

    def play(self, name: str) -> None:
        """تشغيل مؤثر بالاسم. يتجاهل أي خطأ حتى لا يعطّل الصوتُ اللعبةَ."""
        if not self.enabled or self._backend == "none":
            return
        data = self._data(name)
        if not data:
            return
        try:
            if self._backend == "winsound":
                import winsound
                winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_ASYNC)
            else:
                import pygame
                pygame.mixer.Sound(buffer=data).play()
        except Exception:
            pass

    def prewarm(self) -> None:
        """توليد كل المؤثرات مسبقاً لتفادي أي تلعثم عند أول تشغيل."""
        for name in _RECIPES:
            self._data(name)


# ==============================================================================
# 5. قاعدة البيانات
# ==============================================================================

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    salt          TEXT NOT NULL,
    avatar        TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS games (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    difficulty TEXT    NOT NULL,
    score      INTEGER NOT NULL,
    moves      INTEGER NOT NULL,
    matches    INTEGER NOT NULL,
    duration   REAL    NOT NULL,
    best_combo INTEGER NOT NULL DEFAULT 0,
    won        INTEGER NOT NULL,
    played_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_games_user  ON games(user_id);
CREATE INDEX IF NOT EXISTS idx_games_score ON games(score DESC);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    """غلاف بسيط حول SQLite. كل الدوال تعيد قواميس عادية."""

    def __init__(self, path: Path | str = DB_PATH) -> None:
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------ الحسابات

    def username_exists(self, username: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
        return row is not None

    def create_user(self, username: str, password_hash: str, salt: str, avatar: str) -> dict:
        cur = self.conn.execute(
            "INSERT INTO users (username, password_hash, salt, avatar, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (username, password_hash, salt, avatar, _now()),
        )
        self.conn.commit()
        return self.get_user(cur.lastrowid)

    def get_user(self, user_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_user_by_name(self, username: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
        return dict(row) if row else None

    def set_avatar(self, user_id: int, avatar: str) -> None:
        self.conn.execute("UPDATE users SET avatar = ? WHERE id = ?", (avatar, user_id))
        self.conn.commit()

    def set_password(self, user_id: int, password_hash: str, salt: str) -> None:
        self.conn.execute(
            "UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
            (password_hash, salt, user_id),
        )
        self.conn.commit()

    # ------------------------------------------------------------ الجولات

    def save_game(
        self,
        user_id: int,
        difficulty: str,
        score: int,
        moves: int,
        matches: int,
        duration: float,
        best_combo: int,
        won: bool,
    ) -> None:
        self.conn.execute(
            "INSERT INTO games (user_id, difficulty, score, moves, matches, duration,"
            " best_combo, won, played_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, difficulty, int(score), int(moves), int(matches),
             float(duration), int(best_combo), int(won), _now()),
        )
        self.conn.commit()

    def recent_games(self, user_id: int, limit: int = 8) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM games WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ الصدارة

    def leaderboard(self, difficulty: str | None = None, limit: int = 50) -> list[dict]:
        """ترتيب اللاعبين حسب مجموع النقاط (ضمن مستوى محدَّد أو في كل المستويات)."""
        where = "WHERE g.difficulty = ?" if difficulty else ""
        params: tuple[Any, ...] = (difficulty,) if difficulty else ()
        rows = self.conn.execute(
            f"""
            SELECT u.id            AS user_id,
                   u.username      AS username,
                   u.avatar        AS avatar,
                   SUM(g.score)    AS total_score,
                   MAX(g.score)    AS best_score,
                   COUNT(*)        AS games,
                   SUM(g.won)      AS wins,
                   MIN(CASE WHEN g.won = 1 THEN g.duration END) AS best_time
            FROM users u
            JOIN games g ON g.user_id = u.id
            {where}
            GROUP BY u.id
            ORDER BY total_score DESC, wins DESC, best_time ASC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()

        out = []
        for i, row in enumerate(rows, start=1):
            entry = dict(row)
            entry["rank"] = i
            out.append(entry)
        return out

    def user_rank(self, user_id: int, difficulty: str | None = None) -> int | None:
        """ترتيب لاعب معيّن ضمن القائمة الكاملة (None إن لم يلعب بعد)."""
        for entry in self.leaderboard(difficulty, limit=100000):
            if entry["user_id"] == user_id:
                return entry["rank"]
        return None

    def player_count(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(DISTINCT user_id) AS n FROM games"
        ).fetchone()
        return int(row["n"] or 0)

    # ------------------------------------------------------------ إحصائيات

    def user_stats(self, user_id: int) -> dict:
        row = self.conn.execute(
            """
            SELECT COUNT(*)                AS games,
                   COALESCE(SUM(score), 0) AS total_score,
                   COALESCE(MAX(score), 0) AS best_score,
                   COALESCE(SUM(won), 0)   AS wins,
                   COALESCE(SUM(moves), 0) AS moves,
                   COALESCE(SUM(matches), 0) AS matches,
                   COALESCE(MAX(best_combo), 0) AS best_combo,
                   MIN(CASE WHEN won = 1 THEN duration END) AS best_time
            FROM games WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()

        stats = dict(row)
        stats["losses"] = stats["games"] - stats["wins"]
        stats["accuracy"] = (stats["matches"] / stats["moves"]) if stats["moves"] else 0.0
        stats["win_rate"] = (stats["wins"] / stats["games"]) if stats["games"] else 0.0
        return stats

    def best_score_by_difficulty(self, user_id: int) -> dict[str, dict]:
        """أفضل نتيجة وأسرع وقت لكل مستوى صعوبة."""
        rows = self.conn.execute(
            """
            SELECT difficulty,
                   MAX(score) AS best_score,
                   COUNT(*)   AS games,
                   MIN(CASE WHEN won = 1 THEN duration END) AS best_time
            FROM games WHERE user_id = ? GROUP BY difficulty
            """,
            (user_id,),
        ).fetchall()
        return {r["difficulty"]: dict(r) for r in rows}


# ==============================================================================
# 6. الحسابات والتحقق
# ==============================================================================

_ITERATIONS = 200_000
_SALT_BYTES = 16

USERNAME_MIN, USERNAME_MAX = 3, 16
PASSWORD_MIN = 4

# حروف عربية أو لاتينية أو أرقام أو شرطة سفلية
_USERNAME_RE = re.compile("^[\\w\u0600-\u06FF]+$", re.UNICODE)


# ------------------------------------------------------------------ التجزئة

def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """يعيد (التجزئة، الملح) بصيغة hex."""
    salt = salt or os.urandom(_SALT_BYTES).hex()
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS
    )
    return digest.hex(), salt


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    """مقارنة آمنة زمنياً ضد هجمات التوقيت."""
    candidate, _ = hash_password(password, salt)
    return hmac.compare_digest(candidate, expected_hash)


# ------------------------------------------------------------------ التحقق

def validate_username(username: str) -> str | None:
    """يعيد رسالة خطأ، أو None إذا كان الاسم صالحاً."""
    name = username.strip()
    if not name:
        return "اكتب اسم المستخدم"
    if len(name) < USERNAME_MIN:
        return f"الاسم قصير جداً (الحد الأدنى {USERNAME_MIN} أحرف)"
    if len(name) > USERNAME_MAX:
        return f"الاسم طويل جداً (الحد الأقصى {USERNAME_MAX} حرفاً)"
    if not _USERNAME_RE.match(name):
        return "يُسمح بالحروف والأرقام والشرطة السفلية فقط"
    return None


def validate_password(password: str) -> str | None:
    if not password:
        return "اكتب كلمة المرور"
    if len(password) < PASSWORD_MIN:
        return f"كلمة المرور قصيرة جداً (الحد الأدنى {PASSWORD_MIN} رموز)"
    return None


# ------------------------------------------------------------------ الخدمة

class AuthError(Exception):
    """خطأ يُعرض للمستخدم مباشرةً."""


class AuthService:
    """تسجيل الدخول وإنشاء الحسابات فوق قاعدة البيانات."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def register(self, username: str, password: str, confirm: str, avatar: str) -> dict:
        username = username.strip()

        error = validate_username(username) or validate_password(password)
        if error:
            raise AuthError(error)
        if password != confirm:
            raise AuthError("كلمتا المرور غير متطابقتين")
        if self.db.username_exists(username):
            raise AuthError("هذا الاسم مستخدم بالفعل، جرّب اسماً آخر")

        password_hash, salt = hash_password(password)
        return self.db.create_user(username, password_hash, salt, avatar)

    def login(self, username: str, password: str) -> dict:
        username = username.strip()
        if not username:
            raise AuthError("اكتب اسم المستخدم")
        if not password:
            raise AuthError("اكتب كلمة المرور")

        user = self.db.get_user_by_name(username)
        # نفس الرسالة في الحالتين حتى لا نكشف أي الأسماء مسجَّلة
        if not user or not verify_password(password, user["salt"], user["password_hash"]):
            raise AuthError("اسم المستخدم أو كلمة المرور غير صحيحة")
        return user

    def change_password(self, user: dict, current: str, new: str, confirm: str) -> None:
        if not verify_password(current, user["salt"], user["password_hash"]):
            raise AuthError("كلمة المرور الحالية غير صحيحة")
        error = validate_password(new)
        if error:
            raise AuthError(error)
        if new != confirm:
            raise AuthError("كلمتا المرور غير متطابقتين")
        password_hash, salt = hash_password(new)
        self.db.set_password(user["id"], password_hash, salt)
        user["password_hash"], user["salt"] = password_hash, salt


# ==============================================================================
# 7. منطق اللعبة
# ==============================================================================

class FlipResult(Enum):
    """نتيجة النقر على بطاقة."""

    IGNORED = "ignored"    # نقرة غير صالحة (بطاقة مكشوفة أو مطابَقة)
    FIRST = "first"        # كُشفت البطاقة الأولى من الزوج
    MATCH = "match"        # تطابق
    MISMATCH = "mismatch"  # عدم تطابق


@dataclass
class ScoreBreakdown:
    """تفصيل النقاط النهائي لعرضه على شاشة النتيجة."""

    matches_points: int = 0
    combo_points: int = 0
    penalty_points: int = 0
    time_bonus: int = 0
    perfect_bonus: int = 0
    multiplier: float = 1.0
    total: int = 0

    @property
    def subtotal(self) -> int:
        return (
            self.matches_points
            + self.combo_points
            + self.time_bonus
            + self.perfect_bonus
            - self.penalty_points
        )


@dataclass
class MemoryGame:
    """حالة جولة واحدة."""

    difficulty: Difficulty
    seed: int | None = None

    cards: list[int] = field(default_factory=list)     # رقم الرمز لكل خانة
    matched: set[int] = field(default_factory=set)     # خانات تمّت مطابقتها
    revealed: set[int] = field(default_factory=set)    # خانات مكشوفة حالياً

    first_pick: int | None = None
    moves: int = 0            # عدد المحاولات (كل زوج مكشوف = محاولة)
    matches: int = 0
    misses: int = 0
    combo: int = 0            # سلسلة المطابقات المتتالية الحالية
    best_combo: int = 0
    score: int = 0

    finished: bool = False
    won: bool = False

    def __post_init__(self) -> None:
        self._deal()

    # ------------------------------------------------------------ التوزيع

    def _deal(self) -> None:
        """اختيار رموز عشوائية وتوزيعها في أزواج مخلوطة."""
        rng = random.Random(self.seed)
        pairs = self.difficulty.pairs
        symbols = rng.sample(range(len(SYMBOLS)), pairs)
        deck = symbols * 2
        rng.shuffle(deck)
        self.cards = deck

    # ------------------------------------------------------------ الخصائص

    @property
    def total_pairs(self) -> int:
        return self.difficulty.pairs

    @property
    def accuracy(self) -> float:
        """نسبة المحاولات الناجحة."""
        return self.matches / self.moves if self.moves else 0.0

    @property
    def is_complete(self) -> bool:
        return len(self.matched) == len(self.cards)

    def symbol_at(self, index: int) -> tuple[str, str]:
        """(الرمز، اللون) للخانة المطلوبة."""
        return SYMBOLS[self.cards[index]]

    def is_face_up(self, index: int) -> bool:
        return index in self.revealed or index in self.matched

    # ------------------------------------------------------------ اللعب

    def flip(self, index: int) -> FlipResult:
        """كشف بطاقة وتحديث الحالة. يُفترض أن الواجهة سوّت أي زوج سابق."""
        if self.finished:
            return FlipResult.IGNORED
        if index in self.matched or index in self.revealed:
            return FlipResult.IGNORED
        if len(self.revealed) >= 2:
            # زوج غير محسوم مكشوف بالفعل — تتولّى الواجهة إخفاءه أولاً
            return FlipResult.IGNORED

        self.revealed.add(index)

        if self.first_pick is None:
            self.first_pick = index
            return FlipResult.FIRST

        first = self.first_pick
        self.first_pick = None
        self.moves += 1

        if self.cards[first] == self.cards[index]:
            self.matched.update({first, index})
            # البطاقات المطابَقة تنتقل من "مكشوفة مؤقتاً" إلى "محسومة"
            self.revealed.difference_update({first, index})
            self.matches += 1
            self.combo += 1
            self.best_combo = max(self.best_combo, self.combo)
            self.score += POINTS_PER_MATCH + self._combo_bonus()
            if self.is_complete:
                self.finished = True
                self.won = True
            return FlipResult.MATCH

        self.misses += 1
        self.combo = 0
        self.score = max(0, self.score - PENALTY_PER_MISS)
        return FlipResult.MISMATCH

    def _combo_bonus(self) -> int:
        """مكافأة السلسلة للمطابقة الحالية (تبدأ من المطابقة الثانية)."""
        return min(COMBO_STEP * (self.combo - 1), COMBO_MAX)

    def hide_unmatched(self) -> None:
        """إخفاء البطاقات المكشوفة غير المتطابقة (تستدعيها الواجهة بعد المهلة)."""
        self.revealed = {i for i in self.revealed if i in self.matched}
        self.first_pick = None

    def time_out(self) -> None:
        """انتهاء الوقت قبل إكمال اللوحة."""
        if not self.finished:
            self.finished = True
            self.won = False

    # ------------------------------------------------------------ النتيجة

    def compute_final_score(self, seconds_left: float) -> ScoreBreakdown:
        """حساب النقاط النهائية مع مكافآت الوقت والجولة المثالية."""
        result = ScoreBreakdown(multiplier=self.difficulty.multiplier)

        result.matches_points = self.matches * POINTS_PER_MATCH
        result.penalty_points = self.misses * PENALTY_PER_MISS
        # مجموع مكافآت السلسلة = ما تراكم فعلياً أثناء اللعب
        result.combo_points = max(
            0, self.score - result.matches_points + result.penalty_points
        )

        if self.won:
            result.time_bonus = int(max(0, seconds_left) * TIME_BONUS_PER_SECOND)
            if self.misses == 0:
                result.perfect_bonus = PERFECT_BONUS

        result.total = max(0, int(round(result.subtotal * result.multiplier)))
        self.score = result.total
        return result

    def stars(self) -> int:
        """تقييم من 3 نجوم حسب الدقة (0 عند الخسارة)."""
        if not self.won:
            return 0
        high, mid = STAR_THRESHOLDS
        accuracy = self.accuracy
        if accuracy >= high:
            return 3
        if accuracy >= mid:
            return 2
        return 1


# ==============================================================================
# 8. عناصر الواجهة
# ==============================================================================

RLM = "\u200F"          # علامة "من اليمين إلى اليسار"
_ARABIC = re.compile("[\u0600-\u06FF]")

_THEME: Theme | None = None


def set_theme(value: Theme) -> None:
    global _THEME
    _THEME = value


def theme() -> Theme:
    assert _THEME is not None, "يجب استدعاء set_theme بعد إنشاء نافذة Tk"
    return _THEME


def px(value: float) -> int:
    return theme().px(value)


# ------------------------------------------------------------------ الرسم

def round_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float,
               radius: float, **kwargs) -> int:
    """مستطيل بحواف دائرية عبر مضلّع مُنعَّم."""
    radius = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


def bidi(text):
    """يضبط اتجاه الفقرة إلى اليمين لأي نص عربي.

    Tk يفترض اتجاهاً من اليسار لليمين، فسطر مثل "5 لاعبين" يُعرض "لاعبين 5"
    وتقفز علامة % والأقواس إلى الطرف الخطأ. العلامة في البداية تجعل أول محرف
    قويّ عربيَّ الاتجاه، والعلامة في النهاية تُبقي رمزاً ختامياً مثل % ملتصقاً
    برقمه بدل أن يقفز إلى أول السطر.
    """
    if not isinstance(text, str) or not text or not _ARABIC.search(text):
        return text
    if text.startswith(RLM):
        return text
    return RLM + text + RLM


class BidiCanvas(tk.Canvas):
    """لوحة رسم تضبط اتجاه كل نص عربي تكتبه تلقائياً."""

    def create_text(self, *args, **kwargs):
        if "text" in kwargs:
            kwargs["text"] = bidi(kwargs["text"])
        return super().create_text(*args, **kwargs)


def parent_bg(widget: tk.Misc, fallback: str = P.bg) -> str:
    try:
        return widget.cget("bg")
    except tk.TclError:
        return fallback


# ------------------------------------------------------------------ زر

class GButton(BidiCanvas):
    """زر مرسوم يدوياً مع حالات مرور وضغط وتعطيل."""

    STYLES = {
        "primary": dict(fill=P.primary, hover=P.primary_hi, press=P.primary_lo,
                        fg="#FFFFFF", outline=""),
        "accent":  dict(fill=P.accent, hover="#4BE0F5", press=P.accent_lo,
                        fg="#04222B", outline=""),
        "soft":    dict(fill=P.surface2, hover=P.surface3, press=P.surface,
                        fg=P.text, outline=P.stroke),
        "ghost":   dict(fill="", hover=P.surface2, press=P.surface,
                        fg=P.muted, outline=""),
        "danger":  dict(fill="#3A1E2B", hover="#54293A", press="#2E1722",
                        fg=P.danger, outline="#5E2D3F"),
        "success": dict(fill=P.success, hover="#5CE0B4", press="#25A97B",
                        fg="#04241A", outline=""),
    }

    def __init__(self, master: tk.Misc, text: str = "", command: Callable[[], None] | None = None,
                 kind: str = "primary", width: int = 170, height: int = 46,
                 radius: int | None = None, icon: str | None = None,
                 font_size: int = 11, bold: bool = True, bg: str | None = None) -> None:
        self.t = theme()
        self.w_px, self.h_px = px(width), px(height)
        self._bg = bg or parent_bg(master)
        super().__init__(master, width=self.w_px, height=self.h_px,
                         bg=self._bg, highlightthickness=0, bd=0, takefocus=0)

        self.style = dict(self.STYLES.get(kind, self.STYLES["primary"]))
        self.command = command
        self._text = text
        self._icon = icon
        self._enabled = True
        self._pressed = False
        radius_px = px(radius if radius is not None else height * 0.32)

        self._font = tkfont.Font(family=self.t.family, size=font_size,
                                 weight="bold" if bold else "normal")
        self._icon_font = tkfont.Font(family=self.t.emoji, size=font_size + 2)

        self._shape = round_rect(self, 0.5, 0.5, self.w_px - 0.5, self.h_px - 0.5, radius_px,
                                 fill=self.style["fill"] or self._bg,
                                 outline=self.style["outline"],
                                 width=1 if self.style["outline"] else 0)
        self._label = self.create_text(0, 0, text=text, fill=self.style["fg"],
                                       font=self._font, anchor="center")
        self._icon_item = (
            self.create_text(0, 0, text=icon, fill=self.style["fg"],
                             font=self._icon_font, anchor="center")
            if icon else None
        )
        self._layout()

        self.configure(cursor="hand2")
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    # -------------------------------------------------------------- تخطيط

    def _layout(self) -> None:
        """ترتيب الأيقونة يمين النص (اتجاه من اليمين لليسار)."""
        cx, cy = self.w_px / 2, self.h_px / 2
        if self._icon_item is None:
            self.coords(self._label, cx, cy)
            return
        gap = px(8)
        text_w = self._font.measure(self._text) if self._text else 0
        icon_w = self._icon_font.measure(self._icon or "")
        total = text_w + (gap if text_w else 0) + icon_w
        right = cx + total / 2
        self.coords(self._icon_item, right - icon_w / 2, cy)
        self.coords(self._label, right - icon_w - gap - text_w / 2, cy)

    # -------------------------------------------------------------- حالات

    def _paint(self, key: str) -> None:
        self.itemconfig(self._shape, fill=self.style[key] or self._bg)

    def _on_enter(self, _event=None) -> None:
        if self._enabled:
            self._paint("hover")

    def _on_leave(self, _event=None) -> None:
        self._pressed = False
        if self._enabled:
            self._paint("fill")

    def _on_press(self, _event=None) -> None:
        if self._enabled:
            self._pressed = True
            self._paint("press")

    def _on_release(self, event=None) -> None:
        if not (self._enabled and self._pressed):
            return
        self._pressed = False
        self._paint("hover")
        # ينفَّذ الأمر فقط إذا رُفع الزر داخل حدود الزر نفسه
        inside = event and 0 <= event.x <= self.w_px and 0 <= event.y <= self.h_px
        if inside and self.command:
            self.command()

    # -------------------------------------------------------------- واجهة

    def set_text(self, text: str) -> None:
        self._text = text
        self.itemconfig(self._label, text=bidi(text))
        self._layout()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        if enabled:
            self._paint("fill")
            self.itemconfig(self._label, fill=self.style["fg"])
        else:
            self.itemconfig(self._shape, fill=P.surface2)
            self.itemconfig(self._label, fill=P.faint)
        if self._icon_item:
            self.itemconfig(self._icon_item,
                            fill=self.style["fg"] if enabled else P.faint)


# ------------------------------------------------------------------ لوحة

class RoundedPanel(BidiCanvas):
    """لوحة بحواف دائرية؛ تُضاف العناصر داخل panel.body."""

    def __init__(self, master: tk.Misc, fill: str = P.surface, outline: str = P.stroke,
                 radius: int = 18, pad: int = 18, bg: str | None = None,
                 outline_width: int = 1) -> None:
        super().__init__(master, bg=bg or parent_bg(master), highlightthickness=0, bd=0)
        self._fill, self._outline, self._ow = fill, outline, outline_width
        self._radius, self._pad = px(radius), px(pad)
        self._shape: int | None = None

        self.body = tk.Frame(self, bg=fill)
        self._window = self.create_window(0, 0, window=self.body, anchor="nw")
        self.bind("<Configure>", self._redraw)

    def _redraw(self, event=None) -> None:
        w = event.width if event else self.winfo_width()
        h = event.height if event else self.winfo_height()
        if w <= 1 or h <= 1:
            return
        if self._shape is not None:
            self.delete(self._shape)
        self._shape = round_rect(self, 0.5, 0.5, w - 0.5, h - 0.5, self._radius,
                                 fill=self._fill, outline=self._outline, width=self._ow)
        self.tag_lower(self._shape)
        self.coords(self._window, self._pad, self._pad)
        self.itemconfig(self._window, width=max(1, w - 2 * self._pad),
                        height=max(1, h - 2 * self._pad))

    def set_fill(self, fill: str, outline: str | None = None) -> None:
        self._fill = fill
        if outline is not None:
            self._outline = outline
        self.body.configure(bg=fill)
        self._redraw()


# ------------------------------------------------------------------ حقل

class GEntry(tk.Frame):
    """حقل إدخال داخل إطار دائري، مع نص إرشادي وإظهار/إخفاء كلمة المرور."""

    def __init__(self, master: tk.Misc, placeholder: str = "", show: str | None = None,
                 width: int = 320, height: int = 50, icon: str | None = None,
                 on_submit: Callable[[], None] | None = None,
                 justify: str = "center", bg: str | None = None) -> None:
        self.t = theme()
        bg = bg or parent_bg(master)
        super().__init__(master, bg=bg)

        self.w_px, self.h_px = px(width), px(height)
        self._show = show
        self._masked = show is not None

        self.canvas = BidiCanvas(self, width=self.w_px, height=self.h_px, bg=bg,
                                highlightthickness=0, bd=0)
        self.canvas.pack()
        self._shape = round_rect(self.canvas, 1, 1, self.w_px - 1, self.h_px - 1,
                                 px(height * 0.30), fill=P.surface2,
                                 outline=P.stroke, width=1)

        pad = px(16)
        left, right = pad, self.w_px - pad

        # أيقونة على اليمين (بداية السطر في الاتجاه العربي)
        if icon:
            self.canvas.create_text(right - px(4), self.h_px / 2, text=icon, fill=P.faint,
                                    font=self.t.emoji_font(px(17)), anchor="e")
            right -= px(30)

        # زر إظهار كلمة المرور على اليسار
        self._eye_item = None
        if show is not None:
            self._eye_item = self.canvas.create_text(
                left + px(2), self.h_px / 2, text="\U0001F441", fill=P.faint,
                font=self.t.emoji_font(px(16)), anchor="w")
            for event_name, color in (("<Enter>", P.text), ("<Leave>", P.faint)):
                self.canvas.tag_bind(
                    self._eye_item, event_name,
                    lambda e, c=color: self.canvas.itemconfig(self._eye_item, fill=c))
            self.canvas.tag_bind(self._eye_item, "<Button-1>", self._toggle_mask)
            left += px(28)

        self.var = tk.StringVar()
        self.entry = tk.Entry(
            self.canvas, textvariable=self.var, bd=0, highlightthickness=0,
            bg=P.surface2, fg=P.text, insertbackground=P.primary,
            font=self.t.font(12), show=show or "", justify=justify,
        )
        self.canvas.create_window((left + right) / 2, self.h_px / 2, window=self.entry,
                                  width=max(px(40), right - left),
                                  height=self.h_px - px(16), anchor="center")

        # النص الإرشادي يُكتب داخل الحقل نفسه: Tk يرسم الودجات المدمجة
        # فوق كل عناصر الـCanvas، فلا يصلح رسمه كنص على اللوحة.
        self._placeholder_text = placeholder
        self._placeholder_on = False

        self.entry.bind("<FocusIn>", self._on_focus_in)
        self.entry.bind("<FocusOut>", self._on_focus_out)
        if on_submit:
            self.entry.bind("<Return>", lambda e: on_submit())
        self._apply_placeholder()

    # -------------------------------------------------------------- سلوك

    def _apply_placeholder(self) -> None:
        if self._placeholder_on or not self._placeholder_text or self.var.get():
            return
        self._placeholder_on = True
        self.entry.configure(show="", fg=P.faint)
        self.var.set(self._placeholder_text)

    def _clear_placeholder(self) -> None:
        if not self._placeholder_on:
            return
        self._placeholder_on = False
        self.var.set("")
        self.entry.configure(show=self._show if self._masked else "", fg=P.text)

    def _on_focus_in(self, _event=None) -> None:
        self._clear_placeholder()
        self.canvas.itemconfig(self._shape, outline=P.primary, width=2)

    def _on_focus_out(self, _event=None) -> None:
        self.canvas.itemconfig(self._shape, outline=P.stroke, width=1)
        self._apply_placeholder()

    def _toggle_mask(self, _event=None) -> None:
        self._masked = not self._masked
        if not self._placeholder_on:
            self.entry.configure(show=self._show if self._masked else "")
        self.canvas.itemconfig(self._eye_item, fill=P.faint if self._masked else P.primary)

    def mark_error(self, is_error: bool = True) -> None:
        self.canvas.itemconfig(self._shape,
                               outline=P.danger if is_error else P.stroke,
                               width=2 if is_error else 1)

    # -------------------------------------------------------------- واجهة

    def get(self) -> str:
        return "" if self._placeholder_on else self.var.get()

    def set(self, value: str) -> None:
        self._clear_placeholder()
        self.var.set(value)
        self.entry.configure(fg=P.text)

    def clear(self) -> None:
        self._placeholder_on = False
        self.var.set("")
        self._apply_placeholder()

    def focus(self) -> None:
        self.entry.focus_set()


# ------------------------------------------------------------------ شارة

class StatChip(BidiCanvas):
    """شارة صغيرة تعرض عنواناً وقيمة (تُستخدم في شريط اللعب)."""

    def __init__(self, master: tk.Misc, label_text: str, value: str = "—",
                 width: int = 108, height: int = 56, accent: str = P.accent,
                 bg: str | None = None) -> None:
        self.t = theme()
        self.w_px, self.h_px = px(width), px(height)
        super().__init__(master, width=self.w_px, height=self.h_px,
                         bg=bg or parent_bg(master), highlightthickness=0, bd=0)

        self._shape = round_rect(self, 0.5, 0.5, self.w_px - 0.5, self.h_px - 0.5,
                                 px(14), fill=P.surface, outline=P.stroke, width=1)
        cx = self.w_px / 2
        self.create_text(cx, self.h_px * 0.30, text=label_text, fill=P.muted,
                         font=self.t.font(9), anchor="center")
        self._value = self.create_text(cx, self.h_px * 0.66, text=value, fill=accent,
                                       font=self.t.num_font(14), anchor="center")
        self._accent = accent

    def set_value(self, value: str, color: str | None = None) -> None:
        self.itemconfig(self._value, text=value, fill=color or self._accent)

    def flash(self, color: str = P.success, duration: int = 320) -> None:
        """وميض قصير للفت الانتباه عند تغيّر القيمة."""
        self.itemconfig(self._shape, outline=color, width=2)
        self.after(duration,
                   lambda: self.itemconfig(self._shape, outline=P.stroke, width=1))


# ------------------------------------------------------------------ صورة

class Avatar(BidiCanvas):
    """دائرة ملوّنة تحمل رمز اللاعب."""

    def __init__(self, master: tk.Misc, emoji: str = "", size: int = 52,
                 ring: str = P.primary, bg: str | None = None) -> None:
        self.t = theme()
        self._size = px(size)
        super().__init__(master, width=self._size, height=self._size,
                         bg=bg or parent_bg(master), highlightthickness=0, bd=0)
        pad = px(2)
        self._circle = self.create_oval(pad, pad, self._size - pad, self._size - pad,
                                        fill=mix(P.surface2, ring, 0.25), outline=ring,
                                        width=px(2))
        self._emoji = self.create_text(self._size / 2, self._size / 2, text=emoji,
                                       fill=P.text,
                                       font=self.t.emoji_font(self._size * 0.5))

    def set_avatar(self, emoji: str) -> None:
        self.itemconfig(self._emoji, text=emoji)

    def set_ring(self, color: str) -> None:
        self.itemconfig(self._circle, outline=color, fill=mix(P.surface2, color, 0.25))


# ------------------------------------------------------------------ تمرير

class ScrollFrame(tk.Frame):
    """حاوية قابلة للتمرير عموديّاً؛ تُضاف العناصر داخل ScrollFrame.body."""

    def __init__(self, master: tk.Misc, bg: str | None = None) -> None:
        bg = bg or parent_bg(master)
        super().__init__(master, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)

        self.body = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window(0, 0, window=self.body, anchor="nw")

        self.body.bind("<Configure>", self._on_body_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.bind_all("<MouseWheel>", self._on_wheel, add="+")

    def _on_body_configure(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfig(self._window, width=event.width)

    def _on_wheel(self, event) -> None:
        # يمرّر فقط عندما يكون المؤشر فوق هذه الحاوية تحديداً
        if not self.winfo_exists():
            return
        widget = self.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            if widget is self:
                self.canvas.yview_scroll(-1 * (event.delta // 120), "units")
                return
            widget = getattr(widget, "master", None)


# ------------------------------------------------------------------ مساعدات

def label(master: tk.Misc, text: str, size: float = 11, color: str = P.text,
          weight: str = "normal", bg: str | None = None, **kwargs) -> tk.Label:
    """اختصار لإنشاء نص بخط الواجهة."""
    return tk.Label(master, text=bidi(text), fg=color, bg=bg or parent_bg(master),
                    font=(theme().family, int(round(size)), weight), **kwargs)


def emoji_label(master: tk.Misc, text: str, pixels: int = 20,
                color: str = P.text, bg: str | None = None, **kwargs) -> tk.Label:
    return tk.Label(master, text=bidi(text), fg=color, bg=bg or parent_bg(master),
                    font=theme().emoji_font(px(pixels)), **kwargs)


# ==============================================================================
# 9أ. شاشة الدخول وإنشاء الحساب
# ==============================================================================

class AuthScreen(tk.Frame):
    """عمودان: لوحة تعريفية على اليسار ونموذج الدخول على اليمين."""

    def __init__(self, master: tk.Misc, app) -> None:
        super().__init__(master, bg=P.bg)
        self.app = app
        self.mode = "login"                       # login | register
        self.selected_avatar = random.choice(AVATARS)
        self._avatar_widgets: list[tuple[Avatar, str]] = []

        self.columnconfigure(0, weight=1, minsize=px(380))
        self.columnconfigure(1, weight=0, minsize=px(470))
        self.rowconfigure(0, weight=1)

        self._build_hero()
        self._build_form()

    # ------------------------------------------------------------ اليسار

    def _build_hero(self) -> None:
        self.hero = BidiCanvas(self, bg=P.bg, highlightthickness=0, bd=0)
        self.hero.grid(row=0, column=0, sticky="nsew")
        self.hero.bind("<Configure>", self._draw_hero)

    def _draw_hero(self, event=None) -> None:
        c = self.hero
        c.delete("all")
        width = event.width if event else c.winfo_width()
        height = event.height if event else c.winfo_height()
        if width <= 1 or height <= 1:
            return

        cx = width / 2
        top = height * 0.20

        # هالة ضوئية خلف البطاقات
        glow = px(150)
        for i, ratio in enumerate((0.10, 0.16, 0.24)):
            r = glow - i * px(34)
            c.create_oval(cx - r, top - r * 0.72, cx + r, top + r * 0.72,
                          fill=mix(P.bg, P.primary, ratio), outline="")

        # ثلاث بطاقات مبعثرة كأنها في يد لاعب
        card_w, card_h = px(96), px(132)
        specs = [
            (-px(112), px(14), "back", None),
            (0, -px(10), "face", SYMBOLS[17]),
            (px(112), px(14), "face", SYMBOLS[3]),
        ]
        for dx, dy, kind, symbol in specs:
            x1, y1 = cx + dx - card_w / 2, top + dy - card_h / 2
            x2, y2 = x1 + card_w, y1 + card_h
            if kind == "back":
                round_rect(c, x1, y1, x2, y2, px(16), fill=P.card_back,
                             outline=P.card_back_edge, width=px(2))
                c.create_text((x1 + x2) / 2, (y1 + y2) / 2, text="❖",
                              fill=P.card_motif,
                              font=self.app.theme.emoji_font(px(34)))
            else:
                glyph, color = symbol
                round_rect(c, x1, y1, x2, y2, px(16), fill=P.card_face,
                             outline=P.card_face_edge, width=px(2))
                c.create_text((x1 + x2) / 2, (y1 + y2) / 2, text=glyph, fill=color,
                              font=self.app.theme.emoji_font(px(48)))

        # العنوان والوصف
        title_y = top + px(120)
        c.create_text(cx, title_y, text=APP_NAME, fill=P.text,
                      font=self.app.theme.title(26), anchor="center")
        c.create_text(cx, title_y + px(38), text=APP_TAGLINE, fill=P.muted,
                      font=self.app.theme.font(12), anchor="center")

        # مزايا مختصرة
        features = [
            ("\U0001F3AE", "أربعة مستويات صعوبة"),
            ("⏱", "سباق مع الوقت ومكافأة للسلاسل"),
            ("\U0001F3C6", "لوحة صدارة تجمع نقاط الجميع"),
        ]
        y = title_y + px(86)
        for icon, text in features:
            if y > height - px(30):
                break
            c.create_text(cx + px(126), y, text=icon, fill=P.accent, anchor="e",
                          font=self.app.theme.emoji_font(px(17)))
            c.create_text(cx + px(96), y, text=text, fill=P.muted, anchor="e",
                          font=self.app.theme.font(11))
            y += px(34)

    # ------------------------------------------------------------ اليمين

    def _build_form(self) -> None:
        wrapper = tk.Frame(self, bg=P.bg)
        wrapper.grid(row=0, column=1, sticky="nsew", padx=(0, px(28)), pady=px(28))
        wrapper.rowconfigure(0, weight=1)
        wrapper.columnconfigure(0, weight=1)

        panel = RoundedPanel(wrapper, fill=P.surface, outline=P.stroke, radius=22, pad=26)
        panel.grid(row=0, column=0, sticky="nsew")

        # التذييل أولاً في الأسفل، ثم المحتوى يتمدّد ليتوسّط ما تبقّى عمودياً
        label(panel.body, "الحسابات محفوظة محلياً على جهازك فقط", size=9,
                color=P.faint).pack(side="bottom", pady=(px(12), 0))
        body = tk.Frame(panel.body, bg=P.surface)
        body.pack(expand=True, fill="x")

        self.heading = label(body, "أهلاً بعودتك", size=19, weight="bold")
        self.heading.pack(anchor="e", pady=(px(4), 0))
        self.subheading = label(body, "سجّل الدخول لمتابعة نقاطك", size=11, color=P.muted)
        self.subheading.pack(anchor="e", pady=(px(4), px(18)))

        # مبدّل بين الدخول وإنشاء حساب
        tabs = tk.Frame(body, bg=P.surface2, padx=px(4), pady=px(4))
        tabs.pack(fill="x", pady=(0, px(18)))
        self.tab_login = GButton(tabs, text="تسجيل الدخول", kind="primary",
                                 width=190, height=38, radius=10, font_size=10,
                                 command=lambda: self._set_mode("login"), bg=P.surface2)
        self.tab_register = GButton(tabs, text="حساب جديد", kind="ghost",
                                    width=190, height=38, radius=10, font_size=10,
                                    command=lambda: self._set_mode("register"), bg=P.surface2)
        self.tab_login.pack(side="right", expand=True, fill="x")
        self.tab_register.pack(side="right", expand=True, fill="x")

        self.fields = tk.Frame(body, bg=P.surface)
        self.fields.pack(fill="x")

        self.error = label(body, "", size=10, color=P.danger, wraplength=px(360),
                             justify="right")
        self.error.pack(anchor="e", pady=(px(10), 0))

        self.submit = GButton(body, text="دخول", kind="primary", width=330, height=50,
                              font_size=12, command=self._submit)
        self.submit.pack(pady=(px(14), px(10)))

        self.guest = GButton(body, text="الدخول كضيف", kind="ghost", width=330,
                             height=40, font_size=10, command=self._play_as_guest)
        self.guest.pack()

        self._build_fields()

    # ------------------------------------------------------------ الحقول

    def _build_fields(self) -> None:
        for child in self.fields.winfo_children():
            child.destroy()

        self.username = GEntry(self.fields, placeholder="اسم المستخدم", width=330,
                               icon="\U0001F464", on_submit=self._submit)
        self.username.pack(pady=(0, px(10)))

        self.password = GEntry(self.fields, placeholder="كلمة المرور", show="•",
                               width=330, icon="\U0001F512", on_submit=self._submit)
        self.password.pack(pady=(0, px(10)))

        self.confirm = None
        if self.mode == "register":
            self.confirm = GEntry(self.fields, placeholder="تأكيد كلمة المرور",
                                  show="•", width=330, icon="\U0001F501",
                                  on_submit=self._submit)
            self.confirm.pack(pady=(0, px(12)))
            self._build_avatar_picker()
        else:
            saved = self.app.settings.get("last_username", "")
            if saved:
                self.username.set(saved)

        self.username.focus()

    def _build_avatar_picker(self) -> None:
        label(self.fields, "اختر صورتك", size=10, color=P.muted).pack(
            anchor="e", pady=(0, px(8)))

        grid = tk.Frame(self.fields, bg=P.surface)
        grid.pack(fill="x")
        self._avatar_widgets = []

        per_row = 6
        for index, emoji in enumerate(AVATARS):
            row, col = divmod(index, per_row)
            # العمود معكوس ليقرأ الاختيار من اليمين إلى اليسار
            avatar = Avatar(grid, emoji=emoji, size=44,
                              ring=P.primary if emoji == self.selected_avatar else P.stroke)
            avatar.grid(row=row, column=per_row - 1 - col, padx=px(3), pady=px(3))
            avatar.configure(cursor="hand2")
            avatar.bind("<Button-1>", lambda e, s=emoji: self._select_avatar(s))
            self._avatar_widgets.append((avatar, emoji))

    def _select_avatar(self, emoji: str) -> None:
        self.selected_avatar = emoji
        for avatar, value in self._avatar_widgets:
            avatar.set_ring(P.primary if value == emoji else P.stroke)
        self.app.play("click")

    # ------------------------------------------------------------ الأوضاع

    def _set_mode(self, mode: str) -> None:
        if mode == self.mode:
            return
        self.mode = mode
        self.app.play("click")
        self._show_error("")

        is_login = mode == "login"
        self.tab_login.style = dict(GButton.STYLES["primary" if is_login else "ghost"])
        self.tab_register.style = dict(GButton.STYLES["ghost" if is_login else "primary"])
        for tab in (self.tab_login, self.tab_register):
            tab.itemconfig(tab._shape, fill=tab.style["fill"] or P.surface2)
            tab.itemconfig(tab._label, fill=tab.style["fg"])

        self.heading.configure(text="أهلاً بعودتك" if is_login else "أنشئ حسابك")
        self.subheading.configure(
            text="سجّل الدخول لمتابعة نقاطك" if is_login
            else "احفظ نتائجك وتنافس على الصدارة")
        self.submit.set_text("دخول" if is_login else "إنشاء الحساب")
        self._build_fields()

    # ------------------------------------------------------------ الإرسال

    def _show_error(self, message: str) -> None:
        self.error.configure(text=message)
        for field in (self.username, self.password, self.confirm):
            if field is not None:
                field.mark_error(False)

    def _submit(self) -> None:
        username = self.username.get()
        password = self.password.get()
        try:
            if self.mode == "login":
                user = self.app.auth.login(username, password)
            else:
                user = self.app.auth.register(
                    username, password,
                    self.confirm.get() if self.confirm else "",
                    self.selected_avatar,
                )
        except AuthError as exc:
            self._show_error(str(exc))
            self.app.play("mismatch")
            target = self.confirm if ("متطابقتين" in str(exc) and self.confirm) else self.password
            if "الاسم" in str(exc) or "مستخدم" in str(exc):
                target = self.username
            target.mark_error(True)
            target.focus()
            return

        self.app.play("match")
        self.app.sign_in(user)

    def _play_as_guest(self) -> None:
        self.app.play("click")
        self.app.continue_as_guest()


# ==============================================================================
# 9ب. القائمة الرئيسية
# ==============================================================================

class DifficultyCard(BidiCanvas):
    """بطاقة مستوى قابلة للنقر مع تأثير عند مرور الفأرة."""

    W, H = 214, 286

    def __init__(self, master: tk.Misc, difficulty: Difficulty, best: dict | None,
                 command) -> None:
        self.t = theme()
        self.w_px, self.h_px = px(self.W), px(self.H)
        super().__init__(master, width=self.w_px, height=self.h_px, bg=P.bg,
                         highlightthickness=0, bd=0, cursor="hand2")

        self.difficulty = difficulty
        self.command = command
        accent = difficulty.color

        self._shape = round_rect(self, 1, 1, self.w_px - 1, self.h_px - 1, px(20),
                                   fill=P.surface, outline=P.stroke, width=1)

        # دائرة ملوّنة تحمل رقم المستوى
        badge_r = px(26)
        cx, top = self.w_px / 2, px(46)
        self.create_oval(cx - badge_r, top - badge_r, cx + badge_r, top + badge_r,
                         fill=mix(P.surface, accent, 0.22), outline=accent, width=px(2))
        self.create_text(cx, top, text=str(DIFFICULTY_ORDER.index(difficulty.key) + 1),
                         fill=accent, font=self.t.num_font(20))

        self.create_text(cx, top + px(54), text=difficulty.label, fill=P.text,
                         font=self.t.title(17))

        rows = [
            ("▦", f"{difficulty.total_cards} بطاقة  ·  {difficulty.grid_label}"),
            ("⏱", f"{difficulty.seconds // 60}:{difficulty.seconds % 60:02d} دقيقة"),
            ("✖", f"مضاعف النقاط {difficulty.multiplier:g}"),
        ]
        y = top + px(92)
        for icon, text in rows:
            self.create_text(cx + px(88), y, text=icon, fill=P.faint, anchor="e",
                             font=self.t.emoji_font(px(13)))
            self.create_text(cx + px(66), y, text=text, fill=P.muted, anchor="e",
                             font=self.t.font(10))
            y += px(27)

        # أفضل نتيجة سابقة في هذا المستوى
        divider_y = y + px(6)
        self.create_line(px(24), divider_y, self.w_px - px(24), divider_y, fill=P.stroke)
        if best and best.get("best_score"):
            label_text = f"أفضل نتيجة  {best['best_score']:,}"
            sub = f"أسرع وقت  {format_duration(best.get('best_time'))}"
            color = P.gold
        else:
            label_text = "لم تجرّبه بعد"
            sub = "ابدأ أول جولة"
            color = P.faint
        self.create_text(cx, divider_y + px(24), text=label_text, fill=color,
                         font=self.t.num_font(12))
        self.create_text(cx, divider_y + px(45), text=sub, fill=P.faint,
                         font=self.t.font(9))

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", lambda e: self.command(self.difficulty))

    def _on_enter(self, _event=None) -> None:
        self.itemconfig(self._shape, fill=P.surface2,
                        outline=self.difficulty.color, width=px(2))

    def _on_leave(self, _event=None) -> None:
        self.itemconfig(self._shape, fill=P.surface, outline=P.stroke, width=1)


class MenuScreen(tk.Frame):
    """شاشة البداية بعد تسجيل الدخول."""

    def __init__(self, master: tk.Misc, app) -> None:
        super().__init__(master, bg=P.bg)
        self.app = app
        self._build_header()
        self._build_body()

    # ------------------------------------------------------------- الرأس

    def _build_header(self) -> None:
        bar = tk.Frame(self, bg=P.bg)
        bar.pack(fill="x", padx=px(28), pady=(px(22), px(6)))

        # --- يمين: هوية اللاعب
        identity = tk.Frame(bar, bg=P.bg)
        identity.pack(side="right")

        Avatar(identity, emoji=self.app.avatar, size=54,
                 ring=P.primary if self.app.can_save_scores else P.faint
                 ).pack(side="right", padx=(0, px(12)))

        names = tk.Frame(identity, bg=P.bg)
        names.pack(side="right")
        label(names, f"مرحباً، {self.app.display_name}", size=16,
                weight="bold").pack(anchor="e")

        if self.app.can_save_scores:
            stats = self.app.db.user_stats(self.app.user["id"])
            rank = self.app.db.user_rank(self.app.user["id"])
            rank_text = f"المركز {rank}" if rank else "لم تدخل الترتيب بعد"
            subtitle = f"{stats['total_score']:,} نقطة  ·  {rank_text}"
        else:
            subtitle = "وضع الضيف — النتائج لا تُحفظ"
        label(names, subtitle, size=10, color=P.muted).pack(anchor="e")

        # --- يسار: أزرار التنقّل
        actions = tk.Frame(bar, bg=P.bg)
        actions.pack(side="left")

        GButton(actions, text="خروج", kind="ghost", width=88, height=40,
                font_size=10, command=self._sign_out).pack(side="left", padx=px(4))

        self.sound_button = GButton(
            actions, text="", kind="soft", width=52, height=40,
            icon="\U0001F50A" if self.app.sound.enabled else "\U0001F507",
            command=self._toggle_sound)
        self.sound_button.pack(side="left", padx=px(4))

        if self.app.can_save_scores:
            GButton(actions, text="ملفي", kind="soft", width=104, height=40,
                    icon="\U0001F464", font_size=10,
                    command=lambda: self._go("profile")).pack(side="left", padx=px(4))

        GButton(actions, text="لوحة الصدارة", kind="soft", width=148, height=40,
                icon="\U0001F3C6", font_size=10,
                command=lambda: self._go("leaderboard")).pack(side="left", padx=px(4))

    # ------------------------------------------------------------- المحتوى

    def _build_body(self) -> None:
        body = tk.Frame(self, bg=P.bg)
        body.pack(fill="both", expand=True, padx=px(28), pady=(px(10), px(24)))

        label(body, "اختر مستوى الصعوبة", size=20, weight="bold").pack(
            anchor="e", pady=(px(10), px(2)))
        label(body, "كل مستوى أصعب يمنحك مضاعف نقاط أعلى",
                size=11, color=P.muted).pack(anchor="e", pady=(0, px(20)))

        cards = tk.Frame(body, bg=P.bg)
        cards.pack(expand=True)

        best_scores = (
            self.app.db.best_score_by_difficulty(self.app.user["id"])
            if self.app.can_save_scores else {}
        )
        # نعرضها من اليمين لليسار: الأسهل أولاً
        for key in DIFFICULTY_ORDER:
            difficulty = DIFFICULTIES[key]
            card = DifficultyCard(cards, difficulty, best_scores.get(key), self._start)
            card.pack(side="right", padx=px(9))

        hint = tk.Frame(body, bg=P.bg)
        hint.pack(pady=(px(18), 0))
        label(hint, "اقلب بطاقتين في كل محاولة — المطابقات المتتالية تضاعف نقاطك",
                size=10, color=P.faint).pack()

    # ------------------------------------------------------------- أوامر

    def _start(self, difficulty: Difficulty) -> None:
        self.app.play("click")
        self.app.settings.set("last_difficulty", difficulty.key)
        self.app.show("game", difficulty_key=difficulty.key)

    def _go(self, screen: str) -> None:
        self.app.play("click")
        self.app.show(screen)

    def _sign_out(self) -> None:
        self.app.play("click")
        self.app.sign_out()

    def _toggle_sound(self) -> None:
        enabled = self.app.toggle_sound()
        self.sound_button._icon = "\U0001F50A" if enabled else "\U0001F507"
        self.sound_button.itemconfig(self.sound_button._icon_item,
                                     text=self.sound_button._icon)


# ==============================================================================
# 9ج. شاشة اللعب
# ==============================================================================

# مراحل تصغير البطاقة أثناء القلب (تعطي إيهام الدوران حول المحور الرأسي)
FLIP_STEPS = (1.0, 0.78, 0.54, 0.28, 0.06)
FLIP_FRAME_MS = 22


class GameScreen(tk.Frame):
    """جولة كاملة من بدايتها حتى شاشة النتيجة."""

    def __init__(self, master: tk.Misc, app, difficulty_key: str = "easy") -> None:
        super().__init__(master, bg=P.bg)
        self.app = app
        self.difficulty = DIFFICULTIES[difficulty_key]
        self.game = MemoryGame(self.difficulty)

        # الحالة المرئية (قد تتأخّر عن حالة المنطق أثناء الحركة)
        self.face_up = [False] * len(self.game.cards)
        self.rects: list[tuple[float, float, float, float]] = []
        self.card_w = self.card_h = 0.0

        self.started = False
        self.paused = False
        self.elapsed = 0.0
        self._start_stamp = 0.0
        self._timer_job: str | None = None
        self._mismatch_job: str | None = None
        self._anim_jobs: set[str] = set()
        self._overlay_widgets: list[tk.Widget] = []
        self._breakdown = None

        self._build_hud()
        self._build_board()
        self._bind_keys()

    # ============================================================== الواجهة

    def _build_hud(self) -> None:
        bar = tk.Frame(self, bg=P.bg)
        bar.pack(fill="x", padx=px(22), pady=(px(18), px(10)))

        # --- يمين: الرجوع واسم المستوى
        right = tk.Frame(bar, bg=P.bg)
        right.pack(side="right")
        GButton(right, text="رجوع", kind="ghost", width=92, height=42, font_size=10,
                icon="↩", command=self._confirm_exit).pack(side="right")

        chip = BidiCanvas(right, width=px(104), height=px(42), bg=P.bg,
                         highlightthickness=0, bd=0)
        chip.pack(side="right", padx=(px(10), px(8)))
        round_rect(chip, 0.5, 0.5, px(104) - 0.5, px(42) - 0.5, px(12),
                     fill=mix(P.surface, self.difficulty.color, 0.18),
                     outline=self.difficulty.color, width=1)
        chip.create_text(px(52), px(21), text=self.difficulty.label,
                         fill=self.difficulty.color, font=self.app.theme.num_font(12))

        # --- يسار: أزرار التحكّم
        left = tk.Frame(bar, bg=P.bg)
        left.pack(side="left")
        GButton(left, text="", kind="soft", width=52, height=42, icon="⏸",
                command=self._toggle_pause).pack(side="left", padx=px(4))
        GButton(left, text="", kind="soft", width=52, height=42, icon="↻",
                command=self._restart).pack(side="left", padx=px(4))

        # --- الوسط: شارات الإحصاء
        center = tk.Frame(bar, bg=P.bg)
        center.pack()
        self.chips: dict[str, StatChip] = {}
        specs = [
            ("time", "الوقت", format_duration(self.difficulty.seconds), P.accent, 112),
            ("score", "النقاط", "0", P.gold, 112),
            ("pairs", "الأزواج", f"0/{self.game.total_pairs}", P.success, 112),
            ("moves", "الحركات", "0", P.text, 100),
            ("combo", "السلسلة", "×0", P.primary_hi, 100),
        ]
        # نرصّها من اليمين لليسار
        for key, label_text, value, accent, width in specs:
            chip_widget = StatChip(center, label_text, value, width=width, accent=accent)
            chip_widget.pack(side="right", padx=px(5))
            self.chips[key] = chip_widget

        # --- شريط الوقت
        self.progress = BidiCanvas(self, height=px(6), bg=P.bg,
                                  highlightthickness=0, bd=0)
        self.progress.pack(fill="x", padx=px(22), pady=(0, px(8)))
        self.progress.bind("<Configure>", lambda e: self._draw_progress())

    def _build_board(self) -> None:
        self.board = BidiCanvas(self, bg=P.bg, highlightthickness=0, bd=0)
        self.board.pack(fill="both", expand=True, padx=px(16), pady=(0, px(16)))
        self.board.bind("<Configure>", self._on_resize)
        self.board.bind("<Button-1>", self._on_click)
        self.board.bind("<Motion>", self._on_motion)

    def _bind_keys(self) -> None:
        root = self.app.root
        root.bind("<Escape>", lambda e: self._toggle_pause())
        root.bind("<F2>", lambda e: self._restart())

    def teardown(self) -> None:
        """إيقاف كل المؤقّتات قبل إغلاق الشاشة."""
        for job in (self._timer_job, self._mismatch_job):
            if job:
                self.after_cancel(job)
        for job in list(self._anim_jobs):
            self.after_cancel(job)
        self._anim_jobs.clear()
        for sequence in ("<Escape>", "<F2>"):
            self.app.root.unbind(sequence)

    # ============================================================== التخطيط

    def _on_resize(self, event=None) -> None:
        width = event.width if event else self.board.winfo_width()
        height = event.height if event else self.board.winfo_height()
        if width <= 1 or height <= 1:
            return

        cols, rows = self.difficulty.cols, self.difficulty.rows
        pad, gap = px(8), px(10)
        cell_w = (width - 2 * pad - (cols - 1) * gap) / cols
        cell_h = (height - 2 * pad - (rows - 1) * gap) / rows

        # الحفاظ على نسبة بطاقة اللعب المعتادة
        aspect = 1.32
        if cell_h / cell_w > aspect:
            cell_h = cell_w * aspect
        else:
            cell_w = cell_h / aspect
        self.card_w, self.card_h = max(24.0, cell_w), max(32.0, cell_h)

        grid_w = cols * self.card_w + (cols - 1) * gap
        grid_h = rows * self.card_h + (rows - 1) * gap
        ox = (width - grid_w) / 2
        oy = (height - grid_h) / 2

        self.rects = []
        for index in range(len(self.game.cards)):
            row, col = divmod(index, cols)
            # العمود معكوس: أول بطاقة في أعلى اليمين
            x1 = ox + (cols - 1 - col) * (self.card_w + gap)
            y1 = oy + row * (self.card_h + gap)
            self.rects.append((x1, y1, x1 + self.card_w, y1 + self.card_h))

        self._redraw_all()

    def _redraw_all(self) -> None:
        self.board.delete("card")
        for index in range(len(self.game.cards)):
            self._draw_card(index)
        if self._overlay_widgets or self.paused or self.game.finished:
            self._refresh_overlay()

    # ============================================================== البطاقة

    def _draw_card(self, index: int, scale_x: float = 1.0, scale_y: float = 1.0,
                   highlight: bool = False) -> None:
        if index >= len(self.rects):
            return
        self.board.delete(f"c{index}")

        x1, y1, x2, y2 = self.rects[index]
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        half_w = (x2 - x1) / 2 * scale_x
        half_h = (y2 - y1) / 2 * scale_y
        tags = ("card", f"c{index}")
        radius = px(13) * min(1.0, scale_x * 1.6)

        matched = index in self.game.matched
        showing = self.face_up[index] or matched

        if matched:
            fill, outline, width = P.card_matched, P.card_matched_edge, px(2)
        elif showing:
            fill, outline, width = P.card_face, P.card_face_edge, px(2)
        elif highlight:
            fill, outline, width = P.card_back_hi, P.card_back_edge, px(2)
        else:
            fill, outline, width = P.card_back, P.card_back_edge, px(2)

        round_rect(self.board, cx - half_w, cy - half_h, cx + half_w, cy + half_h,
                     radius, fill=fill, outline=outline, width=width, tags=tags)

        # يختفي المحتوى عندما تصبح البطاقة رفيعة جداً أثناء القلب
        if scale_x < 0.42:
            return

        if showing:
            glyph, color = self.game.symbol_at(index)
            size = self.card_h * 0.40
            self.board.create_text(cx, cy, text=glyph,
                                   fill=color if not matched else mix(color, P.card_matched, 0.25),
                                   font=self.app.theme.emoji_font(size), tags=tags)
        else:
            # زخرفة ظهر البطاقة
            size = self.card_h * 0.26
            self.board.create_text(cx, cy, text="❖", fill=P.card_motif,
                                   font=self.app.theme.emoji_font(size), tags=tags)
            dot_r = max(1.0, self.card_w * 0.018)
            for dx, dy in ((-0.30, -0.34), (0.30, -0.34), (-0.30, 0.34), (0.30, 0.34)):
                px_, py_ = cx + dx * self.card_w, cy + dy * self.card_h
                self.board.create_oval(px_ - dot_r, py_ - dot_r, px_ + dot_r, py_ + dot_r,
                                       fill=P.card_motif, outline="", tags=tags)

    def _animate_flip(self, index: int, to_face_up: bool, on_done=None) -> None:
        """تصغير البطاقة، تبديل وجهها، ثم إعادة تكبيرها."""
        sequence = list(FLIP_STEPS) + [None] + list(reversed(FLIP_STEPS))

        def step(position: int = 0) -> None:
            if not self.winfo_exists():
                return
            if position >= len(sequence):
                if on_done:
                    on_done()
                return
            value = sequence[position]
            if value is None:
                self.face_up[index] = to_face_up   # لحظة انقلاب الوجه
            else:
                self._draw_card(index, scale_x=value)
            job = self.after(FLIP_FRAME_MS, lambda: step(position + 1))
            self._anim_jobs.add(job)

        step()

    def _pop(self, index: int) -> None:
        """نبضة صغيرة تؤكّد نجاح المطابقة."""
        def shrink() -> None:
            if self.winfo_exists():
                self._draw_card(index)
        self._draw_card(index, scale_x=1.07, scale_y=1.07)
        self._anim_jobs.add(self.after(120, shrink))

    # ============================================================== التفاعل

    def _hit_test(self, x: float, y: float) -> int | None:
        for index, (x1, y1, x2, y2) in enumerate(self.rects):
            if x1 <= x <= x2 and y1 <= y <= y2:
                return index
        return None

    def _on_motion(self, event) -> None:
        if self.paused or self.game.finished:
            self.board.configure(cursor="")
            return
        index = self._hit_test(event.x, event.y)
        clickable = (index is not None and index not in self.game.matched
                     and not self.face_up[index])
        self.board.configure(cursor="hand2" if clickable else "")

    def _on_click(self, event) -> None:
        if self.paused or self.game.finished:
            return
        index = self._hit_test(event.x, event.y)
        if index is None:
            return

        # نقرة أثناء انتظار إخفاء زوج خاطئ: نحسمه فوراً بدل تجاهل النقرة
        if self._mismatch_job is not None:
            self.after_cancel(self._mismatch_job)
            self._mismatch_job = None
            self._resolve_mismatch()

        result = self.game.flip(index)
        if result is FlipResult.IGNORED:
            return

        if not self.started:
            self._start_timer()

        self.app.play("flip")

        if result is FlipResult.FIRST:
            self._animate_flip(index, True)
        elif result is FlipResult.MATCH:
            self._animate_flip(index, True, on_done=self._on_match)
        else:
            self._animate_flip(index, True, on_done=self._on_mismatch)

        self._update_hud()

    def _on_match(self) -> None:
        for index in self.game.matched:
            self._draw_card(index)
            self._pop(index)
        self.app.play("combo" if self.game.combo >= 3 else "match")
        self.chips["score"].flash(P.gold)
        if self.game.combo >= 2:
            self.chips["combo"].flash(P.primary_hi)
        self._update_hud()

        if self.game.is_complete:
            self._anim_jobs.add(self.after(360, lambda: self._finish(won=True)))

    def _on_mismatch(self) -> None:
        self.app.play("mismatch")
        self._mismatch_job = self.after(MISMATCH_DELAY_MS, self._resolve_mismatch)

    def _resolve_mismatch(self) -> None:
        self._mismatch_job = None
        pending = [i for i in self.game.revealed if i not in self.game.matched]
        self.game.hide_unmatched()
        for index in pending:
            self._animate_flip(index, False)

    # ============================================================== المؤقّت

    def _start_timer(self) -> None:
        self.started = True
        self._start_stamp = time.monotonic()
        self._tick()

    def _tick(self) -> None:
        if self.paused or self.game.finished or not self.winfo_exists():
            return
        self.elapsed = time.monotonic() - self._start_stamp
        remaining = self.seconds_left
        self._update_time_chip(remaining)
        self._draw_progress()

        if remaining <= 0:
            self.game.time_out()
            self._finish(won=False)
            return
        self._timer_job = self.after(200, self._tick)

    @property
    def seconds_left(self) -> float:
        return max(0.0, self.difficulty.seconds - self.elapsed)

    def _update_time_chip(self, remaining: float) -> None:
        color = P.accent
        if remaining <= 10:
            color = P.danger
        elif remaining <= 30:
            color = P.warning
        self.chips["time"].set_value(format_duration(remaining), color)

    def _draw_progress(self) -> None:
        canvas = self.progress
        canvas.delete("all")
        width = canvas.winfo_width()
        height = px(6)
        if width <= 1:
            return
        round_rect(canvas, 0, 0, width, height, height / 2, fill=P.surface2, outline="")

        ratio = self.seconds_left / self.difficulty.seconds if self.started else 1.0
        ratio = max(0.0, min(1.0, ratio))
        if ratio <= 0:
            return
        color = P.accent if ratio > 0.33 else (P.warning if ratio > 0.15 else P.danger)
        fill_w = max(height, width * ratio)
        # يتقلّص من اليسار ليبقى الطرف الثابت على اليمين
        round_rect(canvas, width - fill_w, 0, width, height, height / 2,
                     fill=color, outline="")

    # ============================================================== الشارات

    def _update_hud(self) -> None:
        game = self.game
        self.chips["score"].set_value(f"{game.score:,}")
        self.chips["pairs"].set_value(f"{game.matches}/{game.total_pairs}")
        self.chips["moves"].set_value(str(game.moves))
        self.chips["combo"].set_value(
            f"×{game.combo}", P.primary_hi if game.combo >= 2 else P.faint)

    # ============================================================== النهاية

    def _finish(self, won: bool) -> None:
        if self._timer_job:
            self.after_cancel(self._timer_job)
            self._timer_job = None
        self.game.finished, self.game.won = True, won

        self._breakdown = self.game.compute_final_score(self.seconds_left)
        self._update_hud()
        self.chips["score"].set_value(f"{self._breakdown.total:,}")

        if self.app.can_save_scores:
            self.app.db.save_game(
                user_id=self.app.user["id"],
                difficulty=self.difficulty.key,
                score=self._breakdown.total,
                moves=self.game.moves,
                matches=self.game.matches,
                duration=self.elapsed,
                best_combo=self.game.best_combo,
                won=won,
            )

        self.app.play("win" if won else "lose")
        self._show_overlay("win" if won else "lose")

    # ============================================================== التغطية

    def _clear_overlay(self) -> None:
        self.board.delete("overlay")
        for widget in self._overlay_widgets:
            widget.destroy()
        self._overlay_widgets = []

    def _refresh_overlay(self) -> None:
        if self.paused:
            self._show_overlay("pause")
        elif self.game.finished:
            self._show_overlay("win" if self.game.won else "lose")

    def _show_overlay(self, kind: str) -> None:
        self._clear_overlay()
        canvas = self.board
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width <= 1 or height <= 1:
            return

        # طبقة تعتيم (Tk بلا شفافية: نستخدم نقشاً متقطّعاً)
        canvas.create_rectangle(0, 0, width, height, fill=P.scrim, outline="",
                                stipple="gray75", tags="overlay")

        panel_w = min(px(460), width - px(40))
        panel_h = min(px(430) if kind == "win" else px(360), height - px(20))
        x1, y1 = (width - panel_w) / 2, (height - panel_h) / 2
        x2, y2 = x1 + panel_w, y1 + panel_h
        round_rect(canvas, x1, y1, x2, y2, px(22), fill=P.surface,
                     outline=P.stroke_hi, width=px(2), tags="overlay")

        cx = (x1 + x2) / 2
        y = y1 + px(34)

        titles = {
            "win": ("\U0001F389", "أحسنت!", P.success),
            "lose": ("⏰", "انتهى الوقت", P.danger),
            "pause": ("⏸", "اللعبة متوقفة", P.accent),
        }
        icon, title, color = titles[kind]
        canvas.create_text(cx, y, text=icon, fill=color,
                           font=self.app.theme.emoji_font(px(38)), tags="overlay")
        y += px(46)
        canvas.create_text(cx, y, text=title, fill=P.text,
                           font=self.app.theme.title(21), tags="overlay")
        y += px(34)

        if kind == "pause":
            canvas.create_text(cx, y, text="خذ نفَسك — المؤقّت متوقف",
                               fill=P.muted, font=self.app.theme.font(11), tags="overlay")
            y += px(40)
            self._overlay_buttons(cx, y, [
                ("متابعة", "primary", self._toggle_pause),
                ("إعادة", "soft", self._restart),
                ("القائمة", "ghost", self._go_menu),
            ])
            return

        y = self._draw_stars(cx, y, self.game.stars())
        y = self._draw_breakdown(cx, y, x1, x2, kind)

        if not self.app.can_save_scores:
            canvas.create_text(cx, y, text="أنشئ حساباً لحفظ نتائجك في لوحة الصدارة",
                               fill=P.warning, font=self.app.theme.font(9.5),
                               tags="overlay")
            y += px(24)

        buttons = [("مرة أخرى", "primary", self._restart),
                   ("القائمة", "soft", self._go_menu)]
        if self.app.can_save_scores:
            buttons.append(("الصدارة", "ghost", self._go_leaderboard))
        self._overlay_buttons(cx, y + px(4), buttons)

    def _draw_stars(self, cx: float, y: float, stars: int) -> float:
        gap = px(38)
        for i in range(3):
            # النجوم مرتبة من اليمين لليسار
            x = cx + gap - i * gap
            earned = i < stars
            self.board.create_text(
                x, y, text="★" if earned else "☆",
                fill=P.gold if earned else P.faint,
                font=self.app.theme.emoji_font(px(30) if earned else px(26)),
                tags="overlay")
        return y + px(38)

    def _draw_breakdown(self, cx: float, y: float, x1: float, x2: float,
                        kind: str) -> float:
        breakdown = self._breakdown
        canvas = self.board
        right = x2 - px(44)
        left = x1 + px(44)

        rows = [("المطابقات", f"+{breakdown.matches_points:,}", P.text)]
        if breakdown.combo_points:
            rows.append(("مكافأة السلاسل", f"+{breakdown.combo_points:,}", P.primary_hi))
        if breakdown.penalty_points:
            rows.append(("خصم المحاولات الخاطئة", f"-{breakdown.penalty_points:,}", P.danger))
        if breakdown.time_bonus:
            rows.append(("مكافأة الوقت المتبقّي", f"+{breakdown.time_bonus:,}", P.accent))
        if breakdown.perfect_bonus:
            rows.append(("جولة مثالية بلا أخطاء", f"+{breakdown.perfect_bonus:,}", P.gold))
        rows.append((f"مضاعف {self.difficulty.label}", f"×{breakdown.multiplier:g}", P.muted))

        for title, value, color in rows:
            canvas.create_text(right, y, text=title, fill=P.muted, anchor="e",
                               font=self.app.theme.font(10), tags="overlay")
            canvas.create_text(left, y, text=value, fill=color, anchor="w",
                               font=self.app.theme.num_font(11), tags="overlay")
            y += px(23)

        y += px(4)
        canvas.create_line(left, y, right, y, fill=P.stroke, tags="overlay")
        y += px(20)
        canvas.create_text(right, y, text="المجموع", fill=P.text, anchor="e",
                           font=self.app.theme.title(13), tags="overlay")
        canvas.create_text(left, y, text=f"{breakdown.total:,}", fill=P.gold, anchor="w",
                           font=self.app.theme.num_font(18), tags="overlay")
        y += px(24)

        summary = (f"{self.game.matches} من {self.game.total_pairs} أزواج  ·  "
                   f"{self.game.moves} حركة  ·  دقة {self.game.accuracy:.0%}")
        canvas.create_text(cx, y, text=summary, fill=P.faint,
                           font=self.app.theme.font(9.5), tags="overlay")
        return y + px(26)

    def _overlay_buttons(self, cx: float, y: float, specs: list) -> None:
        """أزرار حقيقية توضع فوق اللوحة عبر create_window."""
        widths = {"primary": 132, "soft": 116, "ghost": 108}
        total = sum(widths[kind] for _, kind, _ in specs) + 10 * (len(specs) - 1)
        cursor = cx + px(total) / 2
        for text, kind, command in specs:
            width = widths[kind]
            button = GButton(self.board, text=text, kind=kind, width=width, height=44,
                             font_size=11, command=command, bg=P.surface)
            self.board.create_window(cursor - px(width) / 2, y + px(22),
                                     window=button, tags="overlay")
            self._overlay_widgets.append(button)
            cursor -= px(width + 10)

    # ============================================================== الأوامر

    def _toggle_pause(self) -> None:
        if self.game.finished:
            return
        self.paused = not self.paused
        self.app.play("click")

        if self.paused:
            if self._timer_job:
                self.after_cancel(self._timer_job)
                self._timer_job = None
            self._show_overlay("pause")
        else:
            self._clear_overlay()
            if self.started:
                # نُزيح نقطة البداية حتى لا يُحتسب زمن التوقّف
                self._start_stamp = time.monotonic() - self.elapsed
                self._tick()

    def _restart(self) -> None:
        self.app.play("click")
        self.app.show("game", difficulty_key=self.difficulty.key)

    def _go_menu(self) -> None:
        self.app.play("click")
        self.app.show("menu")

    def _go_leaderboard(self) -> None:
        self.app.play("click")
        self.app.show("leaderboard")

    def _confirm_exit(self) -> None:
        """الخروج أثناء جولة جارية يوقفها أولاً ليؤكّد اللاعب."""
        if self.game.finished or not self.started:
            self._go_menu()
            return
        if not self.paused:
            self._toggle_pause()


# ==============================================================================
# 9د. لوحة الصدارة
# ==============================================================================

MEDALS = {1: ("\U0001F947", P.gold), 2: ("\U0001F948", P.silver), 3: ("\U0001F949", P.bronze)}

# مواضع الأعمدة من الحافة اليسرى
COL_TOTAL, COL_BEST, COL_GAMES = 30, 152, 268


class LeaderRow(BidiCanvas):
    """صف لاعب واحد في الترتيب."""

    H = 62

    def __init__(self, master: tk.Misc, entry: dict, is_me: bool) -> None:
        self.t = theme()
        super().__init__(master, height=px(self.H), bg=P.bg,
                         highlightthickness=0, bd=0)
        self.entry = entry
        self.is_me = is_me
        self.bind("<Configure>", self._draw)

    def _draw(self, event=None) -> None:
        self.delete("all")
        width = event.width if event else self.winfo_width()
        height = px(self.H)
        if width <= 1:
            return

        entry = self.entry
        rank = entry["rank"]
        medal = MEDALS.get(rank)
        accent = medal[1] if medal else P.stroke

        if self.is_me:
            fill, outline = mix(P.surface, P.primary, 0.16), P.primary
        elif medal:
            fill, outline = mix(P.surface, accent, 0.08), accent
        else:
            fill, outline = P.surface, P.stroke

        round_rect(self, 1, 3, width - 1, height - 3, px(14),
                     fill=fill, outline=outline, width=1)

        mid = height / 2
        right = width - px(22)

        # --- الترتيب
        if medal:
            self.create_text(right - px(12), mid, text=medal[0], fill=accent,
                             font=self.t.emoji_font(px(22)))
        else:
            self.create_text(right - px(12), mid, text=str(rank), fill=P.muted,
                             font=self.t.num_font(13))

        # --- الصورة والاسم
        avatar_x = right - px(52)
        radius = px(17)
        self.create_oval(avatar_x - radius, mid - radius, avatar_x + radius, mid + radius,
                         fill=mix(P.surface2, accent, 0.20), outline=accent, width=1)
        self.create_text(avatar_x, mid, text=entry["avatar"] or "\U0001F464",
                         fill=P.text, font=self.t.emoji_font(px(18)))

        name = entry["username"] + ("  (أنت)" if self.is_me else "")
        self.create_text(right - px(80), mid - px(9), text=name,
                         fill=P.text if not self.is_me else P.primary_hi, anchor="e",
                         font=self.t.num_font(12))
        wins = entry["wins"] or 0
        self.create_text(right - px(80), mid + px(11),
                         text=f"{wins} فوز  ·  أسرع وقت {format_duration(entry['best_time'])}",
                         fill=P.faint, anchor="e", font=self.t.font(9))

        # --- الأرقام
        self.create_text(px(COL_TOTAL), mid, text=f"{entry['total_score']:,}",
                         fill=P.gold, anchor="w", font=self.t.num_font(15))
        self.create_text(px(COL_BEST), mid, text=f"{entry['best_score']:,}",
                         fill=P.text, anchor="w", font=self.t.num_font(12))
        self.create_text(px(COL_GAMES), mid, text=str(entry["games"]),
                         fill=P.muted, anchor="w", font=self.t.num_font(12))


class LeaderboardScreen(tk.Frame):
    """ترتيب عام أو مُصفّى حسب مستوى الصعوبة."""

    def __init__(self, master: tk.Misc, app) -> None:
        super().__init__(master, bg=P.bg)
        self.app = app
        self.filter_key: str | None = None
        self._filter_buttons: dict[str | None, GButton] = {}

        self._build_header()
        self._build_filters()
        self._build_table()
        self._load()

    # ------------------------------------------------------------- الرأس

    def _build_header(self) -> None:
        bar = tk.Frame(self, bg=P.bg)
        bar.pack(fill="x", padx=px(28), pady=(px(22), px(8)))

        GButton(bar, text="رجوع", kind="ghost", width=92, height=42, icon="↩",
                font_size=10, command=self._back).pack(side="left")

        titles = tk.Frame(bar, bg=P.bg)
        titles.pack(side="right")
        label(titles, "لوحة الصدارة", size=20, weight="bold").pack(anchor="e")
        count = self.app.db.player_count()
        label(titles, f"{count} لاعباً سجّلوا نتائج حتى الآن" if count
                else "لا نتائج بعد — كن أول المتصدّرين",
                size=10, color=P.muted).pack(anchor="e")

    def _build_filters(self) -> None:
        row = tk.Frame(self, bg=P.bg)
        row.pack(fill="x", padx=px(28), pady=(px(6), px(12)))

        options: list[tuple[str | None, str]] = [(None, "كل المستويات")]
        options += [(key, DIFFICULTIES[key].label) for key in DIFFICULTY_ORDER]

        for key, text in options:
            button = GButton(row, text=text, kind="primary" if key is None else "soft",
                             width=118, height=38, radius=12, font_size=10,
                             command=lambda k=key: self._set_filter(k))
            button.pack(side="right", padx=px(4))
            self._filter_buttons[key] = button

    def _build_table(self) -> None:
        # عناوين الأعمدة
        head = BidiCanvas(self, height=px(26), bg=P.bg, highlightthickness=0, bd=0)
        head.pack(fill="x", padx=px(28))
        head.bind("<Configure>", lambda e: self._draw_head(head, e.width))

        self.scroller = ScrollFrame(self, bg=P.bg)
        self.scroller.pack(fill="both", expand=True, padx=px(28), pady=(px(2), px(20)))

    def _draw_head(self, canvas: tk.Canvas, width: int) -> None:
        canvas.delete("all")
        if width <= 1:
            return
        font = self.app.theme.font(9)
        canvas.create_text(width - px(34), px(13), text="#", fill=P.faint,
                           anchor="e", font=font)
        canvas.create_text(width - px(80), px(13), text="اللاعب", fill=P.faint,
                           anchor="e", font=font)
        canvas.create_text(px(COL_TOTAL), px(13), text="مجموع النقاط", fill=P.faint,
                           anchor="w", font=font)
        canvas.create_text(px(COL_BEST), px(13), text="أفضل نتيجة", fill=P.faint,
                           anchor="w", font=font)
        canvas.create_text(px(COL_GAMES), px(13), text="الجولات", fill=P.faint,
                           anchor="w", font=font)

    # ------------------------------------------------------------- البيانات

    def _set_filter(self, key: str | None) -> None:
        if key == self.filter_key:
            return
        self.app.play("click")
        self.filter_key = key
        for value, button in self._filter_buttons.items():
            style = GButton.STYLES["primary" if value == key else "soft"]
            button.style = dict(style)
            button.itemconfig(button._shape, fill=style["fill"], outline=style["outline"])
            button.itemconfig(button._label, fill=style["fg"])
        self._load()

    def _load(self) -> None:
        for child in self.scroller.body.winfo_children():
            child.destroy()

        entries = self.app.db.leaderboard(self.filter_key, limit=100)
        my_id = self.app.user["id"] if self.app.can_save_scores else None

        if not entries:
            self._empty_state()
            return

        for entry in entries:
            row = LeaderRow(self.scroller.body, entry, is_me=entry["user_id"] == my_id)
            row.pack(fill="x", pady=px(3))

        # إذا كان اللاعب خارج القائمة المعروضة نثبّت صفّه في الأسفل
        if my_id is not None and all(e["user_id"] != my_id for e in entries):
            rank = self.app.db.user_rank(my_id, self.filter_key)
            if rank:
                mine = next((e for e in self.app.db.leaderboard(self.filter_key, 100000)
                             if e["user_id"] == my_id), None)
                if mine:
                    label(self.scroller.body, "مركزك", size=9,
                            color=P.faint).pack(anchor="e", pady=(px(10), px(2)))
                    LeaderRow(self.scroller.body, mine, is_me=True).pack(
                        fill="x", pady=px(3))

    def _empty_state(self) -> None:
        box = tk.Frame(self.scroller.body, bg=P.bg)
        box.pack(fill="both", expand=True, pady=px(70))
        emoji_label(box, "\U0001F3C6", pixels=46, color=P.faint).pack()
        label(box, "لا توجد نتائج في هذا المستوى بعد", size=13,
                weight="bold").pack(pady=(px(12), px(4)))
        label(box, "العب جولة واحدة ليظهر اسمك هنا", size=10,
                color=P.muted).pack()
        GButton(box, text="ابدأ اللعب", kind="primary", width=160, height=44,
                command=self._back).pack(pady=px(18))

    def _back(self) -> None:
        self.app.play("click")
        self.app.show("menu")


# ==============================================================================
# 9هـ. الملف الشخصي
# ==============================================================================

def _arabic_date(iso_text: str) -> str:
    try:
        return datetime.fromisoformat(iso_text).strftime("%Y/%m/%d")
    except (ValueError, TypeError):
        return "—"


class StatBox(BidiCanvas):
    """مربّع إحصائية واحد."""

    W, H = 158, 92

    def __init__(self, master: tk.Misc, title: str, value: str, icon: str,
                 accent: str = P.accent) -> None:
        self.t = theme()
        self.w_px, self.h_px = px(self.W), px(self.H)
        super().__init__(master, width=self.w_px, height=self.h_px, bg=P.bg,
                         highlightthickness=0, bd=0)

        round_rect(self, 0.5, 0.5, self.w_px - 0.5, self.h_px - 0.5, px(16),
                     fill=P.surface, outline=P.stroke, width=1)
        self.create_text(self.w_px - px(16), px(22), text=icon, fill=accent, anchor="e",
                         font=self.t.emoji_font(px(16)))
        self.create_text(self.w_px - px(42), px(22), text=title, fill=P.muted, anchor="e",
                         font=self.t.font(9.5))
        self.create_text(self.w_px - px(16), px(60), text=value, fill=accent, anchor="e",
                         font=self.t.num_font(19))


class ProfileScreen(tk.Frame):
    """ملخّص أداء اللاعب مع إمكانية تغيير صورته."""

    def __init__(self, master: tk.Misc, app) -> None:
        super().__init__(master, bg=P.bg)
        self.app = app
        self.user = app.user
        self.stats = app.db.user_stats(self.user["id"])

        self._build_header()
        self.scroller = ScrollFrame(self, bg=P.bg)
        self.scroller.pack(fill="both", expand=True, padx=px(28), pady=(px(4), px(18)))

        body = self.scroller.body
        self._build_identity(body)
        self._build_stats(body)
        self._build_by_difficulty(body)
        self._build_recent(body)

    # ------------------------------------------------------------- الرأس

    def _build_header(self) -> None:
        bar = tk.Frame(self, bg=P.bg)
        bar.pack(fill="x", padx=px(28), pady=(px(22), px(10)))
        GButton(bar, text="رجوع", kind="ghost", width=92, height=42, icon="↩",
                font_size=10, command=self._back).pack(side="left")
        label(bar, "ملفي الشخصي", size=20, weight="bold").pack(side="right")

    # ------------------------------------------------------------- الهوية

    def _build_identity(self, parent: tk.Misc) -> None:
        panel = RoundedPanel(parent, fill=P.surface, radius=20, pad=20)
        panel.configure(height=px(150))
        panel.pack(fill="x", pady=(px(4), px(14)))
        panel.pack_propagate(False)
        body = panel.body

        right = tk.Frame(body, bg=P.surface)
        right.pack(side="right", fill="y")

        self.avatar = Avatar(right, emoji=self.user["avatar"], size=76, bg=P.surface)
        self.avatar.pack(side="right", padx=(0, px(16)))

        info = tk.Frame(right, bg=P.surface)
        info.pack(side="right", anchor="n", pady=px(4))
        label(info, self.user["username"], size=18, weight="bold",
                bg=P.surface).pack(anchor="e")

        rank = self.app.db.user_rank(self.user["id"])
        rank_text = f"المركز {rank} في لوحة الصدارة" if rank else "لم تدخل الترتيب بعد"
        label(info, rank_text, size=11, color=P.gold if rank and rank <= 3 else P.muted,
                bg=P.surface).pack(anchor="e", pady=(px(4), 0))
        label(info, f"عضو منذ {_arabic_date(self.user['created_at'])}", size=9.5,
                color=P.faint, bg=P.surface).pack(anchor="e", pady=(px(2), 0))

        # تغيير الصورة الرمزية
        left = tk.Frame(body, bg=P.surface)
        left.pack(side="left", anchor="n")
        label(left, "غيّر صورتك", size=9.5, color=P.muted,
                bg=P.surface).pack(anchor="w", pady=(0, px(6)))

        grid = tk.Frame(left, bg=P.surface)
        grid.pack()
        self._avatar_widgets = []
        for index, emoji in enumerate(AVATARS):
            row, col = divmod(index, 6)
            item = Avatar(grid, emoji=emoji, size=34, bg=P.surface,
                            ring=P.primary if emoji == self.user["avatar"] else P.stroke)
            item.grid(row=row, column=5 - col, padx=px(2), pady=px(2))
            item.configure(cursor="hand2")
            item.bind("<Button-1>", lambda e, s=emoji: self._change_avatar(s))
            self._avatar_widgets.append((item, emoji))

    def _change_avatar(self, emoji: str) -> None:
        self.app.db.set_avatar(self.user["id"], emoji)
        self.user["avatar"] = emoji
        self.avatar.set_avatar(emoji)
        for item, value in self._avatar_widgets:
            item.set_ring(P.primary if value == emoji else P.stroke)
        self.app.play("click")

    # ------------------------------------------------------------- الأرقام

    def _build_stats(self, parent: tk.Misc) -> None:
        label(parent, "إحصائياتك", size=14, weight="bold").pack(
            anchor="e", pady=(px(6), px(10)))

        stats = self.stats
        boxes = [
            ("مجموع النقاط", f"{stats['total_score']:,}", "\U0001F3C6", P.gold),
            ("أفضل نتيجة", f"{stats['best_score']:,}", "⭐", P.warning),
            ("الجولات", str(stats["games"]), "\U0001F3AE", P.text),
            ("الانتصارات", str(stats["wins"]), "\U0001F947", P.success),
            ("نسبة الفوز", f"{stats['win_rate']:.0%}", "\U0001F4C8", P.accent),
            ("الدقة", f"{stats['accuracy']:.0%}", "\U0001F3AF", P.primary_hi),
            ("أسرع وقت", format_duration(stats["best_time"]), "⏱", P.accent),
            ("أطول سلسلة", f"×{stats['best_combo']}", "\U0001F525", P.danger),
        ]

        grid = tk.Frame(parent, bg=P.bg)
        grid.pack(anchor="e")
        per_row = 4
        for index, (title, value, icon, accent) in enumerate(boxes):
            row, col = divmod(index, per_row)
            StatBox(grid, title, value, icon, accent).grid(
                row=row, column=per_row - 1 - col, padx=px(5), pady=px(5))

    # ------------------------------------------------------------- المستويات

    def _build_by_difficulty(self, parent: tk.Misc) -> None:
        label(parent, "أداؤك في كل مستوى", size=14, weight="bold").pack(
            anchor="e", pady=(px(18), px(10)))

        by_difficulty = self.app.db.best_score_by_difficulty(self.user["id"])
        row = tk.Frame(parent, bg=P.bg)
        row.pack(fill="x")

        for key in DIFFICULTY_ORDER:
            difficulty = DIFFICULTIES[key]
            record = by_difficulty.get(key)
            card = BidiCanvas(row, width=px(210), height=px(96), bg=P.bg,
                             highlightthickness=0, bd=0)
            card.pack(side="right", padx=px(5))

            round_rect(card, 0.5, 0.5, px(210) - 0.5, px(96) - 0.5, px(16),
                         fill=mix(P.surface, difficulty.color, 0.07),
                         outline=difficulty.color if record else P.stroke, width=1)
            card.create_text(px(194), px(24), text=difficulty.label,
                             fill=difficulty.color, anchor="e",
                             font=self.app.theme.num_font(13))
            if record:
                card.create_text(px(194), px(54), text=f"{record['best_score']:,}",
                                 fill=P.text, anchor="e",
                                 font=self.app.theme.num_font(18))
                card.create_text(px(194), px(78),
                                 text=f"{record['games']} جولة  ·  "
                                      f"{format_duration(record['best_time'])}",
                                 fill=P.faint, anchor="e",
                                 font=self.app.theme.font(9))
            else:
                card.create_text(px(194), px(60), text="لم تلعبه بعد", fill=P.faint,
                                 anchor="e", font=self.app.theme.font(10))

    # ------------------------------------------------------------- السجل

    def _build_recent(self, parent: tk.Misc) -> None:
        label(parent, "آخر الجولات", size=14, weight="bold").pack(
            anchor="e", pady=(px(18), px(10)))

        games = self.app.db.recent_games(self.user["id"], limit=8)
        if not games:
            label(parent, "لا توجد جولات بعد — ابدأ أول جولة لك",
                    size=10, color=P.faint).pack(anchor="e", pady=px(10))
            return

        for game in games:
            difficulty = DIFFICULTIES.get(game["difficulty"])
            card = BidiCanvas(parent, height=px(52), bg=P.bg,
                             highlightthickness=0, bd=0)
            card.pack(fill="x", pady=px(3))
            card.bind("<Configure>",
                      lambda e, c=card, g=game, d=difficulty: self._draw_game_row(c, e.width, g, d))

    def _draw_game_row(self, canvas: tk.Canvas, width: int, game: dict,
                       difficulty) -> None:
        canvas.delete("all")
        if width <= 1:
            return
        height = px(52)
        won = bool(game["won"])
        accent = P.success if won else P.danger

        round_rect(canvas, 1, 2, width - 1, height - 2, px(13),
                     fill=P.surface, outline=P.stroke, width=1)
        mid = height / 2
        right = width - px(18)

        canvas.create_text(right, mid, text="\U0001F3C6" if won else "⏰",
                           fill=accent, anchor="e",
                           font=self.app.theme.emoji_font(px(15)))
        canvas.create_text(right - px(30), mid,
                           text=difficulty.label if difficulty else game["difficulty"],
                           fill=difficulty.color if difficulty else P.text, anchor="e",
                           font=self.app.theme.num_font(11))
        canvas.create_text(right - px(96), mid,
                           text=f"{game['matches']} أزواج  ·  {game['moves']} حركة  ·  "
                                f"{format_duration(game['duration'])}",
                           fill=P.muted, anchor="e", font=self.app.theme.font(9.5))
        canvas.create_text(px(20), mid, text=f"{game['score']:,}", fill=P.gold,
                           anchor="w", font=self.app.theme.num_font(13))
        canvas.create_text(px(110), mid, text=_arabic_date(game["played_at"]),
                           fill=P.faint, anchor="w", font=self.app.theme.font(9))

    def _back(self) -> None:
        self.app.play("click")
        self.app.show("menu")


# ==============================================================================
# 10. التطبيق والتشغيل
# ==============================================================================

class App:
    """يملك النافذة والحالة المشتركة (المستخدم الحالي، الصوت، قاعدة البيانات)."""

    WINDOW_W, WINDOW_H = 1180, 760
    MIN_W, MIN_H = 940, 640

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()  # نُظهرها بعد ضبط الحجم لتفادي وميض النافذة

        self.theme = Theme(self.root)
        set_theme(self.theme)

        self.settings = Settings()
        self.db = Database()
        self.auth = AuthService(self.db)
        self.sound = SoundEngine(enabled=bool(self.settings.get("sound", True)))
        self.sound.prewarm()

        self.user: dict | None = None   # المستخدم المسجَّل حالياً
        self.is_guest = False

        self._screen: tk.Frame | None = None
        self._setup_window()

        self.container = tk.Frame(self.root, bg=P.bg)
        self.container.pack(fill="both", expand=True)

        self.show("auth")
        self.root.deiconify()

    # ------------------------------------------------------------- النافذة

    def _work_area(self) -> tuple[int, int, int, int]:
        """المساحة القابلة للاستخدام من الشاشة (بدون شريط المهام)."""
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes

                rect = wintypes.RECT()
                SPI_GETWORKAREA = 0x0030
                if ctypes.windll.user32.SystemParametersInfoW(
                    SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
                ):
                    return (rect.left, rect.top,
                            rect.right - rect.left, rect.bottom - rect.top)
            except Exception:
                pass
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _setup_window(self) -> None:
        self.root.title(f"{APP_NAME}  ·  {APP_VERSION}")
        self.root.configure(bg=P.bg)

        area_x, area_y, area_w, area_h = self._work_area()
        # geometry يصف مساحة المحتوى، بينما يشغل إطار النافذة ارتفاعاً إضافياً
        frame_h = self.theme.px(40)

        width = min(self.theme.px(self.WINDOW_W), int(area_w * 0.96))
        height = min(self.theme.px(self.WINDOW_H), area_h - frame_h - self.theme.px(12))
        x = area_x + max(0, (area_w - width) // 2)
        y = area_y + max(0, (area_h - height - frame_h) // 2)

        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(min(self.theme.px(self.MIN_W), width),
                          min(self.theme.px(self.MIN_H), height))
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

    # ------------------------------------------------------------- التوجيه

    def show(self, name: str, **kwargs) -> None:
        """استبدال الشاشة الحالية بشاشة أخرى."""

        screens = {
            "auth": AuthScreen,
            "menu": MenuScreen,
            "game": GameScreen,
            "leaderboard": LeaderboardScreen,
            "profile": ProfileScreen,
        }

        if self._screen is not None:
            # نمنح الشاشة فرصة لإيقاف مؤقّتاتها قبل الإغلاق
            teardown = getattr(self._screen, "teardown", None)
            if callable(teardown):
                teardown()
            self._screen.destroy()

        self._screen = screens[name](self.container, self, **kwargs)
        self._screen.pack(fill="both", expand=True)

    # ------------------------------------------------------------- الحساب

    @property
    def display_name(self) -> str:
        if self.is_guest:
            return "ضيف"
        return self.user["username"] if self.user else ""

    @property
    def avatar(self) -> str:
        if self.is_guest or not self.user:
            return GUEST_AVATAR
        return self.user["avatar"]

    @property
    def can_save_scores(self) -> bool:
        """نتائج الضيف لا تُحفظ ولا تدخل لوحة الصدارة."""
        return self.user is not None and not self.is_guest

    def sign_in(self, user: dict) -> None:
        self.user = user
        self.is_guest = False
        self.settings.set("last_username", user["username"])
        self.show("menu")

    def continue_as_guest(self) -> None:
        self.user = None
        self.is_guest = True
        self.show("menu")

    def sign_out(self) -> None:
        self.user = None
        self.is_guest = False
        self.show("auth")

    # ------------------------------------------------------------- الصوت

    def play(self, name: str) -> None:
        self.sound.play(name)

    def toggle_sound(self) -> bool:
        enabled = self.sound.toggle()
        self.settings.set("sound", enabled)
        if enabled:
            self.play("click")
        return enabled

    # ------------------------------------------------------------- التشغيل

    def run(self) -> None:
        self.root.mainloop()

    def quit(self) -> None:
        try:
            self.db.close()
        finally:
            self.root.destroy()


# ==============================================================================
# نقطة التشغيل
# ==============================================================================

def enable_dpi_awareness() -> None:
    """طلب دقة عرض حقيقية على ويندوز حتى لا يبدو النص مموّهاً على شاشات التكبير."""
    if sys.platform != "win32":
        return
    import ctypes

    for attempt in (
        lambda: ctypes.windll.shcore.SetProcessDpiAwareness(1),
        lambda: ctypes.windll.user32.SetProcessDPIAware(),
    ):
        try:
            attempt()
            return
        except Exception:
            continue


def main() -> int:
    enable_dpi_awareness()
    App().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
