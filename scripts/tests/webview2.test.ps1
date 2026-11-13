$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$helper = Join-Path $repoRoot 'scripts\webview2.ps1'
function Assert-WebView([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }

# Merely importing bootstrap helpers must never contact the registry, disk or network.
function New-Item { throw 'Dot-source attempted filesystem mutation.' }
. $helper
Remove-Item Function:\New-Item
Assert-WebView ($null -ne (Get-Command Get-AivdWebView2Status -ErrorAction SilentlyContinue)) 'Status API is missing.'
Write-Output 'PASS: dot-source declares helpers without installing or writing'

$script:RegistryReads = @()
$script:RegistryValues = @{}
function Get-AivdWebView2RegistryValue {
    param([string]$Hive, [string]$View, [string]$KeyPath)
    $script:RegistryReads += "$Hive|$View|$KeyPath"
    $script:RegistryValues["$Hive|$View"]
}
$script:RegistryValues = @{ 'CurrentUser|Default'='0.0.0.0'; 'LocalMachine|Registry32'='146.0.3856.98'; 'LocalMachine|Registry64'='bad' }
$status = Get-AivdWebView2Status
Assert-WebView ($status.installed -and $status.version -eq '146.0.3856.98' -and $status.scope -eq 'machine') 'Machine runtime must be detected despite invalid user metadata.'
Assert-WebView ($status.registry_path -match 'WOW6432Node.*F3017226-FE2A-4295-8BDF-00C3A9A7E4C5') 'Registry location must match Microsoft documented x64 metadata.'
Assert-WebView (@($script:RegistryReads | Where-Object { $_ -notmatch 'Software\\Microsoft\\EdgeUpdate\\Clients\\\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5\}' }).Count -eq 0) 'Detection must inspect only the documented WebView2 product key.'
$script:RegistryValues = @{ 'CurrentUser|Default'='147.0.1.2'; 'LocalMachine|Registry32'='146.0.1.2' }
$status = Get-AivdWebView2Status
Assert-WebView ($status.installed -and $status.scope -eq 'user' -and $status.version -eq '147.0.1.2') 'Highest usable installed runtime should be reported.'
$script:RegistryValues = @{ 'CurrentUser|Default'=''; 'LocalMachine|Registry32'='0.0.0.0'; 'LocalMachine|Registry64'='invalid' }
$status = Get-AivdWebView2Status
Assert-WebView (-not $status.installed -and $status.version -eq '' -and $status.scope -eq 'none') 'Missing/zero/malformed runtime versions must not count as installed.'
Write-Output 'PASS: registry status accepts valid per-user/machine versions and rejects zero or malformed data'

$fixture = Join-Path $repoRoot ('.verification\webview2-tests\' + [guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($fixture) | Out-Null
$script:Installed = $true
$script:Elevated = $false
$script:Downloaded = 0
$script:Executed = 0
$script:SignatureTrusted = $true
$script:InstallerExit = 0
$script:RegistersRuntime = $true
$script:Sleeps = 0
function Get-AivdWebView2Status {
    [pscustomobject]@{ installed=$script:Installed; version=$(if ($script:Installed) {'146.0.1.2'} else {''}); scope=$(if ($script:Installed) {'user'} else {'none'}); registry_path='' }
}
function Test-AivdWebView2Elevated { $script:Elevated }
function Save-AivdWebView2Bootstrapper {
    param([string]$Destination)
    $script:Downloaded++
    Assert-WebView ($Destination.StartsWith($fixture + '\', [StringComparison]::OrdinalIgnoreCase)) 'Download must remain in its data root.'
    [IO.File]::WriteAllText($Destination, 'fixture bootstrapper')
}
function Get-AivdWebView2Signature {
    param([string]$Path)
    Assert-WebView (Test-Path -LiteralPath $Path -PathType Leaf) 'Signature validation requires an actual downloaded fixture.'
    [pscustomobject]@{ valid=$script:SignatureTrusted; publisher=$(if ($script:SignatureTrusted) {'Microsoft Corporation'} else {'Unknown Publisher'}) }
}
function Invoke-AivdWebView2Installer {
    param([string]$Path, [string]$LogPath, [string]$TempDir)
    Assert-WebView ($script:SignatureTrusted -and (Test-Path -LiteralPath $Path)) 'An unverified executable must never run.'
    Assert-WebView ($LogPath.StartsWith($fixture + '\') -and $TempDir.StartsWith($fixture + '\')) 'Installer logs/temp must stay in the app data root.'
    $script:Executed++
    if ($script:InstallerExit -eq 0 -and $script:RegistersRuntime) { $script:Installed=$true }
    $script:InstallerExit
}
function Start-Sleep { param($Milliseconds); $script:Sleeps++ }

$unused = Join-Path $fixture 'should-not-exist'
$status = Install-AivdWebView2 -DataDir $unused
Assert-WebView ($status.installed -and -not (Test-Path -LiteralPath $unused) -and $script:Downloaded -eq 0 -and $script:Executed -eq 0) 'An installed runtime must skip directory, download and process changes.'
Write-Output 'PASS: installed runtime is a complete no-op'

$script:Installed=$false
$data = Join-Path $fixture 'normal'
$status = Install-AivdWebView2 -DataDir $data
Assert-WebView ($status.installed -and $script:Downloaded -eq 1 -and $script:Executed -eq 1) 'Missing runtime should download, verify, install and recheck.'
Assert-WebView (Test-Path -LiteralPath (Join-Path $data 'logs\webview2-setup.log')) 'Installation must retain its setup log.'
Write-Output 'PASS: missing runtime uses a contained signed bootstrapper and rechecks readiness'

$script:Installed=$false
$script:SignatureTrusted=$false
$before = $script:Executed
$rejected=$false
try { $null = Install-AivdWebView2 -DataDir (Join-Path $fixture 'unsigned') } catch { $rejected=$_.Exception.Message -match 'Microsoft|signature|ch.k.' }
Assert-WebView ($rejected -and $script:Executed -eq $before) 'Untrusted publisher/signature must fail before execution.'
$script:SignatureTrusted=$true
Write-Output 'PASS: untrusted installer is rejected before any execution'

$script:Elevated=$true
$before=$script:Downloaded
$elevatedRoot=Join-Path $fixture 'elevated'
$rejected=$false
try { $null=Install-AivdWebView2 -DataDir $elevatedRoot } catch { $rejected=$_.Exception.Message -match 'Administrator|qu.n tr.' }
Assert-WebView ($rejected -and $script:Downloaded -eq $before -and -not (Test-Path -LiteralPath $elevatedRoot)) 'Missing runtime must not be installed from an elevated host.'
$script:Elevated=$false
Write-Output 'PASS: an elevated host cannot silently change installation scope'

$script:InstallerExit=1603
$rejected=$false
try { $null=Install-AivdWebView2 -DataDir (Join-Path $fixture 'failed') } catch { $rejected=$_.Exception.Message -match '1603' }
Assert-WebView ($rejected -and -not $script:Installed) 'Installer failure code must be reported and cannot return ready.'
$script:InstallerExit=0
Write-Output 'PASS: installer failure preserves its error code and never reports readiness'

$script:RegistersRuntime=$false
$rejected=$false
try { $null=Install-AivdWebView2 -DataDir (Join-Path $fixture 'unregistered') } catch { $rejected=$_.Exception.Message -match 'WebView2' }
Assert-WebView ($rejected -and -not $script:Installed -and $script:Sleeps -gt 0) 'Zero exit without runtime registration must still fail readiness.'
Write-Output 'PASS: successful installer exit alone cannot create a false ready result'

$script:RegistersRuntime=$true
$badPathRejected=$false
try { $null=Install-AivdWebView2 -DataDir 'C:\' } catch { $badPathRejected=$true }
Assert-WebView $badPathRejected 'A drive root must not be accepted as an application data directory.'
Write-Output 'PASS: unsafe data roots are rejected'

$lockedRoot = Join-Path $fixture 'busy'
[IO.Directory]::CreateDirectory((Join-Path $lockedRoot 'logs')) | Out-Null
$existingLog = Join-Path $lockedRoot 'logs\webview2-setup.log'
[IO.File]::WriteAllText($existingLog, 'Other setup owns this log.')
$lease = [IO.File]::Open((Join-Path $lockedRoot 'webview2-setup.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
$beforeDownloads=$script:Downloaded
$beforeExecutions=$script:Executed
$rejected=$false
try { $null=Install-AivdWebView2 -DataDir $lockedRoot } catch { $rejected=$true } finally { $lease.Dispose() }
Assert-WebView ($rejected -and $script:Downloaded -eq $beforeDownloads -and $script:Executed -eq $beforeExecutions) 'Concurrent setup must stop before download/installation.'
Assert-WebView ([IO.File]::ReadAllText($existingLog) -eq 'Other setup owns this log.') 'A failed setup lease must not append into another active installer log.'
Write-Output 'PASS: a concurrent setup cannot modify the active installer log or run another installer'
