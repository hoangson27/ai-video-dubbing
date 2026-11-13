$ErrorActionPreference = 'Stop'
$scripts = Split-Path -Parent $PSScriptRoot
. (Join-Path $scripts 'install-deps.ps1') -FunctionsOnly
function Assert-Dev([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
$fixture = Join-Path (Split-Path -Parent $scripts) ('.verification\dev-profile-tests\' + [guid]::NewGuid().ToString('N'))
$settings = [pscustomobject]@{ dev_mode = $true; dev_root = $fixture; python_path = 'C:\poison\python.exe'; ffmpeg_path = 'C:\poison\ffmpeg.exe'; tts_model_path = 'C:\poison\weights'; projects_dir = 'C:\poison\projects'; output_dir = 'C:\poison\output'; ollama_host = 'https://cloud.example:11434'; ollama_owned = $false }
function Get-CommandFile { throw 'Dev discovery must never consult PATH.' }
$candidates = @(Get-PythonCandidates $settings $fixture)
Assert-Dev ($candidates.Count -eq 2 -and $candidates[0] -eq (Join-Path $fixture 'venv\Scripts\python.exe') -and $candidates[1] -eq (Join-Path $fixture 'runtime\python311-nuget\python.exe')) 'Dev Python discovery must ignore explicit globals, registry, launcher, and legacy runtime.'
Write-Output 'PASS: dev Python candidates are exclusively managed paths'

$profile = Get-AivdDevProfile $settings $fixture
Assert-Dev ($profile.Settings.ffmpeg_path -eq (Join-Path $fixture 'tools\ffmpeg\bin\ffmpeg.exe') -and $profile.Settings.tts_model_path -eq (Join-Path $fixture 'models\vieneu')) 'Dev profile must replace persisted global tool and model paths.'
Assert-Dev ($profile.Settings.projects_dir -eq (Join-Path $fixture 'projects') -and $profile.Settings.output_dir -eq (Join-Path $fixture 'output')) 'Dev profile must replace persisted external project and output directories.'
Assert-Dev ($profile.Environment.OLLAMA_HOST -eq '127.0.0.1:11435' -and $profile.Environment.OLLAMA_MODELS -eq (Join-Path $fixture 'models\ollama')) 'Dev Ollama must use the fixed local endpoint and weights.'
foreach ($name in 'USERPROFILE','APPDATA','LOCALAPPDATA','PIP_CACHE_DIR','HF_HOME','TORCH_HOME','XDG_CACHE_HOME','UV_CACHE_DIR','NUMBA_CACHE_DIR','GRADIO_TEMP_DIR','TEMP','TMP') {
    Assert-Dev ($profile.Environment[$name].StartsWith($fixture + '\', [StringComparison]::OrdinalIgnoreCase)) "$name must remain inside the dev root."
}
Assert-Dev (-not $profile.Environment.ContainsKey('HOME')) 'Dev profile must not override HOME.'
Assert-Dev ($profile.Environment.PIP_CONFIG_FILE -ceq 'nul') 'Pip configuration must be disabled using the exact Windows os.devnull spelling.'
foreach ($entry in @{ HF_ASSETS_CACHE='cache\huggingface\assets'; HUGGINGFACE_ASSETS_CACHE='cache\huggingface\assets'; HF_XET_CACHE='cache\huggingface\xet' }.GetEnumerator()) {
    Assert-Dev ($profile.Environment[$entry.Key] -eq (Join-Path $fixture $entry.Value)) "$($entry.Key) must use the same scoped cache as native workers."
}
Write-Output 'PASS: scoped caches, profile, models, and endpoint override hostile settings'

function Get-CompatiblePython { param($Candidates); Assert-Dev ($Candidates.Count -eq 2 -and -not ($Candidates -match 'poison|python311\\')) 'Setup must use exactly the managed dev Python candidates.'; $null }
$null = Get-ExistingPython $settings $fixture
Write-Output 'PASS: setup cannot fall back to global Python'

function Get-PythonProbe { $null }
function Find-InventoryExecutable { param($Explicit,$Candidates); Assert-Dev (-not (($Explicit + ($Candidates -join ' ')) -match 'poison|Programs\\Ollama')) 'Dev inventory tool candidates must stay local.'; '' }
function Get-LocalOllamaModels { throw 'Unowned dev service must never be contacted.' }
function Get-InventoryHardware { [pscustomobject]@{ cpu='Fixture';ram_gb=16;gpus=@();gpu_status='unchecked';cuda_devices=$null } }
$report = Invoke-DependencyInventory ([pscustomobject]@{ data_dir='C:\poison\data'; settings=$settings; engine_dir=(Join-Path (Split-Path -Parent $scripts) 'engine') })
Assert-Dev (-not $report.ready -and ($report.checks | Where-Object id -eq 'ollama_service').status -ne 'installed') 'A foreign daemon must not count as dev readiness.'
Assert-Dev (($report.setup | ConvertTo-Json -Compress) -notmatch 'poison') 'Inventory must not return external tool paths.'
Write-Output 'PASS: inventory cannot accept an unknown dev daemon'

function Get-PythonProbe { [pscustomobject]@{major=3;minor=11;version='3.11.9';bits=64;path=(Join-Path $fixture 'venv\Scripts\python.exe');venv=$true} }
function Invoke-InventoryProcess {
    $response = @{type='result';data=@{ready=$true;checks=@(@{name='Ollama';status='installed';detail='Foreign service claimed ready'},@{name='Qwen';status='installed';detail='Foreign weights'});voices=@()}} | ConvertTo-Json -Depth 8 -Compress
    [pscustomobject]@{exit_code=0;stdout=$response;stderr=''}
}
$report = Invoke-DependencyInventory ([pscustomobject]@{data_dir=$fixture;settings=$settings;engine_dir=(Join-Path (Split-Path -Parent $scripts) 'engine')})
Assert-Dev (-not $report.ready -and ($report.checks | Where-Object id -eq 'ollama_service').status -eq 'unchecked' -and ($report.checks | Where-Object id -eq 'ollama').status -eq 'missing') 'Engine readiness must not promote an unowned service or missing local CLI.'
Write-Output 'PASS: engine response cannot promote foreign dev service readiness'

$rejected = $false
try { Assert-AivdDevPortFree { $true } } catch { $rejected = $_.Exception.Message -match '11435.*busy|11435.*đang' }
Assert-Dev $rejected 'Standalone setup must reject an occupied dev endpoint.'
Write-Output 'PASS: standalone dev rejects occupied endpoint'

# A real subprocess receives the local profile without mutating the inventory caller.
. (Join-Path $scripts 'install-deps.ps1') -FunctionsOnly
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$originalProfile = $env:USERPROFILE
$savedEnvironment = @{}
foreach ($name in 'PYTHONHOME','PYTHONPATH','PIP_TARGET','PIP_PREFIX','PIP_USER','PIP_ROOT','PIP_LOG','PIP_BUILD_TRACKER','PIP_PYTHON','PIP_SRC','TRANSFORMERS_CACHE') {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    [Environment]::SetEnvironmentVariable($name, 'C:\poison\redirect', 'Process')
}
try { $run = Invoke-InventoryProcess $powershell @('-NoProfile','-Command','[Environment]::GetEnvironmentVariables() | ConvertTo-Json -Compress') -DevProfile $profile }
finally { foreach ($name in $savedEnvironment.Keys) { [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process') } }
$childEnvironment = $run.stdout.Trim() | ConvertFrom-Json
Assert-Dev ($run.exit_code -eq 0 -and $childEnvironment.USERPROFILE -eq $profile.Environment.USERPROFILE -and $childEnvironment.OLLAMA_HOST -eq '127.0.0.1:11435' -and $childEnvironment.TEMP -eq $profile.Environment.TEMP) 'Worker process environment must receive the dev profile.'
foreach ($name in $savedEnvironment.Keys) { Assert-Dev (-not $childEnvironment.$name) "$name must not redirect dev subprocess imports or package writes." }
Assert-Dev ($env:USERPROFILE -eq $originalProfile) 'Read-only inventory must not change the caller profile.'
Write-Output 'PASS: real subprocess environment is scoped without changing inventory caller'

New-Item -ItemType Directory -Path $fixture -Force | Out-Null
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
$archive = Join-Path $fixture 'ollama.zip'
$zip = [IO.Compression.ZipFile]::Open($archive, [IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($name in 'ollama.exe','lib/ollama/fixture.dll') {
        $entry = $zip.CreateEntry($name)
        $writer = [IO.StreamWriter]::new($entry.Open())
        try { $writer.Write('portable fixture') } finally { $writer.Dispose() }
    }
} finally { $zip.Dispose() }
$script:FixtureHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
function Assert-ArchiveHash {
    param($Path,$Expected)
    Assert-Dev ($Expected -eq '50fcf5ad54277f2fa5d173ebfc93bcb017adbc0c9ef2314daabc96ca9e6aa64e') 'Portable Ollama requires its pinned archive hash.'
    Assert-Dev ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -eq $script:FixtureHash) 'Unexpected portable Ollama archive.'
}
$script:SignedPaths = @()
$script:RejectOllamaSignature = $false
function Get-AuthenticodeSignature {
    param($LiteralPath)
    $script:SignedPaths += $LiteralPath
    $certificate = [pscustomobject]@{}
    $certificate | Add-Member -MemberType ScriptMethod -Name GetNameInfo -Value { param($Type,$Issuer); 'Ollama Inc.' }
    [pscustomobject]@{ Status = $(if ($script:RejectOllamaSignature) { 'NotSigned' } else { 'Valid' }); SignerCertificate = $certificate }
}
function Invoke-InstallCommand { throw 'Portable Ollama must never invoke a system installer.' }
$target = Join-Path $fixture 'tools\ollama'
New-Item -ItemType Directory -Path $target -Force | Out-Null
[IO.File]::WriteAllText((Join-Path $target 'keep.txt'), 'old portable installation')
$installed = Install-PortableOllama $archive $target
Assert-Dev ($installed -eq (Join-Path $target 'ollama.exe') -and (Test-Path -LiteralPath (Join-Path $target 'lib\ollama\fixture.dll'))) 'Portable Ollama must preserve its bundled libraries in the managed directory.'
$backup = @(Get-ChildItem -LiteralPath (Split-Path -Parent $target) -Directory -Filter 'ollama-backup-*')
Assert-Dev ($backup.Count -eq 1 -and [IO.File]::ReadAllText((Join-Path $backup[0].FullName 'keep.txt')) -eq 'old portable installation') 'The previous portable installation must be preserved.'
$receipt = Get-Content -LiteralPath (Join-Path $target '.ai-video-dubbing-ollama.json') -Raw | ConvertFrom-Json
Assert-Dev ($receipt.source -eq 'https://github.com/ollama/ollama/releases/download/v0.40.16/ollama-windows-amd64.zip') 'Ollama must use the official pinned portable ZIP.'
$script:RejectOllamaSignature = $true
$rejected = $false
try { $null = Install-PortableOllama $archive $target } catch { $rejected = $_.Exception.Message -match 'signature' }
Assert-Dev ($rejected -and (Test-Path -LiteralPath (Join-Path $target 'ollama.exe')) -and @(Get-ChildItem -LiteralPath (Split-Path -Parent $target) -Directory -Filter 'ollama-backup-*').Count -eq 1) 'Unsigned Ollama must be rejected before replacing the existing portable installation.'
Write-Output 'PASS: pinned portable Ollama installs locally, preserves libraries and old files, and rejects unsigned executables'

$smallTarget = Join-Path $fixture 'bounded-extract'
$rejected = $false
try { Expand-ManagedArchive $archive $smallTarget -MaxExpandedBytes 1 } catch { $rejected = $_.Exception.Message -match 'expanded size' }
Assert-Dev ($rejected -and -not (Test-Path -LiteralPath $smallTarget)) 'Generic tool extraction must validate expanded size before writing.'
$unsafeArchive = Join-Path $fixture 'unsafe-tool.zip'
$zip = [IO.Compression.ZipFile]::Open($unsafeArchive, [IO.Compression.ZipArchiveMode]::Create)
try { $null = $zip.CreateEntry('../outside.txt') } finally { $zip.Dispose() }
$rejected = $false
try { Expand-ManagedArchive $unsafeArchive (Join-Path $fixture 'unsafe-extract') } catch { $rejected = $_.Exception.Message -match 'ZIP' }
Assert-Dev ($rejected -and -not (Test-Path -LiteralPath (Join-Path $fixture 'outside.txt'))) 'Portable tool extraction must reject path traversal.'
Write-Output 'PASS: portable tool extraction validates size and traversal before writing'
