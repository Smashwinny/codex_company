[CmdletBinding()]
param(
    [string]$Python = "",
    [string]$IsccPath = "",
    [switch]$SkipDependencyInstall,
    [switch]$RecreateBuildEnvironment
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$packageRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$pyprojectPath = Join-Path $repoRoot "pyproject.toml"
$iconPath = Join-Path $repoRoot "assets\codex-quota.ico"
$issPath = Join-Path $PSScriptRoot "installer.iss"
$specPath = Join-Path $PSScriptRoot "CodexQuota.spec"
$launcherPath = Join-Path $PSScriptRoot "launcher.py"
$versionInfoPath = Join-Path $PSScriptRoot "version_info.txt"
$buildRoot = Join-Path $PSScriptRoot ".build"
$venvRoot = Join-Path $buildRoot "venv"
$pyinstallerWork = Join-Path $buildRoot "pyinstaller-work"
$distRoot = Join-Path $buildRoot "dist"
$outputRoot = Join-Path $buildRoot "output"

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$Description
    )

    Write-Host "==> $Description"
    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE"
    }
}

function Remove-SafeBuildTree {
    param([Parameter(Mandatory = $true)][string]$Path)

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $allowedPrefix = $packageRoot.TrimEnd('\') + '\'
    if (-not $fullPath.StartsWith(
            $allowedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a path outside packaging/windows: $fullPath"
    }
    if (Test-Path -LiteralPath $fullPath) {
        Remove-Item -LiteralPath $fullPath -Recurse -Force
    }
}

function Resolve-Executable {
    param([Parameter(Mandatory = $true)][string]$Value)

    if (Test-Path -LiteralPath $Value -PathType Leaf) {
        return (Resolve-Path -LiteralPath $Value).Path
    }
    $command = Get-Command $Value -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        return $null
    }
    return $command.Source
}

function Find-Iscc {
    param([string]$ExplicitPath)

    if ($ExplicitPath) {
        $resolved = Resolve-Executable $ExplicitPath
        if ($null -eq $resolved) {
            throw "ISCC.exe was not found at: $ExplicitPath"
        }
        return $resolved
    }

    $fromPath = Resolve-Executable "ISCC.exe"
    if ($null -ne $fromPath) {
        return $fromPath
    }

    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 7\ISCC.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 7\ISCC.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 7\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return $null
}

foreach ($requiredFile in @(
        $pyprojectPath, $iconPath, $issPath, $specPath,
        $launcherPath, $versionInfoPath)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required file is missing: $requiredFile"
    }
}

$pyproject = Get-Content -LiteralPath $pyprojectPath -Raw
$projectBlock = [regex]::Match(
    $pyproject, '(?ms)^\[project\]\s*(?<body>.*?)(?=^\[|\z)')
if (-not $projectBlock.Success) {
    throw "Could not find the [project] section in pyproject.toml"
}
$versionMatch = [regex]::Match(
    $projectBlock.Groups['body'].Value,
    '(?m)^version\s*=\s*["''](?<value>[^"'']+)["'']\s*$')
if (-not $versionMatch.Success) {
    throw "Could not read project.version from pyproject.toml"
}
$appVersion = $versionMatch.Groups['value'].Value
$numericMatch = [regex]::Match(
    $appVersion, '^(?<major>\d+)\.(?<minor>\d+)\.(?<patch>\d+)')
if (-not $numericMatch.Success) {
    throw "project.version must begin with major.minor.patch: $appVersion"
}
$numericVersion = '{0}.{1}.{2}.0' -f @(
    $numericMatch.Groups['major'].Value
    $numericMatch.Groups['minor'].Value
    $numericMatch.Groups['patch'].Value
)

# 由 pyproject 版本现生成 version_info.txt——不维护静态副本，
# 否则 EXE 属性页版本与安装包版本必然漂移（filevers 曾停在 0.2.0）
$verTuple = '{0}, {1}, {2}, 0' -f @(
    $numericMatch.Groups['major'].Value
    $numericMatch.Groups['minor'].Value
    $numericMatch.Groups['patch'].Value
)
$versionInfo = @"
# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($verTuple),
    prodvers=($verTuple),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'080404B0',
        [
          StringStruct(u'CompanyName', u'codex-company'),
          StringStruct(u'FileDescription', u'Codex Quota Monitor'),
          StringStruct(u'FileVersion', u'$appVersion'),
          StringStruct(u'InternalName', u'CodexQuota'),
          StringStruct(u'LegalCopyright', u'MIT License'),
          StringStruct(u'OriginalFilename', u'CodexQuota.exe'),
          StringStruct(u'ProductName', u'Codex Quota'),
          StringStruct(u'ProductVersion', u'$appVersion'),
        ],
      )
    ]),
    VarFileInfo([VarStruct(u'Translation', [2052, 1200])]),
  ],
)
"@
Set-Content -LiteralPath $versionInfoPath -Value $versionInfo -Encoding UTF8

$basePython = $null
$basePythonArgs = @()
if ($Python) {
    $basePython = Resolve-Executable $Python
    if ($null -eq $basePython) {
        throw "Python was not found: $Python"
    }
} else {
    $basePython = Resolve-Executable "py.exe"
    if ($null -ne $basePython) {
        $basePythonArgs = @("-3")
    } else {
        $basePython = Resolve-Executable "python.exe"
    }
    if ($null -eq $basePython) {
        throw "Python 3.10+ was not found. Install Python or pass -Python <path>."
    }
}

