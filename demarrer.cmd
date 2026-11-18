@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
where uv >nul 2>nul || (echo uv est introuvable : installez-le d'abord ^(voir docs\MISE_EN_ROUTE.md^). & pause & exit /b 1)
title TCF SO - site reel (port 8000)
rem Site reel : base data\db.sqlite3 et fournisseurs de .env. On efface d'eventuelles variables
rem de demonstration heritees, pour que .env s'applique.
set "DATABASE_URL="
set "MEDIA_ROOT="
set "TTS_PROVIDER="
set "LLM_PROVIDER="
set "SITE_URL="
echo Mise a jour de la base...
uv run python manage.py migrate || goto :erreur
uv run python manage.py site_info --port 8000
start "" /min powershell -NoProfile -Command "Start-Sleep -Seconds 4; Start-Process 'http://localhost:8000'"
uv run python manage.py runserver 8000
goto :fin

:erreur
echo.
echo Une erreur est survenue : lisez le message ci-dessus.

:fin
pause
