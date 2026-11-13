[CmdletBinding()]
param([switch]$SkipInstall)

$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'This script builds a Windows x64 NSIS installer on Windows.'
}
$project = Split-Path -Parent $PSScriptRoot
$target = 'x86_64-pc-windows-msvc'
foreach ($dependency in 'node.exe', 'npm.cmd', 'cargo.exe', 'rustc.exe') {
    if (-not (Get-Command $dependency -ErrorAction SilentlyContinue)) {
        throw "Missing $dependency. Install the Windows build prerequisites listed in README.md."
    }
}

Push-Location -LiteralPath $project
try {
    if (-not $SkipInstall) {
        & npm.cmd ci
        if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
    }
    $tauri = Join-Path $project 'node_modules\.bin\tauri.cmd'
    if (-not (Test-Path -LiteralPath $tauri -PathType Leaf)) { throw 'Install project npm dependencies first.' }
    & $tauri build --target $target --bundles nsis
    if ($LASTEXITCODE -ne 0) { throw 'Tauri/NSIS build failed. See the output above.' }

    $version = (Get-Content -LiteralPath 'src-tauri/tauri.conf.json' -Raw | ConvertFrom-Json).version
    $cargoTarget = if ($env:CARGO_TARGET_DIR) { [IO.Path]::GetFullPath($env:CARGO_TARGET_DIR) } else {
        Join-Path $project 'src-tauri/target'
    }
    $bundle = Join-Path $cargoTarget "$target/release/bundle/nsis"
    $installers = @(Get-ChildItem -LiteralPath $bundle -File -Filter "*_${version}_*-setup.exe")
    if ($installers.Count -ne 1) { throw "Expected one NSIS installer for $version in $bundle." }
    $release = Join-Path $project 'release'
    New-Item -ItemType Directory -Path $release -Force | Out-Null
    $result = Join-Path $release 'AI-Video-Dubbing-Setup.exe'
    Copy-Item -LiteralPath $installers[0].FullName -Destination $result -Force
    Get-FileHash -LiteralPath $result -Algorithm SHA256
} finally {
    Pop-Location
}
