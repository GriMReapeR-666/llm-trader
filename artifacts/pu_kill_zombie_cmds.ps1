$cutoff = (Get-Date).AddMinutes(-20)
$zombies = Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" | Where-Object {
    $_.CreationDate -gt $cutoff -and $_.CommandLine -match 'run\.bat'
}
foreach ($z in $zombies) {
    Write-Output ("killing zombie cmd " + $z.ProcessId + " : " + $z.CommandLine)
    Stop-Process -Id $z.ProcessId -Force -ErrorAction SilentlyContinue
}
Write-Output "remaining cmd.exe count:"
@(Get-CimInstance Win32_Process -Filter "Name='cmd.exe'").Count
