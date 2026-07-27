Param(
  [string]$OutputDir = (Join-Path $PSScriptRoot "release"),
  [string]$PackageName = "epub-audio-sync-builder-installer",
  [switch]$SkipMfaRebuild = $false,
  [switch]$SkipPyInstaller = $false,
  [switch]$SkipNpm = $false,
  [switch]$SkipInstaller = $false,
  [switch]$PackageForDistribution = $false
)

$ErrorActionPreference = "Continue"
$root = (Resolve-Path $PSScriptRoot).Path

# ─── Paths ────────────────────────────────────────────────────────────────────
$conda = "C:\ProgramData\miniforge3\Scripts\conda.exe"
$condaBase = "C:\ProgramData\miniforge3"
$envAeneas = "aeneas-build"
$envMfa = "mfa-build"
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
$mfaExport = Join-Path $root "mfa_env.tar.gz"

# ─── Prerequisites ─────────────────────────────────────────────────────────────
function Test-Command($name) {
  return $null -ne (Get-Command $name -ErrorAction SilentlyContinue)
}

if (-not (Test-Path $conda)) {
  throw "Miniforge3 not found at $conda. Install from https://github.com/conda-forge/miniforge"
}

$pyinstaller = Get-Command pyinstaller -ErrorAction SilentlyContinue
if (-not $pyinstaller -and -not $SkipPyInstaller) {
  Write-Host "pyinstaller not found on PATH; will install inside conda env"
}

$innoSetupCandidates = @(
  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
  "C:\Program Files\Inno Setup 6\ISCC.exe"
)
$innoSetup = $innoSetupCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $innoSetup -and -not $SkipInstaller) {
  Write-Warning "Inno Setup not found. Installer will be skipped. Download from https://jrsoftware.org/isdl.php"
  $SkipInstaller = $true
}

$upx = Get-Command upx -ErrorAction SilentlyContinue

# ─── Step 1: Verify aeneas-build conda env ────────────────────────────────────
Write-Host "`n=== Step 1: Verifying aeneas-build conda environment ===" -ForegroundColor Cyan
$hasAeneas = & $conda env list | Select-String "^$envAeneas\s"
if (-not $hasAeneas) {
  Write-Host "Creating conda env '$envAeneas'..."
  & $conda create -y -n $envAeneas python=3.9
}
Write-Host "Installing Python dependencies in '$envAeneas'..."
& $conda install -y -n $envAeneas -c conda-forge numpy lxml beautifulsoup4 fastapi uvicorn pyinstaller ffmpeg
if ($LASTEXITCODE -ne 0) {
  # conda SSL may also fail; try pip with --index-url as last resort
  & $conda run -n $envAeneas pip install --index-url http://pypi.org/simple/ --trusted-host pypi.org numpy
  & $conda run -n $envAeneas pip install --index-url http://pypi.org/simple/ --trusted-host pypi.org -r (Join-Path $root "requirements.txt")
  & $conda run -n $envAeneas pip install --index-url http://pypi.org/simple/ --trusted-host pypi.org pyinstaller
}

# ─── Step 2: Compile C extensions ──────────────────────────────────────────────
Write-Host "`n=== Step 2: Compiling C extensions ===" -ForegroundColor Cyan
Push-Location $root
try {
  $env:AENEAS_WITH_CDTW = "True"
  $env:AENEAS_WITH_CMFCC = "True"
  $env:AENEAS_WITH_CEW = "False"
  $env:AENEAS_FORCE_CFW = "False"
  $vcvars = @(
    "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvarsall.bat",
    "C:\Program Files\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvarsall.bat",
    "C:\Program Files\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvarsall.bat",
    "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat"
  ) | Where-Object { Test-Path $_ } | Select-Object -First 1
  if ($vcvars) {
    Write-Host "Using vcvarsall.bat: $vcvars"
    # Resolve Windows SDK and MSVC include/lib dirs manually to avoid conda run env stripping
    $winSdk = "C:\Program Files (x86)\Windows Kits\10"
    $sdkIncludeDir = Get-ChildItem "$winSdk\Include\10.*" -Directory | Select-Object -Last 1
    $sdkLibDir = Get-ChildItem "$winSdk\Lib\10.*" -Directory | Select-Object -Last 1
    $msvcRootDir = Get-ChildItem "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Tools\MSVC\14.*" -Directory | Select-Object -Last 1
    if (-not $sdkIncludeDir -or -not $msvcRootDir) {
      Write-Warning "Could not detect Windows SDK or MSVC root. Falling back to vcvarsall.bat directly."
      $batContent = @"
@echo off
call "$vcvars" x64 >nul || exit /b 1
python setup.py build_ext --inplace
"@
    } else {
      $sdkVer = $sdkIncludeDir.Name
      $msvcRoot = $msvcRootDir.FullName
      $vcInclude = "$msvcRoot\include;$msvcRoot\ATLMFC\include"
      $sdkInclude = "$winSdk\Include\$sdkVer\ucrt;$winSdk\Include\$sdkVer\um;$winSdk\Include\$sdkVer\shared;$winSdk\Include\$sdkVer\winrt"
      $vcLib = "$msvcRoot\lib\x64;$msvcRoot\ATLMFC\lib\x64"
      $sdkLib = "$winSdk\Lib\$sdkVer\ucrt\x64;$winSdk\Lib\$sdkVer\um\x64"
      $sdkBin = "$winSdk\bin\$sdkVer\x64"
      $batContent = @"
@echo off
set "INCLUDE=$vcInclude;$sdkInclude"
set "LIB=$vcLib;$sdkLib"
set "PATH=$sdkBin;%PATH%"
python setup.py build_ext --inplace
"@
    }
    $batFile = Join-Path $root "build_ext_temp.bat"
    Set-Content -Path $batFile -Value $batContent -Encoding ASCII
    Write-Host "Running build_ext via batch file..."
    & $conda run -n $envAeneas cmd /c "$batFile 2>NUL"
    Remove-Item $batFile -Force -ErrorAction SilentlyContinue
  } else {
    & $conda run -n $envAeneas cmd /c "python setup.py build_ext --inplace 2>NUL"
  }
  if ($LASTEXITCODE -ne 0) {
    Write-Warning "C extension compilation had warnings (exit $LASTEXITCODE). Continuing..."
  }
} finally {
  Pop-Location
}

