[CmdletBinding()]
param([switch]$SkipAi, [switch]$CheckOnly, [switch]$Offline, [switch]$FunctionsOnly)

$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop

function Invoke-AivdSetupCommand {
    param([string]$Executable, [string[]]$ArgumentList, [string]$WorkingDirectory, [string]$InputText = '')
    Push-Location -LiteralPath $WorkingDirectory
    try {
        if ($InputText) { $InputText | & $Executable @ArgumentList | ForEach-Object { Write-Host $_ } }
        else { & $Executable @ArgumentList | ForEach-Object { Write-Host $_ } }
        if ($LASTEXITCODE -ne 0) { throw "$Executable failed with exit code $LASTEXITCODE. Review the stage above and rerun setup.cmd." }
    } finally { Pop-Location }
}

function Get-AivdDevSetupStatus {
    param([string]$RepoRoot)
    $data = Join-Path $RepoRoot '.dev'
    . (Join-Path $RepoRoot 'scripts\bootstrap-python.ps1') -FunctionsOnly
    . (Join-Path $RepoRoot 'scripts\webview2.ps1')
    $null = Assert-LocalDirectory $data
    Assert-NoReparsePath $data
    $python = Get-AivdBootstrapPythonStatus $data
    $nodeReady = $false
    $nodeDetail = ''
    try { $nodeDetail = & (Join-Path $RepoRoot 'scripts\bootstrap-node.ps1') -CheckOnly; $nodeReady = $true } catch { $nodeDetail = $_.Exception.Message }
    $nativeReady = $false
    $nativeDetail = 'Prepare the portable Python runtime before inspecting the native toolchain.'
    $bootstrap = Join-Path $RepoRoot 'scripts\toolchain\bootstrap.py'
    if ($python.ready -and (Test-Path -LiteralPath $bootstrap)) {
        $native = Invoke-InventoryProcess $python.path @('-I','-B',$bootstrap,'--repo-root',$RepoRoot,'--check') -TimeoutMs 45000
        $nativeReady = $native.exit_code -eq 0
        $nativeDetail = ($native.stdout + $native.stderr).Trim()
    }
    $npmReady = $true
    foreach ($relative in @('node_modules\@tauri-apps\cli\tauri.js','node_modules\vite\bin\vite.js','node_modules\vue-tsc\bin\vue-tsc.js')) {
        if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot $relative) -PathType Leaf)) { $npmReady = $false }
    }
    $webview = Get-AivdWebView2Status
    [pscustomobject]@{
        ready = ($python.ready -and $nodeReady -and $nativeReady -and $npmReady -and $webview.installed)
        python = $python; node = [pscustomobject]@{ ready=$nodeReady; detail=$nodeDetail }
        native = [pscustomobject]@{ ready=$nativeReady; detail=$nativeDetail }; npm_ready=$npmReady; webview2=$webview
        ai_ready = $null; ai_note = 'AI models and service readiness are checked in the app under System. This inspection never starts Ollama.'
    }
}

