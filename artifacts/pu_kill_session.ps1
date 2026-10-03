Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
    $_.CommandLine -match 'mt5_trader\.py llm-trade' -or $_.CommandLine -match 'timeout 1440'
} | ForEach-Object {
    $cmd = $_.CommandLine
    if ($null -eq $cmd) { $cmd = '<null>' }
    $snippet = $cmd.Substring(0, [Math]::Min(100, $cmd.Length))
    Write-Output ("killing " + $_.ProcessId + " : " + $snippet)
    Stop-Process -Id $_.ProcessId -Force
}
Write-Output "remaining python processes:"
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ForEach-Object {
    $cmd = $_.CommandLine
    if ($null -eq $cmd) { $cmd = '<null>' }
    $snippet = $cmd.Substring(0, [Math]::Min(80, $cmd.Length))
    Write-Output ("  " + $_.ProcessId + " : " + $snippet)
}
