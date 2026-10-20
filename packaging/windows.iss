; Build with python build.py --installer. No user data or credentials are packaged.
#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif

[Setup]
AppId={{DD39A38E-F1E9-4AEC-B5CF-AB432DD4B797}
AppName=YT Slop DL
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\YT Slop DL
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist\release
OutputBaseFilename=YT-Slop-DL-{#AppVersion}-windows-x64-setup
SetupIconFile=..\build\icons\YTSlopDL-Lemon-FullBleed.ico
UninstallDisplayIcon={app}\YT Slop DL.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "ukrainian"; MessagesFile: "compiler:Languages\Ukrainian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\YT Slop DL\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\YT Slop DL"; Filename: "{app}\YT Slop DL.exe"
Name: "{autodesktop}\YT Slop DL"; Filename: "{app}\YT Slop DL.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\YT Slop DL.exe"; Description: "{cm:LaunchProgram,YT Slop DL}"; Flags: nowait postinstall skipifsilent