# ─── Step 3: Run tests ────────────────────────────────────────────────────────
Write-Host "`n=== Step 3: Running tests ===" -ForegroundColor Cyan
& $conda run -n $envAeneas python -m pytest (Join-Path $root "aeneas\tests\test_epubsync.py") -v 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Tests failed. Fix before proceeding."
}
Write-Host "All tests passed." -ForegroundColor Green

# ─── Step 4: Create / rebuild mfa-build conda env (OpenBLAS) ──────────────────
if (-not $SkipMfaRebuild) {
  Write-Host "`n=== Step 4: Creating mfa-build conda env with OpenBLAS ===" -ForegroundColor Cyan
  # Remove any existing directory (even if not a valid conda env) to start fresh
  $mfaPrefix = Join-Path (Join-Path $condaBase "envs") $envMfa
  if (Test-Path $mfaPrefix) {
    Write-Host "Removing existing '$envMfa' directory..."
    Remove-Item $mfaPrefix -Recurse -Force -ErrorAction SilentlyContinue
  }
  Write-Host "Creating '$envMfa' with montreal-forced-aligner + CPU-only kaldi + OpenBLAS..."
  & $conda create -y -n $envMfa python=3.12
  & $conda install -y -n $envMfa -c conda-forge montreal-forced-aligner "kaldi=5.5.1172=*cpu*" libblas=*=*openblas
  if ($LASTEXITCODE -ne 0) {
    throw "MFA conda env creation failed"
  }
  # Verify Python works before proceeding
  & $conda run -n $envMfa python -c "print('ok')" 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw "MFA env Python is broken after install"
  }
  # Nothing needed here; archiving done in Step 6 via tarfile
  Write-Host "MFA conda env created successfully." -ForegroundColor Green
} else {
  Write-Host "`n=== Step 4: Skipping MFA env rebuild ===" -ForegroundColor Yellow
}

