<#
.SYNOPSIS
  ripgrep の検索結果を「サクラエディタの Grep 結果」形式に変換する。
  結果ウィンドウで F12 (タグジャンプ) を押すと、その行のファイル・行・桁へ飛べる。

.EXAMPLE
  rgs TODO                 # 結果をファイルに書いてサクラエディタで開く
  rgs -i "foo bar" src
  rgs -Direct MainWindow   # 先頭ヒットのファイルを直接開く
  rgs -Stdout TODO         # 結果を UTF-8 で標準出力に流す (サクラエディタのマクロ用)
#>
# rg のオプション (-i, -w など) をそのまま素通しさせるため、
# パラメーターは宣言せず $args を自前で解釈する
param()

$RgArgs = @($args)
$Direct = $false
$Stdout = $false
$Dialog = $false
$DryRun = $false
$WordJp = $false
$InitWord   = ''
$InitFolder = ''

$switches = @('-Direct', '-Stdout', '-Dialog', '-DryRun', '-Word', '-Folder', '-WordJp')
while ($RgArgs.Count -gt 0 -and $switches -contains [string]$RgArgs[0]) {
    $sw = [string]$RgArgs[0]
    $take = 1
    switch ($sw) {
        '-Direct' { $Direct = $true }
        '-Stdout' { $Stdout = $true }
        '-Dialog' { $Dialog = $true }
        '-DryRun' { $DryRun = $true }
        '-WordJp' { $WordJp = $true }
        '-Word'   { $InitWord   = [string]$RgArgs[1]; $take = 2 }
        '-Folder' { $InitFolder = [string]$RgArgs[1]; $take = 2 }
    }
    if ($RgArgs.Count -le $take) { $RgArgs = @() }
    else { $RgArgs = @($RgArgs[$take..($RgArgs.Count - 1)]) }
}

# 標準出力へは常に UTF-8 のバイト列で書く
# (サクラエディタの ExecCommand に 0x80 を付けて受け取る前提)
function Write-Utf8Stdout {
    param([string[]]$Lines)
    $s = ($Lines -join "`r`n") + "`r`n"
    $b = [System.Text.Encoding]::UTF8.GetBytes($s)
    $o = [Console]::OpenStandardOutput()
    $o.Write($b, 0, $b.Length)
    $o.Flush()
}

function Write-Msg {
    param([string]$Text)
    if ($Stdout) { Write-Utf8Stdout @($Text) } else { Write-Host $Text }
}

if (-not $Dialog -and (-not $RgArgs -or $RgArgs.Count -eq 0)) {
    Write-Msg 'usage: rgs [-Dialog] [-Direct] [-Stdout] [-DryRun] [-WordJp] <rg の引数...>'
    exit 2
}

