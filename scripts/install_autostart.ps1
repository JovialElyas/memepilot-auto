# MemePilot — démarrage automatique du bot papier à l'ouverture de ta session Windows (sans droits admin).
#   .\scripts\install_autostart.ps1            # installer
#   .\scripts\install_autostart.ps1 -Remove    # désinstaller
param([switch]$Remove)

$name = "MemePilot papier"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Démarrage automatique supprimé."
    exit 0
}
$root = Split-Path -Parent $PSScriptRoot
$script = Join-Path $root "scripts\run_forever.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$script`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "OK : MemePilot démarrera (fenêtre réduite) à chaque ouverture de session."
Write-Host "Interface : http://127.0.0.1:8765   ·   Désinstaller : .\scripts\install_autostart.ps1 -Remove"
