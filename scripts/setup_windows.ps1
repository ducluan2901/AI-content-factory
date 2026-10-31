# One-time setup for Windows PowerShell 5.1+. Run from the repo root:  .\scripts\setup_windows.ps1
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

# Python: prefer the py launcher (the bare `python` may be the Microsoft Store stub)
$ErrorActionPreference = 'Continue'  # PS 5.1 turns native stderr into fatal errors under 'Stop'
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not (Test-Path .venv)) {
    if ($uv) { uv venv --python 3.13 .venv } else { py -3.13 -m venv .venv }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv: install Python 3.12+ from python.org (see README)' }
}
if ($uv) { uv pip install -q --python .\.venv\Scripts\python.exe -e ".[dev]" }
else { & .\.venv\Scripts\python.exe -m pip install -q -e ".[dev]" }
if ($LASTEXITCODE -ne 0) { throw 'Dependency install failed' }

# FFmpeg: find it on PATH or in the winget package folder
$ffmpeg = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
if (-not $ffmpeg) {
    $ffmpeg = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $ffmpeg) {
    Write-Host 'FFmpeg not found. Installing with winget...'
    winget install --id Gyan.FFmpeg --source winget --accept-package-agreements --accept-source-agreements
    $ffmpeg = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter ffmpeg.exe | Select-Object -First 1 -ExpandProperty FullName
}
$ffprobe = Join-Path (Split-Path $ffmpeg) 'ffprobe.exe'

if (-not (Test-Path .env)) {
    (Get-Content .env.example -Encoding UTF8) `
        -replace '^FFMPEG_PATH=.*', "FFMPEG_PATH=$ffmpeg" `
        -replace '^FFPROBE_PATH=.*', "FFPROBE_PATH=$ffprobe" |
        Set-Content .env -Encoding UTF8
    Write-Host 'Created .env'
}
Write-Host "FFmpeg: $ffmpeg"
Write-Host 'Done. Next: .\scripts\run_api.ps1 and, in a second terminal, .\scripts\run_ui.ps1'
