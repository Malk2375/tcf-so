@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
where uv >nul 2>nul || (echo uv est introuvable : installez-le d'abord ^(voir docs\MISE_EN_ROUTE.md^). & pause & exit /b 1)
title TCF SO - Importer les bruits de fond
rem Importe les bruits de fond de assets\sound d'apres assets\sound\ambiences.json (SPEC 7.5.4).
rem Les entrees dont la licence n'est pas CC0, CC-BY ou CC-BY-NC sont refusees ; relancer ne cree pas de doublon.
echo.
echo Importer les bruits de fond du dossier assets\sound.
choice /c DR /m "D = site de demonstration (demo.cmd), R = site reel (demarrer.cmd)"
if errorlevel 2 goto :reel
rem Demonstration : variables valables pour cette seule fenetre, comme demo.cmd.
set "DATABASE_URL=sqlite:///data/demo.sqlite3"
set "MEDIA_ROOT=data/demo-media"
set "TTS_PROVIDER=fake"
set "LLM_PROVIDER=fake"
echo Site de demonstration (data\demo.sqlite3).
if not exist "data\demo.sqlite3" uv run python manage.py reset_demo || goto :erreur
goto :importer
:reel
set "DATABASE_URL="
set "MEDIA_ROOT="
echo Site reel (data\db.sqlite3).
:importer
uv run python manage.py migrate || goto :erreur
uv run python manage.py import_ambiences_folder assets\sound || goto :erreur
echo.
echo Termine. Reglez les niveaux sur la page Back-office, Bruits de fond, avec le bouton Essayer.
goto :fin

:erreur
echo.
echo Import impossible : lisez le message ci-dessus.

:fin
pause
