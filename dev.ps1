<#
.SYNOPSIS
  Runs the whole WeatherGPT stack locally - FastAPI backend + Vite frontend.

.DESCRIPTION
  Opens two windows: the backend on http://localhost:8000 and the Vite dev
  server on http://localhost:5173. The dev server proxies /query, /health and
  /voice/* through to the backend (see frontend/vite.config.ts), so the UI and
  the API share an origin exactly like they do in production.

  Open http://localhost:5173 - that is the app, with hot reload.

  Requires no Docker and no Postgres: DATABASE_URL defaults to the local
  SQLite file backend/dev.db. Set DATABASE_URL yourself beforehand to point at
  a real Postgres instead. API keys are read from .env in this folder.

.EXAMPLE
  .\dev.ps1
#>
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Host 'No .venv found. Create one first:' -ForegroundColor Yellow
    Write-Host '  python -m venv .venv'
    Write-Host '  .\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt'
    exit 1
}

if (-not (Test-Path (Join-Path $root 'frontend\node_modules'))) {
    Write-Host 'Installing frontend dependencies...' -ForegroundColor Cyan
    Push-Location (Join-Path $root 'frontend')
    npm install
    Pop-Location
}

# Local runs default to SQLite; .env's DATABASE_URL points at the Docker
# Compose Postgres service, which is not reachable outside the compose network.
if (-not $env:DATABASE_URL) { $env:DATABASE_URL = 'sqlite:///dev.db' }

Write-Host "Backend  -> http://localhost:8000  (DATABASE_URL=$env:DATABASE_URL)" -ForegroundColor Green
Start-Process powershell -WorkingDirectory (Join-Path $root 'backend') -ArgumentList @(
    '-NoExit', '-Command',
    "`$env:DATABASE_URL = '$env:DATABASE_URL'; & '$python' -m uvicorn main:app --reload --port 8000"
)

Write-Host 'Frontend -> http://localhost:5173  (open this one)' -ForegroundColor Green
Start-Process powershell -WorkingDirectory (Join-Path $root 'frontend') -ArgumentList @(
    '-NoExit', '-Command',
    'npm run dev'
)
