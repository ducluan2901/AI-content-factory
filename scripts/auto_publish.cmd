@echo off
rem Daily YouTube publisher (Windows Task Scheduler, after the 15:00 VN quota reset). Uploads only rows of storage\publish_queue.csv.
cd /d "%~dp0.."
set PYTHONUTF8=1
.venv\Scripts\python.exe scriptsuto_publish.py >> storageuto_publish.log 2>&1
