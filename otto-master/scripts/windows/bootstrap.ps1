[CmdletBinding()]
param(
    [string]$SecretsFile,
    [string]$VcpkgRoot,
    [switch]$ForceSecrets,
    [switch]$SkipTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $ProjectRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required. Install it from https://docs.astral.sh/uv/getting-started/installation/ and open a new PowerShell window."
}

if (-not [string]::IsNullOrWhiteSpace($SecretsFile)) {
    & (Join-Path $PSScriptRoot "restore-secrets.ps1") -SecretsFile $SecretsFile -Force:$ForceSecrets
}
if (-not (Test-Path (Join-Path $ProjectRoot ".env"))) {
    throw "Missing .env. Pass -SecretsFile with the separately delivered secrets TXT."
}

& uv python install 3.12
if ($LASTEXITCODE -ne 0) { throw "uv python install failed." }
& uv sync --all-extras --locked
if ($LASTEXITCODE -ne 0) { throw "uv sync failed." }

if ([string]::IsNullOrWhiteSpace($VcpkgRoot)) {
    $VcpkgRoot = $env:VCPKG_ROOT
}
if ([string]::IsNullOrWhiteSpace($VcpkgRoot)) {
    $VcpkgRoot = $env:VCPKG_INSTALLATION_ROOT
}
if ([string]::IsNullOrWhiteSpace($VcpkgRoot)) {
    throw "Set VCPKG_ROOT or pass -VcpkgRoot so Opus can be installed for voice."
}

$Vcpkg = Join-Path $VcpkgRoot "vcpkg.exe"
if (-not (Test-Path $Vcpkg)) {
    throw "vcpkg.exe was not found at $Vcpkg."
}
& $Vcpkg install opus:x64-windows
if ($LASTEXITCODE -ne 0) { throw "vcpkg failed to install opus:x64-windows." }
$OpusBin = Join-Path $VcpkgRoot "installed\x64-windows\bin"
if (-not (Test-Path (Join-Path $OpusBin "opus.dll"))) {
    throw "opus.dll was not found at $OpusBin after installation."
}
$env:PATH = "$OpusBin;$env:PATH"

if (-not $SkipTests) {
    & (Join-Path $PSScriptRoot "verify.ps1") -OpusBin $OpusBin
}

Write-Host "Windows source runtime is ready. Start it with scripts\windows\run.ps1."
