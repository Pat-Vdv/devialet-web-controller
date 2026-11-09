@echo off
rem Builds the Devialet spectrum agent with the C# compiler shipped with Windows
rem (.NET Framework 4.x). No SDK, no download, no installation.
rem Output: %LOCALAPPDATA%\Devialet\DevialetSpectrumAgent.exe (or the path given as %1)
setlocal
set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%CSC%" set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
if not exist "%CSC%" (
  echo csc.exe introuvable : .NET Framework 4 requis.
  exit /b 1
)
set "OUT=%~1"
if "%OUT%"=="" set "OUT=%LOCALAPPDATA%\Devialet\DevialetSpectrumAgent.exe"
for %%D in ("%OUT%") do if not exist "%%~dpD" mkdir "%%~dpD"
"%CSC%" /nologo /optimize+ /warnaserror+ /target:exe /platform:anycpu /out:"%OUT%" ^
  "%~dp0SpectrumCore.cs" "%~dp0Wasapi.cs" "%~dp0Agent.cs" "%~dp0SelfTest.cs"
if errorlevel 1 exit /b 1
echo Agent compile : %OUT%
