# Restart the dev server. Stops the WHOLE process tree on port 8000: stopping only the
# port owner (the uvicorn reloader) leaves its worker alive and still serving old code.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Stop-Tree($id) {
  Get-CimInstance Win32_Process -Filter "ParentProcessId=$id" | ForEach-Object { Stop-Tree $_.ProcessId }
  Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
}

Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Tree $_ }
# workers orphaned by an earlier restart still hold the socket but are not listed as its owner
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like "*multiprocessing-fork*" -and -not (Get-Process -Id $_.ParentProcessId -ErrorAction SilentlyContinue) } |
  ForEach-Object { Stop-Tree $_.ProcessId }
Start-Sleep -Seconds 1

if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
  throw "Port 8000 is still in use after stopping the server."
}

$started = Get-Date
Start-Process -FilePath ".\.venv\Scripts\python.exe" -ArgumentList "-m","uvicorn","agent_team.main:app","--reload" `
  -WorkingDirectory $PSScriptRoot -RedirectStandardError "uvicorn.err.log" -RedirectStandardOutput "uvicorn.out.log" -WindowStyle Hidden

for ($i = 0; $i -lt 30; $i++) {
  Start-Sleep -Seconds 1
  try { if ((Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health -TimeoutSec 2).StatusCode -eq 200) { break } } catch {}
}

# Every listener, and every child of a listener, must have been created by this restart.
$stale = @()
Get-NetTCPConnection -LocalPort 8000 -State Listen | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
  $procs = @(Get-CimInstance Win32_Process -Filter "ProcessId=$_") + @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$_")
  foreach ($p in $procs) {
    $state = if ($p.CreationDate -ge $started.AddSeconds(-2)) { "new" } else { $stale += $p.ProcessId; "STALE" }
    "{0,-6} pid={1,-6} created={2:HH:mm:ss}" -f $state, $p.ProcessId, $p.CreationDate
  }
}
if ($stale) { throw "Stale processes still serving port 8000: $($stale -join ', ')" }
"Server restarted at {0:HH:mm:ss}" -f $started
