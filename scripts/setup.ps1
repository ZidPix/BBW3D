<#
.SYNOPSIS
    One-shot BBW3D setup for Windows: clone cad-agent, build it, start it,
    install the toolbelt, and run the endpoint verification.

.DESCRIPTION
    Run this from anywhere inside the BBW3D repo:

        .\scripts\setup.ps1

    Everything is idempotent - safe to re-run. Nothing is installed globally
    except the bbw3d package itself (editable, from this folder).

.PARAMETER CadAgentPath
    Where to clone/find the cad-agent source. Defaults to a sibling folder
    next to this repo.

.PARAMETER SkipBuild
    Skip the docker build (use when the image already exists and you only
    want to restart and re-verify).

.PARAMETER Python
    Python launcher to use. Defaults to 'py' if present, otherwise 'python'.
#>

#Requires -Version 5.1
[CmdletBinding()]
param(
    [string] $CadAgentPath = "",
    [switch] $SkipBuild,
    [string] $Python = ""
)

$ErrorActionPreference = "Stop"

function Write-Step  ([string] $Text) { Write-Host "`n==> $Text" -ForegroundColor Cyan }
function Write-Ok    ([string] $Text) { Write-Host "    OK  $Text" -ForegroundColor Green }
function Write-Warn2 ([string] $Text) { Write-Host "    !   $Text" -ForegroundColor Yellow }

function Stop-With ([string] $Text, [string] $Fix) {
    Write-Host "`nSTOPPED: $Text" -ForegroundColor Red
    if ($Fix) { Write-Host "Fix:     $Fix" -ForegroundColor Yellow }
    exit 1
}

