param([string]$Executable = 'dist/BellPlan.exe')
$ErrorActionPreference = 'Stop'
$sourceExe = (Resolve-Path -LiteralPath $Executable).Path
$testDirectory = Join-Path ([IO.Path]::GetTempPath()) ('BellPlan 便携测试 ' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testDirectory | Out-Null
try {
    $testExe = Join-Path $testDirectory 'BellPlan.exe'
    $reportPath = Join-Path $testDirectory 'self-check.json'
    Copy-Item -LiteralPath $sourceExe -Destination $testExe
    $process = Start-Process -FilePath $testExe `
        -ArgumentList "--self-check-report `"$reportPath`"" `
        -WorkingDirectory $testDirectory -PassThru
    if (!$process.WaitForExit(120000)) {
        $process.Kill()
        throw '便携包检查超时'
    }
    $process.Refresh()
    if ($process.ExitCode -ne 0 -or !(Test-Path -LiteralPath $reportPath)) {
        throw "便携包无法独立启动，退出码 $($process.ExitCode)"
    }
    $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
    $manifest = Get-Content -LiteralPath 'src/bellplan/assets/manifest.json' -Raw | ConvertFrom-Json
    if (!$report.ok -or !$report.frozen -or $report.platform -ne 'Windows') {
        throw '未通过 Windows 独立运行检查'
    }
    if ($report.audio_count -ne $manifest.media_count) {
        throw '便携包音频数量与清单不一致'
    }
    Write-Host "单文件启动及资源校验通过：$($report.audio_count) 个音频，$($report.audio_bytes) 字节。"
} finally {
    Remove-Item -LiteralPath $testDirectory -Recurse -Force -ErrorAction SilentlyContinue
}
