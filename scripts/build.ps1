$ErrorActionPreference = "Stop"

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw ".venv not found. Run scripts/dev-setup.ps1 first."
}

$workspace = (Get-Location).Path
$pyinstallerRoot = Join-Path $workspace ".pyinstaller"
$workPath = Join-Path $pyinstallerRoot "work"
$specPath = $pyinstallerRoot
$releaseRoot = Join-Path $workspace "release"
$artifactDir = Join-Path $releaseRoot "ScreenCommentatorLauncher"
$zipPath = Join-Path $releaseRoot "ScreenCommentatorLauncher-win64.zip"
$demoOverlayPath = Join-Path $releaseRoot "demo-overlay.png"

function Remove-PathWithRetry {
    param(
        [string]$Path,
        [bool]$Required
    )

    if (-not (Test-Path $Path)) {
        return
    }

    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            Remove-Item -Recurse -Force $Path
            return
        }
        catch {
            if ($attempt -eq 5) {
                if ($Required) {
                    throw
                }
                Write-Warning "Could not remove optional path: $Path"
                return
            }
            Start-Sleep -Seconds 2
        }
    }
}

function Compress-ArchiveWithRetry {
    param(
        [string]$SourcePath,
        [string]$DestinationPath
    )

    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            Compress-Archive -Path $SourcePath -DestinationPath $DestinationPath
            return
        }
        catch {
            if ($attempt -eq 5) {
                throw
            }
            Start-Sleep -Seconds 2
        }
    }
}

Remove-PathWithRetry -Path (Join-Path $workspace "build") -Required $false
Remove-PathWithRetry -Path (Join-Path $workspace "dist") -Required $false
Remove-PathWithRetry -Path $releaseRoot -Required $true
Remove-PathWithRetry -Path $pyinstallerRoot -Required $true

New-Item -ItemType Directory -Path $releaseRoot | Out-Null

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name ScreenCommentatorLauncher `
    --paths src `
    --distpath $releaseRoot `
    --workpath $workPath `
    --specpath $specPath `
    launcher.py

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed."
}

$exePath = Join-Path $artifactDir "ScreenCommentatorLauncher.exe"
if (-not (Test-Path $exePath)) {
    throw "Packaged executable was not generated."
}

$previousQtPlatform = $env:QT_QPA_PLATFORM
try {
    $env:QT_QPA_PLATFORM = "offscreen"

    $smoke = Start-Process -FilePath $exePath -ArgumentList @("--self-test", "smoke") -PassThru -Wait
    if ($smoke.ExitCode -ne 0) {
        throw "Packaged smoke self-test failed with exit code $($smoke.ExitCode)."
    }

    $demo = Start-Process -FilePath $exePath -ArgumentList @("--self-test", "demo-overlay", "--self-test-output", $demoOverlayPath) -PassThru -Wait
    if ($demo.ExitCode -ne 0) {
        throw "Packaged overlay self-test failed with exit code $($demo.ExitCode)."
    }

    if (-not (Test-Path $demoOverlayPath)) {
        throw "Packaged overlay self-test did not create the expected PNG."
    }
}
finally {
    if ($null -eq $previousQtPlatform) {
        Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
    }
    else {
        $env:QT_QPA_PLATFORM = $previousQtPlatform
    }
}

if (Test-Path $zipPath) {
    Remove-Item -Force $zipPath
}
Compress-ArchiveWithRetry -SourcePath $artifactDir -DestinationPath $zipPath

if (Test-Path $pyinstallerRoot) {
    Remove-Item -Recurse -Force $pyinstallerRoot
}

Write-Host "Release directory: $artifactDir"
Write-Host "Release zip: $zipPath"
Write-Host "Overlay self-test PNG: $demoOverlayPath"
