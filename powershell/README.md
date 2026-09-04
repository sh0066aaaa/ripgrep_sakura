# PowerShell 実装

仕様は [../docs/spec.md](../docs/spec.md)。Python 版と同じ出力を返す。

## 実行

```
rgs.cmd TODO
rgs.cmd -Dialog
```

`rgs.cmd` は `-ExecutionPolicy Bypass` 付きで `rgs.ps1` を起動する。

## rgs.ps1 を直接叩きたい場合

実行ポリシーが既定の `Restricted` のままだと弾かれる。一度だけ:

```
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

そのうえで `$PROFILE` に登録しておくと短く書ける:

```
function rgs { & C:\work\ripgrep_sakura\powershell\rgs.ps1 @args }
```

## 注意

`.cmd` は cmd.exe が cp932 で読むため、`rgs.cmd` には**非 ASCII 文字を入れないこと**。
日本語コメントを入れると、化けた文字列がコマンドとして実行されようとして失敗する。
