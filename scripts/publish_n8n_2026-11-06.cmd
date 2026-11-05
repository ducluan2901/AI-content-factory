@echo off
rem One-shot: after the YouTube upload quota resets (15:00 VN, 2026-11-06), send tim cho -> 11/11 19:30.
cd /d "D:\NDL\AI Content Factory\AI-content-factory"
set PYTHONUTF8=1
set LOG=storage\n8n_task_2026-11-06.log
echo === %date% %time% >> %LOG%
.venv\Scripts\python.exe ..\wt-n8n\scripts\publish_n8n.py --job job_01a1a959e25f66295fbb --at 19:30 --days-ahead 5 >> %LOG% 2>&1
rem cho di vang -> 8/11
.venv\Scripts\python.exe ..\wt-n8n\scripts\publish_n8n.py --job job_01a1a046d2579e716549 --at 19:30 --days-ahead 2 >> %LOG% 2>&1
echo === done %time% >> %LOG%
rem English channel: the 2 uploads left in today's shared quota go to 10/11 and 11/11; the rest fail harmlessly and go on 7/11
call scripts\publish_n8n_en_week2.cmd 6
