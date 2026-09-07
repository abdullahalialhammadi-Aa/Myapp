"""لعبة العثور على البطاقات المتشابهة — نسخة الويب بـ Streamlit.

    streamlit run streamlit_app.py

تعمل في المتصفّح مع حسابات ونقاط ولوحة صدارة. تُنشئ بجانبها عند أول تشغيل
ملف memory_game.db، وحذفه يعيد كل شيء إلى نقطة الصفر.

محتويات الملف:
    1. الإعدادات        المستويات، قيم النقاط، الرموز، الصور الرمزية
    2. منطق اللعبة      توزيع البطاقات والقواعد وحساب النتيجة
    3. قاعدة البيانات   الحسابات والجولات ولوحة الصدارة
    4. الحسابات         تجزئة كلمات المرور والتحقق من المدخلات
    5. الصوت            مؤثرات مُولَّدة رياضياً تُشغَّل في المتصفّح
    6. المظهر           لوحة الألوان وأنماط CSS
    7. الحالة           حالة الجلسة وأدواتها
    8. الصفحات          الدخول، القائمة، اللعب، الصدارة، الملف الشخصي
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import math
import os
import random
import re
import sqlite3
import struct
import threading
import time
import wave
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import streamlit as st

# ==============================================================================
# 1. الإعدادات
# ==============================================================================

APP_NAME = "لعبة البطاقات المتشابهة"
APP_TAGLINE = "درّب ذاكرتك · اجمع النقاط · تصدّر القائمة"

DB_PATH = Path(__file__).resolve().parent / "memory_game.db"


@dataclass(frozen=True)
class Difficulty:
    """تعريف مستوى صعوبة واحد."""

    key: str
    label: str
    cols: int
    rows: int
    seconds: int
    multiplier: float
    color: str

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

# --- النقاط
POINTS_PER_MATCH = 100
COMBO_STEP = 25
COMBO_MAX = 100
PENALTY_PER_MISS = 10
TIME_BONUS_PER_SECOND = 5
PERFECT_BONUS = 500
STAR_THRESHOLDS = (0.80, 0.55)

# مدة عرض الزوج غير المتطابق قبل إخفائه (بالثواني)
MISMATCH_DELAY = 0.85

# (الرمز، لونه)
SYMBOLS: list[tuple[str, str]] = [
    ("🍎", "#FF5A5F"), ("🚀", "#8B7BFF"), ("🎸", "#F59E0B"), ("🐙", "#22D3EE"),
    ("🌸", "#F472B6"), ("🥕", "#FB923C"), ("🌞", "#FBBF24"), ("🌙", "#93C5FD"),
    ("⚡", "#FACC15"), ("🎯", "#EF4444"), ("🎲", "#34D399"), ("🐝", "#EAB308"),
    ("🐠", "#38BDF8"), ("🍄", "#F87171"), ("🏀", "#FB7185"), ("💧", "#60A5FA"),
    ("🔥", "#F97316"), ("⭐", "#FDE047"), ("🍉", "#4ADE80"), ("🔑", "#FCD34D"),
    ("💎", "#67E8F9"), ("🎪", "#C084FC"), ("🐞", "#F43F5E"), ("🌿", "#22C55E"),
]

AVATARS = ["🦊", "🐱", "🐶", "🐼", "🦁", "🐸", "🐵", "🐧", "🦄", "🦉", "🐯", "🐰"]
GUEST_AVATAR = "👤"


def format_duration(seconds: float | None) -> str:
    """تحويل عدد الثواني إلى صيغة m:ss."""
    if seconds is None:
        return "—"
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60}:{seconds % 60:02d}"


def arabic_date(iso_text: str) -> str:
    try:
        return datetime.fromisoformat(iso_text).strftime("%Y/%m/%d")
    except (ValueError, TypeError):
        return "—"


# ==============================================================================
# 2. منطق اللعبة
# ==============================================================================

# نتيجة النقر على بطاقة. نصوص لا Enum عن قصد: Streamlit يعيد تنفيذ الملف مع
# كل تحديث فيُنشئ أصناف الوحدة من جديد، بينما يبقى كائن اللعبة محفوظاً في
# حالة الجلسة من تنفيذ سابق — فتفشل مقارنة الهوية بين عضوَي Enum مختلفَي
# الصنف رغم تطابق قيمتيهما، وتمرّ الأخطاء صامتة.
FLIP_IGNORED = "ignored"
FLIP_FIRST = "first"
FLIP_MATCH = "match"
FLIP_MISMATCH = "mismatch"


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
        return (self.matches_points + self.combo_points + self.time_bonus
                + self.perfect_bonus - self.penalty_points)


@dataclass
class MemoryGame:
    """حالة جولة واحدة — منطق خالص لا يعرف شيئاً عن الواجهة."""

    difficulty: Difficulty
    seed: int | None = None

    cards: list[int] = field(default_factory=list)
    matched: set[int] = field(default_factory=set)
    revealed: set[int] = field(default_factory=set)

    first_pick: int | None = None
    moves: int = 0
    matches: int = 0
    misses: int = 0
    combo: int = 0
    best_combo: int = 0
    score: int = 0

    finished: bool = False
    won: bool = False

    def __post_init__(self) -> None:
        rng = random.Random(self.seed)
        deck = rng.sample(range(len(SYMBOLS)), self.difficulty.pairs) * 2
        rng.shuffle(deck)
        self.cards = deck

    # ------------------------------------------------------------ خصائص

    @property
    def total_pairs(self) -> int:
        return self.difficulty.pairs

    @property
    def accuracy(self) -> float:
        return self.matches / self.moves if self.moves else 0.0

    @property
    def is_complete(self) -> bool:
        return len(self.matched) == len(self.cards)

    def symbol_at(self, index: int) -> tuple[str, str]:
        return SYMBOLS[self.cards[index]]

    def is_face_up(self, index: int) -> bool:
        return index in self.revealed or index in self.matched

    # ------------------------------------------------------------ اللعب

    def flip(self, index: int) -> str:
        """كشف بطاقة وتحديث الحالة."""
        if self.finished or index in self.matched or index in self.revealed:
            return FLIP_IGNORED
        if len(self.revealed) >= 2:
            # زوج غير محسوم مكشوف بالفعل — تتولّى الواجهة إخفاءه أولاً
            return FLIP_IGNORED

        self.revealed.add(index)

        if self.first_pick is None:
            self.first_pick = index
            return FLIP_FIRST

        first, self.first_pick = self.first_pick, None
        self.moves += 1

        if self.cards[first] == self.cards[index]:
            self.matched.update({first, index})
            self.revealed.difference_update({first, index})
            self.matches += 1
            self.combo += 1
            self.best_combo = max(self.best_combo, self.combo)
            self.score += POINTS_PER_MATCH + min(COMBO_STEP * (self.combo - 1), COMBO_MAX)
            if self.is_complete:
                self.finished, self.won = True, True
            return FLIP_MATCH

        self.misses += 1
        self.combo = 0
        self.score = max(0, self.score - PENALTY_PER_MISS)
        return FLIP_MISMATCH

    def hide_unmatched(self) -> None:
        """إخفاء البطاقات المكشوفة غير المتطابقة."""
        self.revealed = {i for i in self.revealed if i in self.matched}
        self.first_pick = None

    def time_out(self) -> None:
        if not self.finished:
            self.finished, self.won = True, False

    # ------------------------------------------------------------ النتيجة

    def compute_final_score(self, seconds_left: float) -> ScoreBreakdown:
        result = ScoreBreakdown(multiplier=self.difficulty.multiplier)
        result.matches_points = self.matches * POINTS_PER_MATCH
        result.penalty_points = self.misses * PENALTY_PER_MISS
        # مجموع مكافآت السلسلة = ما تراكم فعلياً أثناء اللعب
        result.combo_points = max(
            0, self.score - result.matches_points + result.penalty_points)

        if self.won:
            result.time_bonus = int(max(0, seconds_left) * TIME_BONUS_PER_SECOND)
            if self.misses == 0:
                result.perfect_bonus = PERFECT_BONUS

        result.total = max(0, int(round(result.subtotal * result.multiplier)))
        self.score = result.total
        return result

    def stars(self) -> int:
        if not self.won:
            return 0
        high, mid = STAR_THRESHOLDS
        return 3 if self.accuracy >= high else (2 if self.accuracy >= mid else 1)


# ==============================================================================
# 3. قاعدة البيانات
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
CREATE INDEX IF NOT EXISTS idx_games_user ON games(user_id);
"""


