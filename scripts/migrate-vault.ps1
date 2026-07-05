# Copies the Tesseract vault out of OneDrive to C:\Vaults\Tesseract.
# Copy (not move): the OneDrive original stays frozen as a backup.
# ASCII-only on purpose: PS 5.1 mis-decodes non-ASCII in BOM-less .ps1 files.
param(
    [string]$Source = "$env:USERPROFILE\OneDrive\Documents\Tesseract",
    [string]$Dest = "C:\Vaults\Tesseract"
)

if (-not (Test-Path $Source)) { Write-Error "Source not found: $Source"; exit 1 }
if (Test-Path $Dest) { Write-Error "Destination already exists: $Dest - refusing to merge."; exit 1 }

Write-Host "NOTE: before running, right-click the vault folder in OneDrive and choose"
Write-Host "'Always keep on this device' so every file is fully downloaded (hydrated)."
Write-Host ""
Write-Host "Copying $Source -> $Dest ..."
robocopy $Source $Dest /E /COPY:DAT /DCOPY:T /R:2 /W:2 /NFL /NDL | Out-Null
if ($LASTEXITCODE -ge 8) { Write-Error "robocopy reported failure ($LASTEXITCODE)"; exit 1 }

$src = Get-ChildItem $Source -Recurse -File | Measure-Object -Sum Length
$dst = Get-ChildItem $Dest -Recurse -File | Measure-Object -Sum Length
Write-Host "Files: source=$($src.Count) dest=$($dst.Count)"
Write-Host "Bytes: source=$($src.Sum) dest=$($dst.Sum)"
if ($src.Count -ne $dst.Count) { Write-Error "File counts differ - investigate before proceeding."; exit 1 }
if ($src.Sum -ne $dst.Sum) { Write-Error "Byte totals differ (cloud-only placeholders?) - hydrate the OneDrive folder and retry."; exit 1 }

Write-Host ""
Write-Host "Done. Next steps (manual):"
Write-Host " 1. Open Obsidian -> 'Open folder as vault' -> $Dest"
Write-Host " 2. Verify plugins/settings loaded (they live in .obsidian inside the vault)."
Write-Host " 3. Do NOT open the OneDrive copy again; delete it after ~2 weeks."