function Invoke-AivdDevSetup {
    param([string]$RepoRoot, [switch]$SkipAi, [switch]$CheckOnly, [switch]$Offline)
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or -not [Environment]::Is64BitOperatingSystem) { throw 'This application requires Windows x64.' }
    $RepoRoot = [IO.Path]::GetFullPath($RepoRoot)
    if ($CheckOnly) { return Get-AivdDevSetupStatus $RepoRoot }
    if ($Offline -and -not $SkipAi) { throw 'Offline verification requires -SkipAi. Full first-time AI setup needs Internet.' }
    $data = Join-Path $RepoRoot '.dev'
    . (Join-Path $RepoRoot 'scripts\bootstrap-python.ps1') -FunctionsOnly
    . (Join-Path $RepoRoot 'scripts\webview2.ps1')
    $null = Assert-LocalDirectory $data
    Assert-NoReparsePath $data
    $bootstrapLease = $null
    $engineLease = $null
    $environmentBefore = @{}
    $changed = @{
        TEMP=(Join-Path $data 'temp'); TMP=(Join-Path $data 'temp')
        NPM_CONFIG_CACHE=(Join-Path $data 'cache\npm'); NPM_CONFIG_PREFIX=(Join-Path $data 'cache\npm-global')
        NPM_CONFIG_USERCONFIG=(Join-Path $data 'profile\.npmrc'); NPM_CONFIG_GLOBALCONFIG=(Join-Path $data 'profile\npm-globalrc')
        PYTHONPATH=$null; PYTHONHOME=$null; PYTHONNOUSERSITE='1'; PYTHONUTF8='1'; NODE_PATH=$null; NODE_OPTIONS=$null
    }
    $toolchainNames = @('PATH','LIB','INCLUDE','VCToolsInstallDir','VCINSTALLDIR','VSINSTALLDIR','WindowsSdkDir','WindowsSDKVersion','CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER','CC','CXX','AR','RC','CARGO_HOME','CARGO_TARGET_DIR','RUSTC','CARGO')
    foreach ($name in @($changed.Keys) + $toolchainNames) { $environmentBefore[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
    $beforeConsoleEncoding = [Console]::OutputEncoding
    $beforePipelineEncoding = $OutputEncoding
    try {
        foreach ($relative in @('temp','profile','cache\npm','projects')) {
            $directory = Join-Path $data $relative
            Assert-NoReparsePath $directory
            New-Item -ItemType Directory -Path $directory -Force | Out-Null
        }
        Assert-NoReparsePath (Join-Path $data 'bootstrap.lock')
        Assert-NoReparsePath (Join-Path $data 'engine.lock')
        try { $bootstrapLease = [IO.File]::Open((Join-Path $data 'bootstrap.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
        catch [IO.IOException] { throw 'Another clone setup is running. Wait for it to finish.' }
        if (-not $SkipAi) {
            try { $engineLease = [IO.File]::Open((Join-Path $data 'engine.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
            catch [IO.IOException] { throw 'The app is processing a project. Close the app or let the task finish before running full setup.' }
            Assert-AivdDevPortFree
        }
        foreach ($name in $changed.Keys) { [Environment]::SetEnvironmentVariable($name, $changed[$name], 'Process') }
        [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
        $OutputEncoding = [Text.UTF8Encoding]::new($false)
        Write-Host '[1/5] Preparing project-local Python and Node...'
        $python = Initialize-AivdBootstrapPython -DataDir $data -Offline:$Offline -EngineLeaseOwned:($null -ne $engineLease)
        $null = & (Join-Path $RepoRoot 'scripts\bootstrap-node.ps1') -Offline:$Offline
        $node = Join-Path $data 'tools\node\node.exe'
        $npmCli = Join-Path $data 'tools\node\node_modules\npm\bin\npm-cli.js'
        Write-Host '[2/5] Preparing project-local Rust, MSVC and Windows SDK...'
        $nativeArgs = @('-I','-B',(Join-Path $RepoRoot 'scripts\toolchain\bootstrap.py'),'--repo-root',$RepoRoot)
        if ($Offline) { $nativeArgs += '--offline' }
        Invoke-AivdSetupCommand $python $nativeArgs $RepoRoot
        . (Join-Path $RepoRoot 'scripts\toolchain\env.ps1')
        $env:PATH = (Split-Path -Parent $node) + ';' + $env:PATH
        Write-Host '[3/5] Installing frontend packages from package-lock.json...'
        $npmArgs = @($npmCli,'ci','--cache',$changed.NPM_CONFIG_CACHE,'--no-audit','--no-fund')
        if ($Offline) { $npmArgs += '--offline' }
        Invoke-AivdSetupCommand $node $npmArgs $RepoRoot
        Write-Host '[4/5] Checking Microsoft WebView2 runtime...'
        $webview = Get-AivdWebView2Status
        if (-not $webview.installed) {
            if ($Offline) { throw 'WebView2 is missing. Run setup with Internet access.' }
            $webview = Install-AivdWebView2 -DataDir $data
        }
        if (-not $webview.installed) { throw 'Microsoft WebView2 is not ready.' }
        if (-not $SkipAi) {
            Write-Host '[5/5] Installing AI packages and downloading Whisper, Qwen and VieNeu models...'
            $request = @{ data_dir=$data; cancel_path=(Join-Path $data ('cancel\setup-' + [guid]::NewGuid().ToString('N'))); settings=@{ dev_mode=$true; dev_root=$data; whisper_model='small'; ollama_model='qwen2.5:3b'; hardware_mode='auto'; asr_device='auto'; translation_device='auto'; tts_device='auto' } }
            $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
            Invoke-AivdSetupCommand $powershell @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',(Join-Path $RepoRoot 'scripts\install-deps.ps1'),'-InstallMissing') $RepoRoot ($request | ConvertTo-Json -Depth 5 -Compress)
            $engineLease.Dispose(); $engineLease = $null
            Invoke-AivdSetupCommand $powershell @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',(Join-Path $RepoRoot 'scripts\check-deps.ps1')) $RepoRoot
        } else { Write-Host '[5/5] AI setup deferred. Use System > Automatic setup in the app when needed.' }
        Write-Host 'Setup completed. Run .\dev.cmd to open the desktop app.'
        [pscustomobject]@{ ready=$true; repo_root=$RepoRoot; python=$python; node=$node; ai_ready=$(if ($SkipAi) { $null } else { $true }) }
    } finally {
        if ($engineLease) { $engineLease.Dispose() }
        if ($bootstrapLease) { $bootstrapLease.Dispose() }
        foreach ($name in $environmentBefore.Keys) { [Environment]::SetEnvironmentVariable($name, $environmentBefore[$name], 'Process') }
        [Console]::OutputEncoding = $beforeConsoleEncoding
        $OutputEncoding = $beforePipelineEncoding
    }
}

if (-not $FunctionsOnly) {
    try {
        $result = Invoke-AivdDevSetup -RepoRoot (Split-Path -Parent $PSScriptRoot) -SkipAi:$SkipAi -CheckOnly:$CheckOnly -Offline:$Offline
        $result | ConvertTo-Json -Depth 6
        if (-not $result.ready) { exit 1 }
    } catch { Write-Error $_; exit 1 }
}
