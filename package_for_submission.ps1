# Packages the project into a clean zip ready for submission.
# Excludes secrets (.env), virtual environment, build artifacts, and generated reports.

$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$zipName = "seo-geo-audit_submission_$stamp.zip"
$staging = Join-Path $env:TEMP "seo-geo-audit-staging-$stamp"

Write-Host "Building submission package..." -ForegroundColor Cyan
New-Item -ItemType Directory -Path $staging -Force | Out-Null

# Copy everything except the excluded paths
$exclude = @(".venv", ".env", "__pycache__", "reports", ".git", "*.pyc", "package_for_submission.ps1")
robocopy $root $staging /E /XD ".venv" "__pycache__" "reports" ".git" /XF ".env" "*.pyc" "package_for_submission.ps1" /NFL /NDL /NJH /NJS | Out-Null

# Move the latest generated .pptx (if any) into a "sample_output" folder alongside the code
$reportsDir = Join-Path $root "reports"
if (Test-Path $reportsDir) {
    $latest = Get-ChildItem $reportsDir -Filter "*.pptx" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($latest) {
        $sampleDir = Join-Path $staging "sample_output"
        New-Item -ItemType Directory -Path $sampleDir -Force | Out-Null
        Copy-Item $latest.FullName $sampleDir
        Write-Host "  Included sample output: $($latest.Name)" -ForegroundColor Green
    }
}

# Create the zip on the user's Desktop
$desktopPath = [Environment]::GetFolderPath("Desktop")
$zipPath = Join-Path $desktopPath $zipName

if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path "$staging\*" -DestinationPath $zipPath -CompressionLevel Optimal

# Clean up staging
Remove-Item $staging -Recurse -Force

Write-Host ""
Write-Host "Submission package ready!" -ForegroundColor Green
Write-Host "  Location: $zipPath"
$size = (Get-Item $zipPath).Length / 1KB
Write-Host ("  Size:     {0:N0} KB" -f $size)
Write-Host ""
Write-Host "Excluded (NOT in the zip):" -ForegroundColor Yellow
Write-Host "  - .env  (your API keys stay on this machine)"
Write-Host "  - .venv  (virtual environment - recipient creates their own)"
Write-Host "  - reports/  (except the latest .pptx, in sample_output/)"
Write-Host "  - __pycache__/, .git/, *.pyc"
Write-Host ""
Write-Host "To submit:"
Write-Host "  1. Email or upload the zip above"
Write-Host "  2. Also send the latest .pptx separately if you want CEO to see it without unzipping"
