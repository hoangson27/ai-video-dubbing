# WebView2 is an OS runtime. Importing these helpers never installs or changes anything.
# Microsoft: https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution
# Evergreen bootstrapper: https://developer.microsoft.com/en-us/microsoft-edge/webview2/
function Get-AivdWebView2RegistryValue {
    param([string]$Hive, [string]$View, [string]$KeyPath)
    $base = $null
    $key = $null
    try {
        $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::$Hive, [Microsoft.Win32.RegistryView]::$View)
        $key = $base.OpenSubKey($KeyPath, $false)
        if ($key) { return $key.GetValue('pv', $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames) }
    } catch [Security.SecurityException] {
        return $null
    } catch [UnauthorizedAccessException] {
        return $null
    } finally {
        if ($key) { $key.Dispose() }
        if ($base) { $base.Dispose() }
    }
}

function Get-AivdWebView2Status {
    $key = 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    $locations = @(
        @{ Hive='CurrentUser'; View='Default'; Scope='user'; Path=('HKEY_CURRENT_USER\' + $key) },
        @{ Hive='LocalMachine'; View='Registry32'; Scope='machine'; Path=('HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\' + $key.Substring(9)) },
        @{ Hive='LocalMachine'; View='Registry64'; Scope='machine'; Path=('HKEY_LOCAL_MACHINE\' + $key) }
    )
    $found = @()
    foreach ($location in $locations) {
        $raw = Get-AivdWebView2RegistryValue $location.Hive $location.View $key
        $version = $null
        if ($raw -is [string] -and [version]::TryParse($raw.Trim(), [ref]$version) -and $version -gt [version]'0.0.0.0') {
            $found += [pscustomobject]@{ installed=$true; version=$version.ToString(); scope=$location.Scope; registry_path=$location.Path; parsed=$version }
        }
    }
    if ($found.Count) { return $found | Sort-Object parsed -Descending | Select-Object -First 1 installed,version,scope,registry_path }
    [pscustomobject]@{ installed=$false; version=''; scope='none'; registry_path='' }
}

function Test-AivdWebView2Elevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    try { ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) }
    finally { $identity.Dispose() }
}

function Assert-AivdWebView2Path {
    param([string]$Path, [string]$Root)
    $boundary = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    $resolved = [IO.Path]::GetFullPath($Path)
    if ($boundary -notmatch '^[A-Za-z]:[\\/].+' -or ($resolved -ine $boundary -and -not $resolved.StartsWith($boundary + '\', [StringComparison]::OrdinalIgnoreCase))) {
        throw 'Thư mục WebView2 phải nằm trong thư mục dữ liệu cục bộ của ứng dụng.'
    }
    $current = $resolved
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            if (((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Không tải WebView2 qua liên kết hoặc junction: $current" }
        }
        $current = [IO.Path]::GetDirectoryName($current)
    }
    $resolved
}

function Write-AivdWebView2Log {
    param([string]$Path, [string]$Message)
    [IO.File]::AppendAllText($Path, ('[{0:u}] {1}' -f [DateTime]::UtcNow, $Message) + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
}

function Save-AivdWebView2Bootstrapper {
    param([string]$Destination)
    $uri = [Uri]'https://go.microsoft.com/fwlink/p/?LinkId=2124703'
    $protocol = [Net.ServicePointManager]::SecurityProtocol
    $response = $null
    $inputStream = $null
    $outputStream = $null
    try {
        [Net.ServicePointManager]::SecurityProtocol = $protocol -bor [Net.SecurityProtocolType]::Tls12
        for ($redirect = 0; $redirect -lt 10; $redirect++) {
            $allowed = $uri.Host -eq 'go.microsoft.com' -or $uri.Host -eq 'download.microsoft.com' -or $uri.Host.EndsWith('.dl.delivery.mp.microsoft.com', [StringComparison]::OrdinalIgnoreCase)
            if ($uri.Scheme -ne 'https' -or -not $uri.IsDefaultPort -or -not $allowed -or $uri.UserInfo) { throw 'Địa chỉ tải WebView2 không thuộc nguồn Microsoft được cho phép.' }
            $request = [Net.HttpWebRequest]::Create($uri)
            $request.AllowAutoRedirect = $false
            $request.Timeout = 60000
            $request.ReadWriteTimeout = 60000
            $response = $request.GetResponse()
            $code = [int]$response.StatusCode
            if ($code -ge 300 -and $code -lt 400) {
                $uri = [Uri]::new($uri, [string]$response.Headers['Location'])
                $response.Dispose(); $response=$null
                continue
            }
            if ($code -ne 200 -or $response.ContentLength -gt 64MB) { throw 'Phản hồi tải WebView2 không hợp lệ hoặc vượt giới hạn 64 MB.' }
            $inputStream = $response.GetResponseStream()
            $outputStream = [IO.File]::Open($Destination, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            $buffer = New-Object byte[] 1048576
            $received = 0L
            while (($count = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                $received += $count
                if ($received -gt 64MB) { throw 'Gói WebView2 vượt giới hạn 64 MB.' }
                $outputStream.Write($buffer, 0, $count)
            }
            if ($received -le 0 -or ($response.ContentLength -gt 0 -and $received -ne $response.ContentLength)) { throw 'Gói WebView2 chưa tải đủ dữ liệu.' }
            return
        }
        throw 'Nguồn WebView2 chuyển hướng quá nhiều lần.'
    } finally {
        if ($outputStream) { $outputStream.Dispose() }
        if ($inputStream) { $inputStream.Dispose() }
        if ($response) { $response.Dispose() }
        [Net.ServicePointManager]::SecurityProtocol = $protocol
    }
}

function Get-AivdWebView2Signature {
    param([string]$Path)
    Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    $publisher = if ($signature.SignerCertificate) { $signature.SignerCertificate.GetNameInfo([Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false) } else { '' }
    [pscustomobject]@{ valid=($signature.Status -eq 'Valid'); publisher=$publisher }
}

function Assert-AivdWebView2Signature {
    param([string]$Path)
    $signature = Get-AivdWebView2Signature $Path
    if (-not $signature.valid -or $signature.publisher -ne 'Microsoft Corporation') { throw "Chữ ký Microsoft của trình cài WebView2 không hợp lệ: $Path. Đã dừng cài đặt." }
}

function Invoke-AivdWebView2Installer {
    param([string]$Path, [string]$LogPath, [string]$TempDir)
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $Path
    $start.Arguments = '/silent /install'
    $start.WorkingDirectory = Split-Path -Parent $Path
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.EnvironmentVariables['TEMP'] = $TempDir
    $start.EnvironmentVariables['TMP'] = $TempDir
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    try {
        $null = $process.Start()
        $output = $process.StandardOutput.ReadToEndAsync()
        $errors = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(600000)) { throw "Trình cài WebView2 vẫn chạy quá 10 phút (PID $($process.Id)). Xem nhật ký: $LogPath" }
        $stdout = $output.GetAwaiter().GetResult()
        $stderr = $errors.GetAwaiter().GetResult()
        if ($stdout) { Write-AivdWebView2Log $LogPath $stdout.Trim() }
        if ($stderr) { Write-AivdWebView2Log $LogPath $stderr.Trim() }
        return $process.ExitCode
    } finally { $process.Dispose() }
}

function Install-AivdWebView2 {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][string]$DataDir)
    $status = Get-AivdWebView2Status
    if ($status.installed) { return $status }
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'WebView2 chỉ được chuẩn bị trên Windows.' }
    if (Test-AivdWebView2Elevated) { throw 'Máy chưa có WebView2. Hãy chạy setup từ PowerShell thường, không dùng Administrator, để cài cho tài khoản hiện tại.' }
    if ($DataDir -notmatch '^[A-Za-z]:[\\/].+') { throw 'Cần đường dẫn tuyệt đối tới thư mục dữ liệu ứng dụng.' }
    $root = Assert-AivdWebView2Path $DataDir $DataDir
    $downloads = Assert-AivdWebView2Path (Join-Path $root 'downloads') $root
    $logs = Assert-AivdWebView2Path (Join-Path $root 'logs') $root
    $tempDir = Assert-AivdWebView2Path (Join-Path $root 'temp') $root
    foreach ($directory in @($root, $downloads, $logs, $tempDir)) { [IO.Directory]::CreateDirectory($directory) | Out-Null }
    $lease = $null
    $candidate = $null
    $logPath = Assert-AivdWebView2Path (Join-Path $logs 'webview2-setup.log') $root
    try {
        $lockPath = Assert-AivdWebView2Path (Join-Path $root 'webview2-setup.lock') $root
        $lease = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        $status = Get-AivdWebView2Status
        if ($status.installed) { return $status }
        Write-AivdWebView2Log $logPath 'Preparing the Microsoft WebView2 Evergreen runtime for the current user.'
        $installer = Assert-AivdWebView2Path (Join-Path $downloads 'MicrosoftEdgeWebview2Setup.exe') $root
        $cached = $false
        if (Test-Path -LiteralPath $installer -PathType Leaf) {
            try { Assert-AivdWebView2Signature $installer; $cached=$true } catch { Write-AivdWebView2Log $logPath 'Cached installer is invalid; downloading a fresh Microsoft bootstrapper.' }
        }
        if (-not $cached) {
            $candidate = Assert-AivdWebView2Path (Join-Path $downloads ('webview2-' + [guid]::NewGuid().ToString('N') + '.part.exe')) $root
            Save-AivdWebView2Bootstrapper $candidate
            Assert-AivdWebView2Signature $candidate
            Move-Item -LiteralPath $candidate -Destination $installer -Force -ErrorAction Stop
            $candidate=$null
        }
        Assert-AivdWebView2Signature $installer
        Write-AivdWebView2Log $logPath "Running signed Microsoft bootstrapper: $installer /silent /install"
        $code = Invoke-AivdWebView2Installer $installer $logPath $tempDir
        Write-AivdWebView2Log $logPath "Bootstrapper exit code: $code"
        if ($code -ne 0) { throw "Cài WebView2 thất bại (mã $code). Xem nhật ký: $logPath" }
        for ($attempt=0; $attempt -lt 20; $attempt++) {
            $status = Get-AivdWebView2Status
            if ($status.installed) { Write-AivdWebView2Log $logPath "WebView2 ready: $($status.version) [$($status.scope)]."; return $status }
            Start-Sleep -Milliseconds 250
        }
        throw "Trình cài đã kết thúc nhưng chưa phát hiện được WebView2. Xem nhật ký: $logPath"
    } catch {
        if ($lease) { try { Write-AivdWebView2Log $logPath $_.Exception.Message } catch { } }
        throw
    } finally {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { try { Remove-Item -LiteralPath $candidate -Force -ErrorAction Stop } catch { } }
        if ($lease) { $lease.Dispose() }
    }
}
