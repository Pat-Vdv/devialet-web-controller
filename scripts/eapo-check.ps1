# Equalizer APO - vérification en LECTURE SEULE (V2.4.3-A2)
# N'écrit rien : ni registre, ni fichier, ni périphérique.
# Usage (PowerShell, sans droits administrateur) :
#   powershell -NoProfile -File "\\wsl.localhost\<distribution>\home\<utilisateur>\<projet>\scripts\eapo-check.ps1" -DeviceName "<nom Bluetooth de la paire>"
#   ou -EndpointGuid "{...}" ; valeurs par défaut : variables DEVIALET_EAPO_DEVICE / DEVIALET_EAPO_ENDPOINT.
#   Sans paramètre, le script liste les sorties audio et leur GUID.
param(
    [string]$EndpointGuid = $env:DEVIALET_EAPO_ENDPOINT,
    [string]$DeviceName = $env:DEVIALET_EAPO_DEVICE
)

$ErrorActionPreference = "SilentlyContinue"
$Render = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render"
$NameKey = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"         # nom Bluetooth du périphérique
$DescKey = "{a45c254e-df1c-4efd-8020-67d146a850e0},2"         # connecteur (« Haut-parleurs »)
if (-not $EndpointGuid) {
    $found = @(Get-ChildItem $Render | ForEach-Object {
        $p = Get-ItemProperty "$($_.PSPath)\Properties"
        [pscustomobject]@{ Guid = $_.PSChildName; Name = $p.$NameKey; Desc = $p.$DescKey }
    })
    $match = @($found | Where-Object { $DeviceName -and $_.Name -eq $DeviceName })
    if ($match.Count -ne 1) {
        Write-Host "Choisissez la sortie : -DeviceName <nom> ou -EndpointGuid <GUID>. Sorties trouvées :"
        $found | ForEach-Object { Write-Host ("  {0}  {1} ({2})" -f $_.Guid, $_.Desc, $_.Name) }
        exit 1
    }
    $EndpointGuid = $match[0].Guid
}
$Guid = $EndpointGuid
$Ep   = "$Render\$Guid"
$FxKey = "{d04e05a6-594b-4fb6-a80d-01af5eed7d1d}"            # PKEY_FX_*
$Slots = [ordered]@{ "SFX (flux)" = 5; "MFX (mode)" = 6; "EFX (sortie)" = 7; "LFX (ancien)" = 1; "GFX (ancien)" = 2 }

function Resolve-Clsid($clsid) {
    if (-not $clsid) { return "-" }
    $name = (Get-Item "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Classes\CLSID\$clsid").GetValue("")
    $dll  = (Get-ItemProperty "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Classes\CLSID\$clsid\InprocServer32")."(default)"
    return "$clsid  $name  [$dll]"
}

Write-Host "=== Sortie $Guid ==="
$props = Get-ItemProperty "$Ep\Properties"
if (-not $props) { Write-Host "Introuvable : le GUID a changé (nouvel appairage ?)"; exit 1 }
Write-Host ("Nom      : {0} ({1})" -f $props.$DescKey, $props.$NameKey)
$state = (Get-ItemProperty $Ep).DeviceState
Write-Host ("État     : {0}  (1 = active)" -f $state)
$sysfx = (Get-Item "$Ep\Properties").GetValueNames() -contains "{1da5d803-d492-4edd-8c23-e0c0ffee7f0e},5"
if ($sysfx) { $v = $props."{1da5d803-d492-4edd-8c23-e0c0ffee7f0e},5"; Write-Host "Améliorations audio : désactivation = $v (1 = désactivées, l'APO ne serait pas chargé)" }
else { Write-Host "Améliorations audio : actives (aucune désactivation enregistrée)" }

Write-Host "`n=== Effets audio (APO) de la sortie ==="
$fx = Get-ItemProperty "$Ep\FxProperties"
$eapo = $false
foreach ($slot in $Slots.GetEnumerator()) {
    $clsid = $fx."$FxKey,$($slot.Value)"
    $line = Resolve-Clsid $clsid
    if ($line -match "EqualizerAPO") { $eapo = $true }
    Write-Host ("{0,-14}: {1}" -f $slot.Key, $line)
}

Write-Host "`n=== Equalizer APO ==="
$dir = "C:\Program Files\EqualizerAPO"
Write-Host ("Installé : {0}" -f (Test-Path "$dir\EqualizerAPO.dll"))
Write-Host ("Chargé sur la sortie (registre) : {0}" -f $eapo)
if (Test-Path "$dir\config\config.txt") {
    Write-Host "`n--- config\config.txt ---"
    Get-Content "$dir\config\config.txt" | ForEach-Object { Write-Host "  $_" }
}
Write-Host "`nRéférence AVANT installation (2026-10-28) : SFX = WM LFX APO, MFX = WM GFX APO, EFX = -, [WMALFXGFXDSP.dll]"
