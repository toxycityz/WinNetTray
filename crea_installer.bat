@echo off
setlocal
chcp 65001 >nul
title Compilatore Installer Ufficiale WinNetTray
color 0B

cd /d "%~dp0"

echo ===================================================================
echo             COMPILATORE INSTALLER UFFICIALE WINDOWS
echo                   WinNetTray_Setup.exe (Inno Setup)
echo ===================================================================
echo Cartella di lavoro: %CD%
echo.

set PYTHON_CMD=python
python --version >nul 2>&1
if not errorlevel 1 goto check_py_ok

py -3 --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON_CMD=py -3
    goto check_py_ok
)

echo [ERRORE] Python non e' stato trovato nel sistema o non e' nel PATH.
echo Installa Python da https://www.python.org/ sulla macchina di sviluppo.
echo Ricordati di selezionare la casella 'Add Python to PATH'!
echo.
pause
exit /b 1

:check_py_ok
echo [OK] Interprete Python rilevato: %PYTHON_CMD%
%PYTHON_CMD% --version
echo.

if not exist "net_tray.py" (
    color 0C
    echo [ERRORE] Il file net_tray.py non e' presente in: %CD%
    echo Assicurati che net_tray.py si trovi nella stessa cartella di questo file .bat.
    pause
    exit /b 1
)

echo [1/4] Verifica ed installazione librerie e PyInstaller...
%PYTHON_CMD% -m pip install pyinstaller pystray Pillow psutil requests nvidia-ml-py
if errorlevel 1 (
    echo [Avviso] Errore durante pip install, procedo con i moduli gia' presenti...
)

echo.
echo [2/4] Generazione asset di pubblicazione (winnettray.ico, version_info.txt, LICENSE.txt)...
%PYTHON_CMD% net_tray.py --generate-build-assets

echo.
echo [3/4] Compilazione eseguibile autonomo singolo file WinNetTray.exe (--onefile)...
echo Incorporamento di icona multi-risoluzione, metadati versione Windows e librerie...
if exist "WinNetTray.spec" del /f /q "WinNetTray.spec"
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
%PYTHON_CMD% -m PyInstaller --noconsole --onefile --clean --icon "winnettray.ico" --version-file "version_info.txt" --distpath "dist\WinNetTray" --name "WinNetTray" net_tray.py

if not exist "dist\WinNetTray\WinNetTray.exe" (
    color 0C
    echo [ERRORE] Compilazione PyInstaller non riuscita.
    echo Controlla gli errori visualizzati sopra nel terminale.
    pause
    exit /b 1
)
%PYTHON_CMD% net_tray.py --sign-binary "dist\WinNetTray\WinNetTray.exe"
copy /y "dist\WinNetTray\WinNetTray.exe" "WinNetTray.exe" >nul
%PYTHON_CMD% net_tray.py --compute-sha256 "WinNetTray.exe"
echo [OK] Eseguibile unico portatile pronto in: %CD%\WinNetTray.exe (con checksum WinNetTray.exe.sha256)

echo.
echo [4/4] Ricerca e preparazione compilatore Inno Setup...

set "ISCC_EXE="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"

if defined ISCC_EXE goto compile_setup

echo [Info] Inno Setup 6 non e' ancora installato sulla macchina di sviluppo.
echo Download automatico del pacchetto ufficiale da jrsoftware.org...
set "INNO_SETUP_INST=%TEMP%\innosetup_setup.exe"
powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; (New-Object System.Net.WebClient).DownloadFile('https://files.jrsoftware.org/is/6/innosetup-6.3.3.exe', '%TEMP%\innosetup_setup.exe')"

if not exist "%TEMP%\innosetup_setup.exe" goto inno_manual

echo Installazione rapida e silenziosa in corso...
"%TEMP%\innosetup_setup.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
del "%TEMP%\innosetup_setup.exe" 2>nul

if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"

if not defined ISCC_EXE goto inno_manual

:compile_setup
echo [OK] Compilatore Inno Setup pronto: "%ISCC_EXE%"
echo Creazione pacchetto installer 'WinNetTray_Setup.exe'...
"%ISCC_EXE%" "winnettray.iss"

if not exist "WinNetTray_Setup.exe" goto inno_manual

%PYTHON_CMD% net_tray.py --sign-binary "WinNetTray_Setup.exe"
%PYTHON_CMD% net_tray.py --compute-sha256 "WinNetTray_Setup.exe"

color 0A
echo.
echo ===================================================================
echo         [VERO INSTALLER GRAFICO .EXE CREATO CON SUCCESSO!]
echo File pronto da distribuire: %CD%\WinNetTray_Setup.exe
echo Checksum SHA-256 salvato in: %CD%\WinNetTray_Setup.exe.sha256
echo Modulo Whitelist Microsoft:  %CD%\RICHIESTA_WHITELIST_MICROSOFT.txt
echo ===================================================================
echo.
echo COSA OFFRE QUESTO VERO INSTALLER:
echo 1. Installa i file binari completi in: C:\Program Files\WinNetTray
echo 2. Crea il collegamento Desktop e Start che punta a: WinNetTray.exe
echo 3. Icona di rete Windows autentica (senza icone con punto interrogativo)
echo 4. Avvio istantaneo e silenzioso con le 4 icone nella barra delle applicazioni
echo 5. Disinstallatore ufficiale presente in 'Impostazioni' - 'App installate'
echo.
echo WHITELIST GRATUITA MICROSOFT (PER RIMUOVERE L'AVVISO SMARTSCREEN):
echo E' stato generato il file 'RICHIESTA_WHITELIST_MICROSOFT.txt' con il
echo testo gia' precompilato e gli hash SHA-256 reali del tuo installer!
echo ===================================================================
echo.
set /p OPENWDSI="Vuoi aprire ora il portale Microsoft Whitelist e il testo precompilato? (S/N): "
if /i "%OPENWDSI%"=="S" (
    start "" notepad.exe "%CD%\RICHIESTA_WHITELIST_MICROSOFT.txt"
    start "" "https://www.microsoft.com/en-us/wdsi/filesubmission"
)
echo.
pause
exit /b 0

:inno_manual
color 0E
echo ===================================================================
echo [AVVISO] Compilazione Inno Setup non completata automaticamente.
echo.
echo I file binari sono pronti in 'dist\WinNetTray\'.
echo Puoi creare il setup manualmente in 5 secondi:
echo 1. Installa Inno Setup gratuito da: https://jrsoftware.org/isdl.php
echo 2. Fai click destro sul file 'winnettray.iss' presente in questa cartella
echo 3. Seleziona 'Compile'
echo Verra' generato istantaneamente 'WinNetTray_Setup.exe'!
echo ===================================================================
pause
exit /b 1
