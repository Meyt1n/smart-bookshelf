"""
config.py
集中管理配置常量、关键词表、唤醒词表等。
"""

import os


def _env_flag(name, default="0"):
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _env_int(name, default):
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip(), 0)
    except Exception:
        return default


def _env_float(name, default):
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except Exception:
        return default

BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "data", "bookshelf.db")
VOICE_MODE = os.getenv("VOICE_MODE", "auto").strip().lower()
VOICE_MODEL_DISPATCH = _env_flag("VOICE_MODEL_DISPATCH", "0")
BOOK_COVER_YOLO_MODEL = os.getenv(
    "BOOK_COVER_YOLO_MODEL",
    os.path.join(BASE_DIR, "YOLO_model", "book_cover.pt"),
).strip()
BOOK_SPINE_YOLO_MODEL = os.getenv(
    "BOOK_SPINE_YOLO_MODEL",
    os.path.join(BASE_DIR, "YOLO_model", "book_spine.pt"),
).strip()
YOLO_OCR_CONF = _env_float("YOLO_OCR_CONF", 0.25)
YOLO_OCR_IOU = _env_float("YOLO_OCR_IOU", 0.7)
YOLO_OCR_MAX_DET = _env_int("YOLO_OCR_MAX_DET", 20)
YOLO_OCR_PADDING_RATIO = _env_float("YOLO_OCR_PADDING_RATIO", 0.04)
JWT_SECRET = os.getenv("JWT_SECRET", "bookshelf-dev-secret-change-me-2026")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256").strip()
JWT_EXPIRES_DAYS = int(os.getenv("JWT_EXPIRES_DAYS", "30"))
PAIR_CODE_EXPIRES_MINUTES = int(os.getenv("PAIR_CODE_EXPIRES_MINUTES", "5"))
PAIR_TOKEN_EXPIRES_MINUTES = int(os.getenv("PAIR_TOKEN_EXPIRES_MINUTES", "10"))
AUTH_COOKIE_NAME = os.getenv("AUTH_COOKIE_NAME", "bookshelf_auth_token").strip()
AUTH_COOKIE_SECURE = _env_flag("AUTH_COOKIE_SECURE", "0")
CABINET_NAME_DEFAULT = os.getenv("CABINET_NAME_DEFAULT", "智慧书架").strip() or "智慧书架"
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
PI_BRIDGE_BASE_URL = os.getenv("PI_BRIDGE_BASE_URL", "http://127.0.0.1:8765").strip().rstrip("/")

MOTION_MODE = os.getenv("MOTION_MODE", "direct").strip().lower() or "direct"
STM32_PROTOCOL_BACKEND = os.getenv("STM32_PROTOCOL_BACKEND", "memory").strip().lower() or "memory"
STM32_I2C_SLAVE_ADDR = _env_int("STM32_I2C_SLAVE_ADDR", 0x30)
STM32_ACK_TIMEOUT_MS = _env_int("STM32_ACK_TIMEOUT_MS", 3000)
STM32_POLL_INTERVAL_MS = _env_int("STM32_POLL_INTERVAL_MS", 50)
STM32_CELL_ID_BASE = _env_int("STM32_CELL_ID_BASE", 0)
STM32_ACK_IMPLIES_COMPLETION = _env_flag("STM32_ACK_IMPLIES_COMPLETION", "0")

_WAKE_DEBUG_LOG = _env_flag("WAKE_DEBUG_LOG", "0")
_WAKE_LOCK_PATH = os.path.join(os.getcwd(), ".wake.lock")


# ── 数据库字段白名单 ──────────────────────────────────────

USER_PROFILE_FIELDS = (
    "gender",
    "birth_date",
    "age",
    "grade_level",
    "reading_level",
    "interests",
    "family_id",
    "updated_at",
)
BOOK_FIELDS = (
    "title",
    "author",
    "category",
    "keywords",
    "description",
    "isbn",
    "publisher",
    "publish_year",
    "age_min",
    "age_max",
    "difficulty_level",
    "tags",
    "cover_url",
    "updated_at",
)
ACCOUNT_FIELDS = (
    "username",
    "phone",
    "password_hash",
    "status",
    "last_login_at",
    "created_at",
    "updated_at",
)
FAMILY_FIELDS = (
    "family_name",
    "owner_account_id",
    "created_at",
)


# ── 语音关键词 & 唤醒词 ──────────────────────────────────

STORE_KEYWORDS = [
    "存书",
    "放回",
    "归还",
    "还书",
    "上架",
    "放入书柜",
    "存一下",
    "放回去",
]
TAKE_KEYWORDS = [
    "取书",
    "拿书",
    "借书",
    "找书",
    "取出",
    "拿出",
    "帮我拿",
    "帮我取",
]

STORE_SAMPLES = [
    "帮我存书",
    "我要存书",
    "请帮我存书",
    "把书放回去",
    "帮我归还这本书",
]
TAKE_SAMPLES = [
    "帮我取书",
    "我要取书",
    "请帮我拿书",
    "帮我取乡土中国",
    "我要拿图灵传",
]

WAKE_WORDS = [
    "\u5c0f\u71d5\u5c0f\u71d5",
    "\u5c0f\u71d5",
    "\u6653\u71d5",
    "\u6653\u71d5\u6653\u71d5",
    "\u5c0f\u96c1",
    "\u5c0f\u8273",
    "\u5c0f\u71d5\u513f",
    "\u5c0f\u71d5\u554a",
]
