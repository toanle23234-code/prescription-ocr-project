# Database Init Usage

## One-command init (keep existing data)

Run from project root:

powershell -ExecutionPolicy Bypass -File ops/init_db.ps1

This creates `database/app.db` (if missing) and applies schema from `database/create_db.sql`.

## Init with default admin

powershell -ExecutionPolicy Bypass -File ops/init_db.ps1 -SeedAdmin -AdminFullname "System Admin" -AdminEmail "admin@example.com" -AdminPassword "Admin@123"

The default admin is created only when table `users` is empty.

## Reset database (delete old data and recreate)

powershell -ExecutionPolicy Bypass -File ops/init_db.ps1 -Reset

## Direct Python command

python ops/init_db.py

or

python ops/init_db.py --reset

## Backup database

powershell -ExecutionPolicy Bypass -File ops/db_backup.ps1

Backups are saved to `database/backups`.

## Restore database

powershell -ExecutionPolicy Bypass -File ops/db_restore.ps1 -BackupFile "database/backups/app_YYYYMMDD_HHMMSS.db"
