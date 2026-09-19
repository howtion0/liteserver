[CmdletBinding()]
param([string]$OpusBin)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $ProjectRoot

if ([string]::IsNullOrWhiteSpace($OpusBin) -and -not [string]::IsNullOrWhiteSpace($env:VCPKG_ROOT)) {
    $OpusBin = Join-Path $env:VCPKG_ROOT "installed\x64-windows\bin"
}
if ([string]::IsNullOrWhiteSpace($OpusBin) -and -not [string]::IsNullOrWhiteSpace($env:VCPKG_INSTALLATION_ROOT)) {
    $OpusBin = Join-Path $env:VCPKG_INSTALLATION_ROOT "installed\x64-windows\bin"
}
if (-not [string]::IsNullOrWhiteSpace($OpusBin)) {
    $ResolvedOpus = (Resolve-Path $OpusBin).Path
    if (-not (Test-Path (Join-Path $ResolvedOpus "opus.dll"))) {
        throw "opus.dll is missing from $ResolvedOpus."
    }
    $env:PATH = "$ResolvedOpus;$env:PATH"
}
if (-not (Test-Path (Join-Path $ProjectRoot ".env"))) {
    throw "Missing .env. Run scripts\windows\bootstrap.ps1 with the secrets TXT first."
}

& uv run python -m otto_master
exit $LASTEXITCODE
