import os
import sys
import uuid
import re
import html
import json
import csv
import sqlite3
import secrets
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from flask import Flask, request, jsonify, send_from_directory, session

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

# Thêm thư mục gốc vào path để import module nội bộ theo kiểu package sibling.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ops.medical_glossary_vi import apply_medical_glossary

try:
    import pyodbc
except Exception:
    pyodbc = None

try:
    import dns.resolver as _dns_resolver
    _HAS_DNSPYTHON = True
except ImportError:
    _dns_resolver = None
    _HAS_DNSPYTHON = False

try:
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token
    _HAS_GOOGLE_AUTH = True
except ImportError:
    google_requests = None
    google_id_token = None
    _HAS_GOOGLE_AUTH = False

from ocr.ocr_engine import extract_text

app = Flask(__name__)
app.secret_key = "super_secret_key_for_prescription_ocr"
app.config["JSON_AS_ASCII"] = False


def load_env_file():
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if not os.path.isfile(env_path):
        return

    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    os.environ[key] = value
    except Exception:
        # Keep app running even if .env parse fails.
        pass


load_env_file()

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "database", "app.db")
DB_BACKEND = os.environ.get("DB_BACKEND", "sqlite").strip().lower()
AVATAR_UPLOAD_SUBDIR = "avatars"
MAX_AVATAR_FILE_SIZE = 10 * 1024 * 1024


def using_sqlserver():
    return DB_BACKEND in {"sqlserver", "mssql"}


def get_sqlserver_connection_string():
    raw_conn = os.environ.get("SQLSERVER_CONNECTION_STRING", "").strip()
    if raw_conn:
        return raw_conn

    driver = os.environ.get("SQLSERVER_DRIVER", "ODBC Driver 17 for SQL Server").strip()
    server = os.environ.get("SQLSERVER_SERVER", "localhost").strip()
    database = os.environ.get("SQLSERVER_DATABASE", "PrescriptionOCR").strip()
    trusted = os.environ.get("SQLSERVER_TRUSTED_CONNECTION", "yes").strip().lower()
    uid = os.environ.get("SQLSERVER_UID", "").strip()
    pwd = os.environ.get("SQLSERVER_PWD", "").strip()

    parts = [
        f"DRIVER={{{driver}}}",
        f"SERVER={server}",
        f"DATABASE={database}",
    ]

    if trusted in {"yes", "true", "1"}:
        parts.append("Trusted_Connection=yes")
    else:
        if uid:
            parts.append(f"UID={uid}")
        if pwd:
            parts.append(f"PWD={pwd}")

    parts.append("TrustServerCertificate=yes")
    return ";".join(parts)


def parse_db_datetime(value):
    if isinstance(value, datetime):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def is_google_avatar_url(url: str) -> bool:
    value = (url or "").strip().lower()
    return value.startswith("https://lh3.googleusercontent.com/")

def init_db():
    if using_sqlserver():
        # SQL Server schema is managed via database/create_db_sqlserver.sql in SSMS.
        # Keep a light backward-compatible migration for avatar_url.
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_NAME = 'users' AND COLUMN_NAME = 'avatar_url'
                """
            )
            row = cursor.fetchone()
            has_avatar_column = bool(row and int(row[0]) > 0)
            if not has_avatar_column:
                cursor.execute("ALTER TABLE users ADD avatar_url NVARCHAR(1000) NULL")
                conn.commit()
            # Migration: add google_avatar_url column
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_NAME = 'users' AND COLUMN_NAME = 'google_avatar_url'
                """
            )
            row2 = cursor.fetchone()
            if not (row2 and int(row2[0]) > 0):
                cursor.execute("ALTER TABLE users ADD google_avatar_url NVARCHAR(1000) NULL")
                conn.commit()
            conn.close()
        except Exception:
            pass
        return

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fullname TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
    ''')

    # Backward-compatible profile columns for existing databases.
    cursor.execute("PRAGMA table_info(users)")
    existing_columns = {row[1] for row in cursor.fetchall()}
    optional_columns = {
        "phone": "TEXT",
        "birth_date": "TEXT",
        "address": "TEXT",
        "bio": "TEXT",
        "role": "TEXT DEFAULT 'user'",
        "avatar_url": "TEXT",
        "google_avatar_url": "TEXT"
    }
    for column_name, column_type in optional_columns.items():
        if column_name not in existing_columns:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {column_name} {column_type}")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scan_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            full_text TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scan_error_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            filename TEXT,
            error_message TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            expires_at TEXT NOT NULL,
            used INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS password_reset_otps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            otp_code TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS registration_otps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fullname TEXT NOT NULL,
            email TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            otp_code TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    ''')

    conn.commit()
    conn.close()

def get_db_connection():
    if using_sqlserver():
        if pyodbc is None:
            raise RuntimeError("Chưa cài pyodbc cho chế độ SQL Server.")
        conn_str = get_sqlserver_connection_string()
        return pyodbc.connect(conn_str)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

init_db()


def get_inserted_id(cursor):
    if using_sqlserver():
        cursor.execute("SELECT CAST(SCOPE_IDENTITY() AS INT)")
        row = cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else None
    return cursor.lastrowid


def get_db_backend_label():
    return "sqlserver" if using_sqlserver() else "sqlite"


def is_dev_otp_fallback_enabled():
    value = os.environ.get("ALLOW_DEV_OTP_FALLBACK", "true").strip().lower()
    return value in {"1", "true", "yes", "on"}


def get_google_client_id():
    return (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()


DISPOSABLE_EMAIL_DOMAINS = {
    # Guerrilla Mail family
    "guerrillamail.com", "guerrillamail.net", "guerrillamail.org", "guerrillamail.biz",
    "guerrillamail.de", "guerrillamail.info", "grr.la", "spam4.me",
    "sharklasers.com", "guerrillamailblock.com", "hammerspacejunk.com",
    # YOPmail family
    "yopmail.com", "yopmail.fr", "cool.fr.nf", "jetable.fr.nf", "nospam.ze.tc",
    "nomail.xl.cx", "mega.zik.dj", "speed.1s.fr", "courriel.fr.nf",
    # Mailinator family
    "mailinator.com", "tradermail.info", "mailin8r.com", "mailinator2.com",
    "notmailinator.com", "mailinater.com", "suremail.info",
    "spamherelots.com", "binkmail.com", "streetwisemail.com",
    # 10 minute mail family
    "10minutemail.com", "10minutemail.net", "10minutemail.org", "10minutemail.co.uk",
    "10minutemail.de", "10minutemail.be", "10minutemail.cn", "10minutemail.us",
    "10minutemail.info", "10minutemail.ru",
    # TempMail and variants
    "tempmail.com", "tempmail.net", "tempmail.org", "temp-mail.org", "temp-mail.io",
    "temp-mail.ru", "tempr.email", "tempail.com", "tempemail.com", "tempinbox.com",
    "tempinbox.net", "spamgourmet.com", "tmpmail.net", "tmpmail.org",
    "mail-temp.com", "mail.tm", "tempmail.plus", "tempmail.gg",
    # Trashmail family
    "trashmail.com", "trashmail.me", "trashmail.net", "trashmail.at",
    "trashmail.io", "trashmail.org", "trashmail.xyz",
    # FakeInbox / Discard
    "fakeinbox.com", "fakeinbox.net", "getairmail.com", "dispostable.com",
    "discard.email", "discardmail.com", "discardmail.de",
    "disposableemailaddresses.com", "spambog.com",
    # Throwaway
    "throwam.com", "throwam.net", "throwaway.email", "throwam.us",
    # MailDrop / Maildrop
    "maildrop.cc",
    # Mailnull / Spamgourmet
    "mailnull.com", "spamgourmet.net", "spamgourmet.org",
    # German temp mails
    "sofort-mail.de", "spaml.de", "spamoff.de", "zehnminutenmail.de",
    "emailgo.de", "filzmail.com", "instant-mail.de", "safetypost.de",
    "trashdevil.com", "trashdevil.de",
    # Misc well-known disposables
    "gishpuppy.com", "incognitomail.com", "incognitomail.net", "incognitomail.org",
    "kasmail.com", "koszmail.pl", "letthemeatspam.com", "meltmail.com",
    "mohmal.com", "mt2009.com", "mt2014.com", "mytempemail.com",
    "nowmymail.com", "rmqkr.net", "spamavert.com",
    "spambob.com", "spambob.net", "spambob.org",
    "spamex.com", "spaml.com", "spammotel.com",
    "spamspot.com", "thrma.com", "tmailinator.com",
    "trbvm.com", "turual.com", "mailnesia.com", "mailexpire.com",
    "mintemail.com", "owlpic.com",
    "proxymail.eu", "shieldedmail.com", "sinnlos-mail.de",
    # More modern disposable services
    "guerrillamail.biz", "fakemailgenerator.com",
    "crazymailing.com", "dragonmail.live", "trollmail.store",
    "flyspam.com", "jetable.net", "jetable.org", "jetable.pp.ua",
    "netzidiot.de", "notsharingmy.info", "obobbo.com", "objectmail.com",
    "oneoffemail.com", "onewaymail.com", "pookmail.com", "powered.name",
    "reallymymail.com", "recyclemail.dk", "rtrtr.com",
    "s0ny.net", "slaskpost.se", "slopsbox.com",
    "sneakemail.com", "speed.1s.fr", "spoofmail.de",
    "squizzy.de", "startkeys.com", "stuffmail.de",
    "super-auswahl.de", "supergreatmail.com", "superrito.com",
    "teleworm.com", "teleworm.us", "tempalias.com",
    "tempemail.net", "tempemail.org",
    "trashmail.fr", "trashmail.global",
    "tunxis.net", "upliftnow.com",
    "wegwrfmail.de", "wegwrfmail.net", "wegwrfmail.org",
    "wh4f.org", "whatpaas.com", "whyspam.me",
    "willselfdestruct.com", "wudet.men",
    "yopmail.pp.ua", "yoru-dea.com",
    "zippymail.info",
}


def _check_domain_has_mx(domain: str) -> bool:
    """Kiểm tra tên miền có bản ghi MX (mail server) thật không.
    
    Trả về True nếu hợp lệ hoặc không xác định được (để tránh chặn nhầm).
    Trả về False chỉ khi chắc chắn không có MX record.
    """
    if not _HAS_DNSPYTHON:
        return True  # Bỏ qua nếu chưa cài dnspython
    try:
        answers = _dns_resolver.resolve(domain, "MX", lifetime=5)
        return len(answers) > 0
    except (_dns_resolver.NXDOMAIN, _dns_resolver.NoAnswer):
        # Tên miền không tồn tại hoặc không có MX record
        return False
    except Exception:
        # Timeout, network error — không chặn để tránh nhầm
        return True


def validate_real_email(email):
    normalized = (email or "").strip().lower()
    if not normalized:
        return False, "Vui lòng nhập email."

    if not re.match(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", normalized):
        return False, "Email không đúng định dạng hợp lệ."

    domain = normalized.split("@", 1)[1]

    if domain in DISPOSABLE_EMAIL_DOMAINS:
        return False, "Email tạm thời/ảo không được chấp nhận. Vui lòng dùng email thật."

    if domain.startswith("localhost") or domain.endswith(".local"):
        return False, "Email không hợp lệ."

    # Kiểm tra tên miền có mail server thật không
    if not _check_domain_has_mx(domain):
        return False, f"Tên miền '{domain}' không có máy chủ email. Vui lòng dùng email thật."

    return True, ""


UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "..", "uploads")
AVATAR_UPLOAD_FOLDER = os.path.join(UPLOAD_FOLDER, AVATAR_UPLOAD_SUBDIR)
DATA_FOLDER = os.path.join(os.path.dirname(__file__), "..", "data")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "bmp", "tiff"}
ALLOWED_AVATAR_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff"}
ALLOWED_DATA_EXTENSIONS = {".txt", ".md", ".json", ".csv"}

# Prefer curated Vietnamese dataset when available to reduce noisy English chunks.
PREFERRED_DATA_REPLACEMENTS = {
    "drugbank_clean.csv": "drugbank_vi.csv",
}

CSV_PRIORITY_FIELDS = [
    "name",
    "drugbank-id",
    "tom-tat-vi",
    "chi-dinh-vi",
    "co-che-vi",
    "duoc-luc-hoc-vi",
    "doc-tinh-vi",
    "tuong-tac-thuc-an-vi",
    "tuong-tac-thuoc-vi",
    "chi-dinh",
    "co-che-tac-dung",
    "duoc-luc-hoc",
    "doc-tinh",
    "tuong-tac-thuc-an",
    "tuong-tac-thuoc",
    "keywords-vi",
    "tags",
]

SEARCH_STOPWORDS = {
    "la", "gi", "co", "khong", "cho", "toi", "ban", "minh", "nay", "kia", "duoc", "de", "va", "the", "nao",
    "what", "is", "are", "the", "a", "an", "to", "of", "and", "for", "in", "on", "with", "from", "about",
}

GREETING_TOKENS = {"hello", "hi", "hey", "xin", "chao", "alo"}

_DATASET_CACHE = {
    "fingerprint": None,
    "docs": [],
    "summary": {
        "fileCount": 0,
        "docCount": 0,
        "loadedAt": None,
    },
}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(AVATAR_UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DATA_FOLDER, exist_ok=True)


