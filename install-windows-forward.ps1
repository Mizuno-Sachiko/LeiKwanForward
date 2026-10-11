<#
.SYNOPSIS
    Install the standalone LeiKwanForward CLI and register its machine PATH entry.
.EXAMPLE
    .\install-windows-forward.ps1
.EXAMPLE
    .\install-windows-forward.ps1 -InstallDir "D:\Apps\LeiKwanForward"
.EXAMPLE
    .\install-windows-forward.ps1 -InstallDir "D:\Apps\LeiKwanForward" -WhatIf
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateNotNullOrEmpty()]
    [string]$InstallDir = (Join-Path $env:ProgramFiles 'LeiKwanForward')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an elevated PowerShell under the intended Windows user.'
}

if (-not [IO.Path]::IsPathRooted($InstallDir) -or $InstallDir.Contains(';')) {
    throw "InstallDir must be an absolute filesystem path without semicolons."
}
$InstallDir = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
$payloadDir = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')
if ($InstallDir -eq [IO.Path]::GetPathRoot($InstallDir).TrimEnd('\')) {
    throw "InstallDir must name an application directory, not a drive root."
}

$names = @(
    'LeiKwanForward.cmd',
    'LeiKwanForward.py',
    'leikwan_api.py',
    'README.md',
    'test_leikwan_api.py',
    'install-windows-forward.ps1'
)
foreach ($name in $names + @('runtime\python.exe', 'runtime\python314._pth')) {
    if (-not (Test-Path -LiteralPath (Join-Path $payloadDir $name) -PathType Leaf)) {
        throw "Incomplete installation package: $name is missing."
    }
}
if (Test-Path -LiteralPath $InstallDir) {
    if (-not (Test-Path -LiteralPath $InstallDir -PathType Container)) {
        throw "InstallDir is not a directory: $InstallDir"
    }
    $contents = @(Get-ChildItem -LiteralPath $InstallDir -Force)
    if ($contents.Count -gt 0 -and (
            -not (Test-Path -LiteralPath (Join-Path $InstallDir 'LeiKwanForward.py') -PathType Leaf) -or
            -not (Test-Path -LiteralPath (Join-Path $InstallDir 'leikwan_api.py') -PathType Leaf))) {
        throw "The destination is not empty and is not a LeiKwanForward installation."
    }
}

# Validate the bundled runtime before changing files or the user's PATH.
& (Join-Path $payloadDir 'runtime\python.exe') -X utf8 -B (Join-Path $payloadDir 'LeiKwanForward.py') --help | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "The bundled LeiKwanForward CLI could not start."
}

Write-Host "install:      $InstallDir"
Write-Host "runtime:      bundled Python; no system Python required"
$dataDir = Join-Path $InstallDir 'data'
Write-Host "credentials:  $(Join-Path $dataDir 'api-token') (current-user encryption)"
if (-not $PSCmdlet.ShouldProcess($InstallDir, "Install LeiKwanForward and update machine PATH")) {
    return
}

# Protect program files even when the selected drive grants write access by default.
New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
& icacls.exe $InstallDir /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Could not restrict the program directory permissions: $InstallDir"
}

if ($InstallDir -ne $payloadDir) {
    $files = @($names | ForEach-Object { Get-Item -LiteralPath (Join-Path $payloadDir $_) })
    $files += @(Get-ChildItem -LiteralPath (Join-Path $payloadDir 'runtime') -File -Recurse)
    foreach ($file in $files) {
        $relativePath = $file.FullName.Substring($payloadDir.Length + 1)
        $destination = Join-Path $InstallDir $relativePath
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
    }
}

# Only the data directory is writable by the installing user; program files stay protected.
New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
$userSid = $identity.User.Value
& icacls.exe $dataDir /inheritance:r /grant:r "*${userSid}:(OI)(CI)M" '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Could not restrict the credential directory permissions: $dataDir"
}

$machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
$entries = @($machinePath -split ';' | Where-Object {
        $_ -and $_.TrimEnd('\', '/') -ine $InstallDir
    })
# Prepend the selected directory without deleting another installation's files.
[Environment]::SetEnvironmentVariable('Path', (@($InstallDir) + $entries -join ';'), 'Machine')
$env:Path = $InstallDir + ';' + $env:Path

Write-Host "cli:          LeiKwanForward --help"
Write-Host "path:         machine PATH updated; reopen other terminals"
