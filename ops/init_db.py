import argparse
import sqlite3
from pathlib import Path
from werkzeug.security import generate_password_hash


def seed_default_admin(conn: sqlite3.Connection, fullname: str, email: str, password: str) -> bool:
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    user_count = int(cursor.fetchone()[0])
    if user_count > 0:
        return False

    password_hash = generate_password_hash(password)
    cursor.execute(
        "INSERT INTO users (fullname, email, password_hash, role) VALUES (?, ?, ?, ?)",
        (fullname.strip(), email.strip().lower(), password_hash, "admin"),
    )
    conn.commit()
    return True


def init_database(
    reset: bool = False,
    seed_admin: bool = False,
    admin_fullname: str = "System Admin",
    admin_email: str = "admin@example.com",
    admin_password: str = "Admin@123",
) -> None:
    project_root = Path(__file__).resolve().parents[1]
    db_path = project_root / "database" / "app.db"
    schema_path = project_root / "database" / "create_db.sql"

    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")

    db_path.parent.mkdir(parents=True, exist_ok=True)

    if reset and db_path.exists():
        db_path.unlink()

    schema_sql = schema_path.read_text(encoding="utf-8")

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(schema_sql)
        conn.commit()

        admin_seeded = False
        if seed_admin:
            admin_seeded = seed_default_admin(
                conn,
                fullname=admin_fullname,
                email=admin_email,
                password=admin_password,
            )
    finally:
        conn.close()

    print(f"Database initialized successfully: {db_path}")
    if reset:
        print("Mode: reset (old database was removed before initialization)")
    if seed_admin:
        if admin_seeded:
            print(f"Default admin created: {admin_email}")
        else:
            print("Default admin skipped: users table already has data")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize SQLite DB from create_db.sql")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete existing database before initialization",
    )
    parser.add_argument(
        "--seed-admin",
        action="store_true",
        help="Create a default admin account if users table is empty",
    )
    parser.add_argument(
        "--admin-fullname",
        default="System Admin",
        help="Default admin full name used with --seed-admin",
    )
    parser.add_argument(
        "--admin-email",
        default="admin@example.com",
        help="Default admin email used with --seed-admin",
    )
    parser.add_argument(
        "--admin-password",
        default="Admin@123",
        help="Default admin password used with --seed-admin",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    init_database(
        reset=args.reset,
        seed_admin=args.seed_admin,
        admin_fullname=args.admin_fullname,
        admin_email=args.admin_email,
        admin_password=args.admin_password,
    )


if __name__ == "__main__":
    main()
