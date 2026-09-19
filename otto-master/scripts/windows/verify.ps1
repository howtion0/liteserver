[CmdletBinding()]
param([string]$OpusBin)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $ProjectRoot

if (-not [string]::IsNullOrWhiteSpace($OpusBin)) {
    $ResolvedOpus = (Resolve-Path $OpusBin).Path
    $env:PATH = "$ResolvedOpus;$env:PATH"
}

& uv lock --check
if ($LASTEXITCODE -ne 0) { throw "uv lock check failed." }
& uv run ruff check src tests scripts
if ($LASTEXITCODE -ne 0) { throw "Ruff failed." }
& uv run mypy src
if ($LASTEXITCODE -ne 0) { throw "mypy failed." }
& uv run pytest -q
if ($LASTEXITCODE -ne 0) { throw "pytest failed." }
& uv run python tests/packaging/static_assets_smoke.py
if ($LASTEXITCODE -ne 0) { throw "WebUI static asset smoke failed." }
& uv run python -c "from otto_master.audio.opus import OpusParameters, StreamingOpusEncoder; assert StreamingOpusEncoder(OpusParameters(16000)).feed(bytes(1920))"
if ($LASTEXITCODE -ne 0) { throw "Native Opus load smoke failed." }

Write-Host "Otto Master Windows verification passed."
