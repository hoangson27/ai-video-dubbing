[CmdletBinding()]
param(
    [string]$PythonPath,
    [string]$DataDir,
    [ValidatePattern('^[a-z0-9][a-z0-9.-]{0,59}$')][string]$WhisperModel = 'small',
    [ValidatePattern('^qwen[a-zA-Z0-9._:-]*$')][string]$OllamaModel = 'qwen2.5:3b',
    [string]$FFmpegPath,
    [string]$FFprobePath,
    [string]$TtsModelPath,
    [switch]$DownloadModels,
    [switch]$Gpu,
    [switch]$ReleaseProfile
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'dependency-inventory.ps1')
if ($ReleaseProfile -and $env:AIVD_DEV_MODE -eq '1') { throw '-ReleaseProfile cannot be combined with AIVD_DEV_MODE=1. Start a normal PowerShell process for release setup.' }
if (-not $DataDir) { $DataDir = if ($ReleaseProfile) { Join-Path $env:LOCALAPPDATA 'com.aivideodubbing.desktop' } else { Join-Path (Split-Path -Parent $PSScriptRoot) '.dev' } }
$profile = $null
if (-not $ReleaseProfile) {
    $profile = Get-AivdDevProfile @{ dev_mode = $true; dev_root = $DataDir } $DataDir
    $DataDir = $profile.Root
    $PythonPath = Join-Path $DataDir 'runtime\python311-nuget\python.exe'
    $FFmpegPath = $profile.Settings.ffmpeg_path
    $FFprobePath = $profile.Settings.ffprobe_path
    $TtsModelPath = $profile.Settings.tts_model_path
    Set-AivdDevEnvironment $profile
    Assert-AivdDevPortFree
}
$engine = Join-Path (Split-Path -Parent $PSScriptRoot) 'engine'
$requirements = Join-Path $engine 'requirements.txt'
if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) {
    throw "Cannot find packaged Python engine: $requirements"
}
$DataDir = [IO.Path]::GetFullPath($DataDir)
$venvPath = Join-Path $DataDir 'venv'
$venvPython = Join-Path $venvPath 'Scripts\python.exe'

function Invoke-Checked {
    param([string]$Executable, [string[]]$ArgumentList)
    & $Executable @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$Executable failed with exit code $LASTEXITCODE. Review the output above and rerun setup."
    }
}

$versionCheck = 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 11) and sys.maxsize > 2**32 else 1)'
if ($profile) {
    $expectedBase = (Join-Path $DataDir 'runtime\python311-nuget') | ConvertTo-Json -Compress
    $versionCheck = "import os,sys;expected=$expectedBase;sys.exit(0 if sys.version_info[:2]==(3,11) and sys.maxsize>2**32 and os.path.normcase(os.path.realpath(sys.base_prefix))==os.path.normcase(os.path.realpath(expected)) else 1)"
}
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    $launcherArgs = @()
    if ($profile) {
        $launcher = $PythonPath
        if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) { throw "Missing project-local Python: $launcher. Run dependency installation in the app first." }
    } elseif ($PythonPath) {
        $launcher = (Resolve-Path -LiteralPath $PythonPath).Path
    } elseif (Test-Path -LiteralPath (Join-Path $DataDir 'runtime\python311-nuget\python.exe') -PathType Leaf) {
        $launcher = Join-Path $DataDir 'runtime\python311-nuget\python.exe'
    } elseif (Test-Path -LiteralPath (Join-Path $DataDir 'runtime\python311\python.exe') -PathType Leaf) {
        $launcher = Join-Path $DataDir 'runtime\python311\python.exe'
    } else {
        $py = Get-Command py.exe -ErrorAction SilentlyContinue
        if (-not $py) { throw 'Install Python 3.11 x64 from python.org, or pass -PythonPath.' }
        $launcher = $py.Source
        $launcherArgs = @('-3.11')
    }
    $autoInstall = $env:PYTHON_MANAGER_AUTOMATIC_INSTALL
    $legacyInstall = $env:PYLAUNCHER_ALLOW_INSTALL
    $env:PYTHON_MANAGER_AUTOMATIC_INSTALL = 'false'
    $env:PYLAUNCHER_ALLOW_INSTALL = $null
    try {
        & $launcher @launcherArgs -E -s -c $versionCheck
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 x64 is required. Pass -PythonPath to its python.exe.' }
        Invoke-Checked $launcher ($launcherArgs + @('-E', '-s', '-m', 'venv', $venvPath))
    } finally {
        $env:PYTHON_MANAGER_AUTOMATIC_INSTALL = $autoInstall
        $env:PYLAUNCHER_ALLOW_INSTALL = $legacyInstall
    }
}

