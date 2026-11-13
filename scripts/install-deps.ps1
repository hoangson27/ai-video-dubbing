[CmdletBinding()]
param([switch]$InstallMissing, [switch]$FunctionsOnly)

$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
$ProgressPreference = 'SilentlyContinue'
. (Join-Path $PSScriptRoot 'dependency-inventory.ps1')
$script:PortablePythonSource = 'https://api.nuget.org/v3-flatcontainer/python/3.11.9/python.3.11.9.nupkg'
$script:PortablePythonHash = '9283876D58C017E0E846F95B490DA3BCA0FC0A6EE1134B2870677CFB7EEC3C67'
$script:PortableOllamaSource = 'https://github.com/ollama/ollama/releases/download/v0.40.16/ollama-windows-amd64.zip'
$script:PortableOllamaHash = '50fcf5ad54277f2fa5d173ebfc93bcb017adbc0c9ef2314daabc96ca9e6aa64e'

function Write-InstallEvent {
    param([hashtable]$Event)
    $line = $Event | ConvertTo-Json -Depth 8 -Compress
    if ($script:SetupLogPath) { [IO.File]::AppendAllText($script:SetupLogPath, $line + [Environment]::NewLine, [Text.UTF8Encoding]::new($false)) }
    [Console]::Out.WriteLine($line)
}

function Write-InstallProgress {
    param([double]$Progress, [string]$Message)
    Write-InstallEvent @{ type = 'progress'; stage = 'setup'; progress = $Progress; message = $Message }
}

function Write-InstallLog {
    param([string]$Message, [ValidateSet('info','warning','error')][string]$Level = 'info')
    $Message = [regex]::Replace($Message, ([char]27).ToString() + '\[[0-?]*[ -/]*[@-~]', '')
    if ($Message.Trim()) { Write-InstallEvent @{ type = 'log'; level = $Level; message = $Message } }
}

function Test-InstallCancellation {
    if ($script:CancelPath -and (Test-Path -LiteralPath $script:CancelPath)) {
        throw [OperationCanceledException]::new('Đã hủy cài đặt. Có thể bấm cài lại để tiếp tục.')
    }
}

function Assert-LocalDirectory {
    param([string]$Path)
    if (-not $Path -or $Path -notmatch '^[A-Za-z]:[\\/]' -or $Path.Contains([char]0)) {
        throw 'Thư mục dữ liệu phải là đường dẫn tuyệt đối trên ổ đĩa cục bộ.'
    }
    [IO.Path]::GetFullPath($Path)
}

function Assert-InstallSettings {
    param($Settings)
    $whisper = if ($Settings.whisper_model) { [string]$Settings.whisper_model } else { 'small' }
    $qwen = if ($Settings.ollama_model) { [string]$Settings.ollama_model } else { 'qwen2.5:3b' }
    $mode = if ($Settings.hardware_mode) { [string]$Settings.hardware_mode } else { 'auto' }
    if ($whisper -notmatch '^[a-z0-9][a-z0-9.-]{0,59}$') { throw 'Tên model Whisper không hợp lệ.' }
    if ($qwen -notmatch '^qwen[a-zA-Z0-9._:-]{0,100}$' -or $qwen -match '(?i)(?:-cloud|:cloud)(?:$|:)') {
        throw 'Chỉ hỗ trợ model Qwen local; không dùng model cloud.'
    }
    if ($mode -notin @('auto', 'cpu', 'gpu')) { throw 'Chế độ phần cứng không hợp lệ.' }
    if ($Settings.tts_model_path) { $null = Assert-LocalDirectory ([string]$Settings.tts_model_path) }
    $selection = @{ whisper = $whisper; qwen = $qwen; mode = $mode }
    foreach ($stage in @('asr', 'translation', 'tts')) {
        $key = $stage + '_device'
        $device = if ($Settings.$key) { [string]$Settings.$key } else { $mode }
        if ($device -notin @('auto','cpu','gpu')) { throw "Chế độ thiết bị $stage không hợp lệ." }
        $selection[$stage + '_mode'] = $device
    }
    $selection
}

