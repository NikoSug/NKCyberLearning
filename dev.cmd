@echo off
rem Lanceur : .\dev <commande>  (contourne la politique d'exécution PowerShell pour ce seul script)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\dev.ps1" %*