& $venvPython -E -s -c $versionCheck
if ($LASTEXITCODE -ne 0) { throw "The virtual environment at $venvPath must use Python 3.11 x64." }

Write-Host "Installing Python packages only inside $venvPath"
Invoke-Checked $venvPython @('-m', 'pip', 'install', '--requirement', $requirements)
if ($Gpu) {
    Invoke-Checked $venvPython @('-m', 'pip', 'install', '--upgrade', '--index-url', 'https://download.pytorch.org/whl/cu128', 'torch==2.8.0', 'torchaudio==2.8.0')
    Invoke-Checked $venvPython @('-m', 'pip', 'install', 'transformers==4.57.6')
}
Invoke-Checked $venvPython @('-m', 'pip', 'check')

if ($DownloadModels) {
    Write-Host 'Downloading the selected Whisper, VieNeu-TTS and Ollama models. This can require several GB.'
    $modelArgs = @('-X', 'utf8', '-E', '-s', '-B', '-m', 'dubbing.setup_models', '--data-dir', $DataDir, '--whisper-model', $WhisperModel)
    if ($Gpu) { $modelArgs += '--gpu' }
    if ($TtsModelPath) { $modelArgs += @('--tts-model-path', $TtsModelPath) }
    Push-Location -LiteralPath $engine
    try {
        Invoke-Checked $venvPython $modelArgs
    } finally {
        Pop-Location
    }
    $ollama = if (-not $profile) { Get-Command ollama.exe -ErrorAction SilentlyContinue } else { $null }
    $ollamaPath = if ($profile) { $profile.Settings.ollama_path } elseif ($ollama) { $ollama.Source } elseif (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe') -PathType Leaf) {
        Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
    } elseif (Test-Path -LiteralPath (Join-Path $DataDir 'tools\ollama\ollama.exe') -PathType Leaf) {
        Join-Path $DataDir 'tools\ollama\ollama.exe'
    } else { throw 'Install Ollama for Windows, reopen PowerShell and rerun -DownloadModels.' }
    $previousHost = $env:OLLAMA_HOST
    $env:OLLAMA_HOST = if ($profile) { '127.0.0.1:11435' } else { '127.0.0.1:11434' }
    $ownedServer = $null
    try {
        if ($profile) { $ownedServer = Start-AivdDevOllama $profile }
        Invoke-Checked $ollamaPath @('pull', $OllamaModel)
    } finally {
        Stop-AivdOwnedServer $ownedServer
        $env:OLLAMA_HOST = $previousHost
    }
} else {
    Write-Host 'Model downloads require -DownloadModels or an existing local model installation.'
}

$check = @{ DataDir = $DataDir; WhisperModel = $WhisperModel; OllamaModel = $OllamaModel }
if ($FFmpegPath) { $check.FFmpegPath = $FFmpegPath }
if ($FFprobePath) { $check.FFprobePath = $FFprobePath }
if ($TtsModelPath) { $check.TtsModelPath = $TtsModelPath }
if ($Gpu) { $check.HardwareMode = 'gpu' }
if ($ReleaseProfile) { $check.ReleaseProfile = $true }
& (Join-Path $PSScriptRoot 'check-deps.ps1') @check
exit $LASTEXITCODE
