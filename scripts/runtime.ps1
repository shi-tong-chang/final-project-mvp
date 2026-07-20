param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $RuntimeArgs
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if ($env:OS -ne "Windows_NT") {
    & python3 (Join-Path $PSScriptRoot "fpmvp_runtime.py") @RuntimeArgs
    exit $LASTEXITCODE
}

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw "找不到 wsl.exe。請先安裝 WSL2 與 Ubuntu 24.04。"
}

$distroArgs = @()
$linuxRepo = $null
$wslPathMatch = [regex]::Match(
    $repoRoot,
    '^\\\\(?:wsl\$|wsl\.localhost)\\(?<distro>[^\\]+)(?<path>\\.*)$',
    [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
)

if ($wslPathMatch.Success) {
    $distroArgs = @("-d", $wslPathMatch.Groups["distro"].Value)
    $linuxRepo = $wslPathMatch.Groups["path"].Value.Replace("\", "/")
}
else {
    if ($env:FPMVP_WSL_DISTRO) {
        $distroArgs = @("-d", $env:FPMVP_WSL_DISTRO)
    }
    $convertedPath = & wsl.exe @distroArgs -- wslpath -a $repoRoot
    if ($LASTEXITCODE -ne 0 -or -not $convertedPath) {
        throw "無法將專案路徑轉換為 WSL 路徑。"
    }
    $linuxRepo = ($convertedPath | Select-Object -First 1).Trim()
}

& wsl.exe @distroArgs --cd $linuxRepo -- python3 scripts/fpmvp_runtime.py @RuntimeArgs
exit $LASTEXITCODE
