[CmdletBinding()]
param([string[]]$PytestArgs=@('tests/service','tests/engine','tests/test_release_runtime.py','-q'), [switch]$KeepFixtures)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'owned-cleanup.ps1')
$workspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
foreach ($argument in $PytestArgs) {
    if ($argument -match '^--(basetemp|junitxml)(=|$)') { throw 'The runner owns basetemp and report paths' }
}
$id = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,8)
$temporary = Join-Path $workspace ('data\tmp\tests\'+$id)
$reports = Join-Path $workspace 'runtime\test-reports'
[IO.Directory]::CreateDirectory($temporary) | Out-Null
[IO.Directory]::CreateDirectory($reports) | Out-Null
$python = Join-Path $workspace 'data\envs\app\Scripts\python.exe'
$fixture = Join-Path $temporary 'fixtures'
$parameters = @('-m','pytest')+$PytestArgs+@('--basetemp',$fixture,'-o',('cache_dir='+$temporary+'\cache'),'--junitxml',(Join-Path $reports ($id+'.xml')))
function Quote-Argument([string]$value) {
    if ($value -notmatch '[\s"]') { return $value }
    return '"'+([regex]::Replace([regex]::Replace($value,'(\\*)"','$1$1\"'),'(\\+)$','$1$1'))+'"'
}
$code = 1
$record = @{id=$id;temporary=$temporary;arguments=$PytestArgs;keep_requested=[bool]$KeepFixtures}
$previousPythonPath = $env:PYTHONPATH
$previousBytecode = $env:PYTHONDONTWRITEBYTECODE
try {
    $env:PYTHONPATH = (Join-Path $workspace 'src')+';'+(Join-Path $workspace 'scripts')
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $process = Start-Process -FilePath $python -ArgumentList (($parameters | ForEach-Object { Quote-Argument $_ }) -join ' ') -WorkingDirectory $workspace -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $reports ($id+'.log')) -RedirectStandardError (Join-Path $reports ($id+'.stderr.log'))
    $nativeHandle = $process.Handle
    $process.WaitForExit(); $process.Refresh(); $code=$process.ExitCode
    if ($null -eq $code) { throw 'Test process exited without a readable exit code' }
} finally {
    $env:PYTHONPATH = $previousPythonPath
    $env:PYTHONDONTWRITEBYTECODE = $previousBytecode
    $users = Get-CleanupPathUsers $temporary
    if ($KeepFixtures -or $users.Count) { $record.retained=$true; $record.active_pids=$users; Write-Warning "Temporary fixtures retained: $temporary" }
    else { $record.cleanup=Remove-OwnedCleanupTree $temporary (Join-Path $workspace 'data\tmp\tests') -RemoveCreatedLinks }
    $record.exit_code=$code
    $record | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $reports ($id+'.json')) -Encoding utf8
}
Get-Content -LiteralPath (Join-Path $reports ($id+'.log')) -ErrorAction SilentlyContinue
exit $code
