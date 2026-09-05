' ============================================================
'  rgs_quick.vbs - カーソル位置の単語をそのまま ripgrep で検索し、
'                  結果をアウトプットウィンドウに出力する WSH マクロ
'
'  結果の行で F12 (タグジャンプ) を押すとその箇所へ飛べる
' ============================================================

' 使う実装: "python" または "powershell"
Const IMPL = "python"

' このマクロの置き場所からリポジトリのルートを自動で求めるので、通常は変更不要。
' 求められなかったときだけ下の値を使う（必要なら書き換える）。
Const ROOT_FALLBACK = "C:\tools\ripgrep_sakura"

Dim root, key, f, folder, cmd, opt

root = RepoRoot()

key = Editor.ExpandParameter("$C")
key = InputBox("ripgrep で検索する文字列", "rgs", key)

If Len(key) > 0 Then

    f = Editor.ExpandParameter("$F")
    folder = ""
    If InStrRev(f, "\") > 0 Then folder = Left(f, InStrRev(f, "\") - 1)

    If IMPL = "python" Then
        ' 標準出力を取り込むので pythonw ではなく python を使うこと
        cmd = "python """ & root & "\python\rgs.py"""
    Else
        cmd = "powershell -NoProfile -ExecutionPolicy Bypass -File """ & _
              root & "\powershell\rgs.ps1"""
    End If
    cmd = cmd & " -Stdout -- """ & key & """"

    ' 0x001 標準出力を得る
    ' 0x040 ヘッダー・フッター情報を出力しない
    ' 0x080 標準出力を UTF-8 で受け取る（文字化け防止）
    ' 0x200 カレントディレクトリ（第3引数）を有効にする
    opt = &H1 + &H40 + &H80
    If Len(folder) > 0 Then
        opt = opt + &H200
        Editor.ExecCommand cmd, opt, folder
    Else
        Editor.ExecCommand cmd, opt, ""
    End If

End If

Function RepoRoot()
    Dim m, d
    m = Editor.ExpandParameter("$M")
    If InStrRev(m, "\") > 0 Then
        d = Left(m, InStrRev(m, "\") - 1)
        If InStrRev(d, "\") > 0 Then
            RepoRoot = Left(d, InStrRev(d, "\") - 1)
            Exit Function
        End If
    End If
    RepoRoot = ROOT_FALLBACK
End Function
