[CmdletBinding()]
param([Parameter(Mandatory)][string]$Plan, [switch]$Execute)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'owned-cleanup.ps1')
$workspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$planPath = Assert-OwnedCleanupPath $Plan $workspace
$value = Get-Content -LiteralPath $planPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([IO.Path]::GetFullPath($value.workspace) -ne $workspace) { throw 'Cleanup plan belongs to another workspace' }
if ($value.schema_version -ne 1) { throw 'Unknown cleanup plan' }
$allowed = @('data\app-data\desktop-profile','data\maintenance\native-engine\n7\tdead1','data\maintenance\native-engine\n7\tdead2','runtime\build\engine-dynamic-fixture\CMakeFiles','.pytest_cache','.ruff_cache')
$profileRuns = @('desktop-staged-final','desktop-reuse-v2','desktop-reuse','desktop-package','default-startup')
$targets = @($value.targets | ForEach-Object {
    $relative = $_.Replace('/','\')
    $profile = $false
    foreach ($run in $profileRuns) {
        if ($relative -in @("data\maintenance\native-engine\n7\$run\native-profile","data\maintenance\native-engine\n7\$run\profile","data\maintenance\native-engine\n7\$run\profile-dom")) { $profile=$true }
    }
    if ($relative -notin $allowed -and -not $profile -and $relative -notmatch '^data\\app-data\\live\\[a-f0-9]{32}$') { throw "Target not in reviewed whitelist: $relative" }
    Assert-OwnedCleanupPath (Join-Path $workspace $relative) $workspace
})
foreach ($target in $targets) {
    if ((Get-CleanupPathUsers $target).Count) { throw "Cleanup target is referenced by a running process: $target" }
    if ([IO.Path]::GetDirectoryName($target) -eq (Join-Path $workspace 'data\app-data\live')) {
        if (Test-Path -LiteralPath (Join-Path $target 'owner.json')) { throw "Session has an owner marker; leave it alone: $target" }
    }
}
if ($targets | Where-Object { $_.StartsWith((Join-Path $workspace 'data\app-data\live')+'\') }) {
    if (Get-CimInstance Win32_Process -Filter "Name='ml_track_host.exe'") { throw 'Legacy playback is running; leave legacy sessions alone' }
}
if (-not $Execute) { @{verified=$true;execute=$false;targets=$targets} | ConvertTo-Json -Depth 5; return }
$reportPath = Join-Path ([IO.Path]::GetDirectoryName($planPath)) 'cleanup-result.json'
if (Test-Path -LiteralPath $reportPath) { throw 'Do not overwrite original cleanup evidence' }
$freeBefore = [IO.DriveInfo]::new($workspace.Substring(0,3)).AvailableFreeSpace
$results = @()
foreach ($target in $targets) {
    if ((Get-CleanupPathUsers $target).Count) { throw "Target became active: $target" }
    $results += Remove-OwnedCleanupTree $target $workspace
}
$result = @{started_at=$value.created_at;completed_at=[DateTime]::UtcNow.ToString('o');results=$results;free_before_bytes=$freeBefore;free_after_bytes=[IO.DriveInfo]::new($workspace.Substring(0,3)).AvailableFreeSpace;scope='Reviewed E workspace paths only; no library/intake/fixed report/release/production asset deletion'}
$result | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $reportPath -Encoding utf8
@{files=($results|Measure-Object -Property files -Sum).Sum;directories=($results|Measure-Object -Property directories -Sum).Sum;errors=@($results|ForEach-Object {$_.errors}).Count;skipped_links=@($results|ForEach-Object {$_.skipped}).Count;observed_free_delta_gib=($result.free_after_bytes-$freeBefore)/1GB} | ConvertTo-Json
