# Register Mon-Fri 15:40 and 16:30 closer jobs.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$cmd = Join-Path $root "sync_once.cmd"

function Register-Closer($name, $time) {
    schtasks.exe /Create /F /TN $name /TR "`"$cmd`"" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST $time /RL LIMITED | Out-Host
}

Register-Closer "GeorgeChin-Trade-1540" "15:40"
Register-Closer "GeorgeChin-Trade-1630" "16:30"
Write-Host "OK: weekday 15:40 and 16:30"
Write-Host "Remove: schtasks /Delete /TN GeorgeChin-Trade-1540 /F"
Write-Host "        schtasks /Delete /TN GeorgeChin-Trade-1630 /F"
