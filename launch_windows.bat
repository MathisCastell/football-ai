@echo off
chcp 65001 >nul
title Football.AI — Lancement
color 0A

echo.
echo  ╔══════════════════════════════════════════════╗
echo  ║         FOOTBALL.AI — LANCEMENT              ║
echo  ╚══════════════════════════════════════════════╝
echo.

python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo  [ERREUR] Python non detecte. Installe-le sur https://python.org
    pause
    exit /b 1
)

cd /d "%~dp0"

echo  [1/5] Installation des dependances...
pip install numpy scipy scikit-learn requests --quiet 2>nul
echo        OK
echo.

echo  [2/5] Collecte des donnees...
python 1_collect_data.py
IF %ERRORLEVEL% NEQ 0 ( echo  [ERREUR] Echec. & pause & exit /b 1 )
echo.

echo  [3/5] Calcul des predictions IA...
python 2_predict.py
IF %ERRORLEVEL% NEQ 0 ( echo  [ERREUR] Echec. & pause & exit /b 1 )
echo.

echo  [4/5] Export JSON...
python 3_export_json.py
IF %ERRORLEVEL% NEQ 0 ( echo  [ERREUR] Echec. & pause & exit /b 1 )
echo.

echo  [5/5] Ouverture du site...
start "" "index.html"
echo.
echo  Site ouvert dans ton navigateur !
echo  Relance ce fichier pour mettre a jour les donnees.
echo.
pause
