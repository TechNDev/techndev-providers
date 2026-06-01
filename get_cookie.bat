@echo off
rem ============================================================================
rem  cubegolem — Session-Cookie aus der Zwischenablage in die Config schreiben
rem ----------------------------------------------------------------------------
rem  SO GEHT'S:
rem   1) Im Browser bei cubegolem.de EINGELOGGT sein.
rem   2) DevTools (F12) -> Tab "Network" -> Seite neu laden (F5).
rem   3) Einen beliebigen cubegolem.de-Request anklicken -> Rechtsklick ->
rem      "Copy" -> "Copy as cURL (bash)".
rem   4) Diese Datei doppelklicken. Der Rest passiert automatisch
rem      (Cookie wird gespeichert + Session live geprueft).
rem ============================================================================
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

echo cubegolem - Cookie aus Zwischenablage uebernehmen
echo --------------------------------------------------
powershell -NoProfile -Command "Get-Clipboard -Raw" | python -m cubegolem.setcookie

echo.
pause
