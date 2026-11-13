[CmdletBinding()]
param(
    [string]$DataDir,
    [string]$PythonPath,
    [string]$FFmpegPath,
    [string]$FFprobePath,
    [string]$WhisperModel = 'small',
    [string]$OllamaModel = 'qwen2.5:3b',
    [string]$TtsModelPath,
    [ValidateSet('auto', 'cpu', 'gpu')][string]$HardwareMode = 'auto',
    [switch]$Json,
    [switch]$ReleaseProfile
)

$ErrorActionPreference = 'Stop'
$engine = Join-Path (Split-Path -Parent $PSScriptRoot) 'engine'
. (Join-Path $PSScriptRoot 'dependency-inventory.ps1')
if ($ReleaseProfile -and $env:AIVD_DEV_MODE -eq '1') { throw '-ReleaseProfile cannot be combined with AIVD_DEV_MODE=1. Start a normal PowerShell process for release checks.' }
if (-not $DataDir) { $DataDir = if ($ReleaseProfile) { Join-Path $env:LOCALAPPDATA 'com.aivideodubbing.desktop' } else { Join-Path (Split-Path -Parent $PSScriptRoot) '.dev' } }
$DataDir = [IO.Path]::GetFullPath($DataDir)

$request = [pscustomobject]@{
    data_dir = $DataDir; engine_dir = $engine
    settings = [pscustomobject]@{
        python_path = $PythonPath
        ffmpeg_path = $FFmpegPath; ffprobe_path = $FFprobePath; whisper_model = $WhisperModel
        ollama_model = $OllamaModel; tts_model_path = $TtsModelPath; hardware_mode = $HardwareMode
    }
}
$profile = $null
if (-not $ReleaseProfile) {
    $request.settings | Add-Member -NotePropertyName dev_mode -NotePropertyValue $true
    $request.settings | Add-Member -NotePropertyName dev_root -NotePropertyValue $DataDir
    $profile = Get-AivdDevProfile $request.settings $DataDir
    $request.settings = $profile.Settings
    Set-AivdDevEnvironment $profile
}

$originalConsoleEncoding = [Console]::OutputEncoding
$ownedServer = $null
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    if ($profile) {
        # A standalone check owns its server. An occupied endpoint is never accepted.
        $ownedServer = Start-AivdDevOllama $profile
        $request.settings | Add-Member -NotePropertyName ollama_owned -NotePropertyValue $true -Force
    }
    $report = Invoke-DependencyInventory $request
    if ($Json) {
        $report | ConvertTo-Json -Depth 10
    } else {
        $report.checks | Format-Table name, status, detail -Wrap | Out-Host
        $report.hardware | ConvertTo-Json -Depth 4 | Out-Host
    }
} finally {
    Stop-AivdOwnedServer $ownedServer
    [Console]::OutputEncoding = $originalConsoleEncoding
}
if ($report.ready) { exit 0 }
exit 1