# ─── Step 5: Trim MFA conda env ───────────────────────────────────────────────
Write-Host "`n=== Step 5: Trimming MFA conda env ===" -ForegroundColor Cyan
$mfaPrefix = Join-Path (Join-Path $condaBase "envs") $envMfa
$mfaEnvValid = (Test-Path $mfaPrefix) -and (Test-Path (Join-Path $mfaPrefix "conda-meta"))
if (-not $mfaEnvValid) {
  Write-Warning "MFA env not found or invalid at $mfaPrefix. Skipping trim."
} else {
  # Delete non-runtime files
  Write-Host "  Deleting static .lib files (Library\lib)..."
  Remove-Item (Join-Path $mfaPrefix "Library\lib\*.lib") -Force -ErrorAction SilentlyContinue
  Write-Host "  Deleting debug symbols (Library\symbols)..."
  Remove-Item (Join-Path $mfaPrefix "Library\symbols") -Recurse -Force -ErrorAction SilentlyContinue
  Write-Host "  Deleting C headers (Library\include)..."
  Remove-Item (Join-Path $mfaPrefix "Library\include") -Recurse -Force -ErrorAction SilentlyContinue
  # conda-meta is kept because conda pack needs it to validate the environment
  Write-Host "  Deleting fonts..."
  Remove-Item (Join-Path $mfaPrefix "fonts") -Recurse -Force -ErrorAction SilentlyContinue

  # Delete unnecessary Library\share subdirs
  $shareDir = Join-Path $mfaPrefix "Library\share"
  if (Test-Path $shareDir) {
    Get-ChildItem $shareDir -Directory | Where-Object { $_.Name -notin @('ffmpeg', 'licenses') } | ForEach-Object {
      Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
  }

  # Trim Python packages
  $sitePkgs = Join-Path $mfaPrefix "Lib\site-packages"
  $removePkgs = @(
    "matplotlib", "matplotlib-*", "mpl_toolkits",
    "fontTools", "fonttools-*",
    "PIL", "Pillow-*",
    "pygments", "pygments-*",
    "pip", "pip-*",
    "setuptools", "setuptools-*",
    "wheel", "wheel-*",
    "biopython-*",  # keep Bio if MFA needs it
    "tkinter", "tk-*",
    "__pycache__"
  )
  foreach ($pkg in $removePkgs) {
    $targets = Get-ChildItem $sitePkgs -Filter $pkg -ErrorAction SilentlyContinue
    foreach ($t in $targets) {
      Write-Host "  Removing $($t.Name)..."
      if ($t.PSIsContainer) { Remove-Item $t.FullName -Recurse -Force -ErrorAction SilentlyContinue }
      else { Remove-Item $t.FullName -Force -ErrorAction SilentlyContinue }
    }
  }

  # Strip __pycache__ everywhere
  Get-ChildItem $mfaPrefix -Recurse -Directory -Filter "__pycache__" -Force -ErrorAction SilentlyContinue | ForEach-Object {
    Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
  }

  # Compile all .py to .pyc, then delete .py source files
  Write-Host "  Compiling Python to bytecode..."
  & $conda run -n $envMfa python -m compileall -q $mfaPrefix 2>&1 | Out-Null
  Write-Host "  Removing .py source files..."
  Get-ChildItem $mfaPrefix -Recurse -Filter "*.py" -Force -ErrorAction SilentlyContinue | Where-Object {
    # Keep setup.py etc. in scripts
    $_.DirectoryName -notlike "*\Scripts\*"
  } | Remove-Item -Force -ErrorAction SilentlyContinue

  # UPX compress binaries
  if ($upx) {
    Write-Host "  UPX compressing DLLs and EXEs..."
    Get-ChildItem $mfaPrefix -Recurse -Include "*.dll", "*.exe", "*.pyd" -Force -ErrorAction SilentlyContinue | ForEach-Object {
      try { & $upx --best --quiet $_.FullName 2>&1 | Out-Null } catch {}
    }
  }

  # Measure final size
  $finalSize = (Get-ChildItem $mfaPrefix -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
  Write-Host "  Trimmed MFA env: $([math]::Round($finalSize / 1MB, 0)) MB" -ForegroundColor Green
}

# ─── Step 6: Archive MFA env ─────────────────────────────────────────────────
Write-Host "`n=== Step 6: Archiving MFA env ===" -ForegroundColor Cyan
if (-not $mfaEnvValid) {
  Write-Warning "MFA env not found or invalid at $mfaPrefix. Skipping archive."
} else {
  if (Test-Path $mfaExport) { Remove-Item $mfaExport -Force }
  $tarScriptFile = Join-Path $root "archive_mfa_env.py"
  @"
import tarfile, os
src = r"$mfaPrefix".rstrip('\\')
dst = r"$mfaExport"
with tarfile.open(dst, "w:gz", compresslevel=9) as tar:
    for root, dirs, files in os.walk(src):
        for name in files:
            fpath = os.path.join(root, name)
            arcname = os.path.relpath(fpath, src)
            tar.add(fpath, arcname)
"@ | Set-Content -Path $tarScriptFile -Encoding ASCII
  & $conda run -n $envAeneas python $tarScriptFile 2>&1
  Remove-Item $tarScriptFile -Force -ErrorAction SilentlyContinue
  if ($LASTEXITCODE -eq 0 -and (Test-Path $mfaExport)) {
    $packSize = (Get-Item $mfaExport).Length
    Write-Host "  Archived MFA env: $([math]::Round($packSize / 1MB, 0)) MB" -ForegroundColor Green
  } else {
    Write-Warning "Archiving failed. Continuing without MFA archive."
  }
}

# ─── Step 7: Install npm dependencies ─────────────────────────────────────────
if (-not $SkipNpm) {
  Write-Host "`n=== Step 7: Installing npm dependencies ===" -ForegroundColor Cyan
  # Kill any lingering Electron processes that may lock node_modules
  Get-Process "electron" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 1
  Push-Location $root
  try {
    npm ci
    if ($LASTEXITCODE -ne 0) {
      Write-Warning "npm ci failed; trying npm install..."
      npm install
    }
  } finally {
    Pop-Location
  }
} else {
  Write-Host "`n=== Step 7: Skipping npm install ===" -ForegroundColor Yellow
}

# ─── Step 8: Build PyInstaller backend ────────────────────────────────────────
if (-not $SkipPyInstaller) {
  Write-Host "`n=== Step 8: Building PyInstaller backend ===" -ForegroundColor Cyan
  Push-Location $root
  try {
    if (Test-Path $backendBuildRoot) {
      Remove-Item $backendBuildRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    & $conda run -n $envAeneas pyinstaller --noconfirm --clean pyinstaller-epub-sync-desktop.spec 2>&1
    if ($LASTEXITCODE -ne 0) {
      throw "PyInstaller build failed"
    }
    if (-not (Test-Path $backendExe)) {
      throw "Backend executable was not created at $backendExe"
    }
    Write-Host "  Backend built: $backendExe" -ForegroundColor Green
  } finally {
    Pop-Location
  }
} else {
  Write-Host "`n=== Step 8: Skipping PyInstaller ===" -ForegroundColor Yellow
}

# ─── Step 9: Assemble staging directory ──────────────────────────────────────
Write-Host "`n=== Step 9: Assembling staging directory ===" -ForegroundColor Cyan
Remove-Item $OutputDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $staging -Force | Out-Null
New-Item -ItemType Directory -Path $appResources -Force | Out-Null

# Electron binary
$electronDir = Join-Path $root "node_modules\electron\dist"
if (Test-Path $electronDir) {
  Copy-Item (Join-Path $electronDir "*") $staging -Recurse -Force
  $electronExe = Join-Path $staging "electron.exe"
  if (Test-Path $electronExe) {
    Rename-Item $electronExe $appExeName
  }
} else {
  Write-Warning "Electron binary not found at $electronDir. Skipping."
}

# App resources
Copy-Item (Join-Path $root "electron") (Join-Path $appResources "electron") -Recurse -Force
Copy-Item (Join-Path $root "package.json") (Join-Path $appResources "package.json") -Force
Copy-Item $iconPng (Join-Path $appResources "audiobook.png") -Force

# Backend (PyInstaller output)
if (Test-Path $backendBuildRoot) {
  Copy-Item $backendBuildRoot (Join-Path $appResources "backend") -Recurse -Force
}

# ffmpeg / ffprobe from npm
$backendBin = Join-Path $appResources "backend\bin"
New-Item -ItemType Directory -Path $backendBin -Force | Out-Null
$ffmpegSrc = Join-Path $root "node_modules\ffmpeg-static\ffmpeg.exe"
$ffprobeSrc = Join-Path $root "node_modules\ffprobe-static\bin\win32\x64\ffprobe.exe"
if (Test-Path $ffmpegSrc) { Copy-Item $ffmpegSrc (Join-Path $backendBin "ffmpeg.exe") -Force }
if (Test-Path $ffprobeSrc) { Copy-Item $ffprobeSrc (Join-Path $backendBin "ffprobe.exe") -Force }

# MFA bundled env
if (Test-Path $mfaExport) {
  $mfaStaging = Join-Path $appResources "mfa"
  New-Item -ItemType Directory -Path $mfaStaging -Force | Out-Null
  Copy-Item $mfaExport (Join-Path $mfaStaging "mfa_env.tar.gz") -Force
}

# ─── Step 10: Create icon ─────────────────────────────────────────────────────
Write-Host "`n=== Step 10: Creating .ico icon ===" -ForegroundColor Cyan
$iconScript = @"
from pathlib import Path
from PIL import Image
src = Path(r"$iconPng")
dst = Path(r"$iconIco")
image = Image.open(src).convert("RGBA")
image.save(dst, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
"@
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCmd) {
  $iconScript | python - 2>&1 | Out-Null
} else {
  Write-Warning "Python not on PATH; skipping icon generation"
}

# ─── Step 11: Create Inno Setup installer ─────────────────────────────────────
if (-not $SkipInstaller -and $innoSetup) {
  Write-Host "`n=== Step 11: Creating Inno Setup installer ===" -ForegroundColor Cyan
  $package = Get-Content (Join-Path $root "package.json") -Raw | ConvertFrom-Json

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
  $installerSize = (Get-Item $installer).Length
  Write-Host "Installer created: $installer ($([math]::Round($installerSize/1MB,0)) MB)" -ForegroundColor Green
} else {
  Write-Host "`n=== Step 11: Skipping installer ===" -ForegroundColor Yellow
}

Write-Host "`n=== Build Complete ===" -ForegroundColor Green
