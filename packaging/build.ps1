<#
.SYNOPSIS
    Builds the TimeShot release for Windows x64.

.DESCRIPTION
    Publishes TimeShot as one self-contained TimeShot.exe, so no .NET installation is needed,
    with the native libraries (OpenCV, LSL, the Visual C++ runtime), config.yaml and the
    license files next to it in dist/TimeShot, and zips that folder as
    dist/TimeShot-<version>-windows-x64.zip. The version is <Version> in TimeShot/TimeShot.csproj.

    Needs the .NET 8 SDK (or newer) and Visual Studio or its Build Tools with the
    "Desktop development with C++" workload, for the Visual C++ runtime files.

.PARAMETER ExpectVersion
    Fail unless the project version matches this, e.g. the release tag "v1.1.0".

.OUTPUTS
    In GitHub Actions, writes the step outputs version, publish_dir and archive.

.EXAMPLE
    pwsh packaging/build.ps1
#>
param([string]$ExpectVersion)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot
$project = Join-Path $root 'TimeShot/TimeShot.csproj'
$dist = Join-Path $root 'dist'
$publishDir = Join-Path $dist 'TimeShot'

# lsl.dll needs the Visual C++ runtime (MSVCP140.dll and VCRUNTIME140.dll, which in turn needs
# VCRUNTIME140_1.dll). A fresh Windows lacks it and the self-contained .NET runtime does not bring
# it, so the release ships it next to TimeShot.exe ("app-local", as Microsoft allows), taken from
# Visual Studio's redistributable folder. The OpenCV libraries need nothing beyond Windows itself.
$vcRuntimeFiles = 'msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll'

function Invoke-Native([string]$What, [scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE) { throw "$What failed (exit code $LASTEXITCODE)." }
}

# The x64 Microsoft.VC*.CRT folder of the newest Visual C++ redistributable files installed.
function Find-VCRuntimeFolder {
    if ($env:VCToolsRedistDir) { # set in a Visual Studio developer prompt
        $folder = Get-ChildItem (Join-Path $env:VCToolsRedistDir 'x64') -Directory -Filter 'Microsoft.VC*.CRT' -ErrorAction SilentlyContinue |
            Select-Object -Last 1
        if ($folder) { return $folder.FullName }
    }
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
    if (Test-Path $vswhere) {
        # ...\VC\Redist\MSVC\<toolset version>\x64\Microsoft.VC143.CRT\msvcp140.dll: newest toolset wins.
        $found = & $vswhere -latest -products * -find 'VC\Redist\MSVC\*\x64\Microsoft.VC*.CRT\msvcp140.dll' |
            Sort-Object { (Split-Path -Leaf (Split-Path (Split-Path (Split-Path $_)))) -as [version] } |
            Select-Object -Last 1
        if ($found) { return Split-Path $found }
    }
    throw 'The Visual C++ redistributable files were not found. Install Visual Studio or its Build Tools ' +
        'with the "Desktop development with C++" workload, or run this from a Visual Studio developer prompt.'
}

# The last line: a first run of the SDK may print a welcome text before it.
$version = (Invoke-Native 'Reading the project version' { dotnet msbuild $project -nologo -getProperty:Version } |
    Where-Object { $_.Trim() } | Select-Object -Last 1).Trim()
if ($ExpectVersion -and $ExpectVersion.TrimStart('v') -ne $version) {
    throw "Tag $ExpectVersion does not match the project version $version (<Version> in TimeShot/TimeShot.csproj)."
}
$archive = Join-Path $dist "TimeShot-$version-windows-x64.zip"
Write-Host "Building TimeShot $version for windows-x64"

if (Test-Path $dist) { Remove-Item $dist -Recurse -Force }
$publishArgs = @(
    'publish', $project, '--configuration', 'Release', '--runtime', 'win-x64', '--output', $publishDir,
    '--self-contained', 'true',          # runs without a .NET installation
    '-p:PublishSingleFile=true',         # one TimeShot.exe; native libraries stay next to it instead of
                                         # being unpacked to a temporary folder on every start
    '-p:DebugType=embedded',             # symbols inside the exe: stack traces with line numbers, no .pdb
    '-p:PublishDocumentationFile=false'  # the API documentation (TimeShot.xml) is no use to users
)
Invoke-Native 'dotnet publish' { dotnet @publishArgs }

$vcRuntimeFolder = Find-VCRuntimeFolder
Write-Host "Visual C++ runtime from $vcRuntimeFolder"
foreach ($file in $vcRuntimeFiles) {
    Copy-Item (Join-Path $vcRuntimeFolder $file) $publishDir
}
Copy-Item (Join-Path $root 'LICENSE') (Join-Path $publishDir 'LICENSE.txt')
Copy-Item (Join-Path $PSScriptRoot 'THIRD-PARTY-NOTICES.txt') $publishDir

# The archive holds the TimeShot folder itself, so unzipping never scatters files.
Compress-Archive -Path $publishDir -DestinationPath $archive
Get-ChildItem $publishDir | Format-Table Name, Length -AutoSize | Out-String | Write-Host
Write-Host "Built $archive"

if ($env:GITHUB_OUTPUT) {
    "version=$version", "publish_dir=$publishDir", "archive=$archive" | Add-Content $env:GITHUB_OUTPUT
}
