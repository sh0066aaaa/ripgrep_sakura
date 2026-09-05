' ============================================================
'  rgs.vbs - サクラエディタから ripgrep 検索ダイアログを開く WSH マクロ
'
'  ・カーソル位置の単語（または選択範囲）を検索文字列の初期値にする
'  ・編集中のファイルのフォルダをフォルダ欄の初期値にする
'  ・結果はサクラエディタの新しいタブに開く。F12 でタグジャンプ
'  ・非同期起動なので、ダイアログを出している間もサクラエディタは固まらない
' ============================================================

' 使う実装: "python" または "powershell"
Const IMPL = "python"

' このマクロの置き場所からリポジトリのルートを自動で求めるので、通常は変更不要。
' 求められなかったときだけ下の値を使う（必要なら書き換える）。
Const ROOT_FALLBACK = "C:\tools\ripgrep_sakura"

Dim root, key, f, folder, cmd, sh

root = RepoRoot()

' 検索文字列の初期値：選択範囲、なければカーソル位置の単語
key = Editor.ExpandParameter("$C")

' フォルダの初期値：編集中ファイルのフォルダ
f = Editor.ExpandParameter("$F")
folder = ""
If InStrRev(f, "\") > 0 Then folder = Left(f, InStrRev(f, "\") - 1)

If IMPL = "python" Then
    ' pythonw なのでコンソールが一瞬も出ない
    cmd = "pythonw """ & root & "\python\rgs.py"""
Else
    cmd = "powershell -NoProfile -Sta -WindowStyle Hidden -ExecutionPolicy Bypass -File """ & _
          root & "\powershell\rgs.ps1"""
End If
cmd = cmd & " -Dialog -Word """ & key & """ -Folder """ & folder & """"

Set sh = CreateObject("WScript.Shell")
sh.Run cmd, 1, False

' このマクロ (macros\rgs.vbs) の 1 つ上がリポジトリのルート
Function RepoRoot()
    Dim m, d
    m = Editor.ExpandParameter("$M")
    If InStrRev(m, "\") > 0 Then
        d = Left(m, InStrRev(m, "\") - 1)          ' ...\macros
        If InStrRev(d, "\") > 0 Then
            RepoRoot = Left(d, InStrRev(d, "\") - 1)
            Exit Function
        End If
    End If
    RepoRoot = ROOT_FALLBACK
End Function
