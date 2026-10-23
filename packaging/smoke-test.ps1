<#
.SYNOPSIS
    Checks a TimeShot release folder before it is published.

.DESCRIPTION
    A build machine has no camera, so recording cannot be tried. This checks what a broken
    package would get wrong instead:

    * every file the app loads at run time is in the folder;
    * lsl.dll loads (the app itself loads it only once streams are created), together with
      the Visual C++ runtime it needs;
    * TimeShot.exe starts and shows its window, which takes the self-contained .NET runtime,
      Windows Forms and the OpenCV libraries (the camera scan at start-up loads them);
    * and it exits cleanly once that window is closed.

.PARAMETER PublishDir
    The release folder made by packaging/build.ps1 (dist/TimeShot).

.EXAMPLE
    pwsh packaging/smoke-test.ps1 dist/TimeShot
#>
param([Parameter(Mandatory)][string]$PublishDir)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$PublishDir = (Resolve-Path $PublishDir).Path
$startTimeout = [TimeSpan]::FromSeconds(90) # the camera scan probes ten devices first
$exitTimeout = [TimeSpan]::FromSeconds(30)

$requiredFiles = @(
    'TimeShot.exe', 'config.yaml', 'LICENSE.txt', 'THIRD-PARTY-NOTICES.txt',
    'lsl.dll', 'msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll',
    'OpenCvSharpExtern.dll', 'opencv_videoio_ffmpeg*_64.dll'
)
$missing = $requiredFiles | Where-Object { -not (Test-Path (Join-Path $PublishDir $_)) }
if ($missing) { throw "The release lacks: $($missing -join ', ')" }
Write-Host 'All run-time files are present.'

# Loaded from its full path, lsl.dll finds its dependencies in its own folder first.
$lsl = Join-Path $PublishDir 'lsl.dll'
Add-Type -Namespace TimeShotSmokeTest -Name Lsl -MemberDefinition @"
[System.Runtime.InteropServices.DllImport(@"$lsl", CallingConvention = System.Runtime.InteropServices.CallingConvention.Cdecl)]
public static extern int lsl_library_version();
"@
$lslVersion = [TimeShotSmokeTest.Lsl]::lsl_library_version()
Write-Host "lsl.dll loads: liblsl $([math]::Floor($lslVersion / 100)).$($lslVersion % 100)."

# Started from another folder: TimeShot must find config.yaml next to itself, not in the working directory.
$app = Start-Process (Join-Path $PublishDir 'TimeShot.exe') -WorkingDirectory ([IO.Path]::GetTempPath()) -PassThru
$null = $app.Handle # keeps the process handle open, so ExitCode stays readable after it exits
try {
    $deadline = [DateTime]::UtcNow + $startTimeout
    while ($true) {
        Start-Sleep -Milliseconds 500
        $app.Refresh()
        if ($app.HasExited) { throw "TimeShot.exe exited during start-up (exit code $($app.ExitCode))." }
        if ($app.MainWindowHandle -ne [IntPtr]::Zero) { break }
        if ([DateTime]::UtcNow -gt $deadline) { throw "TimeShot.exe showed no window within $($startTimeout.TotalSeconds) s." }
    }
    Write-Host "TimeShot.exe starts and shows its window ""$($app.MainWindowTitle)""."

    $null = $app.CloseMainWindow()
    if (-not $app.WaitForExit([int]$exitTimeout.TotalMilliseconds)) {
        throw "TimeShot.exe did not exit within $($exitTimeout.TotalSeconds) s of closing its window."
    }
    if ($app.ExitCode -ne 0) { throw "TimeShot.exe exited with code $($app.ExitCode) after closing its window." }
    Write-Host 'TimeShot.exe exits cleanly when its window is closed.'
}
finally {
    if (-not $app.HasExited) { $app.Kill() }
}

Write-Host 'Smoke test passed.'