def allowed_avatar_file(filename: str) -> bool:
    if "." not in (filename or ""):
        return False
    extension = filename.rsplit(".", 1)[1].lower()
    return extension in ALLOWED_AVATAR_EXTENSIONS


def avatar_public_url(filename: str) -> str:
    safe_name = secure_filename(filename or "")
    if not safe_name:
        return ""
    return f"/uploads/{AVATAR_UPLOAD_SUBDIR}/{safe_name}"


def resolve_local_avatar_path(avatar_url: str) -> str:
    value = (avatar_url or "").strip()
    expected_prefix = f"/uploads/{AVATAR_UPLOAD_SUBDIR}/"
    if not value.startswith(expected_prefix):
        return ""
    filename = secure_filename(value[len(expected_prefix):])
    if not filename:
        return ""
    return os.path.join(AVATAR_UPLOAD_FOLDER, filename)


def _tokenize_for_search(text: str):
    normalized = (text or "").lower()
    return re.findall(r"[0-9a-zA-Zà-ỹ]+", normalized)


def _meaningful_query_tokens(text: str):
    raw_tokens = _tokenize_for_search(text)
    tokens = [
        token for token in raw_tokens
        if len(token) >= 2 and token not in SEARCH_STOPWORDS
    ]
    return tokens


def _is_greeting_question(question: str):
    tokens = _tokenize_for_search(question)
    if not tokens or len(tokens) > 3:
        return False
    return all(token in GREETING_TOKENS for token in tokens)


def _dataset_fingerprint():
    file_signatures = []
    if not os.path.isdir(DATA_FOLDER):
        return tuple(file_signatures)

    for root, _, files in os.walk(DATA_FOLDER):
        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in ALLOWED_DATA_EXTENSIONS:
                continue
            full_path = os.path.join(root, filename)
            try:
                stat = os.stat(full_path)
                relative_path = os.path.relpath(full_path, DATA_FOLDER).replace("\\", "/")
                file_signatures.append((relative_path, int(stat.st_mtime), int(stat.st_size)))
            except OSError:
                continue

    file_signatures.sort()
    return tuple(file_signatures)


def _normalize_doc_text(value):
    text = str(value or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _should_skip_data_file(relative_path: str):
    normalized = (relative_path or "").replace("\\", "/").lower()
    basename = os.path.basename(normalized)
    replacement = PREFERRED_DATA_REPLACEMENTS.get(basename)
    if not replacement:
        return False
    replacement_path = os.path.join(DATA_FOLDER, os.path.dirname(normalized), replacement)
    return os.path.isfile(replacement_path)


def _csv_row_to_chunks(row: dict, index: int):
    normalized_map = {}
    for key, value in (row or {}).items():
        norm_key = _normalize_doc_text(key).lower()
        norm_value = _normalize_doc_text(value)
        if norm_key and norm_value:
            normalized_map[norm_key] = norm_value

    parts = []
    for key in CSV_PRIORITY_FIELDS:
        value = normalized_map.get(key)
        if value:
            parts.append(f"{key}: {value}")

    # Fallback for generic CSV files that do not follow known schema.
    if not parts:
        for key, value in normalized_map.items():
            parts.append(f"{key}: {value}")

    if not parts:
        return []

    line = f"row {index} | " + " | ".join(parts)
    return _chunk_long_text(line)


def _chunk_long_text(text: str, chunk_size: int = 700):
    source = _normalize_doc_text(text)
    if not source:
        return []
    if len(source) <= chunk_size:
        return [source]

    chunks = []
    current = []
    current_len = 0
    for sentence in re.split(r"(?<=[.!?])\s+", source):
        segment = sentence.strip()
        if not segment:
            continue
        segment_len = len(segment)
        if current and current_len + 1 + segment_len > chunk_size:
            chunks.append(" ".join(current).strip())
            current = [segment]
            current_len = segment_len
        else:
            current.append(segment)
            current_len += segment_len + (1 if current_len else 0)
    if current:
        chunks.append(" ".join(current).strip())
    return chunks


def _json_to_text_chunks(payload, prefix=""):
    chunks = []

    if isinstance(payload, dict):
        scalar_parts = []
        for key, value in payload.items():
            if isinstance(value, (dict, list)):
                next_prefix = f"{prefix}.{key}" if prefix else str(key)
                chunks.extend(_json_to_text_chunks(value, next_prefix))
            else:
                normalized_value = _normalize_doc_text(value)
                if normalized_value:
                    scalar_parts.append(f"{key}: {normalized_value}")
        if scalar_parts:
            header = f"{prefix} | " if prefix else ""
            chunks.append(header + " | ".join(scalar_parts))
        return chunks

    if isinstance(payload, list):
        for index, item in enumerate(payload):
            next_prefix = f"{prefix}[{index}]" if prefix else f"item[{index}]"
            chunks.extend(_json_to_text_chunks(item, next_prefix))
        return chunks

    text = _normalize_doc_text(payload)
    if text:
        if prefix:
            chunks.append(f"{prefix}: {text}")
        else:
            chunks.append(text)
    return chunks


def _load_dataset_docs():
    docs = []
    file_count = 0

    if not os.path.isdir(DATA_FOLDER):
        return docs, file_count

    for root, _, files in os.walk(DATA_FOLDER):
        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in ALLOWED_DATA_EXTENSIONS:
                continue

            full_path = os.path.join(root, filename)
            relative_path = os.path.relpath(full_path, DATA_FOLDER).replace("\\", "/")

            if _should_skip_data_file(relative_path):
                continue

            try:
                if ext in {".txt", ".md"}:
                    with open(full_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    raw_chunks = re.split(r"\n\s*\n", content)
                    text_chunks = []
                    for block in raw_chunks:
                        text_chunks.extend(_chunk_long_text(block))

                elif ext == ".json":
                    with open(full_path, "r", encoding="utf-8") as f:
                        payload = json.load(f)
                    raw_chunks = _json_to_text_chunks(payload)
                    text_chunks = []
                    for block in raw_chunks:
                        text_chunks.extend(_chunk_long_text(block))

                elif ext == ".csv":
                    with open(full_path, "r", encoding="utf-8-sig", newline="") as f:
                        reader = csv.DictReader(f)
                        text_chunks = []
                        for index, row in enumerate(reader, start=1):
                            text_chunks.extend(_csv_row_to_chunks(row, index))
                else:
                    text_chunks = []

                if text_chunks:
                    file_count += 1
                    for chunk in text_chunks:
                        tokens = _tokenize_for_search(chunk)
                        if not tokens:
                            continue
                        docs.append({
                            "source": relative_path,
                            "text": chunk,
                            "tokens": Counter(tokens),
                        })
            except Exception:
                continue

    return docs, file_count


def _ensure_dataset_index():
    current_fingerprint = _dataset_fingerprint()
    if _DATASET_CACHE["fingerprint"] == current_fingerprint:
        return _DATASET_CACHE

    docs, file_count = _load_dataset_docs()
    _DATASET_CACHE["fingerprint"] = current_fingerprint
    _DATASET_CACHE["docs"] = docs
    _DATASET_CACHE["summary"] = {
        "fileCount": file_count,
        "docCount": len(docs),
        "loadedAt": datetime.now().isoformat(timespec="seconds"),
    }
    return _DATASET_CACHE


def _search_dataset_documents(question: str, top_k: int = 3):
    cache = _ensure_dataset_index()
    docs = cache.get("docs", [])
    question_tokens = _meaningful_query_tokens(question)
    if not question_tokens:
        question_tokens = [token for token in _tokenize_for_search(question) if len(token) >= 2]
    if not question_tokens:
        return []

    question_counter = Counter(question_tokens)
    max_q = sum(question_counter.values()) or 1
    question_text = (question or "").strip().lower()

    scored = []
    for doc in docs:
        doc_counter = doc["tokens"]
        overlap = sum(min(question_counter[t], doc_counter.get(t, 0)) for t in question_counter)
        if overlap == 0:
            continue

        score = overlap / max_q
        doc_text_lower = doc["text"].lower()
        if question_text and question_text in doc_text_lower:
            score += 1.25

        # Strongly prioritize rows whose `name:` field matches queried drug token.
        for token in question_tokens:
            if len(token) < 3:
                continue
            name_pattern = rf"\bname\s*:\s*{re.escape(token)}\b"
            if re.search(name_pattern, doc_text_lower):
                score += 2.0

        if len(question_tokens) >= 2:
            two_token_phrase = " ".join(question_tokens[:2])
            if two_token_phrase in doc_text_lower:
                score += 0.2

        unique_match = len([t for t in question_counter if doc_counter.get(t, 0) > 0])
        coverage = unique_match / max(1, len(question_counter))
        long_tokens = [t for t in question_counter if len(t) >= 5]
        long_token_match = len([t for t in long_tokens if doc_counter.get(t, 0) > 0])
        if coverage < 0.34 and score < 0.55 and long_token_match == 0:
            continue

        scored.append({
            "score": score,
            "source": doc["source"],
            "text": doc["text"],
            "coverage": coverage,
        })

    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:top_k]


def _shorten_text(value: str, max_len: int = 170):
    text = _normalize_doc_text(value)
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def _clean_answer_whitespace(text: str):
    compact = re.sub(r"[ \t\u00a0]+", " ", (text or ""))
    compact = re.sub(r"\s*\n\s*", "\n", compact)
    compact = re.sub(r"\n{2,}", "\n", compact)
    return compact.strip()


def _light_translate_en_to_vi(text: str):
    result = apply_medical_glossary(text)
    result = re.sub(r"\bindication\b", "chỉ định", result, flags=re.IGNORECASE)
    result = re.sub(r"\bdescription\b", "mô tả", result, flags=re.IGNORECASE)
    return _clean_answer_whitespace(result)


def _semantic_vi_summary(text: str):
    source = _clean_answer_whitespace(text)
    lower = source.lower()

    intent_clauses = []
    if "diagnostic aid" in lower or "radiologic" in lower:
        intent_clauses.append("hỗ trợ chẩn đoán hình ảnh")
    if "gastrointestinal tract" in lower or "movement of the gastrointestinal" in lower:
        intent_clauses.append("làm giảm nhu động đường tiêu hóa tạm thời")
    if "hypoglycemia" in lower:
        intent_clauses.append("điều trị hạ đường huyết")
    if "diabetes" in lower:
        intent_clauses.append("liên quan điều trị đái tháo đường")
    if "insulin" in lower and "side" in lower and "effect" in lower:
        intent_clauses.append("cần theo dõi tác dụng phụ khi dùng insulin")

    if intent_clauses:
        unique_clauses = []
        for clause in intent_clauses:
            if clause not in unique_clauses:
                unique_clauses.append(clause)
        return "Thuốc này " + " và ".join(unique_clauses[:2]) + "."

    use_case = ""
    treat_case = ""

    use_match = re.search(r"used\s+(?:as|to)\s+([^\.\,;]+)", lower)
    if use_match:
        use_case = use_match.group(1).strip()

    treat_match = re.search(r"to\s+treat\s+([^\.\,;]+)", lower)
    if treat_match:
        treat_case = treat_match.group(1).strip()

    if not use_case and not treat_case:
        return "Dữ liệu cho thấy thuốc này có thông tin sử dụng lâm sàng; bạn có thể hỏi cụ thể hơn về công dụng hoặc tác dụng phụ."

    segments = []
    if use_case:
        segments.append(f"dùng để {use_case}")
    if treat_case:
        segments.append(f"điều trị {treat_case}")

    summary = "Thuốc này " + " và ".join(segments) + "."
    summary = _light_translate_en_to_vi(summary)
    summary = _clean_answer_whitespace(summary)

    # Nếu vẫn còn quá nhiều tiếng Anh, trả về bản tóm tắt tiếng Việt an toàn.
    english_tokens = re.findall(r"[a-zA-Z]{4,}", summary)
    unresolved_en_patterns = [
        r"\bfor whom\b",
        r"\bsuch as\b",
        r"\bwithout\b",
        r"\bin patients\b",
    ]
    has_unresolved_pattern = any(re.search(pattern, summary, flags=re.IGNORECASE) for pattern in unresolved_en_patterns)
    if len(english_tokens) >= 3 or has_unresolved_pattern:
        return "Dữ liệu cho thấy thuốc này có công dụng điều trị; bạn có thể hỏi cụ thể hơn để nhận câu trả lời chính xác hơn."
    return summary


def _extract_compact_snippet(text: str, query_tokens):
    source = _normalize_doc_text(text)
    if not source:
        return ""

    lower_tokens = set((query_tokens or []))
    parts = [part.strip() for part in source.split("|") if part.strip()]
    key_value_pairs = []
    for part in parts:
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if value:
            key_value_pairs.append((key, value))

    if key_value_pairs:
        preferred_keys = ["name", "description", "indication", "mechanism-of-action", "pharmacodynamics", "toxicity"]
        selected = []

        for key, value in key_value_pairs:
            value_lower = value.lower()
            if any(token in value_lower for token in lower_tokens):
                selected.append((key, value))

        if not selected:
            for pref in preferred_keys:
                for key, value in key_value_pairs:
                    if key == pref:
                        selected.append((key, value))
                        if len(selected) >= 2:
                            break
                if len(selected) >= 2:
                    break

        if not selected:
            selected = key_value_pairs[:2]

        compact_lines = []
        for key, value in selected[:2]:
            if key in {"name", "drugbank-id", "row"}:
                continue
            compact_lines.append(_shorten_text(value, 170))
        if not compact_lines:
            compact_lines = [_shorten_text(selected[0][1], 170)]
        return "\n".join(compact_lines)

    sentences = re.split(r"(?<=[.!?])\s+", source)
    matched_sentences = []
    for sentence in sentences:
        sentence_clean = sentence.strip()
        if not sentence_clean:
            continue
        sentence_lower = sentence_clean.lower()
        if any(token in sentence_lower for token in lower_tokens):
            matched_sentences.append(_shorten_text(sentence_clean, 190))
        if len(matched_sentences) >= 2:
            break

    if not matched_sentences:
        matched_sentences = [_shorten_text(sentences[0], 190)]

    return " ".join(matched_sentences[:2])


def _parse_compact_row_fields(text: str):
    fields = {}
    source = _normalize_doc_text(text)
    for part in source.split("|"):
        item = part.strip()
        if ":" not in item:
            continue
        key, value = item.split(":", 1)
        norm_key = _normalize_doc_text(key).lower()
        norm_value = _normalize_doc_text(value)
        if norm_key and norm_value:
            fields[norm_key] = norm_value
    return fields


def _vi_sentence_focus(text: str, max_sentences: int = 2):
    source = _clean_answer_whitespace(text)
    if not source:
        return ""

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", source) if s.strip()]
    if not sentences:
        return _shorten_text(source, 220)

    picked = []
    for sentence in sentences:
        tokens = re.findall(r"[A-Za-zÀ-ỹ]+", sentence)
        if not tokens:
            continue
        ascii_tokens = len([t for t in tokens if re.fullmatch(r"[A-Za-z]+", t)])
        ratio = ascii_tokens / max(1, len(tokens))
        if ratio <= 0.45:
            picked.append(sentence)
        if len(picked) >= max_sentences:
            break

    if not picked:
        picked = [sentences[0]]
    return _shorten_text(" ".join(picked), 240)


def _is_mixed_english_heavy(text: str):
    source = _clean_answer_whitespace(text)
    tokens = re.findall(r"[A-Za-zÀ-ỹ]+", source)
    if len(tokens) < 8:
        return False
    ascii_tokens = len([t for t in tokens if re.fullmatch(r"[A-Za-z]+", t)])
    return (ascii_tokens / max(1, len(tokens))) > 0.35


def _intent_fallback_vi(intent_label: str, drug_name: str):
    subject = drug_name or "thuốc này"
    label = (intent_label or "Thông tin").lower()

    if "công dụng" in label:
        return f"Công dụng: {subject} có chỉ định điều trị trong dữ liệu; bạn có thể hỏi thêm theo bệnh cụ thể để nhận thông tin chính xác hơn."
    if "cơ chế" in label:
        return f"Cơ chế: Dữ liệu ghi nhận {subject} có cơ chế tác dụng dược lý; bạn có thể hỏi thêm theo nhóm thuốc để mình trả lời chi tiết hơn."
    if "tương tác" in label:
        return f"Tương tác: Dữ liệu có thông tin tương tác của {subject}; bạn hãy cung cấp thêm thuốc hoặc thực phẩm đi kèm để tra chính xác."
    if "an toàn" in label:
        return f"An toàn: Dữ liệu có thông tin về độc tính và tác dụng không mong muốn của {subject}; cần tham khảo bác sĩ khi dùng thực tế."
    return f"Thông tin: Dữ liệu có thông tin lâm sàng về {subject}; bạn có thể hỏi cụ thể hơn để nhận câu trả lời chi tiết."


def _select_curated_field_values(question: str, fields: dict):
    q = (question or "").lower()

    intent_map = [
        (
            ["tương tác", "interaction", "interact"],
            ["tuong-tac-thuoc-vi", "tuong-tac-thuc-an-vi", "tom-tat-vi"],
            "Tương tác",
        ),
        (
            ["cơ chế", "mechanism"],
            ["co-che-vi", "duoc-luc-hoc-vi", "tom-tat-vi"],
            "Cơ chế",
        ),
        (
            ["tác dụng phụ", "độc tính", "adverse", "toxicity", "an toàn"],
            ["doc-tinh-vi", "tom-tat-vi"],
            "An toàn",
        ),
        (
            ["công dụng", "dùng", "chỉ định", "điều trị", "làm gì", "indication"],
            ["chi-dinh-vi", "tom-tat-vi"],
            "Công dụng",
        ),
    ]

    for triggers, keys, label in intent_map:
        if any(trigger in q for trigger in triggers):
            for key in keys:
                value = fields.get(key)
                if value:
                    return label, value

    for key in ["tom-tat-vi", "chi-dinh-vi", "co-che-vi", "duoc-luc-hoc-vi"]:
        value = fields.get(key)
        if value:
            return "Thông tin", value

    return "Thông tin", ""


def _build_dataset_answer(question: str):
    if _is_greeting_question(question):
        return {
            "answer": "Chào bạn. Hãy hỏi tên thuốc hoặc công dụng, ví dụ: 'Glucagon dùng để làm gì?'.",
            "references": [],
        }

    hits = _search_dataset_documents(question, top_k=3)
    if not hits:
        return {
            "answer": "Mình chưa tìm thấy thông tin phù hợp trong dataset ở thư mục data. Bạn thử diễn đạt lại câu hỏi hoặc bổ sung dữ liệu vào thư mục data.",
            "references": [],
        }

    query_tokens = _meaningful_query_tokens(question)
    best_hit = hits[0]
    concise = _extract_compact_snippet(best_hit["text"], query_tokens)
    if not concise:
        concise = _shorten_text(best_hit["text"], 220)

    concise = _clean_answer_whitespace(concise)
    concise = concise.replace("Description:", "").replace("description:", "")
    concise = concise.replace("Indication:", "").replace("indication:", "")
    concise = concise.replace("Pharmacodynamics:", "").replace("pharmacodynamics:", "")
    concise = concise.strip(" -:\n")

    drug_name = ""
    name_match = re.search(r"\bname\s*:\s*([^|]+)", best_hit.get("text", ""), flags=re.IGNORECASE)
    if name_match:
        drug_name = _normalize_doc_text(name_match.group(1))

    best_hit_text = (best_hit.get("text") or "").lower()
    has_curated_vi_fields = any(
        field in best_hit_text
        for field in ["tom-tat-vi:", "chi-dinh-vi:", "co-che-vi:"]
    )

    if has_curated_vi_fields:
        fields = _parse_compact_row_fields(best_hit.get("text", ""))
        intent_label, intent_value = _select_curated_field_values(question, fields)
        if intent_value:
            concise = _vi_sentence_focus(_light_translate_en_to_vi(intent_value), max_sentences=2)
            is_too_short = len(_normalize_doc_text(concise)) < 24
            if _is_mixed_english_heavy(concise) or is_too_short:
                concise = _intent_fallback_vi(intent_label, drug_name)
            else:
                concise = f"{intent_label}: {concise}"
        else:
            concise = _vi_sentence_focus(_light_translate_en_to_vi(concise), max_sentences=2)
    else:
        concise = _semantic_vi_summary(concise)
        concise = _light_translate_en_to_vi(concise)

    if concise:
        if drug_name:
            answer_text = f"Thông tin chính về {drug_name}: {concise}"
        else:
            answer_text = f"Thông tin chính: {concise}"
    else:
        answer_text = "Mình chưa tìm được nội dung phù hợp."
    answer_text = _clean_answer_whitespace(answer_text)
    if len(answer_text) > 260:
        answer_text = _shorten_text(answer_text, 260)

    return {
        "answer": answer_text,
        "references": [],
    }

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def has_scannable_text(text):
    normalized = re.sub(r"\s+", " ", (text or "")).strip()
    if not normalized:
        return False

    alnum_count = len(re.findall(r"[A-Za-zÀ-ỹ0-9]", normalized))
    token_count = len([t for t in normalized.split(" ") if t])
    return alnum_count >= 12 and token_count >= 3


def process_uploaded_file(file_storage):
    if file_storage.filename == "":
        return None, "Chưa chọn file", 400

    if not allowed_file(file_storage.filename):
        return None, "Định dạng file không hỗ trợ", 400

    random_id = uuid.uuid4().hex[:8]
    original_name = secure_filename(file_storage.filename)
    safe_filename = f"{random_id}_{original_name}"
    filepath = os.path.join(UPLOAD_FOLDER, safe_filename)

    try:
        file_storage.save(filepath)
        text = extract_text(filepath)

        if str(text).startswith("Lỗi:") or str(text).startswith("Không thể"):
            return None, text, 500

        if not has_scannable_text(str(text)):
            return None, "Hình ảnh của bạn không thể quét. Vui lòng chọn ảnh đơn thuốc rõ chữ.", 422

        return {"text": text, "filename": original_name}, None, 200
    finally:
        if os.path.exists(filepath):
            os.remove(filepath)


def record_scan_error(user_id, filename, error_message):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO scan_error_logs (user_id, filename, error_message) VALUES (?, ?, ?)",
            (user_id, filename or "", (error_message or "Lỗi nhận dạng không xác định")[:500])
        )
        conn.commit()
        conn.close()
    except Exception:
        # Do not break main request flow when logging failures.
        pass


