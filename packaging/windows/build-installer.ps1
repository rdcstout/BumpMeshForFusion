param(
    [string]$Version = "0.1.0"
)

$ErrorActionPreference = "Stop"
$ScriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDirectory = Resolve-Path (Join-Path $ScriptDirectory "..\..")
$Compiler = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"

if (-not (Test-Path $Compiler)) {
    throw "Inno Setup 6 was not found at $Compiler"
}

New-Item -ItemType Directory -Force -Path (Join-Path $ProjectDirectory "dist") | Out-Null
& $Compiler "/DMyAppVersion=$Version" (Join-Path $ScriptDirectory "BumpMeshForFusion.iss")

