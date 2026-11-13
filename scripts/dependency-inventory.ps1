[CmdletBinding()]
param()

. (Join-Path $PSScriptRoot 'dev-profile.ps1')

# This bootstrap runs without Python and never installs or downloads software.
function New-DependencyRow {
    param([string]$Id, [string]$Name, [string]$Status, [string]$Detail)
    [pscustomobject]@{ id = $Id; name = $Name; status = $Status; detail = $Detail }
}

function ConvertTo-ProcessArgument {
    param([AllowEmptyString()][string]$Value)
    if ($Value -notmatch '[\s"]' -and $Value.Length) { return $Value }
    '"' + [regex]::Replace([regex]::Replace($Value, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
}

function Invoke-InventoryProcess {
    param([string]$Executable, [string[]]$ArgumentList = @(), [string]$InputText = '', [string]$WorkingDirectory = '', [int]$TimeoutMs = 12000, $DevProfile = $null)
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = New-Object System.Diagnostics.ProcessStartInfo
    $process.StartInfo.FileName = $Executable
    $process.StartInfo.Arguments = (($ArgumentList | ForEach-Object { ConvertTo-ProcessArgument $_ }) -join ' ')
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.CreateNoWindow = $true
    $process.StartInfo.RedirectStandardInput = $true
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    $process.StartInfo.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $process.StartInfo.StandardErrorEncoding = [System.Text.UTF8Encoding]::new($false)
    if ($WorkingDirectory) { $process.StartInfo.WorkingDirectory = $WorkingDirectory }
    foreach ($key in 'PYLAUNCHER_ALLOW_INSTALL', 'PYLAUNCHER_ALWAYS_INSTALL', 'PYLAUNCHER_DRYRUN') {
        $process.StartInfo.EnvironmentVariables.Remove($key)
    }
    $process.StartInfo.EnvironmentVariables['PYTHON_MANAGER_AUTOMATIC_INSTALL'] = 'false'
    if (-not $DevProfile -and $env:AIVD_DEV_MODE -eq '1') { $DevProfile = Get-AivdDevProfile $null $env:AIVD_DEV_ROOT }
    Set-AivdDevProcessEnvironment $process.StartInfo $DevProfile
    try {
        if (-not $process.Start()) { throw 'Cannot start process.' }
        $output = $process.StandardOutput.ReadToEndAsync()
        $errors = $process.StandardError.ReadToEndAsync()
        if ($InputText) { $process.StandardInput.Write($InputText) }
        $process.StandardInput.Close()
        if (-not $process.WaitForExit($TimeoutMs)) {
            try { $process.Kill() } catch { }
            return [pscustomobject]@{ exit_code = -1; stdout = ''; stderr = 'Quá thời gian kiểm tra.' }
        }
        $stdout = if ($output.Wait(1000)) { $output.Result } else { '' }
        $stderr = if ($errors.Wait(1000)) { $errors.Result } else { '' }
        [pscustomobject]@{ exit_code = $process.ExitCode; stdout = $stdout; stderr = $stderr }
    } catch {
        [pscustomobject]@{ exit_code = -1; stdout = ''; stderr = $_.Exception.Message }
    } finally {
        $process.Dispose()
    }
}

function Get-InventorySetting {
    param($Settings, [string]$Name, [string]$Default = '')
    $value = if ($Settings) { $Settings.$Name } else { $null }
    if ($null -eq $value -or [string]::IsNullOrWhiteSpace([string]$value)) { return $Default }
    ([string]$value).Trim()
}

function Find-InventoryExecutable {
    param([string]$Explicit, [string[]]$Candidates)
    if ($Explicit) {
        if (Test-Path -LiteralPath $Explicit -PathType Leaf -ErrorAction SilentlyContinue) { return [IO.Path]::GetFullPath($Explicit) }
        return ''
    }
    foreach ($candidate in $Candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf -ErrorAction SilentlyContinue)) { return [IO.Path]::GetFullPath($candidate) }
    }
    ''
}

function Get-CommandFile {
    param([string]$Name)
    $command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { [string]$command.Source } else { '' }
}

