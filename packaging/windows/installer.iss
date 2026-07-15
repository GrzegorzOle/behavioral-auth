; Inno Setup script -- installer for the behavioural-auth Windows bundle.
;
; Wraps the one-folder PyInstaller output (dist\behavioral-auth\, built from
; packaging/windows/behavioral-auth-windows.spec) into a single setup .exe that:
;
;   * installs the suite under Program Files;
;   * drops an editable config in C:\ProgramData\behavioral-auth\config.yaml
;     (kept across upgrades -- never clobbers the user's edits) and points
;     BEHAVIORAL_AUTH_CONFIG at it, machine-wide, so both the service and the CLI
;     read it;
;   * registers behavioral-auth-service.exe with the Service Control Manager
;     (auto-start) and starts it. Registering the service as admin also lets the
;     Event Log source be created the first time an event is written.
;
; On uninstall the service is stopped and removed before the files go.
;
; Build (on Windows, after the PyInstaller onedir exists):
;     iscc /DMyAppVersion=0.3.0 packaging\windows\installer.iss
; Not runtime-verified on a real Windows box yet -- see Planned work, Stage 2.

#ifndef MyAppVersion
  #define MyAppVersion "0.4.0"
#endif

#define MyAppName "behavioral-auth"
#define MyService "behavioral-auth-service.exe"
#define DataDir "{commonappdata}\behavioral-auth"

[Setup]
AppId={{5B8D3E2A-9C41-4F7B-A0E6-BEHAV10AUTH01}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=behavioral-auth
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; The service is registered machine-wide and writes under ProgramData, so the
; installer must run elevated.
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=behavioral-auth-setup-{#MyAppVersion}
; Write the installer into the repo's dist\ (relative to this .iss), not Inno
; Setup's default Output\ subdir -- that is where build-windows.ps1, the CI
; smoke test and the upload step all look for it.
OutputDir=..\..\dist
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#MyAppName} {#MyAppVersion}

[Files]
; The whole PyInstaller one-folder tree (exes + _internal\).
Source: "..\..\dist\behavioral-auth\*"; DestDir: "{app}"; \
    Flags: recursesubdirs createallsubdirs ignoreversion
; The editable config: install to ProgramData, but only if the user has none
; there yet, so an upgrade never overwrites their settings.
Source: "config.windows.yaml"; DestDir: "{#DataDir}"; DestName: "config.yaml"; \
    Flags: onlyifdoesntexist uninsneveruninstall

[Dirs]
; World-writable enough for the LocalSystem service; created up front so the
; first run does not race to make it.
Name: "{#DataDir}"

[Registry]
; Machine-wide env var so the SCM-launched service and any CLI both resolve the
; ProgramData config. uninsdeletevalue removes just this value on uninstall.
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; \
    ValueType: expandsz; ValueName: "BEHAVIORAL_AUTH_CONFIG"; \
    ValueData: "{#DataDir}\config.yaml"; Flags: preservestringtype uninsdeletevalue

[Run]
; Register with the SCM (auto-start) and start it now. runascurrentuser keeps the
; elevated installer token, which the service registration needs.
Filename: "{app}\{#MyService}"; Parameters: "--startup auto install"; \
    StatusMsg: "Registering the behavioral-auth service..."; \
    Flags: runhidden waituntilterminated
Filename: "{app}\{#MyService}"; Parameters: "start"; \
    StatusMsg: "Starting the behavioral-auth service..."; \
    Flags: runhidden waituntilterminated

[UninstallRun]
; Stop and remove the service before the files are deleted. runascurrentuser so
; the uninstaller's elevated token reaches the SCM.
Filename: "{app}\{#MyService}"; Parameters: "stop"; \
    Flags: runhidden waituntilterminated; RunOnceId: "StopSvc"
Filename: "{app}\{#MyService}"; Parameters: "remove"; \
    Flags: runhidden waituntilterminated; RunOnceId: "RemoveSvc"
