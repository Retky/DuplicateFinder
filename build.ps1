# Build the windowed Duplicate Finder folder. Stops at the first failing step.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$pyinstaller = Join-Path $PSScriptRoot ".venv\Scripts\pyinstaller.exe"

& $python -m pytest
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $pyinstaller --noconfirm --clean DuplicateFinder.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$dist = Join-Path $PSScriptRoot "dist\DuplicateFinder"
$exe = Join-Path $dist "DuplicateFinder.exe"
Copy-Item -Path (Join-Path $PSScriptRoot "README.md") -Destination (Join-Path $dist "README.md") -Force

if (-not (Test-Path -LiteralPath $exe)) {
    throw "Missing $exe"
}
if (-not (Test-Path -LiteralPath (Join-Path $dist "README.md"))) {
    throw "README.md was not copied next to the executable"
}

$expected = (& $python -c "import duplicate_finder; print(duplicate_finder.__version__)").Trim()
$fileVersion = (Get-Item -LiteralPath $exe).VersionInfo.FileVersion
if ($fileVersion -ne $expected) {
    throw "FileVersion '$fileVersion' does not match $expected"
}

$bytes = [System.IO.File]::ReadAllBytes($exe)
$peOffset = [BitConverter]::ToInt32($bytes, 0x3C)
$subsystem = [BitConverter]::ToUInt16($bytes, $peOffset + 24 + 68)
if ($subsystem -ne 2) {
    throw "Expected the Windows GUI subsystem (2), got $subsystem"
}

$embedded = [System.Text.Encoding]::ASCII.GetString($bytes)
if ($embedded -notmatch "longPathAware") {
    throw "The embedded manifest is missing longPathAware"
}

$zip = Join-Path $PSScriptRoot "dist\DuplicateFinder-$expected-windows.zip"
if (Test-Path -LiteralPath $zip) {
    Remove-Item -LiteralPath $zip -Force
}
Compress-Archive -Path $dist -DestinationPath $zip

Write-Host "Built $exe ($fileVersion)"
Write-Host "Packed $zip"
