# Restart the API through its logon task (AIContentFactoryAPI). Refuses while a job is QUEUED or RUNNING,
# because a restart kills that job mid-stage; pass -Force to restart anyway.
# Stop-ScheduledTask alone leaves the uvicorn child alive on port 8000, so the processes are killed by name.
param([switch]$Force)
try {
    $busy = (Invoke-RestMethod http://127.0.0.1:8000/jobs -TimeoutSec 5) | Where-Object { $_.status -in 'QUEUED', 'RUNNING' }
    if ($busy -and -not $Force) { Write-Host "Not restarting: job(s) in progress: $($busy.id -join ', ') (use -Force)"; exit 1 }
} catch { }  # API down: nothing to protect
Stop-ScheduledTask -TaskName AIContentFactoryAPI -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'uvicorn --factory app\.main|run_api\.ps1' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 2
Start-ScheduledTask -TaskName AIContentFactoryAPI
foreach ($i in 1..40) {
    Start-Sleep 1
    try { Invoke-RestMethod http://127.0.0.1:8000/jobs -TimeoutSec 2 | Out-Null; Write-Host "API up after ${i}s"; exit 0 } catch { }
}
Write-Host "API did not answer within 40 s: check the AIContentFactoryAPI window"; exit 1
