import argparse
import os
import re
from pathlib import Path

from werkzeug.security import generate_password_hash

try:
    import pyodbc
except Exception as exc:  # pragma: no cover
    raise RuntimeError("pyodbc is required for SQL Server initialization") from exc


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


def split_sqlserver_batches(sql: str):
    # SSMS style batch separator: GO on its own line.
    return [chunk.strip() for chunk in re.split(r"(?im)^\s*GO\s*$", sql) if chunk.strip()]


def execute_schema(cursor, schema_sql: str):
    for batch in split_sqlserver_batches(schema_sql):
        cursor.execute(batch)


def seed_default_admin(cursor, fullname: str, email: str, password: str) -> bool:
    cursor.execute("SELECT COUNT(*) FROM users")
    user_count = int(cursor.fetchone()[0])
    if user_count > 0:
        return False

    password_hash = generate_password_hash(password)
    cursor.execute(
        "INSERT INTO users (fullname, email, password_hash, role) VALUES (?, ?, ?, ?)",
        (fullname.strip(), email.strip().lower(), password_hash, "admin"),
    )
    return True


def init_sqlserver_database(
    create_schema: bool = True,
    seed_admin: bool = False,
    admin_fullname: str = "System Admin",
    admin_email: str = "admin@example.com",
    admin_password: str = "Admin@123",
) -> None:
    project_root = Path(__file__).resolve().parents[1]
    load_env_file(project_root)

    conn_str = build_sqlserver_connection_string()
    conn = pyodbc.connect(conn_str)
    try:
        cursor = conn.cursor()

        if create_schema:
            schema_path = project_root / "database" / "create_db_sqlserver.sql"
            if not schema_path.exists():
                raise FileNotFoundError(f"Schema file not found: {schema_path}")
            schema_sql = schema_path.read_text(encoding="utf-8")
            execute_schema(cursor, schema_sql)
            conn.commit()

            # Ensure we are connected to the target DB after CREATE DATABASE batch.
            db_name = (os.environ.get("SQLSERVER_DATABASE", "PrescriptionOCR") or "PrescriptionOCR").strip()
            cursor.execute(f"USE [{db_name}]")

        admin_seeded = False
        if seed_admin:
            admin_seeded = seed_default_admin(
                cursor,
                fullname=admin_fullname,
                email=admin_email,
                password=admin_password,
            )
            conn.commit()
    finally:
        conn.close()

    print("SQL Server connection: OK")
    print("Database schema: initialized" if create_schema else "Database schema: skipped")
    if seed_admin:
        if admin_seeded:
            print(f"Default admin created: {admin_email}")
        else:
            print("Default admin skipped: users table already has data")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize SQL Server DB from create_db_sqlserver.sql")
    parser.add_argument(
        "--skip-schema",
        action="store_true",
        help="Skip schema execution and only test connection/optional seeding",
    )
    parser.add_argument(
        "--seed-admin",
        action="store_true",
        help="Create default admin account if users table is empty",
    )
    parser.add_argument("--admin-fullname", default="System Admin")
    parser.add_argument("--admin-email", default="admin@example.com")
    parser.add_argument("--admin-password", default="Admin@123")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    init_sqlserver_database(
        create_schema=not args.skip_schema,
        seed_admin=args.seed_admin,
        admin_fullname=args.admin_fullname,
        admin_email=args.admin_email,
        admin_password=args.admin_password,
    )


if __name__ == "__main__":
    main()
