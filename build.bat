@echo off
rem Rebuilds dist\Noita Companion.exe from the source files in this folder.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  python -m venv .venv || goto :fail
  .venv\Scripts\python.exe -m pip install --upgrade pip numpy pillow pyinstaller pywebview || goto :fail
)
rem refresh the wiki data and icons that ship inside the app
.venv\Scripts\python.exe wiki_data.py || goto :fail
rem add newly downloaded icons to the shipped set; the cache only holds icons seed_icons lacked, so never replace it
if exist cache\icons xcopy /e /i /q /y cache\icons seed_icons >nul
.venv\Scripts\pyinstaller.exe --noconfirm --clean --onefile --windowed --name "Noita Companion" ^
  --icon "%CD%\app.ico" ^
  --add-data "%CD%\map_template.html;." --add-data "%CD%\wiki_cache.json;." --add-data "%CD%\seed_icons;seed_icons" ^
  --hidden-import webview.platforms.edgechromium --exclude-module tkinter ^
  --distpath dist --workpath build --specpath build companion.py || goto :fail
echo.
echo Built dist\Noita Companion.exe
pause
exit /b 0
:fail
echo Build failed.
pause
exit /b 1
