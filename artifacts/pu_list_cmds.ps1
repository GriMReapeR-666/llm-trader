Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" | ForEach-Object {
    $cmd = $_.CommandLine
    if ($null -eq $cmd) { $cmd = "<null>" }
    $snippet = $cmd.Substring(0, [Math]::Min(110, $cmd.Length))
    Write-Output ("PID=" + $_.ProcessId + " created=" + $_.CreationDate.ToString("HH:mm:ss") + " : " + $snippet)
}
