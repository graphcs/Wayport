<#
.SYNOPSIS
    Set up Wayport on this machine. Run once after cloning.

.DESCRIPTION
    Creates a virtual environment, installs Wayport, saves the relay settings,
    and prints this machine's connection code.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -Token "abc123" -Secret "my-shared-secret"
#>
[CmdletBinding()]
param(
    [string]$RelayUrl = "wss://relay-production-587a.up.railway.app",
    [string]$Token,
    [string]$Secret
)

$ErrorActionPreference = "Stop"

function Write-Step($msg)  { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "    $msg" -ForegroundColor Yellow }

# Work from the repository root regardless of where this was invoked.
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

Write-Host ""
Write-Host "  Wayport setup" -ForegroundColor White
Write-Host "  $repoRoot" -ForegroundColor DarkGray

# --- 1. Find a suitable Python -------------------------------------------
Write-Step "Looking for Python 3.11 or newer"

$python = $null
# The py launcher is the most reliable way to get a specific version on Windows.
foreach ($candidate in @(
    @{ Cmd = "py";      Args = @("-3.13") },
    @{ Cmd = "py";      Args = @("-3.12") },
    @{ Cmd = "py";      Args = @("-3.11") },
    @{ Cmd = "python";  Args = @() },
    @{ Cmd = "python3"; Args = @() }
)) {
    $exe = Get-Command $candidate.Cmd -ErrorAction SilentlyContinue
    if (-not $exe) { continue }
    try {
        $version = & $candidate.Cmd @($candidate.Args + @("-c", "import sys; print('%d.%d' % sys.version_info[:2])")) 2>$null
    } catch { continue }
    if (-not $version) { continue }
    $parts = $version.Trim().Split(".")
    if ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 11) {
        $python = @($candidate.Cmd) + $candidate.Args
        Write-Ok "Found Python $($version.Trim()) via '$($candidate.Cmd) $($candidate.Args -join ' ')'"
        break
    }
}

if (-not $python) {
    Write-Host ""
    Write-Host "  Python 3.11 or newer is required and was not found." -ForegroundColor Red
    Write-Host "  Install it from https://www.python.org/downloads/ and make sure" -ForegroundColor Red
    Write-Host "  you tick 'Add python.exe to PATH' in the installer." -ForegroundColor Red
    exit 1
}

# --- 2. Virtual environment ----------------------------------------------
Write-Step "Creating the virtual environment"
if (Test-Path ".venv") {
    Write-Ok "Reusing the existing .venv"
} else {
    & $python[0] @($python[1..($python.Length-1)] + @("-m", "venv", ".venv"))
    if ($LASTEXITCODE -ne 0) { throw "Could not create the virtual environment" }
    Write-Ok "Created .venv"
}

$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$wayport    = Join-Path $repoRoot ".venv\Scripts\wayport.exe"
if (-not (Test-Path $venvPython)) { throw "Virtual environment looks broken: $venvPython not found" }

# --- 3. Install -----------------------------------------------------------
Write-Step "Installing Wayport (this takes a minute the first time)"
& $venvPython -m pip install --quiet --upgrade pip
& $venvPython -m pip install --quiet -e .
if ($LASTEXITCODE -ne 0) { throw "Installation failed" }
Write-Ok "Installed"

# --- 4. Relay settings ----------------------------------------------------
Write-Step "Saving relay settings"

if (-not $Token) {
    Write-Host "    The relay token must match the other machine." -ForegroundColor DarkGray
    Write-Host "    On macOS/Linux find it with:  cat ~/.wayport-relay-token" -ForegroundColor DarkGray
    $secure = Read-Host "    Relay token" -AsSecureString
    $Token = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
}
if (-not $Token) { throw "A relay token is required" }

$setupArgs = @("setup", "--relay-url", $RelayUrl, "--relay-token", $Token)
if ($Secret) { $setupArgs += @("--secret", $Secret) }
& $wayport @setupArgs
if ($LASTEXITCODE -ne 0) { throw "Could not save the configuration" }

# --- 5. Check it works ----------------------------------------------------
Write-Step "Checking everything works"
& $wayport doctor

# --- 6. What to do next ---------------------------------------------------
Write-Host ""
Write-Host "  Done. Use Wayport with:" -ForegroundColor White
Write-Host ""
Write-Host "    .\.venv\Scripts\wayport.exe share            " -NoNewline -ForegroundColor Green
Write-Host "# share this machine's connection" -ForegroundColor DarkGray
Write-Host "    .\.venv\Scripts\wayport.exe connect <code>   " -NoNewline -ForegroundColor Green
Write-Host "# use the other machine's" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Or activate the environment first, then just 'wayport':" -ForegroundColor DarkGray
Write-Host "    .\.venv\Scripts\Activate.ps1" -ForegroundColor DarkGray
Write-Host ""

if (-not $Secret) {
    Write-Warn2 "No shared secret set, so the relay can read your traffic."
    Write-Warn2 "Set the same one on both machines with:  wayport setup --secret <value>"
    Write-Host ""
}
