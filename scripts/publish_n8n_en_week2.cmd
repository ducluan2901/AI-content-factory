@echo off
rem English week 2 via the n8n EN workflow (webhook in Channel.handles_json). Run after 15:00 VN once the workflow is Active.
rem days-ahead counts from the run date: set RUN_DAY below to the day you run it (5 = run on 5/11, 6 = run on 6/11 ...).
cd /d "D:\NDL\AI Content Factory\AI-content-factory"
set PYTHONUTF8=1
set /a D10=10-%1, D11=11-%1, D12=12-%1, D13=13-%1, D14=14-%1, D15=15-%1
set LOG=storage\n8n_en_week2.log
echo === %date% %time% run-day %1 >> %LOG%
.venv\Scripts\python.exe ..\wt-n8n\scripts\publish_n8n.py --job job_01a1aa014ccd50b2bbed --at 07:30 --days-ahead %D10% >> %LOG% 2>&1
.venv\Scripts\python.exe ..\wt-n8n\scripts\publish_n8n.py --job job_01a1aa014ddaacaf8eae --at 07:30 --days-ahead %D11% >> %LOG% 2>&1
.venv\Scripts\python.exe ..\wt-n8n\scripts\publish_n8n.py --job job_01a1aa014e832aa896d3 --at 07:30 --days-ahead %D12% >> %LOG% 2>&1
.venv\Scripts\python.exe ..\wt-n8n\scripts\publish_n8n.py --job job_01a1aa014f3f389dbc75 --at 07:30 --days-ahead %D13% >> %LOG% 2>&1
.venv\Scripts\python.exe ..\wt-n8n\scripts\publish_n8n.py --job job_01a1aa01515e263e64d7 --at 07:30 --days-ahead %D14% >> %LOG% 2>&1
.venv\Scripts\python.exe ..\wt-n8n\scripts\publish_n8n.py --job job_01a1aa015222f4691cdd --at 07:30 --days-ahead %D15% >> %LOG% 2>&1
type %LOG%