def is_admin():
    return session.get("role") == "admin"


def get_smtp_settings():
    smtp_host = (os.environ.get("SMTP_HOST") or os.environ.get("SMTP_SERVER") or "smtp.gmail.com").strip()
    smtp_port = int(os.environ.get("SMTP_PORT", "587").strip())
    smtp_username = (os.environ.get("SMTP_USERNAME") or os.environ.get("SMTP_USER") or "").strip()
    smtp_password = (os.environ.get("SMTP_PASSWORD") or os.environ.get("SMTP_PASS") or "").strip()
    smtp_from = os.environ.get("SMTP_FROM", smtp_username).strip()
    smtp_from_name = os.environ.get("SMTP_FROM_NAME", APP_DISPLAY_NAME).strip() or APP_DISPLAY_NAME

    # Gmail App Password is sometimes copied with spaces every 4 chars.
    if "gmail.com" in smtp_host.lower() and smtp_password:
        smtp_password = smtp_password.replace(" ", "")

    missing_keys = []
    if not smtp_username:
        missing_keys.append("SMTP_USERNAME")
    if not smtp_password:
        missing_keys.append("SMTP_PASSWORD")
    if not smtp_from:
        missing_keys.append("SMTP_FROM")

    invalid_keys = []
    if smtp_username.lower().startswith("your_") or smtp_username.lower() == "your_email@gmail.com":
        invalid_keys.append("SMTP_USERNAME")
    if smtp_from.lower().startswith("your_") or smtp_from.lower() == "your_email@gmail.com":
        invalid_keys.append("SMTP_FROM")
    if smtp_password.lower().startswith("your_") or smtp_password.lower() == "your_app_password":
        invalid_keys.append("SMTP_PASSWORD")

    return {
        "host": smtp_host,
        "port": smtp_port,
        "username": smtp_username,
        "password": smtp_password,
        "from": smtp_from,
        "from_name": smtp_from_name,
        "missing_keys": missing_keys,
        "invalid_keys": invalid_keys,
    }


APP_DISPLAY_NAME = "Trợ Lý Y Tế AI"


def _build_otp_html(otp_code: str, title: str, subtitle: str, note: str = "") -> str:
    """Tạo nội dung email HTML đẹp theo chủ đề dark/blue của ứng dụng."""
    note_block = f'<p style="margin:0 0 0 0;font-size:13px;color:#94a3b8;">{note}</p>' if note else ""
    digits = "".join(
        f'<span style="display:inline-block;width:42px;height:52px;line-height:52px;text-align:center;'
        f'background:rgba(59,130,246,0.15);border:1.5px solid rgba(59,130,246,0.4);'
        f'border-radius:10px;font-size:26px;font-weight:700;color:#93c5fd;margin:0 4px;'
        f'letter-spacing:0;">{d}</span>'
        for d in str(otp_code)
    )
    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title}</title>