function Invoke-InstallCommand {
    param([string]$Executable, [string[]]$Arguments)
    Test-InstallCancellation
    $process = New-Object Diagnostics.Process
    $process.StartInfo = New-Object Diagnostics.ProcessStartInfo
    $process.StartInfo.FileName = $Executable
    $process.StartInfo.Arguments = (($Arguments | ForEach-Object { ConvertTo-ProcessArgument $_ }) -join ' ')
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.WorkingDirectory = (Get-Location).ProviderPath
    $process.StartInfo.CreateNoWindow = $true
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    $process.StartInfo.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
    $process.StartInfo.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
    if ($env:AIVD_DEV_MODE -eq '1') { Set-AivdDevProcessEnvironment $process.StartInfo (Get-AivdDevProfile $null $env:AIVD_DEV_ROOT) }
    $exitCode = -1
    try {
        Write-InstallLog ("Chạy {0} {1}" -f $Executable, $process.StartInfo.Arguments)
        if (-not $process.Start()) { throw "Không khởi động được $Executable." }
        $output = $process.StandardOutput.ReadLineAsync()
        $errors = $process.StandardError.ReadLineAsync()
        $outputDone = $false; $errorsDone = $false
        while (-not $outputDone -or -not $errorsDone -or -not $process.HasExited) {
            Test-InstallCancellation
            $observed = $false
            if (-not $outputDone -and $output.IsCompleted) {
                $line = $output.GetAwaiter().GetResult()
                if ($null -eq $line) { $outputDone = $true } else { Write-InstallLog $line; $output = $process.StandardOutput.ReadLineAsync() }
                $observed = $true
            }
            if (-not $errorsDone -and $errors.IsCompleted) {
                $line = $errors.GetAwaiter().GetResult()
                if ($null -eq $line) { $errorsDone = $true } else { Write-InstallLog $line; $errors = $process.StandardError.ReadLineAsync() }
                $observed = $true
            }
            if (-not $observed) { Start-Sleep -Milliseconds 100 }
        }
        $exitCode = $process.ExitCode
        Write-InstallLog ("Kết thúc {0}: mã {1}" -f ([IO.Path]::GetFileName($Executable)), $exitCode)
    } finally {
        try { if (-not $process.HasExited) { $process.Kill() } } catch { }
        $process.Dispose()
    }
    Test-InstallCancellation
    if ($exitCode -notin @(0, 3010)) { throw "$Executable thất bại (mã $exitCode). Xem nhật ký rồi bấm cài lại." }
}

function Assert-NoReparsePath {
    param([string]$Path)
    $current = [IO.Path]::GetFullPath($Path)
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Đường dẫn chứa liên kết không an toàn: $current"
            }
        }
        $parent = [IO.Path]::GetDirectoryName($current)
        if ($parent -eq $current) { break }
        $current = $parent
    }
}

