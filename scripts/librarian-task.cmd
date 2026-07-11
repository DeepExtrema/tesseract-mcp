@echo off
rem Daily tesseract Librarian sweep (created 2026-07-11, M0 ops hardening).
rem Remove with: schtasks /delete /tn tesseract-librarian /f
rem codex preferred once quota returns (2026-08-10); claude until then.
set TESSERACT_EXTRACTOR=claude
if not exist "%USERPROFILE%\.tesseract-mcp" mkdir "%USERPROFILE%\.tesseract-mcp"
"C:\Users\Taimoor\Documents\GitHub\tesseract-mcp\.venv\Scripts\python.exe" -m tesseract_mcp.librarian "C:\Vaults\Tesseract" >> "%USERPROFILE%\.tesseract-mcp\librarian-task.log" 2>&1
