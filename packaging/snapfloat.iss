; Windows installer (Inno Setup 6). CI builds it after PyInstaller:
;   ISCC /DVersion=1.2.0 packaging\snapfloat.iss   ->  dist\SnapFloat-Setup.exe
; Per-user: no admin prompt, installs to %LOCALAPPDATA%\Programs\SnapFloat.
#ifndef Version
  #define Version "dev"
#endif

[Setup]
AppId={{3B84BBBD-89C0-40DE-B250-CE0E4D4B92EB}
AppName=SnapFloat
AppVersion={#Version}
AppPublisher=Shaurya Shakya
AppPublisherURL=https://github.com/shaurya-8055/snapfloat
DefaultDirName={localappdata}\Programs\SnapFloat
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=SnapFloat-Setup
SetupIconFile=snapfloat.ico
UninstallDisplayIcon={app}\SnapFloat.exe
WizardStyle=modern

[Files]
Source: "..\dist\SnapFloat.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\SnapFloat"; Filename: "{app}\SnapFloat.exe"

[Run]
Filename: "{app}\SnapFloat.exe"; Description: "Run SnapFloat"; Flags: nowait postinstall skipifsilent

[Code]
const
  // a one-file exe keeps itself locked for a moment after it says goodbye (it cleans up its unpacked copy)
  EXIT_GRACE_MS = 2000;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  // updating: a running SnapFloat locks its exe, so ask it to quit first
  if FileExists(ExpandConstant('{app}\SnapFloat.exe')) then begin
    Exec(ExpandConstant('{app}\SnapFloat.exe'), '--quit', '', SW_HIDE, ewWaitUntilTerminated, Code);
    Sleep(EXIT_GRACE_MS);
  end;
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
begin
  // before any file is removed: quit SnapFloat and undo what it set up (autostart, PrtSc takeover, settings, temp shots)
  if CurUninstallStep = usUninstall then begin
    Exec(ExpandConstant('{app}\SnapFloat.exe'), '--uninstall --quiet', '', SW_HIDE, ewWaitUntilTerminated, Code);
    Sleep(EXIT_GRACE_MS);
  end;
end;