function Get-PythonCandidates {
    param($Settings, [string]$DataDir)
    $profile = Get-AivdDevProfile $Settings $DataDir
    if ($profile) {
        return @($profile.Settings.python_path, (Assert-AivdDevPath (Join-Path $profile.Root 'runtime\python311-nuget\python.exe') $profile.Root))
    }
    $candidates = [System.Collections.Generic.List[string]]::new()
    foreach ($candidate in @(
        (Get-InventorySetting $Settings 'python_path'),
        (Join-Path $DataDir 'venv\Scripts\python.exe'),
        (Join-Path $DataDir 'runtime\python311-nuget\python.exe'),
        (Join-Path $DataDir 'runtime\python311\python.exe'),
        (Get-CommandFile 'python.exe')
    )) { if ($candidate) { $candidates.Add($candidate) } }
    foreach ($registry in @('HKCU:\Software\Python\PythonCore', 'HKLM:\Software\Python\PythonCore', 'HKLM:\Software\WOW6432Node\Python\PythonCore')) {
        foreach ($version in @(Get-ChildItem -LiteralPath $registry -ErrorAction SilentlyContinue)) {
            $install = Get-Item -LiteralPath ($version.PSPath + '\InstallPath') -ErrorAction SilentlyContinue
            if ($install) {
                try {
                    $executable = $install.GetValue('ExecutablePath')
                    if (-not $executable -and $install.GetValue('')) { $executable = Join-Path $install.GetValue('') 'python.exe' }
                    if ($executable) { $candidates.Add([string]$executable) }
                } catch { }
            }
        }
    }
    # Read the launcher's existing inventory only. Never request automatic installation.
    $launcher = Get-CommandFile 'py.exe'
    if ($launcher) {
        $listed = Invoke-InventoryProcess $launcher @('--list-paths') -TimeoutMs 5000
        foreach ($line in ($listed.stdout -split '\r?\n')) {
            if ($line -match '([A-Za-z]:\\.+\\python(?:w)?\.exe)\s*$') { $candidates.Add($Matches[1].Trim()) }
        }
    }
    @($candidates | Where-Object { $_ -notmatch '\\Microsoft\\WindowsApps\\' } | Select-Object -Unique)
}

function Get-PythonProbe {
    param([string]$Executable, $DevProfile = $null)
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf -ErrorAction SilentlyContinue)) { return $null }
    $code = 'import json,struct,sys; print(json.dumps(dict(version=".".join(map(str,sys.version_info[:3])),major=sys.version_info.major,minor=sys.version_info.minor,bits=struct.calcsize("P")*8,path=sys.executable,base_prefix=sys.base_prefix,venv=sys.prefix!=sys.base_prefix)))'
    $result = Invoke-InventoryProcess $Executable @('-X', 'utf8', '-E', '-s', '-B', '-c', $code) -TimeoutMs 6000 -DevProfile $DevProfile
    if ($result.exit_code -ne 0) { return $null }
    try {
        $probe = $result.stdout.Trim() | ConvertFrom-Json -ErrorAction Stop
        if ($DevProfile -and ([IO.Path]::GetFullPath([string]$probe.path) -ne [IO.Path]::GetFullPath($Executable) -or [IO.Path]::GetFullPath([string]$probe.base_prefix) -ne (Join-Path $DevProfile.Root 'runtime\python311-nuget'))) { return $null }
        if ($probe.path -and $probe.major -and $probe.minor -and $probe.bits) { return $probe }
    } catch { }
    $null
}

function Test-CompatiblePython {
    param($Probe)
    $null -ne $Probe -and $Probe.major -eq 3 -and $Probe.minor -eq 11 -and $Probe.bits -eq 64
}

function Select-InventoryPython {
    param([object[]]$Probes, [string]$Explicit = '')
    if ($Explicit) {
        $selected = $Probes | Where-Object { [IO.Path]::GetFullPath($_.path) -eq [IO.Path]::GetFullPath($Explicit) } | Select-Object -First 1
    } else { $selected = $Probes | Where-Object { Test-CompatiblePython $_ } | Select-Object -First 1 }
    $selected
}

