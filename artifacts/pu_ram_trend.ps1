for ($i = 0; $i -lt 6; $i++) {
  $os = Get-CimInstance Win32_OperatingSystem
  "{0:H:mm:ss} free={1:N2}GB" -f (Get-Date), ($os.FreePhysicalMemory/1MB)
  if ($i -lt 5) { Start-Sleep -Seconds 20 }
}
