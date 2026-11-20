@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
where uv >nul 2>nul || (echo uv est introuvable : installez-le d'abord ^(voir docs\MISE_EN_ROUTE.md^). & pause & exit /b 1)
title TCF SO - Debloquer une connexion
rem Efface le compteur d'echecs de connexion d'un compte (5 echecs en 5 minutes bloquent le compte).
echo.
echo Debloquer la connexion d'un compte.
choice /c DR /m "D = site de demonstration (demo.cmd), R = site reel (demarrer.cmd)"
if errorlevel 2 goto :reel
rem Demonstration : variables valables pour cette seule fenetre, comme demo.cmd.
set "DATABASE_URL=sqlite:///data/demo.sqlite3"
set "MEDIA_ROOT=data/demo-media"
set "TTS_PROVIDER=fake"
set "LLM_PROVIDER=fake"
echo Site de demonstration (data\demo.sqlite3).
goto :demander
:reel
set "DATABASE_URL="
set "MEDIA_ROOT="
echo Site reel (data\db.sqlite3).
:demander
set "EMAIL="
set /p "EMAIL=Adresse e-mail du compte (Entree seule = debloquer tout le monde) : "
if "%EMAIL%"=="" (
    uv run python manage.py unlock_login --all || goto :erreur
) else (
    uv run python manage.py unlock_login "%EMAIL%" || goto :erreur
)
goto :fin

:erreur
echo.
echo Rien n'a ete debloque : lisez le message ci-dessus.

:fin
pause
