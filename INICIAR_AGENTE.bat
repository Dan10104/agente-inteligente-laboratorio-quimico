@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Lib\site-packages\tensorflow" (
    echo ERROR: No se encontro el entorno virtual .venv.
    echo Consulte README.md para instalar el entorno del proyecto.
    pause
    exit /b 1
)
if not exist "main.py" (
    echo ERROR: No se encontro main.py en la carpeta del proyecto.
    pause
    exit /b 1
)
call ".venv\Scripts\activate.bat"
if exist ".runtime\python\python.exe" (
    "%~dp0.runtime\python\python.exe" "%~dp0main.py" %*
) else (
    "%~dp0.venv\Scripts\python.exe" "%~dp0main.py" %*
)
set "AGENTE_EXIT_CODE=%ERRORLEVEL%"
if not "%AGENTE_EXIT_CODE%"=="0" (
    echo.
    echo La aplicacion termino con error %AGENTE_EXIT_CODE%.
    echo Revise logs\agente.log o ejecute DIAGNOSTICO.bat.
    pause
)
exit /b %AGENTE_EXIT_CODE%
