# AI Guardian Setup (Windows)

## 1. What this does
- Monitors your web endpoints continuously.
- Restarts Flask app automatically when health checks fail.
- Writes incident reports and AI repair suggestions to `ops/autofix_artifacts`.

## 2. Quick start
1. Open PowerShell in project root.
2. Activate your venv.
3. Set API key (optional but recommended for AI advice):

```powershell
$env:OPENAI_API_KEY="your_api_key_here"
```

4. Start guardian:

```powershell
powershell -ExecutionPolicy Bypass -File ops/run_guardian.ps1
```

## 3. Output files
- Runtime log: `ops/autofix_artifacts/guardian.log`
- Incident JSON: `ops/autofix_artifacts/incident_YYYYMMDD_HHMMSS.json`
- AI advice: `ops/autofix_artifacts/incident_YYYYMMDD_HHMMSS_ai_advice.md`

## 4. Important note
- This guardian auto-restarts the app.
- AI advice is generated continuously, but code changes are not auto-applied.
- This is safer for production and avoids accidental destructive edits.

## 5. Auto-start with Task Scheduler (optional)
1. Open Task Scheduler -> Create Task.
2. Trigger: At log on.
3. Action: Start a program.
4. Program/script:

```text
powershell.exe
```

5. Add arguments:

```text
-ExecutionPolicy Bypass -File "C:\Users\ToanLe\OneDrive\Pictures\Ảnh Kỉ Niệm\prescription_ocr_project\ops\run_guardian.ps1"
```

6. Start in:

```text
C:\Users\ToanLe\OneDrive\Pictures\Ảnh Kỉ Niệm\prescription_ocr_project
```
