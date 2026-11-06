# MemePilot — lance le bot PAPIER et le relance automatiquement s'il s'arrête sur une erreur.
# Usage (PowerShell, dans le dossier du projet) :   .\scripts\run_forever.ps1
# Arrêt : Ctrl+C (ou fermer la fenêtre). L'état est sauvegardé ; au redémarrage tout reprend.
param([string]$BotArgs = "--demo")

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { Write-Host "Environnement .venv introuvable dans $root" -ForegroundColor Red; exit 1 }

$restarts = 0
while ($true) {
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] démarrage de MemePilot (papier) $BotArgs" -ForegroundColor Green
    & $python scripts\paper_trade.py @($BotArgs -split ' ')
    $code = $LASTEXITCODE
    if ($code -eq 0) { Write-Host "Arrêt normal."; break }
    if ($code -eq 3) { Write-Host "Un autre MemePilot tourne déjà : pas de relance." -ForegroundColor Yellow; break }
    $restarts++
    $wait = [Math]::Min(300, 30 * $restarts)
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] arrêt inattendu (code $code), relance n°$restarts dans $wait s" -ForegroundColor Yellow
    Start-Sleep -Seconds $wait
}
