@echo off
title Football.AI - Lancement
color 0A

echo.
echo  ==================================================
echo            FOOTBALL.AI - LANCEMENT
echo  ==================================================
echo.

python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo  [ERREUR] Python non detecte. Installe-le sur https://python.org
    echo  (coche bien "Add python.exe to PATH" pendant l'installation)
    pause
    exit /b 1
)

cd /d "%~dp0"

echo  [1/2] Installation des dependances...
python -m pip install -r requirements.txt --quiet
IF %ERRORLEVEL% NEQ 0 (
    echo  [ERREUR] Echec de l'installation des dependances.
    pause
    exit /b 1
)
echo        OK
echo.

echo  [2/2] Lancement du serveur...
echo  Le site va s'ouvrir sur http://localhost:5000
echo  (premiere collecte + calcul des predictions en arriere-plan, ca peut prendre 1-2 minutes)
echo.
start "" "http://localhost:5000"
python main.py

pause
