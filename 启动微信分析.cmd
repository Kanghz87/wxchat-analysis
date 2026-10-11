@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 未找到项目的 Python 环境，请先按 README 完成安装：
  echo py -3.12 -m venv .venv
  echo .venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -X utf8 "web_app.py" --open-browser %*
set "launch_result=%errorlevel%"
if not "%launch_result%"=="0" (
  echo 启动失败，请查看上方错误。缺少依赖时，按 README 安装 requirements.txt。
  pause
)
exit /b %launch_result%