function Test-Command ([string] $Name) {
    $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Resolve-Docker {
    <#
        Docker Desktop can be installed and running while docker.exe is absent
        from THIS shell's PATH - Windows snapshots PATH when a process starts,
        so any terminal opened before the install cannot see it. Fall back to
        the standard install locations rather than claiming Docker is missing.
    #>
    if (Test-Command "docker") { return "docker" }

    $roots = @(
        $env:ProgramFiles,
        ${env:ProgramFiles(x86)},
        $env:LOCALAPPDATA,
        "C:\Program Files"
    ) | Where-Object { $_ }

    foreach ($root in $roots) {
        $candidate = "$root\Docker\Docker\resources\bin\docker.exe"
        if (Test-Path $candidate) { return $candidate }
    }
    if (Test-Path "C:\ProgramData\DockerDesktop\version-bin\docker.exe") {
        return "C:\ProgramData\DockerDesktop\version-bin\docker.exe"
    }
    return $null
}

# --- where are we -----------------------------------------------------------

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
Write-Step "BBW3D setup"
Write-Host "    repo: $RepoRoot"

if (-not (Test-Path (Join-Path $RepoRoot "pyproject.toml"))) {
    Stop-With "This does not look like the BBW3D repo (no pyproject.toml)." `
              "cd into your BBW3D clone and run .\scripts\setup.ps1 again."
}

# --- prerequisites ----------------------------------------------------------

Write-Step "Checking prerequisites"

if (-not (Test-Command "git")) {
    Stop-With "git not found." "Install Git for Windows: https://git-scm.com/download/win"
}
Write-Ok "git"

$Docker = Resolve-Docker
if (-not $Docker) {
    Stop-With "docker.exe not found, on PATH or in the usual install locations." `
              "If Docker Desktop IS installed, open a NEW PowerShell window and re-run - Windows caches PATH per session, so a shell opened before the install cannot see it. Otherwise install Docker Desktop: https://docs.docker.com/desktop/install/windows-install/"
}
if ($Docker -ne "docker") { Write-Warn2 "docker not on PATH; using $Docker" }

& $Docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Stop-With "Docker is installed but the daemon is not responding." `
              "Start Docker Desktop, wait for the whale icon to settle, then re-run this script."
}
Write-Ok "docker (daemon responding)"

if (-not $Python) {
    if (Test-Command "py") { $Python = "py" }
    elseif (Test-Command "python") { $Python = "python" }
    else { Stop-With "No Python found." "Install Python 3.11+: https://www.python.org/downloads/windows/" }
}
$pyVersion = (& $Python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null)
if ($LASTEXITCODE -ne 0) { Stop-With "'$Python' did not run." "Pass a working one: .\scripts\setup.ps1 -Python python3" }
Write-Ok "python $pyVersion ($Python)"

$verOk = & $Python -c "import sys; print(1 if sys.version_info >= (3, 11) else 0)"
if ($verOk.Trim() -ne "1") {
    Stop-With "Python 3.11+ required, found $pyVersion." "Install a newer Python, then re-run."
}

# --- cad-agent source -------------------------------------------------------

if (-not $CadAgentPath) {
    $CadAgentPath = Join-Path (Split-Path -Parent $RepoRoot) "cad-agent"
}

Write-Step "cad-agent source"
if (Test-Path (Join-Path $CadAgentPath ".git")) {
    Write-Ok "already cloned at $CadAgentPath"
} else {
    $parent = Split-Path -Parent $CadAgentPath
    if (-not (Test-Path $parent)) {
        Stop-With "Cannot clone into '$parent' (it does not exist)." `
                  "Pass somewhere writable: .\scripts\setup.ps1 -CadAgentPath C:\dev\cad-agent"
    }
    Write-Host "    cloning into $CadAgentPath"
    git clone --depth 1 https://github.com/Svetlana-DAO-LLC/cad-agent $CadAgentPath
    if ($LASTEXITCODE -ne 0) {
        Stop-With "git clone failed (see above)." `
                  "If it was a permissions error, pick a writable folder: -CadAgentPath C:\dev\cad-agent"
    }
    Write-Ok "cloned"
}

# --- build the image --------------------------------------------------------

if ($SkipBuild) {
    Write-Step "Skipping docker build (-SkipBuild)"
} else {
    Write-Step "Building cad-agent:latest (first run takes several minutes)"
    & $Docker build -t cad-agent:latest $CadAgentPath
    if ($LASTEXITCODE -ne 0) { Stop-With "docker build failed (see above)." "" }
    Write-Ok "image built"
}

# --- start it ---------------------------------------------------------------

Write-Step "Starting the container"
foreach ($dir in @("workspace", "renders")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $RepoRoot $dir) | Out-Null
}
& $Docker compose up -d
if ($LASTEXITCODE -ne 0) { Stop-With "docker compose up failed (see above)." "" }
Write-Ok "compose up"

# --- install the toolbelt ---------------------------------------------------

Write-Step "Installing the bbw3d toolbelt (editable, no dependencies)"
& $Python -m pip install -e . --quiet
if ($LASTEXITCODE -ne 0) { Stop-With "pip install failed (see above)." "" }
Write-Ok "bbw3d installed"

# --- wait for health --------------------------------------------------------

Write-Step "Waiting for the container to answer /health"
$healthy = $false
for ($i = 1; $i -le 40; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri "http://localhost:8123/health" -TimeoutSec 3 -UseBasicParsing
        if ($resp.StatusCode -eq 200) { $healthy = $true; break }
    } catch {
        Start-Sleep -Seconds 3
    }
}
if (-not $healthy) {
    Write-Warn2 "No answer on http://localhost:8123/health after ~2 minutes."
    Write-Host ""
    Write-Host "Collecting diagnostics..." -ForegroundColor Yellow

    $diagDir = Join-Path $RepoRoot "out"
    New-Item -ItemType Directory -Force -Path $diagDir | Out-Null
    $diag = Join-Path $diagDir "container-diagnostics.txt"
    "BBW3D container diagnostics - $(Get-Date -Format s)" | Set-Content -Path $diag

    function Add-Diag ([string] $Title, [scriptblock] $Action) {
        Write-Host ""
        Write-Host "--- $Title ---" -ForegroundColor Yellow
        "" | Add-Content -Path $diag
        "--- $Title ---" | Add-Content -Path $diag
        try {
            $output = & $Action 2>&1 | Out-String
        } catch {
            $output = "(command failed: $_)"
        }
        Write-Host $output
        $output | Add-Content -Path $diag
    }

    Add-Diag "docker compose ps" { & $Docker compose ps -a }
    Add-Diag "docker compose logs (last 60 lines)" { & $Docker compose logs --tail 60 }
    # If this answers but localhost does not, the server is bound to 127.0.0.1
    # inside the container and the published port cannot reach it.
    Add-Diag "health probe from INSIDE the container" {
        & $Docker compose exec -T cad-agent python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8123/health').read())"
    }
    Add-Diag "what the container is listening on" {
        & $Docker compose exec -T cad-agent sh -c "ss -ltnp 2>/dev/null || netstat -ltnp 2>/dev/null || echo '(no ss/netstat in image)'"
    }

    Write-Host ""
    Write-Host "Saved to: $diag" -ForegroundColor Cyan
    Stop-With "Container never answered on port 8123." "Send me $diag - it has everything needed to work out why."
}
Write-Ok "container is healthy"

# --- verify -----------------------------------------------------------------

Write-Step "Verifying the API contract (this is the bit to send back)"
$outDir = Join-Path $RepoRoot "out"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$report = Join-Path $outDir "verify-report.json"

& $Python -m bbw3d.cli verify | Tee-Object -FilePath $report
$verifyExit = $LASTEXITCODE

Write-Host ""
if ($verifyExit -eq 0) {
    Write-Host "All documented endpoints answered." -ForegroundColor Green
} else {
    Write-Host "Some endpoints did not behave as documented - that is exactly what this check is for." -ForegroundColor Yellow
}
Write-Host "Report saved to: $report" -ForegroundColor Cyan
Write-Host "Send me that file (or paste it) and I'll correct the client." -ForegroundColor Cyan
Write-Host ""
Write-Host "Then you're ready:  bbw3d new .\your-design.png" -ForegroundColor White
