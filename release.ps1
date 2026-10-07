# One-click local release: verify version -> tests -> build -> push -> GitHub Release with exe.
# Usage: .\release.ps1 -Tag v1.3.7 [-NotesFile notes.md]
# NOTE: pushing a commit that adds/modifies .github/workflows/* requires a token
# with the "workflow" scope; this script only pushes code + tags.
param(
    [Parameter(Mandatory = $true)][string]$Tag,
    [string]$NotesFile
)
$ErrorActionPreference = "Stop"

$repo = "Sword6988/boc-fx-rates"
# App name built from code points (ASCII-safe, same as build.ps1).
$appName = -join ([char[]](0x4e2d,0x884c,0x6c47,0x7387,0x6362,0x7b97))

Push-Location $PSScriptRoot
try {
    # 1. Tag must match version_info.txt (vX.Y.Z -> X.Y.Z.0)
    if ($Tag -notmatch '^v\d+\.\d+\.\d+$') {
        throw "Tag must look like v1.2.3, got: $Tag"
    }
    $expectedVer = $Tag.Substring(1) + ".0"
    $vi = Get-Content -LiteralPath "version_info.txt" -Raw
    if ($vi -notmatch [regex]::Escape("'FileVersion', '$expectedVer'")) {
        throw "version_info.txt has no FileVersion '$expectedVer'; bump the version first"
    }
    Write-Host "[1/6] Version check OK ($expectedVer)"

    # 2. Clean tracked tree required (untracked local files are ignored)
    $dirty = & git status --porcelain -uno
    if ($dirty) { throw "Tracked working tree not clean; commit first:`n$dirty" }
    Write-Host "[2/6] Working tree clean"

    # 3. Run all offline tests
    $py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $py)) { $py = "python" }
    & $py run_tests.py
    if ($LASTEXITCODE -ne 0) { throw "Tests failed" }
    Write-Host "[3/6] Tests passed"

    # 4. Build exe
    # NOTE: do NOT trust $LASTEXITCODE after build.ps1 — some environments
    # hook the deletion of build intermediates and leave a stale non-zero
    # exit code behind. Verify the output artifact instead.
    & (Join-Path $PSScriptRoot "build.ps1")
    $exe = Join-Path $PSScriptRoot (Join-Path "dist" "$appName.exe")
    if (-not (Test-Path -LiteralPath $exe)) { throw "Build output missing: $exe" }
    Write-Host "[4/6] Build OK"

    # 5. Create tag if missing, push branch + tag
    # Retries: the local proxy intermittently aborts the CONNECT tunnel (502 /
    # schannel close_notify); a retry usually succeeds.
    # Refuse if the remote tag exists at a different commit (avoid wrong release)
    $remoteRef = & git ls-remote --tags origin "refs/tags/$Tag"
    if ($remoteRef) {
        $remoteSha = ((($remoteRef | Select-Object -First 1) -split '\s+')[0]).Trim()
        $localRef = & git rev-parse --verify --quiet "refs/tags/$Tag"
        if (-not $localRef) { $localRef = & git rev-parse --verify HEAD }
        $localSha = (($localRef | Select-Object -First 1)).Trim()
        if ($remoteSha -ne $localSha) {
            throw "Remote tag $Tag exists at $remoteSha but local is $localSha; refusing to overwrite"
        }
        Write-Host "Remote tag $Tag already at $localSha (OK)"
    }
    $localTag = & git tag --list $Tag
    if (-not $localTag) { & git tag $Tag }
    foreach ($what in @("main", $Tag)) {
        $done = $false
        for ($attempt = 1; $attempt -le 3; $attempt++) {
            $out = & git push origin $what 2>&1
            if ($LASTEXITCODE -eq 0) { $done = $true; break }
            Write-Host "git push $what failed (attempt $attempt): $out"
            Start-Sleep -Seconds 10
        }
        if (-not $done) { throw "git push $what failed after 3 attempts" }
    }
    Write-Host "[5/6] Pushed main + $Tag"

    # 6. Create GitHub Release via API and upload exe
    $credLines = ("protocol=https`nhost=github.com`n`n" | & git credential fill)
    $token = ($credLines | Where-Object { $_ -like 'password=*' }) -replace '^password=', ''
    if (-not $token) { throw "No GitHub credential found via git credential fill" }
    $auth = "Authorization: token $token"

    if ($NotesFile) {
        # An explicitly passed notes file must exist; silent fallback hides typos
        if (-not (Test-Path -LiteralPath $NotesFile)) {
            throw "NotesFile not found: $NotesFile"
        }
        $notes = [System.IO.File]::ReadAllText(
            (Resolve-Path -LiteralPath $NotesFile).Path,
            [System.Text.Encoding]::UTF8)
    } else {
        $notes = "Release $Tag ($expectedVer). See commit history for details."
    }
    $body = @{
        tag_name = $Tag; name = $Tag; body = $notes
        draft = $false; prerelease = $false
    } | ConvertTo-Json
    $tmp = $env:TEMP
    $bodyFile = Join-Path $tmp "release_body_$Tag.json"
    [System.IO.File]::WriteAllText($bodyFile, $body,
        (New-Object System.Text.UTF8Encoding($false)))
    $respFile = Join-Path $tmp "release_resp_$Tag.json"
    curl.exe -s -S -X POST -H $auth -H "Content-Type: application/json" `
        --data-binary "@$bodyFile" `
        "https://api.github.com/repos/$repo/releases" -o $respFile
    $resp = [System.IO.File]::ReadAllText($respFile,
        [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    if (-not $resp.id) { throw "Create release failed: $($resp | ConvertTo-Json -Depth 3)" }
    Write-Host "Release created: $($resp.html_url)"

    # ASCII asset name avoids URL-encoding trouble with the Chinese exe name
    $assetName = "boc-fx-rates-$Tag.exe"
    $assetTmp = Join-Path $tmp $assetName
    Copy-Item -LiteralPath $exe -Destination $assetTmp -Force
    $upFile = Join-Path $tmp "release_up_$Tag.json"
    curl.exe -s -S -X POST -H $auth -H "Content-Type: application/octet-stream" `
        --data-binary "@$assetTmp" `
        "https://uploads.github.com/repos/$repo/releases/$($resp.id)/assets?name=$assetName" `
        -o $upFile
    $up = [System.IO.File]::ReadAllText($upFile,
        [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    if ($up.state -ne "uploaded") {
        # Release created but asset missing: give explicit remediation steps
        Write-Host "WARNING: release was created but the asset upload FAILED (half-finished release)."
        Write-Host "Release URL: $($resp.html_url)"
        Write-Host "Remediation: delete this release and re-run, or upload manually:"
        Write-Host "  gh release delete $Tag --yes"
        Write-Host "  gh release upload $Tag `"$assetTmp`" --clobber"
        throw "Asset upload failed: $($up | ConvertTo-Json -Depth 3)"
    }
    Write-Host "Asset uploaded: $($up.browser_download_url)"
    Write-Host "[6/6] Release OK: $Tag"
} finally {
    Pop-Location
}
