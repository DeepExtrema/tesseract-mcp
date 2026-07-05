# Copies the Tesseract vault out of OneDrive to C:\Vaults\Tesseract.
# Copy (not move): the OneDrive original stays frozen as a backup.
param(
    [string]$Source = "$env:USERPROFILE\OneDrive\Documents\Tesseract",
    [string]$Dest = "C:\Vaults\Tesseract"
)

if (-not (Test-Path $Source)) { Write-Error "Source not found: $Source"; exit 1 }
if (Test-Path $Dest) { Write-Error "Destination already exists: $Dest — refusing to merge."; exit 1 }

Write-Host "Copying $Source -> $Dest ..."
robocopy $Source $Dest /E /COPY:DAT /DCOPY:T /R:2 /W:2 /NFL /NDL | Out-Null
if ($LASTEXITCODE -ge 8) { Write-Error "robocopy reported failure ($LASTEXITCODE)"; exit 1 }

$srcCount = (Get-ChildItem $Source -Recurse -File | Measure-Object).Count
$dstCount = (Get-ChildItem $Dest -Recurse -File | Measure-Object).Count
Write-Host "Files: source=$srcCount dest=$dstCount"
if ($srcCount -ne $dstCount) { Write-Error "File counts differ — investigate before proceeding."; exit 1 }

Write-Host ""
Write-Host "Done. Next steps (manual):"
Write-Host " 1. Open Obsidian -> 'Open folder as vault' -> $Dest"
Write-Host " 2. Verify plugins/settings loaded (they live in .obsidian inside the vault)."
Write-Host " 3. Do NOT open the OneDrive copy again; delete it after ~2 weeks."
