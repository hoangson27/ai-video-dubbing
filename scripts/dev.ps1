[CmdletBinding()]
param([switch]$CheckOnly)

$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
$repoRoot = Split-Path -Parent $PSScriptRoot
$devRoot = Join-Path $repoRoot '.dev'
$rustEnv = Join-Path $repoRoot 'scripts\toolchain\env.ps1'
$node = Join-Path $devRoot 'tools\node\node.exe'
$npmCli = Join-Path $devRoot 'tools\node\node_modules\npm\bin\npm-cli.js'
$tauriCli = Join-Path $repoRoot 'node_modules\@tauri-apps\cli\tauri.js'
$rustBin = Join-Path $repoRoot '.verification\toolchain\rust-local\bin'
$changes = @{
    AIVD_DEV_MODE='1'; AIVD_DEV_ROOT=$devRoot
    OLLAMA_HOST='127.0.0.1:11435'; OLLAMA_MODELS=(Join-Path $devRoot 'models\ollama'); OLLAMA_NO_CLOUD='1'
    PIP_CACHE_DIR=(Join-Path $devRoot 'cache\pip'); HF_HOME=(Join-Path $devRoot 'cache\huggingface')
    TORCH_HOME=(Join-Path $devRoot 'cache\torch'); XDG_CACHE_HOME=(Join-Path $devRoot 'cache')
    UV_CACHE_DIR=(Join-Path $devRoot 'cache\uv'); NUMBA_CACHE_DIR=(Join-Path $devRoot 'cache\numba')
    GRADIO_TEMP_DIR=(Join-Path $devRoot 'temp\gradio'); COREPACK_HOME=(Join-Path $devRoot 'cache\corepack')
    NPM_CONFIG_CACHE=(Join-Path $devRoot 'cache\npm'); NPM_CONFIG_PREFIX=(Join-Path $devRoot 'cache\npm-global')
    NPM_CONFIG_USERCONFIG=(Join-Path $devRoot 'profile\.npmrc'); NPM_CONFIG_GLOBALCONFIG=(Join-Path $devRoot 'profile\npm-globalrc')
    TEMP=(Join-Path $devRoot 'temp'); TMP=(Join-Path $devRoot 'temp')
    RUSTC=(Join-Path $rustBin 'rustc.exe'); CARGO=(Join-Path $rustBin 'cargo.exe')
    PYTHONNOUSERSITE='1'; PYTHONPATH=$null; PYTHONHOME=$null; NODE_PATH=$null; NODE_OPTIONS=$null
}
$rustNames = @('PATH','LIB','INCLUDE','VCToolsInstallDir','VCINSTALLDIR','VSINSTALLDIR','WindowsSdkDir','WindowsSDKVersion','CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER','CC','CXX','AR','RC','CARGO_HOME','CARGO_TARGET_DIR')
$oldEnvironment = @{}
foreach ($name in @($changes.Keys) + $rustNames) { $oldEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$pushed = $false
try {
    foreach ($relative in @('cache','temp','temp\gradio','profile','models\ollama','projects')) { New-Item -ItemType Directory -Path (Join-Path $devRoot $relative) -Force | Out-Null }
    foreach ($name in $changes.Keys) { [Environment]::SetEnvironmentVariable($name, $changes[$name], 'Process') }
    $nativeTools = Join-Path $repoRoot '.verification\toolchain'
    if (-not (Test-Path -LiteralPath (Join-Path $rustBin 'cargo.exe')) -or -not (Test-Path -LiteralPath (Join-Path $nativeTools 'msvc\VC\Tools\MSVC')) -or -not (Test-Path -LiteralPath (Join-Path $nativeTools 'msvc\Windows Kits\10\Lib'))) {
        if ($CheckOnly) { throw 'Thiếu toolchain cục bộ. Chạy .\setup.cmd để chuẩn bị môi trường.' }
        & (Join-Path $PSScriptRoot 'setup-dev.ps1') -SkipAi
        if ($LASTEXITCODE -ne 0) { throw 'Thiết lập công cụ chưa hoàn tất. Chạy lại .\setup.cmd -SkipAi.' }
    }
    if (-not (Test-Path -LiteralPath $rustEnv)) { throw 'Thiếu scripts/toolchain/env.ps1 trong mã nguồn. Kiểm tra lại bản clone.' }
    . $rustEnv
    $null = & (Join-Path $PSScriptRoot 'bootstrap-node.ps1') -CheckOnly:$CheckOnly
    if (-not (Test-Path -LiteralPath $node) -or -not (Test-Path -LiteralPath $npmCli)) { throw 'Chưa có Node/npm riêng của project.' }
    $env:PATH = (Split-Path -Parent $node) + ';' + (Join-Path $repoRoot 'node_modules\.bin') + ';' + $env:PATH
    foreach ($tool in @('node.exe','npm.cmd','cargo.exe','rustc.exe')) {
        $command = Get-Command $tool -CommandType Application -ErrorAction Stop | Select-Object -First 1
        if (-not [IO.Path]::GetFullPath($command.Source).StartsWith($repoRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw "$tool đang trỏ ra ngoài project." }
    }
    Push-Location -LiteralPath $repoRoot
    $pushed = $true
    if ($CheckOnly) {
        [ordered]@{ dev_root=$devRoot; node=$node; npm=$npmCli; cargo=$env:CARGO; rustc=$env:RUSTC; cargo_home=$env:CARGO_HOME; ollama_host=$env:OLLAMA_HOST; ollama_models=$env:OLLAMA_MODELS; pip_cache=$env:PIP_CACHE_DIR; npm_cache=$env:NPM_CONFIG_CACHE } | ConvertTo-Json
    } else {
        if (-not (Test-Path -LiteralPath $tauriCli)) {
            & $node $npmCli ci --cache $env:NPM_CONFIG_CACHE
            if ($LASTEXITCODE -ne 0) { throw 'npm ci chưa hoàn tất.' }
        }
        & $node $tauriCli dev
        if ($LASTEXITCODE -ne 0) { throw "Tauri dev đã dừng với mã $LASTEXITCODE." }
    }
} finally {
    if ($pushed) { Pop-Location }
    foreach ($name in $oldEnvironment.Keys) { [Environment]::SetEnvironmentVariable($name, $oldEnvironment[$name], 'Process') }
}
