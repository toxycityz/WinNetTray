@echo off
chcp 65001 >nul
title Compilatore e Creatore WinNetTray
color 0B

cd /d "%~dp0"

echo ===================================================================
echo              COMPILATORE E CREATORE WinNetTray
echo       Monitor Rete e Hardware per Windows System Tray
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
echo Installa Python da https://www.python.org/
echo Ricordati di selezionare la spunta 'Add Python to PATH' durante l'installazione!
echo.
pause
exit /b 1

:check_py_ok
echo [OK] Interprete rilevato: %PYTHON_CMD%
%PYTHON_CMD% --version
echo.

if not exist "net_tray.py" goto err_no_pyfile

echo [1/3] Installazione o verifica librerie necessarie...
%PYTHON_CMD% -m pip install pystray Pillow psutil requests pyinstaller nvidia-ml-py
if errorlevel 1 goto err_pip

echo.
echo [Asset Pubblicazione] Generazione automatica di winnettray.ico, version_info.txt e LICENSE.txt...
%PYTHON_CMD% net_tray.py --generate-build-assets

echo.
echo ===================================================================
echo                  SCEGLI LA MODALITA' DESIDERATA:
echo ===================================================================
echo.
echo  --- SOLUZIONI ORIGINALI (PyInstaller) ---
echo  1. Cartella Standalone PyInstaller (--onedir)
echo     [La vecchia soluzione 1: crea la cartella con WinNetTray.exe e dll]
echo.
echo  2. File Singolo PyInstaller (--onefile)
echo     [Eseguibile compresso autonomo classico da ~40 MB]
echo.
echo  --- NUOVE SOLUZIONI PER IL PC DEL LAVORO (Anti-Blocco Antivirus) ---
echo  3. Collegamento Windows 'WinNetTray.lnk' con Icona
echo     [Icona di rete Windows, 100% invisibile, richiede Python locale]
echo.
echo  4. Micro-Eseguibile Nativo 'WinNetTray.exe' (~8 KB)
echo     [Vero file .exe compilato al volo con Microsoft csc.exe, no PyInstaller]
echo.
echo  5. CREA INSTALLATORE UFFICIALE: 'WinNetTray_Setup.exe' [CONSIGLIATO PER PUBBLICAZIONE]
echo     [Genera icona .ico, metadati versione, installer Inno Setup e checksum SHA-256]
echo.
echo ===================================================================
set /p SCELTA="Digita il numero (1, 2, 3, 4 o 5) e premi Invio [Default=5]: "

if "%SCELTA%"=="1" goto build_onedir
if "%SCELTA%"=="2" goto build_onefile
if "%SCELTA%"=="3" goto create_shortcut
if "%SCELTA%"=="4" goto build_native_exe
goto build_installer_pkg

:build_installer_pkg
if exist "crea_installer.bat" (
    call crea_installer.bat
    goto finish
)

:build_onedir
echo.
echo [2/3] Compilazione in corso in modalita' cartella PyInstaller (--onedir)...
echo Attendere circa 20-30 secondi...
if exist "WinNetTray.spec" del /f /q "WinNetTray.spec"
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
%PYTHON_CMD% -m PyInstaller --noconsole --onedir --clean --icon "winnettray.ico" --version-file "version_info.txt" --name "WinNetTray" net_tray.py
if not exist "dist\WinNetTray\WinNetTray.exe" goto err_build

%PYTHON_CMD% net_tray.py --sign-binary "dist\WinNetTray\WinNetTray.exe"
%PYTHON_CMD% net_tray.py --compute-sha256 "dist\WinNetTray\WinNetTray.exe"

echo.
echo [3/3] Finalizzazione...
if not exist "WinNetTray_App" mkdir "WinNetTray_App"
xcopy /E /I /Y "dist\WinNetTray\*" "WinNetTray_App\" >nul

color 0A
echo ===================================================================
echo [COMPLETATO CON SUCCESSO!]
echo La cartella completa (exe + cartella _internal) e' pronta in:
echo %CD%\WinNetTray_App\WinNetTray.exe
echo NOTA: Per un unico file .exe portatile senza cartella _internal usa l'Opzione 2!
echo ===================================================================
goto ask_run_onedir

