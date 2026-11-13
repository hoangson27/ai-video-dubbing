# Shared project-local policy. Dot-sourcing this file never changes the caller's environment.
function Get-AivdSettingValue {
    param($Settings, [string]$Name)
    if ($Settings -is [Collections.IDictionary]) { return $Settings[$Name] }
    if ($Settings) { return $Settings.$Name }
    $null
}

function Test-AivdDevMode {
    param($Settings)
    $enabled = Get-AivdSettingValue $Settings 'dev_mode'
    ([string]$enabled -in @('1', 'true')) -or $env:AIVD_DEV_MODE -eq '1'
}

function Assert-AivdDevPath {
    param([string]$Path, [string]$Root)
    $resolved = [IO.Path]::GetFullPath($Path)
    $boundary = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    if ($resolved -ne $boundary -and -not $resolved.StartsWith($boundary + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Development path must stay inside $boundary : $resolved"
    }
    $current = $resolved
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Development path contains a link: $current" }
        }
        $parent = [IO.Path]::GetDirectoryName($current)
        if ($parent -eq $current) { break }
        $current = $parent
    }
    $resolved
}

function Get-AivdDevProfile {
    param($Settings, [string]$DataDir)
    if (-not (Test-AivdDevMode $Settings)) { return $null }
    $root = [string](Get-AivdSettingValue $Settings 'dev_root')
    if (-not $root) { $root = $env:AIVD_DEV_ROOT }
    if (-not $root) { $root = $DataDir }
    if (-not $root -or $root -notmatch '^[A-Za-z]:[\\/]' -or $root.Contains([char]0)) { throw 'Development root must be an absolute local directory.' }
    $root = [IO.Path]::GetFullPath($root).TrimEnd('\', '/')
    $null = Assert-AivdDevPath $root $root
    $effective = @{}
    if ($Settings -is [Collections.IDictionary]) { foreach ($key in $Settings.Keys) { $effective[$key] = $Settings[$key] } }
    elseif ($Settings) { foreach ($property in $Settings.PSObject.Properties) { $effective[$property.Name] = $property.Value } }
    $effective.dev_mode = $true
    $effective.dev_root = $root
    foreach ($entry in @{
        python_path = 'venv\Scripts\python.exe'; ffmpeg_path = 'tools\ffmpeg\bin\ffmpeg.exe'; ffprobe_path = 'tools\ffmpeg\bin\ffprobe.exe'
        ollama_path = 'tools\ollama\ollama.exe'; tts_model_path = 'models\vieneu'; projects_dir = 'projects'; output_dir = 'output'
    }.GetEnumerator()) { $effective[$entry.Key] = Assert-AivdDevPath (Join-Path $root $entry.Value) $root }
    $effective.ollama_host = '127.0.0.1:11435'
    $environment = @{ AIVD_DEV_MODE = '1'; AIVD_DEV_ROOT = $root; OLLAMA_HOST = '127.0.0.1:11435'; OLLAMA_NO_CLOUD = '1'; PYTHONNOUSERSITE = '1'; PYTHON_MANAGER_AUTOMATIC_INSTALL = 'false'; PIP_CONFIG_FILE = 'nul' }
    foreach ($entry in @{
        OLLAMA_MODELS = 'models\ollama'; USERPROFILE = 'profile'; APPDATA = 'profile\AppData\Roaming'; LOCALAPPDATA = 'profile\AppData\Local'
        PIP_CACHE_DIR = 'cache\pip'; HF_HOME = 'cache\huggingface'; HF_HUB_CACHE = 'cache\huggingface\hub'; HUGGINGFACE_HUB_CACHE = 'cache\huggingface\hub'
        HF_ASSETS_CACHE = 'cache\huggingface\assets'; HUGGINGFACE_ASSETS_CACHE = 'cache\huggingface\assets'; HF_XET_CACHE = 'cache\huggingface\xet'
        TORCH_HOME = 'cache\torch'; TORCH_EXTENSIONS_DIR = 'cache\torch_extensions'; HF_TOKEN_PATH = 'cache\huggingface\token'; XDG_CACHE_HOME = 'cache'; UV_CACHE_DIR = 'cache\uv'; NUMBA_CACHE_DIR = 'cache\numba'; GRADIO_TEMP_DIR = 'cache\gradio'
        TEMP = 'temp'; TMP = 'temp'; PYTHONUSERBASE = 'profile\python'; PYTHONPYCACHEPREFIX = 'cache\pycache'
    }.GetEnumerator()) { $environment[$entry.Key] = Assert-AivdDevPath (Join-Path $root $entry.Value) $root }
    [pscustomobject]@{ Root = $root; Settings = [pscustomobject]$effective; Environment = $environment; OllamaOwned = ((Get-AivdSettingValue $Settings 'ollama_owned') -eq $true) }
}

function Set-AivdDevEnvironment {
    param($Profile)
    if (-not $Profile) { return }
    foreach ($name in $Profile.Environment.Keys) { [Environment]::SetEnvironmentVariable($name, $Profile.Environment[$name], 'Process') }
    foreach ($name in 'PYTHONPATH','PYTHONHOME','PYLAUNCHER_ALLOW_INSTALL','PYLAUNCHER_ALWAYS_INSTALL','PYLAUNCHER_DRYRUN','PIP_TARGET','PIP_PREFIX','PIP_USER','PIP_ROOT','PIP_LOG','PIP_BUILD_TRACKER','PIP_PYTHON','PIP_SRC','TRANSFORMERS_CACHE') { [Environment]::SetEnvironmentVariable($name, $null, 'Process') }
    foreach ($path in @($Profile.Environment.TEMP, $Profile.Environment.USERPROFILE, $Profile.Environment.APPDATA, $Profile.Environment.LOCALAPPDATA)) {
        New-Item -ItemType Directory -Path $path -Force | Out-Null
    }
}

function Set-AivdDevProcessEnvironment {
    param([Diagnostics.ProcessStartInfo]$StartInfo, $Profile)
    if (-not $Profile) { return }
    foreach ($name in $Profile.Environment.Keys) { $StartInfo.EnvironmentVariables[$name] = $Profile.Environment[$name] }
    foreach ($name in 'PYTHONPATH','PYTHONHOME','PYLAUNCHER_ALLOW_INSTALL','PYLAUNCHER_ALWAYS_INSTALL','PYLAUNCHER_DRYRUN','PIP_TARGET','PIP_PREFIX','PIP_USER','PIP_ROOT','PIP_LOG','PIP_BUILD_TRACKER','PIP_PYTHON','PIP_SRC','TRANSFORMERS_CACHE') { $StartInfo.EnvironmentVariables.Remove($name) }
}

function Assert-AivdDevPortFree {
    param([scriptblock]$IsBusy)
    if ($IsBusy) { $busy = & $IsBusy } else {
        $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Parse('127.0.0.1'), 11435)
        $listener.Server.ExclusiveAddressUse = $true
        $busy = $false
        try { $listener.Start() } catch { $busy = $true } finally { $listener.Stop() }
    }
    if ($busy) { throw 'Development Ollama port 11435 is busy. Stop the other server before retrying; an unknown daemon cannot be reused.' }
}

