# Two-day script experiment agent (app/experiment.py). Runs in its own window next to the API; shares the database,
# so the videos appear in the console as AWAITING_APPROVAL. Restart after a reboot: it resumes where it stopped.
# Usage: .\scripts\run_experiment.ps1 [experiment-id]   (default id: exp-YYYYMMDD; the same id resumes)
param([string]$Id = ("exp-" + (Get-Date -Format "yyyyMMdd")))
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONUTF8 = '1'
& .\.venv\Scripts\python.exe -m app.experiment $Id
