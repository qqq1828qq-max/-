@echo off
setlocal
cd /d "%~dp0"

echo [1/4] Checking required files
set MISSING=0
for %%F in (elevator_review_app.py elevator_shaft_review_module.py elevator_security.py designhub_ui_theme.py make_embedded.py) do (
  if not exist "%%F" (
    echo   MISSING: %%F
    set MISSING=1
  )
)
if "%MISSING%"=="1" (
  echo Put the missing files in this folder and run again.
  pause
  exit /b 1
)

echo [2/4] Installing packages
python -m pip install --upgrade nuitka ordered-set zstandard ezdxf openpyxl
if errorlevel 1 (
  echo pip install failed
  pause
  exit /b 1
)

echo [2.5/4] Embedding admin settings from admin_config.txt
python make_embedded.py
if errorlevel 1 (
  echo Fix admin_config.txt and run again
  pause
  exit /b 1
)
set EMB=
if exist elevator_embedded.py set EMB=--include-module=elevator_embedded

echo [3/4] Building - folder mode. If asked to download a C compiler, answer y. It can take 10-30 minutes.
if exist dist\ElevatorReview rmdir /s /q dist\ElevatorReview
if exist dist\elevator_review_app.dist rmdir /s /q dist\elevator_review_app.dist

python -m nuitka --standalone --assume-yes-for-downloads ^
  --windows-console-mode=disable ^
  --enable-plugin=tk-inter ^
  --python-flag=no_docstrings --python-flag=-O ^
  --include-package=ezdxf ^
  --include-package=openpyxl ^
  --nofollow-import-to=ezdxf.addons ^
  --nofollow-import-to=openpyxl.utils.dataframe ^
  --nofollow-import-to=pandas ^
  --nofollow-import-to=scipy ^
  --nofollow-import-to=matplotlib ^
  --nofollow-import-to=PIL ^
  --nofollow-import-to=pymupdf ^
  --nofollow-import-to=fitz ^
  --nofollow-import-to=sqlalchemy ^
  --nofollow-import-to=xlsxwriter ^
  --nofollow-import-to=jinja2 ^
  --nofollow-import-to=PySide6 ^
  --nofollow-import-to=PyQt5 ^
  --nofollow-import-to=PyQt6 ^
  --lto=no ^
  --include-module=elevator_shaft_review_module ^
  --include-module=elevator_security ^
  --include-module=designhub_ui_theme ^
  %EMB% ^
  --output-filename=ElevatorReview.exe ^
  --output-dir=dist ^
  elevator_review_app.py

if errorlevel 1 (
  echo Build failed - check the messages above
  pause
  exit /b 1
)

echo [4/4] Finishing
ren dist\elevator_review_app.dist ElevatorReview

echo.
echo DONE: dist\ElevatorReview folder - run ElevatorReview.exe inside it.
echo Distribute the whole folder.
echo Admin settings are inside the exe from admin_config.txt. Do not distribute admin_config.txt or elevator_embedded.py.
pause
