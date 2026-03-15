# OTP Email Setup

## Required environment variables

Set these variables before running the Flask app:

```powershell
$env:SMTP_HOST="smtp.gmail.com"
$env:SMTP_PORT="587"
$env:SMTP_USERNAME="your_email@gmail.com"
$env:SMTP_PASSWORD="your_app_password"
$env:SMTP_FROM="your_email@gmail.com"
```

## Recommended: dedicated mailbox for website

Use a separate account for system email, for example:

- trolyyteai.yourproject@gmail.com

Then configure:

```powershell
$env:SMTP_USERNAME="trolyyteai.yourproject@gmail.com"
$env:SMTP_FROM="trolyyteai.yourproject@gmail.com"
```

Benefits:

- Brand consistency in sender name
- Easy account ownership transfer for team members
- Better security isolation from personal mailbox

## Notes for Gmail

- Use an App Password (not your normal account password).
- Turn on 2-Step Verification in Google account first, then create App Password.
- Update profile picture of the dedicated Gmail account to your brand logo.

## Website brand logo image

Project pages are configured to load logo image from:

- /frontend/brand/logo-ai-engine.png

Steps:

1. Save your logo image to frontend/brand/logo-ai-engine.png
2. Restart app
3. Refresh browser (Ctrl+F5)

If the file is missing, system will fallback to the default icon automatically.

## Run app

```powershell
python backend/app.py
```

If SMTP is not configured correctly, the API `/api/forgot-password` will return an error message describing the issue.
