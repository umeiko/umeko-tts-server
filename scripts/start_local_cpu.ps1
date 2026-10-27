param(
    [int]$Port = 9880,
    [string]$BaseUrl = $env:UMEKO_TTS_BASE_URL,
    [string]$Config = $env:GSV_CONFIG,
    [switch]$InstallNina
)
$ErrorActionPreference = 'Stop'
$ttsRoot = Split-Path -Parent $PSScriptRoot
$ttsPython = Join-Path $ttsRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $ttsPython)) { throw '请先按 README 创建 .venv 并安装 CPU 推理依赖。' }
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) { throw "端口 $Port 已被占用，请先停止原服务。" }
if ($InstallNina) {
    & $ttsPython (Join-Path $PSScriptRoot 'download_voices.py') nina
    if ($LASTEXITCODE -ne 0) { throw '仁菜音色安装失败。' }
}
$ttsData = if ($env:TTS_DATA_DIR) { [IO.Path]::GetFullPath($env:TTS_DATA_DIR) } else { Join-Path $ttsRoot 'data' }
[IO.Directory]::CreateDirectory($ttsData) | Out-Null
$ttsTokenFile = Join-Path $ttsData 'admin-token.txt'
if (-not $env:TTS_ADMIN_TOKEN) {
    if (Test-Path -LiteralPath $ttsTokenFile) { $env:TTS_ADMIN_TOKEN = [IO.File]::ReadAllText($ttsTokenFile).Trim() }
    else {
        $ttsBytes = New-Object byte[] 32
        $ttsRng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $ttsRng.GetBytes($ttsBytes) } finally { $ttsRng.Dispose() }
        $env:TTS_ADMIN_TOKEN = [Convert]::ToBase64String($ttsBytes)
        [IO.File]::WriteAllText($ttsTokenFile, $env:TTS_ADMIN_TOKEN, (New-Object Text.UTF8Encoding($false)))
        $ttsIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        & icacls.exe $ttsTokenFile /inheritance:r /grant:r "${ttsIdentity}:(F)" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw '无法限制管理员令牌文件的访问权限。' }
    }
    Write-Output "管理员登录令牌保存在本机文件：$ttsTokenFile"
}
if (-not $BaseUrl) { $BaseUrl = "http://127.0.0.1:$Port" }
if (-not $Config -and (Test-Path -LiteralPath (Join-Path $ttsRoot 'mambo_cpu.yaml'))) { $Config = Join-Path $ttsRoot 'mambo_cpu.yaml' }
if ($Config) { $env:GSV_CONFIG = [IO.Path]::GetFullPath($Config) }
$env:TTS_HOST = '127.0.0.1'
$env:TTS_PORT = "$Port"
$env:TTS_DATA_DIR = $ttsData
$env:UMEKO_TTS_BASE_URL = $BaseUrl.TrimEnd('/')
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$ttsLogs = Join-Path $ttsData 'logs'
[IO.Directory]::CreateDirectory($ttsLogs) | Out-Null
$ttsStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$ttsProcess = Start-Process -FilePath $ttsPython -ArgumentList @('-u', 'scripts/run_local_cpu.py') -WorkingDirectory $ttsRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $ttsLogs "$ttsStamp-out.log") -RedirectStandardError (Join-Path $ttsLogs "$ttsStamp-err.log") -PassThru
[IO.File]::WriteAllText((Join-Path $ttsRoot 'server.pid'), "$($ttsProcess.Id)")
Write-Output "正在启动 CPU 服务，进程 $($ttsProcess.Id)"
Write-Output "控制台：$($env:UMEKO_TTS_BASE_URL)/console/"
Write-Output "MCP：$($env:UMEKO_TTS_BASE_URL)/mcp"
Write-Output "日志：$ttsLogs"
