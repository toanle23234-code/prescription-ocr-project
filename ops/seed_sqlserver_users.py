import argparse
import os
from datetime import datetime, timedelta
from pathlib import Path

from werkzeug.security import generate_password_hash

try:
    import pyodbc
except Exception as exc:  # pragma: no cover
    raise RuntimeError("pyodbc is required for SQL Server seeding") from exc


def load_env_file(project_root: Path) -> None:
    env_path = project_root / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def build_sqlserver_connection_string() -> str:
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


def resolve_users_table(cursor) -> str:
    cursor.execute(
        """
        SELECT TOP 1 TABLE_SCHEMA, TABLE_NAME
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE = 'BASE TABLE' AND LOWER(TABLE_NAME) = 'users'
        ORDER BY TABLE_SCHEMA
        """
    )
    row = cursor.fetchone()
    if not row:
        raise RuntimeError("Không tìm thấy bảng users trong database hiện tại.")
    return f"[{row[0]}].[{row[1]}]"


def fetch_columns(cursor, table_name: str):
    schema_name, raw_name = [part.strip("[]") for part in table_name.split(".")]
    cursor.execute(
        """
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
        """,
        (schema_name, raw_name),
    )
    return {str(row[0]).lower() for row in cursor.fetchall()}


def has_email(cursor, table_name: str, email: str) -> bool:
    cursor.execute(f"SELECT TOP 1 1 FROM {table_name} WHERE LOWER(email) = ?", (email.lower(),))
    return cursor.fetchone() is not None


def build_sample_rows(count: int):
    names = [
        "Nguyen Quoc Khanh",
        "Huynh Van Quoc Khanh",
        "Khanh Test",
        "Nguyen Thai Hao",
        "Khanh Huynh",
        "Le Nhut Toan",
        "Tran Minh Duc",
        "Pham Gia Bao",
        "Doan Thanh Nhan",
        "Vo Ngoc Han",
    ]
    genders = ["Nam", "Nu", "Nam", "Nam", "Nu", "Nam", "Nam", "Nam", "Nu", "Nu"]
    providers = ["local", "local", "local", "local", "google", "local", "local", "local", "google", "local"]

    rows = []
    base_birth = datetime(2000, 1, 1)
    for idx in range(count):
        name = names[idx % len(names)]
        provider = providers[idx % len(providers)]
        gender = genders[idx % len(genders)]
        birth_date = (base_birth + timedelta(days=idx * 380)).strftime("%Y-%m-%d")
        created_at = (datetime.now() - timedelta(days=(count - idx))).strftime("%Y-%m-%d %H:%M:%S")
        email_local = name.lower().replace(" ", "")
        email = f"{email_local}{idx + 1:02d}@gmail.com"

        rows.append(
            {
                "username": f"user{idx + 1}",
                "passwordhash": generate_password_hash(f"User@{idx + 1:03d}"),
                "password_hash": generate_password_hash(f"User@{idx + 1:03d}"),
                "provider": provider,
                "createdat": created_at,
                "created_at": created_at,
                "email": email,
                "fullname": name,
                "full_name": name,
                "phone": f"09{78000000 + idx:08d}",
                "dateofbirth": birth_date,
                "birth_date": birth_date,
                "gender": gender,
                "address": "TP. Ho Chi Minh",
                "profilepicture": None,
                "updatedat": created_at,
                "isactive": 1,
                "googleid": None,
                "bio": "Nguoi dung mau cho kiem thu SQL Server",
                "role": "user",
            }
        )
    return rows


def insert_row(cursor, table_name: str, allowed_columns: set[str], row: dict) -> bool:
    if "email" not in allowed_columns:
        raise RuntimeError("Bảng users phải có cột email để seed dữ liệu.")

    email_value = row.get("email")
    if not email_value:
        return False
    if has_email(cursor, table_name, email_value):
        return False

    insert_columns = []
    insert_values = []
    for key, value in row.items():
        if key.lower() in allowed_columns:
            insert_columns.append(key)
            insert_values.append(value)

    if not insert_columns:
        return False

    placeholders = ", ".join(["?"] * len(insert_columns))
    columns_sql = ", ".join([f"[{col}]" for col in insert_columns])
    sql = f"INSERT INTO {table_name} ({columns_sql}) VALUES ({placeholders})"
    cursor.execute(sql, tuple(insert_values))
    return True


def seed_sqlserver_users(count: int) -> None:
    project_root = Path(__file__).resolve().parents[1]
    load_env_file(project_root)

    conn = pyodbc.connect(build_sqlserver_connection_string())
    inserted = 0
    skipped = 0

    try:
        cursor = conn.cursor()
        table_name = resolve_users_table(cursor)
        allowed_columns = fetch_columns(cursor, table_name)

        for row in build_sample_rows(count):
            if insert_row(cursor, table_name, allowed_columns, row):
                inserted += 1
            else:
                skipped += 1

        conn.commit()
    finally:
        conn.close()

    print(f"Users table: {table_name}")
    print(f"Seed inserted: {inserted}")
    print(f"Seed skipped (email existed/invalid): {skipped}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed sample users into SQL Server users table")
    parser.add_argument("--count", type=int, default=12, help="Number of sample users to insert")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    seed_sqlserver_users(max(1, int(args.count)))


if __name__ == "__main__":
    main()