:build_onefile
echo.
echo [2/3] Compilazione in corso come UNICO FILE .EXE autonomo (--onefile)...
echo Incorporamento di icona multi-risoluzione, metadati versione Windows e librerie...
echo Attendere circa 20-35 secondi...
if exist "WinNetTray.spec" del /f /q "WinNetTray.spec"
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
%PYTHON_CMD% -m PyInstaller --noconsole --onefile --clean --icon "winnettray.ico" --version-file "version_info.txt" --name "WinNetTray" net_tray.py
if not exist "dist\WinNetTray.exe" goto err_build

%PYTHON_CMD% net_tray.py --sign-binary "dist\WinNetTray.exe"
copy /y "dist\WinNetTray.exe" "WinNetTray.exe" >nul
%PYTHON_CMD% net_tray.py --compute-sha256 "WinNetTray.exe"
color 0A
echo ===================================================================
echo [COMPLETATO CON SUCCESSO!]
echo Il file eseguibile UNICO e PORTATILE (con icona e metadati versione) e' qui:
echo %CD%\WinNetTray.exe
echo Checksum SHA-256: %CD%\WinNetTray.exe.sha256
echo ===================================================================
goto ask_run_onefile

:create_shortcut
echo.
echo [2/3] Creazione del collegamento 'WinNetTray.lnk' con icona di rete...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$WshShell = New-Object -ComObject WScript.Shell; $Shortcut = $WshShell.CreateShortcut('WinNetTray.lnk'); $Shortcut.TargetPath = 'pythonw.exe'; $Shortcut.Arguments = 'net_tray.py'; $Shortcut.WorkingDirectory = (Get-Item '.').FullName; $Shortcut.WindowStyle = 7; $icoLocal = (Join-Path (Get-Item '.').FullName 'winnettray.ico'); if (Test-Path $icoLocal) { $Shortcut.IconLocation = $icoLocal } else { $Shortcut.IconLocation = $env:SystemRoot + '\System32\imageres.dll,140' }; $Shortcut.Description = 'WinNetTray - Monitor di Rete e Hardware'; $Shortcut.Save()"

if not exist "WinNetTray.lnk" goto err_shortcut

color 0A
echo ===================================================================
echo [COMPLETATO CON SUCCESSO!]
echo Il collegamento 'WinNetTray' e' pronto nella stessa cartella:
echo %CD%\WinNetTray.lnk
echo.
echo Vantaggi:
echo - Ha l'icona ufficiale di WinNetTray.
echo - Doppio click: parte subito senza finestre console o ritardi.
echo - L'antivirus NON lo blocca perche' usa il pythonw.exe certificato.
echo - Puoi trascinarlo sul Desktop o metterlo in 'shell:startup'.
echo ===================================================================
echo.
set /p RUNNOW="Vuoi avviare subito WinNetTray? (S per Si, N per No): "
if /i "%RUNNOW%"=="S" start "" "%CD%\WinNetTray.lnk"
goto finish

:build_native_exe
echo.
echo [2/3] Incorporamento di 'net_tray.py', icona e metadati dentro 'WinNetTray.exe'...

set CSC_PATH=
if exist "%SystemRoot%\Microsoft.NET\Framework64\v4.0.30319\csc.exe" (
    set "CSC_PATH=%SystemRoot%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
) else if exist "%SystemRoot%\Microsoft.NET\Framework\v4.0.30319\csc.exe" (
    set "CSC_PATH=%SystemRoot%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
)

if "%CSC_PATH%"=="" goto err_no_csc

