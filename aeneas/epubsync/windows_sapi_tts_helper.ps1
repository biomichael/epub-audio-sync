param(
    [string]$VoiceCode,
    [string]$OutputPath,
    [string]$TextPath
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech

$text = [System.IO.File]::ReadAllText($TextPath, [System.Text.Encoding]::UTF8)
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer

if ($VoiceCode) {
    $voices = $synth.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo }
    $target = $voices | Where-Object {
        $_.Culture -and (
            $_.Culture.ThreeLetterISOLanguageName -eq $VoiceCode -or
            $_.Culture.TwoLetterISOLanguageName -eq $VoiceCode -or
            $_.Name -like "*$VoiceCode*"
        )
    } | Select-Object -First 1
    if ($target) {
        $synth.SelectVoice($target.Name)
    }
}

$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$synth.SetOutputToWaveFile($OutputPath, $format)
$synth.Speak($text)
$synth.Dispose()