function Assert-AivdSignedOllama {
    param([string]$Path)
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    $publisher = if ($signature.SignerCertificate) { $signature.SignerCertificate.GetNameInfo([Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false) } else { '' }
    if ($signature.Status -ne 'Valid' -or $publisher -notin @('Ollama Inc.', 'Ollama, Inc.')) { throw "Ollama signature is not valid: $Path" }
}

function Test-AivdOwnedOllamaEndpoint {
    param([int]$ServerProcessId)
    $listeners = @(Get-NetTCPConnection -LocalPort 11435 -State Listen -ErrorAction SilentlyContinue)
    [bool]($listeners | Where-Object { $_.LocalAddress -eq '127.0.0.1' -and $_.OwningProcess -eq $ServerProcessId })
}

function Start-AivdDevOllama {
    param($Profile)
    Assert-AivdDevPortFree
    $executable = Assert-AivdDevPath $Profile.Settings.ollama_path $Profile.Root
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) { throw "Missing project-local Ollama: $executable. Run dependency installation in the app first." }
    Assert-AivdSignedOllama $executable
    $logs = Assert-AivdDevPath (Join-Path $Profile.Root 'logs') $Profile.Root
    New-Item -ItemType Directory -Path $logs -Force | Out-Null
    $session = [Guid]::NewGuid().ToString('N')
    $server = Start-Process -FilePath $executable -ArgumentList @('serve') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logs "ollama-cli-$session.out.log") -RedirectStandardError (Join-Path $logs "ollama-cli-$session.err.log")
    try {
        for ($attempt = 0; $attempt -lt 45; $attempt++) {
            if ($server.HasExited) { break }
            if ((Test-AivdOwnedOllamaEndpoint $server.Id) -and (Get-LocalOllamaModels '127.0.0.1:11435').available) { return $server }
            Start-Sleep -Milliseconds 1000
        }
        throw "Could not start owned development Ollama at 127.0.0.1:11435. Review $logs."
    } catch {
        Stop-AivdOwnedServer $server
        throw
    }
}

function Stop-AivdOwnedServer {
    param($Server)
    if ($Server) {
        try { if (-not $Server.HasExited) { $Server.Kill(); $null = $Server.WaitForExit(3000) } } finally { $Server.Dispose() }
    }
}
