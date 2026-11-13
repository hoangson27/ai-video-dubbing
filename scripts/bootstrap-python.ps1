[CmdletBinding()]
param([switch]$FunctionsOnly)

. (Join-Path $PSScriptRoot 'install-deps.ps1') -FunctionsOnly

function Get-AivdBootstrapPythonStatus {
    param([string]$DataDir)
    $directory = Join-Path $DataDir 'runtime\python311-nuget'
    $python = Join-Path $directory 'python.exe'
    try {
        Assert-NoReparsePath $python
        $probe = Get-PythonProbe $python
        if (-not (Test-CompatiblePython $probe) -or $probe.version -ne '3.11.9' -or $probe.venv -or [IO.Path]::GetFullPath($probe.base_prefix) -ine [IO.Path]::GetFullPath($directory)) { throw 'Missing complete project-local Python 3.11.9 x64.' }
        $native = Invoke-InventoryProcess $python @('-I','-B','-c','import _msi,tomllib,tarfile,zipfile,ssl,ctypes,venv,ensurepip') -TimeoutMs 15000
        if ($native.exit_code -ne 0) { throw 'The portable Python runtime is missing native-bootstrap modules.' }
        [pscustomobject]@{ ready = $true; path = $python; detail = $probe.version }
    } catch { [pscustomobject]@{ ready = $false; path = $python; detail = $_.Exception.Message } }
}

function Initialize-AivdBootstrapPython {
    param([string]$DataDir, [switch]$Offline, [switch]$EngineLeaseOwned)
    $DataDir = (Assert-LocalDirectory $DataDir).TrimEnd('\','/')
    Assert-NoReparsePath $DataDir
    $status = Get-AivdBootstrapPythonStatus $DataDir
    if ($status.ready) {
        $null = Assert-PortablePython (Split-Path -Parent $status.path)
        return $status.path
    }
    # Protect a base interpreter already used by a running engine, even in build-only setup.
    $engineLease = $null
    $engineLock = Join-Path $DataDir 'engine.lock'
    try {
        if (-not $EngineLeaseOwned) {
            Assert-NoReparsePath $engineLock
            New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
            try { $engineLease = [IO.File]::Open($engineLock, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None) }
            catch [IO.IOException] { throw 'Python is in use by the app. Close the app before repairing its runtime.' }
        }
        $script:CancelPath = ''
        $script:SetupLogPath = ''
        $script:CacheDir = Join-Path $DataDir 'downloads'
        Assert-NoReparsePath $script:CacheDir
        New-Item -ItemType Directory -Path $script:CacheDir -Force | Out-Null
        $archive = Join-Path $script:CacheDir 'python.3.11.9.nupkg.zip'
        if ($Offline) { Assert-ArchiveHash $archive $script:PortablePythonHash }
        else { Get-VerifiedDownload $script:PortablePythonSource $archive 'Python 3.11 x64' 0 100 -Hash $script:PortablePythonHash }
        $python = Install-PortablePython $archive (Join-Path $DataDir 'runtime\python311-nuget')
        $verified = Get-AivdBootstrapPythonStatus $DataDir
        if (-not $verified.ready) { throw $verified.detail }
        $python
    } finally { if ($engineLease) { $engineLease.Dispose() } }
}

if (-not $FunctionsOnly) {
    $data = Join-Path (Split-Path -Parent $PSScriptRoot) '.dev'
    Initialize-AivdBootstrapPython $data
}
