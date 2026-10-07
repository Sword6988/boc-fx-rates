# One-click build of dist exe (usage: .\build.ps1).
# Kept ASCII-only so it parses correctly regardless of console codepage.
$ErrorActionPreference = "Stop"

# App name "中行汇率换算" built from code points (ASCII-safe).
$appName = -join ([char[]](0x4e2d,0x884c,0x6c47,0x7387,0x6362,0x7b97))

Push-Location $PSScriptRoot
try {
    # 优先使用 .venv 的 Python（含 PyInstaller）；venv 损坏/缺失时回退系统 python
    $py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    $pyOK = $false
    $cfg = Join-Path $PSScriptRoot ".venv\pyvenv.cfg"
    if ((Test-Path -LiteralPath $py) -and (Test-Path -LiteralPath $cfg)) {
        # venv launcher 依赖 pyvenv.cfg 的 home；home 不存在即 venv 失效
        $venvHome = (Select-String -LiteralPath $cfg -Pattern '^home\s*=\s*' |
                     Select-Object -First 1).Line -replace '^home\s*=\s*', ''
        if ($venvHome -and (Test-Path -LiteralPath (Join-Path $venvHome "python.exe"))) {
            $prev = $ErrorActionPreference
            $ErrorActionPreference = "SilentlyContinue"
            try {
                & $py -c "import PyInstaller" *> $null
                $pyOK = ($LASTEXITCODE -eq 0)
            } catch {
                $pyOK = $false
            }
            $ErrorActionPreference = $prev
        }
    }
    if (-not $pyOK) {
        Write-Host "venv Python 不可用或缺少 PyInstaller，回退到系统 python"
        $py = "python"
        & $py -c "import PyInstaller" *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "未找到可用的 PyInstaller，请先安装：pip install pyinstaller"
        }
    }
    & $py -m PyInstaller --onefile --windowed --clean --name $appName --icon app_icon.ico --add-data "app_icon.ico;." --version-file version_info.txt boc_fx_rates.py
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed, exit code $LASTEXITCODE"
    }
    # Clean regenerable intermediates (non-fatal: some environments block
    # scripted deletion; the intermediates are rebuilt on the next run anyway)
    $build = Join-Path $PSScriptRoot "build"
    $spec = Join-Path $PSScriptRoot "$appName.spec"
    foreach ($item in @($build, $spec)) {
        if (Test-Path -LiteralPath $item) {
            try {
                Remove-Item -LiteralPath $item -Recurse -Force
            } catch {
                Write-Warning "Could not clean $item (non-fatal): $($_.Exception.Message)"
            }
        }
    }
    $exe = Join-Path $PSScriptRoot (Join-Path "dist" "$appName.exe")
    Write-Host "Build OK: $exe"
} finally {
    Pop-Location
}