</head>
<body style="margin:0;padding:0;background:#0f172a;font-family:'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0f172a;padding:40px 16px;">
    <tr><td align="center">
      <table width="520" cellpadding="0" cellspacing="0" style="max-width:520px;width:100%;background:rgba(30,41,59,0.95);border:1px solid rgba(255,255,255,0.07);border-radius:20px;overflow:hidden;">

        <!-- Header -->
        <tr>
          <td style="background:linear-gradient(135deg,#1e3a5f 0%,#1e293b 100%);padding:32px 40px 24px;text-align:center;border-bottom:1px solid rgba(59,130,246,0.2);">
            <div style="display:inline-block;width:56px;height:56px;background:rgba(59,130,246,0.2);border:1.5px solid rgba(59,130,246,0.4);border-radius:16px;line-height:56px;text-align:center;font-size:28px;margin-bottom:16px;">⚕</div>
            <h1 style="margin:0 0 6px;font-size:22px;font-weight:700;color:#f1f5f9;letter-spacing:-0.3px;">{APP_DISPLAY_NAME}</h1>
            <p style="margin:0;font-size:13px;color:#64748b;">Hệ thống hỗ trợ nhận dạng đơn thuốc AI</p>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="padding:36px 40px 32px;">
            <h2 style="margin:0 0 8px;font-size:20px;font-weight:700;color:#e2e8f0;">{title}</h2>
            <p style="margin:0 0 28px;font-size:14px;color:#94a3b8;line-height:1.6;">{subtitle}</p>

            <!-- OTP Box -->
            <div style="background:rgba(15,23,42,0.7);border:1px solid rgba(59,130,246,0.25);border-radius:14px;padding:24px 20px;text-align:center;margin-bottom:24px;">
              <p style="margin:0 0 14px;font-size:12px;text-transform:uppercase;letter-spacing:1.5px;color:#64748b;font-weight:600;">Mã OTP của bạn</p>
              <div style="white-space:nowrap;">{digits}</div>
              <p style="margin:16px 0 0;font-size:12px;color:#64748b;">⏱ Mã có hiệu lực trong <strong style="color:#fbbf24;">5 phút</strong></p>
            </div>

            {note_block}

            <div style="border-top:1px solid rgba(255,255,255,0.06);margin-top:28px;padding-top:20px;">
              <p style="margin:0;font-size:12px;color:#475569;line-height:1.7;">
                🔒 Vui lòng không chia sẻ mã OTP này với bất kỳ ai.<br>
                Nếu bạn không thực hiện yêu cầu này, hãy bỏ qua email này.
              </p>
            </div>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:rgba(15,23,42,0.5);padding:16px 40px;text-align:center;border-top:1px solid rgba(255,255,255,0.04);">
            <p style="margin:0;font-size:11px;color:#334155;">© 2026 {APP_DISPLAY_NAME} · Email tự động, vui lòng không phản hồi</p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def send_email_message(to_email, subject, content, html_content=None, from_name=None):
    smtp = get_smtp_settings()
    if smtp["missing_keys"]:
        missing = ", ".join(smtp["missing_keys"])
        return False, f"Email server chưa được cấu hình. Thiếu: {missing}."
    if smtp["invalid_keys"]:
        invalid = ", ".join(smtp["invalid_keys"])
        return False, f"Cấu hình SMTP trong file .env vẫn đang là giá trị mẫu. Hãy thay giá trị thật cho: {invalid}."

    msg = EmailMessage()
    msg["Subject"] = subject
    display_name = (from_name or smtp.get("from_name") or APP_DISPLAY_NAME).strip() or APP_DISPLAY_NAME
    msg["From"] = formataddr((display_name, smtp["from"]))
    msg["To"] = to_email
    msg.set_content(content)  # plain-text fallback
    if html_content:
        msg.add_alternative(html_content, subtype="html")

    try:
        with smtplib.SMTP(smtp["host"], smtp["port"], timeout=20) as server:
            server.starttls()
            server.login(smtp["username"], smtp["password"])
            server.send_message(msg)
        return True, ""
    except smtplib.SMTPAuthenticationError:
        gmail_hint = ""
        if "gmail" in (smtp.get("host") or "").lower():
            pwd = (smtp.get("password") or "").strip()
            if pwd.isdigit() and len(pwd) == 16:
                gmail_hint = " Mật khẩu hiện tại là 16 chữ số, nhưng App Password của Gmail không phải dãy số tự đặt."
        return (
            False,
            "Xác thực SMTP thất bại (535). Với Gmail, hãy dùng App Password 16 ký tự do Google tạo (không dùng mật khẩu thường), bật 2-Step Verification và kiểm tra lại SMTP_USERNAME/SMTP_PASSWORD."
            + gmail_hint,
        )
    except smtplib.SMTPConnectError as e:
        return False, f"Không thể kết nối SMTP: {str(e)}"
    except smtplib.SMTPServerDisconnected as e:
        return False, f"SMTP bị ngắt kết nối: {str(e)}"
    except Exception as e:
        return False, f"Không gửi được email: {str(e)}"


def send_reset_otp_email(to_email, otp_code):
    return send_otp_email(
        email=to_email,
        otp=otp_code,
        title="Đặt lại mật khẩu",
        subtitle="Chúng tôi nhận được yêu cầu đặt lại mật khẩu tài khoản của bạn. Nhập mã OTP bên dưới để tiếp tục.",
        note="Nếu bạn không yêu cầu đặt lại mật khẩu, hãy bỏ qua email này. Tài khoản của bạn vẫn an toàn.",
    )


def generate_otp():
    """Tạo mã OTP 6 chữ số."""
    return f"{secrets.randbelow(10**6):06d}"


def send_otp_email(email, otp, title="Xác thực OTP", subtitle="", note=""):
    """Gửi OTP qua email bằng SMTP đã cấu hình trong .env."""
    otp_code = (otp or "").strip()
    if not otp_code:
        return False, "OTP không hợp lệ."

    subject = f"[{APP_DISPLAY_NAME}] {title}"
    plain_content = (
        f"Mã OTP của bạn là: {otp_code}\n"
        "Mã có hiệu lực trong 5 phút. Vui lòng không chia sẻ mã này.\n"
    )

    return send_email_message(
        to_email=email,
        subject=subject,
        content=plain_content,
        html_content=_build_otp_html(
            otp_code=otp_code,
            title=title,
            subtitle=subtitle or "Vui lòng nhập mã OTP để tiếp tục.",
            note=note,
        ),
    )


