param([switch]$CheckInternet)
$ErrorActionPreference = 'Stop'
$JarvixOS = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
"Windows $($JarvixOS.DisplayVersion) build $($JarvixOS.CurrentBuildNumber).$($JarvixOS.UBR)"
"64-bit Windows: $([Environment]::Is64BitOperatingSystem)"
$JarvixIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$JarvixPrincipal = [Security.Principal.WindowsPrincipal]::new($JarvixIdentity)
"Elevated: $($JarvixPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))"
$JarvixPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $JarvixPython -PathType Leaf)) {
    $JarvixPython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
}
if (Test-Path -LiteralPath $JarvixPython -PathType Leaf) {
    & $JarvixPython --version
    & $JarvixPython -B -c "from importlib.util import find_spec; print('Packages:', {n: bool(find_spec(n)) for n in ('PySide6','psutil','pycaw','speech_recognition','vosk','pyttsx3','google_auth_oauthlib','keyring','cryptography')})"
} else {
    'Python 3.12 interpreter missing. The Microsoft Store alias is ignored.'
}
$JarvixDrive = Get-PSDrive -Name ($env:SystemDrive.TrimEnd(':'))
"Free system drive space: $([math]::Round($JarvixDrive.Free / 1GB, 1)) GB"
"Java visible: $([bool](Get-Command java -ErrorAction SilentlyContinue) -or [bool]$env:JAVA_HOME)"
"Gradle visible: $([bool](Get-Command gradle -ErrorAction SilentlyContinue))"
"Android SDK configured: $([bool]$env:ANDROID_HOME -or [bool]$env:ANDROID_SDK_ROOT)"
if ($CheckInternet) {
    try {
        $JarvixResponse = Invoke-WebRequest -Uri 'https://www.python.org' -Method Head -TimeoutSec 5
        "Python package site check: HTTP $($JarvixResponse.StatusCode)"
    } catch {
        'Public HTTPS check failed.'
    }
}
