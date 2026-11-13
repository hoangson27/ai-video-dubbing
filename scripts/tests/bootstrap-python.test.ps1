$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'bootstrap-python.ps1') -FunctionsOnly
$fixtureRoot = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) ('.verification\python-lease-tests\' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixtureRoot -Force | Out-Null
$script:RuntimeReady = $false
$script:DownloadCalls = 0
function Get-AivdBootstrapPythonStatus { param([string]$DataDir) [pscustomobject]@{ready=$script:RuntimeReady;path=(Join-Path $DataDir 'runtime\python.exe')} }
function Assert-PortablePython { param([string]$Directory) }
function Get-VerifiedDownload {
    param($Source, $Archive, $Label, $Start, $End, $Hash)
    $script:DownloadCalls++
    $locked = $false
    try { $probe = [IO.File]::Open((Join-Path $script:CurrentData 'engine.lock'), 'OpenOrCreate', 'ReadWrite', 'None'); $probe.Dispose() }
    catch [IO.IOException] { $locked = $true }
    if (-not $locked) { throw 'FAIL: Python repair started downloading without an exclusive engine lease.' }
    throw 'Fixture download stopped.'
}
function Assert-PythonLease($Condition, [string]$Message) { if (-not $Condition) { throw $Message } }

$script:CurrentData = Join-Path $fixtureRoot 'missing lock'
$message = ''
try { Initialize-AivdBootstrapPython $script:CurrentData } catch { $message = $_.Exception.Message }
Assert-PythonLease ($message -eq 'Fixture download stopped.' -and $script:DownloadCalls -eq 1) 'Repair must create and hold the lease before downloading.'
$probe = [IO.File]::Open((Join-Path $script:CurrentData 'engine.lock'), 'Open', 'ReadWrite', 'None'); $probe.Dispose()
Write-Output 'PASS: missing lock is created and held before repair; failure releases the lease'

$script:DownloadCalls = 0
$held = [IO.File]::Open((Join-Path $script:CurrentData 'engine.lock'), 'Open', 'ReadWrite', 'None')
try {
    $message = ''
    try { Initialize-AivdBootstrapPython $script:CurrentData } catch { $message = $_.Exception.Message }
    Assert-PythonLease ($message -match 'Python is in use' -and $script:DownloadCalls -eq 0) 'An active engine must block runtime repair before downloading.'
    $message = ''
    try { Initialize-AivdBootstrapPython $script:CurrentData -EngineLeaseOwned } catch { $message = $_.Exception.Message }
    Assert-PythonLease ($message -eq 'Fixture download stopped.' -and $script:DownloadCalls -eq 1) 'The controller-owned lease must not be acquired twice.'
} finally { $held.Dispose() }
Write-Output 'PASS: active engine blocks repair; controller-owned lease avoids nested acquisition'

$script:RuntimeReady = $true
$script:DownloadCalls = 0
$script:CurrentData = Join-Path $fixtureRoot 'ready runtime'
$null = Initialize-AivdBootstrapPython $script:CurrentData
Assert-PythonLease (-not (Test-Path -LiteralPath $script:CurrentData) -and $script:DownloadCalls -eq 0) 'A ready runtime must be reused without lock or directory writes.'
Write-Output 'PASS: ready runtime is reused without repair or lease writes'
