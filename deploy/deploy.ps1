# deploy/deploy.ps1
# Author: Noah Rix
# Uploads all API source files to Databricks Workspace and deploys the
# Databricks App for ai-product-content-generator-api.
#
# Prerequisites:
#   - Databricks CLI installed and authenticated (profile: nrix)
#   - App already created in Databricks: databricks apps create ai-product-content-generator-api
#   - TAVILY_API_TOKEN and ANTHROPIC_API_TOKEN set as Databricks secrets
#     in the secret scope used by the app, OR injected via the Databricks
#     Apps environment variable configuration.
#
# Usage:
#   .\deploy\deploy.ps1

$sep = "=" * 70

# ── Config ────────────────────────────────────────────────────────────────────
$AppName    = "ai-product-content-generator-api"
$RemotePath = "/Workspace/ML_ai_squad/nrix/$AppName"
$LocalPath  = (Resolve-Path "$PSScriptRoot\..").Path

# File extensions to upload (Python source, config, requirements)
$UploadExtensions = @('.py', '.txt', '.yaml', '.yml', '.json')

# Directories to skip
$ExcludeDirs = @('__pycache__', '.venv', '.git', 'deploy')

# ── Step 1: Create workspace directory ───────────────────────────────────────
Write-Host $sep -ForegroundColor Cyan
Write-Host "STEP 1 - Creating workspace directory" -ForegroundColor Cyan
Write-Host $sep -ForegroundColor Cyan

databricks workspace mkdirs $RemotePath
databricks workspace mkdirs "$RemotePath/routers"
databricks workspace mkdirs "$RemotePath/json-structures"
Write-Host "  [OK] Directories ready" -ForegroundColor Green

# ── Step 2: Upload source files ───────────────────────────────────────────────
Write-Host ""
Write-Host $sep -ForegroundColor Cyan
Write-Host "STEP 2 - Uploading source files from $LocalPath" -ForegroundColor Cyan
Write-Host $sep -ForegroundColor Cyan

$allFiles = Get-ChildItem -Path $LocalPath -Recurse -File | Where-Object {
    $_.Extension -in $UploadExtensions -and
    -not ($ExcludeDirs | Where-Object { $_.FullName -match "\\$_\\" -or $_.FullName -match "\\$_$" })
}

foreach ($f in $allFiles) {
    $rel        = $f.FullName.Substring($LocalPath.Length + 1).Replace('\', '/')
    $remoteDest = "$RemotePath/$rel"

    # Ensure parent directory exists for nested files
    $parts     = $remoteDest -split '/'
    $remoteDir = ($parts[0..($parts.Count - 2)]) -join '/'
    databricks workspace mkdirs $remoteDir 2>$null

    databricks workspace import $remoteDest --file $f.FullName --format RAW --overwrite
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK]   $rel" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] $rel" -ForegroundColor Red
        exit 1
    }
}

Write-Host "  [OK] All files uploaded" -ForegroundColor Green

# ── Step 3: Deploy Databricks App ─────────────────────────────────────────────
Write-Host ""
Write-Host $sep -ForegroundColor Cyan
Write-Host "STEP 3 - Deploying Databricks App: $AppName" -ForegroundColor Cyan
Write-Host $sep -ForegroundColor Cyan

databricks apps deploy $AppName --source-code-path $RemotePath
if ($LASTEXITCODE -ne 0) {
    Write-Error "Deployment failed"
    exit 1
}

Write-Host ""
Write-Host $sep -ForegroundColor Green
Write-Host "DEPLOY COMPLETE" -ForegroundColor Green
Write-Host $sep -ForegroundColor Green
Write-Host ""

databricks apps get $AppName