# --- サクラエディタの場所 -------------------------------------------------
$sakura = $env:SAKURA_EXE
if (-not $sakura -or -not (Test-Path $sakura)) {
    $sakura = @(
        (Join-Path ${env:ProgramFiles(x86)} 'sakura\sakura.exe')
        (Join-Path $env:ProgramFiles 'sakura\sakura.exe')
        (Join-Path $env:LOCALAPPDATA 'Programs\sakura\sakura.exe')
    ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
}
if (-not $sakura) {
    $c = Get-Command sakura.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { $sakura = $c.Source }
}
if (-not $sakura -and -not $Stdout -and -not $DryRun) {
    Write-Msg 'sakura.exe が見つかりません。環境変数 SAKURA_EXE にパスを設定してください。'
    exit 1
}

# --- ripgrep の場所 -------------------------------------------------------
$rg = $env:RG_EXE
if (-not $rg -or -not (Test-Path $rg)) {
    # rg.exe だけでなく rg.cmd / rg.bat 等も拾う
    $c = Get-Command rg -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { $rg = $c.Source } else { $rg = $null }
}
if (-not $rg) {
    # 最後の手段: VS Code 同梱の rg.exe
    # (フォルダ全体の再帰検索は遅いのでワイルドカードで直接叩く)
    $vs = Join-Path $env:LOCALAPPDATA 'Programs\Microsoft VS Code'
    $rg = @(
        (Join-Path $vs 'resources\app\node_modules\@vscode\ripgrep*\bin\rg.exe')
        (Join-Path $vs 'resources\app\node_modules\@vscode\ripgrep*\bin\win32-*\rg.exe')
        (Join-Path $vs '*\resources\app\node_modules\@vscode\ripgrep*\bin\win32-*\rg.exe')
    ) | ForEach-Object { Resolve-Path $_ -ErrorAction SilentlyContinue } |
        Select-Object -First 1 -ExpandProperty Path
}
if (-not $rg) {
    Write-Msg 'rg.exe が見つかりません。winget install BurntSushi.ripgrep.MSVC でインストールするか、環境変数 RG_EXE にパスを設定してください。'
    exit 1
}

# --- 検索ダイアログ ---------------------------------------------------------
# 設定は次回のためにここへ保存する
$cfgBase = $env:APPDATA
if (-not $cfgBase) { $cfgBase = $env:LOCALAPPDATA }
if (-not $cfgBase) { $cfgBase = $env:TEMP }
$cfgFile = Join-Path (Join-Path $cfgBase 'rgs') 'dialog.json'

function Read-RgsConfig {
    $def = @{
        Word = ''; Folder = ''; Files = ''
        Case = $false; Whole = $false; Regex = $false; Sub = $true; Hidden = $false
    }
    if (Test-Path $cfgFile) {
        try {
            $j = Get-Content $cfgFile -Raw -Encoding UTF8 | ConvertFrom-Json
            foreach ($k in @($def.Keys)) { if ($null -ne $j.$k) { $def[$k] = $j.$k } }
        } catch { }
    }
    return $def
}

function Save-RgsConfig {
    param($Cfg)
    try {
        $dir = Split-Path $cfgFile -Parent
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        ($Cfg | ConvertTo-Json) | Set-Content -Path $cfgFile -Encoding UTF8
    } catch { }
}

# rg の -w は Unicode の単語境界なので、漢字・かなも単語構成文字として扱われる。
# そのため "test" は "test用" にマッチしない。
# サクラエディタは文字種（英数字/ひらがな/カタカナ/漢字）の変わり目を区切りとみなすので、
# 検索語の先頭・末尾の文字種だけを禁止する PCRE2 の先読み・後読みで同じ挙動にする。
function Get-CharClassPattern {
    param([string]$Ch)
    if ($Ch -cmatch '[0-9A-Za-z_]') { return '0-9A-Za-z_' }
    if ($Ch -cmatch '[\u3041-\u309F]') { return '\x{3041}-\x{309F}' }
    if ($Ch -cmatch '[\u30A0-\u30FF\uFF66-\uFF9F]') { return '\x{30A0}-\x{30FF}\x{FF66}-\x{FF9F}' }
    if ($Ch -cmatch '[\u4E00-\u9FFF\u3005\u3400-\u4DBF]') { return '\x{3400}-\x{4DBF}\x{4E00}-\x{9FFF}\x{3005}' }
    return ''
}

function ConvertTo-JpWordPattern {
    param([string]$Text)
    if (-not $Text) { return $Text }
    $core = [regex]::Escape($Text)
    $pre  = Get-CharClassPattern $Text.Substring(0, 1)
    $post = Get-CharClassPattern $Text.Substring($Text.Length - 1, 1)
    if ($pre)  { $core = "(?<![$pre])" + $core }
    if ($post) { $core = $core + "(?![$post])" }
    return $core
}

# rg が PCRE2 付きでビルドされているか（一度だけ判定してキャッシュ）
$script:HasPcre2 = $null
function Test-Pcre2 {
    if ($null -ne $script:HasPcre2) { return $script:HasPcre2 }
    & $rg --pcre2-version 2>&1 | Out-Null
    $script:HasPcre2 = ($LASTEXITCODE -eq 0)
    return $script:HasPcre2
}

# ダイアログの設定内容 -> rg の引数
function ConvertTo-RgArgs {
    param($Cfg)
    $a = New-Object System.Collections.Generic.List[string]
    if ($Cfg.Case) { $a.Add('-s') } else { $a.Add('-i') }

    $pattern = [string]$Cfg.Word
    if ($Cfg.Whole -and -not $Cfg.Regex -and (Test-Pcre2)) {
        # サクラエディタ互換の単語単位（文字種の変わり目を区切りとみなす）
        $a.Add('-P')
        $pattern = ConvertTo-JpWordPattern $pattern
    } elseif ($Cfg.Whole) {
        $a.Add('-w')
        if (-not $Cfg.Regex) { $a.Add('-F') }
    } elseif (-not $Cfg.Regex) {
        $a.Add('-F')
    }
    if (-not $Cfg.Sub) { $a.Add('--max-depth=1') }
    if ($Cfg.Hidden) { $a.Add('--hidden') }
    foreach ($g in ([string]$Cfg.Files -split '[;,]')) {
        $g = $g.Trim()
        if ($g) { $a.Add('-g'); $a.Add($g) }
    }
    $a.Add('--')
    $a.Add($pattern)
    $a.Add([string]$Cfg.Folder)
    return $a.ToArray()
}

function Show-RgsDialog {
    param($Cfg)
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    [System.Windows.Forms.Application]::EnableVisualStyles()

    $f = New-Object System.Windows.Forms.Form
    $f.Text = 'rgs - ripgrep 検索'
    $f.FormBorderStyle = 'FixedDialog'
    $f.MaximizeBox = $false
    $f.MinimizeBox = $false
    $f.StartPosition = 'CenterScreen'
    $f.TopMost = $true
    $f.ClientSize = New-Object System.Drawing.Size(530, 262)
    $f.Font = New-Object System.Drawing.Font('Meiryo UI', 9)

    $mkLabel = {
        param($t, $x, $y)
        $l = New-Object System.Windows.Forms.Label
        $l.Text = $t
        $l.Location = New-Object System.Drawing.Point($x, $y)
        $l.AutoSize = $true
        $l
    }
    $mkCheck = {
        param($t, $x, $y, $on)
        $c = New-Object System.Windows.Forms.CheckBox
        $c.Text = $t
        $c.Location = New-Object System.Drawing.Point($x, $y)
        $c.AutoSize = $true
        $c.Checked = [bool]$on
        $c
    }

    $tWord = New-Object System.Windows.Forms.TextBox
    $tWord.Location = New-Object System.Drawing.Point(96, 14)
    $tWord.Size = New-Object System.Drawing.Size(418, 24)
    $tWord.Text = [string]$Cfg.Word

    $tFolder = New-Object System.Windows.Forms.TextBox
    $tFolder.Location = New-Object System.Drawing.Point(96, 46)
    $tFolder.Size = New-Object System.Drawing.Size(330, 24)
    $tFolder.Text = [string]$Cfg.Folder

    $bRef = New-Object System.Windows.Forms.Button
    $bRef.Text = '参照...'
    $bRef.Location = New-Object System.Drawing.Point(434, 45)
    $bRef.Size = New-Object System.Drawing.Size(80, 26)
    $bRef.Add_Click({
        $d = New-Object System.Windows.Forms.FolderBrowserDialog
        if ($tFolder.Text -and (Test-Path $tFolder.Text)) { $d.SelectedPath = $tFolder.Text }
        if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { $tFolder.Text = $d.SelectedPath }
    }.GetNewClosure())

    $tFiles = New-Object System.Windows.Forms.TextBox
    $tFiles.Location = New-Object System.Drawing.Point(96, 78)
    $tFiles.Size = New-Object System.Drawing.Size(418, 24)
    $tFiles.Text = [string]$Cfg.Files

    $lHint = & $mkLabel '例: *.c;*.h   空欄ならすべてのファイル' 96 106
    $lHint.ForeColor = [System.Drawing.Color]::Gray

    $cCase   = & $mkCheck '大文字小文字を区別する' 96 134 $Cfg.Case
    $cWhole  = & $mkCheck '単語単位で探す' 96 160 $Cfg.Whole
    $cHidden = & $mkCheck '隠しファイルも含める' 96 186 $Cfg.Hidden
    $cRegex  = & $mkCheck '正規表現' 310 134 $Cfg.Regex
    $cSub    = & $mkCheck 'サブフォルダも検索' 310 160 $Cfg.Sub

    $bOk = New-Object System.Windows.Forms.Button
    $bOk.Text = '検索'
    $bOk.Location = New-Object System.Drawing.Point(324, 220)
    $bOk.Size = New-Object System.Drawing.Size(90, 28)
    $bOk.DialogResult = [System.Windows.Forms.DialogResult]::OK

    $bNg = New-Object System.Windows.Forms.Button
    $bNg.Text = 'キャンセル'
    $bNg.Location = New-Object System.Drawing.Point(424, 220)
    $bNg.Size = New-Object System.Drawing.Size(90, 28)
    $bNg.DialogResult = [System.Windows.Forms.DialogResult]::Cancel

    $f.Controls.AddRange(@(
        (& $mkLabel '検索文字列' 14 17), $tWord,
        (& $mkLabel 'フォルダ' 14 49), $tFolder, $bRef,
        (& $mkLabel 'ファイル' 14 81), $tFiles, $lHint,
        $cCase, $cWhole, $cHidden, $cRegex, $cSub, $bOk, $bNg
    ))
    $f.AcceptButton = $bOk
    $f.CancelButton = $bNg

    # OK 時の入力チェック（フォルダが実在しなければ閉じない）
    $f.Add_FormClosing({
        param($sender, $e)
        if ($f.DialogResult -ne [System.Windows.Forms.DialogResult]::OK) { return }
        $tWord.Text = $tWord.Text.Trim()
        $tFolder.Text = $tFolder.Text.Trim()
        if (-not $tWord.Text) {
            [void][System.Windows.Forms.MessageBox]::Show('検索文字列を入力してください。', 'rgs')
            $e.Cancel = $true
            $tWord.Focus()
            return
        }
        if ($tFolder.Text -and -not (Test-Path -LiteralPath $tFolder.Text -PathType Container)) {
            [void][System.Windows.Forms.MessageBox]::Show('フォルダが見つかりません:' + [Environment]::NewLine + $tFolder.Text, 'rgs')
            $e.Cancel = $true
            $tFolder.Focus()
        }
    }.GetNewClosure())
    $f.Add_Shown({ $f.Activate(); $tWord.Focus(); $tWord.SelectAll() }.GetNewClosure())

    if ($f.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { return $null }

    return @{
        Word = $tWord.Text; Folder = $tFolder.Text; Files = $tFiles.Text
        Case = $cCase.Checked; Whole = $cWhole.Checked; Regex = $cRegex.Checked
        Sub = $cSub.Checked; Hidden = $cHidden.Checked
    }
}

if ($Dialog) {
    $cfg = Read-RgsConfig
    if ($InitWord) { $cfg.Word = $InitWord }
    if ($InitFolder) { $cfg.Folder = $InitFolder }
    if (-not $cfg.Folder) { $cfg.Folder = (Get-Location).ProviderPath }

    $res = Show-RgsDialog $cfg
    if ($null -eq $res -or -not $res.Word) { exit 0 }

    Save-RgsConfig $res
    if ($res.Folder -and (Test-Path -LiteralPath $res.Folder -PathType Container)) { Set-Location -LiteralPath $res.Folder }
    $RgArgs = ConvertTo-RgArgs $res
}

# --- rg の既定オプション ---------------------------------------------------
# RIPGREP_CONFIG_PATH が未設定なら、このスクリプトと同じ場所の rgrc を既定にする
if (-not $env:RIPGREP_CONFIG_PATH) {
    # スクリプトと同じ場所、無ければリポジトリ直下の rgrc を使う
    $defaultRc = @(
        (Join-Path $PSScriptRoot 'rgrc')
        (Join-Path (Split-Path $PSScriptRoot -Parent) 'rgrc')
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($defaultRc) { $env:RIPGREP_CONFIG_PATH = $defaultRc }
}

# --- 検索パスの補完 ---------------------------------------------------------
# rg はパス未指定かつ標準入力がパイプだと「標準入力」を検索して固まる。
# サクラエディタの ExecCommand から呼ぶとこの状態になるため、
# パスが指定されていなければ明示的に ./ を足す。
$valueOpts = @(
    '-e', '-g', '-t', '-T', '-m', '-A', '-B', '-C', '-M', '-j', '-f', '-r', '-E',
    '--glob', '--iglob', '--type', '--type-not', '--regexp', '--replace', '--file',
    '--max-count', '--max-depth', '--max-columns', '--encoding', '--pre', '--sort',
    '--sortr', '--threads', '--ignore-file', '--context', '--before-context', '--after-context'
)
# -WordJp: サクラエディタ互換の単語単位検索に変換する
if ($WordJp) {
    $idx = -1
    $dd  = $false
    for ($i = 0; $i -lt $RgArgs.Count; $i++) {
        $a = [string]$RgArgs[$i]
        if (-not $dd -and $a -eq '--') { $dd = $true; continue }
        if (-not $dd -and $a.StartsWith('-') -and $a.Length -gt 1) {
            if ($valueOpts -ccontains $a) { $i++ }
            continue
        }
        $idx = $i
        break
    }
    if ($idx -ge 0 -and (Test-Pcre2)) {
        $RgArgs[$idx] = ConvertTo-JpWordPattern ([string]$RgArgs[$idx])
        $RgArgs = @('-P') + @($RgArgs | Where-Object { $_ -ne '-F' -and $_ -ne '-w' })
    } elseif ($idx -ge 0) {
        $RgArgs = @('-w') + @($RgArgs)
    }
}

$origArgs   = @($RgArgs)
$positional = 0
for ($i = 0; $i -lt $RgArgs.Count; $i++) {
    $a = [string]$RgArgs[$i]
    if ($a -eq '--') { continue }
    if ($a.StartsWith('-') -and $a.Length -gt 1) {
        if ($valueOpts -ccontains $a) { $i++ }
        continue
    }
    $positional++
}
if ($positional -le 1) { $RgArgs = @($RgArgs) + './' }

if ($DryRun) {
    Write-Msg ('rg --vimgrep --no-heading --color never ' + ($RgArgs -join ' '))
    Write-Msg ('cwd: ' + (Get-Location).ProviderPath)
    exit 0
}

# --- rg 実行 (出力は UTF-8 なのでコンソールのエンコーディングを合わせる) ---
$prevEnc = [Console]::OutputEncoding
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $lines = & $rg --vimgrep --no-heading --color never @RgArgs 2>&1
    $rc = $LASTEXITCODE
} finally {
    [Console]::OutputEncoding = $prevEnc
}

if ($rc -eq 1) { Write-Msg '該当なし'; exit 1 }
if ($rc -gt 1) { Write-Msg (($lines | ForEach-Object { [string]$_ }) -join "`r`n"); exit $rc }

# --- file:line:col:text  ->  file(line,col): text --------------------------
$root = (Get-Location).ProviderPath
$utf8 = [System.Text.Encoding]::UTF8
$hits = New-Object System.Collections.Generic.List[object]

foreach ($line in $lines) {
    $m = [regex]::Match([string]$line, '^(.*):(\d+):(\d+):(.*)$')
    if (-not $m.Success) { continue }

    $path    = $m.Groups[1].Value
    $lno     = [int]$m.Groups[2].Value
    $byteCol = [int]$m.Groups[3].Value
    $text    = $m.Groups[4].Value

    if (-not [System.IO.Path]::IsPathRooted($path)) {
        $path = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($root, $path))
    }
    $path = $path.Replace('/', '\')

    # rg の桁はバイト単位。サクラエディタは文字単位なので変換する
    $b   = $utf8.GetBytes($text)
    $n   = [Math]::Max(0, [Math]::Min($byteCol - 1, $b.Length))
    $col = $utf8.GetString($b, 0, $n).Length + 1

    $hits.Add([pscustomobject]@{ Path = $path; Line = $lno; Col = $col; Text = $text })
}

if ($hits.Count -eq 0) { Write-Msg '該当なし'; exit 1 }

# --- 先頭ヒットを直接開く --------------------------------------------------
if ($Direct) {
    $h = $hits[0]
    & $sakura "-Y=$($h.Line)" "-X=$($h.Col)" -- $h.Path
    exit 0
}

# --- 出力の組み立て --------------------------------------------------------
$header = @(
    ('□検索条件  ' + ($origArgs -join ' '))
    ('□フォルダ  ' + $root)
)
if ($env:RIPGREP_CONFIG_PATH) { $header += ('□既定設定  ' + $env:RIPGREP_CONFIG_PATH) }
$body   = $hits | ForEach-Object { '{0}({1},{2}): {3}' -f $_.Path, $_.Line, $_.Col, $_.Text }
$footer = @('', ('該当 {0} 件' -f $hits.Count))

# --- 標準出力に流す (サクラエディタのアウトプットウィンドウ用) ---------------
if ($Stdout) {
    Write-Utf8Stdout ($header + $body + $footer)
    exit 0
}

# --- Grep 結果ファイルを作ってサクラエディタで開く --------------------------
$outDir = Join-Path $env:TEMP 'rgs'
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir | Out-Null }
$outFile = Join-Path $outDir ('rg_{0}.grepout.txt' -f (Get-Date -Format 'yyyyMMdd_HHmmss_fff'))

# BOM 付き UTF-8 で書けばサクラエディタが確実に UTF-8 と判定する
[System.IO.File]::WriteAllLines($outFile, ($header + $body + $footer), (New-Object System.Text.UTF8Encoding($true)))

# -R = ビューモード / -Y = 先頭ヒット行にカーソル
& $sakura -R "-Y=$($header.Count + 1)" -- $outFile
Write-Host ('該当 {0} 件 -> {1}' -f $hits.Count, $outFile)
