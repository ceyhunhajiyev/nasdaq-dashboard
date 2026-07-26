Set-Location -Path $PSScriptRoot
Start-Process 'http://localhost:8000'
& "$PSScriptRoot\venv\Scripts\python.exe" -m uvicorn app.main:app --reload
