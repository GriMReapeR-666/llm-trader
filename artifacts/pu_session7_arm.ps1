$repo = (Get-Location).Path
$w = Start-Process -FilePath "python" `
  -ArgumentList "-X","utf8","artifacts/pu_session7_boot.py" `
  -WorkingDirectory $repo -WindowStyle Hidden -PassThru `
  -RedirectStandardOutput "artifacts/pu_session7_boot_stdout.txt" `
  -RedirectStandardError "artifacts/pu_session7_boot_stderr.txt"
"watcher pid=$($w.Id)"
$v = Start-Process -FilePath "python" `
  -ArgumentList "-X","utf8","artifacts/pu_session7_waiter.py" `
  -WorkingDirectory $repo -WindowStyle Hidden -PassThru `
  -RedirectStandardOutput "artifacts/pu_session7_waiter_stdout.txt" `
  -RedirectStandardError "artifacts/pu_session7_waiter_stderr.txt"
"waiter pid=$($v.Id)"
