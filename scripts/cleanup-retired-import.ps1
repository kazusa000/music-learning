# User-run cleanup only. Without -Execute this script prints the exact whitelist.
[CmdletBinding()]
param([switch]$Execute, [switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
if ($Execute -and $CheckOnly) { throw 'Choose either -Execute or -CheckOnly.' }
$projectRoot = 'E:\Intrest\music-learning'
if ([IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')) -ne $projectRoot) {
    throw 'This whitelist belongs only to E:\Intrest\music-learning.'
}
$relativeTargets = @(
    'data\models\demucs-cbc8a9b1a870',
    'data\models\mert-d8ba1c745e73',
    'data\models\muscriptor-f32236969308',
    'data\models\sheetsage-b83c3d79a6d9',
    'data\envs\sheetsage',
    'data\app-data\backups\projects-1792520318088701500.sqlite3',
    'data\maintenance\large-beats-migration\accept',
    'data\t\large-beats-final',
    'data\t\large-beats-tests-v1',
    'data\t\large-beats-tests-v2',
    'data\t\stream-weights-tests',
    'data\t\stream-weights-final'
)
$targets = @($relativeTargets | ForEach-Object { Join-Path $projectRoot $_ })
$objectsRoot = Join-Path $projectRoot 'data\app-data\objects'
$planPath = Join-Path $projectRoot 'data\maintenance\large-beats-migration\project-cas-cleanup-plan.json'
$plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
if ([IO.Path]::GetFullPath($plan.root) -ne $objectsRoot) { throw 'Unexpected CAS plan root.' }

function Assert-NoLinkedParents([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    if (-not $full.StartsWith($projectRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Outside the project: $Path"
    }
    $current = $full
    while ($current -and $current.StartsWith($projectRoot, [StringComparison]::OrdinalIgnoreCase)) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Linked path refused: $current" }
        }
        if ($current -eq $projectRoot) { break }
        $current = Split-Path -Parent $current
    }
}

# Validate the entire whitelist before any deletion.
foreach ($target in $targets) {
    Assert-NoLinkedParents $target
    if ((Test-Path -LiteralPath $target) -and (Get-Item -LiteralPath $target).PSIsContainer) {
        $linked = Get-ChildItem -LiteralPath $target -Recurse -Force -Attributes ReparsePoint | Select-Object -First 1
        if ($linked) { throw "Linked child refused: $($linked.FullName)" }
    }
}
$casTargets = @()
foreach ($entry in $plan.files) {
    if ($entry.id -notmatch '^[a-f0-9]{64}$') { throw 'Invalid CAS hash in plan.' }
    $expected = Join-Path (Join-Path $objectsRoot $entry.id.Substring(0, 2)) $entry.id
    if ([IO.Path]::GetFullPath($entry.path) -ne $expected) { throw 'CAS plan path mismatch.' }
    Assert-NoLinkedParents $expected
    $casTargets += $expected
}
Write-Host 'Directories/files on the fixed whitelist:'
$targets | ForEach-Object { Write-Host $_ }
Write-Host "CAS: $($casTargets.Count) exact files listed in $planPath"
if (-not $Execute -and -not $CheckOnly) {
    Write-Host 'Preview only. Use -CheckOnly to verify the database and CAS, or -Execute to delete.'
    return
}

$python = Join-Path $projectRoot 'data\envs\app\Scripts\python.exe'
$manager = Join-Path $projectRoot 'scripts\manage_app.py'
$savedConfig = $env:MUSIC_APP_CONFIG
$savedPythonPath = $env:PYTHONPATH
$savedUtf8 = $env:PYTHONUTF8
$stopped = $false
$checkDatabase = @'
import sqlite3,sys
from pathlib import Path
p=Path(sys.argv[1])/'data/app-data/projects.sqlite3'
with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as db:
    projects=db.execute('SELECT COUNT(*) FROM projects').fetchone()[0]
    active=db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running','cancel_requested')").fetchone()[0]
    if projects or active:
        raise SystemExit('Refusing cleanup: a new project or active project task exists.')
'@
try {
    $env:MUSIC_APP_CONFIG = Join-Path $projectRoot 'runtime\app.local.json'
    $env:PYTHONPATH = Join-Path $projectRoot 'src'
    $env:PYTHONUTF8 = '1'
    # Send source through stdin: Windows PowerShell 5.1 strips embedded quotes
    # from native -c arguments. The source is ASCII and never shell-expanded.
    $checkDatabase | & $python - $projectRoot
    if ($LASTEXITCODE -ne 0) { throw 'Database preflight failed; nothing deleted.' }
    # This manager owns only the application, not the independent Pianoteq verifier.
    if ($Execute) {
        & $python $manager stop
        if ($LASTEXITCODE -ne 0) { throw 'Application stop failed.' }
        $stopped = $true
        foreach ($port in @(8765, 8779)) {
            $listener = New-Object Net.Sockets.TcpClient
            try {
                $connection = $listener.BeginConnect('127.0.0.1', $port, $null, $null)
                if ($connection.AsyncWaitHandle.WaitOne(300) -and $listener.Connected) {
                    throw "Application port $port is still active; nothing deleted."
                }
            } finally { $listener.Dispose() }
        }
    }
    $checkDatabase | & $python - $projectRoot
    if ($LASTEXITCODE -ne 0) { throw 'Database changed; nothing deleted.' }
    foreach ($entry in $plan.files) {
        if (Test-Path -LiteralPath $entry.path) {
            if ((Get-Item -LiteralPath $entry.path).Length -ne $entry.bytes -or
                (Get-FileHash -LiteralPath $entry.path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.id) {
                throw "CAS evidence changed; nothing deleted: $($entry.path)"
            }
        }
    }
    if ($CheckOnly) {
        Write-Host 'Database and CAS checks passed. No services stopped; nothing deleted.'
        return
    }
    $freeBefore = (Get-PSDrive -Name E).Free
    foreach ($target in $targets) {
        if (Test-Path -LiteralPath $target) {
            $resolved = (Resolve-Path -LiteralPath $target).ProviderPath
            if ($resolved -ne $target) { throw "Resolved target changed: $target" }
            Assert-NoLinkedParents $resolved
            Remove-Item -LiteralPath $resolved -Recurse -Force
        }
    }
    foreach ($target in $casTargets) {
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force }
    }
    $remaining = @(@($targets) + @($casTargets) | Where-Object { Test-Path -LiteralPath $_ })
    Write-Host ("Net free-space increase: {0:N2} GiB" -f (((Get-PSDrive -Name E).Free - $freeBefore) / 1GB))
    Write-Host "Remaining targets: $($remaining.Count)"
    $remaining | ForEach-Object { Write-Host $_ }
} finally {
    if ($stopped) { & $python $manager start }
    $env:MUSIC_APP_CONFIG = $savedConfig
    $env:PYTHONPATH = $savedPythonPath
    $env:PYTHONUTF8 = $savedUtf8
}
