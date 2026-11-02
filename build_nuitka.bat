@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo [1/4] 필요한 파일 확인
set MISSING=0
for %%F in (elevator_review_app.py elevator_shaft_review_module.py elevator_security.py designhub_ui_theme.py) do (
  if not exist "%%F" (echo   [누락] %%F & set MISSING=1)
)
if "%MISSING%"=="1" (echo 위 파일을 이 폴더에 넣고 다시 실행하세요. & pause & exit /b 1)

echo [2/4] 패키지 설치
python -m pip install --upgrade nuitka ordered-set zstandard ezdxf openpyxl
if errorlevel 1 (echo pip 설치 실패 & pause & exit /b 1)

echo [3/4] 빌드 (처음엔 C 컴파일러 다운로드를 물어보면 y 입력, 10~30분 걸릴 수 있습니다)
python -m nuitka --onefile --assume-yes-for-downloads ^
  --windows-console-mode=disable ^
  --enable-plugin=tk-inter ^
  --python-flag=no_docstrings --python-flag=-O ^
  --include-package=ezdxf ^
  --include-package=openpyxl ^
  --include-module=elevator_shaft_review_module ^
  --include-module=elevator_security ^
  --include-module=designhub_ui_theme ^
  --output-filename=ElevatorReview.exe ^
  --output-dir=dist ^
  elevator_review_app.py

if errorlevel 1 (echo 빌드 실패 - 위 메시지를 확인하세요 & pause & exit /b 1)

echo [4/4] 완료
echo   결과: dist\ElevatorReview.exe
echo   배포 시 exe 옆에 .admin_settings.json 을 같이 두세요
echo   (exe 실행 후 Ctrl+Shift+Q+P 관리 창에서 사번/시작일/기간 저장 시 생성됨)
pause
