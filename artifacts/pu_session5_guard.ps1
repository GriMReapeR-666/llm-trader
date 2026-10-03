$now = [DateTime]::UtcNow
"UTC now: {0:yyyy-MM-ddTHH:mm:ssZ}" -f $now
"os = Get-CimInstance Win32_OperatingSystem"
$os = Get-CimInstance Win32_OperatingSystem
"RAM: {0:N2} GB free / {1:N2} GB total" -f ($os.FreePhysicalMemory/1MB), ($os.TotalVisibleMemorySize/1MB)
""
"--- mt5_trader python processes (must be none) ---"
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'mt5_trader' }
if ($procs) { $procs | ForEach-Object { "PID {0}: {1}" -f $_.ProcessId, $_.CommandLine } } else { "none" }
