@echo off
REM Gera o Achadinhos.exe (fica na pasta dist\Achadinhos). Rode o Iniciar.bat pelo menos uma vez antes.
cd /d "%~dp0"
.venv\Scripts\pip install -q pyinstaller
.venv\Scripts\pyinstaller --noconfirm --windowed --name Achadinhos --add-data "ui;ui" launcher.py
copy .env.example dist\Achadinhos\ >nul
REM o .env NAO vai junto: tem as suas chaves (cada um preenche as proprias na aba Contas)
echo.
echo Pronto! Abra dist\Achadinhos\Achadinhos.exe
pause
