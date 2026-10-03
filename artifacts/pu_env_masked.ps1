Get-Content .env | ForEach-Object {
  if ($_ -match '^(export )?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
    $name = $matches[2]
    $val  = $matches[3].Trim()
    if ($val.Length -ge 10) {
      $masked = $val.Substring(0,4) + '...' + $val.Substring($val.Length-4) + ' (len=' + $val.Length + ')'
    } elseif ($val.Length -gt 0) {
      $masked = '*** (len=' + $val.Length + ')'
    } else {
      $masked = '(EMPTY)'
    }
    "{0} = {1}" -f $name, $masked
  } else {
    $_
  }
}