if ($RecreateBuildEnvironment) {
    Remove-SafeBuildTree $venvRoot
}
New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $venvRoot "Scripts\python.exe"))) {
    $venvArgs = @($basePythonArgs) + @("-m", "venv", $venvRoot)
    Invoke-Checked $basePython $venvArgs "Creating isolated build environment"
}
$venvPython = Join-Path $venvRoot "Scripts\python.exe"

if (-not $SkipDependencyInstall) {
    Invoke-Checked $venvPython @(
        "-m", "pip", "install", "--disable-pip-version-check", "--upgrade",
        "pip"
    ) "Updating pip"
    Invoke-Checked $venvPython @(
        "-m", "pip", "install", "--disable-pip-version-check", "--upgrade",
        "-e", "${repoRoot}[gui,build]"
    ) "Installing build dependencies"
}

Invoke-Checked $venvPython @(
    "-c",
    "import struct,sys; bits=struct.calcsize('P')*8; print(f'Python {sys.version.split()[0]} ({bits}-bit)'); raise SystemExit(0 if bits == 64 and sys.version_info >= (3,10) else 1)"
) "Checking Python version and architecture"

Remove-SafeBuildTree $pyinstallerWork
Remove-SafeBuildTree $distRoot
Remove-SafeBuildTree $outputRoot
New-Item -ItemType Directory -Path $pyinstallerWork, $distRoot, $outputRoot -Force | Out-Null

$cloudflared = Join-Path $repoRoot "vendor\bin\cloudflared.exe"
if (-not (Test-Path -LiteralPath $cloudflared -PathType Leaf)) {
    New-Item -ItemType Directory -Path (Split-Path $cloudflared -Parent) -Force |
        Out-Null
    Write-Host "==> Downloading cloudflared for the tunnel feature"
    $downloadArgs = @{
        Uri = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
        OutFile = $cloudflared
        UseBasicParsing = $true
    }
    Invoke-WebRequest @downloadArgs
}
$cloudflaredSignature = Get-AuthenticodeSignature -LiteralPath $cloudflared
if (($cloudflaredSignature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) -or
        ($null -eq $cloudflaredSignature.SignerCertificate) -or
        ($cloudflaredSignature.SignerCertificate.Subject -notmatch 'Cloudflare, Inc\.')) {
    throw "cloudflared.exe is not validly signed by Cloudflare: $($cloudflaredSignature.Status)"
}
Write-Host "==> Verified Cloudflare Authenticode signature"

$pyinstallerArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--workpath", $pyinstallerWork,
    "--distpath", $distRoot,
    $specPath
)
Invoke-Checked $venvPython $pyinstallerArgs "Building PyInstaller onedir application"

$applicationDir = Join-Path $distRoot "CodexQuota"
$applicationExe = Join-Path $applicationDir "CodexQuota.exe"
if (-not (Test-Path -LiteralPath $applicationExe -PathType Leaf)) {
    throw "PyInstaller completed but the application executable is missing: $applicationExe"
}
Write-Host "==> Smoke-testing the packaged executable"
# PowerShell does not reliably wait for a GUI-subsystem executable invoked with
# &, so use Start-Process and check its real exit code. Hidden also guarantees
# this verification cannot flash a console window.
$smokeArgs = @{
    FilePath = $applicationExe
    ArgumentList = @("--cli", "--help")
    WindowStyle = "Hidden"
    Wait = $true
    PassThru = $true
}
$smokeProcess = Start-Process @smokeArgs
if ($smokeProcess.ExitCode -ne 0) {
    throw "Packaged executable smoke test failed with exit code $($smokeProcess.ExitCode)"
}

$iscc = Find-Iscc $IsccPath
if ($null -eq $iscc) {
    throw @"
Inno Setup 6 or 7 was not found. Install it, then run this script again:
  winget install --id JRSoftware.InnoSetup --exact
Or pass -IsccPath <path-to-ISCC.exe>.
"@
}

$isccArgs = @(
    "/DAppVersion=$appVersion",
    "/DAppVersionNumeric=$numericVersion",
    "/DPyInstallerDir=$applicationDir",
    "/DInstallerOutputDir=$outputRoot",
    "/DIconFile=$iconPath",
    $issPath
)
$chineseLanguageFile = Join-Path (Split-Path $iscc -Parent) `
    "Languages\ChineseSimplified.isl"
if (Test-Path -LiteralPath $chineseLanguageFile -PathType Leaf) {
    $isccArgs = @("/DChineseLanguageFile=$chineseLanguageFile") + $isccArgs
    Write-Host "==> Including Simplified Chinese Inno Setup messages"
} else {
    Write-Host "==> Simplified Chinese Inno messages not installed; using English wizard"
}
Invoke-Checked $iscc $isccArgs "Compiling Inno Setup installer"

$installer = Get-ChildItem -LiteralPath $outputRoot -Filter "*.exe" -File |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if ($null -eq $installer) {
    throw "Inno Setup completed but no installer was written to: $outputRoot"
}

$hash = Get-FileHash -LiteralPath $installer.FullName -Algorithm SHA256
Write-Host ""
Write-Host "Installer ready: $($installer.FullName)"
Write-Host "SHA256: $($hash.Hash)"
