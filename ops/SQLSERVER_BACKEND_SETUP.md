# SQL Server Backend Setup

## 1) Create DB schema in SSMS

Run file:

`database/create_db_sqlserver.sql`

This creates database `PrescriptionOCR` and all tables used by backend.

Hoac chay tu command line (tu dong thuc thi file schema):

`python ops/init_db_sqlserver.py`

Neu dung PowerShell:

`./ops/init_db_sqlserver.ps1`

## 2) Configure `.env`

Set:

DB_BACKEND=sqlserver

Goi y nhanh:

`copy .env.sqlserver.example .env`

Then choose one method:

### Method A: Single connection string

SQLSERVER_CONNECTION_STRING=DRIVER={ODBC Driver 17 for SQL Server};SERVER=localhost;DATABASE=PrescriptionOCR;Trusted_Connection=yes;TrustServerCertificate=yes

### Method B: Split settings

SQLSERVER_DRIVER=ODBC Driver 17 for SQL Server
SQLSERVER_SERVER=localhost
SQLSERVER_DATABASE=PrescriptionOCR
SQLSERVER_TRUSTED_CONNECTION=yes

or SQL login:

SQLSERVER_TRUSTED_CONNECTION=no
SQLSERVER_UID=sa
SQLSERVER_PWD=your_password

## 3) Run backend

python backend/app.py

Co the seed admin mac dinh (chi khi bang users rong):

`python ops/init_db_sqlserver.py --seed-admin --admin-email admin@example.com --admin-password Admin@123`

## 4) Verify

- Register a new account in web UI.
- Check table `users` in SSMS to confirm data is saved there.
- Kiem tra API tinh trang DB: `GET /api/db/status`

## 5) Seed du lieu mau (giong bang Users ban chup)

Neu muon bang `users` co nhieu ban ghi de test nhanh:

`python ops/seed_sqlserver_users.py --count 12`

Hoac PowerShell:

`./ops/seed_sqlserver_users.ps1 -Count 12`

Script se tu dong map theo cot dang co trong bang `users` (hoac `Users`) va bo qua email da ton tai.
