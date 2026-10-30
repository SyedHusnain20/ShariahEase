<#
.SYNOPSIS
  Backs up the ShariahEase Postgres database to a compressed file on your own computer.

.DESCRIPTION
  Needs pg_dump (installed with PostgreSQL client tools: https://www.postgresql.org/download/windows/).
  Reads DATABASE_URL from the environment, or from the .env file next to this repo.
  The backup contains users' data. Keep it private: never commit it, never upload it to a public place.
  Neon Free only keeps a short history window, so this is your real backup.
  Run it weekly, and before every migration or deploy that changes the database.

.EXAMPLE
  .\scripts\backup_db.ps1
  .\scripts\backup_db.ps1 -OutDir D:\private\shariahease-backups -Keep 12
#>
param(
    [string]$OutDir = (Join-Path $HOME "shariahease-backups"),
    [int]$Keep = 8
)

$ErrorActionPreference = "Stop"

# 1. Find DATABASE_URL (environment first, then .env)
$url = $env:DATABASE_URL
if (-not $url) {
    $envFile = Join-Path (Split-Path $PSScriptRoot -Parent) ".env"
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile) {
            if ($line -match '^\s*DATABASE_URL\s*=\s*(.+?)\s*$') { $url = $Matches[1].Trim('"').Trim("'") }
        }
    }
}
if (-not $url) { throw "DATABASE_URL is not set (environment or .env). Nothing to back up." }
if ($url -notmatch '^postgres(ql)?://') { throw "DATABASE_URL does not look like a Postgres URL." }

# 2. Find pg_dump
$pgDump = Get-Command pg_dump -ErrorAction SilentlyContinue
if (-not $pgDump) { throw "pg_dump not found. Install the PostgreSQL client tools and reopen PowerShell." }

# 3. Dump
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$file  = Join-Path $OutDir "shariahease-$stamp.dump"
& $pgDump.Source --format=custom --no-owner --no-privileges --file=$file --dbname=$url
if ($LASTEXITCODE -ne 0) {
    if (Test-Path $file) { Remove-Item $file -Force }
    throw "pg_dump failed (exit code $LASTEXITCODE). No backup was kept."
}
if ((Get-Item $file).Length -lt 1024) { throw "Backup file is suspiciously small; check it before relying on it." }

# 4. Keep only the newest $Keep backups
Get-ChildItem $OutDir -Filter "shariahease-*.dump" | Sort-Object LastWriteTime -Descending |
    Select-Object -Skip $Keep | Remove-Item -Force

Write-Host "Backup saved: $file ($([math]::Round((Get-Item $file).Length / 1KB)) KB)"
Write-Host "To restore into an EMPTY database:  pg_restore --no-owner --dbname=<target url> `"$file`""
