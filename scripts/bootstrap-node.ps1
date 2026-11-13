[CmdletBinding()]
param([switch]$CheckOnly, [switch]$Offline)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$devRoot = Join-Path $repoRoot '.dev'
. (Join-Path $PSScriptRoot 'install-deps.ps1') -FunctionsOnly
$script:CancelPath = ''
$script:SetupLogPath = ''
$script:CacheDir = Join-Path $devRoot 'downloads'
$nodeDir = Join-Path $devRoot 'tools\node'
$node = Join-Path $nodeDir 'node.exe'
$version = 'v22.22.3'
$nodeHash = '6c8d54f635feff4df76c2ca80f45332eb2ff57d25226edce36592e51a177ee33'

function Test-ProjectNode([string]$Executable) {
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $false }
    Assert-NoReparsePath $Executable
    Assert-TrustedInstaller $Executable @('OpenJS Foundation', 'Node.js Foundation')
    $probe = Invoke-InventoryProcess $Executable @('--version') -TimeoutMs 10000
    $probe.exit_code -eq 0 -and $probe.stdout.Trim() -eq $version
}

if (-not (Test-ProjectNode $node)) {
    if ($CheckOnly) { throw 'Project-local Node.js is missing or incomplete. Run setup.cmd.' }
    $archive = Join-Path $script:CacheDir 'node-v22.22.3-win-x64.zip'
    if ($Offline) { Assert-ArchiveHash $archive $nodeHash }
    else { Get-VerifiedDownload 'https://nodejs.org/dist/v22.22.3/node-v22.22.3-win-x64.zip' $archive 'Node.js cho project' 0 100 -Hash $nodeHash }
    Assert-NoReparsePath $nodeDir
    $tools = Split-Path -Parent $nodeDir
    New-Item -ItemType Directory -Path $tools -Force | Out-Null
    $stage = Join-Path $tools ('.node-stage-' + [guid]::NewGuid().ToString('N'))
    Expand-Archive -LiteralPath $archive -DestinationPath $stage
    $content = Join-Path $stage 'node-v22.22.3-win-x64'
    if (-not (Test-ProjectNode (Join-Path $content 'node.exe'))) { throw 'Node.js trong gói tải không đúng phiên bản.' }
    if (Test-Path -LiteralPath $nodeDir) {
        Move-Item -LiteralPath $nodeDir -Destination ($nodeDir + '-backup-' + [guid]::NewGuid().ToString('N'))
    }
    Move-Item -LiteralPath $content -Destination $nodeDir
}
Write-Output $node