def _split_medical_lines(full_text: str):
    raw = (full_text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    splitter = r"\s+(?=(Họ tên|NS|Ns|Địa chỉ|Điện thoại|Sinh hiệu|Thân nhiệt|Huyết áp|Cân nặng|Chẩn đoán|Chuẩn đoán|Điều trị|Bệnh kèm theo)\s*:)"
    for src_line in raw.split("\n"):
        line = re.sub(r"\s+", " ", src_line).strip()
        if not line:
            continue
        with_breaks = re.sub(splitter, "\n", line, flags=re.IGNORECASE)
        for part in with_breaks.split("\n"):
            cleaned = part.strip()
            if cleaned:
                lines.append(cleaned)
    return lines


def _extract_prescription_sections(full_text: str):
    sections = {
        "admin": [],
        "vitals": [],
        "diagnosis": [],
        "treatment": [],
        "medicines": [],
        "instructions": [],
        "other": [],
    }

    for raw in _split_medical_lines(full_text):
        line = re.sub(r"^~+\s*", "", raw).strip()
        if not line:
            continue
        if re.match(r"^(ĐƠN THUỐC|DON THUOC)$", line, flags=re.IGNORECASE):
            continue

        if re.match(r"^\d+\s*[\/\.\-)]+", line):
            sections["medicines"].append(line)
            continue
        if re.match(r"^(Họ tên|NS|Ns|Địa chỉ|Điện thoại)\s*:", line, flags=re.IGNORECASE):
            sections["admin"].append(line)
            continue
        if re.match(r"^(Sinh hiệu|Thân nhiệt|Huyết áp|Cân nặng)\s*:", line, flags=re.IGNORECASE):
            sections["vitals"].append(line)
            continue
        if re.match(r"^(Chẩn đoán|Chuẩn đoán)\s*:", line, flags=re.IGNORECASE):
            sections["diagnosis"].append(line)
            continue
        if re.match(r"^Điều trị\s*:", line, flags=re.IGNORECASE):
            sections["treatment"].append(line)
            continue
        if re.match(r"^(Sáng|Sang|Trưa|Trua|Chiều|Chieu|Tối|Toi|Uống|Uong|Thoa|Bôi|Boi|Ngày|Ngay)\b", line, flags=re.IGNORECASE):
            sections["instructions"].append(line)
            continue
        sections["other"].append(line)

    return sections


def _render_section_lines(lines):
    if not lines:
        return '<div style="font-size:13px;color:#94a3b8;">Không có dữ liệu.</div>'
    return "".join(
        f'<div style="padding:6px 10px;border-radius:8px;background:rgba(15,23,42,0.45);border:1px solid rgba(148,163,184,0.15);margin-bottom:6px;color:#e2e8f0;font-size:13px;line-height:1.55;">{html.escape(item)}</div>'
        for item in lines
    )


def _normalize_medicine_name(raw_line: str) -> str:
    line = (raw_line or "").strip()
    line = re.sub(r"^\d+\s*[\/\.\-)]+\s*", "", line).strip()
    return line or "Không rõ tên thuốc"


def _parse_instruction_schedule(line: str):
    source = re.sub(r"\s+", " ", (line or "").strip())
    if not source:
        return {"morning": "", "noon": "", "afternoon": "", "evening": "", "note": ""}

    schedule = {"morning": "", "noon": "", "afternoon": "", "evening": "", "note": ""}
    parts = []
    consumed_spans = []

    pattern = re.compile(
        r"(Sáng|Sang|Trưa|Trua|Tra|Chiều|Chieu|Tối|Toi)\s*[:\-]?\s*([^;\n]+)",
        flags=re.IGNORECASE,
    )

    label_key_map = {
        "sáng": "morning",
        "sang": "morning",
        "trưa": "noon",
        "trua": "noon",
        "tra": "noon",
        "chiều": "afternoon",
        "chieu": "afternoon",
        "tối": "evening",
        "toi": "evening",
    }

    for match in pattern.finditer(source):
        label = (match.group(1) or "").strip().lower()
        value = (match.group(2) or "").strip(" ;,.")
        key = label_key_map.get(label)
        if not key:
            continue
        if value:
            schedule[key] = value
            consumed_spans.append((match.start(), match.end()))

    if consumed_spans:
        remainder = []
        cursor = 0
        for start, end in consumed_spans:
            if start > cursor:
                remainder.append(source[cursor:start])
            cursor = end
        if cursor < len(source):
            remainder.append(source[cursor:])
        note_text = " ".join(remainder)
        note_text = re.sub(r"\s*[,;]+\s*", " ", note_text).strip(" ,;.")
        schedule["note"] = note_text
    else:
        schedule["note"] = source

    return schedule


def _render_instruction_plan(medicines, instruction_lines, extra_lines):
    medicine_lines = medicines or []
    instruction_lines = instruction_lines or []
    extra_lines = extra_lines or []

    if not medicine_lines and not instruction_lines and not extra_lines:
        return '<div style="font-size:13px;color:#94a3b8;">Không có dữ liệu hướng dẫn dùng thuốc.</div>'

    time_labels = [
        ("morning", "Sáng"),
        ("noon", "Trưa"),
        ("afternoon", "Chiều"),
        ("evening", "Tối"),
    ]

    total_rows = max(len(medicine_lines), len(instruction_lines))
    rows_html = ""

    for index in range(total_rows):
        medicine_name = _normalize_medicine_name(medicine_lines[index]) if index < len(medicine_lines) else f"Thuốc {index + 1}"
        instruction = instruction_lines[index] if index < len(instruction_lines) else ""
        parsed = _parse_instruction_schedule(instruction)

        schedule_badges = ""
        for key, label in time_labels:
            value = (parsed.get(key) or "").strip()
            if not value:
                value = "-"
            schedule_badges += (
                f'<span style="display:inline-block;padding:4px 8px;border-radius:999px;border:1px solid rgba(103,232,249,0.22);'
                f'background:rgba(14,116,144,0.16);color:#bae6fd;font-size:12px;margin:0 6px 6px 0;">'
                f'{label}: <strong style="color:#e0f2fe;">{html.escape(value)}</strong></span>'
            )

        note_html = ""
        if parsed.get("note"):
            note_html = (
                '<div style="margin-top:6px;font-size:12px;line-height:1.5;color:#cbd5e1;">'
                f'<strong style="color:#a7f3d0;">Ghi chú:</strong> {html.escape(parsed["note"])}'
                '</div>'
            )

        rows_html += (
            '<div style="padding:10px 10px 8px;border-radius:10px;background:rgba(15,23,42,0.45);'
            'border:1px solid rgba(148,163,184,0.15);margin-bottom:8px;">'
            f'<div style="color:#67e8f9;font-size:12px;font-weight:700;margin-bottom:6px;">Thuốc {index + 1}: '
            f'<span style="color:#e2e8f0;font-weight:600;">{html.escape(medicine_name)}</span></div>'
            f'<div>{schedule_badges}</div>'
            f'{note_html}'
            '</div>'
        )

    if len(instruction_lines) < len(extra_lines):
        remaining_notes = extra_lines[len(instruction_lines):]
    else:
        remaining_notes = extra_lines

    remaining_notes = [item.strip() for item in remaining_notes if (item or "").strip()]
    if remaining_notes:
        rows_html += (
            '<div style="margin-top:10px;padding:10px;border-radius:10px;background:rgba(15,23,42,0.45);'
            'border:1px dashed rgba(148,163,184,0.25);">'
            '<div style="font-size:12px;color:#67e8f9;font-weight:700;margin-bottom:6px;">Ghi chú chung</div>'
            + "".join(
                f'<div style="font-size:13px;color:#e2e8f0;line-height:1.5;margin-bottom:4px;">- {html.escape(item)}</div>'
                for item in remaining_notes
            )
            + '</div>'
        )

    return rows_html


def _build_history_result_email_html(filename: str, created_at: str, fullname: str, full_text: str) -> str:
    safe_filename = html.escape(filename or "Không xác định")
    safe_created_at = html.escape(created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    safe_fullname = html.escape(fullname or "Người dùng")
    sections = _extract_prescription_sections(full_text)

    medicines_rows = ""
    if sections["medicines"]:
        for index, med in enumerate(sections["medicines"], start=1):
            medicines_rows += (
                f'<tr>'
                f'<td style="padding:8px 10px;border:1px solid rgba(148,163,184,0.25);color:#93c5fd;text-align:center;font-size:12px;">{index}</td>'
                f'<td style="padding:8px 10px;border:1px solid rgba(148,163,184,0.25);color:#e2e8f0;font-size:13px;line-height:1.55;">{html.escape(med)}</td>'
                f'</tr>'
            )
    else:
        medicines_rows = '<tr><td colspan="2" style="padding:10px;border:1px solid rgba(148,163,184,0.25);color:#94a3b8;font-size:13px;">Không có dữ liệu thuốc tách được từ OCR.</td></tr>'

    instructions_html = _render_instruction_plan(
        sections["medicines"],
        sections["instructions"],
        sections["other"],
    )

    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Bản in đơn thuốc</title>
</head>
<body style="margin:0;padding:0;background:#0f172a;font-family:'Segoe UI',Arial,sans-serif;">
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#0f172a;padding:28px 14px;">
        <tr><td align="center">
            <table width="700" cellpadding="0" cellspacing="0" style="max-width:700px;width:100%;background:rgba(30,41,59,0.96);border:1px solid rgba(255,255,255,0.07);border-radius:16px;overflow:hidden;">
                <tr>
                    <td style="padding:20px 24px;background:linear-gradient(135deg,#1e3a5f 0%,#1e293b 100%);border-bottom:1px solid rgba(59,130,246,0.22);">
                        <table width="100%" cellpadding="0" cellspacing="0"><tr>
                            <td style="width:58px;vertical-align:top;">
                                <table cellpadding="0" cellspacing="0" role="presentation" style="width:48px;height:48px;border-radius:24px;background:#1d4ed8;background:linear-gradient(135deg,#60a5fa 0%,#1d4ed8 100%);">
                                    <tr>
                                        <td align="center" valign="middle" style="color:#ffffff;font-weight:700;font-size:22px;font-family:'Segoe UI Emoji','Segoe UI Symbol','Apple Color Emoji',Arial,sans-serif;line-height:1;">
                                            💊
                                        </td>
                                    </tr>
                                </table>
                            </td>
                            <td>
                                <h2 style="margin:0;color:#f1f5f9;font-size:20px;">Bản in đơn thuốc từ {APP_DISPLAY_NAME}</h2>
                                <p style="margin:8px 0 0;color:#94a3b8;font-size:13px;">Tệp: <strong style="color:#e2e8f0;">{safe_filename}</strong> · Thời gian quét: <strong style="color:#e2e8f0;">{safe_created_at}</strong></p>
                            </td>
                        </tr></table>
                    </td>
                </tr>
                <tr>
                    <td style="padding:18px 24px;">
                        <p style="margin:0 0 12px;color:#cbd5e1;font-size:14px;">Xin chào <strong>{safe_fullname}</strong>, hệ thống gửi bạn nội dung đơn thuốc đã chọn để lưu trữ/in lại.</p>

                        <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:separate;border-spacing:0 10px;">
                            <tr>
                                <td style="background:rgba(15,23,42,0.72);border:1px solid rgba(148,163,184,0.3);border-radius:12px;padding:12px 14px;">
                                    <div style="font-size:12px;color:#67e8f9;font-weight:700;text-transform:uppercase;letter-spacing:.7px;margin-bottom:8px;">Thông tin hành chính</div>
                                    {_render_section_lines(sections["admin"])}
                                </td>
                            </tr>
                            <tr>
                                <td style="background:rgba(15,23,42,0.72);border:1px solid rgba(148,163,184,0.3);border-radius:12px;padding:12px 14px;">
                                    <div style="font-size:12px;color:#67e8f9;font-weight:700;text-transform:uppercase;letter-spacing:.7px;margin-bottom:8px;">Sinh hiệu</div>
                                    {_render_section_lines(sections["vitals"])}
                                </td>
                            </tr>
                            <tr>
                                <td style="background:rgba(15,23,42,0.72);border:1px solid rgba(148,163,184,0.3);border-radius:12px;padding:12px 14px;">
                                    <div style="font-size:12px;color:#67e8f9;font-weight:700;text-transform:uppercase;letter-spacing:.7px;margin-bottom:8px;">Chẩn đoán và điều trị</div>
                                    {_render_section_lines(sections["diagnosis"] + sections["treatment"])}
                                </td>
                            </tr>
                            <tr>
                                <td style="background:rgba(15,23,42,0.72);border:1px solid rgba(148,163,184,0.3);border-radius:12px;padding:12px 14px;">
                                    <div style="font-size:12px;color:#67e8f9;font-weight:700;text-transform:uppercase;letter-spacing:.7px;margin-bottom:8px;">Danh mục thuốc</div>
                                    <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
                                        <tr>
                                            <th style="padding:8px 10px;border:1px solid rgba(148,163,184,0.25);background:rgba(56,189,248,0.12);color:#bae6fd;font-size:12px;text-align:center;">STT</th>
                                            <th style="padding:8px 10px;border:1px solid rgba(148,163,184,0.25);background:rgba(56,189,248,0.12);color:#bae6fd;font-size:12px;text-align:left;">Nội dung thuốc</th>
                                        </tr>
                                        {medicines_rows}
                                    </table>
                                </td>
                            </tr>
                            <tr>
                                <td style="background:rgba(15,23,42,0.72);border:1px solid rgba(148,163,184,0.3);border-radius:12px;padding:12px 14px;">
                                    <div style="font-size:12px;color:#67e8f9;font-weight:700;text-transform:uppercase;letter-spacing:.7px;margin-bottom:8px;">Hướng dẫn dùng thuốc / Ghi chú</div>
                                    {instructions_html}
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
                <tr>
                    <td style="padding:12px 24px 16px;color:#64748b;font-size:12px;border-top:1px solid rgba(255,255,255,0.05);">Email tự động từ hệ thống OCR Thuốc. Vui lòng không phản hồi email này.</td>
                </tr>
            </table>
        </td></tr>
    </table>
</body>
</html>"""

@app.route("/")

def index():
    frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
    return send_from_directory(frontend_dir, "index.html")


# Route cho login.html
@app.route("/login")
def login_page():
    frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
    return send_from_directory(frontend_dir, "login.html")

# Route cho register.html
@app.route("/register")
def register_page():
    frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
    return send_from_directory(frontend_dir, "register.html")


@app.route("/forgot-password")
def forgot_password_page():
    frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
    return send_from_directory(frontend_dir, "forgot_password.html")


@app.route("/reset-password")
def reset_password_page():
    frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
    return send_from_directory(frontend_dir, "reset_password.html")


@app.route("/profile")
def profile_page():
    frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
    return send_from_directory(frontend_dir, "profile.html")


@app.route("/history")
def history_page():
    frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
    return send_from_directory(frontend_dir, "history.html")


@app.route("/stats")
def stats_page():
    frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
    return send_from_directory(frontend_dir, "stats.html")

# Route cho các file tĩnh trong frontend (css, js, img...)
@app.route('/frontend/<path:filename>')
def frontend_static(filename):
    frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
    return send_from_directory(frontend_dir, filename)


@app.route('/uploads/<path:filename>')
def uploads_static(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


@app.route("/upload", methods=["POST"])
def upload():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Vui lòng đăng nhập để sử dụng chức năng quét đơn thuốc."}), 401

    if "file" not in request.files:
        return jsonify({"error": "Không tìm thấy file"}), 400

    file = request.files["file"]
    try:
        payload, err_msg, status = process_uploaded_file(file)
        if not payload:
            record_scan_error(user_id, file.filename, err_msg)
            return jsonify({"error": err_msg}), status
        return jsonify(payload)
    except Exception as e:
        record_scan_error(user_id, file.filename, str(e))
        return jsonify({"error": f"Lỗi hệ thống không mong muốn: {str(e)}"}), 500


@app.route("/upload-batch", methods=["POST"])
def upload_batch():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Vui lòng đăng nhập để sử dụng chức năng quét đơn thuốc."}), 401

    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "Không tìm thấy danh sách file."}), 400

    results = []
    errors = []
    for file in files:
        try:
            payload, err_msg, status = process_uploaded_file(file)
            if payload:
                results.append(payload)
            else:
                record_scan_error(user_id, file.filename, err_msg)
                errors.append({"filename": file.filename, "error": err_msg, "status": status})
        except Exception as e:
            record_scan_error(user_id, file.filename, str(e))
            errors.append({"filename": file.filename, "error": str(e), "status": 500})

    return jsonify({"success": True, "results": results, "errors": errors})

@app.route("/api/register", methods=["POST"])
def api_register():
    return jsonify({"success": False, "error": "Đăng ký yêu cầu xác thực OTP email. Vui lòng dùng API /api/register/request-otp trước."}), 400


@app.route("/api/register/request-otp", methods=["POST"])
def api_register_request_otp():
    data = request.get_json() or {}
    fullname = (data.get("fullname") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()

    if not fullname or not email or not password:
        return jsonify({"success": False, "error": "Vui lòng nhập đầy đủ thông tin."}), 400
    if len(password) < 8:
        return jsonify({"success": False, "error": "Mật khẩu cần ít nhất 8 ký tự."}), 400

    valid_email, email_err = validate_real_email(email)
    if not valid_email:
        return jsonify({"success": False, "error": email_err}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE lower(email) = ?", (email,))
        if cursor.fetchone():
            conn.close()
            return jsonify({"success": False, "error": "Email đã tồn tại."}), 400

        otp_code = generate_otp()
        otp_expires_at = (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        sent_ok, sent_err = send_otp_email(
            email=email,
            otp=otp_code,
            title="Xác nhận đăng ký tài khoản",
            subtitle=f"Xin chào! Bạn đang đăng ký tài khoản tại <strong style='color:#93c5fd;'>{APP_DISPLAY_NAME}</strong>. Nhập mã OTP bên dưới để hoàn tất.",
        )
        use_fallback = (not sent_ok) and is_dev_otp_fallback_enabled()
        if not sent_ok and not use_fallback:
            conn.close()
            return jsonify({"success": False, "error": sent_err}), 500

        password_hash = generate_password_hash(password)
        cursor.execute("UPDATE registration_otps SET used = 1 WHERE lower(email) = ? AND used = 0", (email,))
        cursor.execute(
            """
            INSERT INTO registration_otps (fullname, email, password_hash, otp_code, expires_at, used)
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (fullname, email, password_hash, otp_code, otp_expires_at),
        )
        conn.commit()
        conn.close()

        response = {
            "success": True,
            "message": "Mã OTP đăng ký đã được gửi về email của bạn.",
            "otpDelivery": "email",
        }

        if use_fallback:
            response["otpDelivery"] = "dev-fallback"
            response["message"] = "SMTP đang lỗi. Hệ thống đã bật OTP fallback để bạn tiếp tục đăng ký."
            response["devOtp"] = otp_code
            response["warning"] = sent_err

        return jsonify(response)
    except Exception as e:
        return jsonify({"success": False, "error": f"Lỗi hệ thống: {str(e)}"}), 500


@app.route("/api/register/resend-otp", methods=["POST"])
def api_register_resend_otp():
    """Gửi lại OTP đăng ký. Giới hạn 60 giây giữa các lần gửi."""
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()

    if not email:
        return jsonify({"success": False, "error": "Vui lòng nhập email."}), 400

    valid_email, email_err = validate_real_email(email)
    if not valid_email:
        return jsonify({"success": False, "error": email_err}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM users WHERE lower(email) = ?", (email,))
        if cursor.fetchone():
            conn.close()
            return jsonify({"success": False, "error": "Email đã tồn tại."}), 400

        # Kiểm tra OTP gần nhất có được gửi chưa đầy 60 giây trước không
        if using_sqlserver():
            cursor.execute(
                "SELECT TOP 1 created_at FROM registration_otps WHERE lower(email) = ? AND used = 0 ORDER BY id DESC",
                (email,),
            )
        else:
            cursor.execute(
                "SELECT created_at FROM registration_otps WHERE lower(email) = ? AND used = 0 ORDER BY id DESC LIMIT 1",
                (email,),
            )
        last_row = cursor.fetchone()
        if last_row:
            last_sent = parse_db_datetime(last_row[0])
            if last_sent and (datetime.now() - last_sent).total_seconds() < 60:
                remaining = int(60 - (datetime.now() - last_sent).total_seconds())
                conn.close()
                return jsonify({
                    "success": False,
                    "error": f"Vui lòng đợi {remaining} giây trước khi gửi lại OTP.",
                    "wait_seconds": remaining,
                }), 429

        # Lấy thông tin đăng ký tạm từ OTP cũ nhất chưa dùng
        if using_sqlserver():
            cursor.execute(
                "SELECT TOP 1 fullname, password_hash FROM registration_otps WHERE lower(email) = ? AND used = 0 ORDER BY id DESC",
                (email,),
            )
        else:
            cursor.execute(
                "SELECT fullname, password_hash FROM registration_otps WHERE lower(email) = ? AND used = 0 ORDER BY id DESC LIMIT 1",
                (email,),
            )
        info_row = cursor.fetchone()
        if not info_row:
            conn.close()
            return jsonify({"success": False, "error": "Không tìm thấy thông tin đăng ký. Vui lòng điền lại form."}), 400

        fullname = info_row[0]
        password_hash = info_row[1]

        otp_code = generate_otp()
        otp_expires_at = (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")

        sent_ok, sent_err = send_otp_email(
            email=email,
            otp=otp_code,
            title="Mã OTP đăng ký (gửi lại)",
            subtitle=f"Theo yêu cầu, chúng tôi đã gửi lại mã OTP mới để hoàn tất đăng ký tại <strong style='color:#93c5fd;'>{APP_DISPLAY_NAME}</strong>.",
            note="Mã OTP cũ đã bị vô hiệu hóa. Chỉ dùng mã mới nhất trong email này.",
        )
        use_fallback = (not sent_ok) and is_dev_otp_fallback_enabled()
        if not sent_ok and not use_fallback:
            conn.close()
            return jsonify({"success": False, "error": sent_err}), 500

        # Hủy OTP cũ, tạo OTP mới
        cursor.execute("UPDATE registration_otps SET used = 1 WHERE lower(email) = ? AND used = 0", (email,))
        cursor.execute(
            "INSERT INTO registration_otps (fullname, email, password_hash, otp_code, expires_at, used) VALUES (?, ?, ?, ?, ?, 0)",
            (fullname, email, password_hash, otp_code, otp_expires_at),
        )
        conn.commit()
        conn.close()

        response = {
            "success": True,
            "message": "Mã OTP mới đã được gửi về email của bạn.",
            "otpDelivery": "email",
        }

        if use_fallback:
            response["otpDelivery"] = "dev-fallback"
            response["message"] = "SMTP đang lỗi. Hệ thống đã bật OTP fallback để bạn tiếp tục đăng ký."
            response["devOtp"] = otp_code
            response["warning"] = sent_err

        return jsonify(response)
    except Exception as e:
        return jsonify({"success": False, "error": f"Lỗi hệ thống: {str(e)}"}), 500


@app.route("/api/register/verify-otp", methods=["POST"])
def api_register_verify_otp():
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    otp = (data.get("otp") or "").strip()

    if not email or not otp:
        return jsonify({"success": False, "error": "Vui lòng nhập email và OTP."}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM users WHERE lower(email) = ?", (email,))
        if cursor.fetchone():
            conn.close()
            return jsonify({"success": False, "error": "Email đã tồn tại."}), 400

        if using_sqlserver():
            cursor.execute(
                """
                SELECT TOP 1 id, fullname, email, password_hash, expires_at, used
                FROM registration_otps
                WHERE lower(email) = ? AND otp_code = ?
                ORDER BY id DESC
                """,
                (email, otp),
            )
        else:
            cursor.execute(
                """
                SELECT id, fullname, email, password_hash, expires_at, used
                FROM registration_otps
                WHERE lower(email) = ? AND otp_code = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (email, otp),
            )

        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "error": "OTP đăng ký không đúng."}), 400

        if int(row[5]) == 1:
            conn.close()
            return jsonify({"success": False, "error": "OTP đã được sử dụng."}), 400

        expires_at = parse_db_datetime(row[4])
        if not expires_at:
            conn.close()
            return jsonify({"success": False, "error": "Dữ liệu hạn OTP không hợp lệ."}), 500
        if expires_at < datetime.now():
            cursor.execute("UPDATE registration_otps SET used = 1 WHERE id = ?", (row[0],))
            conn.commit()
            conn.close()
            return jsonify({"success": False, "error": "OTP đã hết hạn. Vui lòng gửi lại OTP."}), 400

        cursor.execute("SELECT COUNT(*) FROM users")
        user_count = cursor.fetchone()[0]
        role = "admin" if user_count == 0 else "user"

        cursor.execute(
            "INSERT INTO users (fullname, email, password_hash, role) VALUES (?, ?, ?, ?)",
            (row[1], row[2], row[3], role),
        )
        cursor.execute("UPDATE registration_otps SET used = 1 WHERE id = ?", (row[0],))
        conn.commit()
        conn.close()

        return jsonify({"success": True, "message": "Đăng ký thành công! Bạn có thể đăng nhập ngay."})
    except Exception as e:
        return jsonify({"success": False, "error": f"Lỗi hệ thống: {str(e)}"}), 500

@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json()
    email = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()
    if not email or not password:
        return jsonify({"success": False, "error": "Vui lòng nhập đầy đủ email và mật khẩu."}), 400
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, fullname, password_hash, role, avatar_url FROM users WHERE lower(email) = ?", (email,))
        user = cursor.fetchone()
        conn.close()
        if not user:
            return jsonify({"success": False, "error": "Email không tồn tại."}), 400
        if not check_password_hash(user[2], password):
            return jsonify({"success": False, "error": "Mật khẩu không đúng."}), 400
        session['user_id'] = user[0]
        session['fullname'] = user[1]
        session['email'] = email
        session['role'] = user[3] or "user"
        return jsonify({
            "success": True,
            "message": f"Đăng nhập thành công! Xin chào {user[1]}",
            "user": {
                "fullname": user[1],
                "email": email,
                "role": user[3] or "user",
                "avatar_url": user[4] or ""
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": f"Lỗi hệ thống: {str(e)}"}), 500


@app.route("/api/auth/google-config", methods=["GET"])
def api_google_auth_config():
    client_id = get_google_client_id()
    return jsonify({
        "success": True,
        "enabled": bool(client_id),
        "clientId": client_id,
        "googleAuthLibInstalled": _HAS_GOOGLE_AUTH,
    })


def _verify_google_identity_token(id_token_value: str):
    token_value = (id_token_value or "").strip()
    if not token_value:
        return None, "Thiếu Google ID token.", 400

    google_client_id = get_google_client_id()
    if not google_client_id:
        return None, "Máy chủ chưa cấu hình GOOGLE_CLIENT_ID.", 500

    if not _HAS_GOOGLE_AUTH:
        return None, "Máy chủ chưa cài thư viện google-auth.", 500

    try:
        id_info = google_id_token.verify_oauth2_token(
            token_value,
            google_requests.Request(),
            google_client_id,
        )
    except Exception:
        return None, "Google token không hợp lệ hoặc đã hết hạn.", 400

    issuer = (id_info.get("iss") or "").strip().lower()
    if issuer not in {"accounts.google.com", "https://accounts.google.com"}:
        return None, "Nguồn token Google không hợp lệ.", 400

    email = (id_info.get("email") or "").strip().lower()
    if not email:
        return None, "Không lấy được email từ Google.", 400
    if not bool(id_info.get("email_verified")):
        return None, "Email Google chưa được xác minh.", 400

    return id_info, "", 200


@app.route("/api/register/google", methods=["POST"])
def api_register_google():
    data = request.get_json() or {}
    id_info, err_msg, status = _verify_google_identity_token(data.get("idToken"))
    if not id_info:
        return jsonify({"success": False, "error": err_msg}), status

    email = (id_info.get("email") or "").strip().lower()
    fallback_name = email.split("@", 1)[0]
    fullname_from_google = (id_info.get("name") or "").strip()
    avatar_from_google = (id_info.get("picture") or "").strip()
    resolved_fullname = fullname_from_google or fallback_name

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        if using_sqlserver():
            cursor.execute(
                "SELECT TOP 1 id, fullname FROM users WHERE lower(email) = ?",
                (email,),
            )
        else:
            cursor.execute(
                "SELECT id, fullname FROM users WHERE lower(email) = ? LIMIT 1",
                (email,),
            )
        user = cursor.fetchone()

        if user:
            user_id = user[0]
            updates = []
            params = []
            if not (user[1] or "").strip():
                updates.append("fullname = ?")
                params.append(resolved_fullname)
            if avatar_from_google:
                updates.append("google_avatar_url = ?")
                params.append(avatar_from_google)
            if updates:
                params.append(user_id)
                cursor.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", tuple(params))
                conn.commit()
            conn.close()
            return jsonify({
                "success": True,
                "message": "Email Google này đã có tài khoản. Vui lòng đăng nhập.",
                "alreadyExists": True,
            })

        cursor.execute("SELECT COUNT(*) FROM users")
        user_count = int(cursor.fetchone()[0] or 0)
        role = "admin" if user_count == 0 else "user"
        random_password_hash = generate_password_hash(secrets.token_urlsafe(32))
        cursor.execute(
            "INSERT INTO users (fullname, email, password_hash, role, avatar_url, google_avatar_url) VALUES (?, ?, ?, ?, ?, ?)",
            (resolved_fullname, email, random_password_hash, role, avatar_from_google, avatar_from_google),
        )
        conn.commit()
        conn.close()

        return jsonify({
            "success": True,
            "message": "Đăng ký Google thành công! Vui lòng đăng nhập để tiếp tục.",
            "alreadyExists": False,
        })
    except Exception as e:
        return jsonify({"success": False, "error": f"Lỗi hệ thống: {str(e)}"}), 500


@app.route("/api/login/google", methods=["POST"])
def api_login_google():
    data = request.get_json() or {}
    id_info, err_msg, status = _verify_google_identity_token(data.get("idToken"))
    if not id_info:
        return jsonify({"success": False, "error": err_msg}), status

    email = (id_info.get("email") or "").strip().lower()
    fullname_from_google = (id_info.get("name") or "").strip()
    avatar_from_google = (id_info.get("picture") or "").strip()

    fallback_name = email.split("@", 1)[0]
    resolved_fullname = fullname_from_google or fallback_name

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        if using_sqlserver():
            cursor.execute(
                "SELECT TOP 1 id, fullname, role, avatar_url FROM users WHERE lower(email) = ?",
                (email,),
            )
        else:
            cursor.execute(
                "SELECT id, fullname, role, avatar_url FROM users WHERE lower(email) = ? LIMIT 1",
                (email,),
            )
        user = cursor.fetchone()

        if user:
            user_id = user[0]
            fullname = (user[1] or "").strip() or resolved_fullname
            role = user[2] or "user"
            current_avatar_url = (user[3] or "").strip()
            avatar_url = current_avatar_url

            updates = []
            params = []

            # Sync fullname if existing record is blank.
            if not (user[1] or "").strip():
                updates.append("fullname = ?")
                params.append(fullname)

            # Always keep google_avatar_url in sync.
            if avatar_from_google:
                updates.append("google_avatar_url = ?")
                params.append(avatar_from_google)

            # Keep Google avatar in sync unless user already switched to a custom avatar.
            if avatar_from_google and (not current_avatar_url or is_google_avatar_url(current_avatar_url)):
                avatar_url = avatar_from_google
                updates.append("avatar_url = ?")
                params.append(avatar_url)

            if updates:
                params.append(user_id)
                cursor.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", tuple(params))
                conn.commit()
        else:
            cursor.execute("SELECT COUNT(*) FROM users")
            user_count = int(cursor.fetchone()[0] or 0)
            role = "admin" if user_count == 0 else "user"
            random_password_hash = generate_password_hash(secrets.token_urlsafe(32))
            cursor.execute(
                "INSERT INTO users (fullname, email, password_hash, role, avatar_url, google_avatar_url) VALUES (?, ?, ?, ?, ?, ?)",
                (resolved_fullname, email, random_password_hash, role, avatar_from_google, avatar_from_google),
            )
            conn.commit()
            user_id = get_inserted_id(cursor)
            fullname = resolved_fullname
            avatar_url = avatar_from_google

        conn.close()

        session["user_id"] = user_id
        session["fullname"] = fullname
        session["email"] = email
        session["role"] = role

        return jsonify({
            "success": True,
            "message": f"Đăng nhập Google thành công! Xin chào {fullname}",
            "user": {
                "fullname": fullname,
                "email": email,
                "role": role,
                "avatar_url": avatar_url,
            },
        })
    except Exception as e:
        return jsonify({"success": False, "error": f"Lỗi hệ thống: {str(e)}"}), 500


@app.route("/api/forgot-password", methods=["POST"])
def api_forgot_password():
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()

    if not email:
        return jsonify({"success": False, "error": "Vui lòng nhập email."}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, email FROM users WHERE lower(email) = ?", (email,))
        user = cursor.fetchone()
        if not user:
            conn.close()
            return jsonify({"success": False, "error": "Email chưa được đăng ký trong hệ thống."}), 400

        user_id = user[0]
        otp_code = generate_otp()
        otp_expires_at = (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")

        sent_ok, sent_err = send_reset_otp_email(user[1], otp_code)
        use_fallback = (not sent_ok) and is_dev_otp_fallback_enabled()
        if not sent_ok and not use_fallback:
            conn.close()
            return jsonify({"success": False, "error": sent_err}), 500

        cursor.execute("UPDATE password_reset_otps SET used = 1 WHERE user_id = ? AND used = 0", (user_id,))
        cursor.execute(
            "INSERT INTO password_reset_otps (user_id, otp_code, expires_at, used) VALUES (?, ?, ?, 0)",
            (user_id, otp_code, otp_expires_at)
        )
        conn.commit()
        conn.close()

        response = {
            "success": True,
            "message": "Mã OTP đã được gửi về email của bạn.",
            "requireOtp": True,
            "otpDelivery": "email",
        }

        if use_fallback:
            response["otpDelivery"] = "dev-fallback"
            response["message"] = "SMTP đang lỗi. Hệ thống đã bật OTP fallback để bạn tiếp tục đặt lại mật khẩu."
            response["devOtp"] = otp_code
            response["warning"] = sent_err

        return jsonify(response)
    except Exception as e:
        return jsonify({"success": False, "error": f"Lỗi hệ thống: {str(e)}"}), 500


@app.route("/api/test-smtp", methods=["POST"])
def api_test_smtp():
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"success": False, "error": "Vui lòng nhập email để kiểm tra SMTP."}), 400

    try:
        smtp = get_smtp_settings()
        if smtp["missing_keys"]:
            missing = ", ".join(smtp["missing_keys"])
            return jsonify({"success": False, "error": f"Thiếu cấu hình SMTP: {missing}."}), 400
        if smtp["invalid_keys"]:
            invalid = ", ".join(smtp["invalid_keys"])
            return jsonify({"success": False, "error": f"File .env đang dùng giá trị mẫu cho: {invalid}."}), 400

        ok, err = send_email_message(
            to_email=email,
            subject="SMTP Test - OCR Thuoc",
            content=(
                "Day la email kiem tra cau hinh SMTP tu he thong OCR Thuoc.\n"
                "Neu ban nhan duoc email nay, chuc nang gui OTP da san sang hoat dong.\n"
            ),
        )
        if not ok:
            return jsonify({"success": False, "error": err}), 500

        return jsonify({"success": True, "message": "Kiểm tra SMTP thành công. Email test đã được gửi."})
    except Exception as e:
        return jsonify({"success": False, "error": f"Lỗi hệ thống: {str(e)}"}), 500


@app.route("/api/verify-reset-otp", methods=["POST"])
def api_verify_reset_otp():
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    otp = (data.get("otp") or "").strip()

    if not email or not otp:
        return jsonify({"success": False, "error": "Vui lòng nhập email và mã OTP."}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM users WHERE lower(email) = ?", (email,))
        user = cursor.fetchone()
        if not user:
            conn.close()
            return jsonify({"success": False, "error": "Email không tồn tại trong hệ thống."}), 400

        user_id = user[0]
        if using_sqlserver():
            cursor.execute(
                """
                SELECT TOP 1 id, expires_at, used
                FROM password_reset_otps
                WHERE user_id = ? AND otp_code = ?
                ORDER BY id DESC
                """,
                (user_id, otp)
            )
        else:
            cursor.execute(
                """
                SELECT id, expires_at, used
                FROM password_reset_otps
                WHERE user_id = ? AND otp_code = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (user_id, otp)
            )
        otp_row = cursor.fetchone()

        if not otp_row:
            conn.close()
            return jsonify({"success": False, "error": "Mã OTP không đúng."}), 400

        if int(otp_row[2]) == 1:
            conn.close()
            return jsonify({"success": False, "error": "Mã OTP đã được sử dụng."}), 400

        expires_at = parse_db_datetime(otp_row[1])
        if not expires_at:
            conn.close()
            return jsonify({"success": False, "error": "Dữ liệu hạn OTP không hợp lệ."}), 500
        if expires_at < datetime.now():
            cursor.execute("UPDATE password_reset_otps SET used = 1 WHERE id = ?", (otp_row[0],))
            conn.commit()
            conn.close()
            return jsonify({"success": False, "error": "Mã OTP đã hết hạn. Vui lòng yêu cầu mã mới."}), 400

        cursor.execute("UPDATE password_reset_otps SET used = 1 WHERE id = ?", (otp_row[0],))

        token = secrets.token_urlsafe(32)
        expires_at_token = (datetime.now() + timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("UPDATE password_reset_tokens SET used = 1 WHERE user_id = ? AND used = 0", (user_id,))
        cursor.execute(
            "INSERT INTO password_reset_tokens (user_id, token, expires_at, used) VALUES (?, ?, ?, 0)",
            (user_id, token, expires_at_token)
        )

        conn.commit()
        conn.close()

        return jsonify({
            "success": True,
            "message": "Xác thực OTP thành công.",
            "resetPath": f"/reset-password?token={token}"
        })
    except Exception as e:
        return jsonify({"success": False, "error": f"Lỗi hệ thống: {str(e)}"}), 500


@app.route("/api/reset-password", methods=["POST"])
def api_reset_password():
    data = request.get_json() or {}
    token = (data.get("token") or "").strip()
    new_password = (data.get("newPassword") or "").strip()

    if not token or not new_password:
        return jsonify({"success": False, "error": "Thiếu dữ liệu đặt lại mật khẩu."}), 400

    if len(new_password) < 6:
        return jsonify({"success": False, "error": "Mật khẩu mới cần ít nhất 6 ký tự."}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, user_id, expires_at, used FROM password_reset_tokens WHERE token = ?",
            (token,)
        )
        token_row = cursor.fetchone()

        if not token_row:
            conn.close()
            return jsonify({"success": False, "error": "Liên kết đặt lại mật khẩu không hợp lệ."}), 400

        if int(token_row[3]) == 1:
            conn.close()
            return jsonify({"success": False, "error": "Liên kết này đã được sử dụng."}), 400

        expires_at = parse_db_datetime(token_row[2])
        if not expires_at:
            conn.close()
            return jsonify({"success": False, "error": "Dữ liệu hạn token không hợp lệ."}), 500
        if expires_at < datetime.now():
            conn.close()
            return jsonify({"success": False, "error": "Liên kết đặt lại mật khẩu đã hết hạn."}), 400

        password_hash = generate_password_hash(new_password)
        cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, token_row[1]))
        cursor.execute("UPDATE password_reset_tokens SET used = 1 WHERE id = ?", (token_row[0],))
        conn.commit()
        conn.close()

        return jsonify({"success": True, "message": "Đặt lại mật khẩu thành công. Vui lòng đăng nhập lại."})
    except Exception as e:
        return jsonify({"success": False, "error": f"Lỗi hệ thống: {str(e)}"}), 500


@app.route("/api/profile", methods=["GET"])
def api_profile_get():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "Bạn chưa đăng nhập."}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT fullname, email, phone, birth_date, address, bio, role, avatar_url, google_avatar_url
            FROM users
            WHERE id = ?
            """,
            (user_id,)
        )
        user = cursor.fetchone()
        conn.close()

        if not user:
            return jsonify({"success": False, "error": "Không tìm thấy người dùng."}), 404

        return jsonify({
            "success": True,
            "data": {
                "fullname": user[0] or "",
                "email": user[1] or "",
                "phone": user[2] or "",
                "birth_date": user[3] or "",
                "address": user[4] or "",
                "bio": user[5] or "",
                "role": user[6] or "user",
                "avatar_url": user[7] or "",
                "google_avatar_url": user[8] or ""
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": f"Lỗi hệ thống: {str(e)}"}), 500


@app.route("/api/profile", methods=["PUT"])
def api_profile_update():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "Bạn chưa đăng nhập."}), 401

    data = request.get_json() or {}
    fullname = (data.get("fullname") or "").strip()
    phone = (data.get("phone") or "").strip()
    birth_date = (data.get("birth_date") or "").strip()
    address = (data.get("address") or "").strip()
    bio = (data.get("bio") or "").strip()

    if not fullname:
        return jsonify({"success": False, "error": "Họ và tên không được để trống."}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE users
            SET fullname = ?, phone = ?, birth_date = ?, address = ?, bio = ?
            WHERE id = ?
            """,
            (fullname, phone, birth_date, address, bio, user_id)
        )
        conn.commit()
        conn.close()

        session["fullname"] = fullname
        return jsonify({"success": True, "message": "Cập nhật hồ sơ thành công."})
    except Exception as e:
        return jsonify({"success": False, "error": f"Lỗi hệ thống: {str(e)}"}), 500


@app.route("/api/profile/avatar/google", methods=["POST"])
def api_profile_use_google_avatar():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "Bạn chưa đăng nhập."}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if using_sqlserver():
            cursor.execute("SELECT TOP 1 google_avatar_url, avatar_url FROM users WHERE id = ?", (user_id,))
        else:
            cursor.execute("SELECT google_avatar_url, avatar_url FROM users WHERE id = ? LIMIT 1", (user_id,))
        row = cursor.fetchone()
        if not row or not (row[0] or "").strip():
            conn.close()
            return jsonify({"success": False, "error": "Không tìm thấy ảnh Google. Hãy đăng nhập lại bằng Google để đồng bộ."}), 400

        google_url = row[0].strip()
        old_avatar_url = (row[1] or "").strip()

        cursor.execute("UPDATE users SET avatar_url = ? WHERE id = ?", (google_url, user_id))
        conn.commit()
        conn.close()

        # Clean up old local avatar file if any
        old_local_path = resolve_local_avatar_path(old_avatar_url)
        if old_local_path and os.path.isfile(old_local_path):
            try:
                os.remove(old_local_path)
            except Exception:
                pass

        return jsonify({
            "success": True,
            "message": "Đã chuyển sang ảnh Google.",
            "data": {"avatar_url": google_url}
        })
    except Exception as e:
        return jsonify({"success": False, "error": f"Lỗi hệ thống: {str(e)}"}), 500


