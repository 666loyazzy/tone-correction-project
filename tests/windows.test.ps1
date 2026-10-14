# Run after Install. Uses the local entry point without creating user accounts.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$entry = Join-Path $root 'scripts\windows.ps1'
. $entry Start
if ((Get-ToneHealth).queue_mode -ne 'local') { throw 'Local queue check failed.' }
if (-not (Get-ToneHealth 'api/auth/status').setup_token_required) { throw '.env was not loaded.' }
. $entry Backup
Add-Type -AssemblyName System.IO.Compression.FileSystem
$latest = Get-ChildItem -LiteralPath (Join-Path $root 'backups') -Filter '*.zip' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$archive = [IO.Compression.ZipFile]::OpenRead($latest.FullName)
try {
    if (-not ($archive.Entries.FullName -match 'tone_correction.sqlite3$')) { throw 'Database missing from backup.' }
} finally { $archive.Dispose() }
. $entry Stop
if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) { throw 'Server still listening after Stop.' }
. $entry Start
if ((Get-ToneHealth).status -ne 'ok') { throw 'Restart check failed.' }
Write-Output 'Windows checks passed: startup, .env loading, SQLite backup, stop and restart.'
