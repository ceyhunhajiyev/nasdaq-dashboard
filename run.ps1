Set-Location -Path $PSScriptRoot
Start-Process powershell -WindowStyle Hidden -ArgumentList '-Command','Start-Sleep -Seconds 3; Start-Process "http://localhost:8000"'
& "$PSScriptRoot\venv\Scripts\python.exe" -m uvicorn app.main:app --reload
