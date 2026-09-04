# rgs — ripgrep の結果をサクラエディタで開く

ripgrep で検索し、その結果を **サクラエディタの Grep 結果形式**
（`ファイル名(行,桁): 内容`）に変換してサクラエディタで開く。
結果行で **F12（タグジャンプ）** を押すとその箇所へ飛べる。`Shift+F12` で戻る。

Python 版と PowerShell 版があり、**どちらも同じ仕様**（[docs/spec.md](docs/spec.md)）で
同じ出力を返す。通常は Python 版を使えばよい。

## 構成

```
ripgrep_sakura/
├─ rgrc                  ripgrep の既定オプション（両実装で共有）
├─ docs/
│  ├─ spec.md            仕様（実装非依存）
│  └─ sakura-notes.md    サクラエディタ側の調査メモ
├─ python/
│  ├─ rgs.py             Python 実装（推奨）
│  ├─ rgs.cmd            cmd.exe / Win+R 用ラッパー
│  └─ test_rgs.py        単体テスト
├─ powershell/
│  ├─ rgs.ps1            PowerShell 実装
│  └─ rgs.cmd            cmd.exe / Win+R 用ラッパー
└─ macros/
   ├─ rgs.vbs            サクラエディタ用マクロ（検索ダイアログを開く）
   └─ rgs_quick.vbs      サクラエディタ用マクロ（即検索してアウトプットウィンドウへ）
```

## 準備

1. ripgrep をインストール（PATH に `rg` が無い場合）

```
winget install BurntSushi.ripgrep.MSVC
```

2. `python`（または `powershell`）フォルダを PATH に追加すると、
   どこからでも `rgs` で呼べる

## 使い方

```
rgs TODO                      # カレント配下を検索
rgs -i "foo bar" src          # rg のオプションはそのまま渡せる
rgs -g "*.cs" -w Hoge
rgs -Dialog                   # 検索ダイアログを開く
rgs -WordJp test              # サクラエディタと同じ単語単位で検索
rgs -Direct MainWindow        # 先頭ヒットのファイルを直接その行で開く
rgs -DryRun -i -w foo         # rg に渡す引数を表示するだけ（確認用）
```

制御スイッチ（`-Dialog` `-Direct` `-Stdout` `-DryRun` `-WordJp`）は**先頭**に置く。
それ以外の引数はすべて rg にそのまま渡る。

| キー | 動作 |
|---|---|
| `F12` | タグジャンプ（カーソル行のファイル・行・桁へ移動） |
| `Shift+F12` | タグジャンプバック（結果一覧に戻る） |

## 検索ダイアログ

`rgs -Dialog` で、サクラエディタの Grep ダイアログ相当の画面が出る。

| 項目 | 渡される rg オプション |
|---|---|
| 検索文字列 | パターン |
| フォルダ（参照ボタン付き） | 検索パス |
| ファイル（`*.c;*.h`、空欄で全部） | `-g *.c -g *.h` |
| 大文字小文字を区別する | ON `-s` / OFF `-i` |
| 単語単位で探す | サクラエディタ互換の文字種境界（下記） |
| 正規表現 | OFF のとき `-F` |
| サブフォルダも検索 | OFF のとき `--max-depth=1` |
| 隠しファイルも含める | `--hidden` |

入力内容は `%APPDATA%\rgs\dialog.json` に保存され、次回復元される。
この設定ファイルは Python 版と PowerShell 版で共有される。

## サクラエディタから起動する（マクロ）

### 登録手順

1. **設定 → 共通設定 → マクロ**
   - 「マクロ一覧」のフォルダに `C:\work\ripgrep_sakura\macros` を指定
   - 空き番号に 名前 `rgs` / File `rgs.vbs`（必要なら `rgs_quick.vbs` も別番号に）
2. **設定 → キー割り当て**
   - 種別「外部マクロ」→ `rgs` を選び、好きなキー（例 `Ctrl+Shift+G`）に割り当て

### 2 つのマクロ

| マクロ | 動作 | 結果の出力先 |
|---|---|---|
| `rgs.vbs` | 検索ダイアログを開く（非同期なのでサクラエディタは固まらない） | 新しいタブ |
| `rgs_quick.vbs` | 入力ボックスで確認したら即検索 | アウトプットウィンドウ |

どちらもカーソル位置の単語（または選択範囲）と、編集中ファイルのフォルダを初期値にする。

### 実装の切り替え

各マクロの先頭にある定数を書き換えるだけ。

```vbs
' 使う実装: "python" または "powershell"
Const IMPL = "python"
```

マクロは自身のパス（`$M`）からリポジトリの場所を求めるので、
フォルダごと移動しても書き換えは不要（求まらないときは `ROOT_FALLBACK` を使う）。

## 単語単位検索の違いについて

ripgrep の `-w` とサクラエディタの「単語単位」は**単語境界の定義が違う**。

| | 単語構成文字の考え方 | `test` で `test用` は |
|---|---|---|
| ripgrep `-w` | Unicode の単語境界。漢字・かなも単語構成文字 | **マッチしない** |
| サクラエディタ | 文字種（英数字／ひらがな／カタカナ／漢字）の変わり目が区切り | マッチする |

`-WordJp` とダイアログの「単語単位で探す」は**サクラエディタ側の挙動**に合わせてある。
検索語の先頭・末尾の文字種だけを禁止する先読み・後読みを組み立てて PCRE2（`-P`）で検索する。

```
test  ->  (?<![0-9A-Za-z_])test(?![0-9A-Za-z_])
```

実際の挙動（検索語 `test`）:

| 対象 | `-w` | `-WordJp` |
|---|---|---|
| `test用のコード` | ✗ | ✓ |
| `これはtestです` | ✗ | ✓ |
| `テスト用のtest` | ✗ | ✓ |
| `unit test done` | ✓ | ✓ |
| `testing here` | ✗ | ✗ |
| `a_test_b` | ✗ | ✗ |
| `contest用` | ✗ | ✗ |

日本語の検索語でも同じ考え方で動く（検索語 `用`）:

| 対象 | 結果 |
|---|---|
| `test用のコード` | ✓（前が英数字） |
| `テスト用です` | ✓（前がカタカナ） |
| `この用でいい` | ✓（前後がひらがな） |
| `試用中のもの` | ✗（前が漢字） |
| `用途を確認` | ✗（後ろが漢字） |

正規表現と併用したとき、および rg が PCRE2 無しビルドのときは `-w` にフォールバックする。

## 既定オプション

毎回付けたいオプションは ripgrep 本体の設定ファイル機能で指定する。
リポジトリ直下の `rgrc` がそれで、`RIPGREP_CONFIG_PATH` が未設定なら自動で使われる。

```
# 1 行 1 引数。値を取るものは --opt=value の形（スペース区切り不可）
--smart-case
--max-columns=500
--glob=!node_modules/*
```

コマンドラインやダイアログの指定のほうが優先される。

## 環境変数

| 変数 | 用途 |
|---|---|
| `SAKURA_EXE` | sakura.exe のパス（未設定なら Program Files 等を自動探索） |
| `RG_EXE` | rg.exe のパス（未設定なら PATH → VS Code 同梱を自動探索） |
| `RIPGREP_CONFIG_PATH` | rg の設定ファイル（未設定なら `rgrc`） |

## 仕様・調査メモ

- [docs/spec.md](docs/spec.md) — 実装非依存の仕様
- [docs/sakura-notes.md](docs/sakura-notes.md) — サクラエディタのヘルプから確認した内容
