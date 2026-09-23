param([int]$Port = 8001)
$ErrorActionPreference = 'Stop'
$repoDirectory = Split-Path -Parent $PSScriptRoot
$pythonExecutable = Join-Path $repoDirectory '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    throw 'Create .venv and install backend/requirements-ayko.txt first.'
}
$hrSecurePassword = Read-Host 'Choose HR password for this server session' -AsSecureString
$previousHrPassword = $env:HR_PASSWORD
try {
    $env:HR_PASSWORD = [System.Net.NetworkCredential]::new('', $hrSecurePassword).Password
    if ([string]::IsNullOrWhiteSpace($env:HR_PASSWORD)) {
        throw 'HR password must not be empty.'
    }
    & $pythonExecutable -m uvicorn app.ayko_main:app --app-dir $PSScriptRoot --port $Port --reload --reload-dir $PSScriptRoot
}
finally {
    $env:HR_PASSWORD = $previousHrPassword
    $hrSecurePassword.Dispose()
}
