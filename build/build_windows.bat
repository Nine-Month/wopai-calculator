@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

echo === Pacific Front Helper Windows build (slim) ===
if not exist .venv (
  py -3 -m venv .venv
  if errorlevel 1 python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install -U pip
pip install -r requirements.txt
pip install pyinstaller

if not exist "%CD%\bridge\inject.js" (
  echo ERROR: bridge\inject.js missing
  exit /b 1
)

REM Use slim spec: no --collect-all PySide6
pyinstaller --noconfirm "%CD%\build\PacificFrontHelper.spec" ^
  --distpath "%CD%\dist" --workpath "%CD%\build\work"

if errorlevel 1 (
  echo Build failed
  exit /b 1
)

if exist dist\PacificFrontHelper\ (
  if not exist dist\PacificFrontHelper\bridge mkdir dist\PacificFrontHelper\bridge
  copy /Y bridge\inject.js dist\PacificFrontHelper\bridge\inject.js >nul
  if not exist dist\PacificFrontHelper\data mkdir dist\PacificFrontHelper\data
  if exist data\locale_zh_sg.json copy /Y data\locale_zh_sg.json dist\PacificFrontHelper\data\locale_zh_sg.json >nul
  if exist data\mod_icons (
    if not exist dist\PacificFrontHelper\data\mod_icons mkdir dist\PacificFrontHelper\data\mod_icons
    xcopy /Y /E /I data\mod_icons dist\PacificFrontHelper\data\mod_icons >nul
  )
  if exist README.md copy /Y README.md dist\PacificFrontHelper\README.md >nul
  if exist README_zh.md copy /Y README_zh.md dist\PacificFrontHelper\README_zh.md >nul

  REM Extra prune of any leftover heavy Qt bits
  powershell -NoProfile -Command ^
    "$root='dist\PacificFrontHelper'; $patterns=@('*WebEngine*','*webengine*','*Qt6Quick*','*Qt6Qml*','*Qt6Pdf*','*Qt6Designer*','*Qt63D*','*Qt6Charts*','*Qt6Graphs*','*Qt6Multimedia*','*avcodec*','*avformat*','*opengl32sw*','*.pdb','qmlls.exe','assistant.exe','designer.exe','linguist.exe'); Get-ChildItem $root -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $n=$_.Name; foreach($p in $patterns){ if($n -like $p){ return $true } }; $false } | ForEach-Object { Remove-Item -Force $_.FullName -ErrorAction SilentlyContinue }; if(Test-Path \"$root\_internal\PySide6\resources\"){ Remove-Item -Recurse -Force \"$root\_internal\PySide6\resources\" -ErrorAction SilentlyContinue }; if(Test-Path \"$root\_internal\PySide6\translations\"){ Get-ChildItem \"$root\_internal\PySide6\translations\" -Filter '*webengine*' -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue }"

  echo.
  echo OK: dist\PacificFrontHelper\PacificFrontHelper.exe
) else (
  echo WARNING: unexpected dist layout
)

endlocal
