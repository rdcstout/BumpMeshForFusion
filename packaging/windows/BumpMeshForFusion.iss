#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif

#define MyAppName "BumpMesh for Fusion"
#define MyAppPublisher "Extrusion Therapy"

[Setup]
AppId={{5DA2AC90-7DE9-46A7-B458-A6AF99829599}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={userappdata}\Autodesk\FusionAddins\BumpMeshForFusion
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=BumpMeshForFusion-Windows-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#MyAppName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=no
RestartApplications=no

[InstallDelete]
Type: filesandordirs; Name: "{app}"
Type: filesandordirs; Name: "{userappdata}\Autodesk\Autodesk Fusion 360\API\AddIns\BumpMeshForFusion"

[Dirs]
Name: "{app}"

[Files]
Source: "..\..\BumpMeshForFusion.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\BumpMeshForFusion.manifest"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\resources\*"; DestDir: "{app}\resources"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\web\*"; DestDir: "{app}\web"; Flags: ignoreversion recursesubdirs createallsubdirs

[Run]
Filename: "{cmd}"; Parameters: "/C echo BumpMesh for Fusion is installed. Restart Fusion to load it. & pause"; Description: "Show next step"; Flags: postinstall nowait skipifsilent

