@echo off
chcp 65001 >nul
python -m pip install nuitka ordered-set zstandard
python -m nuitka --onefile --windows-console-mode=disable ^
  --enable-plugin=tk-inter --python-flag=no_docstrings --python-flag=-O ^
  --include-module=elevator_shaft_review_module --include-module=elevator_security ^
  --include-module=designhub_ui_theme --include-module=ai_client ^
  --include-module=api_settings_module --include-module=elevator_standards ^
  --include-data-files=elevator_shaft_table.json=elevator_shaft_table.json ^
  --output-filename=ElevatorReview.exe elevator_review_app.py
echo 완료: ElevatorReview.exe 와 같은 폴더에 .admin_settings.json 이 생성됩니다(첫 Ctrl+Shift+G 시 관리자 비밀번호 설정).
pause
