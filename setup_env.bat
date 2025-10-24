@echo off
echo === Setting up Python environment ===
where python >nul 2>nul || (
  echo Python not found! Please install Python 3.10+ from python.org and check "Add to PATH".
  pause
  exit /b
)
python -m venv venv
call venv\Scripts\activate
python -m ensurepip
python -m pip install --upgrade pip
python -m pip install flask gspread oauth2client requests
echo === Setup complete! ===
pause