@app.route("/api/profile/avatar", methods=["PUT", "POST"])
def api_profile_update_avatar():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "Bạn chưa đăng nhập."}), 401

    if "avatar" not in request.files:
        return jsonify({"success": False, "error": "Không tìm thấy file ảnh avatar."}), 400

    avatar_file = request.files["avatar"]
    if not avatar_file or not avatar_file.filename:
        return jsonify({"success": False, "error": "Vui lòng chọn ảnh avatar."}), 400

    if not allowed_avatar_file(avatar_file.filename):
        return jsonify({"success": False, "error": "Định dạng avatar không hợp lệ. Chỉ hỗ trợ PNG/JPG/JPEG/WEBP/GIF/BMP/TIFF."}), 400

    file_bytes = avatar_file.read()
    if not file_bytes:
        return jsonify({"success": False, "error": "File avatar rỗng."}), 400

    if len(file_bytes) > MAX_AVATAR_FILE_SIZE:
        return jsonify({"success": False, "error": "Ảnh avatar quá lớn. Tối đa 10MB."}), 400

    safe_filename = secure_filename(avatar_file.filename)
    extension = safe_filename.rsplit(".", 1)[1].lower()
    new_filename = f"user_{user_id}_{uuid.uuid4().hex[:12]}.{extension}"
    new_avatar_path = os.path.join(AVATAR_UPLOAD_FOLDER, new_filename)

    try:
        old_avatar_url = ""
        conn = get_db_connection()
        cursor = conn.cursor()
        if using_sqlserver():
            cursor.execute("SELECT TOP 1 avatar_url FROM users WHERE id = ?", (user_id,))
        else:
            cursor.execute("SELECT avatar_url FROM users WHERE id = ? LIMIT 1", (user_id,))
        row = cursor.fetchone()
        if row:
            old_avatar_url = row[0] or ""

        with open(new_avatar_path, "wb") as output:
            output.write(file_bytes)

        new_avatar_url = avatar_public_url(new_filename)
        cursor.execute("UPDATE users SET avatar_url = ? WHERE id = ?", (new_avatar_url, user_id))
        conn.commit()
        conn.close()

        old_local_path = resolve_local_avatar_path(old_avatar_url)
        if old_local_path and os.path.isfile(old_local_path) and old_local_path != new_avatar_path:
            try:
                os.remove(old_local_path)
            except Exception:
                pass

        return jsonify({
            "success": True,
            "message": "Cập nhật avatar thành công.",
            "data": {
                "avatar_url": new_avatar_url,
            },
        })
    except Exception as e:
        if os.path.isfile(new_avatar_path):
            try:
                os.remove(new_avatar_path)
            except Exception:
                pass
        return jsonify({"success": False, "error": f"Lỗi hệ thống: {str(e)}"}), 500


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"success": True, "message": "Đăng xuất thành công."})


