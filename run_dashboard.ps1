$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $project '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $venvPython)) {
    py -3 -m venv (Join-Path $project '.venv')
}

& $venvPython -m pip install -r (Join-Path $project 'requirements.txt')
if ($LASTEXITCODE -ne 0) {
    throw 'Could not install dashboard dependencies.'
}

& $venvPython (Join-Path $project 'dashboard.py')
