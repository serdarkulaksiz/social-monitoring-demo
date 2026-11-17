@echo off
rem Demo: ilk seferde kurar, monitor'u sentetik veriyle baslatir ve dashboard'u acar.
rem Ayrintilar: docs\DEMO.md
setlocal
chcp 65001 >nul
cd /d "%~dp0"

rem Python 3.13 veya daha yenisi
set "PY="
py -3.13 --version >nul 2>&1 && set "PY=py -3.13"
if not defined PY py -3 -c "import sys; sys.exit(sys.version_info < (3, 13))" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(sys.version_info < (3, 13))" >nul 2>&1 && set "PY=python"
if not defined PY (
  echo Python 3.13 bulunamadi. https://www.python.org/downloads/ adresinden kurun.
  echo Kurulumda "Add python.exe to PATH" kutusunu isaretleyin.
  goto :end
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/2] social-incident-monitor kuruluyor, birkac dakika surebilir...
  %PY% -m venv .venv || goto :failed
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt || goto :failed
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -e . --no-deps || goto :failed
)

for /d %%D in ("..\social-data-collector*") do (
  if exist "%%~D\src\social_data_collector" if not exist "%%~D\.venv\Scripts\python.exe" (
    call :install_collector "%%~D" || goto :failed
  )
)

".venv\Scripts\python.exe" -B scripts\demo.py %*
goto :end

:install_collector
echo [2/2] social-data-collector kuruluyor...
pushd "%~1"
%PY% -m venv .venv || (popd & exit /b 1)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt || (popd & exit /b 1)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -e . --no-deps || (popd & exit /b 1)
popd
exit /b 0

:failed
echo.
echo Kurulum tamamlanamadi. Hata mesajina ve docs\DEMO.md "Sorun giderme" bolumune bakin.
echo Yarim kalan kurulum icin .venv klasorunu silip bu dosyayi tekrar calistirin.

:end
echo.
pause
