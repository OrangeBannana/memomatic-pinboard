# run-local.ps1 — start Memomatic Pinboard in WSL2 from Windows PowerShell.
#
# Usage:  Right-click → "Run with PowerShell"
#         Or from a PowerShell window: .\run-local.ps1
#         Then open http://127.0.0.1:8080/admin  (token: dev)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

# Convert the Windows path to a WSL /mnt/... path
$wslDir = (& wsl wslpath -u "$scriptDir") -replace "`r`n","" -replace "`n",""

if ([string]::IsNullOrEmpty($wslDir)) {
    Write-Host "ERROR: Could not convert path to WSL format. Is WSL2 installed?" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "Repo: $wslDir" -ForegroundColor Cyan
& wsl bash "$wslDir/run-local.sh"
Read-Host "Press Enter to exit"
