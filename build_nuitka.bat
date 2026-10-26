@echo off
chcp 65001 >nul
cd /d "%~dp0"

set MISSING=0
for %%F in (elevator_review_app.py elevator_shaft_review_module.py elevator_security.py designhub_ui_theme.py ai_client.py api_settings_module.py elevator_standards.py elevator_shaft_table.json) do (
  if not exist "%%F" (echo [누락] %%F & set MISSING=1)
)
if "%MISSING%"=="1" (echo 위 파일을 이 폴더에 넣고 다시 실행하세요. & pause & exit /b 1)

python -m pip install --upgrade nuitka ordered-set zstandard ezdxf matplotlib
if errorlevel 1 (echo pip 설치 실패 & pause & exit /b 1)

python -m nuitka --onefile --assume-yes-for-downloads ^
  --windows-console-mode=disable ^
  --enable-plugin=tk-inter ^
  --python-flag=no_docstrings --python-flag=-O ^
  --include-module=elevator_shaft_review_module ^
  --include-module=elevator_security ^
  --include-module=designhub_ui_theme ^
  --include-module=ai_client ^
  --include-module=api_settings_module ^
  --include-module=elevator_standards ^
  --include-data-files=elevator_shaft_table.json=elevator_shaft_table.json ^
  --output-filename=ElevatorReview.exe ^
  --output-dir=dist ^
  elevator_review_app.py

if errorlevel 1 (echo 빌드 실패 - 위 메시지를 확인하세요 & pause & exit /b 1)
echo.
echo 완료: dist\ElevatorReview.exe
echo 배포 시 exe 옆에 .admin_settings.json 을 같이 두세요(관리 창에서 사번/기간 저장 후 생성됨).
pause
