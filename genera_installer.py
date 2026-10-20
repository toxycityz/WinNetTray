# -*- coding: utf-8 -*-
"""
Generatore dell'Installer Grafico Nativo per WinNetTray (Wizard Windows Forms).
Unisce il launcher batch, il codice dell'interfaccia grafica a passaggi e il payload zip.
"""
import os
import sys

def build_installer(payload_zip_path, output_bat_path):
    if not os.path.exists(payload_zip_path):
        print(f"[ERRORE] File payload non trovato: {payload_zip_path}")
        return False

    ps_wizard_code = r'''
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.Windows.Forms.Application]::EnableVisualStyles()

# Configurazione Finestra Principale
$form = New-Object System.Windows.Forms.Form
$form.Text = "Installazione guidata di WinNetTray"
$form.Size = New-Object System.Drawing.Size(600, 440)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.BackColor = [System.Drawing.Color]::FromArgb(248, 250, 252)

# Banner Superiore
$topBanner = New-Object System.Windows.Forms.Panel
$topBanner.Dock = "Top"
$topBanner.Height = 72
$topBanner.BackColor = [System.Drawing.Color]::FromArgb(15, 23, 42)
$form.Controls.Add($topBanner)

$lblBannerTitle = New-Object System.Windows.Forms.Label
$lblBannerTitle.Text = "WinNetTray • Installazione Guidata"
$lblBannerTitle.Font = New-Object System.Drawing.Font("Segoe UI", 13, [System.Drawing.FontStyle]::Bold)
$lblBannerTitle.ForeColor = [System.Drawing.Color]::White
$lblBannerTitle.Location = New-Object System.Drawing.Point(24, 14)
$lblBannerTitle.AutoSize = $true
$topBanner.Controls.Add($lblBannerTitle)

$lblBannerSub = New-Object System.Windows.Forms.Label
$lblBannerSub.Text = "Monitor di Rete & Temperature Hardware per la Barra delle Applicazioni"
$lblBannerSub.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$lblBannerSub.ForeColor = [System.Drawing.Color]::FromArgb(148, 163, 184)
$lblBannerSub.Location = New-Object System.Drawing.Point(26, 40)
$lblBannerSub.AutoSize = $true
$topBanner.Controls.Add($lblBannerSub)

# Barra Inferiore con Pulsanti
$bottomPanel = New-Object System.Windows.Forms.Panel
$bottomPanel.Dock = "Bottom"
$bottomPanel.Height = 56
$bottomPanel.BackColor = [System.Drawing.Color]::FromArgb(241, 245, 249)
$form.Controls.Add($bottomPanel)

$btnCancel = New-Object System.Windows.Forms.Button
$btnCancel.Text = "Annulla"
$btnCancel.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$btnCancel.Size = New-Object System.Drawing.Size(88, 30)
$btnCancel.Location = New-Object System.Drawing.Point(484, 12)
$btnCancel.Add_Click({ $form.Close() })
$bottomPanel.Controls.Add($btnCancel)

$btnNext = New-Object System.Windows.Forms.Button
$btnNext.Text = "Avanti >"
$btnNext.Font = New-Object System.Drawing.Font("Segoe UI", 9, [System.Drawing.FontStyle]::Bold)
$btnNext.BackColor = [System.Drawing.Color]::FromArgb(37, 99, 235)
$btnNext.ForeColor = [System.Drawing.Color]::White
$btnNext.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$btnNext.Size = New-Object System.Drawing.Size(92, 30)
$btnNext.Location = New-Object System.Drawing.Point(384, 12)
$btnNext.Cursor = [System.Windows.Forms.Cursors]::Hand
$bottomPanel.Controls.Add($btnNext)

$btnBack = New-Object System.Windows.Forms.Button
$btnBack.Text = "< Indietro"
$btnBack.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$btnBack.Size = New-Object System.Drawing.Size(88, 30)
$btnBack.Location = New-Object System.Drawing.Point(288, 12)
$btnBack.Enabled = $false
$bottomPanel.Controls.Add($btnBack)

# Area Contenuto Pagine (Wizard Pages)
$pageContainer = New-Object System.Windows.Forms.Panel
$pageContainer.Dock = "Fill"
$pageContainer.Padding = New-Object System.Windows.Forms.Padding(24)
$form.Controls.Add($pageContainer)

# ----------------- PAGINA 1: BENVENUTO & DISCLAIMER -----------------
$page1 = New-Object System.Windows.Forms.Panel
$page1.Dock = "Fill"
$pageContainer.Controls.Add($page1)

$lblP1Title = New-Object System.Windows.Forms.Label
$lblP1Title.Text = "Benvenuto nell'installazione di WinNetTray"
$lblP1Title.Font = New-Object System.Drawing.Font("Segoe UI", 11, [System.Drawing.FontStyle]::Bold)
$lblP1Title.ForeColor = [System.Drawing.Color]::FromArgb(30, 41, 59)
$lblP1Title.Location = New-Object System.Drawing.Point(10, 10)
$lblP1Title.AutoSize = $true
$page1.Controls.Add($lblP1Title)

$lblP1Desc = New-Object System.Windows.Forms.Label
$lblP1Desc.Text = "Si consiglia di leggere le seguenti note e il disclaimer prima di continuare:"
$lblP1Desc.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$lblP1Desc.ForeColor = [System.Drawing.Color]::FromArgb(71, 85, 105)
$lblP1Desc.Location = New-Object System.Drawing.Point(12, 38)
$lblP1Desc.AutoSize = $true
$page1.Controls.Add($lblP1Desc)

$txtDisclaimer = New-Object System.Windows.Forms.TextBox
$txtDisclaimer.Multiline = $true
$txtDisclaimer.ReadOnly = $true
$txtDisclaimer.ScrollBars = "Vertical"
$txtDisclaimer.BackColor = [System.Drawing.Color]::White
$txtDisclaimer.Font = New-Object System.Drawing.Font("Segoe UI", 8.5)
$txtDisclaimer.Location = New-Object System.Drawing.Point(12, 65)
$txtDisclaimer.Size = New-Object System.Drawing.Size(520, 185)
$txtDisclaimer.Text = @"
CONTRATTO DI LICENZA D'USO (EULA) & DISCLAIMER
Freeware - Tutti i diritti riservati (Copyright © 2026 Luca Tosi)

1. CONCESSIONE DI LICENZA FREEWARE E TUTELA DEL COPYRIGHT
Il software WinNetTray è di proprietà esclusiva di Luca Tosi (Tutti i diritti riservati). È concesso l'uso gratuito su computer personali e aziendali. Sono espressamente vietati la modifica, la decompilazione, l'estrazione del codice sorgente, la rimozione del copyright e la rivendita.

2. PRIVACY E SICUREZZA (ZERO TELEMETRIA)
WinNetTray opera esclusivamente in locale sul tuo computer. Non raccoglie, non memorizza e non trasmette a server esterni alcun dato personale, cronologia di navigazione o informazione sul traffico di rete.

3. FUNZIONAMENTO E CONSUMO RISORSE
L'applicazione è progettata per vivere silenziosamente nella barra delle applicazioni (System Tray accanto all'orologio) con un consumo medio di memoria RAM inferiore a 15 MB e impatto CPU trascurabile (< 0.1%).

4. ESCLUSIONE DI GARANZIA (DISCLAIMER)
Il software viene fornito "così com'è" (AS IS), senza alcuna garanzia esplicita o implicita. In nessun caso l'autore sarà ritenuto responsabile per eventuali danni, malfunzionamenti o anomalie derivanti dall'uso di questa applicazione.

Facendo clic su 'Avanti', accetti i presenti termini e confermi di voler installare WinNetTray sul tuo computer.
"@
$page1.Controls.Add($txtDisclaimer)

# ----------------- PAGINA 2: SELEZIONE CARTELLA -----------------
$page2 = New-Object System.Windows.Forms.Panel
$page2.Dock = "Fill"
$page2.Visible = $false
$pageContainer.Controls.Add($page2)

$lblP2Title = New-Object System.Windows.Forms.Label
$lblP2Title.Text = "Seleziona la cartella di installazione"
$lblP2Title.Font = New-Object System.Drawing.Font("Segoe UI", 11, [System.Drawing.FontStyle]::Bold)
$lblP2Title.ForeColor = [System.Drawing.Color]::FromArgb(30, 41, 59)
$lblP2Title.Location = New-Object System.Drawing.Point(10, 10)
$lblP2Title.AutoSize = $true
$page2.Controls.Add($lblP2Title)

$lblP2Desc = New-Object System.Windows.Forms.Label
$lblP2Desc.Text = "WinNetTray verrà installato nella seguente cartella di Windows." + [char]10 + "Per installare in una cartella diversa, fai clic su 'Sfoglia...'."
$lblP2Desc.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$lblP2Desc.ForeColor = [System.Drawing.Color]::FromArgb(71, 85, 105)
$lblP2Desc.Location = New-Object System.Drawing.Point(12, 38)
$lblP2Desc.Size = New-Object System.Drawing.Size(520, 36)
$page2.Controls.Add($lblP2Desc)

$grpPath = New-Object System.Windows.Forms.GroupBox
$grpPath.Text = "Cartella di destinazione:"
$grpPath.Font = New-Object System.Drawing.Font("Segoe UI", 9, [System.Drawing.FontStyle]::Bold)
$grpPath.Location = New-Object System.Drawing.Point(12, 85)
$grpPath.Size = New-Object System.Drawing.Size(520, 80)
$page2.Controls.Add($grpPath)

# Cartella predefinita: C:\Program Files\WinNetTray
$defaultDir = [System.IO.Path]::Combine([System.Environment]::GetFolderPath('ProgramFiles'), 'WinNetTray')
$txtPath = New-Object System.Windows.Forms.TextBox
$txtPath.Text = $defaultDir
$txtPath.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$txtPath.Location = New-Object System.Drawing.Point(16, 32)
$txtPath.Size = New-Object System.Drawing.Size(385, 26)
$grpPath.Controls.Add($txtPath)

$btnBrowse = New-Object System.Windows.Forms.Button
$btnBrowse.Text = "Sfoglia..."
$btnBrowse.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$btnBrowse.Location = New-Object System.Drawing.Point(412, 30)
$btnBrowse.Size = New-Object System.Drawing.Size(94, 28)
$btnBrowse.Cursor = [System.Windows.Forms.Cursors]::Hand
$btnBrowse.Add_Click({
    $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
    $dlg.Description = "Scegli la cartella in cui installare WinNetTray:"
    $dlg.SelectedPath = $txtPath.Text
    if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        $txtPath.Text = $dlg.SelectedPath
    }
})
$grpPath.Controls.Add($btnBrowse)

$lblSpace = New-Object System.Windows.Forms.Label
$lblSpace.Text = "Spazio su disco richiesto: circa 35 MB."
$lblSpace.Font = New-Object System.Drawing.Font("Segoe UI", 8.5)
$lblSpace.ForeColor = [System.Drawing.Color]::FromArgb(100, 116, 139)
$lblSpace.Location = New-Object System.Drawing.Point(14, 180)
$lblSpace.AutoSize = $true
$page2.Controls.Add($lblSpace)

$lblNoteAdmin = New-Object System.Windows.Forms.Label
$lblNoteAdmin.Text = "Nota: Se sul PC aziendale non hai i permessi di Amministratore per scrivere in C:\Program Files, l'installer ti proporrà automaticamente di installare nella cartella programmi utente senza errori."
$lblNoteAdmin.Font = New-Object System.Drawing.Font("Segoe UI", 8.5, [System.Drawing.FontStyle]::Italic)
$lblNoteAdmin.ForeColor = [System.Drawing.Color]::FromArgb(148, 163, 184)
$lblNoteAdmin.Location = New-Object System.Drawing.Point(14, 208)
$lblNoteAdmin.Size = New-Object System.Drawing.Size(515, 45)
$page2.Controls.Add($lblNoteAdmin)

# ----------------- PAGINA 3: OPZIONI AVVIO E COLLEGAMENTI -----------------
$page3 = New-Object System.Windows.Forms.Panel
$page3.Dock = "Fill"
$page3.Visible = $false
$pageContainer.Controls.Add($page3)

$lblP3Title = New-Object System.Windows.Forms.Label
$lblP3Title.Text = "Seleziona le attività aggiuntive"
$lblP3Title.Font = New-Object System.Drawing.Font("Segoe UI", 11, [System.Drawing.FontStyle]::Bold)
$lblP3Title.ForeColor = [System.Drawing.Color]::FromArgb(30, 41, 59)
$lblP3Title.Location = New-Object System.Drawing.Point(10, 10)
$lblP3Title.AutoSize = $true
$page3.Controls.Add($lblP3Title)

$lblP3Desc = New-Object System.Windows.Forms.Label
$lblP3Desc.Text = "Configura l'avvio automatico e le icone di collegamento:"
$lblP3Desc.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$lblP3Desc.ForeColor = [System.Drawing.Color]::FromArgb(71, 85, 105)
$lblP3Desc.Location = New-Object System.Drawing.Point(12, 38)
$lblP3Desc.AutoSize = $true
$page3.Controls.Add($lblP3Desc)

$grpOpts = New-Object System.Windows.Forms.GroupBox
$grpOpts.Text = "Collegamenti e Avvio di Windows:"
$grpOpts.Font = New-Object System.Drawing.Font("Segoe UI", 9, [System.Drawing.FontStyle]::Bold)
$grpOpts.Location = New-Object System.Drawing.Point(12, 75)
$grpOpts.Size = New-Object System.Drawing.Size(520, 150)
$page3.Controls.Add($grpOpts)

$chkStartup = New-Object System.Windows.Forms.CheckBox
$chkStartup.Text = "Avvia automaticamente WinNetTray all'accensione di Windows (Consigliato)"
$chkStartup.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$chkStartup.Checked = $true
$chkStartup.Location = New-Object System.Drawing.Point(18, 30)
$chkStartup.Size = New-Object System.Drawing.Size(480, 24)
$grpOpts.Controls.Add($chkStartup)

$chkDesktop = New-Object System.Windows.Forms.CheckBox
$chkDesktop.Text = "Crea collegamento sul Desktop con icona di rete Windows"
$chkDesktop.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$chkDesktop.Checked = $true
$chkDesktop.Location = New-Object System.Drawing.Point(18, 65)
$chkDesktop.Size = New-Object System.Drawing.Size(480, 24)
$grpOpts.Controls.Add($chkDesktop)

$chkRunNow = New-Object System.Windows.Forms.CheckBox
$chkRunNow.Text = "Avvia subito WinNetTray al termine dell'installazione"
$chkRunNow.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$chkRunNow.Checked = $true
$chkRunNow.Location = New-Object System.Drawing.Point(18, 100)
$chkRunNow.Size = New-Object System.Drawing.Size(480, 24)
$grpOpts.Controls.Add($chkRunNow)

# ----------------- PAGINA 4: INSTALLAZIONE / AVANZAMENTO -----------------
$page4 = New-Object System.Windows.Forms.Panel
$page4.Dock = "Fill"
$page4.Visible = $false
$pageContainer.Controls.Add($page4)

$lblP4Title = New-Object System.Windows.Forms.Label
$lblP4Title.Text = "Installazione in corso..."
$lblP4Title.Font = New-Object System.Drawing.Font("Segoe UI", 11, [System.Drawing.FontStyle]::Bold)
$lblP4Title.ForeColor = [System.Drawing.Color]::FromArgb(30, 41, 59)
$lblP4Title.Location = New-Object System.Drawing.Point(10, 10)
$lblP4Title.AutoSize = $true
$page4.Controls.Add($lblP4Title)

$lblStatus = New-Object System.Windows.Forms.Label
$lblStatus.Text = "Preparazione dei file..."
$lblStatus.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$lblStatus.ForeColor = [System.Drawing.Color]::FromArgb(71, 85, 105)
$lblStatus.Location = New-Object System.Drawing.Point(12, 45)
$lblStatus.Size = New-Object System.Drawing.Size(520, 25)
$page4.Controls.Add($lblStatus)

$pBar = New-Object System.Windows.Forms.ProgressBar
$pBar.Location = New-Object System.Drawing.Point(12, 80)
$pBar.Size = New-Object System.Drawing.Size(520, 24)
$pBar.Style = [System.Windows.Forms.ProgressBarStyle]::Continuous
$pBar.Value = 0
$page4.Controls.Add($pBar)

# ----------------- PAGINA 5: FINE -----------------
$page5 = New-Object System.Windows.Forms.Panel
$page5.Dock = "Fill"
$page5.Visible = $false
$pageContainer.Controls.Add($page5)

$lblP5Title = New-Object System.Windows.Forms.Label
$lblP5Title.Text = "Completamento dell'installazione di WinNetTray"
$lblP5Title.Font = New-Object System.Drawing.Font("Segoe UI", 12, [System.Drawing.FontStyle]::Bold)
$lblP5Title.ForeColor = [System.Drawing.Color]::FromArgb(16, 185, 129)
$lblP5Title.Location = New-Object System.Drawing.Point(10, 10)
$lblP5Title.AutoSize = $true
$page5.Controls.Add($lblP5Title)

$lblP5Desc = New-Object System.Windows.Forms.Label
$lblP5Desc.Text = "WinNetTray è stato installato con successo sul tuo computer!" + [char]10 + [char]10 +
                  "Le icone di monitoraggio compariranno nella barra di sistema accanto all'orologio." + [char]10 +
                  "Fai clic con il tasto destro su un'icona per aprire le Impostazioni o uscire."
$lblP5Desc.Font = New-Object System.Drawing.Font("Segoe UI", 9.5)
$lblP5Desc.ForeColor = [System.Drawing.Color]::FromArgb(51, 65, 85)
$lblP5Desc.Location = New-Object System.Drawing.Point(12, 45)
$lblP5Desc.Size = New-Object System.Drawing.Size(520, 80)
$page5.Controls.Add($lblP5Desc)

$lblInstalledPath = New-Object System.Windows.Forms.Label
$lblInstalledPath.Text = ""
$lblInstalledPath.Font = New-Object System.Drawing.Font("Segoe UI", 8.5, [System.Drawing.FontStyle]::Bold)
$lblInstalledPath.ForeColor = [System.Drawing.Color]::FromArgb(100, 116, 139)
$lblInstalledPath.Location = New-Object System.Drawing.Point(12, 140)
$lblInstalledPath.Size = New-Object System.Drawing.Size(520, 45)
$page5.Controls.Add($lblInstalledPath)

# ----------------- LOGICA DI NAVIGAZIONE WIZARD -----------------
$script:currentStep = 1

$fnUpdateView = {
    $page1.Visible = ($script:currentStep -eq 1)
    $page2.Visible = ($script:currentStep -eq 2)
    $page3.Visible = ($script:currentStep -eq 3)
    $page4.Visible = ($script:currentStep -eq 4)
    $page5.Visible = ($script:currentStep -eq 5)

    if ($script:currentStep -eq 1) {
        $btnBack.Enabled = $false
        $btnNext.Text = "Avanti >"
        $btnNext.Visible = $true
        $btnCancel.Visible = $true
    }
    elseif ($script:currentStep -eq 2) {
        $btnBack.Enabled = $true
        $btnNext.Text = "Avanti >"
        $btnNext.Visible = $true
        $btnCancel.Visible = $true
    }
    elseif ($script:currentStep -eq 3) {
        $btnBack.Enabled = $true
        $btnNext.Text = "Installa"
        $btnNext.Visible = $true
        $btnCancel.Visible = $true
    }
    elseif ($script:currentStep -eq 4) {
        $btnBack.Enabled = $false
        $btnNext.Visible = $false
        $btnCancel.Enabled = $false
    }
    elseif ($script:currentStep -eq 5) {
        $btnBack.Visible = $false
        $btnCancel.Visible = $false
        $btnNext.Text = "Fine"
        $btnNext.Visible = $true
        $btnNext.Enabled = $true
    }
}

$fnPerformInstall = {
    $script:currentStep = 4
    & $fnUpdateView
    $form.Refresh()

    $target = $txtPath.Text.Trim()
    if ([string]::IsNullOrWhiteSpace($target)) {
        $target = [System.IO.Path]::Combine([System.Environment]::GetFolderPath('ProgramFiles'), 'WinNetTray')
    }

    $lblStatus.Text = "Controllo cartella di destinazione: $target..."
    $pBar.Value = 15
    $form.Refresh()

    try {
        if (-not (Test-Path $target)) {
            [System.IO.Directory]::CreateDirectory($target) | Out-Null
        }
        # Test di scrittura
        $testFile = [System.IO.Path]::Combine($target, "__wnt_test.tmp")
        [System.IO.File]::WriteAllText($testFile, "test")
        [System.IO.File]::Delete($testFile)
    }
    catch {
        $userFallback = [System.IO.Path]::Combine([System.Environment]::GetFolderPath('LocalApplicationData'), 'Programs', 'WinNetTray')
        $msg = "Non si dispone dei privilegi di amministratore per scrivere in:" + [char]10 +
               $target + [char]10 + [char]10 +
               "Vuoi installare automaticamente nella cartella dell'utente corrente (senza richiedere password di admin)?" + [char]10 +
               $userFallback
        $res = [System.Windows.Forms.MessageBox]::Show($msg, "Permessi di Sistema", [System.Windows.Forms.MessageBoxButtons]::YesNo, [System.Windows.Forms.MessageBoxIcon]::Question)
        if ($res -eq [System.Windows.Forms.DialogResult]::Yes) {
            $target = $userFallback
            $txtPath.Text = $target
            if (-not (Test-Path $target)) {
                [System.IO.Directory]::CreateDirectory($target) | Out-Null
            }
        } else {
            $script:currentStep = 2
            & $fnUpdateView
            return
        }
    }

    $lblStatus.Text = "Estrazione componenti di runtime ed eseguibili..."
    $pBar.Value = 40
    $form.Refresh()

    # Estrazione del payload zip allegato a questo script .bat
    $me = $env:WNT_SELF_PATH
    if ([string]::IsNullOrWhiteSpace($me)) { $me = $MyInvocation.MyCommand.Path }
    if ([string]::IsNullOrWhiteSpace($me)) { $me = [System.IO.Path]::Combine((Get-Location).Path, 'Installa_WinNetTray.bat') }

    $bytes = [System.IO.File]::ReadAllBytes($me)
    $ascii = [System.Text.Encoding]::ASCII.GetString($bytes)
    $marker = '__PAYLOAD_BELOW__'
    $idx = $ascii.IndexOf($marker)
    if ($idx -ge 0) {
        $offset = $idx + $marker.Length
        while ($offset -lt $bytes.Length -and ($bytes[$offset] -eq 13 -or $bytes[$offset] -eq 10)) { $offset++ }
        $len = $bytes.Length - $offset
        $zipBytes = New-Object byte[] $len
        [System.Array]::Copy($bytes, $offset, $zipBytes, 0, $len)

        $tmpZip = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), 'wnt_wizard_pkg.zip')
        [System.IO.File]::WriteAllBytes($tmpZip, $zipBytes)

        $pBar.Value = 65
        $lblStatus.Text = "Decompressione file in $target..."
        $form.Refresh()

        [System.IO.Compression.ZipFile]::ExtractToDirectory($tmpZip, $target)
        Remove-Item $tmpZip -Force -ErrorAction SilentlyContinue
    }

    $pBar.Value = 85
    $lblStatus.Text = "Creazione collegamento con icona ufficiale..."
    $form.Refresh()

    $lnkPath = [System.IO.Path]::Combine($target, 'WinNetTray.lnk')
    $pyw = [System.IO.Path]::Combine($target, 'pythonw.exe')
    $wsh = New-Object -ComObject WScript.Shell
    $sc = $wsh.CreateShortcut($lnkPath)
    $sc.TargetPath = $pyw
    $sc.Arguments = 'net_tray.py'
    $sc.WorkingDirectory = $target
    $sc.WindowStyle = 7
    $sc.IconLocation = [System.IO.Path]::Combine($env:SystemRoot, 'System32', 'imageres.dll') + ',140'
    $sc.Description = "WinNetTray - Monitor di Rete e Hardware"
    $sc.Save()

    if ($chkDesktop.Checked) {
        $desk = [System.IO.Path]::Combine([System.Environment]::GetFolderPath('Desktop'), 'WinNetTray.lnk')
        Copy-Item $lnkPath -Destination $desk -Force
    }

    if ($chkStartup.Checked) {
        $startupFolder = [System.Environment]::GetFolderPath('Startup')
        $startupLnk = [System.IO.Path]::Combine($startupFolder, 'WinNetTray.lnk')
        Copy-Item $lnkPath -Destination $startupLnk -Force
    }

    # Creazione script di disinstallazione completa (Disinstalla_WinNetTray.bat) e registrazione in 'App installate'
    $uninstBat = [System.IO.Path]::Combine($target, 'Disinstalla_WinNetTray.bat')
    $uninstLines = @(
        '@echo off',
        'chcp 65001 >nul',
        'title Disinstallazione completa di WinNetTray',
        'echo Arresto di WinNetTray in corso...',
        'taskkill /F /IM WinNetTray.exe >nul 2>&1',
        'powershell -NoProfile -ExecutionPolicy Bypass -Command "' +
            'Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match ''net_tray\.py'' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; ' +
            '$paths = @(' +
                '[System.IO.Path]::Combine($env:APPDATA, ''WinNetTray''), ' +
                '[System.IO.Path]::Combine($env:LOCALAPPDATA, ''WinNetTray''), ' +
                '[System.IO.Path]::Combine([System.Environment]::GetFolderPath(''Desktop''), ''WinNetTray.lnk''), ' +
                '[System.IO.Path]::Combine([System.Environment]::GetFolderPath(''Startup''), ''WinNetTray.lnk''), ' +
                '[System.IO.Path]::Combine([System.Environment]::GetFolderPath(''Programs''), ''WinNetTray.lnk''), ' +
                '[System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), ''winnettray_error.log''), ' +
                '[System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), ''wnt_wizard_pkg.zip''), ' +
                '[System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), ''wnt_setup.ps1'')' +
            '); ' +
            'foreach ($p in $paths) { if (Test-Path $p) { Remove-Item $p -Recurse -Force -ErrorAction SilentlyContinue } }; ' +
            '$niPath = ''HKCU:\Control Panel\NotifyIconSettings''; ' +
            'if (Test-Path $niPath) { Get-ChildItem $niPath -ErrorAction SilentlyContinue | ForEach-Object { $ep = (Get-ItemProperty $_.PSPath -Name ExecutablePath -ErrorAction SilentlyContinue).ExecutablePath; if ($ep -and ($ep -match ''WinNetTray|net_tray'')) { Remove-Item $_.PSPath -Recurse -Force -ErrorAction SilentlyContinue } } }; ' +
            'Remove-Item ''HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\WinNetTray'' -Recurse -Force -ErrorAction SilentlyContinue' +
        '"',
        'cd /d "%TEMP%"',
        'rmdir /s /q "' + $target + '" >nul 2>&1',
        'exit /b 0'
    )
    [System.IO.File]::WriteAllLines($uninstBat, $uninstLines, [System.Text.Encoding]::UTF8)

    try {
        $uninstReg = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\WinNetTray'
        if (-not (Test-Path $uninstReg)) {
            New-Item -Path $uninstReg -Force | Out-Null
        }
        Set-ItemProperty -Path $uninstReg -Name 'DisplayName' -Value 'WinNetTray'
        Set-ItemProperty -Path $uninstReg -Name 'DisplayVersion' -Value '1.0.0'
        Set-ItemProperty -Path $uninstReg -Name 'Publisher' -Value 'Luca Tosi'
        Set-ItemProperty -Path $uninstReg -Name 'InstallLocation' -Value $target
        Set-ItemProperty -Path $uninstReg -Name 'UninstallString' -Value ('"' + $uninstBat + '"')
        Set-ItemProperty -Path $uninstReg -Name 'QuietUninstallString' -Value ('"' + $uninstBat + '"')
        Set-ItemProperty -Path $uninstReg -Name 'NoModify' -Value 1 -Type DWord
        Set-ItemProperty -Path $uninstReg -Name 'NoRepair' -Value 1 -Type DWord
    } catch {}

    $pBar.Value = 100
    $lblStatus.Text = "Installazione completata con successo!"
    $form.Refresh()

    Start-Sleep -Milliseconds 600

    $lblInstalledPath.Text = "Cartella di installazione: " + $target
    $script:finalLnkPath = $lnkPath
    $script:currentStep = 5
    & $fnUpdateView
}

$btnNext.Add_Click({
    if ($script:currentStep -eq 1) {
        $script:currentStep = 2
        & $fnUpdateView
    }
    elseif ($script:currentStep -eq 2) {
        $target = $txtPath.Text.Trim()
        if ([string]::IsNullOrWhiteSpace($target)) {
            [System.Windows.Forms.MessageBox]::Show("Inserisci un percorso di cartella valido!", "Attenzione", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Warning)
            return
        }
        $script:currentStep = 3
        & $fnUpdateView
    }
    elseif ($script:currentStep -eq 3) {
        & $fnPerformInstall
    }
    elseif ($script:currentStep -eq 5) {
        if ($chkRunNow.Checked -and (Test-Path $script:finalLnkPath)) {
            Start-Process $script:finalLnkPath
        }
        $form.Close()
    }
})

$btnBack.Add_Click({
    if ($script:currentStep -gt 1 -and $script:currentStep -lt 4) {
        $script:currentStep--
        & $fnUpdateView
    }
})

& $fnUpdateView
[void]$form.ShowDialog()
'''

    # Launcher batch (5 righe) che avvia la GUI ed esce immediatamente
    bat_header = (
        "@echo off\r\n"
        "setlocal\r\n"
        "chcp 65001 >nul\r\n"
        "set \"WNT_SELF_PATH=%~f0\"\r\n"
        "powershell -NoProfile -ExecutionPolicy Bypass -Command \"$me=$env:WNT_SELF_PATH;$bytes=[System.IO.File]::ReadAllBytes($me);$txt=[System.Text.Encoding]::UTF8.GetString($bytes);$p1=$txt.IndexOf('__PS_CODE_BELOW__');$p2=$txt.IndexOf('__PAYLOAD_BELOW__');if($p1 -ge 0 -and $p2 -gt $p1){$ps=$txt.Substring($p1+17,$p2-($p1+17));$tmp=[System.IO.Path]::Combine([System.IO.Path]::GetTempPath(),'wnt_setup.ps1');[System.IO.File]::WriteAllText($tmp,$ps,[System.Text.Encoding]::UTF8);& $tmp;Remove-Item $tmp -Force -ErrorAction SilentlyContinue}\"\r\n"
        "exit /b 0\r\n"
        "__PS_CODE_BELOW__\r\n"
    )

    with open(payload_zip_path, "rb") as fz:
        zip_bytes = fz.read()

    # Normalizza TUTTI i ritorni a capo in standard Windows CRLF (\r\n) per evitare disallineamenti di seek in CMD.EXE
    header_clean = bat_header.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")
    ps_clean = ps_wizard_code.strip().replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")
    payload_marker = "\r\n__PAYLOAD_BELOW__\r\n".encode("utf-8")

    with open(output_bat_path, "wb") as fb:
        fb.write(header_clean)
        fb.write(ps_clean)
        fb.write(payload_marker)
        fb.write(zip_bytes)

    print(f"[OK] File installer grafico generato con successo: {output_bat_path}")
    print(f"     Dimensione finale: {os.path.getsize(output_bat_path) / (1024*1024):.1f} MB")
    return True

if __name__ == "__main__":
    temp_dir = os.environ.get("TEMP", ".")
    default_zip = os.path.join(temp_dir, "winnettray_payload.zip")
    output_bat = "Installa_WinNetTray.bat"
    
    if len(sys.argv) > 1:
        default_zip = sys.argv[1]
    if len(sys.argv) > 2:
        output_bat = sys.argv[2]
        
    build_installer(default_zip, output_bat)
