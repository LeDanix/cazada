@echo off
cd /d "%~dp0"
python scripts\run_daily_scan.py --incremental --quiet >> data\cron_scan.log 2>&1
