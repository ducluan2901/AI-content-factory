# Operator console on http://127.0.0.1:8501 (needs the API from run_api.ps1).
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONUTF8 = '1'
# Streamlit asks for a marketing email on first run; an empty credentials file skips that prompt
$cred = Join-Path $HOME '.streamlit\credentials.toml'
if (-not (Test-Path $cred)) { New-Item -ItemType Directory -Force (Split-Path $cred) | Out-Null; Set-Content $cred "[general]`nemail = `"`"" -Encoding ascii }
& .\.venv\Scripts\python.exe -m streamlit run ui\Home.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
