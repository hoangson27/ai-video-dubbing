$ErrorActionPreference = 'Stop'
$scripts = Split-Path -Parent $PSScriptRoot
. (Join-Path $scripts 'install-deps.ps1') -FunctionsOnly
. (Join-Path $scripts 'dependency-inventory.ps1')
function Assert-Setup([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }

foreach ($path in @('relative', '\\server\share', '')) {
    $rejected = $false
    try { $null = Assert-LocalDirectory $path } catch { $rejected = $true }
    Assert-Setup $rejected 'Setup must reject nonlocal data directories.'
}
foreach ($model in @('qwen2.5:7b-cloud', 'qwen2.5:cloud', 'qwen; calc.exe', 'other:7b')) {
    $rejected = $false
    try { $null = Assert-InstallSettings @{ ollama_model = $model } } catch { $rejected = $true }
    Assert-Setup $rejected 'Only supported local model names may reach the CLI.'
}
$choice = Assert-InstallSettings @{}
Assert-Setup ($choice.whisper -eq 'small' -and $choice.qwen -eq 'qwen2.5:3b') 'Fresh setup must select the lighter 3B model.'
$explicitChoice = Assert-InstallSettings @{ ollama_model = 'qwen2.5:7b' }
Assert-Setup ($explicitChoice.qwen -eq 'qwen2.5:7b') 'An explicitly selected 7B model must be preserved.'
$stageChoice = Assert-InstallSettings @{ hardware_mode = 'cpu'; tts_device = 'gpu'; asr_device = 'auto'; translation_device = 'cpu' }
Assert-Setup ($stageChoice.tts_mode -eq 'gpu' -and $stageChoice.asr_mode -eq 'auto' -and $stageChoice.translation_mode -eq 'cpu') 'Independent stage devices must control dependency preparation.'
Write-Output 'PASS: local paths and model names validated'

# This runs under Windows PowerShell 5.1 with a real executable, not a probe mock.
$probeCandidates = @((Join-Path $env:LOCALAPPDATA 'com.aivideodubbing.desktop\runtime\python311-nuget\python.exe'), (Join-Path $env:LOCALAPPDATA 'com.aivideodubbing.desktop\runtime\python311\python.exe'))
foreach ($version in '3.11', '3.11-64') {
    $key = Get-Item -LiteralPath "HKCU:\Software\Python\PythonCore\$version\InstallPath" -ErrorAction SilentlyContinue
    if ($key -and $key.GetValue('')) { $probeCandidates += Join-Path $key.GetValue('') 'python.exe' }
}
$realPython = $probeCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if ($realPython) {
    $selected = Get-CompatiblePython @($realPython)
    Assert-Setup ($selected -eq [IO.Path]::GetFullPath($realPython)) 'Installed Python3.11 must pass the actual quoted architecture probe under Windows PowerShell5.1.'
    Write-Output 'PASS: real Python3.11 argument quoting under Windows PowerShell5.1'
}

$fixtureRoot = Join-Path (Split-Path -Parent $scripts) ('.verification\setup-tests\' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $fixtureRoot -Force
$savedPythonProbe = (Get-Command Get-CompatiblePython).ScriptBlock
$savedInstallCommand = (Get-Command Invoke-InstallCommand).ScriptBlock
$savedArchiveHash = (Get-Command Assert-ArchiveHash).ScriptBlock
$savedInventoryProcess = (Get-Command Invoke-InventoryProcess).ScriptBlock
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
function New-PythonArchive {
    param([string]$Path, [string[]]$ExtraEntries = @(), [int]$ExtraAttributes = 0)
    $zip = [IO.Compression.ZipFile]::Open($Path, [IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($name in @('tools/python.exe', 'tools/python311.dll', 'tools/Lib/venv/__init__.py', 'tools/Lib/ensurepip/__init__.py') + $ExtraEntries) {
            $entry = $zip.CreateEntry($name)
            if ($name -in $ExtraEntries) { $entry.ExternalAttributes = $ExtraAttributes }
            $writer = [IO.StreamWriter]::new($entry.Open())
            try { $writer.Write('fixture runtime') } finally { $writer.Dispose() }
        }
    } finally { $zip.Dispose() }
}
$portableArchive = Join-Path $fixtureRoot 'python.3.11.9.nupkg.zip'
New-PythonArchive $portableArchive
$script:FixtureArchiveHash = (Get-FileHash -LiteralPath $portableArchive -Algorithm SHA256).Hash
function Assert-ArchiveHash {
    param($Path, $Expected)
    Assert-Setup ($Expected -eq '9283876D58C017E0E846F95B490DA3BCA0FC0A6EE1134B2870677CFB7EEC3C67') 'Portable Python must require the pinned official package hash.'
    Assert-Setup ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -eq $script:FixtureArchiveHash) 'Unexpected fixture archive.'
}
$script:SignaturePaths = @()
$script:RejectSignature = ''
function Get-AuthenticodeSignature {
    param($LiteralPath)
    $script:SignaturePaths += $LiteralPath
    $certificate = [pscustomobject]@{}
    $certificate | Add-Member -MemberType ScriptMethod -Name GetNameInfo -Value { param($Type,$Issuer); 'Python Software Foundation' }
    [pscustomobject]@{ Status = $(if ($LiteralPath -like $script:RejectSignature -and $script:RejectSignature) { 'NotSigned' } else { 'Valid' }); SignerCertificate = $certificate }
}
$script:PortableProbes = @()
$script:RejectPortableProbe = $false
function Invoke-InventoryProcess {
    param($Executable, $Arguments, $TimeoutMs)
    Assert-Setup ($Executable -in $script:SignaturePaths -and (Join-Path (Split-Path -Parent $Executable) 'python311.dll') -in $script:SignaturePaths) 'The interpreter and DLL must be signature checked before any execution.'
    Assert-Setup ($Arguments[0] -eq '-I' -and $Arguments[-1] -match '\(3,\s*11,\s*9\)' -and $Arguments[-1] -match 'ensurepip' -and $Arguments[-1] -match 'venv' -and $Arguments[-1] -match 'calcsize' -and $Arguments[-1] -match 'base_prefix') 'The portable runtime probe must validate its exact version, architecture, stdlib, and base path in isolated mode.'
    $script:PortableProbes += $Executable
    [pscustomobject]@{ exit_code = $(if ($script:RejectPortableProbe) { 1 } else { 0 }); stdout = ''; stderr = '' }
}
function Invoke-InstallCommand { throw 'Portable Python must never invoke an MSI or native installer.' }
$portableDir = Join-Path $fixtureRoot 'runtime\python311-nuget'
$null = New-Item -ItemType Directory -Path $portableDir -Force
[IO.File]::WriteAllText((Join-Path $portableDir 'keep.txt'), 'incomplete runtime')
$portablePython = Install-PortablePython $portableArchive $portableDir
Assert-Setup ($portablePython -eq (Join-Path $portableDir 'python.exe') -and (Test-Path -LiteralPath $portablePython -PathType Leaf)) 'Portable Python must be promoted into the managed runtime.'
$backups = @(Get-ChildItem -LiteralPath (Split-Path -Parent $portableDir) -Directory -Filter 'python311-nuget-backup-*')
Assert-Setup ($backups.Count -eq 1 -and [IO.File]::ReadAllText((Join-Path $backups[0].FullName 'keep.txt')) -eq 'incomplete runtime') 'An invalid managed runtime must be preserved intact.'
$receipt = Get-Content -LiteralPath (Join-Path $portableDir '.ai-video-dubbing-python.json') -Raw | ConvertFrom-Json
Assert-Setup ($receipt.version -eq '3.11.9' -and $receipt.source -eq 'https://api.nuget.org/v3-flatcontainer/python/3.11.9/python.3.11.9.nupkg' -and $receipt.sha256 -eq '9283876D58C017E0E846F95B490DA3BCA0FC0A6EE1134B2870677CFB7EEC3C67') 'The installed runtime must record its provenance.'
$null = Install-PortablePython (Join-Path $fixtureRoot 'missing-cache.zip') $portableDir
Assert-Setup (@(Get-ChildItem -LiteralPath (Split-Path -Parent $portableDir) -Directory -Filter 'python311-nuget-backup-*').Count -eq 1) 'A verified compatible runtime must be reused without replacing it or downloading again.'
Write-Output 'PASS: portable Python validates signatures before execution, preserves incomplete runtime and reuses verified runtime'

foreach ($case in @(
    @{ name = 'traversal'; entry = '../outside.txt'; attributes = 0 },
    @{ name = 'absolute'; entry = 'C:/outside.txt'; attributes = 0 },
    @{ name = 'alias'; entry = 'tools/../outside.txt'; attributes = 0 },
    @{ name = 'duplicate'; entry = 'tools/PYTHON.EXE'; attributes = 0 },
    @{ name = 'symlink'; entry = 'tools/link'; attributes = [int]0xA0000000 },
    @{ name = 'reparse'; entry = 'tools/link'; attributes = 1024 }
)) {
    $unsafe = Join-Path $fixtureRoot ($case.name + '.zip')
    New-PythonArchive $unsafe @($case.entry) $case.attributes
    $script:FixtureArchiveHash = (Get-FileHash -LiteralPath $unsafe -Algorithm SHA256).Hash
    $target = Join-Path $fixtureRoot ($case.name + '\runtime\python311-nuget')
    $before = $script:PortableProbes.Count
    $rejected = $false
    try { $null = Install-PortablePython $unsafe $target } catch { $rejected = $_.Exception.Message -match 'archive|unsafe|entry|ZIP|gói|an toàn|không hợp lệ' }
    Assert-Setup ($rejected -and -not (Test-Path -LiteralPath $target) -and $script:PortableProbes.Count -eq $before) "Unsafe $($case.name) archive must be rejected before execution or promotion."
}
Assert-Setup (-not (Test-Path -LiteralPath (Join-Path $fixtureRoot 'outside.txt'))) 'Archive traversal must not create files outside extraction staging.'
Write-Output 'PASS: archive traversal, absolute paths, duplicate names, symlinks and reparse entries are rejected before execution'

$script:FixtureArchiveHash = (Get-FileHash -LiteralPath $portableArchive -Algorithm SHA256).Hash
$script:RejectSignature = '*python311.dll'
$target = Join-Path $fixtureRoot 'bad-signature\runtime\python311-nuget'
$before = $script:PortableProbes.Count
$rejected = $false
try { $null = Install-PortablePython $portableArchive $target } catch { $rejected = $_.Exception.Message -match 'Chữ ký' }
Assert-Setup ($rejected -and -not (Test-Path -LiteralPath $target) -and $script:PortableProbes.Count -eq $before) 'A DLL signature failure must prevent all Python execution.'
$script:RejectSignature = ''
$script:RejectPortableProbe = $true
$target = Join-Path $fixtureRoot 'bad-probe\runtime\python311-nuget'
$null = New-Item -ItemType Directory -Path $target -Force
[IO.File]::WriteAllText((Join-Path $target 'keep.txt'), 'old runtime')
$rejected = $false
try { $null = Install-PortablePython $portableArchive $target } catch { $rejected = $true }
Assert-Setup ($rejected -and [IO.File]::ReadAllText((Join-Path $target 'keep.txt')) -eq 'old runtime') 'A failed staged version/stdlib probe must preserve the existing runtime in place.'
$script:RejectPortableProbe = $false
$rejected = $false
try { $null = Install-PortablePython $portableArchive (Join-Path $fixtureRoot 'user-files') } catch { $rejected = $true }
Assert-Setup ($rejected -and -not (Test-Path -LiteralPath (Join-Path $fixtureRoot 'user-files'))) 'The portable installer must reject arbitrary destination names.'
$junctionRoot = Join-Path $fixtureRoot 'junction-target'
$null = New-Item -ItemType Directory -Path $junctionRoot -Force
[IO.File]::WriteAllText((Join-Path $junctionRoot 'keep.txt'), 'user data')
$junctionParent = Join-Path $fixtureRoot 'junction-case'
$null = New-Item -ItemType Directory -Path $junctionParent -Force
$junctionPath = Join-Path $junctionParent 'runtime'
$syntheticReparse = $false
try { $null = New-Item -ItemType Junction -Path $junctionPath -Target $junctionRoot -ErrorAction Stop } catch {
    $cause = $_.Exception.GetBaseException()
    if ($cause -isnot [ComponentModel.Win32Exception] -or $cause.NativeErrorCode -ne 5) { throw }
    # Restricted CI sandboxes may prohibit making links. Exercise the same filesystem
    # attribute boundary with an ordinary managed directory in that environment.
    Write-Output 'SKIP: real junction creation is denied; exercising reparse attributes through a filesystem fixture'
    $null = New-Item -ItemType Directory -Path $junctionPath -Force
    $syntheticReparse = $true
    function Get-Item {
        [CmdletBinding()]param([string]$LiteralPath, [switch]$Force)
        if ($LiteralPath -eq $junctionPath) { [pscustomobject]@{ Attributes = [IO.FileAttributes]::ReparsePoint } }
        else { Microsoft.PowerShell.Management\Get-Item @PSBoundParameters }
    }
}
$before = $script:PortableProbes.Count
$rejected = $false
try { $null = Install-PortablePython $portableArchive (Join-Path $junctionParent 'runtime\python311-nuget') } catch { $rejected = $_.Exception.Message -match 'liên kết' }
Assert-Setup ($rejected -and $script:PortableProbes.Count -eq $before -and -not (Test-Path -LiteralPath (Join-Path $junctionRoot 'python311-nuget')) -and [IO.File]::ReadAllText((Join-Path $junctionRoot 'keep.txt')) -eq 'user data') 'A junction in the runtime parent must not redirect extraction or modify user data.'
if ($syntheticReparse) { Remove-Item -LiteralPath function:Get-Item }
Remove-Item -LiteralPath function:Get-AuthenticodeSignature
Set-Item -LiteralPath function:Assert-ArchiveHash -Value $savedArchiveHash
Set-Item -LiteralPath function:Invoke-InventoryProcess -Value $savedInventoryProcess
Set-Item -LiteralPath function:Invoke-InstallCommand -Value $savedInstallCommand
Write-Output 'PASS: unsigned or incompatible runtime is never promoted and arbitrary destinations are refused'
if ($realPython) {
    $script:SetupLogPath = Join-Path $fixtureRoot 'native-stream.log'
    Invoke-InstallCommand $realPython @('-X','utf8','-c','import sys; print("quoted argument",sys.argv[1]); print("original stderr",file=sys.stderr)','space " value')
    $streamLog = [IO.File]::ReadAllText($script:SetupLogPath)
    Assert-Setup ($streamLog.Contains('quoted argument') -and $streamLog.Contains('original stderr')) 'Native stdout/stderr must be streamed and persisted.'
    $rejected = $false
    try { Invoke-InstallCommand $realPython @('-c','import sys; print("native failure",file=sys.stderr);sys.exit(7)') } catch { $rejected = $_.Exception.Message -match '7' }
    Assert-Setup $rejected 'Failed native commands must preserve the real exit code.'
    Assert-Setup ([IO.File]::ReadAllText($script:SetupLogPath).Contains('native failure')) 'Native failure details must not disappear.'
    Push-Location -LiteralPath $fixtureRoot
    try { Invoke-InstallCommand $realPython @('-c','import pathlib;print("CWD="+str(pathlib.Path.cwd()))') } finally { Pop-Location }
    $streamLog = Get-Content -LiteralPath $script:SetupLogPath | ForEach-Object { ($_ | ConvertFrom-Json).message }
    Assert-Setup ([bool]($streamLog | Where-Object { $_ -eq ('CWD=' + $fixtureRoot) })) 'Native child must inherit PowerShell filesystem location, including Push-Location engine.'
    $script:SetupLogPath = ''
    Write-Output 'PASS: real native arguments, stderr, exit code and persisted log'
}
$script:CancelPath = Join-Path $fixtureRoot 'cancelled'
[IO.File]::WriteAllText($script:CancelPath, '')
$rejected = $false
try { Test-InstallCancellation } catch [OperationCanceledException] { $rejected = $true }
Assert-Setup $rejected 'Cancellation marker must stop setup before any operation.'
Remove-Item -LiteralPath $script:CancelPath
Write-Output 'PASS: cancellation stops pending work'

$cache = Join-Path $fixtureRoot 'archive.zip'
[IO.File]::WriteAllText($cache, 'partial')
$script:Downloads = 0
function Get-Download {
    param($Url, $Destination, $Label, $From, $To)
    $script:Downloads++
    [IO.File]::WriteAllText($Destination, 'verified payload')
}
$hasher = [Security.Cryptography.SHA256]::Create()
try { $hash = ([BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes('verified payload')))).Replace('-', '') } finally { $hasher.Dispose() }
Get-VerifiedDownload 'https://example.invalid/fixture.zip' $cache 'fixture' 1 2 -Hash $hash
Assert-Setup ($script:Downloads -eq 1) 'A corrupt cached archive must be refreshed.'
Get-VerifiedDownload 'https://example.invalid/fixture.zip' $cache 'fixture' 1 2 -Hash $hash
Assert-Setup ($script:Downloads -eq 1) 'A valid cache must be reused.'
Write-Output 'PASS: download verification repairs partial cache and reuses verified files'

$venv = Join-Path $fixtureRoot 'venv'
$null = New-Item -ItemType Directory -Path (Join-Path $venv 'Scripts') -Force
[IO.File]::WriteAllText((Join-Path $venv 'Scripts\python.exe'), 'broken')
function Get-CompatiblePython {
    param($Candidates)
    foreach ($candidate in $Candidates) {
        if ((Test-Path -LiteralPath $candidate -PathType Leaf) -and [IO.File]::ReadAllText($candidate) -eq 'compatible') { return $candidate }
    }
}
$script:Creates = 0
function Invoke-InstallCommand {
    param($Executable, $Arguments)
    $script:Creates++
    $destination = $Arguments[-1]
    $null = New-Item -ItemType Directory -Path (Join-Path $destination 'Scripts') -Force
    [IO.File]::WriteAllText((Join-Path $destination 'Scripts\python.exe'), 'compatible')
}
$actual = Initialize-InstallVenv 'fixture-base-python.exe' $fixtureRoot
Assert-Setup ($actual -eq (Join-Path $venv 'Scripts\python.exe')) 'Setup must return the managed venv path.'
Assert-Setup ([bool](Get-ChildItem -LiteralPath $fixtureRoot -Directory -Filter 'venv-backup-*')) 'Broken app-owned venv must be retained during repair.'
$null = Initialize-InstallVenv 'fixture-base-python.exe' $fixtureRoot
Assert-Setup ($script:Creates -eq 1) 'A compatible venv must not be recreated.'
Write-Output 'PASS: incompatible managed venv is repaired and retry is idempotent'

# Execute the real orchestration body in a child process with offline acquisition stubs.
$scenario = Join-Path $fixtureRoot 'scenario'
$null = New-Item -ItemType Directory -Path (Join-Path $scenario 'scripts'), (Join-Path $scenario 'engine') -Force
[IO.File]::WriteAllText((Join-Path $scenario 'engine\requirements.txt'), '# fixture')
$installerPath = Join-Path $scripts 'install-deps.ps1'
$tokens = $null; $parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($installerPath, [ref]$tokens, [ref]$parseErrors)
Assert-Setup ($parseErrors.Count -eq 0) 'Installer syntax is invalid.'
$main = $ast.EndBlock.Statements | Where-Object { $_ -is [Management.Automation.Language.TryStatementAst] } | Select-Object -Last 1
$stub = @'
param([switch]$InstallMissing, [switch]$HoldLease)
$approved = $InstallMissing
. '__INSTALLER__' -FunctionsOnly
$InstallMissing = $approved
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$script:CancelPath = ''
$script:SetupLogPath = ''
$setupLease = $null
$ownedServer = $null
function Get-ExistingPython {
    if ($HoldLease) {
        [IO.File]::WriteAllText((Join-Path $dataDir 'lease-held'), '')
        $timer = [Diagnostics.Stopwatch]::StartNew()
        while (-not (Test-Path -LiteralPath (Join-Path $dataDir 'release-lease'))) {
            if ($timer.Elapsed.TotalSeconds -gt 25) { throw 'Fixture lease wait timed out.' }
            Start-Sleep -Milliseconds 50
        }
    }
    $null
}
function Get-VerifiedDownload {
    param($Url,$Destination,$Label,$From,$To,$Publisher,$Hash)
    [IO.File]::WriteAllText((Join-Path $dataDir 'acquisition-started'), '')
    if ($Label -eq 'Python 3.11 x64') {
        if ($Url -ne 'https://api.nuget.org/v3-flatcontainer/python/3.11.9/python.3.11.9.nupkg' -or $Hash -ne '9283876D58C017E0E846F95B490DA3BCA0FC0A6EE1134B2870677CFB7EEC3C67' -or (Split-Path -Leaf $Destination) -ne 'python.3.11.9.nupkg.zip') { throw 'Python must use the pinned official NuGet archive.' }
        Copy-Item -LiteralPath '__ARCHIVE__' -Destination $Destination
    } else { [IO.File]::WriteAllText($Destination,'fixture') }
}
function Assert-ArchiveHash {
    param($Path,$Expected)
    if ($Expected -ne '9283876D58C017E0E846F95B490DA3BCA0FC0A6EE1134B2870677CFB7EEC3C67' -or (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne '__HASH__') { throw 'Wrong Python archive.' }
}
function Get-AuthenticodeSignature {
    param($LiteralPath)
    $certificate = [pscustomobject]@{}
    $certificate | Add-Member -MemberType ScriptMethod -Name GetNameInfo -Value { param($Type,$Issuer); 'Python Software Foundation' }
    [pscustomobject]@{Status='Valid';SignerCertificate=$certificate}
}
function Invoke-InventoryProcess {
    param($Executable,$Arguments,$TimeoutMs)
    if ($Arguments[0] -ne '-I') { throw 'Portable Python must be isolated while probing.' }
    [pscustomobject]@{exit_code=0;stdout='';stderr=''}
}
function Get-CompatiblePython { param($Candidates); $Candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1 }
function Get-ExistingTool {
    param($Candidates,$Arguments,$Pattern)
    if ($Pattern -match 'ollama') {
        $tool = Join-Path $dataDir 'tools\ollama\ollama.exe'
        $null = New-Item -ItemType Directory -Path (Split-Path -Parent $tool) -Force
        [IO.File]::WriteAllText($tool,'fixture')
        return $tool
    }
    $Candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
}
function Copy-ToolArchive {
    param($Archive,$Destination,$Executable)
    $null = New-Item -ItemType Directory -Path (Join-Path $Destination 'bin') -Force
    foreach ($name in 'ffmpeg.exe','ffprobe.exe') { [IO.File]::WriteAllText((Join-Path $Destination "bin\$name"),'fixture') }
}
function Invoke-InstallCommand {
    param($Executable,$Arguments)
    Test-InstallCancellation
    if ($Executable -match '(?i)msiexec|setup\.exe|python-3\.' -or $Arguments -contains '/repair' -or $Arguments -contains '/quiet') { throw 'Python setup must never invoke MSI.' }
    if ($Arguments -contains 'venv') {
        $target = Join-Path $Arguments[-1] 'Scripts'
        $null = New-Item -ItemType Directory -Path $target -Force
        [IO.File]::WriteAllText((Join-Path $target 'python.exe'),'fixture')
    }
    Write-InstallLog 'offline fixture completed a dependency step'
}
function Get-LocalOllamaResponse { [pscustomobject]@{models=@()} }
'@
$runner = Join-Path $scenario 'scripts\fixture-setup.ps1'
$stub = $stub.Replace('__INSTALLER__', $installerPath.Replace("'", "''")).Replace('__ARCHIVE__', $portableArchive.Replace("'", "''")).Replace('__HASH__', $script:FixtureArchiveHash)
[IO.File]::WriteAllText($runner, $stub + "`r`n" + $main.Extent.Text, [Text.UTF8Encoding]::new($true))
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$request = @{data_dir=(Join-Path $scenario 'data'); settings=@{hardware_mode='cpu';ollama_model='qwen2.5:1.5b'}} | ConvertTo-Json -Compress -Depth 5
$run = Invoke-InventoryProcess $powershell @('-NoProfile','-ExecutionPolicy','Bypass','-File',$runner,'-InstallMissing') -InputText $request -TimeoutMs 30000
Assert-Setup ($run.exit_code -eq 0) "Offline setup orchestration failed: $($run.stderr) $($run.stdout)"
$events = @($run.stdout -split '\r?\n' | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
Assert-Setup ($events[-1].type -eq 'result') 'Setup must return a structured terminal result.'
Assert-Setup ($events[-1].data.settings_patch.python_path -like '*venv\Scripts\python.exe') 'Setup did not return the managed runtime.'
Assert-Setup (Test-Path -LiteralPath (Join-Path $scenario 'data\runtime\python311-nuget\.ai-video-dubbing-python.json') -PathType Leaf) 'Full setup must provision the independent portable runtime before creating the venv.'
Assert-Setup ([bool]($events | Where-Object { $_.type -eq 'progress' -and $_.progress -ge 98 })) 'Setup did not report completion progress.'
Write-Output 'PASS: full setup orchestration emits progress and managed paths without real installs'

$concurrentData = Join-Path $scenario 'concurrent-data'
$concurrentRequest = @{data_dir=$concurrentData; settings=@{hardware_mode='cpu'}} | ConvertTo-Json -Compress -Depth 5
$holder = New-Object Diagnostics.Process
$holder.StartInfo = New-Object Diagnostics.ProcessStartInfo
$holder.StartInfo.FileName = $powershell
$holder.StartInfo.Arguments = (@('-NoProfile','-ExecutionPolicy','Bypass','-File',$runner,'-InstallMissing','-HoldLease') | ForEach-Object { ConvertTo-ProcessArgument $_ }) -join ' '
$holder.StartInfo.UseShellExecute = $false
$holder.StartInfo.CreateNoWindow = $true
$holder.StartInfo.RedirectStandardInput = $true
$holder.StartInfo.RedirectStandardOutput = $true
$holder.StartInfo.RedirectStandardError = $true
try {
    Assert-Setup ($holder.Start()) 'Could not start the offline setup lease holder.'
    $holderOutput = $holder.StandardOutput.ReadToEndAsync()
    $holderErrors = $holder.StandardError.ReadToEndAsync()
    $holder.StandardInput.Write($concurrentRequest)
    $holder.StandardInput.Close()
    $timer = [Diagnostics.Stopwatch]::StartNew()
    while (-not (Test-Path -LiteralPath (Join-Path $concurrentData 'lease-held'))) {
        Assert-Setup (-not $holder.HasExited -and $timer.Elapsed.TotalSeconds -lt 10) 'Setup fixture did not reach the lease barrier.'
        Start-Sleep -Milliseconds 50
    }
    $activeLog = Join-Path $concurrentData 'logs\setup-latest.log'
    $activeLogBefore = [IO.File]::ReadAllText($activeLog)
    $second = Invoke-InventoryProcess $powershell @('-NoProfile','-ExecutionPolicy','Bypass','-File',$runner,'-InstallMissing') -InputText $concurrentRequest -TimeoutMs 10000
    $secondEvents = @($second.stdout -split '\r?\n' | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
    Assert-Setup ($second.exit_code -eq 1 -and $secondEvents.Count -eq 1 -and $secondEvents[0].type -eq 'error' -and $secondEvents[0].message -match 'thiết lập.*đang chạy') 'Overlapping setup must return a clear structured busy error.'
    Assert-Setup ([IO.File]::ReadAllText($activeLog) -eq $activeLogBefore) 'Rejected setup must not append to or truncate the active setup log.'
    Assert-Setup (-not (Test-Path -LiteralPath (Join-Path $concurrentData 'acquisition-started'))) 'Rejected setup must stop before dependency acquisition.'
    [IO.File]::WriteAllText((Join-Path $concurrentData 'release-lease'), '')
    Assert-Setup ($holder.WaitForExit(15000)) 'The lease holder did not finish after release.'
    Assert-Setup ($holder.ExitCode -eq 0) "The setup lease holder failed: $($holderErrors.GetAwaiter().GetResult()) $($holderOutput.GetAwaiter().GetResult())"
} finally {
    if (-not $holder.HasExited) { $holder.Kill(); $holder.WaitForExit() }
    $holder.Dispose()
}
$retry = Invoke-InventoryProcess $powershell @('-NoProfile','-ExecutionPolicy','Bypass','-File',$runner,'-InstallMissing') -InputText $concurrentRequest -TimeoutMs 30000
Assert-Setup ($retry.exit_code -eq 0) "Setup must release its file lease so a later attempt can run: $($retry.stderr) $($retry.stdout)"
Write-Output 'PASS: concurrent setup is rejected before downloads without changing the active log, and completion releases its lease'

$run = Invoke-InventoryProcess $powershell @('-NoProfile','-ExecutionPolicy','Bypass','-File',$installerPath) -InputText '{}' -TimeoutMs 10000
$terminal = $run.stdout.Trim() | ConvertFrom-Json
Assert-Setup ($run.exit_code -eq 1 -and $terminal.type -eq 'error') 'Installer must require explicit installation consent.'
Write-Output 'PASS: installation is never implicit'

# Exercise the project-local branch through the same real orchestration body.
# Downloads and the owned daemon are fixtures; archive validation/promotion is real.
$ollamaFixture = Join-Path $fixtureRoot 'ollama-fixture.zip'
$zip = [IO.Compression.ZipFile]::Open($ollamaFixture, [IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($name in 'ollama.exe','lib/ollama/fixture.dll') {
        $writer = [IO.StreamWriter]::new($zip.CreateEntry($name).Open())
        try { $writer.Write('portable Ollama fixture') } finally { $writer.Dispose() }
    }
} finally { $zip.Dispose() }
$ollamaFixtureHash = (Get-FileHash -LiteralPath $ollamaFixture -Algorithm SHA256).Hash
$devOverrides = @'
function Assert-FixtureDevEnvironment {
    if ($env:OLLAMA_HOST -ne '127.0.0.1:11435' -or $env:OLLAMA_MODELS -ne (Join-Path $dataDir 'models\ollama') -or $env:USERPROFILE -ne (Join-Path $dataDir 'profile') -or $env:PIP_CACHE_DIR -ne (Join-Path $dataDir 'cache\pip') -or $env:TEMP -ne (Join-Path $dataDir 'temp')) { throw 'Dependency setup escaped the local environment.' }
}
function Get-Command { throw 'Dev setup must not consult PATH command discovery.' }
function Get-VerifiedDownload {
    param($Url,$Destination,$Label,$From,$To,$Publisher,$Hash)
    Assert-FixtureDevEnvironment
    if ($Destination -match 'OllamaSetup|\.exe$') { throw 'Dev setup must never download a system installer.' }
    if ($Label -eq 'Python 3.11 x64') { Copy-Item -LiteralPath '__ARCHIVE__' -Destination $Destination }
    elseif ($Label -eq 'Ollama portable') {
        if ($Url -ne 'https://github.com/ollama/ollama/releases/download/v0.40.16/ollama-windows-amd64.zip' -or $Hash -ne '50fcf5ad54277f2fa5d173ebfc93bcb017adbc0c9ef2314daabc96ca9e6aa64e') { throw 'Dev setup requires the pinned official portable Ollama ZIP.' }
        Copy-Item -LiteralPath '__OLLAMA_ARCHIVE__' -Destination $Destination
    } else { [IO.File]::WriteAllText($Destination,'fixture') }
}
function Assert-ArchiveHash {
    param($Path,$Expected)
    if ($Expected -eq '9283876D58C017E0E846F95B490DA3BCA0FC0A6EE1134B2870677CFB7EEC3C67') { $actual = '__HASH__' }
    elseif ($Expected -eq '50fcf5ad54277f2fa5d173ebfc93bcb017adbc0c9ef2314daabc96ca9e6aa64e') { $actual = '__OLLAMA_HASH__' }
    else { throw 'Unexpected portable archive pin.' }
    if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $actual) { throw 'Fixture archive hash mismatch.' }
}
function Get-AuthenticodeSignature {
    param($LiteralPath)
    $certificate = [pscustomobject]@{ Publisher = $(if ($LiteralPath -match 'ollama') { 'Ollama Inc.' } else { 'Python Software Foundation' }) }
    $certificate | Add-Member -MemberType ScriptMethod -Name GetNameInfo -Value { param($Type,$Issuer); $this.Publisher }
    [pscustomobject]@{Status='Valid';SignerCertificate=$certificate}
}
function Get-ExistingTool {
    param($Candidates,$Arguments,$Pattern)
    Assert-FixtureDevEnvironment
    foreach ($candidate in $Candidates) { if ($candidate -and -not $candidate.StartsWith($dataDir + '\', [StringComparison]::OrdinalIgnoreCase)) { throw "External tool candidate: $candidate" } }
    $Candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
}
function Assert-AivdDevPortFree { }
$script:ServerStarted = $false
function Get-LocalOllamaResponse { if (-not $script:ServerStarted) { throw 'Dev setup probed an unknown daemon.' }; [pscustomobject]@{models=@()} }
function Start-Process {
    param($FilePath,$ArgumentList,$WindowStyle,[switch]$PassThru,$RedirectStandardOutput,$RedirectStandardError)
    Assert-FixtureDevEnvironment
    if ($FilePath -ne (Join-Path $dataDir 'tools\ollama\ollama.exe') -or $ArgumentList[0] -ne 'serve' -or $WindowStyle -ne 'Hidden') { throw 'Dev setup must start only the local hidden portable daemon.' }
    $script:ServerStarted = $true
    $server = [pscustomobject]@{Id=12345;HasExited=$false}
    $server | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value { param($Timeout); $this.HasExited = $true; $true }
    $server
}
function Test-AivdOwnedOllamaEndpoint { param($ServerProcessId); if ($ServerProcessId -ne 12345) { throw 'Unexpected daemon owner.' }; $true }
function Stop-Process { param($Id); if ($Id -ne 12345) { throw 'Setup attempted to stop a foreign process.' } }
'@
$devOverrides = $devOverrides.Replace('__ARCHIVE__', $portableArchive.Replace("'", "''")).Replace('__HASH__', $script:FixtureArchiveHash).Replace('__OLLAMA_ARCHIVE__', $ollamaFixture.Replace("'", "''")).Replace('__OLLAMA_HASH__', $ollamaFixtureHash)
$devRunner = Join-Path $scenario 'scripts\fixture-dev-setup.ps1'
[IO.File]::WriteAllText($devRunner, $stub + "`r`n" + $devOverrides + "`r`n" + $main.Extent.Text, [Text.UTF8Encoding]::new($true))
$devRoot = Join-Path $scenario '.dev'
$devRequest = @{data_dir='C:\poison\data';settings=@{dev_mode=$true;dev_root=$devRoot;python_path='C:\poison\python.exe';ffmpeg_path='C:\poison\ffmpeg.exe';tts_model_path='C:\poison\weights';ollama_host='https://cloud.invalid';hardware_mode='cpu'}} | ConvertTo-Json -Compress -Depth 5
$run = Invoke-InventoryProcess $powershell @('-NoProfile','-ExecutionPolicy','Bypass','-File',$devRunner,'-InstallMissing') -InputText $devRequest -TimeoutMs 30000
Assert-Setup ($run.exit_code -eq 0) "Offline dev setup failed: $($run.stderr) $($run.stdout)"
$events = @($run.stdout -split '\r?\n' | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
$result = $events[-1]
Assert-Setup ($result.type -eq 'result' -and $result.data.ollama_path -eq (Join-Path $devRoot 'tools\ollama\ollama.exe') -and $result.data.settings_patch.python_path -eq (Join-Path $devRoot 'venv\Scripts\python.exe')) 'Dev setup must return only managed executable paths.'
Assert-Setup (Test-Path -LiteralPath (Join-Path $devRoot 'tools\ollama\lib\ollama\fixture.dll')) 'Dev setup must retain portable Ollama libraries.'
Assert-Setup (-not (Test-Path -LiteralPath (Join-Path $devRoot 'downloads\OllamaSetup.exe'))) 'Dev setup must not create the system installer.'
Write-Output 'PASS: full dev setup overrides hostile globals, uses pinned portable ZIP and scoped profile, and starts only its owned local daemon'
