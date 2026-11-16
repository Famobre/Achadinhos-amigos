@echo off
chcp 65001 >nul
title Achadinhos - instalando
cd /d "%~dp0"

set "PYLOCAL=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
set "PY="

if exist ".venv\Scripts\pythonw.exe" goto :abrir

REM ---- acha o Python ----
if exist "%PYLOCAL%" set "PY=%PYLOCAL%"
if not defined PY (py -3 --version >nul 2>&1 && set "PY=py -3")
if not defined PY (python -c "import sys" >nul 2>&1 && set "PY=python")

REM ---- se nao tiver, baixa e instala sozinho (python.org) ----
if not defined PY (
  echo.
  echo  Python nao encontrado. Baixando do python.org e instalando sozinho...
  echo  ^(isso leva uns 2 minutos, nao feche esta janela^)
  echo.
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile \"$env:TEMP\python-instalador.exe\""
  if not exist "%TEMP%\python-instalador.exe" (
    echo  Nao consegui baixar o Python. Confira sua internet e tente de novo.
    pause & exit /b
  )
  "%TEMP%\python-instalador.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0
  del "%TEMP%\python-instalador.exe" >nul 2>&1
  if exist "%PYLOCAL%" (set "PY=%PYLOCAL%") else (
    echo  A instalacao do Python falhou. Tire print e mande pro Claude.
    pause & exit /b
  )
  echo  Python instalado!
)

REM ---- cria o ambiente e instala as dependencias ----
echo  Instalando as bibliotecas do launcher...
%PY% -m venv .venv
".venv\Scripts\python.exe" -m pip install -q --upgrade pip
".venv\Scripts\python.exe" -m pip install -q -r requirements_pc.txt
if errorlevel 1 (
  echo  Erro ao instalar as bibliotecas. Tire print e mande pro Claude.
  pause & exit /b
)

:abrir
REM instala o que faltar (versoes novas do painel podem precisar de bibliotecas novas)
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements_pc.txt >nul 2>&1
if not exist ".env" copy ".env.example" ".env" >nul
start "" ".venv\Scripts\pythonw.exe" launcher.py
exit /b
