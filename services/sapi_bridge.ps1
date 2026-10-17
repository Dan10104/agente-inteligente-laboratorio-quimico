# Puente local. Recibe únicamente datos JSON; COM permanece en este hilo STA.
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$script:voice = $null
$script:active = $null
$fileStream = $null
$initialized = $false

function Send-Event($Name, $Payload) {
    $record = @{event = $Name; payload = $Payload} | ConvertTo-Json -Compress -Depth 8
    [Console]::Out.WriteLine($record)
    [Console]::Out.Flush()
}

function Stop-Reading($Message) {
    if ($null -ne $script:voice -and $null -ne $script:active) {
        [void]$script:voice.Speak('', 19)
    }
    if ($null -ne $script:active) {
        Send-Event 'stopped' @{request_id = $script:active.request_id; label = $script:active.label; message = $Message}
        $script:active = $null
    }
}

try {
    # Console.ReadLineAsync puede bloquear en .NET Framework; el lector dedicado
    # permite atender stop/close mientras SAPI habla de forma asíncrona.
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Concurrent;
using System.Threading;
public static class LabVoiceInput {
    public static readonly ConcurrentQueue<string> Lines = new ConcurrentQueue<string>();
    public static volatile bool Ended;
    public static void Start() {
        var reader = new Thread(() => {
            try {
                string line;
                while ((line = Console.ReadLine()) != null) Lines.Enqueue(line);
            } finally { Ended = true; }
        });
        reader.IsBackground = true;
        reader.Start();
    }
}
'@
    $script:voice = New-Object -ComObject SAPI.SpVoice
    $tokens = $script:voice.GetVoices()
    $voices = @()
    $byId = @{}
    $selectedId = $null
    $spanishScore = 0
    $defaultId = $script:voice.Voice.Id
    for ($index = 0; $index -lt $tokens.Count; $index++) {
        $token = $tokens.Item($index)
        $language = ''
        $spanish = $false
        foreach ($lcidText in ($token.GetAttribute('Language') -split ';')) {
            try {
                $culture = [System.Globalization.CultureInfo]::GetCultureInfo([Convert]::ToInt32($lcidText, 16))
                if (-not $language) { $language = $culture.Name }
                if ($culture.TwoLetterISOLanguageName -eq 'es') { $spanish = $true; $language = $culture.Name; break }
            } catch { }
        }
        $entry = @{id = [string]$token.Id; name = [string]$token.GetDescription(); language = $language; spanish = [bool]$spanish}
        $voices += $entry
        $byId[$entry.id] = $token
        $score = 0
        if ($spanish) { $score = 1 }
        if ($language -eq 'es-MX') { $score = 2 }
        if ($language -eq 'es-ES') { $score = 3 }
        if ($score -gt $spanishScore) { $selectedId = $entry.id; $spanishScore = $score }
    }
    $warning = ''
    if ($voices.Count -eq 0) {
        Send-Event 'ready' @{voices = @(); selected_id = $null; warning = 'No hay voces SAPI instaladas en Windows.'}
        exit 0
    }
    if (-not $selectedId) {
        $selectedId = $defaultId
        if (-not $selectedId -or -not $byId.ContainsKey($selectedId)) { $selectedId = $voices[0].id }
        $warning = 'No hay una voz SAPI en español instalada. Se utilizará una voz de otro idioma.'
    }
    $script:voice.Voice = $byId[$selectedId]
    $script:voice.Rate = 0
    $script:voice.Volume = 100
    if ($env:LAB_VOICE_OUTPUT_PATH) {
        $fileStream = New-Object -ComObject SAPI.SpFileStream
        # SSFMCreateForWrite = 3. Ruta recibida como dato, nunca como comando.
        $fileStream.Open([string]$env:LAB_VOICE_OUTPUT_PATH, 3, $false)
        $script:voice.AudioOutputStream = $fileStream
    }
    Send-Event 'ready' @{voices = @($voices); selected_id = $selectedId; warning = $warning}
    $initialized = $true
    [LabVoiceInput]::Start()
    $closing = $false
    while (-not $closing) {
        $line = $null
        while ([LabVoiceInput]::Lines.TryDequeue([ref]$line)) {
            $command = $null
            try {
                $command = $line | ConvertFrom-Json
                switch ($command.command) {
                    'configure' {
                        if ($command.voice_id) {
                            if (-not $byId.ContainsKey([string]$command.voice_id)) { throw 'La voz seleccionada ya no está disponible.' }
                            $script:voice.Voice = $byId[[string]$command.voice_id]
                        }
                        $script:voice.Rate = [Math]::Max(-10, [Math]::Min(10, [int]$command.rate))
                        $script:voice.Volume = [Math]::Max(0, [Math]::Min(100, [int]$command.volume))
                    }
                    'speak' {
                        Stop-Reading 'Lectura reemplazada.'
                        if ([string]::IsNullOrWhiteSpace([string]$command.text)) { throw 'No hay texto para leer.' }
                        # 1 = async; 2 = purge; 16 = texto literal, nunca XML/archivo.
                        [void]$script:voice.Speak([string]$command.text, 19)
                        $script:active = @{request_id = [long]$command.request_id; label = [string]$command.label}
                        Send-Event 'started' $script:active
                    }
                    'stop' { Stop-Reading 'Lectura detenida.' }
                    'close' { $closing = $true; Stop-Reading 'Servicio de voz cerrado.' }
                    default { throw 'Comando de voz desconocido.' }
                }
            } catch {
                $requestId = $null
                $label = ''
                if ($null -ne $command -and $null -ne $command.request_id) {
                    $requestId = [long]$command.request_id; $label = [string]$command.label
                }
                Send-Event 'error' @{request_id = $requestId; label = $label; message = $_.Exception.Message}
            }
            if ($closing) { break }
        }
        if ($null -ne $script:active -and $script:voice.WaitUntilDone(0)) {
            $status = $script:voice.Status
            if ($status.LastHResult -ne 0) {
                Send-Event 'error' @{request_id = $script:active.request_id; label = $script:active.label; message = ('El dispositivo de audio rechazó la lectura (SAPI: ' + $status.LastHResult + ').')}
            } else {
                Send-Event 'finished' $script:active
            }
            $script:active = $null
        }
        if ([LabVoiceInput]::Ended -and [LabVoiceInput]::Lines.IsEmpty) { $closing = $true }
        if (-not $closing) { Start-Sleep -Milliseconds 25 }
    }
} catch {
    if (-not $initialized) {
        Send-Event 'ready' @{voices = @(); selected_id = $null; warning = ('No se pudo iniciar el motor de voz: ' + $_.Exception.Message)}
    }
    Send-Event 'error' @{request_id = $null; label = ''; message = $_.Exception.Message; fatal = $true}
} finally {
    if ($null -ne $script:voice) {
        try { Stop-Reading 'Servicio de voz cerrado.' } catch { }
        if ($null -ne $fileStream) {
            try { $fileStream.Close() } catch { }
            [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($fileStream)
        }
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($script:voice)
    }
}
exit 0