function Test-InventoryFiles {
    param([string]$Directory, [string[]]$Names)
    foreach ($name in $Names) {
        $file = Get-Item -LiteralPath (Join-Path $Directory $name) -ErrorAction SilentlyContinue
        if (-not $file -or $file.PSIsContainer -or $file.Length -le 0) { return $false }
    }
    $true
}

function Get-InventoryHardware {
    $cpu = $env:PROCESSOR_IDENTIFIER
    try { $cpu = (Get-ItemProperty -LiteralPath 'HKLM:\HARDWARE\DESCRIPTION\System\CentralProcessor\0' -Name ProcessorNameString -ErrorAction Stop).ProcessorNameString.Trim() } catch { }
    $ram = $null
    try {
        Add-Type -AssemblyName Microsoft.VisualBasic -ErrorAction Stop
        $ram = [math]::Round(([Microsoft.VisualBasic.Devices.ComputerInfo]::new().TotalPhysicalMemory / 1GB), 1)
    } catch { }
    $gpus = @()
    $gpuStatus = 'unchecked'
    try {
        $controllers = @(Get-CimInstance Win32_VideoController -OperationTimeoutSec 3 -ErrorAction Stop)
        $gpus = @($controllers | ForEach-Object { [pscustomobject]@{ name = $_.Name; vram_mb = $null } })
        $gpuStatus = if ($gpus.Count) { 'installed' } else { 'missing' }
    } catch {
        # Device registry is available even when WMI permissions are restricted.
        foreach ($adapter in @(Get-ChildItem -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\Video' -ErrorAction SilentlyContinue)) {
            foreach ($subkey in @(Get-ChildItem -LiteralPath $adapter.PSPath -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -match '^\d{4}$' })) {
                $properties = Get-ItemProperty -LiteralPath $subkey.PSPath -ErrorAction SilentlyContinue
                $name = $properties.DriverDesc
                if (-not $name) { $name = $properties.'HardwareInformation.AdapterString' }
                if ($name -is [byte[]]) { $name = [Text.Encoding]::Unicode.GetString($name).Trim([char]0) }
                if ($name) { $gpus += [pscustomobject]@{ name = [string]$name; vram_mb = $null } }
            }
        }
        $gpus = @($gpus | Sort-Object name -Unique)
        if ($gpus.Count) { $gpuStatus = 'installed' }
    }
    $nvidia = Get-CommandFile 'nvidia-smi.exe'
    if ($nvidia) {
        $result = Invoke-InventoryProcess $nvidia @('--query-gpu=name,memory.total', '--format=csv,noheader,nounits') -TimeoutMs 6000
        if ($result.exit_code -eq 0) {
            $nvidiaGpus = @($result.stdout -split '\r?\n' | Where-Object { $_.Trim() } | ForEach-Object {
                $columns = $_ -split ',\s*', 2
                if ($columns.Count -eq 2 -and $columns[1] -match '^\d+$') { [pscustomobject]@{ name = $columns[0].Trim(); vram_mb = [int]$columns[1] } }
            })
            if ($nvidiaGpus.Count) {
                $gpus = @($nvidiaGpus) + @($gpus | Where-Object { $_.name -notmatch 'NVIDIA' })
                $gpuStatus = 'installed'
            }
        }
    }
    [pscustomobject]@{ cpu = $cpu; ram_gb = $ram; gpus = @($gpus); gpu_status = $gpuStatus; cuda_devices = $null }
}

