@echo off
REM ============================================================================
REM  Blackfire-Session-Cookie aus der Zwischenablage speichern + pruefen.
REM
REM  1) In Chrome bei www.blackfire.eu EINLOGGEN.
REM  2) DevTools (F12) -> Network -> Seite neu laden -> Request auf
REM     www.blackfire.eu anklicken -> Rechtsklick -> Copy -> "Copy as cURL (bash)".
REM  3) Diese Datei doppelklicken (oder ausfuehren). Der Cookie wird aus der
REM     Zwischenablage gelesen, in blackfire_config.json gespeichert + geprueft.
REM ============================================================================
powershell -NoProfile -Command "Get-Clipboard" | python "%~dp0setcookie.py"
echo.
pause
