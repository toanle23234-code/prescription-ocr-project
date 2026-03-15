-- ============================================================
-- SQL Server schema for SSMS
-- Aligned with current Flask app (auth, profile, history, OTP)
-- ============================================================

IF DB_ID(N'PrescriptionOCR') IS NULL
BEGIN
    CREATE DATABASE PrescriptionOCR;
END;
GO

USE PrescriptionOCR;
GO

-- 1) Users
IF OBJECT_ID(N'dbo.users', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.users (
        id INT IDENTITY(1,1) PRIMARY KEY,
        fullname NVARCHAR(255) NOT NULL,
        email NVARCHAR(255) NOT NULL UNIQUE,
        password_hash NVARCHAR(255) NOT NULL,
        phone NVARCHAR(50) NULL,
        birth_date NVARCHAR(30) NULL,
        address NVARCHAR(255) NULL,
        bio NVARCHAR(MAX) NULL,
        avatar_url NVARCHAR(1000) NULL,
        role NVARCHAR(50) NOT NULL DEFAULT N'user',
        created_at DATETIME NOT NULL DEFAULT GETDATE()
    );
END;
GO

IF COL_LENGTH('dbo.users', 'avatar_url') IS NULL
BEGIN
    ALTER TABLE dbo.users ADD avatar_url NVARCHAR(1000) NULL;
END;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'idx_users_email' AND object_id = OBJECT_ID(N'dbo.users'))
BEGIN
    CREATE INDEX idx_users_email ON dbo.users(email);
END;
GO

-- 2) OCR Scan History
IF OBJECT_ID(N'dbo.scan_history', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.scan_history (
        id INT IDENTITY(1,1) PRIMARY KEY,
        user_id INT NOT NULL,
        filename NVARCHAR(255) NOT NULL,
        full_text NVARCHAR(MAX) NOT NULL,
        created_at DATETIME NOT NULL DEFAULT GETDATE(),
        CONSTRAINT FK_scan_history_users FOREIGN KEY (user_id) REFERENCES dbo.users(id)
    );
END;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'idx_scan_history_user_id' AND object_id = OBJECT_ID(N'dbo.scan_history'))
BEGIN
    CREATE INDEX idx_scan_history_user_id ON dbo.scan_history(user_id);
END;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'idx_scan_history_created_at' AND object_id = OBJECT_ID(N'dbo.scan_history'))
BEGIN
    CREATE INDEX idx_scan_history_created_at ON dbo.scan_history(created_at);
END;
GO

-- 3) OCR Error Logs
IF OBJECT_ID(N'dbo.scan_error_logs', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.scan_error_logs (
        id INT IDENTITY(1,1) PRIMARY KEY,
        user_id INT NOT NULL,
        filename NVARCHAR(255) NULL,
        error_message NVARCHAR(1000) NOT NULL,
        created_at DATETIME NOT NULL DEFAULT GETDATE(),
        CONSTRAINT FK_scan_error_logs_users FOREIGN KEY (user_id) REFERENCES dbo.users(id)
    );
END;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'idx_scan_error_logs_user_id' AND object_id = OBJECT_ID(N'dbo.scan_error_logs'))
BEGIN
    CREATE INDEX idx_scan_error_logs_user_id ON dbo.scan_error_logs(user_id);
END;
GO

-- 4) Password Reset Tokens
IF OBJECT_ID(N'dbo.password_reset_tokens', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.password_reset_tokens (
        id INT IDENTITY(1,1) PRIMARY KEY,
        user_id INT NOT NULL,
        token NVARCHAR(255) NOT NULL UNIQUE,
        expires_at DATETIME NOT NULL,
        used BIT NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL DEFAULT GETDATE(),
        CONSTRAINT FK_password_reset_tokens_users FOREIGN KEY (user_id) REFERENCES dbo.users(id)
    );
END;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'idx_password_reset_tokens_user_id' AND object_id = OBJECT_ID(N'dbo.password_reset_tokens'))
BEGIN
    CREATE INDEX idx_password_reset_tokens_user_id ON dbo.password_reset_tokens(user_id);
END;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'idx_password_reset_tokens_token' AND object_id = OBJECT_ID(N'dbo.password_reset_tokens'))
BEGIN
    CREATE INDEX idx_password_reset_tokens_token ON dbo.password_reset_tokens(token);
END;
GO

-- 5) Password Reset OTPs
IF OBJECT_ID(N'dbo.password_reset_otps', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.password_reset_otps (
        id INT IDENTITY(1,1) PRIMARY KEY,
        user_id INT NOT NULL,
        otp_code NVARCHAR(20) NOT NULL,
        expires_at DATETIME NOT NULL,
        used BIT NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL DEFAULT GETDATE(),
        CONSTRAINT FK_password_reset_otps_users FOREIGN KEY (user_id) REFERENCES dbo.users(id)
    );
END;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'idx_password_reset_otps_user_id' AND object_id = OBJECT_ID(N'dbo.password_reset_otps'))
BEGIN
    CREATE INDEX idx_password_reset_otps_user_id ON dbo.password_reset_otps(user_id);
END;
GO

-- 6) Registration OTPs
IF OBJECT_ID(N'dbo.registration_otps', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.registration_otps (
        id INT IDENTITY(1,1) PRIMARY KEY,
        fullname NVARCHAR(255) NOT NULL,
        email NVARCHAR(255) NOT NULL,
        password_hash NVARCHAR(255) NOT NULL,
        otp_code NVARCHAR(20) NOT NULL,
        expires_at DATETIME NOT NULL,
        used BIT NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL DEFAULT GETDATE()
    );
END;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'idx_registration_otps_email' AND object_id = OBJECT_ID(N'dbo.registration_otps'))
BEGIN
    CREATE INDEX idx_registration_otps_email ON dbo.registration_otps(email);
END;
GO
