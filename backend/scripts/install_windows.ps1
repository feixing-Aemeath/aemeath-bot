# Native Windows 10/11. Run from PowerShell as the user who keeps QQ/WeChat logged in.
[CmdletBinding()]
param(
    [ValidateSet('install', 'uninstall', 'status')]
    [string]$Action = 'install',
    [string]$BackendDir = (Split-Path -Parent $PSScriptRoot),
    [switch]$Start
)
$ErrorActionPreference = 'Stop'
$taskName = 'local.assistant.windows'
$backendPath = (Resolve-Path -LiteralPath $BackendDir).Path
$pythonPath = Join-Path $backendPath '.venv\Scripts\python.exe'
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing -and $existing.Actions.WorkingDirectory -ne $backendPath) {
    throw 'This task name belongs to another checkout. Inspect it before replacing or removing it.'
}

if ($Action -eq 'status') {
    if (-not $existing) { Write-Output 'not_installed'; exit 1 }
    $existing | Select-Object TaskName, State
    Get-ScheduledTaskInfo -TaskName $taskName | Select-Object LastRunTime, LastTaskResult, NextRunTime
    exit 0
}
if ($Action -eq 'uninstall') {
    if ($existing) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
    Write-Output 'uninstalled; configuration, credentials and data kept'
    exit 0
}
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Run uv sync --locked --python 3.12 in backend first.'
}
Push-Location -LiteralPath $backendPath
try {
    & $pythonPath -X utf8 -m scripts.windows_service --check
    if ($LASTEXITCODE -ne 0) { throw 'Invalid backend configuration; task was not installed.' }
} finally { Pop-Location }

$userName = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$taskAction = New-ScheduledTaskAction -Execute $pythonPath -Argument '-X utf8 -m scripts.windows_service' -WorkingDirectory $backendPath
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userName
# InteractiveToken keeps capture in this user's desktop. No stored Windows password, SYSTEM
# account or elevated token; QQ/WeChat must run at the same integrity level.
$principal = New-ScheduledTaskPrincipal -UserId $userName -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $trigger -Principal $principal -Settings $settings -Description 'Aemeath backend in the logged-in desktop; local/Tailscale HTTP only' -Force | Out-Null
if ($Start) { Start-ScheduledTask -TaskName $taskName }
Write-Output 'installed: starts at logon, retries failures every minute; use -Start to run now'
