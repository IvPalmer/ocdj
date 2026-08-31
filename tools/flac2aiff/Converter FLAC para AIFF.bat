@echo off
rem Arraste a pasta (ou os arquivos .flac) para cima deste arquivo,
rem ou apenas clique duas vezes e informe o caminho da pasta.
rem
rem A logica esta em flac2aiff.ps1 - PowerShell, porque nome de arquivo com
rem acento quebra em batch puro, e aqui isso e garantido.
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0flac2aiff.ps1" %*
