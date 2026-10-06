# bootstrap.ps1 - Windows 11 entry point. Ansible has no native Windows
# control node, so the toolchain lives in WSL2 and this script forwards to
# bootstrap.sh there. Arguments are passed through unchanged.
param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $Rest)

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    Write-Error "WSL2 is required: run 'wsl --install -d AlmaLinux-9' (or Ubuntu), then re-run."
    exit 1
}
$here = (wsl.exe wslpath -a "$PSScriptRoot").Trim()
wsl.exe bash -lc "cd '$here' && ./bootstrap.sh $($Rest -join ' ')"
exit $LASTEXITCODE
