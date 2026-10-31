# API + in-process worker on http://127.0.0.1:8000 (docs at /docs). Localhost only: there is no auth.
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONUTF8 = '1'  # the Windows console default would mangle Vietnamese diacritics
& .\.venv\Scripts\python.exe -m uvicorn --factory app.main:app --host 127.0.0.1 --port 8000
