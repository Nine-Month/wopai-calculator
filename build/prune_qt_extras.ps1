$root = Join-Path $PSScriptRoot '..\dist\PacificFrontHelper' | Resolve-Path
$patterns = @('*WebEngine*','*webengine*','*Qt6Quick*','*Qt6Qml*','*Qt6Pdf*','*Qt6Designer*','*Qt63D*','*Qt6Charts*','*Qt6Graphs*','*Qt6Multimedia*','*avcodec*','*avformat*','*avutil*','*swresample*','*swscale*','*opengl32sw*','*.pdb','qmlls.exe','assistant.exe','designer.exe','linguist.exe','*Qt6ShaderTools*','*Qt6Quick3D*','*Qt6Labs*')
Get-ChildItem $root -Recurse -File -EA SilentlyContinue | Where-Object {
  $n = $_.Name
  foreach ($p in $patterns) { if ($n -like $p) { return $true } }
  $false
} | ForEach-Object { Remove-Item -Force $_.FullName -EA SilentlyContinue }
$res = Join-Path $root '_internal\PySide6\resources'
if (Test-Path $res) { Remove-Item -Recurse -Force $res -EA SilentlyContinue }
Write-Host 'prune done'