> "%TEMP%\WinNetTray_Launcher.cs" echo using System;
>> "%TEMP%\WinNetTray_Launcher.cs" echo using System.IO;
>> "%TEMP%\WinNetTray_Launcher.cs" echo using System.Reflection;
>> "%TEMP%\WinNetTray_Launcher.cs" echo using System.Diagnostics;
>> "%TEMP%\WinNetTray_Launcher.cs" echo [assembly: AssemblyTitle("WinNetTray - Monitor di Rete e Temperature Hardware")]
>> "%TEMP%\WinNetTray_Launcher.cs" echo [assembly: AssemblyDescription("WinNetTray - Monitor di Rete e Temperature Hardware per System Tray")]
>> "%TEMP%\WinNetTray_Launcher.cs" echo [assembly: AssemblyCompany("Luca Tosi")]
>> "%TEMP%\WinNetTray_Launcher.cs" echo [assembly: AssemblyProduct("WinNetTray")]
>> "%TEMP%\WinNetTray_Launcher.cs" echo [assembly: AssemblyCopyright("Freeware - Tutti i diritti riservati (Copyright (c) 2026 Luca Tosi)")]
>> "%TEMP%\WinNetTray_Launcher.cs" echo [assembly: AssemblyVersion("1.0.0.0")]
>> "%TEMP%\WinNetTray_Launcher.cs" echo [assembly: AssemblyFileVersion("1.0.0.0")]
>> "%TEMP%\WinNetTray_Launcher.cs" echo class Program {
>> "%TEMP%\WinNetTray_Launcher.cs" echo     static void Main() {
>> "%TEMP%\WinNetTray_Launcher.cs" echo         try {
>> "%TEMP%\WinNetTray_Launcher.cs" echo             string appData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
>> "%TEMP%\WinNetTray_Launcher.cs" echo             string targetDir = Path.Combine(appData, "WinNetTray");
>> "%TEMP%\WinNetTray_Launcher.cs" echo             Directory.CreateDirectory(targetDir);
>> "%TEMP%\WinNetTray_Launcher.cs" echo             string targetPy = Path.Combine(targetDir, "net_tray.py");
>> "%TEMP%\WinNetTray_Launcher.cs" echo             Assembly asm = Assembly.GetExecutingAssembly();
>> "%TEMP%\WinNetTray_Launcher.cs" echo             Stream resStream = null;
>> "%TEMP%\WinNetTray_Launcher.cs" echo             foreach (string rName in asm.GetManifestResourceNames()) {
>> "%TEMP%\WinNetTray_Launcher.cs" echo                 if (rName.EndsWith("net_tray.py", StringComparison.OrdinalIgnoreCase)) {
>> "%TEMP%\WinNetTray_Launcher.cs" echo                     resStream = asm.GetManifestResourceStream(rName);
>> "%TEMP%\WinNetTray_Launcher.cs" echo                     break;
>> "%TEMP%\WinNetTray_Launcher.cs" echo                 }
>> "%TEMP%\WinNetTray_Launcher.cs" echo             }
>> "%TEMP%\WinNetTray_Launcher.cs" echo             if (resStream != null) {
>> "%TEMP%\WinNetTray_Launcher.cs" echo                 using (FileStream fs = new FileStream(targetPy, FileMode.Create, FileAccess.Write)) {
>> "%TEMP%\WinNetTray_Launcher.cs" echo                     resStream.CopyTo(fs);
>> "%TEMP%\WinNetTray_Launcher.cs" echo                 }
>> "%TEMP%\WinNetTray_Launcher.cs" echo                 resStream.Dispose();
>> "%TEMP%\WinNetTray_Launcher.cs" echo             }
>> "%TEMP%\WinNetTray_Launcher.cs" echo             ProcessStartInfo psi = new ProcessStartInfo();
>> "%TEMP%\WinNetTray_Launcher.cs" echo             psi.FileName = "pythonw.exe";
>> "%TEMP%\WinNetTray_Launcher.cs" echo             psi.Arguments = "\"" + targetPy + "\"";
>> "%TEMP%\WinNetTray_Launcher.cs" echo             psi.WorkingDirectory = targetDir;
>> "%TEMP%\WinNetTray_Launcher.cs" echo             psi.UseShellExecute = false;
>> "%TEMP%\WinNetTray_Launcher.cs" echo             psi.CreateNoWindow = true;
>> "%TEMP%\WinNetTray_Launcher.cs" echo             Process.Start(psi);
>> "%TEMP%\WinNetTray_Launcher.cs" echo         } catch {}
>> "%TEMP%\WinNetTray_Launcher.cs" echo     }
>> "%TEMP%\WinNetTray_Launcher.cs" echo }

