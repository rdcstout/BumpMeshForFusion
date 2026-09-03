param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$ScriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDirectory = Resolve-Path (Join-Path $ScriptDirectory "..\..")
$ManifestVersion = (Get-Content (Join-Path $ProjectDirectory 'BumpMeshForFusion.manifest') -Raw | ConvertFrom-Json).version
if (-not $Version) { $Version = $ManifestVersion }
if ($Version -ne $ManifestVersion) { throw 'Installer version must match the add-in manifest.' }
$Compiler = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"

if (-not (Test-Path $Compiler)) {
    throw "Inno Setup 6 was not found at $Compiler"
}

New-Item -ItemType Directory -Force -Path (Join-Path $ProjectDirectory "dist") | Out-Null
& $Compiler "/DMyAppVersion=$Version" (Join-Path $ScriptDirectory "BumpMeshForFusion.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed with exit code $LASTEXITCODE" }
