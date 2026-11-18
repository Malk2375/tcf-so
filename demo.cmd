@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
where uv >nul 2>nul || (echo uv est introuvable : installez-le d'abord ^(voir docs\MISE_EN_ROUTE.md^). & pause & exit /b 1)
title TCF SO - DEMONSTRATION (port 8001)
rem Site de demonstration : variables valables pour cette seule fenetre.
set "DATABASE_URL=sqlite:///data/demo.sqlite3"
set "MEDIA_ROOT=data/demo-media"
set "TTS_PROVIDER=fake"
set "LLM_PROVIDER=fake"
set "SITE_URL=http://localhost:8001"
if not exist "data\demo.sqlite3" goto :creer
echo.
echo Repartir d'une demonstration neuve (tests factices, compte admin@demo.test) ?
choice /c ON /t 10 /d N /m "O = oui, N = non (non par defaut dans 10 secondes)"
if errorlevel 2 goto :lancer
:creer
echo Creation de la base de demonstration...
uv run python manage.py reset_demo || goto :erreur
:lancer
uv run python manage.py migrate || goto :erreur
uv run python manage.py site_info --port 8001
start "" /min powershell -NoProfile -Command "Start-Sleep -Seconds 4; Start-Process 'http://localhost:8001'"
uv run python manage.py runserver 8001
goto :fin

:erreur
echo.
echo Une erreur est survenue : lisez le message ci-dessus.

:fin
pause
