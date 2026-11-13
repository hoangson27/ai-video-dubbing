$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'dependency-inventory.ps1')

function Assert-Inventory {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

$runtime311 = [pscustomobject]@{ major = 3; minor = 11; version = '3.11.9'; bits = 64; path = 'C:\Python311\python.exe'; venv = $false }
$runtime312 = [pscustomobject]@{ major = 3; minor = 12; version = '3.12.10'; bits = 64; path = 'C:\Python312\python.exe'; venv = $false }
$runtime32 = [pscustomobject]@{ major = 3; minor = 11; bits = 32 }
Assert-Inventory (Test-CompatiblePython $runtime311) 'Python3.11 x64 must be compatible.'
Assert-Inventory (-not (Test-CompatiblePython $runtime312)) 'A detected3.12 must be listed without being accepted as3.11.'
Assert-Inventory (-not (Test-CompatiblePython $runtime32)) '32bit Python must not be accepted.'
Write-Output 'PASS: version and architecture compatibility'

$chosen = Select-InventoryPython @($runtime312, $runtime311)
Assert-Inventory ($chosen.path -eq $runtime311.path) 'Automatic selection must choose compatible runtime.'
$chosen = Select-InventoryPython @($runtime311, $runtime312) $runtime312.path
Assert-Inventory ($chosen.path -eq $runtime312.path) 'An explicit incompatible runtime must not be silently replaced.'
Assert-Inventory ($null -eq (Select-InventoryPython @($runtime311) 'C:\missing\python.exe')) 'Missing explicit runtime must not silently fall back.'
Write-Output 'PASS: explicit runtime choice is preserved'

$candidateData = Join-Path ([IO.Path]::GetTempPath()) 'dubbing-runtime-candidates'
$candidates = @(Get-PythonCandidates ([pscustomobject]@{ python_path = 'C:\explicit\python.exe' }) $candidateData)
$expectedCandidates = @(
    'C:\explicit\python.exe',
    (Join-Path $candidateData 'venv\Scripts\python.exe'),
    (Join-Path $candidateData 'runtime\python311-nuget\python.exe'),
    (Join-Path $candidateData 'runtime\python311\python.exe')
)
for ($index = 0; $index -lt $expectedCandidates.Count; $index++) {
    Assert-Inventory ($candidates[$index] -eq $expectedCandidates[$index]) 'Runtime discovery must prefer explicit Python, venv, portable runtime, then legacy runtime.'
}
Write-Output 'PASS: portable runtime discovery preserves explicit and venv precedence'

$fixture = Join-Path ([IO.Path]::GetTempPath()) ('dubbing-inventory-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixture | Out-Null
try {
    [IO.File]::WriteAllText((Join-Path $fixture 'config.json'), '{}')
    [IO.File]::WriteAllText((Join-Path $fixture 'model.bin'), 'weights')
    Assert-Inventory (Test-InventoryFiles $fixture @('config.json', 'model.bin')) 'Existing nonempty model files must be detected.'
    Assert-Inventory (-not (Test-InventoryFiles $fixture @('tokenizer.json'))) 'Missing model files must be reported.'
    [IO.File]::WriteAllText((Join-Path $fixture 'model.bin'), '')
    Assert-Inventory (-not (Test-InventoryFiles $fixture @('model.bin'))) 'A partial zero-byte download must not be accepted.'
} finally {
    Remove-Item -LiteralPath (Join-Path $fixture 'config.json'), (Join-Path $fixture 'model.bin') -Force
    Remove-Item -LiteralPath $fixture -Force
}
Write-Output 'PASS: model presence rejects missing and partial downloads'

$python = Get-CommandFile 'python.exe'
if ($python) {
    $arguments = @('space value', 'quote"value', 'trailing path\', '"; exit 42; #')
    $code = 'import json,sys;print(json.dumps(sys.argv[1:]))'
    $run = Invoke-InventoryProcess $python (@('-E', '-s', '-B', '-c', $code) + $arguments)
    Assert-Inventory ($run.exit_code -eq 0) 'Argument roundtrip process failed.'
    $actual = ConvertFrom-Json -InputObject $run.stdout
    Assert-Inventory ($actual.Count -eq $arguments.Count) 'Argument count changed.'
    for ($index = 0; $index -lt $arguments.Count; $index++) {
        Assert-Inventory ($actual[$index] -eq $arguments[$index]) 'Arguments must preserve spaces/quotes/backslashes and shell-like data.'
    }
    Write-Output 'PASS: process argument arrays preserve data without shell interpretation'
} else { Write-Output 'SKIP: argument roundtrip requires a Python executable on PATH' }

# Exercise the complete missing-runtime report without making any software/network changes.
function Get-PythonCandidates { @('C:\Python312\python.exe') }
function Get-PythonProbe { $runtime312 }
function Find-InventoryExecutable { '' }
function Get-CommandFile { '' }
function Get-LocalOllamaModels { [pscustomobject]@{ available = $false; models = @(); detail = 'Service stopped' } }
function Get-InventoryHardware { [pscustomobject]@{ cpu = 'Fixture CPU'; ram_gb = 16; gpus = @(); gpu_status = 'unchecked'; cuda_devices = $null } }
$request = [pscustomobject]@{ data_dir = $fixture; settings = [pscustomobject]@{}; engine_dir = (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'engine') }
$report = Invoke-DependencyInventory $request
$expected = @('python','venv','ffmpeg','ffprobe','faster-whisper','ctranslate2','av','vieneu','onnxruntime','numpy','soundfile','whisper_model','whisper_cuda','tts_model','tts_voices','ollama','ollama_service','qwen')
foreach ($id in $expected) { Assert-Inventory ([bool]($report.checks | Where-Object { $_.id -eq $id })) "Report omitted $id when Python3.11 was absent." }
$pythonRow = $report.checks | Where-Object { $_.id -eq 'python' }
Assert-Inventory ($pythonRow.status -eq 'incompatible' -and $pythonRow.detail.Contains('3.12.10')) 'Detected3.12 must be visible with clear incompatibility.'
Assert-Inventory (($report.checks | Where-Object { $_.id -eq 'qwen' }).status -eq 'unchecked') 'Stopped service must not be reported as proof of missing Qwen weights.'
Assert-Inventory (($report.checks | Where-Object { $_.id -eq 'numpy' }).status -eq 'unchecked') 'Unavailable engine must not be reported as proof of missing packages.'
Assert-Inventory (-not $report.ready) 'Bootstrap report alone cannot be ready.'
Assert-Inventory ($report.hardware.ram_gb -eq 16) 'Hardware must be available independently of Python.'
Write-Output 'PASS: full dependency inventory survives absent3.11 and stopped Ollama'

# A real Engine response replaces bootstrap guesses and remains the readiness authority.
$venvRuntime = [pscustomobject]@{ major = 3; minor = 11; version = '3.11.9'; bits = 64; path = (Join-Path $fixture 'venv\Scripts\python.exe'); venv = $true }
function Get-PythonCandidates { @($venvRuntime.path) }
function Get-PythonProbe { $venvRuntime }
function Invoke-InventoryProcess {
    param($Executable, $ArgumentList, $InputText, $WorkingDirectory, $TimeoutMs)
    $checks = @(
        @{ name = 'Python'; status = 'installed'; detail = '3.11.9' },
        @{ name = 'Virtual environment'; status = 'installed'; detail = 'venv' },
        @{ name = 'numpy'; status = 'installed'; detail = 'numpy2' },
        @{ name = 'Ollama'; status = 'installed'; detail = 'Local service connected' },
        @{ name = 'Qwen'; status = 'installed'; detail = 'Model ready' }
    )
    $body = @{ type = 'result'; data = @{ ready = $true; checks = $checks; voices = @(@{id='Voice';label='Voice'}); hardware = @{cpu='Engine CPU'; ram_gb=32;gpus=@();cuda_devices=0} } } | ConvertTo-Json -Depth 8 -Compress
    [pscustomobject]@{ exit_code = 0; stdout = $body; stderr = '' }
}
$report = Invoke-DependencyInventory $request
Assert-Inventory $report.ready 'Valid Engine readiness must be propagated.'
Assert-Inventory (($report.checks | Where-Object { $_.id -eq 'numpy' }).status -eq 'installed') 'Full check must replace blocked package checks.'
Assert-Inventory (($report.checks | Where-Object { $_.id -eq 'ollama' }).status -eq 'installed') 'A running service is sufficient when local CLI path is unavailable.'
Assert-Inventory ($report.voices.Count -eq 1 -and $report.hardware.ram_gb -eq 32) 'Engine voices/hardware must be merged.'
Write-Output 'PASS: Python check merges detail and readiness into bootstrap inventory'
