# Python 実装

仕様は [../docs/spec.md](../docs/spec.md)。

## 必要なもの

- Python 3.8 以降（`tkinter` を使うので標準の Windows インストーラで入るもの）
- ripgrep

## 実行

```
python rgs.py TODO
rgs.cmd -Dialog          # PATH に通してあれば rgs -Dialog
```

ダイアログをコンソールなしで出したいときは `pythonw rgs.py -Dialog`。
ただし `-Stdout` は標準出力を使うので `pythonw` ではなく `python` を使うこと。

## テスト

```
python test_rgs.py
```

桁変換・単語境界パターン・引数解析・ダイアログ設定の変換を検証する。

## PowerShell 版との違い

- 起動が速い（実測でおよそ 1/4）
- 実行ポリシーの影響を受けない
- ダイアログは WinForms ではなく tkinter（ttk）

出力は PowerShell 版と 1 バイトも違わないことを確認済み。
