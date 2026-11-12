param([string]$Gradle = 'gradle')
$ErrorActionPreference = 'Stop'
$JarvixJava = Get-Command java -ErrorAction SilentlyContinue
if (-not $JarvixJava -and -not $env:JAVA_HOME) { throw 'JDK 17 is required.' }
$JarvixSdk = if ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { $env:ANDROID_SDK_ROOT }
if (-not $JarvixSdk -or -not (Test-Path -LiteralPath (Join-Path $JarvixSdk 'platforms/android-35'))) {
    throw 'Android SDK Platform 35 is required. Set ANDROID_HOME to the approved SDK location.'
}
$JarvixGradle = Get-Command $Gradle -ErrorAction SilentlyContinue
if (-not $JarvixGradle) { throw 'Gradle 8.9 is required.' }
$JarvixSource = Join-Path (Split-Path -Parent $PSScriptRoot) 'android'
& $JarvixGradle.Source --no-daemon -p $JarvixSource :app:testDebugUnitTest :app:assembleDebug
if ($LASTEXITCODE -ne 0) { throw 'Android tests or APK build failed.' }
Get-Item -LiteralPath (Join-Path $JarvixSource 'app/build/outputs/apk/debug/app-debug.apk')
