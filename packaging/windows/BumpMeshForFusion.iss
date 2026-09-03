#ifndef MyAppVersion
  #error MyAppVersion must be supplied by build-installer.ps1
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

[Dirs]
Name: "{app}"

[Files]
Source: "..\..\BumpMeshForFusion.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\updates.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\BumpMeshForFusion.manifest"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\resources\*"; DestDir: "{app}\resources"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\web\*"; DestDir: "{app}\web"; Flags: ignoreversion recursesubdirs createallsubdirs

[Run]
Filename: "{cmd}"; Parameters: "/C echo BumpMesh for Fusion is installed. Restart Fusion to load it. & pause"; Description: "Show next step"; Flags: postinstall nowait skipifsilent

[Code]
var
  PreviousPath, LegacyPath, LegacyBackup: String;
  MovedCurrent, MovedLegacy, InstallComplete: Boolean;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  PreviousPath := ExpandConstant('{app}') + '.previous';
  LegacyPath := ExpandConstant('{userappdata}\Autodesk\Autodesk Fusion 360\API\AddIns\BumpMeshForFusion');
  LegacyBackup := LegacyPath + '.previous';
  if (DirExists(PreviousPath) and not MovedCurrent) or (DirExists(LegacyBackup) and not MovedLegacy) then begin
    Result := 'A previous BumpMesh installation backup exists. Restore or move it before trying again.';
    exit;
  end;
  if not MovedCurrent and DirExists(ExpandConstant('{app}')) then begin
    MovedCurrent := RenameFile(ExpandConstant('{app}'), PreviousPath);
    if not MovedCurrent then begin
      Result := 'Close Fusion and try again. The existing add-in could not be backed up.';
      exit;
    end;
  end;
  if not MovedLegacy and DirExists(LegacyPath) then begin
    MovedLegacy := RenameFile(LegacyPath, LegacyBackup);
    if not MovedLegacy then Result := 'Close Fusion and try again. The legacy add-in could not be backed up.';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    InstallComplete := True;
    if MovedCurrent then DelTree(PreviousPath, True, True, True);
    if MovedLegacy then DelTree(LegacyBackup, True, True, True);
  end;
end;

procedure DeinitializeSetup();
begin
  if not InstallComplete then begin
    if MovedCurrent then begin
      DelTree(ExpandConstant('{app}'), True, True, True);
      if not RenameFile(PreviousPath, ExpandConstant('{app}')) then
        MsgBox('Your previous add-in is preserved at ' + PreviousPath + '. Restore it before restarting Fusion.', mbError, MB_OK);
    end;
    if MovedLegacy and not RenameFile(LegacyBackup, LegacyPath) then
      MsgBox('Your legacy add-in is preserved at ' + LegacyBackup + '.', mbError, MB_OK);
  end;
end;