@app.route("/api/history/summary", methods=["GET"])
def api_history_summary():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({
            "success": True,
            "data": {
                "total": 0,
                "today": 0,
                "last7Days": 0,
                "last30Days": 0,
                "errors": 0
            }
        })

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM scan_history WHERE user_id = ?", (user_id,))
        total = cursor.fetchone()[0]

        if using_sqlserver():
            cursor.execute(
                "SELECT COUNT(*) FROM scan_history WHERE user_id = ? AND CAST(created_at AS date) = CAST(GETDATE() AS date)",
                (user_id,),
            )
        else:
            cursor.execute(
                "SELECT COUNT(*) FROM scan_history WHERE user_id = ? AND date(created_at) = date('now', 'localtime')",
                (user_id,),
            )
        today = cursor.fetchone()[0]

        if using_sqlserver():
            cursor.execute(
                "SELECT COUNT(*) FROM scan_history WHERE user_id = ? AND created_at >= DATEADD(day, -7, GETDATE())",
                (user_id,),
            )
        else:
            cursor.execute(
                "SELECT COUNT(*) FROM scan_history WHERE user_id = ? AND datetime(created_at) >= datetime('now', '-7 days', 'localtime')",
                (user_id,),
            )
        last_7_days = cursor.fetchone()[0]

        if using_sqlserver():
            cursor.execute(
                "SELECT COUNT(*) FROM scan_history WHERE user_id = ? AND created_at >= DATEADD(day, -30, GETDATE())",
                (user_id,),
            )
        else:
            cursor.execute(
                "SELECT COUNT(*) FROM scan_history WHERE user_id = ? AND datetime(created_at) >= datetime('now', '-30 days', 'localtime')",
                (user_id,),
            )
        last_30_days = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM scan_error_logs WHERE user_id = ?", (user_id,))
        error_count = cursor.fetchone()[0]
        conn.close()

        return jsonify({
            "success": True,
            "data": {
                "total": total,
                "today": today,
                "last7Days": last_7_days,
                "last30Days": last_30_days,
                "errors": error_count,
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": f"Lỗi hệ thống: {str(e)}"}), 500


@app.route("/api/stats/dashboard", methods=["GET"])
def api_stats_dashboard():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "Bạn chưa đăng nhập."}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        if using_sqlserver():
            cursor.execute(
                """
                SELECT filename, created_at
                FROM scan_history
                WHERE user_id = ?
                ORDER BY id DESC
                """,
                (user_id,),
            )
        else:
            cursor.execute(
                """
                SELECT filename, created_at
                FROM scan_history
                WHERE user_id = ?
                ORDER BY id DESC
                """,
                (user_id,),
            )
        history_rows = cursor.fetchall()

        if using_sqlserver():
            cursor.execute(
                """
                SELECT error_message, created_at
                FROM scan_error_logs
                WHERE user_id = ?
                ORDER BY id DESC
                """,
                (user_id,),
            )
        else:
            cursor.execute(
                """
                SELECT error_message, created_at
                FROM scan_error_logs
                WHERE user_id = ?
                ORDER BY id DESC
                """,
                (user_id,),
            )
        error_rows = cursor.fetchall()
        conn.close()

        now = datetime.now()
        start_30d = now - timedelta(days=29)
        scans_by_date = defaultdict(int)
        scans_by_hour = [0] * 24
        scans_by_weekday = [0] * 7
        filename_counter = Counter()

        total_scans = 0
        today_scans = 0
        last_7_days_scans = 0
        last_30_days_scans = 0

        for row in history_rows:
            filename = str(row[0] or "")
            created_at = parse_db_datetime(row[1])
            if not created_at:
                continue

            total_scans += 1
            filename_counter[filename] += 1
            scans_by_hour[created_at.hour] += 1
            scans_by_weekday[created_at.weekday()] += 1

            date_key = created_at.strftime("%Y-%m-%d")
            scans_by_date[date_key] += 1

            if created_at.date() == now.date():
                today_scans += 1
            if created_at >= (now - timedelta(days=7)):
                last_7_days_scans += 1
            if created_at >= (now - timedelta(days=30)):
                last_30_days_scans += 1

        total_errors = 0
        today_errors = 0
        last_7_days_errors = 0
        for row in error_rows:
            created_at = parse_db_datetime(row[1])
            if not created_at:
                continue
            total_errors += 1
            if created_at.date() == now.date():
                today_errors += 1
            if created_at >= (now - timedelta(days=7)):
                last_7_days_errors += 1

        timeline_labels = []
        timeline_values = []
        for i in range(30):
            d = start_30d + timedelta(days=i)
            date_key = d.strftime("%Y-%m-%d")
            timeline_labels.append(d.strftime("%d/%m"))
            timeline_values.append(scans_by_date.get(date_key, 0))

        top_filenames = [
            {"filename": filename, "count": count}
            for filename, count in filename_counter.most_common(8)
        ]

        total_attempts = total_scans + total_errors
        success_rate = (total_scans / total_attempts * 100.0) if total_attempts > 0 else 0.0
        error_rate = (total_errors / total_attempts * 100.0) if total_attempts > 0 else 0.0
        avg_per_day_30 = (last_30_days_scans / 30.0)

        peak_day_label = None
        peak_day_value = 0
        for i in range(30):
            d = start_30d + timedelta(days=i)
            date_key = d.strftime("%Y-%m-%d")
            count = scans_by_date.get(date_key, 0)
            if count > peak_day_value:
                peak_day_value = count
                peak_day_label = d.strftime("%d/%m/%Y")

        weekday_labels = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật"]

        return jsonify({
            "success": True,
            "data": {
                "kpi": {
                    "totalScans": total_scans,
                    "todayScans": today_scans,
                    "last7DaysScans": last_7_days_scans,
                    "last30DaysScans": last_30_days_scans,
                    "totalErrors": total_errors,
                    "todayErrors": today_errors,
                    "last7DaysErrors": last_7_days_errors,
                    "successRate": round(success_rate, 2),
                    "errorRate": round(error_rate, 2),
                    "avgPerDay30": round(avg_per_day_30, 2),
                    "peakDay": {
                        "date": peak_day_label,
                        "count": peak_day_value,
                    },
                },
                "charts": {
                    "scansByDate30": {
                        "labels": timeline_labels,
                        "values": timeline_values,
                    },
                    "scansByHour": {
                        "labels": [f"{h:02d}:00" for h in range(24)],
                        "values": scans_by_hour,
                    },
                    "scansByWeekday": {
                        "labels": weekday_labels,
                        "values": scans_by_weekday,
                    },
                    "qualityDonut": {
                        "labels": ["Thành công", "Lỗi"],
                        "values": [total_scans, total_errors],
                    },
                },
                "topFilenames": top_filenames,
            },
        })
    except Exception as e:
        return jsonify({"success": False, "error": f"Lỗi hệ thống: {str(e)}"}), 500


@app.route("/api/admin/users", methods=["GET"])
def api_admin_users():
    if not session.get("user_id"):
        return jsonify({"success": False, "error": "Bạn chưa đăng nhập."}), 401
    if not is_admin():
        return jsonify({"success": False, "error": "Bạn không có quyền truy cập."}), 403

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, fullname, email, role FROM users ORDER BY id ASC")
        users = cursor.fetchall()
        conn.close()

        return jsonify({
            "success": True,
            "data": [
                {
                    "id": u[0],
                    "fullname": u[1],
                    "email": u[2],
                    "role": u[3] or "user",
                }
                for u in users
            ],
        })
    except Exception as e:
        return jsonify({"success": False, "error": f"Lỗi hệ thống: {str(e)}"}), 500


@app.route("/api/admin/db-health", methods=["GET"])
def api_admin_db_health():
    if not session.get("user_id"):
        return jsonify({"success": False, "error": "Bạn chưa đăng nhập."}), 401
    if not is_admin():
        return jsonify({"success": False, "error": "Bạn không có quyền truy cập."}), 403

    started = datetime.now()
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        db_backend = get_db_backend_label()

        if using_sqlserver():
            cursor.execute("SELECT DB_NAME()")
            row = cursor.fetchone()
            db_name = str(row[0]) if row and row[0] else os.environ.get("SQLSERVER_DATABASE", "unknown")
        else:
            cursor.execute("SELECT 1")
            db_name = os.path.basename(DB_PATH)

        ping_ms = int((datetime.now() - started).total_seconds() * 1000)
        return jsonify({
            "success": True,
            "data": {
                "backend": db_backend,
                "connected": True,
                "database": db_name,
                "pingMs": ping_ms,
                "checkedAt": datetime.now().isoformat(timespec="seconds")
            }
        })
    except Exception as e:
        ping_ms = int((datetime.now() - started).total_seconds() * 1000)
        return jsonify({
            "success": True,
            "data": {
                "backend": get_db_backend_label(),
                "connected": False,
                "database": None,
                "pingMs": ping_ms,
                "checkedAt": datetime.now().isoformat(timespec="seconds"),
                "error": str(e)
            }
        })
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


@app.route("/api/history", methods=["GET"])
def api_history_get():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": True, "data": []})

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if using_sqlserver():
            cursor.execute(
                """
                SELECT TOP 50 id, filename, full_text, created_at
                FROM scan_history
                WHERE user_id = ?
                ORDER BY id DESC
                """,
                (user_id,)
            )
        else:
            cursor.execute(
                """
                SELECT id, filename, full_text, created_at
                FROM scan_history
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT 50
                """,
                (user_id,)
            )
        rows = cursor.fetchall()
        conn.close()

        history = []
        for row in rows:
            text = row[2] or ""
            history.append({
                "id": row[0],
                "filename": row[1],
                "fullText": text,
                "text": text[:120] + ("..." if len(text) > 120 else ""),
                "createdAt": str(row[3])
            })

        return jsonify({"success": True, "data": history})
    except Exception as e:
        return jsonify({"success": False, "error": f"Lỗi hệ thống: {str(e)}"}), 500


@app.route("/api/history", methods=["POST"])
def api_history_create():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "Bạn chưa đăng nhập."}), 401

    data = request.get_json() or {}
    filename = (data.get("filename") or "").strip()
    full_text = (data.get("fullText") or "").strip()

    if not filename or not full_text:
        return jsonify({"success": False, "error": "Thiếu dữ liệu lịch sử."}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO scan_history (user_id, filename, full_text) VALUES (?, ?, ?)",
            (user_id, filename, full_text)
        )
        history_id = get_inserted_id(cursor)
        conn.commit()
        conn.close()
        return jsonify({"success": True, "id": history_id})
    except Exception as e:
        return jsonify({"success": False, "error": f"Lỗi hệ thống: {str(e)}"}), 500


