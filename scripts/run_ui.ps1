# Operator console on http://127.0.0.1:8501 (needs the API from run_api.ps1).
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONUTF8 = '1'
& .\.venv\Scripts\python.exe -m streamlit run ui\Home.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
