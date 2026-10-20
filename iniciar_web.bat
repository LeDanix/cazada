@echo off
cd /d "%~dp0"
echo Iniciando servidor local ZgzHouseFinder en http://127.0.0.1:8765 ...
start "" "http://127.0.0.1:8765"
python server.py 8765
