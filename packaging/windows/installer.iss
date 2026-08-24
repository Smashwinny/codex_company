#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif

#ifndef AppVersionNumeric
  #define AppVersionNumeric "0.0.0.0"
#endif

#ifndef PyInstallerDir
  #error PyInstallerDir must point to the PyInstaller onedir output
#endif

#ifndef InstallerOutputDir
  #define InstallerOutputDir ".\output"
#endif

#ifndef IconFile
  #error IconFile must point to codex-quota.ico
#endif

#define AppName "Codex Quota"
#define AppExeName "CodexQuota.exe"
#define RunKey "Software\Microsoft\Windows\CurrentVersion\Run"

[Setup]
AppId={{D32E2B52-6B21-4EC1-A2E7-BAF4D3D291A5}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Smashwinny
AppPublisherURL=https://github.com/Smashwinny/codex_company
AppSupportURL=https://github.com/Smashwinny/codex_company/issues
AppUpdatesURL=https://github.com/Smashwinny/codex_company/releases
VersionInfoVersion={#AppVersionNumeric}
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersionNumeric}
VersionInfoProductTextVersion={#AppVersion}
VersionInfoTextVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\Codex Quota
DefaultGroupName=Codex Quota
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#InstallerOutputDir}
OutputBaseFilename=codex-quota-{#AppVersion}-windows-x64-setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
UsePreviousTasks=yes

[Languages]
#ifdef ChineseLanguageFile
Name: "chinesesimp"; MessagesFile: "{#ChineseLanguageFile}"
#endif
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "在桌面创建快捷方式"; GroupDescription: "附加选项："
Name: "autostart"; Description: "登录 Windows 时自动启动 {#AppName}"; GroupDescription: "附加选项："; Flags: unchecked

[Files]
Source: "{#PyInstallerDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{commondesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"

[Registry]
Root: HKCU; Subkey: "{#RunKey}"; ValueType: string; ValueName: "codex-quota"; ValueData: """{app}\{#AppExeName}"""; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#AppExeName}"; Description: "启动 {#AppName}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent unchecked

[Code]
var
  HadExistingAutostart: Boolean;
  MigrationSelectionApplied: Boolean;

function InitializeSetup: Boolean;
var
  ResultCode: Integer;
  PidFile, AppPid: String;
begin
  { 覆盖安装前强制结束运行中的实例。CloseApplications 对本应用无效：
    它发 WM_CLOSE，而托盘形态下关窗只是隐藏，进程不退，文件一直被占用。 }
  Exec('taskkill.exe', '/F /IM {#AppExeName}', '', SW_HIDE,
       ewWaitUntilTerminated, ResultCode);
  { 源码安装形态（pythonw -m codex_quota）：按 app.pid 精确结束，
    避免误杀其他 pythonw 进程 }
  PidFile := ExpandConstant('{localappdata}') + '\codex-quota\app.pid';
  if FileExists(PidFile) and LoadStringFromFile(PidFile, AppPid) then
    Exec('taskkill.exe', '/F /PID ' + Trim(AppPid), '', SW_HIDE,
         ewWaitUntilTerminated, ResultCode);
  Result := True;
end;

procedure InitializeWizard;
var
  ExistingCommand: String;
begin
  { Preserve an enabled source/Python installation while migrating its Run }
  { value to the installed frozen executable. }
  HadExistingAutostart := RegQueryStringValue(
    HKCU, '{#RunKey}', 'codex-quota', ExistingCommand);
  MigrationSelectionApplied := False;
  if HadExistingAutostart then
    WizardSelectTasks('autostart');
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  { Previous-task state is loaded after InitializeWizard. Re-apply the source }
  { installation migration once when the interactive Tasks page is reached. }
  if (CurPageID = wpSelectTasks) and HadExistingAutostart and
      (not MigrationSelectionApplied) then
  begin
    WizardSelectTasks('autostart');
    MigrationSelectionApplied := True;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  { If the user explicitly unchecks autostart, remove a legacy pythonw value }
  { instead of leaving a stale Run entry behind. }
  if CurStep = ssPostInstall then
  begin
    if WizardIsTaskSelected('autostart') or
        (HadExistingAutostart and WizardSilent) then
      RegWriteStringValue(
        HKCU, '{#RunKey}', 'codex-quota',
        '"' + ExpandConstant('{app}\{#AppExeName}') + '"')
    else
      RegDeleteValue(HKCU, '{#RunKey}', 'codex-quota');
  end;
end;

function InitializeUninstall: Boolean;
var
  ResultCode: Integer;
begin
  { 卸载前同样先结束运行中的实例，否则文件占用导致卸载残留 }
  Exec('taskkill.exe', '/F /IM {#AppExeName}', '', SW_HIDE,
       ewWaitUntilTerminated, ResultCode);
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  { The tray can enable autostart after installation. Always remove that value }
  { during uninstall, even when the install-time optional task was unchecked. }
  if CurUninstallStep = usUninstall then
    RegDeleteValue(HKCU, '{#RunKey}', 'codex-quota');
end;
