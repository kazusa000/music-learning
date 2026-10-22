# Delete only an explicitly owned subtree. Never follow links or change attributes.
function Assert-OwnedCleanupPath([string]$Path, [string]$AllowedRoot) {
    $base = [IO.Path]::GetFullPath($AllowedRoot).TrimEnd('\')
    $target = [IO.Path]::GetFullPath($Path)
    if (-not $target.StartsWith($base+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Cleanup target must be below its allowed root' }
    $parent = [IO.Path]::GetDirectoryName($target)
    while ($parent) {
        if ([IO.Directory]::Exists($parent) -and ([IO.File]::GetAttributes($parent) -band [IO.FileAttributes]::ReparsePoint)) { throw "Linked cleanup ancestor: $parent" }
        $parent = [IO.Path]::GetDirectoryName($parent)
    }
    return $target
}

function Remove-OwnedCleanupTree([string]$Path, [string]$AllowedRoot, [switch]$RemoveCreatedLinks) {
    $target = Assert-OwnedCleanupPath $Path $AllowedRoot
    $stats = @{path=$target;files=0;directories=0;links_removed=0;skipped=@();errors=@();retained_parents=@()}
    function Remove-OwnedNode([string]$node) {
        try { $attributes = [IO.File]::GetAttributes($node) }
        catch [IO.FileNotFoundException] { return }
        catch [IO.DirectoryNotFoundException] { return }
        catch {
            if ($_.Exception.InnerException -is [IO.FileNotFoundException] -or $_.Exception.InnerException -is [IO.DirectoryNotFoundException]) { return }
            $stats.errors += @{path=$node;error=$_.Exception.Message}; return
        }
        if ($attributes -band [IO.FileAttributes]::ReparsePoint) {
            if ($RemoveCreatedLinks) {
                try {
                    if ($attributes -band [IO.FileAttributes]::Directory) { [IO.Directory]::Delete($node,$false) }
                    else { [IO.File]::Delete($node) }
                    $stats.links_removed++
                } catch { $stats.errors += @{path=$node;error=$_.Exception.Message} }
            } else { $stats.skipped += $node }
            return
        }
        if ($attributes -band [IO.FileAttributes]::Directory) {
            try { $children = [IO.Directory]::GetFileSystemEntries($node) }
            catch { $stats.errors += @{path=$node;error=$_.Exception.Message}; return }
            foreach ($child in $children) { Remove-OwnedNode $child }
            try {
                if ([IO.Directory]::GetFileSystemEntries($node).Length -eq 0) { [IO.Directory]::Delete($node,$false); $stats.directories++ }
                else { $stats.retained_parents += $node }
            } catch { $stats.errors += @{path=$node;error=$_.Exception.Message} }
        } else {
            try { [IO.File]::Delete($node); $stats.files++ }
            catch { $stats.errors += @{path=$node;error=$_.Exception.Message} }
        }
    }
    Remove-OwnedNode $target
    return $stats
}

function Get-CleanupPathUsers([string]$Path) {
    $target = [IO.Path]::GetFullPath($Path)
    return @(Get-CimInstance Win32_Process | Where-Object {
        $_.ProcessId -ne $PID -and $_.CommandLine -and $_.CommandLine.IndexOf($target,[StringComparison]::OrdinalIgnoreCase) -ge 0
    } | Select-Object -ExpandProperty ProcessId)
}
