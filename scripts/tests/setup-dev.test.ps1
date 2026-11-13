$ErrorActionPreference = 'Stop'
$scripts = Split-Path -Parent $PSScriptRoot
. (Join-Path $scripts 'setup-dev.ps1') -FunctionsOnly
function Assert-Setup($Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
$fixture = Join-Path (Split-Path -Parent $scripts) ('.verification\setup-controller-tests\' + [guid]::NewGuid().ToString('N') + ' source with spaces')
foreach ($directory in @('scripts\toolchain','node_modules\@tauri-apps\cli','node_modules\vite\bin','node_modules\vue-tsc\bin','.dev\tools\node\node_modules\npm\bin')) {
    New-Item -ItemType Directory -Path (Join-Path $fixture $directory) -Force | Out-Null
}
foreach ($file in @('scripts\toolchain\bootstrap.py','scripts\install-deps.ps1','scripts\check-deps.ps1','node_modules\@tauri-apps\cli\tauri.js','node_modules\vite\bin\vite.js','node_modules\vue-tsc\bin\vue-tsc.js','.dev\tools\node\node.exe','.dev\tools\node\node_modules\npm\bin\npm-cli.js')) {
    [IO.File]::WriteAllText((Join-Path $fixture $file), 'fixture')
}
[IO.File]::WriteAllText((Join-Path $fixture 'scripts\bootstrap-python.ps1'), @'
param([switch]$FunctionsOnly)
function Get-AivdBootstrapPythonStatus { param([string]$DataDir) [pscustomobject]@{ ready=$global:SetupTestPythonReady; path=(Join-Path $DataDir 'runtime\python311-nuget\python.exe'); detail='fixture' } }
function Initialize-AivdBootstrapPython { param([string]$DataDir,[switch]$Offline,[switch]$EngineLeaseOwned) $global:SetupTestCalls.Add('python'); Join-Path $DataDir 'runtime\python311-nuget\python.exe' }
function Assert-AivdDevPortFree { }
function Assert-LocalDirectory { param([string]$Path) $Path }
function Assert-NoReparsePath { param([string]$Path) }
'@)
[IO.File]::WriteAllText((Join-Path $fixture 'scripts\bootstrap-node.ps1'), @'
param([switch]$CheckOnly, [switch]$Offline)
if ($CheckOnly -and -not $global:SetupTestNodeReady) { throw 'Node missing' }
if (-not $CheckOnly) { $global:SetupTestCalls.Add('node') }
Join-Path (Split-Path -Parent $PSScriptRoot) '.dev\tools\node\node.exe'
'@)
[IO.File]::WriteAllText((Join-Path $fixture 'scripts\webview2.ps1'), @'
function Get-AivdWebView2Status { [pscustomobject]@{ installed=$true; version='fixture'; scope='user' } }
function Install-AivdWebView2 { param([string]$DataDir) throw 'An installed runtime must never be installed again.' }
'@)
[IO.File]::WriteAllText((Join-Path $fixture 'scripts\toolchain\env.ps1'), '$env:CARGO_HOME = ''fixture''; $env:PATH = ''fixture;'' + $env:PATH')
$global:SetupTestCalls = [Collections.Generic.List[string]]::new()
$global:SetupTestPythonReady = $false
$global:SetupTestNodeReady = $false
$global:SetupTestFailStage = ''
function Invoke-AivdSetupCommand {
    param([string]$Executable, [string[]]$ArgumentList, [string]$WorkingDirectory, [string]$InputText = '')
    $stage = if ($ArgumentList -contains 'ci') { 'npm' } elseif ($ArgumentList -contains '-InstallMissing') { 'ai' } elseif (($ArgumentList -join ' ') -match 'check-deps') { 'ai-check' } else { 'native' }
    $global:SetupTestCalls.Add($stage)
    if ($stage -eq $global:SetupTestFailStage) { throw "Simulated $stage failure" }
    if ($stage -eq 'ai') {
        $request = $InputText | ConvertFrom-Json
        Assert-Setup ($request.settings.dev_mode -and $request.data_dir -eq (Join-Path $fixture '.dev')) 'AI setup must stay inside the requested clone.'
        $locked = $false
        try { $probe = [IO.File]::Open((Join-Path $fixture '.dev\engine.lock'), 'Open', 'ReadWrite', 'None'); $probe.Dispose() } catch [IO.IOException] { $locked = $true }
        Assert-Setup $locked 'AI installation must hold the engine lease to avoid modifying an in-use venv.'
    }
}

$check = Invoke-AivdDevSetup -RepoRoot $fixture -CheckOnly
Assert-Setup (-not $check.ready -and $global:SetupTestCalls.Count -eq 0) 'Read-only inspection must not install missing tools.'
Write-Output 'PASS: missing-prerequisite inspection does not install anything'

$beforePath = $env:PATH
$null = Invoke-AivdDevSetup -RepoRoot $fixture -SkipAi
Assert-Setup (($global:SetupTestCalls -join ',') -eq 'python,node,native,npm') 'SkipAi must install build prerequisites without invoking AI setup.'
Assert-Setup ($env:PATH -eq $beforePath) 'Setup must restore the caller environment.'
Write-Output 'PASS: source paths with spaces, build-stage ordering, SkipAi and environment restoration'

$global:SetupTestCalls.Clear()
$null = Invoke-AivdDevSetup -RepoRoot $fixture
Assert-Setup (($global:SetupTestCalls -join ',') -eq 'python,node,native,npm,ai,ai-check') 'Full setup must install and check AI after build prerequisites.'
Write-Output 'PASS: full setup prepares AI with a contained request and protects the venv'

$global:SetupTestCalls.Clear()
$global:SetupTestFailStage = 'npm'
$rejected = $false
try { $null = Invoke-AivdDevSetup -RepoRoot $fixture } catch { $rejected = $_.Exception.Message -eq 'Simulated npm failure' }
Assert-Setup ($rejected -and -not $global:SetupTestCalls.Contains('ai') -and $env:PATH -eq $beforePath) 'A failed prerequisite must stop setup and restore environment.'
$global:SetupTestFailStage = ''
Write-Output 'PASS: prerequisite failure stops downstream installation and preserves the error'

$global:SetupTestCalls.Clear()
$held = [IO.File]::Open((Join-Path $fixture '.dev\engine.lock'), 'OpenOrCreate', 'ReadWrite', 'ReadWrite')
try {
    $rejected = $false
    try { $null = Invoke-AivdDevSetup -RepoRoot $fixture } catch { $rejected = $true }
    Assert-Setup ($rejected -and $global:SetupTestCalls.Count -eq 0) 'An active engine must block full setup before it mutates any runtime.'
} finally { $held.Dispose() }
Write-Output 'PASS: active processing blocks installation before runtime changes'