function Get-LocalOllamaModels {
    param([string]$HostAddress = '127.0.0.1:11434')
    if ($HostAddress -notin @('127.0.0.1:11434', '127.0.0.1:11435')) { throw 'Only the managed IPv4 loopback Ollama endpoint is allowed.' }
    $request = [Net.HttpWebRequest]::Create("http://$HostAddress/api/tags")
    $request.Proxy = $null
    $request.AllowAutoRedirect = $false
    $request.Timeout = 2000
    $request.ReadWriteTimeout = 2000
    $response = $null
    $reader = $null
    try {
        $response = $request.GetResponse()
        if ([int]$response.StatusCode -ne 200) { throw 'Ollama trả mã HTTP không hợp lệ.' }
        $reader = [IO.StreamReader]::new($response.GetResponseStream(), [Text.Encoding]::UTF8)
        $buffer = New-Object char[] (2MB + 1)
        $read = 0
        while ($read -lt $buffer.Length) {
            $count = $reader.Read($buffer, $read, $buffer.Length - $read)
            if (-not $count) { break }
            $read += $count
        }
        if ($read -gt 2MB) { throw 'Ollama trả dữ liệu vượt giới hạn.' }
        $body = ([string]::new($buffer, 0, $read)) | ConvertFrom-Json -ErrorAction Stop
        if ($null -eq $body.models) { throw 'Ollama trả danh sách model không hợp lệ.' }
        [pscustomobject]@{ available = $true; models = @($body.models); detail = "Đã kết nối http://$HostAddress." }
    } catch {
        [pscustomobject]@{ available = $false; models = @(); detail = "Chưa kết nối được Ollama tại $HostAddress. Cài hoặc khởi động Ollama để kiểm tra model." }
    } finally {
        if ($reader) { $reader.Dispose() }
        if ($response) { $response.Dispose() }
    }
}

function Get-PythonCheckId {
    param([string]$Name)
    switch ($Name) {
        'Python' { 'python' }
        'Virtual environment' { 'venv' }
        'FFMPEG' { 'ffmpeg' }
        'FFPROBE' { 'ffprobe' }
        'Whisper model' { 'whisper_model' }
        'Whisper CUDA' { 'whisper_cuda' }
        'VieNeu-TTS model' { 'tts_model' }
        'VieNeu-TTS CUDA' { 'tts_cuda' }
        'Giọng VieNeu-TTS' { 'tts_voices' }
        'Ollama' { 'ollama_service' }
        'Qwen' { 'qwen' }
        default { $Name }
    }
}

