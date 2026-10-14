param(
    [ValidateSet('Install', 'Start', 'Stop', 'Status', 'Backup')]
    [string]$Action = 'Start',
    [string]$PythonPath = ''
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$runtime = Join-Path $root 'runtime'
$logs = Join-Path $root 'logs'
$stateFile = Join-Path $runtime 'server.json'
$url = 'http://localhost:8000'
New-Item -ItemType Directory -Path $runtime,$logs -Force | Out-Null
$env:PYTHONUTF8 = '1'
$env:PATH = (Join-Path $runtime 'bin') + ';' + $env:PATH
$env:PIP_CACHE_DIR = Join-Path $runtime 'pip-cache'
$env:TEMP = Join-Path $runtime 'tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null

function Get-ToneProcess {
    if (Test-Path -LiteralPath $stateFile) {
        $state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
        $process = Get-Process -Id $state.pid -ErrorAction SilentlyContinue
        if ($process -and $process.StartTime.ToUniversalTime().Ticks.ToString() -eq $state.started) {
            return $process
        }
    }
}
function Get-ToneHealth([string]$Endpoint = 'api/health') {
    $request = [Net.WebRequest]::Create('http://127.0.0.1:8000/' + $Endpoint)
    $request.Proxy = $null
    $request.Timeout = 1000
    $request.ReadWriteTimeout = 1000
    $response = $request.GetResponse()
    try {
        $reader = [IO.StreamReader]::new($response.GetResponseStream())
        try { return ($reader.ReadToEnd() | ConvertFrom-Json) }
        finally { $reader.Dispose() }
    } finally { $response.Dispose() }
}
function Start-Tone {
    if (Get-ToneProcess) { Write-Output "Already running: $url"; return }
    if (-not (Test-Path -LiteralPath $python)) { throw 'Run Install first.' }
    if (-not (Test-Path -LiteralPath (Join-Path $root '.env'))) { throw 'Run Install to create .env first.' }
    if (-not (Test-Path -LiteralPath (Join-Path $runtime 'bin\ffmpeg.exe'))) { throw 'Run Install to install FFmpeg first.' }
    # This entry point always stores local SQLite and recordings beside the source.
    $env:DATA_DIR = Join-Path $root 'data'
    $env:DATABASE_URL = 'sqlite:///' + (Join-Path $env:DATA_DIR 'tone_correction.sqlite3').Replace('\','/')
    $env:QUEUE_MODE = 'local'
    $arguments = '-m uvicorn backend.main:app --app-dir "' + $root + '" --env-file "' + (Join-Path $root '.env') + '" --host 127.0.0.1 --port 8000'
    $process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logs 'server.out.log') -RedirectStandardError (Join-Path $logs 'server.err.log')
    @{pid=$process.Id; started=$process.StartTime.ToUniversalTime().Ticks.ToString()} | ConvertTo-Json | Set-Content -LiteralPath $stateFile
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if (-not (Get-ToneProcess)) { throw "Server exited. Check $logs\server.err.log" }
        try {
            $health = Get-ToneHealth
            if ($health.status -eq 'ok') { Write-Output "Running: $url | data: $root\data | model API configured: $($health.tutor_api_configured)"; return }
        } catch { }
        Start-Sleep -Milliseconds 250
    }
    throw "Health check timed out. Check $logs\server.err.log"
}
function Stop-Tone {
    $process = Get-ToneProcess
    if ($process) {
        # Windows venv launchers create a child Python process; stop the whole tree.
        & taskkill.exe /PID $process.Id /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Could not stop the server process tree.' }
        $process.WaitForExit()
        Write-Output 'Server stopped.'
    }
}

switch ($Action) {
    'Install' {
        if (Get-ToneProcess) { throw 'Stop the server before installing dependencies.' }
        if (-not (Test-Path -LiteralPath $python)) {
            if (-not $PythonPath) {
                $PythonPath = Join-Path $runtime 'python\python.exe'
                if (-not (Test-Path -LiteralPath $PythonPath)) {
                    $PythonPath = (Get-Command python -ErrorAction Stop).Source
                }
            }
            & $PythonPath -c 'import sys; assert sys.version_info[:2] == (3,12), ''Python 3.12 required'''
            if ($LASTEXITCODE -ne 0) { throw 'Pass -PythonPath with a Python 3.12 executable.' }
            & $PythonPath -m venv (Join-Path $root '.venv')
            if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
        }
        & $python -m pip install -r (Join-Path $root 'requirements.txt') imageio-ffmpeg==0.6.0
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
        $ffmpeg = & $python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())'
        if ($LASTEXITCODE -ne 0) { throw 'FFmpeg executable not found.' }
        New-Item -ItemType Directory -Path (Join-Path $runtime 'bin') -Force | Out-Null
        Copy-Item -LiteralPath $ffmpeg -Destination (Join-Path $runtime 'bin\ffmpeg.exe') -Force
        if (-not (Test-Path -LiteralPath (Join-Path $root '.env'))) {
            & $python (Join-Path $PSScriptRoot 'configure.py')
            if ($LASTEXITCODE -ne 0) { throw 'Configuration creation failed.' }
        }
        Write-Output 'Installed. Run windows.ps1 Start.'
    }
    'Start' { Start-Tone }
    'Stop' { Stop-Tone }
    'Status' {
        if (Get-ToneProcess) { Get-ToneHealth | ConvertTo-Json }
        else { Write-Output 'Server stopped.' }
    }
    'Backup' {
        $wasRunning = [bool](Get-ToneProcess)
        Stop-Tone
        try {
            $data = Join-Path $root 'data'
            if (-not (Test-Path -LiteralPath $data)) { throw 'No local data to back up.' }
            $backupDir = Join-Path $root 'backups'
            New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
            $backup = Join-Path $backupDir ('tone-data-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.zip')
            Compress-Archive -LiteralPath $data -DestinationPath $backup
            Write-Output "Private backup: $backup"
        } finally {
            if ($wasRunning) { Start-Tone }
        }
    }
}