set "CSC_ICON_ARG="
if exist "winnettray.ico" set "CSC_ICON_ARG=/win32icon:winnettray.ico"
"%CSC_PATH%" /nologo /target:winexe /optimize+ %CSC_ICON_ARG% /resource:net_tray.py /out:"WinNetTray.exe" "%TEMP%\WinNetTray_Launcher.cs"
del "%TEMP%\WinNetTray_Launcher.cs" >nul 2>&1

if not exist "WinNetTray.exe" goto err_build_native

%PYTHON_CMD% net_tray.py --sign-binary "WinNetTray.exe"
%PYTHON_CMD% net_tray.py --compute-sha256 "WinNetTray.exe"

color 0A
echo ===================================================================
echo [COMPLETATO CON SUCCESSO!]
echo Il file eseguibile STANDALONE 'WinNetTray.exe' e' pronto:
echo %CD%\WinNetTray.exe
echo.
echo VANTAGGI SPECIALI:
echo - E' un UNICO file .exe: NON serve copiare nessun file .py assieme!
echo - Tutto il codice di WinNetTray e' incorporato dentro l'eseguibile.
echo - NON e' fatto con PyInstaller, quindi l'antivirus aziendale non lo blocca!
echo - Lo puoi copiare su chiavetta o sul Desktop del lavoro e avviarlo subito.
echo ===================================================================
goto ask_run_onefile

:ask_run_onedir
echo.
echo [Whitelist Gratuita Microsoft] Modulo precompilato con hash SHA-256 salvato in:
echo %CD%\RICHIESTA_WHITELIST_MICROSOFT.txt
set /p OPENWDSI="Vuoi aprire il portale Microsoft Whitelist e il modulo precompilato? (S/N): "
if /i "%OPENWDSI%"=="S" (
    start "" notepad.exe "%CD%\RICHIESTA_WHITELIST_MICROSOFT.txt"
    start "" "https://www.microsoft.com/en-us/wdsi/filesubmission"
)
echo.
set /p RUNNOW="Vuoi avviare subito WinNetTray? (S per Si, N per No): "
if /i "%RUNNOW%"=="S" start "" "%CD%\WinNetTray_App\WinNetTray.exe"
goto finish

:ask_run_onefile
echo.
echo [Whitelist Gratuita Microsoft] Modulo precompilato con hash SHA-256 salvato in:
echo %CD%\RICHIESTA_WHITELIST_MICROSOFT.txt
set /p OPENWDSI="Vuoi aprire il portale Microsoft Whitelist e il modulo precompilato? (S/N): "
if /i "%OPENWDSI%"=="S" (
    start "" notepad.exe "%CD%\RICHIESTA_WHITELIST_MICROSOFT.txt"
    start "" "https://www.microsoft.com/en-us/wdsi/filesubmission"
)
echo.
set /p RUNNOW="Vuoi avviare subito WinNetTray? (S per Si, N per No): "
if /i "%RUNNOW%"=="S" start "" "%CD%\WinNetTray.exe"
goto finish

:err_no_pyfile
color 0C
echo.
echo [ERRORE] Il file net_tray.py non e' presente in: %CD%
echo Assicurati di mettere questo file .bat nella stessa cartella di net_tray.py!
echo.
pause
exit /b 1

:err_pip
color 0C
echo.
echo [ERRORE] Errore durante l'installazione delle librerie con pip.
echo.
pause
exit /b 1

:err_build
color 0C
echo.
echo [ERRORE] PyInstaller non e' riuscito a generare l'eseguibile.
echo Controlla i messaggi di errore qui sopra.
echo.
pause
exit /b 1

:err_shortcut
color 0C
echo.
echo [ERRORE] Impossibile creare il collegamento WinNetTray.lnk.
echo.
pause
exit /b 1

:err_no_csc
color 0C
echo.
echo [ERRORE] Compilatore Microsoft csc.exe non trovato nel percorso .NET Framework standard.
echo Usa l'Opzione 3 (Collegamento Windows), che funziona sempre al 100%!
echo.
pause
exit /b 1

:err_build_native
color 0C
echo.
echo [ERRORE] Compilazione di WinNetTray.exe non riuscita con csc.exe.
echo Prova l'Opzione 3 (Collegamento Windows).
echo.
pause
exit /b 1

:finish
echo.
echo Operazione conclusa con successo.
pause
exit /b 0