function Invoke-DependencyInventory {
    param($Request)
    $settings = $Request.settings
    $dataDir = [IO.Path]::GetFullPath([string]$Request.data_dir)
    $devProfile = Get-AivdDevProfile $settings $dataDir
    if ($devProfile) { $settings = $devProfile.Settings; $dataDir = $devProfile.Root }
    $engineDir = if ($Request.engine_dir) { [IO.Path]::GetFullPath([string]$Request.engine_dir) } else { Join-Path (Split-Path -Parent $PSScriptRoot) 'engine' }
    $rows = [System.Collections.Generic.List[object]]::new()
    $probes = @()
    foreach ($candidate in @(Get-PythonCandidates $settings $dataDir)) {
        $probe = Get-PythonProbe $candidate $devProfile
        if ($probe) { $probes += $probe }
    }
    $explicitPython = Get-InventorySetting $settings 'python_path'
    $selected = Select-InventoryPython $probes $explicitPython
    $pythonPath = if (Test-CompatiblePython $selected) { [string]$selected.path } else { '' }
    $available = ($probes | ForEach-Object { "$($_.version) ($($_.bits)-bit) · $($_.path)" }) -join '; '
    $pythonStatus = if ($pythonPath) { 'installed' } elseif ($probes.Count -or $explicitPython) { 'incompatible' } else { 'missing' }
    $pythonDetail = if ($pythonPath) { "Python $($selected.version) x64 · $pythonPath" } else { 'Chưa có Python 3.11 x64 phù hợp cho ứng dụng.' }
    if ($available) { $pythonDetail += " Đang có trên máy: $available" }
    if ($explicitPython -and -not $selected) { $pythonDetail += " Không chạy được Python đã chọn: $explicitPython" }
    $rows.Add((New-DependencyRow 'python' 'Python 3.11 x64' $pythonStatus $pythonDetail))

    $venvPath = Join-Path $dataDir 'venv\Scripts\python.exe'
    $venv = @($probes | Where-Object { $_.path -eq $venvPath }) | Select-Object -First 1
    $rows.Add((New-DependencyRow 'venv' 'Môi trường ảo (venv)' $(if ($venv -and (Test-CompatiblePython $venv) -and $venv.venv) { 'installed' } elseif (Test-Path -LiteralPath $venvPath -PathType Leaf) { 'incompatible' } else { 'missing' }) $(if ($venv) { $venvPath } else { 'Chưa tạo môi trường Python riêng của ứng dụng. Nút cài đặt sẽ tạo môi trường này.' })))

    $ffmpegCandidates = @((Join-Path $dataDir 'tools\ffmpeg\bin\ffmpeg.exe'))
    if (-not $devProfile) { $ffmpegCandidates += Get-CommandFile 'ffmpeg.exe' }
    $ffmpeg = Find-InventoryExecutable (Get-InventorySetting $settings 'ffmpeg_path') $ffmpegCandidates
    $siblingProbe = if ($ffmpeg) { Join-Path (Split-Path -Parent $ffmpeg) 'ffprobe.exe' } else { '' }
    $ffprobeCandidates = @($siblingProbe, (Join-Path $dataDir 'tools\ffmpeg\bin\ffprobe.exe'))
    if (-not $devProfile) { $ffprobeCandidates += Get-CommandFile 'ffprobe.exe' }
    $ffprobe = Find-InventoryExecutable (Get-InventorySetting $settings 'ffprobe_path') $ffprobeCandidates
    foreach ($tool in @(@('ffmpeg', $ffmpeg), @('ffprobe', $ffprobe))) {
        $name = $tool[0]
        $path = $tool[1]
        $status = 'missing'
        $detail = "Chưa tìm thấy $($name.ToUpper())."
        if ($path) {
            $run = Invoke-InventoryProcess $path @('-version') -TimeoutMs 6000 -DevProfile $devProfile
            if ($run.exit_code -eq 0 -and $run.stdout -match "(?im)^$name version[^\r\n]*") { $status = 'installed'; $detail = "$($Matches[0]) · $path" }
            else { $status = 'incompatible'; $detail = "Không chạy được $path. $($run.stderr.Trim())" }
        }
        $rows.Add((New-DependencyRow $name $name.ToUpper() $status $detail))
    }

    foreach ($package in @('faster-whisper', 'ctranslate2', 'av', 'vieneu', 'onnxruntime', 'numpy', 'soundfile')) {
        $rows.Add((New-DependencyRow $package $package 'unchecked' 'Chờ môi trường Python 3.11 của ứng dụng để kiểm tra phiên bản và nạp thư viện.'))
    }
    $whisperName = Get-InventorySetting $settings 'whisper_model' 'small'
    if ($whisperName -match '^[a-z0-9][a-z0-9.-]{0,59}$') {
        $whisperPath = Join-Path $dataDir "models\whisper\$whisperName"
        $whisperFiles = (Test-InventoryFiles $whisperPath @('config.json', 'model.bin', 'tokenizer.json')) -and ((Test-InventoryFiles $whisperPath @('vocabulary.json')) -or (Test-InventoryFiles $whisperPath @('vocabulary.txt')))
        $rows.Add((New-DependencyRow 'whisper_model' "Whisper model ($whisperName)" $(if ($whisperFiles) { 'installed' } else { 'missing' }) $(if ($whisperFiles) { $whisperPath } else { "Chưa đủ tệp model trong $whisperPath." })))
    } else { $rows.Add((New-DependencyRow 'whisper_model' 'Whisper model' 'incompatible' 'Tên model Whisper không hợp lệ.')) }
    $rows.Add((New-DependencyRow 'whisper_cuda' 'Whisper CPU / CUDA' 'unchecked' 'Cần runtime AI để xác minh CUDA. Chế độ AUTO có thể dùng CPU.'))

    $ttsPath = Get-InventorySetting $settings 'tts_model_path' (Join-Path $dataDir 'models\vieneu')
    $ttsFiles = Test-InventoryFiles (Join-Path $ttsPath 'onnx_update') @('config.json', 'tokenizer.json', 'vieneu_prefill.onnx', 'vieneu_decode_step.onnx', 'vieneu_acoustic_cached.onnx', 'vieneu_backbone_shared.data', 'vieneu_v3_heads.npz')
    $rows.Add((New-DependencyRow 'tts_model' 'VieNeu-TTS model' $(if ($ttsFiles) { 'unchecked' } else { 'missing' }) $(if ($ttsFiles) { "Đã có tệp model tại $ttsPath; chờ runtime để xác minh codec và khả năng sử dụng." } else { "Chưa đủ tệp model tại $ttsPath." })))
    $rows.Add((New-DependencyRow 'tts_voices' 'Giọng VieNeu-TTS' 'unchecked' 'Chờ môi trường AI để đọc và kiểm tra bộ giọng preset.'))
    if ((Get-InventorySetting $settings 'tts_device' (Get-InventorySetting $settings 'hardware_mode' 'auto')) -eq 'gpu') { $rows.Add((New-DependencyRow 'tts_cuda' 'VieNeu-TTS CUDA' 'unchecked' 'Chờ runtime và weights PyTorch/CUDA được chuẩn bị để kiểm tra.')) }

    if ($devProfile) {
        $ollamaPath = Find-InventoryExecutable $devProfile.Settings.ollama_path @()
        $ollama = if ($devProfile.OllamaOwned) { Get-LocalOllamaModels '127.0.0.1:11435' } else { [pscustomobject]@{ available = $false; models = @(); detail = 'Development Ollama at 127.0.0.1:11435 is not owned by this app; readiness was not probed.' } }
    } else {
        $ollamaPath = Find-InventoryExecutable '' @((Get-CommandFile 'ollama.exe'), (Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'), (Join-Path $dataDir 'tools\ollama\ollama.exe'))
        $ollama = Get-LocalOllamaModels
    }
    $rows.Add((New-DependencyRow 'ollama' 'Ứng dụng Ollama' $(if ($ollamaPath -or $ollama.available) { 'installed' } else { 'missing' }) $(if ($ollamaPath) { $ollamaPath } elseif ($ollama.available) { 'Dịch vụ Ollama đang có sẵn trên máy.' } else { 'Chưa tìm thấy Ollama. Nút cài đặt sẽ cài cho tài khoản hiện tại.' })))
    $rows.Add((New-DependencyRow 'ollama_service' 'Dịch vụ Ollama local' $(if ($ollama.available) { 'installed' } else { 'missing' }) $ollama.detail))
    $qwenName = Get-InventorySetting $settings 'ollama_model' 'qwen2.5:3b'
    $qwen = @($ollama.models | Where-Object { ($_.model -eq $qwenName -or $_.name -eq $qwenName) -and $_.size -gt 0 -and -not $_.remote_model -and -not $_.remote_host }) | Select-Object -First 1
    $rows.Add((New-DependencyRow 'qwen' "Qwen ($qwenName)" $(if (-not $ollama.available) { 'unchecked' } elseif ($qwen) { 'installed' } else { 'missing' }) $(if (-not $ollama.available) { 'Chưa thể xác định model đã tải vì dịch vụ Ollama chưa chạy.' } elseif ($qwen) { 'Đã có trọng số model local.' } else { "Chưa có model local $qwenName." })))

    $hardware = Get-InventoryHardware
    $ready = $false
    $voices = @()
    $engineError = ''
    if ($pythonPath -and (Test-Path -LiteralPath (Join-Path $engineDir 'dubbing\__main__.py') -PathType Leaf)) {
        $resolvedSettings = @{}
        if ($settings -is [Collections.IDictionary]) { foreach ($key in $settings.Keys) { $resolvedSettings[$key] = $settings[$key] } }
        elseif ($settings) { foreach ($property in $settings.PSObject.Properties) { $resolvedSettings[$property.Name] = $property.Value } }
        if ($ffmpeg) { $resolvedSettings.ffmpeg_path = $ffmpeg }
        if ($ffprobe) { $resolvedSettings.ffprobe_path = $ffprobe }
        $payload = @{ action = 'check'; data_dir = $dataDir; settings = $resolvedSettings } | ConvertTo-Json -Depth 8 -Compress
        $run = Invoke-InventoryProcess $pythonPath @('-X', 'utf8', '-E', '-s', '-B', '-u', '-m', 'dubbing') -InputText $payload -WorkingDirectory $engineDir -TimeoutMs 150000 -DevProfile $devProfile
        try {
            $response = ($run.stdout -split '\r?\n' | Where-Object { $_.Trim() } | Select-Object -Last 1) | ConvertFrom-Json -ErrorAction Stop
            if ($run.exit_code -ne 0 -or $response.type -ne 'result' -or $null -eq $response.data.checks) { throw 'Python Engine chưa trả được báo cáo kiểm tra hợp lệ.' }
            foreach ($item in $response.data.checks) {
                $id = Get-PythonCheckId $item.name
                $existing = $rows | Where-Object { $_.id -eq $id } | Select-Object -First 1
                if ($existing) { $existing.status = $item.status; $existing.detail = $item.detail }
                else { $rows.Add((New-DependencyRow $id $item.name $item.status $item.detail)) }
            }
            if (($rows | Where-Object { $_.id -eq 'ollama_service' -and $_.status -eq 'installed' })) {
                $ollamaApplication = $rows | Where-Object { $_.id -eq 'ollama' } | Select-Object -First 1
                if (-not $devProfile -and $ollamaApplication.status -ne 'installed') {
                    $ollamaApplication.status = 'installed'
                    $ollamaApplication.detail = 'Dịch vụ Ollama đang có sẵn trên máy.'
                }
            } else {
                $qwenRow = $rows | Where-Object { $_.id -eq 'qwen' } | Select-Object -First 1
                $qwenRow.status = 'unchecked'
                $qwenRow.detail = 'Chưa thể xác định model đã tải vì dịch vụ Ollama chưa chạy.'
            }
            if ($response.data.hardware) {
                $full = $response.data.hardware
                if ($full.cpu) { $hardware.cpu = $full.cpu }
                if ($null -ne $full.ram_gb) { $hardware.ram_gb = $full.ram_gb }
                if ($full.gpus.Count) {
                    $hardware.gpus = @((@($hardware.gpus) + @($full.gpus)) | Group-Object name | ForEach-Object {
                        $withMemory = $_.Group | Where-Object { $null -ne $_.vram_mb } | Select-Object -First 1
                        if ($withMemory) { $withMemory } else { $_.Group[0] }
                    })
                    $hardware.gpu_status = 'installed'
                }
                $hardware.cuda_devices = $full.cuda_devices
            }
            $voices = @($response.data.voices)
            $ready = [bool]$response.data.ready
            if ($devProfile -and -not $ollamaPath) { $ready = $false }
            if ($devProfile -and -not $devProfile.OllamaOwned) {
                $ready = $false
                foreach ($id in 'ollama_service','qwen') {
                    $row = $rows | Where-Object { $_.id -eq $id } | Select-Object -First 1
                    $row.status = 'unchecked'; $row.detail = $ollama.detail
                }
            }
        } catch {
            $engineError = if ($run.stderr.Trim()) { $run.stderr.Trim() } else { $_.Exception.Message }
            if ($engineError.Length -gt 800) { $engineError = $engineError.Substring($engineError.Length - 800) }
            $rows.Add((New-DependencyRow 'engine' 'Python Engine' 'incompatible' "Chưa chạy được kiểm tra AI. $engineError"))
        }
    }
    [pscustomobject]@{
        ready = $ready; checks = @($rows.ToArray()); hardware = $hardware; voices = @($voices)
        setup = [pscustomobject]@{ python_path = $pythonPath; ffmpeg_path = $ffmpeg; ffprobe_path = $ffprobe; ollama_path = $ollamaPath }
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    $ErrorActionPreference = 'Stop'
    [Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    try {
        $request = [Console]::In.ReadToEnd() | ConvertFrom-Json -ErrorAction Stop
        if (-not $request.data_dir) { throw 'Thiếu thư mục dữ liệu ứng dụng.' }
        $report = Invoke-DependencyInventory $request
        @{ type = 'result'; data = $report } | ConvertTo-Json -Depth 12 -Compress
    } catch {
        @{ type = 'error'; message = "Không thể kiểm tra môi trường: $($_.Exception.Message)" } | ConvertTo-Json -Compress
        exit 1
    }
}
