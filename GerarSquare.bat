@echo off
chcp 65001 >nul
title Achadinhos - gerando pacote da Square Cloud
cd /d "%~dp0"
echo.
echo  Montando o pacote pra Square Cloud...
if exist "_square" rmdir /s /q "_square"
mkdir "_square"
mkdir "_square\plataformas"
for %%F in (square.py comandos.py bot.py canais.py cards.py config.py db.py extrator.py ia.py ofertas.py selecao.py squarecloud.app config.json segredos.json .env dados.db) do (
  if exist "%%F" copy /y "%%F" "_square\" >nul
)
copy /y "plataformas\*.py" "_square\plataformas\" >nul
copy /y "requirements.txt" "_square\requirements.txt" >nul
if exist "achadinhos-square.zip" del "achadinhos-square.zip"
tar -a -c -f "achadinhos-square.zip" -C "_square" square.py comandos.py bot.py canais.py cards.py config.py db.py extrator.py ia.py ofertas.py selecao.py squarecloud.app requirements.txt config.json segredos.json .env dados.db plataformas
rmdir /s /q "_square"
echo.
echo  Pronto! Arquivo criado: achadinhos-square.zip
echo  Agora: squarecloud.app ^> Dashboard ^> Upload ^> escolha esse arquivo.
echo  DEPOIS que subir, feche o painel do PC (senao posta repetido).
echo.
pause
