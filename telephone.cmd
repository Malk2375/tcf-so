@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
where uv >nul 2>nul || (echo uv est introuvable : installez-le d'abord ^(voir docs\MISE_EN_ROUTE.md^). & pause & exit /b 1)
title TCF SO - DEMONSTRATION SUR TELEPHONE (tunnel HTTPS)
rem Site de demonstration joint depuis un telephone par un Cloudflare Quick Tunnel (sans compte).
set "DATABASE_URL=sqlite:///data/demo.sqlite3"
set "MEDIA_ROOT=data/demo-media"
set "TTS_PROVIDER=fake"
set "LLM_PROVIDER=fake"
set "PHONE_TUNNEL=1"
if exist "data\demo.sqlite3" goto :lancer
echo Creation de la base de demonstration...
uv run python manage.py reset_demo || goto :erreur
:lancer
uv run python manage.py migrate || goto :erreur
uv run python manage.py site_info --port 8001
uv run python scripts\phone.py || goto :erreur
goto :fin

:erreur
echo.
echo Une erreur est survenue : lisez le message ci-dessus.

:fin
pause
