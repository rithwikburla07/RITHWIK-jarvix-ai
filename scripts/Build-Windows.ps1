param([Parameter(Mandatory)][string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$JarvixCompiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $JarvixCompiler -PathType Leaf)) {
    throw 'The Windows .NET Framework C# compiler is unavailable.'
}
$JarvixOutput = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $JarvixOutput -Force | Out-Null
$JarvixSource = Join-Path $PSScriptRoot 'Launcher.cs'
$JarvixExe = Join-Path $JarvixOutput 'Jarvix.exe'
$JarvixMaintain = Join-Path $JarvixOutput 'jarvix-maintenance.exe'
& $JarvixCompiler /nologo /target:winexe /platform:x64 /r:System.Windows.Forms.dll "/out:$JarvixExe" $JarvixSource
if ($LASTEXITCODE -ne 0) { throw 'Jarvix launcher compilation failed.' }
& $JarvixCompiler /nologo /target:exe /platform:x64 /define:MAINTENANCE /r:System.Windows.Forms.dll "/out:$JarvixMaintain" $JarvixSource
if ($LASTEXITCODE -ne 0) { throw 'Jarvix maintenance launcher compilation failed.' }
Get-Item -LiteralPath $JarvixExe,$JarvixMaintain | Select-Object Name,Length