class Database:
    """غلاف حول SQLite. مشترك بين جلسات المتصفّح، لذا تُقفل عمليات الكتابة."""

    def __init__(self, path: Path | str = DB_PATH) -> None:
        # Streamlit ينفّذ إعادة التشغيل في خيوط مختلفة
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()
        self.lock = threading.Lock()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    # ------------------------------------------------------------ الحسابات

    def username_exists(self, username: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
        return row is not None

    def create_user(self, username: str, password_hash: str, salt: str,
                    avatar: str) -> dict:
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO users (username, password_hash, salt, avatar, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (username, password_hash, salt, avatar, self._now()),
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
        with self.lock:
            self.conn.execute("UPDATE users SET avatar = ? WHERE id = ?", (avatar, user_id))
            self.conn.commit()

    # ------------------------------------------------------------ الجولات

    def save_game(self, user_id: int, difficulty: str, score: int, moves: int,
                  matches: int, duration: float, best_combo: int, won: bool) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO games (user_id, difficulty, score, moves, matches,"
                " duration, best_combo, won, played_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, difficulty, int(score), int(moves), int(matches),
                 float(duration), int(best_combo), int(won), self._now()),
            )
            self.conn.commit()

    def recent_games(self, user_id: int, limit: int = 8) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM games WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ الصدارة

    def leaderboard(self, difficulty: str | None = None, limit: int = 100) -> list[dict]:
        """ترتيب اللاعبين حسب مجموع النقاط."""
        where = "WHERE g.difficulty = ?" if difficulty else ""
        params: tuple[Any, ...] = (difficulty,) if difficulty else ()
        rows = self.conn.execute(
            f"""
            SELECT u.id AS user_id, u.username, u.avatar,
                   SUM(g.score) AS total_score,
                   MAX(g.score) AS best_score,
                   COUNT(*)     AS games,
                   SUM(g.won)   AS wins,
                   MIN(CASE WHEN g.won = 1 THEN g.duration END) AS best_time
            FROM users u JOIN games g ON g.user_id = u.id
            {where}
            GROUP BY u.id
            ORDER BY total_score DESC, wins DESC, best_time ASC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return [dict(r, rank=i) for i, r in enumerate(rows, start=1)]

    def user_rank(self, user_id: int, difficulty: str | None = None) -> int | None:
        for entry in self.leaderboard(difficulty, limit=100000):
            if entry["user_id"] == user_id:
                return entry["rank"]
        return None

    def player_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(DISTINCT user_id) AS n FROM games").fetchone()
        return int(row["n"] or 0)

    # ------------------------------------------------------------ إحصاءات

    def user_stats(self, user_id: int) -> dict:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS games,
                   COALESCE(SUM(score), 0)   AS total_score,
                   COALESCE(MAX(score), 0)   AS best_score,
                   COALESCE(SUM(won), 0)     AS wins,
                   COALESCE(SUM(moves), 0)   AS moves,
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
        rows = self.conn.execute(
            """
            SELECT difficulty, MAX(score) AS best_score, COUNT(*) AS games,
                   MIN(CASE WHEN won = 1 THEN duration END) AS best_time
            FROM games WHERE user_id = ? GROUP BY difficulty
            """,
            (user_id,),
        ).fetchall()
        return {r["difficulty"]: dict(r) for r in rows}


@st.cache_resource
def get_db() -> Database:
    """اتصال واحد يشترك فيه كل زوّار التطبيق."""
    return Database()


# ==============================================================================
# 4. الحسابات والتحقق
# ==============================================================================

_ITERATIONS = 200_000
USERNAME_MIN, USERNAME_MAX = 3, 16
PASSWORD_MIN = 4
_USERNAME_RE = re.compile("^[\\w؀-ۿ]+$", re.UNICODE)


class AuthError(Exception):
    """خطأ يُعرض للمستخدم مباشرةً."""


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """يعيد (التجزئة، الملح) بصيغة hex."""
    salt = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS)
    return digest.hex(), salt


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    """مقارنة آمنة زمنياً ضد هجمات التوقيت."""
    candidate, _ = hash_password(password, salt)
    return hmac.compare_digest(candidate, expected_hash)


def validate_username(username: str) -> str | None:
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


def register(username: str, password: str, confirm: str, avatar: str) -> dict:
    username = username.strip()
    error = validate_username(username) or validate_password(password)
    if error:
        raise AuthError(error)
    if password != confirm:
        raise AuthError("كلمتا المرور غير متطابقتين")
    db = get_db()
    if db.username_exists(username):
        raise AuthError("هذا الاسم مستخدم بالفعل، جرّب اسماً آخر")
    password_hash, salt = hash_password(password)
    return db.create_user(username, password_hash, salt, avatar)


def login(username: str, password: str) -> dict:
    username = username.strip()
    if not username:
        raise AuthError("اكتب اسم المستخدم")
    if not password:
        raise AuthError("اكتب كلمة المرور")
    user = get_db().get_user_by_name(username)
    # نفس الرسالة في الحالتين حتى لا نكشف أي الأسماء مسجَّلة
    if not user or not verify_password(password, user["salt"], user["password_hash"]):
        raise AuthError("اسم المستخدم أو كلمة المرور غير صحيحة")
    return user


# ==============================================================================
# 5. الصوت — مؤثرات مُولَّدة رياضياً بلا ملفات خارجية
# ==============================================================================

SAMPLE_RATE = 22050

_RECIPES: dict[str, tuple[list[tuple[float, float]], float, str]] = {
    "flip":     ([(880, 0.05)], 0.22, "sine"),
    "match":    ([(659, 0.09), (988, 0.14)], 0.30, "sine"),
    "mismatch": ([(196, 0.10), (147, 0.14)], 0.26, "saw"),
    "combo":    ([(784, 0.07), (988, 0.07), (1319, 0.12)], 0.30, "sine"),
    "win":      ([(523, 0.11), (659, 0.11), (784, 0.11), (1047, 0.28)], 0.34, "sine"),
    "lose":     ([(392, 0.14), (330, 0.14), (247, 0.30)], 0.30, "sine"),
}


def _render_wav(notes: list[tuple[float, float]], volume: float, shape: str) -> bytes:
    """توليد ملف WAV من قائمة نغمات (التردد بالهرتز، المدة بالثواني)."""
    frames = bytearray()
    for freq, duration in notes:
        count = max(1, int(SAMPLE_RATE * duration))
        for n in range(count):
            phase = 2 * math.pi * freq * n / SAMPLE_RATE
            if shape == "saw":
                raw = 2.0 * ((freq * n / SAMPLE_RATE) % 1.0) - 1.0
            else:
                raw = math.sin(phase)
            # تخفيف تدريجي داخل النغمة يعطي إحساس النقرة الطبيعية
            amp = raw * math.exp(-3.0 * n / count) * volume
            frames += struct.pack("<h", int(max(-1.0, min(1.0, amp)) * 32767))

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(bytes(frames))
    return buffer.getvalue()


@st.cache_data(show_spinner=False)
def sound_data_uri(name: str) -> str:
    """رابط data يحمل المؤثر جاهزاً للتشغيل في المتصفّح."""
    notes, volume, shape = _RECIPES[name]
    encoded = base64.b64encode(_render_wav(notes, volume, shape)).decode("ascii")
    return f"data:audio/wav;base64,{encoded}"


def play_sound(name: str) -> None:
    """يُشغَّل المؤثر عند إعادة الرسم التالية."""
    if st.session_state.get("sound_on", True) and name in _RECIPES:
        st.session_state.sound_cue = name


def render_sound_cue() -> None:
    cue = st.session_state.pop("sound_cue", None)
    if not cue:
        return
    # المُعرِّف المتغيّر يُجبر المتصفّح على إنشاء عنصر جديد فيُعاد التشغيل
    st.session_state.sound_serial = st.session_state.get("sound_serial", 0) + 1
    st.markdown(
        f'<audio id="cue{st.session_state.sound_serial}" autoplay '
        f'src="{sound_data_uri(cue)}"></audio>',
        unsafe_allow_html=True,
    )


# ==============================================================================
# 6. المظهر
# ==============================================================================

BG = "#080C1B"
SURFACE = "#131A33"
SURFACE2 = "#1B2444"
STROKE = "#2C3765"
TEXT = "#EDF2FF"
MUTED = "#8E9BC6"
FAINT = "#5C6899"
PRIMARY = "#7C5CFF"
ACCENT = "#22D3EE"
SUCCESS = "#34D399"
WARNING = "#FBBF24"
DANGER = "#F87171"
GOLD = "#FFC857"

CARD_BACK = "#2A2170"
CARD_BACK_EDGE = "#5B49D6"
CARD_MOTIF = "#8072E8"
CARD_FACE = "#F3F6FF"
CARD_FACE_EDGE = "#C6D0EE"
CARD_MATCHED = "#17362F"
CARD_MATCHED_EDGE = "#34D399"

MEDALS = {1: ("🥇", GOLD), 2: ("🥈", "#C7D2E5"), 3: ("🥉", "#DC8A4E")}


def inject_css() -> None:
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;800&display=swap');

        /* --- إخفاء عناصر Streamlit غير اللازمة --- */
        #MainMenu, [data-testid="stToolbar"], [data-testid="stDecoration"],
        [data-testid="stStatusWidget"], footer {{ display: none !important; }}
        [data-testid="stHeader"] {{ background: transparent; height: 0; }}

        /* --- الإطار العام: اتجاه من اليمين لليسار وخلفية داكنة --- */
        [data-testid="stAppViewContainer"] {{
            direction: rtl;
            background:
                radial-gradient(1100px 520px at 80% -80px, #1B1350 0%, transparent 60%),
                radial-gradient(900px 460px at 10% -60px, #0C2A46 0%, transparent 55%),
                {BG};
            color: {TEXT};
            font-family: 'Cairo', 'Segoe UI', Tahoma, sans-serif;
        }}
        [data-testid="stMainBlockContainer"] {{
            padding: 1.6rem 2.2rem 3rem;
            max-width: 1280px;
        }}
        [data-testid="stAppViewContainer"] * {{
            font-family: 'Cairo', 'Segoe UI', Tahoma, sans-serif;
        }}
        /* أيقونات Streamlit تعتمد خطاً برموز مركّبة — تُستثنى من الخط العربي */
        [data-testid="stIconMaterial"] {{
            font-family: 'Material Symbols Rounded' !important;
        }}
        h1, h2, h3, h4, p, span, div, label {{ color: {TEXT}; }}

        /* --- الأزرار (بما فيها أزرار إرسال النماذج) --- */
        [data-testid^="stBaseButton-"] {{
            border-radius: 14px;
            font-weight: 700;
            transition: transform .12s ease, background .15s ease, border-color .15s ease;
        }}
        [data-testid="stBaseButton-secondary"],
        [data-testid="stBaseButton-secondaryFormSubmit"] {{
            background: {SURFACE2}; color: {TEXT}; border: 1px solid {STROKE};
        }}
        [data-testid="stBaseButton-secondary"]:hover,
        [data-testid="stBaseButton-secondaryFormSubmit"]:hover {{
            background: #27325C; border-color: #465393; color: {TEXT};
        }}
        [data-testid="stBaseButton-primary"],
        [data-testid="stBaseButton-primaryFormSubmit"] {{
            background: {PRIMARY}; color: #fff; border: 1px solid {PRIMARY};
        }}
        [data-testid="stBaseButton-primary"]:hover,
        [data-testid="stBaseButton-primaryFormSubmit"]:hover {{
            background: #9179FF; border-color: #9179FF; color: #fff;
        }}

        /* --- الشرائح والمبدّلات (segmented_control و pills) --- */
        [data-testid="stButtonGroup"] button {{
            background: {SURFACE2} !important; color: {MUTED} !important;
            border: 1px solid {STROKE} !important; border-radius: 12px !important;
            font-weight: 700; padding: 6px 16px; margin-inline-end: 6px;
        }}
        [data-testid="stButtonGroup"] button:hover {{
            background: #27325C !important; color: {TEXT} !important;
        }}
        [data-testid="stButtonGroup"] button[data-selected] {{
            background: {PRIMARY} !important; color: #fff !important;
            border-color: {PRIMARY} !important;
        }}
        [data-testid="stButtonGroup"] button p {{ color: inherit !important; }}

        /* --- بطاقات اللعب --- */
        [class*="st-key-card_"] button {{
            height: var(--card-h, 120px);
            font-size: var(--card-f, 42px);
            line-height: 1;
            border-radius: 16px;
            border: 2px solid {CARD_BACK_EDGE};
            background: {CARD_BACK};
            color: {CARD_MOTIF};
            padding: 0;
        }}
        [class*="st-key-card_"] button:hover {{
            background: #3B2E9E; transform: translateY(-2px);
        }}
        [class*="st-key-card_"] button:disabled {{ opacity: 1; cursor: default; }}
        [class*="st-key-card_"] {{ margin-bottom: 6px; }}
        /* نص الزر داخل حاوية markdown لها حجمها الخاص — نرفع التحديد لتجاوزه */
        [class*="st-key-card_"] button [data-testid="stMarkdownContainer"] p {{
            font-size: var(--card-f, 42px) !important;
            line-height: 1 !important;
            margin: 0 !important;
        }}
        /* صفوف اللوحة وحدها تُحصر في عرض يجعل البطاقات طوليّة */
        [data-testid="stHorizontalBlock"]:has([class*="st-key-card_"]) {{
            max-width: var(--board-w, 900px);
            margin-inline: auto;
        }}

        @keyframes flipIn {{
            0%   {{ transform: rotateY(90deg) scale(.94); }}
            100% {{ transform: rotateY(0deg) scale(1); }}
        }}
        @keyframes popIn {{
            0%   {{ transform: scale(1); }}
            55%  {{ transform: scale(1.07); }}
            100% {{ transform: scale(1); }}
        }}

        /* --- عناصر عامة --- */
        .panel {{
            background: {SURFACE}; border: 1px solid {STROKE};
            border-radius: 20px; padding: 22px 26px;
        }}
        /* الأرقام المسبوقة بإشارة (+ − ×) تُعزل باتجاه لاتيني، وإلا انتقلت
           الإشارة إلى الطرف الآخر داخل الفقرة العربية */
        .ltr, .chip .v, .num, .statbox .v {{
            direction: ltr; unicode-bidi: isolate;
        }}
        .muted {{ color: {MUTED}; }}
        .faint {{ color: {FAINT}; }}
        .center {{ text-align: center; }}

        .hud {{ display: flex; gap: 10px; justify-content: center; flex-wrap: wrap; }}
        .chip {{
            background: {SURFACE}; border: 1px solid {STROKE}; border-radius: 14px;
            padding: 8px 18px; min-width: 104px; text-align: center;
        }}
        .chip .k {{ font-size: .72rem; color: {MUTED}; display: block; }}
        .chip .v {{ font-size: 1.25rem; font-weight: 800; display: block; }}

        .bar {{
            height: 7px; background: {SURFACE2}; border-radius: 99px;
            overflow: hidden; margin: 14px 0 6px;
        }}
        .bar > i {{ display: block; height: 100%; border-radius: 99px; float: left; }}

        .rowcard {{
            display: flex; align-items: center; gap: 14px;
            background: {SURFACE}; border: 1px solid {STROKE};
            border-radius: 16px; padding: 12px 18px; margin-bottom: 8px;
        }}
        .rowcard .grow {{ flex: 1; }}
        .rowcard .num {{ font-weight: 800; font-size: 1.15rem; }}
        .avatar {{
            width: 42px; height: 42px; border-radius: 50%;
            display: grid; place-items: center; font-size: 22px;
            background: {SURFACE2}; border: 2px solid {STROKE};
        }}

        .statbox {{
            background: {SURFACE}; border: 1px solid {STROKE}; border-radius: 16px;
            padding: 14px 18px; margin-bottom: 10px;
        }}
        .statbox .k {{ font-size: .78rem; color: {MUTED}; }}
        .statbox .v {{ font-size: 1.5rem; font-weight: 800; }}

        /* --- الحقول --- */
        [data-testid="stTextInputRootElement"] {{
            background: {SURFACE2}; border: 1px solid {STROKE};
            border-radius: 12px; overflow: hidden;
        }}
        [data-testid="stTextInputRootElement"]:focus-within {{ border-color: {PRIMARY}; }}
        [data-testid="stTextInputField"] {{
            background: transparent !important; color: {TEXT} !important;
            height: 42px; caret-color: {PRIMARY};
        }}
        [data-testid="stTextInputField"]::placeholder {{ color: {FAINT} !important; }}
        [data-testid="stWidgetLabel"] p {{ color: {MUTED} !important; font-size: .85rem; }}
        [data-testid="stForm"] {{ border: none; padding: 0; }}
        [data-testid="stIconMaterial"] {{ color: {FAINT}; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def card_css(game: MemoryGame) -> str:
    """أنماط كل بطاقة حسب حالتها: ظهر، وجه مكشوف، أو زوج محسوم."""
    # (ارتفاع البطاقة، أقصى عرض للوحة) لكل عدد أعمدة — تُبقي البطاقة طوليّة
    height, board = {4: (190, 660), 6: (166, 850), 8: (140, 980)}.get(
        game.difficulty.cols, (150, 900))
    font = int(height * 0.40)
    rules = [f':root, [data-testid="stAppViewContainer"] {{ --card-h: {height}px;'
             f' --card-f: {font}px; --board-w: {board}px; }}']

    for index in range(len(game.cards)):
        symbol, color = game.symbol_at(index)
        fresh = index in st.session_state.get("just_flipped", ())
        if index in game.matched:
            rules.append(
                f".st-key-card_{index} button {{ background: {CARD_MATCHED} !important;"
                f" border-color: {CARD_MATCHED_EDGE} !important; color: {color} !important;"
                + (" animation: popIn .3s ease;" if fresh else "") + " opacity: .9; }}")
        elif index in game.revealed:
            rules.append(
                f".st-key-card_{index} button {{ background: {CARD_FACE} !important;"
                f" border-color: {CARD_FACE_EDGE} !important; color: {color} !important;"
                + (" animation: flipIn .28s ease;" if fresh else "") + " }}")
    return "<style>" + "\n".join(rules) + "</style>"


# ==============================================================================
# 7. الحالة
# ==============================================================================

def init_state() -> None:
    # ملاحظة: مفاتيح الودجات (auth_mode, avatar_choice, lb_filter) لا تُهيّأ هنا
    # عمداً — تهيئتها مسبقاً مع تمرير default تجعل Streamlit يُرجع الاختيار
    # إلى قيمته الأولى بعد كل نقرة.
    defaults = {
        "page": "auth",
        "user": None,
        "is_guest": False,
        "auth_error": "",
        "sound_on": True,
        "game": None,
        "started_at": None,
        "paused": False,
        "pause_started": None,
        "paused_total": 0.0,
        "hide_at": None,
        "result": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def can_save_scores() -> bool:
    """نتائج الضيف لا تُحفظ ولا تدخل لوحة الصدارة."""
    return st.session_state.user is not None and not st.session_state.is_guest


def display_name() -> str:
    if st.session_state.is_guest:
        return "ضيف"
    user = st.session_state.user
    return user["username"] if user else ""


def current_avatar() -> str:
    if st.session_state.is_guest or not st.session_state.user:
        return GUEST_AVATAR
    return st.session_state.user["avatar"]


def go(page: str) -> None:
    st.session_state.page = page


def elapsed_seconds() -> float:
    """الزمن المنقضي مع خصم فترات الإيقاف المؤقت."""
    if st.session_state.started_at is None:
        return 0.0
    now = time.time()
    total = now - st.session_state.started_at - st.session_state.paused_total
    if st.session_state.paused and st.session_state.pause_started:
        total -= now - st.session_state.pause_started
    return max(0.0, total)


def seconds_left(game: MemoryGame) -> float:
    return max(0.0, game.difficulty.seconds - elapsed_seconds())


def start_game(key: str) -> None:
    st.session_state.game = MemoryGame(DIFFICULTIES[key])
    st.session_state.started_at = None
    st.session_state.paused = False
    st.session_state.pause_started = None
    st.session_state.paused_total = 0.0
    st.session_state.hide_at = None
    st.session_state.result = None
    st.session_state.finish_synced = False
    st.session_state.just_flipped = set()
    st.session_state.page = "game"


def finish_game(won: bool) -> None:
    game = st.session_state.game
    game.finished, game.won = True, won
    st.session_state.result = game.compute_final_score(seconds_left(game))

    if can_save_scores():
        get_db().save_game(
            user_id=st.session_state.user["id"],
            difficulty=game.difficulty.key,
            score=st.session_state.result.total,
            moves=game.moves,
            matches=game.matches,
            duration=elapsed_seconds(),
            best_combo=game.best_combo,
            won=won,
        )
    play_sound("win" if won else "lose")


def on_card_click(index: int) -> None:
    """يُنفَّذ قبل إعادة الرسم: يقلب البطاقة ويحدّث الحالة."""
    game = st.session_state.game
    if game is None or game.finished or st.session_state.paused:
        return
    if st.session_state.hide_at is not None:
        # نقرة أثناء انتظار إخفاء زوج خاطئ: نحسمه فوراً
        game.hide_unmatched()
        st.session_state.hide_at = None

    partner = game.first_pick          # البطاقة الأولى من الزوج، إن وُجدت
    result = game.flip(index)
    if result == FLIP_IGNORED:
        return
    if st.session_state.started_at is None:
        st.session_state.started_at = time.time()

    # البطاقات التي تُعرض حركتها في هذه النقرة فقط، حتى لا تتكرّر مع كل تحديث
    if result == FLIP_MATCH:
        st.session_state.just_flipped = {index, partner} if partner is not None else {index}
        play_sound("combo" if game.combo >= 3 else "match")
        if game.is_complete:
            finish_game(won=True)
    elif result == FLIP_MISMATCH:
        st.session_state.just_flipped = {index}
        play_sound("mismatch")
        st.session_state.hide_at = time.time() + MISMATCH_DELAY
    else:
        st.session_state.just_flipped = {index}
        play_sound("flip")


def toggle_pause() -> None:
    if st.session_state.paused:
        st.session_state.paused_total += time.time() - st.session_state.pause_started
        st.session_state.pause_started = None
        st.session_state.paused = False
    else:
        st.session_state.pause_started = time.time()
        st.session_state.paused = True


# ==============================================================================
# 8. الصفحات
# ==============================================================================

def html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


# ------------------------------------------------------------------ الدخول

def page_auth() -> None:
    form_col, hero_col = st.columns([1, 1.05], gap="large")

    with hero_col:
        cards = "".join(
            f'<div class="hcard" style="background:{bg};border-color:{edge};'
            f'color:{color};transform:translateY({dy}px)">{glyph}</div>'
            for glyph, bg, edge, color, dy in [
                ("❖", CARD_BACK, CARD_BACK_EDGE, CARD_MOTIF, 14),
                ("⭐", CARD_FACE, CARD_FACE_EDGE, "#FDE047", -10),
                ("🐙", CARD_FACE, CARD_FACE_EDGE, "#22D3EE", 14),
            ]
        )
        features = "".join(
            f'<div class="feat"><span>{icon}</span>{text}</div>'
            for icon, text in [
                ("🎮", "أربعة مستويات صعوبة"),
                ("⏱️", "سباق مع الوقت ومكافأة للسلاسل"),
                ("🏆", "لوحة صدارة تجمع نقاط الجميع"),
            ]
        )
        html(f"""
        <style>
          .hero {{ text-align:center; padding-top: 26px; }}
          .hcards {{ display:flex; justify-content:center; gap:18px; margin-bottom:30px; }}
          .hcard {{ width:104px; height:142px; border-radius:18px; border:2px solid;
                    display:grid; place-items:center; font-size:52px; }}
          .hero h1 {{ font-size:2.3rem; font-weight:800; margin:.2rem 0; }}
          .feat {{ color:{MUTED}; margin:9px 0; font-size:.98rem; }}
          .feat span {{ margin-left:10px; }}
        </style>
        <div class="hero">
          <div class="hcards">{cards}</div>
          <h1>{APP_NAME}</h1>
          <p class="muted">{APP_TAGLINE}</p>
          <div style="margin-top:22px">{features}</div>
        </div>
        """)

    with form_col:
        html('<div style="height:26px"></div>')
        register_mode = st.session_state.get("auth_mode") == "حساب جديد"

        html(f'<h2 style="margin-bottom:2px">'
             f'{"أنشئ حسابك" if register_mode else "أهلاً بعودتك"}</h2>'
             f'<p class="muted" style="margin-top:0">'
             f'{"احفظ نتائجك وتنافس على الصدارة" if register_mode else "سجّل الدخول لمتابعة نقاطك"}</p>')

        st.segmented_control(
            "الوضع", ["تسجيل الدخول", "حساب جديد"], key="auth_mode",
            label_visibility="collapsed", default="تسجيل الدخول",
        )
        register_mode = st.session_state.auth_mode == "حساب جديد"

        with st.form("auth_form", border=False):
            username = st.text_input("اسم المستخدم", placeholder="اسمك في اللعبة")
            password = st.text_input("كلمة المرور", type="password",
                                     placeholder="٤ رموز على الأقل")
            confirm = ""
            if register_mode:
                confirm = st.text_input("تأكيد كلمة المرور", type="password")
                st.pills("اختر صورتك", AVATARS, key="avatar_choice",
                         default=AVATARS[0])

            submitted = st.form_submit_button(
                "إنشاء الحساب" if register_mode else "دخول",
                type="primary", use_container_width=True)

        if submitted:
            try:
                if register_mode:
                    user = register(username, password, confirm,
                                    st.session_state.get("avatar_choice") or AVATARS[0])
                else:
                    user = login(username, password)
                st.session_state.user = user
                st.session_state.is_guest = False
                st.session_state.auth_error = ""
                go("menu")
                st.rerun()
            except AuthError as exc:
                st.session_state.auth_error = str(exc)

        if st.session_state.auth_error:
            st.error(st.session_state.auth_error, icon="⚠️")

        if st.button("الدخول كضيف", use_container_width=True):
            st.session_state.user = None
            st.session_state.is_guest = True
            go("menu")
            st.rerun()

        html(f'<p class="faint center" style="font-size:.8rem;margin-top:18px">'
             f'الحسابات محفوظة محلياً في قاعدة بيانات بجانب التطبيق</p>')


# ------------------------------------------------------------------ القائمة

def top_bar(subtitle: str = "") -> None:
    """شريط علوي: هوية اللاعب على اليمين وأزرار التنقّل على اليسار."""
    identity, actions = st.columns([1.3, 1])

    with identity:
        db = get_db()
        if can_save_scores():
            stats = db.user_stats(st.session_state.user["id"])
            rank = db.user_rank(st.session_state.user["id"])
            info = (f"{stats['total_score']:,} نقطة · "
                    f"{'المركز ' + str(rank) if rank else 'لم تدخل الترتيب بعد'}")
        else:
            info = "وضع الضيف — النتائج لا تُحفظ"
        html(f"""
        <div style="display:flex;align-items:center;gap:14px">
          <div class="avatar" style="width:52px;height:52px;font-size:26px;
               border-color:{PRIMARY if can_save_scores() else FAINT}">{current_avatar()}</div>
          <div>
            <div style="font-size:1.25rem;font-weight:800">مرحباً، {display_name()}</div>
            <div class="muted" style="font-size:.85rem">{subtitle or info}</div>
          </div>
        </div>
        """)

    with actions:
        buttons = st.columns(4 if can_save_scores() else 3)
        i = 0
        with buttons[i]:
            if st.button("🏆 الصدارة", use_container_width=True):
                go("leaderboard")
                st.rerun()
        i += 1
        if can_save_scores():
            with buttons[i]:
                if st.button("👤 ملفي", use_container_width=True):
                    go("profile")
                    st.rerun()
            i += 1
        with buttons[i]:
            icon = "🔊" if st.session_state.sound_on else "🔇"
            if st.button(icon, use_container_width=True, help="المؤثرات الصوتية"):
                st.session_state.sound_on = not st.session_state.sound_on
                st.rerun()
        i += 1
        with buttons[i]:
            if st.button("خروج", use_container_width=True):
                st.session_state.user = None
                st.session_state.is_guest = False
                go("auth")
                st.rerun()


def page_menu() -> None:
    top_bar()
    html('<div style="height:18px"></div>')
    html('<h2 style="margin-bottom:0">اختر مستوى الصعوبة</h2>'
         '<p class="muted" style="margin-top:4px">كل مستوى أصعب يمنحك مضاعف نقاط أعلى</p>')

    best = (get_db().best_score_by_difficulty(st.session_state.user["id"])
            if can_save_scores() else {})

    columns = st.columns(4, gap="medium")
    for column, key in zip(columns, DIFFICULTY_ORDER):
        difficulty = DIFFICULTIES[key]
        record = best.get(key)
        with column:
            if record and record.get("best_score"):
                footer = (f'<div style="color:{GOLD};font-weight:800">'
                          f'أفضل نتيجة {record["best_score"]:,}</div>'
                          f'<div class="faint" style="font-size:.8rem">'
                          f'أسرع وقت {format_duration(record.get("best_time"))}</div>')
            else:
                footer = ('<div class="faint">لم تجرّبه بعد</div>'
                          '<div class="faint" style="font-size:.8rem">ابدأ أول جولة</div>')
            html(f"""
            <div class="panel" style="text-align:center;padding:20px 16px 16px">
              <div style="width:56px;height:56px;margin:0 auto 12px;border-radius:50%;
                   display:grid;place-items:center;font-size:1.5rem;font-weight:800;
                   color:{difficulty.color};border:2px solid {difficulty.color};
                   background:{difficulty.color}22">
                {DIFFICULTY_ORDER.index(key) + 1}
              </div>
              <div style="font-size:1.35rem;font-weight:800">{difficulty.label}</div>
              <div class="muted" style="font-size:.86rem;margin-top:10px;line-height:1.9">
                ▦ {difficulty.total_cards} بطاقة · {difficulty.grid_label}<br>
                ⏱️ {format_duration(difficulty.seconds)} دقيقة<br>
                ✖ مضاعف النقاط <span class="ltr">{difficulty.multiplier:g}</span>
              </div>
              <hr style="border:none;border-top:1px solid {STROKE};margin:14px 0 10px">
              {footer}
            </div>
            """)
            html('<div style="height:10px"></div>')
            if st.button(f"ابدأ {difficulty.label}", key=f"start_{key}",
                         use_container_width=True, type="primary"):
                start_game(key)
                st.rerun()

    html(f'<p class="faint center" style="margin-top:26px">'
         f'اقلب بطاقتين في كل محاولة — المطابقات المتتالية تضاعف نقاطك</p>')


# ------------------------------------------------------------------ اللعب

def render_hud(game: MemoryGame) -> None:
    left = seconds_left(game)
    ratio = left / game.difficulty.seconds if game.difficulty.seconds else 0
    time_color = DANGER if left <= 10 else (WARNING if left <= 30 else ACCENT)
    bar_color = ACCENT if ratio > 0.33 else (WARNING if ratio > 0.15 else DANGER)

    chips = [
        ("الوقت", format_duration(left), time_color),
        ("النقاط", f"{game.score:,}", GOLD),
        ("الأزواج", f"{game.matches}/{game.total_pairs}", SUCCESS),
        ("الحركات", str(game.moves), TEXT),
        ("السلسلة", f"×{game.combo}", PRIMARY if game.combo >= 2 else FAINT),
    ]
    html('<div class="hud">' + "".join(
        f'<div class="chip"><span class="k">{k}</span>'
        f'<span class="v" style="color:{c}">{v}</span></div>'
        for k, v, c in chips) + "</div>"
        f'<div class="bar"><i style="width:{ratio * 100:.1f}%;background:{bar_color}"></i></div>')


def render_board(game: MemoryGame) -> None:
    html(card_css(game))
    for row in range(game.difficulty.rows):
        columns = st.columns(game.difficulty.cols, gap="small")
        for col in range(game.difficulty.cols):
            index = row * game.difficulty.cols + col
            face_up = game.is_face_up(index)
            with columns[col]:
                st.button(
                    game.symbol_at(index)[0] if face_up else "❖",
                    key=f"card_{index}",
                    use_container_width=True,
                    disabled=face_up or game.finished,
                    on_click=on_card_click,
                    args=(index,),
                )
    st.session_state.just_flipped = set()


def render_result(game: MemoryGame) -> None:
    result = st.session_state.result
    stars = game.stars()
    star_html = "".join(
        f'<span style="color:{GOLD if i < stars else FAINT};font-size:2rem">'
        f'{"★" if i < stars else "☆"}</span>' for i in range(3))

    rows = [("المطابقات", f"+{result.matches_points:,}", TEXT)]
    if result.combo_points:
        rows.append(("مكافأة السلاسل", f"+{result.combo_points:,}", PRIMARY))
    if result.penalty_points:
        rows.append(("خصم المحاولات الخاطئة", f"−{result.penalty_points:,}", DANGER))
    if result.time_bonus:
        rows.append(("مكافأة الوقت المتبقّي", f"+{result.time_bonus:,}", ACCENT))
    if result.perfect_bonus:
        rows.append(("جولة مثالية بلا أخطاء", f"+{result.perfect_bonus:,}", GOLD))
    rows.append((f"مضاعف {game.difficulty.label}", f"×{result.multiplier:g}", MUTED))

    lines = "".join(
        f'<div style="display:flex;justify-content:space-between;margin:7px 0">'
        f'<span class="muted">{k}</span>'
        f'<span class="ltr" style="color:{c};font-weight:700">{v}</span></div>'
        for k, v, c in rows)

    icon, title, color = (("🎉", "أحسنت!", SUCCESS) if game.won
                          else ("⏰", "انتهى الوقت", DANGER))
    guest_note = ("" if can_save_scores() else
                  f'<p style="color:{WARNING};font-size:.85rem">'
                  f'أنشئ حساباً لحفظ نتائجك في لوحة الصدارة</p>')

    html(f"""
    <div class="panel" style="max-width:520px;margin:0 auto 18px;text-align:center">
      <div style="font-size:2.6rem">{icon}</div>
      <h2 style="color:{color};margin:.2rem 0">{title}</h2>
      <div>{star_html}</div>
      <div style="text-align:right;margin:18px 0 10px">{lines}</div>
      <hr style="border:none;border-top:1px solid {STROKE}">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span style="font-weight:800;font-size:1.1rem">المجموع</span>
        <span class="ltr" style="color:{GOLD};font-weight:800;font-size:1.8rem">
          {result.total:,}</span>
      </div>
      <p class="faint" style="font-size:.85rem;margin-top:6px">
        {game.matches} من {game.total_pairs} أزواج · {game.moves} حركة ·
        دقة {game.accuracy:.0%}</p>
      {guest_note}
    </div>
    """)

    spacer, actions, spacer2 = st.columns([1, 1.4, 1])
    with actions:
        again, menu, board = st.columns(3)
        with again:
            if st.button("مرة أخرى", type="primary", use_container_width=True):
                start_game(game.difficulty.key)
                st.rerun()
        with menu:
            if st.button("القائمة", use_container_width=True):
                go("menu")
                st.rerun()
        with board:
            if st.button("الصدارة", use_container_width=True):
                go("leaderboard")
                st.rerun()


def page_game() -> None:
    game = st.session_state.game
    if game is None:
        go("menu")
        st.rerun()

    # --- شريط التحكّم
    controls, title = st.columns([1, 1.6])
    with controls:
        back, pause, restart = st.columns(3)
        with back:
            if st.button("↩ رجوع", use_container_width=True):
                go("menu")
                st.rerun()
        with pause:
            label = "▶ متابعة" if st.session_state.paused else "⏸ إيقاف"
            if st.button(label, use_container_width=True,
                         disabled=game.finished or st.session_state.started_at is None):
                toggle_pause()
                st.rerun()
        with restart:
            if st.button("↻ إعادة", use_container_width=True):
                start_game(game.difficulty.key)
                st.rerun()
    with title:
        html(f'<div style="text-align:left;font-weight:800;font-size:1.1rem;'
             f'color:{game.difficulty.color};padding-top:6px">'
             f'المستوى: {game.difficulty.label}</div>')

    # --- ساحة اللعب: المؤقّت واللوحة معاً داخل جزء واحد يتجدّد ذاتياً.
    #     وضعُهما معاً ضروري: التحديث التلقائي يستهلك طلب إعادة التشغيل،
    #     فلو بقيت اللوحة خارجه لتغيّرت حالتها دون أن يتجدّد رسمها.
    def arena() -> None:
        game = st.session_state.game

        # انتهاء الوقت
        if (not game.finished and st.session_state.started_at is not None
                and not st.session_state.paused and seconds_left(game) <= 0):
            game.time_out()
            finish_game(won=False)

        render_hud(game)

        if game.finished:
            # نوقف التحديث الدوري أولاً: لو رسمنا الصوت قبل إعادة التشغيل
            # لأزالته إعادةُ الرسم التالية قبل أن يكتمل تشغيله
            if not st.session_state.get("finish_synced"):
                st.session_state.finish_synced = True
                st.rerun()
            render_result(game)
            html('<div style="opacity:.4;pointer-events:none">')
            render_board(game)
            html("</div>")
            render_sound_cue()
            return

        if st.session_state.paused:
            html("""
            <div class="panel center" style="max-width:420px;margin:40px auto">
              <div style="font-size:2.4rem">⏸</div>
              <h3>اللعبة متوقفة</h3>
              <p class="muted">خذ نفَسك — المؤقّت متوقف</p>
            </div>
            """)
            return

        # إخفاء الزوج غير المتطابق بعد انقضاء مهلة العرض
        if (st.session_state.hide_at is not None
                and time.time() >= st.session_state.hide_at):
            game.hide_unmatched()
            st.session_state.hide_at = None

        render_board(game)
        render_sound_cue()

    running = (st.session_state.started_at is not None and not game.finished
               and not st.session_state.paused)
    if running:
        st.fragment(run_every=0.5)(arena)()
    else:
        arena()


# ------------------------------------------------------------------ الصدارة

def page_leaderboard() -> None:
    top_bar()
    html('<div style="height:16px"></div>')

    db = get_db()
    count = db.player_count()
    html(f'<h2 style="margin-bottom:0">لوحة الصدارة</h2>'
         f'<p class="muted" style="margin-top:4px">'
         f'{f"{count} لاعباً سجّلوا نتائج حتى الآن" if count else "لا نتائج بعد — كن أول المتصدّرين"}'
         f'</p>')

    options = ["كل المستويات"] + [DIFFICULTIES[k].label for k in DIFFICULTY_ORDER]
    st.segmented_control("تصفية", options, key="lb_filter",
                         label_visibility="collapsed",
                         default="كل المستويات")

    label_to_key = {DIFFICULTIES[k].label: k for k in DIFFICULTY_ORDER}
    filter_key = label_to_key.get(st.session_state.get("lb_filter"))

    entries = db.leaderboard(filter_key)
    if not entries:
        html(f'<div class="panel center" style="margin-top:30px">'
             f'<div style="font-size:2.4rem">🏆</div>'
             f'<h3>لا توجد نتائج في هذا المستوى بعد</h3>'
             f'<p class="muted">العب جولة واحدة ليظهر اسمك هنا</p></div>')
        if st.button("ابدأ اللعب", type="primary"):
            go("menu")
            st.rerun()
        return

    my_id = st.session_state.user["id"] if can_save_scores() else None
    html(f'<div style="display:flex;gap:14px;padding:0 18px 6px;color:{FAINT};'
         f'font-size:.78rem">'
         f'<span style="width:34px">#</span><span style="width:52px"></span>'
         f'<span class="grow" style="flex:1">اللاعب</span>'
         f'<span style="width:90px;text-align:center">الجولات</span>'
         f'<span style="width:110px;text-align:center">أفضل نتيجة</span>'
         f'<span style="width:120px;text-align:center">مجموع النقاط</span></div>')

    for entry in entries:
        medal, accent = MEDALS.get(entry["rank"], ("", STROKE))
        is_me = entry["user_id"] == my_id
        border = PRIMARY if is_me else (accent if medal else STROKE)
        background = (f"{PRIMARY}22" if is_me else (f"{accent}14" if medal else SURFACE))
        rank_cell = medal if medal else str(entry["rank"])
        name = entry["username"] + ("  (أنت)" if is_me else "")
        html(f"""
        <div class="rowcard" style="border-color:{border};background:{background}">
          <span style="width:34px;font-size:1.2rem;text-align:center;color:{accent}">{rank_cell}</span>
          <span class="avatar" style="border-color:{accent}">{entry['avatar'] or '👤'}</span>
          <span class="grow">
            <span style="font-weight:800;color:{PRIMARY if is_me else TEXT}">{name}</span><br>
            <span class="faint" style="font-size:.78rem">
              {entry['wins'] or 0} فوز · أسرع وقت {format_duration(entry['best_time'])}</span>
          </span>
          <span style="width:90px;text-align:center" class="muted">{entry['games']}</span>
          <span style="width:110px;text-align:center" class="num">{entry['best_score']:,}</span>
          <span style="width:120px;text-align:center;color:{GOLD}" class="num">
            {entry['total_score']:,}</span>
        </div>
        """)


# ------------------------------------------------------------------ الملف

def page_profile() -> None:
    top_bar()
    html('<div style="height:16px"></div>')

    db = get_db()
    user = st.session_state.user
    stats = db.user_stats(user["id"])
    rank = db.user_rank(user["id"])

    html(f"""
    <div class="panel" style="display:flex;align-items:center;gap:18px;margin-bottom:22px">
      <div class="avatar" style="width:74px;height:74px;font-size:38px;
           border-color:{PRIMARY}">{user['avatar']}</div>
      <div>
        <div style="font-size:1.6rem;font-weight:800">{user['username']}</div>
        <div style="color:{GOLD if rank and rank <= 3 else MUTED}">
          {f'المركز {rank} في لوحة الصدارة' if rank else 'لم تدخل الترتيب بعد'}</div>
        <div class="faint" style="font-size:.82rem">
          عضو منذ {arabic_date(user['created_at'])}</div>
      </div>
    </div>
    """)

    st.markdown("##### غيّر صورتك")
    chosen = st.pills("الصورة الرمزية", AVATARS, default=user["avatar"],
                      label_visibility="collapsed", key="profile_avatar")
    if chosen and chosen != user["avatar"]:
        db.set_avatar(user["id"], chosen)
        st.session_state.user = db.get_user(user["id"])
        st.rerun()

    st.markdown("##### إحصائياتك")
    boxes = [
        ("مجموع النقاط", f"{stats['total_score']:,}", "🏆", GOLD),
        ("أفضل نتيجة", f"{stats['best_score']:,}", "⭐", WARNING),
        ("الجولات", str(stats["games"]), "🎮", TEXT),
        ("الانتصارات", str(stats["wins"]), "🥇", SUCCESS),
        ("نسبة الفوز", f"{stats['win_rate']:.0%}", "📈", ACCENT),
        ("الدقة", f"{stats['accuracy']:.0%}", "🎯", PRIMARY),
        ("أسرع وقت", format_duration(stats["best_time"]), "⏱️", ACCENT),
        ("أطول سلسلة", f"×{stats['best_combo']}", "🔥", DANGER),
    ]
    for start in (0, 4):
        for column, (title, value, icon, color) in zip(
                st.columns(4, gap="small"), boxes[start:start + 4]):
            with column:
                html(f'<div class="statbox"><div class="k">{icon} {title}</div>'
                     f'<div class="v" style="color:{color}">{value}</div></div>')

    st.markdown("##### أداؤك في كل مستوى")
    by_difficulty = db.best_score_by_difficulty(user["id"])
    for column, key in zip(st.columns(4, gap="small"), DIFFICULTY_ORDER):
        difficulty = DIFFICULTIES[key]
        record = by_difficulty.get(key)
        with column:
            if record:
                body = (f'<div class="v">{record["best_score"]:,}</div>'
                        f'<div class="faint" style="font-size:.78rem">'
                        f'{record["games"]} جولة · {format_duration(record["best_time"])}</div>')
            else:
                body = '<div class="faint" style="padding:6px 0">لم تلعبه بعد</div>'
            html(f'<div class="statbox" style="border-color:'
                 f'{difficulty.color if record else STROKE};background:{difficulty.color}11">'
                 f'<div style="color:{difficulty.color};font-weight:800">'
                 f'{difficulty.label}</div>{body}</div>')

    st.markdown("##### آخر الجولات")
    games = db.recent_games(user["id"])
    if not games:
        html('<p class="faint">لا توجد جولات بعد — ابدأ أول جولة لك</p>')
        return
    for game in games:
        difficulty = DIFFICULTIES.get(game["difficulty"])
        won = bool(game["won"])
        html(f"""
        <div class="rowcard" style="padding:10px 18px">
          <span style="font-size:1.1rem">{'🏆' if won else '⏰'}</span>
          <span style="width:70px;font-weight:700;color:{difficulty.color if difficulty else TEXT}">
            {difficulty.label if difficulty else game['difficulty']}</span>
          <span class="grow muted" style="font-size:.85rem">
            {game['matches']} أزواج · {game['moves']} حركة ·
            {format_duration(game['duration'])}</span>
          <span class="faint" style="font-size:.8rem">{arabic_date(game['played_at'])}</span>
          <span class="num" style="color:{GOLD};width:90px;text-align:left">
            {game['score']:,}</span>
        </div>
        """)


# ==============================================================================
# التشغيل
# ==============================================================================

PAGES = {
    "auth": page_auth,
    "menu": page_menu,
    "game": page_game,
    "leaderboard": page_leaderboard,
    "profile": page_profile,
}


def main() -> None:
    st.set_page_config(page_title=APP_NAME, page_icon="🃏", layout="wide")
    init_state()
    inject_css()

    page = st.session_state.page
    if page != "auth" and st.session_state.user is None and not st.session_state.is_guest:
        page = st.session_state.page = "auth"
    if page == "profile" and not can_save_scores():
        page = st.session_state.page = "menu"

    PAGES[page]()
    render_sound_cue()


# streamlit run ينفّذ الملف باسم __main__، والحارس يتيح استيراده للاختبار
if __name__ == "__main__":
    main()
