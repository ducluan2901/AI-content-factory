@echo off
rem One-shot: send the three week-1 videos to n8n after the YouTube upload quota resets (15:00 VN, 2026-11-05).
rem days-ahead counts from the run date (5/11): tai cho -> 6/11, luoi meo -> 7/11, rau meo -> 9/11, all at 19:30.
cd /d "D:\NDL\AI Content Factory\AI-content-factory"
set PYTHONUTF8=1
set LOG=storage\n8n_task_2026-11-05.log
echo === %date% %time% >> %LOG%
.venv\Scripts\python.exe ..\wt-n8n\scripts\publish_n8n.py --job job_01a1a552d4261352e9c5 --at 19:30 --days-ahead 1 >> %LOG% 2>&1
.venv\Scripts\python.exe ..\wt-n8n\scripts\publish_n8n.py --job job_01a1a5589e93c08407a2 --at 19:30 --days-ahead 2 >> %LOG% 2>&1
.venv\Scripts\python.exe ..\wt-n8n\scripts\publish_n8n.py --job job_01a1a5fb8bb1d6ba274d --at 19:30 --days-ahead 4 >> %LOG% 2>&1
rem ca vang -> 10/11 (4th and last upload of the day's quota)
.venv\Scripts\python.exe ..\wt-n8n\scripts\publish_n8n.py --job job_01a1a95b971bdb51ea24 --at 19:30 --days-ahead 5 >> %LOG% 2>&1
echo === done %time% >> %LOG%
