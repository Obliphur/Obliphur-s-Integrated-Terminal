@echo off
rem OIT closed test (sandbox): all data goes to .\closed_test, device sync is off. Dev checkout only (see closedtest.py).
cd /d "%~dp0"
where pythonw >nul 2>nul && (start "" pythonw "%~dp0main.py" --closed-test & exit /b 0)
where pyw >nul 2>nul && (start "" pyw -3 "%~dp0main.py" --closed-test & exit /b 0)
python "%~dp0main.py" --closed-test
