; 微信工作日报助手 - Inno Setup 安装脚本
; 不依赖外部语言文件，全部中文内嵌

#define MyAppName "微信工作日报助手"
#define MyAppVersion "2.1.1.0"
#define MyAppPublisher "WeChatWorkAgent"
#define MyAppExeName "WeChatWorkAgent.exe"

[Setup]
AppId={{7F8A3B2C-1D5E-4A6F-9C8B-0E3F5A2D7E91}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=installer_output
OutputBaseFilename=WeChatWorkAgent_Setup_v{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
WizardSizePercent=120,100
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#MyAppName}
SetupIconFile=assets\app_icon.ico
UninstallDisplayIcon={app}\WeChatWorkAgent.exe

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式:"

[Files]
; 主程序（排除 _internal\config，配置由运行时生成，避免升级卸载删除用户数据）
Source: "dist\WeChatWorkAgent\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "_internal\config\*"
; 配置模板（仅安装一次，卸载/升级不删除，保护用户数据）
Source: "dist\WeChatWorkAgent\_internal\config\config.yaml.example"; DestDir: "{app}\config"; Flags: ignoreversion uninsneveruninstall

[Icons]
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autoprograms}\{#MyAppName}\卸载"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
procedure InitializeWizard;
begin
  WizardForm.Caption := '微信工作日报助手 安装向导';
  WizardForm.WelcomeLabel1.Caption := '欢迎安装 微信工作日报助手';
  WizardForm.WelcomeLabel2.Caption := '本程序将安装微信工作日报助手到您的计算机。';
  WizardForm.FinishedHeadingLabel.Caption := '安装完成';
  WizardForm.FinishedLabel.Caption := '微信工作日报助手 已成功安装。';
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  { 在任务页面时默认勾选桌面快捷方式 }
  if (CurPageID = wpSelectTasks) and (WizardForm.TasksList.Items.Count > 0) then
  begin
    WizardForm.TasksList.Checked[0] := True;
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    MsgBox(
      '安装完成！'#13#10#13#10 +
      '请阅读软件目录下的「使用说明.txt」'#13#10 +
      '按照说明逐步完成初始化配置。',
      mbInformation, MB_OK
    );
  end;
end;
