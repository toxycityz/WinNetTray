; ==============================================================================
; Inno Setup Script per WinNetTray
; Genera il VERO installer grafico ufficiale di Windows: WinNetTray_Setup.exe
; ==============================================================================

#define MyAppName "WinNetTray"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Luca Tosi"
#define MyAppURL "https://github.com/lucatosi1980/WinNetTray"
#define MyAppExeName "WinNetTray.exe"

[Setup]
; Identificativo univoco dell'applicazione
AppId={{5E938B42-4217-4F60-93D2-8A4621A58EF2}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; Modalità 64-bit nativa: installa in "C:\Program Files\WinNetTray" (NON x86)
ArchitecturesInstallIn64BitMode=x64compatible
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes

; Consente l'installazione sia come Amministratore che come utente standard
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; File di output: WinNetTray_Setup.exe nella cartella corrente
OutputDir=.
OutputBaseFilename=WinNetTray_Setup
Compression=lzma2/max
SolidCompression=yes

; Icona ufficiale multi-risoluzione, Licenza Freeware e Metadati PE Windows per Proprietà -> Dettagli
SetupIconFile=winnettray.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
LicenseFile=LICENSE.txt
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=WinNetTray Setup - Monitor di Rete e Temperature Hardware per Windows
VersionInfoCopyright=Freeware - Tutti i diritti riservati (Copyright © 2026 {#MyAppPublisher})
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

; Stile moderno di Windows e chiusura automatica dell'istanza attiva prima di aggiornare/disinstallare
WizardStyle=modern
CloseApplications=force
CloseApplicationsFilter=*.exe,*.dll,*.pyd

[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"

[Tasks]
Name: "desktopicon"; Description: "Crea icona sul Desktop con icona ufficiale WinNetTray"; GroupDescription: "Icone aggiuntive:"; Flags: checkedonce
Name: "autostart"; Description: "Avvia automaticamente WinNetTray all'accensione di Windows (Consigliato)"; GroupDescription: "Avvio automatico:"; Flags: checkedonce

[Files]
; Include tutti i componenti compilati (eseguibile firmato/metadati, icona e licenza)
Source: "dist\WinNetTray\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "winnettray.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Menu Start (usa l'icona multi-risoluzione incorporata direttamente in WinNetTray.exe)
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Comment: "WinNetTray - Monitor di Rete e Hardware"
; Desktop (se selezionato)
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; Comment: "WinNetTray - Monitor di Rete e Hardware"
; Cartella Esecuzione Automatica di Windows (se selezionato)
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: autostart; Comment: "WinNetTray - Monitor di Rete e Hardware"

[Run]
; Avvio facoltativo al termine dell'installazione
Filename: "{app}\{#MyAppExeName}"; Description: "Avvia subito WinNetTray nella barra delle applicazioni"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; 1. Chiude l'eventuale processo WinNetTray.exe in esecuzione nella System Tray prima di rimuovere i file
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM {#MyAppExeName}"; Flags: runhidden; RunOnceId: "KillWinNetTray"
; 2. Esegue la routine interna di pulizia profonda (configurazioni, log e chiavi NotifyIconSettings)
Filename: "{app}\{#MyAppExeName}"; Parameters: "--uninstall-clean"; Flags: runhidden; RunOnceId: "CleanWinNetTrayData"

[UninstallDelete]
; Rimozione completa di tutte le cartelle dati, configurazioni JSON, file di log e collegamenti sparsi nel sistema
Type: filesandordirs; Name: "{userappdata}\{#MyAppName}"
Type: filesandordirs; Name: "{localappdata}\{#MyAppName}"
Type: filesandordirs; Name: "{localappdata}\Programs\{#MyAppName}"
Type: filesandordirs; Name: "{app}"
Type: files; Name: "{userdesktop}\{#MyAppName}.lnk"
Type: files; Name: "{autodesktop}\{#MyAppName}.lnk"
Type: files; Name: "{userstartup}\{#MyAppName}.lnk"
Type: files; Name: "{autoprograms}\{#MyAppName}.lnk"
Type: files; Name: "{tmp}\winnettray_error.log"
Type: files; Name: "{tmp}\wnt_wizard_pkg.zip"
Type: files; Name: "{tmp}\wnt_setup.ps1"

[Code]
procedure CleanNotifyIconRegistry();
var
  Names: TArrayOfString;
  I: Integer;
  SubKeyPath, ExePath, ExeLow: String;
begin
  if RegGetSubkeyNames(HKEY_CURRENT_USER, 'Control Panel\NotifyIconSettings', Names) then
  begin
    for I := 0 to GetArrayLength(Names) - 1 do
    begin
      SubKeyPath := 'Control Panel\NotifyIconSettings\' + Names[I];
      if RegQueryStringValue(HKEY_CURRENT_USER, SubKeyPath, 'ExecutablePath', ExePath) then
      begin
        ExeLow := Lowercase(ExePath);
        if (Pos('winnettray', ExeLow) > 0) or (Pos('net_tray', ExeLow) > 0) then
        begin
          RegDeleteKeyIncludingSubkeys(HKEY_CURRENT_USER, SubKeyPath);
        end;
      end;
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#MyAppExeName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    CleanNotifyIconRegistry();
  end
  else if CurUninstallStep = usPostUninstall then
  begin
    CleanNotifyIconRegistry();
    DelTree(ExpandConstant('{userappdata}\{#MyAppName}'), True, True, True);
    DelTree(ExpandConstant('{localappdata}\{#MyAppName}'), True, True, True);
    DelTree(ExpandConstant('{app}'), True, True, True);
  end;
end;

