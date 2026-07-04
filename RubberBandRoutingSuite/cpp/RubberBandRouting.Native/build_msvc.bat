@echo off
setlocal
where cl >nul 2>nul
if errorlevel 1 (
  echo MSVC cl.exe was not found. Run this script from a Developer Command Prompt for Visual Studio.
  exit /b 1
)
cl /std:c++17 /EHsc /O2 /LD /Fe:RubberBandRouting.Native.dll rubberband_native.cpp
endlocal
