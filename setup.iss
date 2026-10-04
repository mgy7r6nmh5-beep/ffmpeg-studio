; Inno Setup script — FFmpeg Studio 安装版
; 用 Inno Setup 6+ 编译：ISCC.exe setup.iss

#define MyAppName "FFmpeg Studio"
#define MyAppVersion "1.2.1"
#define MyAppPublisher "FFmpeg Studio"
#define MyAppURL "http://localhost:8787"
#define MyAppExeName "FFmpegStudio.exe"

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
OutputDir=dist
OutputBaseFilename=FFmpegStudio_Setup
PrivilegesRequired=admin

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
english.AppName=FFmpeg Studio
english.CreateDesktopIcon=Create a &desktop shortcut
english.RunProgramAfterFinish=Run FFmpeg Studio after installation

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "Additional shortcuts:"; Flags: checkedonce

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; 可选：放入 ffmpeg.exe / ffprobe.exe 到 app 目录以实现完全便携
; Source: "tools\ffmpeg.exe"; DestDir: "{app}"; Flags: ignoreversion
; Source: "tools\ffprobe.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:RunProgramAfterFinish}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // 首次运行自动在 app 目录下创建 uploads/ outputs/
    CreateDir(ExpandConstant('{app}\uploads'));
    CreateDir(ExpandConstant('{app}\outputs'));
  end;
end;