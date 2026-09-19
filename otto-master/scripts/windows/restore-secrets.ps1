[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SecretsFile,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ResolvedSecrets = (Resolve-Path $SecretsFile).Path
$EnvPath = Join-Path $ProjectRoot ".env"
$CredentialDirectory = Join-Path $ProjectRoot ".local-secrets"
$CredentialPath = Join-Path $CredentialDirectory "mqtt-credentials.json"

if ((Test-Path $EnvPath) -and -not $Force) {
    throw "$EnvPath already exists. Re-run with -Force only if replacement is intended."
}
if ((Test-Path $CredentialPath) -and -not $Force) {
    throw "$CredentialPath already exists. Re-run with -Force only if replacement is intended."
}

$EnvironmentLines = [System.Collections.Generic.List[string]]::new()
$CredentialBase64 = $null
foreach ($Line in Get-Content -LiteralPath $ResolvedSecrets -Encoding UTF8) {
    if ($Line -match '^OTTO_MQTT_CREDENTIALS_JSON_BASE64=(.+)$') {
        $CredentialBase64 = $Matches[1]
        continue
    }
    if ($Line -match '^(OTTO_[A-Z0-9_]+|DEEPSEEK_API_KEY|ZHIHU_ACCESS_SECRET)=') {
        $EnvironmentLines.Add($Line)
    }
}

if ($EnvironmentLines.Count -eq 0) {
    throw "No Otto environment values were found in $ResolvedSecrets."
}
if ([string]::IsNullOrWhiteSpace($CredentialBase64)) {
    throw "The existing EVA MQTT credential store is missing from $ResolvedSecrets."
}

$CredentialBytes = [Convert]::FromBase64String($CredentialBase64)
$CredentialText = [Text.Encoding]::UTF8.GetString($CredentialBytes)
$CredentialObject = $CredentialText | ConvertFrom-Json
if ($CredentialObject.version -ne 1 -or $null -eq $CredentialObject.users) {
    throw "The decoded MQTT credential store is invalid."
}

$Utf8NoBom = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllLines($EnvPath, $EnvironmentLines, $Utf8NoBom)
[IO.Directory]::CreateDirectory($CredentialDirectory) | Out-Null
[IO.File]::WriteAllBytes($CredentialPath, $CredentialBytes)

Write-Host "Restored .env without printing secret values."
Write-Host "Restored MQTT identities for $($CredentialObject.users.Count) broker users."