function Expand-ManagedArchive {
    param([string]$Archive, [string]$Destination, [long]$MaxExpandedBytes = 16GB, [int]$MaxEntries = 50000)
    Assert-NoReparsePath $Destination
    Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
    $root = [IO.Path]::GetFullPath($Destination).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    $entries = @()
    $total = [long]0
    try {
        # Validate the whole archive before writing any entry. NuGet metadata stays in staging.
        foreach ($entry in $zip.Entries) {
            Test-InstallCancellation
            if ($entries.Count -ge $MaxEntries) { throw 'ZIP archive contains too many entries.' }
            $name = $entry.FullName.Replace('\', '/')
            $parts = $name.TrimEnd('/').Split('/')
            $unixType = (($entry.ExternalAttributes -shr 16) -band 0xF000)
            if (-not $name -or $name.StartsWith('/') -or $name.Contains(':') -or $unixType -eq 0xA000 -or
                ($entry.ExternalAttributes -band [int][IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "ZIP entry không hợp lệ hoặc không an toàn: $name"
            }
            foreach ($part in $parts) {
                if (-not $part -or $part -in @('.', '..') -or $part.IndexOfAny([IO.Path]::GetInvalidFileNameChars()) -ge 0 -or
                    $part -match '[. ]$|^(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)') {
                    throw "ZIP entry không hợp lệ: $name"
                }
            }
            $target = [IO.Path]::GetFullPath((Join-Path $Destination ($name.Replace('/', [IO.Path]::DirectorySeparatorChar))))
            if (-not $target.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) -or -not $seen.Add($target)) {
                throw "ZIP entry nằm ngoài gói hoặc trùng tên: $name"
            }
            $total += $entry.Length
            if ($total -gt $MaxExpandedBytes -or $entry.Length -gt $MaxExpandedBytes) { throw 'ZIP archive exceeds the allowed expanded size.' }
            $entries += [pscustomobject]@{ entry = $entry; path = $target; directory = $name.EndsWith('/') }
        }
        foreach ($item in $entries) {
            Test-InstallCancellation
            if ($item.directory) {
                New-Item -ItemType Directory -Path $item.path -Force | Out-Null
                continue
            }
            New-Item -ItemType Directory -Path (Split-Path -Parent $item.path) -Force | Out-Null
            $inputStream = $item.entry.Open()
            $outputStream = $null
            try {
                $outputStream = [IO.File]::Open($item.path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
                $buffer = New-Object byte[] (1MB)
                $written = [long]0
                while (($count = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                    Test-InstallCancellation
                    $written += $count
                    if ($written -gt $item.entry.Length -or $written -gt $MaxExpandedBytes) { throw 'ZIP entry expanded beyond its declared size.' }
                    $outputStream.Write($buffer, 0, $count)
                }
                if ($written -ne $item.entry.Length) { throw 'ZIP entry is incomplete.' }
            } finally {
                if ($outputStream) { $outputStream.Dispose() }
                $inputStream.Dispose()
            }
        }
    } finally { $zip.Dispose() }
}

function Expand-PortablePythonArchive {
    param([string]$Archive, [string]$Destination)
    Expand-ManagedArchive $Archive $Destination -MaxExpandedBytes 512MB
}

function Assert-PortablePython {
    param([string]$PythonDir)
    $python = Join-Path $PythonDir 'python.exe'
    foreach ($file in @($python, (Join-Path $PythonDir 'python311.dll'))) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Gói Python thiếu $file" }
        Assert-NoReparsePath $file
        Assert-TrustedInstaller $file @('Python Software Foundation')
    }
    # Isolated mode ignores environment/user-site overrides while validating the complete runtime.
    $test = 'import os,sys,struct,venv,ensurepip,ssl,ctypes,sqlite3,pip;root=os.path.normcase(os.path.realpath(os.path.dirname(sys.executable)));sys.exit(0 if sys.version_info[:3]==(3,11,9) and struct.calcsize("P")==8 and os.path.normcase(os.path.realpath(sys.base_prefix))==root else 1)'
    $probe = Invoke-InventoryProcess $python @('-I', '-c', $test) -TimeoutMs 15000
    if ($probe.exit_code -ne 0) { throw "Python 3.11.9 x64 chưa có đủ venv/ensurepip hoặc không chạy được. $($probe.stderr.Trim())" }
    $python
}

function Install-PortablePython {
    param([string]$Archive, [string]$PythonDir)
    Test-InstallCancellation
    $target = (Assert-LocalDirectory $PythonDir).TrimEnd('\', '/')
    $runtime = Split-Path -Parent $target
    if ((Split-Path -Leaf $target) -ne 'python311-nuget' -or (Split-Path -Leaf $runtime) -ne 'runtime') {
        throw 'Chỉ giải nén Python vào thư mục runtime\python311-nuget riêng của ứng dụng.'
    }
    Assert-NoReparsePath $target
    if (Test-Path -LiteralPath $target) {
        if (-not (Test-Path -LiteralPath $target -PathType Container)) { throw 'Thư mục Python riêng đang là một tệp. Đã dừng để giữ dữ liệu.' }
        try { return (Assert-PortablePython $target) } catch {
            if ($_.Exception -is [OperationCanceledException]) { throw }
            Write-InstallLog 'Python riêng chưa hoàn chỉnh; bản cũ sẽ được giữ lại sau khi kiểm tra bản mới.' 'warning'
        }
    }
    Assert-NoReparsePath $Archive
    Assert-ArchiveHash $Archive $script:PortablePythonHash
    if (-not (Test-Path -LiteralPath $runtime)) { New-Item -ItemType Directory -Path $runtime | Out-Null }
    $staging = Join-Path $runtime ('.python311-nuget-stage-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $staging | Out-Null
    Expand-PortablePythonArchive $Archive $staging
    $tools = Join-Path $staging 'tools'
    $null = Assert-PortablePython $tools
    $receipt = @{ version = '3.11.9'; architecture = 'x64'; source = $script:PortablePythonSource; sha256 = $script:PortablePythonHash }
    [IO.File]::WriteAllText((Join-Path $tools '.ai-video-dubbing-python.json'), ($receipt | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    Test-InstallCancellation
    Assert-NoReparsePath $target
    $backup = ''
    if (Test-Path -LiteralPath $target) {
        $backup = Join-Path $runtime ('python311-nuget-backup-' + [Guid]::NewGuid().ToString('N'))
        Move-Item -LiteralPath $target -Destination $backup
    }
    try { [IO.Directory]::Move($tools, $target) } catch {
        if ($backup -and -not (Test-Path -LiteralPath $target)) { [IO.Directory]::Move($backup, $target) }
        throw
    }
    # Keep unique staging metadata and any prior runtime for diagnostics and recovery.
    Join-Path $target 'python.exe'
}

function Get-CompatiblePython {
    param([string[]]$Candidates)
    $test = 'import sys,struct,venv,ensurepip;sys.exit(0 if sys.version_info[:2]==(3,11) and struct.calcsize("P")==8 else 1)'
    if ($env:AIVD_DEV_MODE -eq '1') {
        $expectedBase = (Join-Path $env:AIVD_DEV_ROOT 'runtime\python311-nuget') | ConvertTo-Json -Compress
        $test = "import os,sys,struct,venv,ensurepip;expected=$expectedBase;sys.exit(0 if sys.version_info[:2]==(3,11) and struct.calcsize('P')==8 and os.path.normcase(os.path.realpath(sys.base_prefix))==os.path.normcase(os.path.realpath(expected)) else 1)"
    }
    foreach ($candidate in ($Candidates | Where-Object { $_ } | Select-Object -Unique)) {
        Test-InstallCancellation
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf) -or $candidate -like '*\Microsoft\WindowsApps\*') { continue }
        if ($env:AIVD_DEV_MODE -eq '1') { $null = Assert-AivdDevPath $candidate $env:AIVD_DEV_ROOT }
        $probe = Invoke-InventoryProcess $candidate @('-E', '-s', '-c', $test) -TimeoutMs 15000
        if ($probe.exit_code -eq 0) { return [IO.Path]::GetFullPath($candidate) }
        if ($probe.stderr.Trim()) { Write-InstallLog ("Không dùng được Python {0}: {1}" -f $candidate, $probe.stderr.Trim()) }
    }
    return $null
}

function Get-ExistingPython {
    param($Settings, [string]$DataDir)
    $profile = Get-AivdDevProfile $Settings $DataDir
    if ($profile) { return (Get-CompatiblePython @(Get-PythonCandidates $profile.Settings $profile.Root)) }
    $candidates = @([string]$Settings.python_path, (Join-Path $DataDir 'venv\Scripts\python.exe'), (Join-Path $DataDir 'runtime\python311-nuget\python.exe'), (Join-Path $DataDir 'runtime\python311\python.exe'))
    foreach ($base in @('HKCU:\Software\Python\PythonCore', 'HKLM:\Software\Python\PythonCore')) {
        foreach ($version in @('3.11', '3.11-64')) {
            $key = Get-Item -LiteralPath "$base\$version\InstallPath" -ErrorAction SilentlyContinue
            if ($key) {
                $registered = [string]$key.GetValue('ExecutablePath')
                $directory = [string]$key.GetValue('')
                if ($registered) { $candidates += $registered }
                elseif ($directory) { $candidates += Join-Path $directory 'python.exe' }
            }
        }
    }
    $onPath = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($onPath) { $candidates += $onPath.Source }
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        $listed = Invoke-InventoryProcess $launcher.Source @('-3.11', '-E', '-s', '-c', 'import sys;print(sys.executable)') -TimeoutMs 10000
        if ($listed.exit_code -eq 0) { $candidates += ($listed.stdout.Trim() -split '\r?\n' | Select-Object -Last 1) }
    }
    Get-CompatiblePython $candidates
}

function Get-ExistingTool {
    param([string[]]$Candidates, [string[]]$Arguments, [string]$Pattern)
    foreach ($candidate in ($Candidates | Where-Object { $_ } | Select-Object -Unique)) {
        Test-InstallCancellation
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        if ($env:AIVD_DEV_MODE -eq '1') {
            $null = Assert-AivdDevPath $candidate $env:AIVD_DEV_ROOT
            if ($Pattern -match 'ollama') { Assert-AivdSignedOllama $candidate }
        }
        $probe = Invoke-InventoryProcess $candidate $Arguments -TimeoutMs 12000
        if ($probe.exit_code -eq 0 -and ($probe.stdout + $probe.stderr) -match $Pattern) { return [IO.Path]::GetFullPath($candidate) }
    }
    return $null
}

function Get-Download {
    param([string]$Url, [string]$Destination, [string]$Label, [double]$From, [double]$To)
    if (-not $Url.StartsWith('https://', [StringComparison]::OrdinalIgnoreCase)) { throw 'Chỉ tải qua HTTPS.' }
    Assert-NoReparsePath $Destination
    Assert-NoReparsePath ($Destination + '.part')
    New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        Test-InstallCancellation
        Write-InstallProgress $From "Đang tải $Label..."
        $response = $null; $inputStream = $null; $outputStream = $null
        try {
            $web = [Net.HttpWebRequest]::Create($Url)
            $web.UserAgent = 'AI-Video-Dubbing-Dependency-Setup/0.1'
            $web.Timeout = 30000; $web.ReadWriteTimeout = 30000
            $web.MaximumAutomaticRedirections = 5
            $response = $web.GetResponse()
            if ($response.ResponseUri.Scheme -ne 'https') { throw 'Máy chủ chuyển hướng sang kết nối không an toàn.' }
            $inputStream = $response.GetResponseStream()
            $outputStream = [IO.File]::Open($Destination + '.part', [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::None)
            $buffer = New-Object byte[] (1024 * 1024)
            $received = [long]0; $lastUpdate = [DateTime]::UtcNow
            while (($count = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                Test-InstallCancellation
                $outputStream.Write($buffer, 0, $count); $received += $count
                if (([DateTime]::UtcNow - $lastUpdate).TotalSeconds -ge 2) {
                    $percent = if ($response.ContentLength -gt 0) { [Math]::Min([double]1.0, [double]$received / [double]$response.ContentLength) } else { 0 }
                    Write-InstallProgress ($From + ($To - $From) * $percent) ("Đang tải {0}: {1:N0} MB" -f $Label, ($received / 1MB))
                    $lastUpdate = [DateTime]::UtcNow
                }
            }
            if ($response.ContentLength -gt 0 -and $received -ne $response.ContentLength) { throw 'Tệp tải chưa đủ dung lượng.' }
            $outputStream.Dispose(); $outputStream = $null
            Move-Item -LiteralPath ($Destination + '.part') -Destination $Destination -Force
            return
        } catch {
            if ($_.Exception -is [OperationCanceledException] -or $attempt -eq 2) { throw }
            Write-InstallLog "Tải $Label chưa thành công, đang thử lại một lần."
        } finally {
            if ($outputStream) { $outputStream.Dispose() }
            if ($inputStream) { $inputStream.Dispose() }
            if ($response) { $response.Dispose() }
        }
    }
}

function Assert-TrustedInstaller {
    param([string]$Path, [string[]]$Publisher)
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    $name = if ($signature.SignerCertificate) { $signature.SignerCertificate.GetNameInfo([Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false) } else { '' }
    if ($signature.Status -ne 'Valid' -or $name -notin $Publisher) {
        throw "Chữ ký số của $Path không hợp lệ hoặc không thuộc $($Publisher -join ', '). Đã dừng cài đặt."
    }
}

function Assert-ArchiveHash {
    param([string]$Path, [string]$Expected)
    if ($Expected -notmatch '^[a-fA-F0-9]{64}$' -or
        (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $Expected) {
        throw "SHA-256 không khớp: $Path. Không giải nén hoặc chạy tệp này."
    }
}

function Get-VerifiedDownload {
    param([string]$Url, [string]$Destination, [string]$Label, [double]$From, [double]$To, [string[]]$Publisher = @(), [string]$Hash = '')
    $valid = $false
    if (Test-Path -LiteralPath $Destination -PathType Leaf) {
        try {
            if ($Hash) { Assert-ArchiveHash $Destination $Hash } else { Assert-TrustedInstaller $Destination $Publisher }
            $valid = $true
        } catch { Write-InstallLog "Bản tải $Label trước đó chưa hợp lệ; đang tải lại." }
    }
    if (-not $valid) {
        Get-Download $Url $Destination $Label $From $To
        if ($Hash) { Assert-ArchiveHash $Destination $Hash } else { Assert-TrustedInstaller $Destination $Publisher }
    }
}

function Initialize-InstallVenv {
    param([string]$Python, [string]$DataDir)
    $venv = Join-Path $DataDir 'venv'
    $executable = Join-Path $venv 'Scripts\python.exe'
    if (Get-CompatiblePython @($executable)) { return $executable }
    if (Test-Path -LiteralPath $venv) {
        $resolved = [IO.Path]::GetFullPath($venv)
        $expected = Join-Path ([IO.Path]::GetFullPath($DataDir)) 'venv'
        if ($resolved -ne $expected) { throw 'Không thể xác định môi trường riêng cần sửa.' }
        $item = Get-Item -LiteralPath $venv -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Môi trường riêng là một liên kết thư mục. Đã dừng để giữ an toàn dữ liệu.' }
        $backup = Join-Path $DataDir ('venv-backup-' + [Guid]::NewGuid().ToString('N'))
        Write-InstallLog 'Đang tạo lại môi trường riêng; bản cũ được giữ để có thể phục hồi.'
        Move-Item -LiteralPath $resolved -Destination $backup
    }
    Invoke-InstallCommand $Python @('-E', '-s', '-m', 'venv', $venv)
    if (-not (Get-CompatiblePython @($executable))) { throw 'Chưa tạo được môi trường AI. Bấm Tiếp tục thiết lập để thử lại.' }
    $executable
}

function Copy-ToolArchive {
    param([string]$Archive, [string]$Destination, [string]$Executable)
    Test-InstallCancellation
    $temporary = Join-Path $script:CacheDir ('extract-' + [Guid]::NewGuid().ToString('N'))
    Assert-NoReparsePath $Destination
    Expand-ManagedArchive $Archive $temporary
    $entry = Get-ChildItem -LiteralPath $temporary -Recurse -File -Filter $Executable | Select-Object -First 1
    if (-not $entry) { throw "Gói tải về không chứa $Executable." }
    $content = if ($Executable -eq 'ffmpeg.exe') { Split-Path -Parent $entry.DirectoryName } else { $entry.DirectoryName }
    # Existing tool directories must not redirect any copied archive member.
    foreach ($item in Get-ChildItem -LiteralPath $content -Recurse -Force) {
        $relative = $item.FullName.Substring($content.Length).TrimStart('\', '/')
        Assert-NoReparsePath (Join-Path $Destination $relative)
    }
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    foreach ($item in Get-ChildItem -LiteralPath $content -Force) {
        Test-InstallCancellation
        Copy-Item -LiteralPath $item.FullName -Destination $Destination -Recurse -Force
    }
    # Leave the unique extraction directory for diagnostics/retry; never delete user files.
}

function Get-LocalOllamaResponse {
    param([string]$HostAddress = $(if ($env:AIVD_DEV_MODE -eq '1') { '127.0.0.1:11435' } else { '127.0.0.1:11434' }))
    if ($HostAddress -notin @('127.0.0.1:11434', '127.0.0.1:11435')) { throw 'Only the managed IPv4 loopback Ollama endpoint is allowed.' }
    $web = [Net.HttpWebRequest]::Create("http://$HostAddress/api/tags")
    $web.Proxy = $null; $web.AllowAutoRedirect = $false; $web.Timeout = 2500; $web.ReadWriteTimeout = 2500
    $response = $null; $reader = $null
    try {
        $response = $web.GetResponse()
        if ([int]$response.StatusCode -ne 200) { return $null }
        $reader = [IO.StreamReader]::new($response.GetResponseStream())
        $buffer = New-Object char[] (2MB + 1)
        $read = 0
        while ($read -lt $buffer.Length) {
            $count = $reader.Read($buffer, $read, $buffer.Length - $read)
            if (-not $count) { break }
            $read += $count
        }
        if ($read -gt 2MB) { return $null }
        $body = ([string]::new($buffer, 0, $read)) | ConvertFrom-Json
        if (-not ($body.PSObject.Properties.Name -contains 'models')) { return $null }
        return $body
    } catch { return $null }
    finally {
        if ($reader) { $reader.Dispose() }
        if ($response) { $response.Dispose() }
    }
}

function Install-PortableOllama {
    param([string]$Archive, [string]$Destination)
    $target = (Assert-LocalDirectory $Destination).TrimEnd('\', '/')
    if ((Split-Path -Leaf $target) -ne 'ollama' -or (Split-Path -Leaf (Split-Path -Parent $target)) -ne 'tools') { throw 'Portable Ollama must be installed in the managed tools\ollama directory.' }
    Assert-NoReparsePath $target
    Assert-NoReparsePath $Archive
    Assert-ArchiveHash $Archive $script:PortableOllamaHash
    $staging = Join-Path (Split-Path -Parent $target) ('.ollama-stage-' + [Guid]::NewGuid().ToString('N'))
    Expand-ManagedArchive $Archive $staging -MaxExpandedBytes 16GB
    $executable = Join-Path $staging 'ollama.exe'
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) { throw 'Portable Ollama archive does not contain ollama.exe at its root.' }
    Assert-AivdSignedOllama $executable
    [IO.File]::WriteAllText((Join-Path $staging '.ai-video-dubbing-ollama.json'), (@{ version = '0.40.16'; source = $script:PortableOllamaSource; sha256 = $script:PortableOllamaHash } | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    Test-InstallCancellation
    Assert-NoReparsePath $target
    $backup = ''
    if (Test-Path -LiteralPath $target) {
        $backup = Join-Path (Split-Path -Parent $target) ('ollama-backup-' + [Guid]::NewGuid().ToString('N'))
        Move-Item -LiteralPath $target -Destination $backup
    }
    try { [IO.Directory]::Move($staging, $target) } catch {
        if ($backup -and -not (Test-Path -LiteralPath $target)) { [IO.Directory]::Move($backup, $target) }
        throw
    }
    Join-Path $target 'ollama.exe'
}

if ($FunctionsOnly) { return }

[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$script:CancelPath = ''
$script:SetupLogPath = ''
$ownedServer = $null
$setupLease = $null
try {
    if (-not $InstallMissing) { throw 'Cài đặt chỉ chạy sau khi người dùng chọn nút Cài các thành phần còn thiếu (-InstallMissing).' }
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or -not [Environment]::Is64BitOperatingSystem) {
        throw 'Cần Windows x64 để thiết lập môi trường này.'
    }
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    if (-not $request) { throw 'Thiếu yêu cầu cài đặt.' }
    $settings = if ($request.settings) { $request.settings } else { @{} }
    $devProfile = Get-AivdDevProfile $settings ([string]$request.data_dir)
    if ($devProfile) { $settings = $devProfile.Settings }
    $dataDir = Assert-LocalDirectory $(if ($devProfile) { $devProfile.Root } else { [string]$request.data_dir })
    Assert-NoReparsePath $dataDir
    New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
    $leasePath = Join-Path $dataDir 'setup.lock'
    Assert-NoReparsePath $leasePath
    try {
        $setupLease = [IO.File]::Open($leasePath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    } catch [IO.IOException] {
        if (($_.Exception.GetBaseException().HResult -band 0xFFFF) -in @(32, 33)) {
            throw 'Một phiên thiết lập khác đang chạy. Hãy chờ phiên đó hoàn tất rồi thử lại.'
        }
        throw
    }
    $logDir = Join-Path $dataDir 'logs'
    Assert-NoReparsePath $logDir
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $script:SetupLogPath = Join-Path $logDir 'setup-latest.log'
    Assert-NoReparsePath $script:SetupLogPath
    Write-InstallLog ("Bắt đầu thiết lập lúc {0:u}. Nhật ký: {1}" -f [DateTime]::Now, $script:SetupLogPath)
    $script:CancelPath = [string]$request.cancel_path
    if ($script:CancelPath -and -not ([IO.Path]::GetFullPath($script:CancelPath)).StartsWith(($dataDir.TrimEnd('\', '/') + '\cancel\'), [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Đường dẫn dấu hủy không thuộc thư mục ứng dụng.'
    }
    $selection = Assert-InstallSettings $settings
    Set-AivdDevEnvironment $devProfile
    if ($devProfile -and -not $devProfile.OllamaOwned) { Assert-AivdDevPortFree }
    $engine = Join-Path (Split-Path -Parent $PSScriptRoot) 'engine'
    $requirements = Join-Path $engine 'requirements.txt'
    if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) { throw 'Thiếu requirements.txt trong tài nguyên ứng dụng.' }
    $script:CacheDir = Join-Path $dataDir 'downloads'
    Assert-NoReparsePath $script:CacheDir
    New-Item -ItemType Directory -Path $script:CacheDir -Force | Out-Null
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $env:PYTHON_MANAGER_AUTOMATIC_INSTALL = 'false'
    $env:PYLAUNCHER_ALLOW_INSTALL = $null
    $env:PYLAUNCHER_ALWAYS_INSTALL = $null
    $env:PYLAUNCHER_DRYRUN = $null
    $env:PYTHONUTF8 = '1'
    $env:OLLAMA_HOST = if ($devProfile) { '127.0.0.1:11435' } else { '127.0.0.1:11434' }
    $env:OLLAMA_NO_CLOUD = '1'
    $env:NO_COLOR = '1'

    Write-InstallProgress 1 'Đang tìm Python tương thích trên máy...'
    $python = Get-ExistingPython $settings $dataDir
    if (-not $python) {
        $archive = Join-Path $script:CacheDir 'python.3.11.9.nupkg.zip'
        Get-VerifiedDownload $script:PortablePythonSource $archive 'Python 3.11 x64' 2 10 -Hash $script:PortablePythonHash
        $pythonDir = Join-Path $dataDir 'runtime\python311-nuget'
        Write-InstallProgress 11 'Đang giải nén Python riêng cho ứng dụng...'
        $python = Install-PortablePython $archive $pythonDir
    }
    Write-InstallLog "Dùng Python: $python"

    $ffmpegCandidates = @([string]$settings.ffmpeg_path, (Join-Path $dataDir 'tools\ffmpeg\bin\ffmpeg.exe'))
    $ffprobeCandidates = @([string]$settings.ffprobe_path, (Join-Path $dataDir 'tools\ffmpeg\bin\ffprobe.exe'))
    if (-not $devProfile) {
        $ffmpegOnPath = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
        $ffprobeOnPath = Get-Command ffprobe.exe -ErrorAction SilentlyContinue
        $ffmpegCandidates += $ffmpegOnPath.Source
        $ffprobeCandidates += $ffprobeOnPath.Source
    }
    $ffmpeg = Get-ExistingTool $ffmpegCandidates @('-version') '^ffmpeg version'
    $siblingProbe = if ($ffmpeg) { Join-Path (Split-Path -Parent $ffmpeg) 'ffprobe.exe' } else { '' }
    $ffprobe = Get-ExistingTool ($ffprobeCandidates + @($siblingProbe)) @('-version') '^ffprobe version'
    if (-not $ffmpeg -or -not $ffprobe) {
        $archive = Join-Path $script:CacheDir 'ffmpeg-9.0.2-essentials_build.zip'
        Get-VerifiedDownload 'https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-essentials_build.zip' $archive 'FFmpeg và FFprobe' 13 24 -Hash '60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba'
        Write-InstallProgress 25 'Đang giải nén FFmpeg vào thư mục ứng dụng...'
        Copy-ToolArchive $archive (Join-Path $dataDir 'tools\ffmpeg') 'ffmpeg.exe'
        if (-not $ffmpeg) { $ffmpeg = Get-ExistingTool @((Join-Path $dataDir 'tools\ffmpeg\bin\ffmpeg.exe')) @('-version') '^ffmpeg version' }
        if (-not $ffprobe) { $ffprobe = Get-ExistingTool @((Join-Path $dataDir 'tools\ffmpeg\bin\ffprobe.exe')) @('-version') '^ffprobe version' }
        if (-not $ffmpeg -or -not $ffprobe) { throw 'FFmpeg/FFprobe chưa chạy được. Xem nhật ký và kiểm tra chính sách Windows.' }
    }

    $ollamaCandidates = @((Join-Path $dataDir 'tools\ollama\ollama.exe'))
    if (-not $devProfile) {
        $ollamaOnPath = Get-Command ollama.exe -ErrorAction SilentlyContinue
        $ollamaCandidates += @((Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'), $ollamaOnPath.Source)
    }
    $ollama = Get-ExistingTool $ollamaCandidates @('--version') '(?i)ollama|client version'
    if (-not $ollama -and $devProfile) {
        $archive = Join-Path $script:CacheDir 'ollama-0.40.16-windows-amd64.zip'
        Get-VerifiedDownload $script:PortableOllamaSource $archive 'Ollama portable' 28 41 -Hash $script:PortableOllamaHash
        Write-InstallProgress 42 'Extracting portable Ollama inside the project...'
        $ollama = Install-PortableOllama $archive (Join-Path $dataDir 'tools\ollama')
        $ollama = Get-ExistingTool @($ollama) @('--version') '(?i)ollama|client version'
        if (-not $ollama) { throw 'The project-local portable Ollama executable could not run.' }
    } elseif (-not $ollama) {
        $installer = Join-Path $script:CacheDir 'OllamaSetup.exe'
        Get-VerifiedDownload 'https://ollama.com/download/OllamaSetup.exe' $installer 'Ollama Windows' 28 41 -Publisher @('Ollama Inc.', 'Ollama, Inc.')
        Write-InstallProgress 42 'Đang cài Ollama cho tài khoản Windows hiện tại...'
        Write-InstallLog 'Trình cài chính thức có thể mở Ollama sau khi cài và thêm Ollama vào PATH của tài khoản hiện tại.'
        Invoke-InstallCommand $installer @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-')
        $ollama = Get-ExistingTool @((Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe')) @('--version') '(?i)ollama|client version'
        if (-not $ollama) { throw 'Ollama chưa cài được cho tài khoản hiện tại. Xem nhật ký trình cài và kiểm tra chính sách Windows.' }
    }

    $venv = Join-Path $dataDir 'venv'
    $venvPython = Join-Path $venv 'Scripts\python.exe'
    Write-InstallProgress 45 'Đang chuẩn bị môi trường Python riêng...'
    $venvPython = Initialize-InstallVenv $python $dataDir
    Write-InstallProgress 48 'Đang cài thư viện AI vào venv...'
    Invoke-InstallCommand $venvPython @('-X', 'utf8', '-E', '-s', '-m', 'pip', '--disable-pip-version-check', 'install', '--requirement', $requirements)
    if ($selection.tts_mode -eq 'gpu') {
        Write-InstallProgress 60 'Đang chuẩn bị thư viện PyTorch CUDA đã chọn...'
        Invoke-InstallCommand $venvPython @('-m', 'pip', '--disable-pip-version-check', 'install', '--upgrade', '--index-url', 'https://download.pytorch.org/whl/cu128', 'torch==2.8.0', 'torchaudio==2.8.0')
        Invoke-InstallCommand $venvPython @('-m', 'pip', '--disable-pip-version-check', 'install', 'transformers==4.57.6')
        Write-InstallLog 'GPU vẫn cần driver NVIDIA và runtime CUDA/cuDNN tương thích. Bộ cài không thay đổi driver hệ thống.'
    }
    Invoke-InstallCommand $venvPython @('-m', 'pip', 'check')
    Write-InstallProgress 67 'Đang kiểm tra và tải model Whisper/VieNeu còn thiếu...'
    $modelArgs = @('-X', 'utf8', '-E', '-s', '-B', '-m', 'dubbing.setup_models', '--data-dir', $dataDir, '--whisper-model', $selection.whisper)
    if ($settings.tts_model_path) { $modelArgs += @('--tts-model-path', [string]$settings.tts_model_path) }
    if ($selection.tts_mode -eq 'gpu') { $modelArgs += '--gpu' }
    Push-Location -LiteralPath $engine
    try { Invoke-InstallCommand $venvPython $modelArgs } finally { Pop-Location }

    Write-InstallProgress 84 'Đang kiểm tra Ollama local...'
    if (($devProfile -and -not $devProfile.OllamaOwned) -or -not (Get-LocalOllamaResponse)) {
        $logDir = Join-Path $dataDir 'logs'
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
        $session = [Guid]::NewGuid().ToString('N')
        if ($devProfile) { Assert-AivdDevPortFree }
        $ownedServer = Start-Process -FilePath $ollama -ArgumentList @('serve') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir "ollama-install-$session.out.log") -RedirectStandardError (Join-Path $logDir "ollama-install-$session.err.log")
        $connected = $false
        for ($attempt = 0; $attempt -lt 45; $attempt++) {
            Test-InstallCancellation
            if ($ownedServer.HasExited) { break }
            if ((-not $devProfile -or (Test-AivdOwnedOllamaEndpoint $ownedServer.Id)) -and (Get-LocalOllamaResponse)) { $connected = $true; break }
            Start-Sleep -Milliseconds 1000
        }
        if (-not $connected) { throw "Could not start owned Ollama at $env:OLLAMA_HOST. Review the Ollama logs in $dataDir\logs." }
    }
    Write-InstallProgress 88 ("Đang kiểm tra và tải {0}..." -f $selection.qwen)
    Invoke-InstallCommand $ollama @('pull', $selection.qwen)
    Write-InstallProgress 98 'Đã chuẩn bị xong các thành phần; đang kiểm tra lại...'
    if ($ownedServer -and -not $ownedServer.HasExited) { Stop-Process -Id $ownedServer.Id -ErrorAction SilentlyContinue; $ownedServer.WaitForExit(3000) | Out-Null }
    $ownedServer = $null
    Write-InstallEvent @{ type = 'result'; data = @{ settings_patch = @{ python_path = $venvPython; ffmpeg_path = $ffmpeg; ffprobe_path = $ffprobe }; ollama_path = $ollama } }
    exit 0
} catch {
    Write-InstallEvent @{ type = 'error'; message = $_.Exception.Message }
    exit 1
} finally {
    if ($ownedServer -and -not $ownedServer.HasExited) { Stop-Process -Id $ownedServer.Id -ErrorAction SilentlyContinue }
    if ($setupLease) { $setupLease.Dispose() }
}