@app.route("/api/history/send-email", methods=["POST"])
def api_history_send_email():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "Bạn chưa đăng nhập."}), 401

    data = request.get_json() or {}
    history_id = data.get("id")

    try:
        history_id = int(history_id)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "ID lịch sử không hợp lệ."}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT email, fullname FROM users WHERE id = ?", (user_id,))
        user_row = cursor.fetchone()
        if not user_row:
            conn.close()
            return jsonify({"success": False, "error": "Không tìm thấy thông tin người dùng."}), 404

        to_email = (user_row[0] or "").strip()
        fullname = user_row[1] or "Người dùng"
        if not to_email:
            conn.close()
            return jsonify({"success": False, "error": "Tài khoản của bạn chưa có email hợp lệ để nhận bản in."}), 400

        cursor.execute(
            "SELECT filename, full_text, created_at FROM scan_history WHERE id = ? AND user_id = ?",
            (history_id, user_id)
        )
        history_row = cursor.fetchone()
        conn.close()

        if not history_row:
            return jsonify({"success": False, "error": "Không tìm thấy đơn thuốc cần gửi mail."}), 404

        filename = history_row[0] or "don-thuoc"
        full_text = (history_row[1] or "").strip()
        created_at = str(history_row[2] or "")

        if not full_text:
            return jsonify({"success": False, "error": "Nội dung đơn thuốc trống, không thể gửi email."}), 400

        subject = f"[{APP_DISPLAY_NAME}] Bản in đơn thuốc: {filename}"
        plain_content = (
            f"Xin chào {fullname},\n\n"
            f"Hệ thống gửi bạn nội dung đơn thuốc đã chọn.\n"
            f"Tệp: {filename}\n"
            f"Thời gian quét: {created_at}\n\n"
            f"--- NỘI DUNG ĐƠN THUỐC ---\n{full_text}\n\n"
            "Bạn có thể dùng email này như một bản in điện tử để lưu trữ/in lại khi cần."
        )
        html_content = _build_history_result_email_html(filename, created_at, fullname, full_text)

        sent_ok, sent_err = send_email_message(
            to_email=to_email,
            subject=subject,
            content=plain_content,
            html_content=html_content,
            from_name=APP_DISPLAY_NAME,
        )
        if not sent_ok:
            return jsonify({"success": False, "error": sent_err}), 500

        return jsonify({
            "success": True,
            "message": "Đã gửi bản in đơn thuốc về email của bạn.",
        })
    except Exception as e:
        return jsonify({"success": False, "error": f"Lỗi hệ thống: {str(e)}"}), 500


@app.route("/api/history", methods=["PUT"])
def api_history_rename():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "Bạn chưa đăng nhập."}), 401

    data = request.get_json() or {}
    history_id = data.get("id")
    new_filename = (data.get("filename") or "").strip()

    try:
        history_id = int(history_id)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "ID lịch sử không hợp lệ."}), 400

    if not new_filename:
        return jsonify({"success": False, "error": "Tên file mới không được để trống."}), 400
    if len(new_filename) > 255:
        return jsonify({"success": False, "error": "Tên file quá dài (tối đa 255 ký tự)."}), 400
    if "/" in new_filename or "\\" in new_filename:
        return jsonify({"success": False, "error": "Tên file không hợp lệ."}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE scan_history SET filename = ? WHERE id = ? AND user_id = ?",
            (new_filename, history_id, user_id)
        )
        updated_count = cursor.rowcount
        conn.commit()
        conn.close()

        if updated_count == 0:
            return jsonify({"success": False, "error": "Không tìm thấy đơn thuốc cần đổi tên."}), 404

        return jsonify({"success": True, "message": "Đổi tên file thành công.", "filename": new_filename})
    except Exception as e:
        return jsonify({"success": False, "error": f"Lỗi hệ thống: {str(e)}"}), 500


@app.route("/api/history", methods=["DELETE"])
def api_history_clear():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "Bạn chưa đăng nhập."}), 401

    data = request.get_json(silent=True) or {}
    history_id = data.get("id")

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        if history_id is not None:
            try:
                history_id = int(history_id)
            except (TypeError, ValueError):
                conn.close()
                return jsonify({"success": False, "error": "ID lịch sử không hợp lệ."}), 400

            cursor.execute(
                "DELETE FROM scan_history WHERE id = ? AND user_id = ?",
                (history_id, user_id)
            )
            deleted_count = cursor.rowcount
            conn.commit()
            conn.close()

            if deleted_count == 0:
                return jsonify({"success": False, "error": "Không tìm thấy đơn thuốc cần xóa."}), 404

            return jsonify({"success": True, "message": "Đã xóa đơn thuốc."})

        cursor.execute("DELETE FROM scan_history WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Đã xóa lịch sử."})
    except Exception as e:
        return jsonify({"success": False, "error": f"Lỗi hệ thống: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(debug=False, use_reloader=False, port=5000)

