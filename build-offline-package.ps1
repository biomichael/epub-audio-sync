Param(
  [string]$OutputDir = (Join-Path $PSScriptRoot "release"),
  [string]$PackageName = "epub-audio-sync-builder-installer"
)

$ErrorActionPreference = "Stop"

$root = (Resolve-Path $PSScriptRoot).Path
$package = Get-Content -LiteralPath (Join-Path $root "package.json") -Raw | ConvertFrom-Json
$pyinstaller = (Get-Command pyinstaller).Source
$pythonCommand = @("python", "py") | ForEach-Object { Get-Command $_ -ErrorAction SilentlyContinue } | Select-Object -First 1
if (-not $pythonCommand) {
  throw "Python is required to generate the installer icon."
}

$innoSetup = @(
  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
  "C:\Program Files\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $innoSetup) {
  throw "Inno Setup Compiler (ISCC.exe) was not found."
}

$buildHome = Join-Path $root ".build-home"
New-Item -ItemType Directory -Path $buildHome -Force | Out-Null
$env:HOME = $buildHome
$env:USERPROFILE = $buildHome
$env:HOMEDRIVE = (Split-Path $buildHome -Qualifier)
$env:HOMEPATH = (Split-Path $buildHome -NoQualifier)

$staging = Join-Path $OutputDir "staging"
$backendBuildRoot = Join-Path $root "dist\desktop_api"
$backendExe = Join-Path $backendBuildRoot "desktop_api.exe"
$installer = Join-Path $OutputDir "$PackageName.exe"
$iconPng = Join-Path $root "audiobook.png"
$iconIco = Join-Path $staging "audiobook.ico"
$appExeName = "EPUB Audio Sync Builder.exe"
$appExePath = Join-Path $staging $appExeName
$appResources = Join-Path $staging "resources\app"
$issPath = Join-Path $OutputDir "epub-audio-sync-builder-installer.iss"

Remove-Item -LiteralPath $OutputDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $staging -Force | Out-Null

Push-Location $root
try {
  & $pyinstaller --noconfirm --clean pyinstaller-epub-sync-desktop.spec
  if (-not (Test-Path $backendExe)) {
    throw "Backend executable was not created: $backendExe"
  }

  Copy-Item -Path (Join-Path $root "node_modules\electron\dist\*") -Destination $staging -Recurse -Force
  New-Item -ItemType Directory -Path $appResources -Force | Out-Null
  Copy-Item -Path (Join-Path $root "electron") -Destination (Join-Path $appResources "electron") -Recurse -Force
  Copy-Item -Path (Join-Path $root "package.json") -Destination (Join-Path $appResources "package.json") -Force
  Copy-Item -Path $iconPng -Destination (Join-Path $appResources "audiobook.png") -Force
  Rename-Item -LiteralPath (Join-Path $staging "electron.exe") -NewName $appExeName

  Copy-Item -Path $backendBuildRoot -Destination (Join-Path $appResources "backend") -Recurse -Force
  New-Item -ItemType Directory -Path (Join-Path $appResources "backend\bin") -Force | Out-Null
  Copy-Item -Path (Join-Path $root "node_modules\ffmpeg-static\ffmpeg.exe") -Destination (Join-Path $appResources "backend\bin\ffmpeg.exe") -Force
  Copy-Item -Path (Join-Path $root "node_modules\ffprobe-static\bin\win32\x64\ffprobe.exe") -Destination (Join-Path $appResources "backend\bin\ffprobe.exe") -Force

  $iconScript = @"
from pathlib import Path
from PIL import Image

src = Path(r"$iconPng")
dst = Path(r"$iconIco")
image = Image.open(src).convert("RGBA")
image.save(dst, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
"@
  $iconScript | & $pythonCommand.Source - | Out-Null

  $iss = @"
#define AppName "EPUB Audio Sync Builder"
#define AppVersion "$($package.version)"
#define AppPublisher "EPUB Audio Sync Builder"
#define AppExeName "$appExeName"

[Setup]
AppId=EPUBAudioSyncBuilder
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={code:GetInstallDir}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputBaseFilename=$PackageName
OutputDir=$OutputDir
SetupIconFile=$iconIco
UninstallDisplayIcon={app}\audiobook.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
UsePreviousAppDir=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: checkedonce

[Files]
Source: "$staging\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\EPUB Audio Sync Builder\EPUB Audio Sync Builder"; Filename: "{app}\$appExeName"; WorkingDir: "{app}"; IconFilename: "{app}\audiobook.ico"
Name: "{autodesktop}\EPUB Audio Sync Builder"; Filename: "{app}\$appExeName"; WorkingDir: "{app}"; IconFilename: "{app}\audiobook.ico"; Tasks: desktopicon
Name: "{autoprograms}\EPUB Audio Sync Builder\Uninstall EPUB Audio Sync Builder"; Filename: "{uninstallexe}"; WorkingDir: "{app}"; IconFilename: "{app}\audiobook.ico"

[Code]
function GetInstallDir(Default: string): string;
begin
  if IsAdminInstallMode then
    Result := ExpandConstant('{autopf}\EPUB Audio Sync Builder')
  else
    Result := ExpandConstant('{localappdata}\Programs\EPUB Audio Sync Builder');
end;
"@
  Set-Content -LiteralPath $issPath -Value $iss -Encoding utf8

  & $innoSetup $issPath
  if (-not (Test-Path $installer)) {
    throw "Installer was not created: $installer"
  }
} finally {
  Pop-Location
}

Write-Host "Installer created at $installer"
