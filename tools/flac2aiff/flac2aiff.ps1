# FLAC -> AIFF, bit-perfect. Windows.
#
# Launched by "Converter FLAC para AIFF.bat" — run that, not this file.
#
# AIFF is uncompressed big-endian PCM and FLAC is compressed PCM, so a correct
# conversion changes the container and nothing else: same samples, same rate,
# same bit depth. "Maximum quality" is not a knob to turn up here, it is the
# absence of everything that would degrade the audio:
#
#   * the PCM codec matches the source bit depth. Writing a 24-bit master as
#     pcm_s16be silently discards the low 8 bits, which is the usual way this
#     conversion goes wrong.
#   * no -ar, so the sample rate is never resampled.
#   * no -ac, so channels are never mixed.
#   * no dither, no normalisation, no filters.
#
# Every file is verified afterwards: source and result are both decoded to
# identical raw PCM and hashed, and a mismatch deletes the output rather than
# leaving a quietly damaged file behind.
#
# PowerShell rather than a .bat because accented filenames are certain here and
# batch mangles them.

param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Targets)

$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Write-Step($msg, $colour = 'Gray') { Write-Host $msg -ForegroundColor $colour }

Write-Host ""
Write-Host "FLAC -> AIFF" -ForegroundColor White -NoNewline
Write-Host "  (sem perda, verificado arquivo por arquivo)" -ForegroundColor DarkGray
Write-Host ""

# ── ffmpeg ───────────────────────────────────────────────────
# Also look beside this script, so dropping ffmpeg.exe in the same folder is a
# valid way to run this without installing anything.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
foreach ($cand in @((Join-Path $here 'ffmpeg.exe'), (Join-Path $here 'bin\ffmpeg.exe'))) {
    if (Test-Path $cand) { $env:PATH = "$(Split-Path -Parent $cand);$env:PATH"; break }
}

$ffmpeg  = Get-Command ffmpeg  -ErrorAction SilentlyContinue
$ffprobe = Get-Command ffprobe -ErrorAction SilentlyContinue

if (-not $ffmpeg -or -not $ffprobe) {
    Write-Step "ffmpeg nao encontrado." Red
    Write-Host ""
    Write-Host "Instale de uma destas formas e rode de novo:"
    Write-Host ""
    Write-Host "  1. No Prompt de Comando ou PowerShell:" -ForegroundColor White
    Write-Host "     winget install Gyan.FFmpeg"
    Write-Host "     (feche e abra a janela depois de instalar)"
    Write-Host ""
    Write-Host "  2. Ou baixe em https://www.gyan.dev/ffmpeg/builds/ (release essentials),"
    Write-Host "     descompacte, e copie ffmpeg.exe e ffprobe.exe para a mesma"
    Write-Host "     pasta deste script."
    Write-Host ""
    Read-Host "Enter para fechar"
    exit 1
}

# ── What to convert ──────────────────────────────────────────
if (-not $Targets -or $Targets.Count -eq 0) {
    Write-Host "Arraste a pasta (ou os arquivos .flac) para cima do arquivo .bat,"
    Write-Host "ou cole o caminho da pasta aqui:"
    Write-Host ""
    $typed = Read-Host "Pasta"
    if ([string]::IsNullOrWhiteSpace($typed)) { Write-Host "Nada informado."; Read-Host "Enter para fechar"; exit 0 }
    $Targets = @($typed.Trim('"'))
}

$files = New-Object System.Collections.Generic.List[string]
foreach ($t in $Targets) {
    $t = $t.Trim('"')
    if (Test-Path -LiteralPath $t -PathType Container) {
        Get-ChildItem -LiteralPath $t -Recurse -File -Filter *.flac -ErrorAction SilentlyContinue |
            ForEach-Object { $files.Add($_.FullName) }
    } elseif (Test-Path -LiteralPath $t -PathType Leaf) {
        if ([IO.Path]::GetExtension($t) -ieq '.flac') { $files.Add((Resolve-Path -LiteralPath $t).Path) }
    }
}

if ($files.Count -eq 0) { Write-Step "Nenhum arquivo .flac encontrado." Yellow; Read-Host "Enter para fechar"; exit 0 }

Write-Host "$($files.Count) arquivo(s) para converter."
Write-Host ""

# Raw PCM at a fixed width, so the comparison is sensitive to bit depth. A hash
# of the decoded stream in its own format would still match after the low bits
# were discarded, which would make the check worthless.
function Get-AudioHash([string]$path) {
    (& ffmpeg -v error -i $path -map 0:a -c:a pcm_s32le -f hash -hash md5 - 2>$null) | Select-Object -First 1
}

$ok = 0; $failed = 0; $skipped = 0

foreach ($src in $files) {
    $out  = [IO.Path]::ChangeExtension($src, '.aiff')
    $name = Split-Path -Leaf $src

    if (Test-Path -LiteralPath $out) {
        Write-Step "pulado (ja existe): $name" DarkGray
        $skipped++; continue
    }

    $depth = (& ffprobe -v error -select_streams a:0 -show_entries stream=bits_per_raw_sample -of csv=p=0 $src 2>$null | Select-Object -First 1)
    $fmt   = (& ffprobe -v error -select_streams a:0 -show_entries stream=sample_fmt          -of csv=p=0 $src 2>$null | Select-Object -First 1)

    # bits_per_raw_sample is the truth for FLAC; sample_fmt only describes how
    # the decoder hands samples over (24-bit arrives as s32).
    switch ($depth) {
        '8'     { $codec = 'pcm_s8' }
        '16'    { $codec = 'pcm_s16be' }
        '24'    { $codec = 'pcm_s24be' }
        '32'    { $codec = 'pcm_s32be' }
        default {
            if     ($fmt -like 's16*') { $codec = 'pcm_s16be' }
            elseif ($fmt -like 's32*') { $codec = 'pcm_s32be' }
            else                       { $codec = 'pcm_s24be' }  # widen, never truncate
        }
    }

    $shownDepth = if ([string]::IsNullOrWhiteSpace($depth)) { '?' } else { $depth }
    Write-Host ("convertendo ({0}-bit): {1} ... " -f $shownDepth, $name) -NoNewline

    # -map 0 keeps cover art, -c:v copy passes it through untouched. Tags are
    # written as ID3, which is what Rekordbox and Serato read from AIFF.
    & ffmpeg -v error -y -i $src -map 0 -c:a $codec -c:v copy -map_metadata 0 -write_id3v2 1 $out 2>$null
    if ($LASTEXITCODE -ne 0) {
        # Some FLACs carry artwork the AIFF muxer refuses. The audio matters more.
        & ffmpeg -v error -y -i $src -map 0:a -c:a $codec -map_metadata 0 -write_id3v2 1 $out 2>$null
    }

    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $out)) {
        Write-Step "erro" Red
        if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Force }
        $failed++; continue
    }

    if ((Get-AudioHash $src) -eq (Get-AudioHash $out)) {
        Write-Step "ok, identico" Green
        $ok++
    } else {
        Write-Step "AUDIO DIFERENTE - arquivo removido" Red
        Remove-Item -LiteralPath $out -Force
        $failed++
    }
}

Write-Host ""
Write-Host "Pronto. " -NoNewline -ForegroundColor White
Write-Host "$ok convertido(s)" -NoNewline -ForegroundColor Green
Write-Host ", $skipped pulado(s), " -NoNewline
Write-Host "$failed com erro" -ForegroundColor Red
Write-Host "Os .flac originais nao foram tocados." -ForegroundColor DarkGray
Write-Host ""
Read-Host "Enter para fechar"
