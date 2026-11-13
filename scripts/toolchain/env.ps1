param([string]$ToolchainRoot = (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) '.verification\toolchain'))

$ErrorActionPreference = 'Stop'
$toolchainRoot = [IO.Path]::GetFullPath($ToolchainRoot)
$projectRoot = Split-Path -Parent (Split-Path -Parent $toolchainRoot)
$rustRoot = Join-Path $toolchainRoot 'rust-local'
$vcRoot = Join-Path $toolchainRoot 'msvc\VC\Tools\MSVC\14.44.35207'
$sdkRoot = Join-Path $toolchainRoot 'msvc\Windows Kits\10'
$sdkVersion = '10.0.26100.0'
$vcBin = Join-Path $vcRoot 'bin\Hostx64\x64'
$sdkBin = Join-Path $sdkRoot "bin\$sdkVersion\x64"
foreach ($required in @((Join-Path $rustRoot 'bin\rustc.exe'), (Join-Path $rustRoot 'bin\cargo.exe'), (Join-Path $vcBin 'cl.exe'), (Join-Path $vcBin 'link.exe'), (Join-Path $sdkBin 'rc.exe'))) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Local native tool is missing: $required. Run setup.cmd first." }
}
$env:PATH = "$rustRoot\bin;$vcBin;$sdkBin;$env:PATH"
$env:LIB = "$vcRoot\lib\x64;$sdkRoot\Lib\$sdkVersion\um\x64;$sdkRoot\Lib\$sdkVersion\ucrt\x64"
$env:INCLUDE = "$vcRoot\include;$sdkRoot\Include\$sdkVersion\ucrt;$sdkRoot\Include\$sdkVersion\shared;$sdkRoot\Include\$sdkVersion\um;$sdkRoot\Include\$sdkVersion\winrt"
$env:VCToolsInstallDir = "$vcRoot\"
$env:VCINSTALLDIR = (Join-Path $toolchainRoot 'msvc\VC\')
$env:VSINSTALLDIR = (Join-Path $toolchainRoot 'msvc\')
$env:WindowsSdkDir = "$sdkRoot\"
$env:WindowsSDKVersion = "$sdkVersion\"
$env:CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER = Join-Path $vcBin 'link.exe'
$env:CC = Join-Path $vcBin 'cl.exe'
$env:CXX = $env:CC
$env:AR = Join-Path $vcBin 'lib.exe'
$env:RC = Join-Path $sdkBin 'rc.exe'
$env:RUSTC = Join-Path $rustRoot 'bin\rustc.exe'
$env:CARGO = Join-Path $rustRoot 'bin\cargo.exe'
$env:CARGO_HOME = Join-Path $toolchainRoot 'cargo-home'
$env:CARGO_TARGET_DIR = Join-Path $projectRoot '.verification\target'
