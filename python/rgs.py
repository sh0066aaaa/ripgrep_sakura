#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rgs - ripgrep の検索結果をサクラエディタで開く。

仕様は ../docs/spec.md を参照。
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# --- 定数 -----------------------------------------------------------------

# 値を取る rg のオプション（次の引数を値として読み飛ばす）
# 比較は大文字小文字を区別すること。-F(固定文字列) と -f(--file) を混同しないため
VALUE_OPTS = {
    "-e", "-g", "-t", "-T", "-m", "-A", "-B", "-C", "-M", "-j", "-f", "-r", "-E",
    "--glob", "--iglob", "--type", "--type-not", "--regexp", "--replace", "--file",
    "--max-count", "--max-depth", "--max-columns", "--encoding", "--pre", "--sort",
    "--sortr", "--threads", "--ignore-file", "--context", "--before-context",
    "--after-context",
}

RG_FIXED_ARGS = ["--vimgrep", "--no-heading", "--color", "never"]

# 文字種 -> PCRE2 の文字クラス（サクラエディタ互換の単語境界に使う）
CHAR_CLASSES = [
    (re.compile(r"[0-9A-Za-z_]"), r"0-9A-Za-z_"),
    (re.compile(r"[\u3041-\u309F]"), r"\x{3041}-\x{309F}"),
    (re.compile(r"[\u30A0-\u30FF\uFF66-\uFF9F]"), r"\x{30A0}-\x{30FF}\x{FF66}-\x{FF9F}"),
    (re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\u3005]"), r"\x{3400}-\x{4DBF}\x{4E00}-\x{9FFF}\x{3005}"),
]

VIMGREP_RE = re.compile(r"^(.*):(\d+):(\d+):(.*)$")
# -v (否該当行) のときは桁が出ない: path:line:text
VIMGREP_NOCOL_RE = re.compile(r"^(.*):(\d+):(.*)$")

USAGE = "usage: rgs [-Dialog] [-Direct] [-Stdout] [-DryRun] [-WordJp] <rg の引数...>"


# --- 出力 -----------------------------------------------------------------

def write_utf8_stdout(lines) -> None:
    """UTF-8 のバイト列を直接書く（コンソールの既定エンコーディングを経由しない）。

    サクラエディタの ExecCommand に 0x80 を付けて受け取る前提。
    """
    data = ("\r\n".join(lines) + "\r\n").encode("utf-8")
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


class Reporter:
    """-Stdout のときはメッセージも UTF-8 で標準出力へ流す。"""

    def __init__(self, to_stdout: bool):
        self.to_stdout = to_stdout

    def msg(self, text: str) -> None:
        if self.to_stdout:
            write_utf8_stdout([text])
        else:
            print(text)


# --- 実行ファイルの探索 -----------------------------------------------------

def find_rg() -> str | None:
    env = os.environ.get("RG_EXE")
    if env and Path(env).exists():
        return env
    found = shutil.which("rg")
    if found:
        return found
    # VS Code 同梱の rg.exe（再帰検索は遅いのでワイルドカードで直接叩く）
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        vs = Path(local) / "Programs" / "Microsoft VS Code"
        patterns = [
            vs / "resources/app/node_modules/@vscode/ripgrep*/bin/rg.exe",
            vs / "resources/app/node_modules/@vscode/ripgrep*/bin/win32-*/rg.exe",
            vs / "*/resources/app/node_modules/@vscode/ripgrep*/bin/win32-*/rg.exe",
        ]
        for pat in patterns:
            hits = glob.glob(str(pat))
            if hits:
                return hits[0]
    return None


def find_sakura() -> str | None:
    env = os.environ.get("SAKURA_EXE")
    if env and Path(env).exists():
        return env
    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", "")) / "sakura" / "sakura.exe",
        Path(os.environ.get("ProgramFiles", "")) / "sakura" / "sakura.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "sakura" / "sakura.exe",
    ]
    for c in candidates:
        if c.parent.name and c.exists():
            return str(c)
    return shutil.which("sakura.exe")


def apply_default_rgrc() -> str | None:
    """RIPGREP_CONFIG_PATH が未設定なら rgrc を割り当てる。"""
    if os.environ.get("RIPGREP_CONFIG_PATH"):
        return os.environ["RIPGREP_CONFIG_PATH"]
    here = Path(__file__).resolve().parent
    for cand in (here / "rgrc", here.parent / "rgrc"):
        if cand.exists():
            os.environ["RIPGREP_CONFIG_PATH"] = str(cand)
            return str(cand)
    return None


_pcre2_cache: bool | None = None


def has_pcre2(rg: str) -> bool:
    global _pcre2_cache
    if _pcre2_cache is None:
        try:
            r = subprocess.run([rg, "--pcre2-version"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            _pcre2_cache = (r.returncode == 0)
        except OSError:
            _pcre2_cache = False
    return _pcre2_cache


# --- 単語単位検索（サクラエディタ互換） --------------------------------------

def char_class_of(ch: str) -> str:
    for pattern, cls in CHAR_CLASSES:
        if pattern.match(ch):
            return cls
    return ""


def jp_word_pattern(text: str) -> str:
    """検索語の先頭・末尾の文字種だけを禁止する PCRE2 パターンを作る。

    rg の -w は Unicode の単語境界なので "test" が "test用" にマッチしない。
    サクラエディタは文字種の変わり目を区切りとみなすので、それに合わせる。
    """
    if not text:
        return text
    core = re.escape(text)
    pre = char_class_of(text[0])
    post = char_class_of(text[-1])
    if pre:
        core = "(?<![" + pre + "])" + core
    if post:
        core = core + "(?![" + post + "])"
    return core


# --- 引数解析 ---------------------------------------------------------------

class Options:
    def __init__(self):
        self.dialog = False
        self.direct = False
        self.stdout = False
        self.dry_run = False
        self.word_jp = False
        self.init_word = ""
        self.init_folder = ""
        self.rg_args: list[str] = []


def parse_args(argv: list[str]) -> Options:
    """制御スイッチは先頭のみ。それ以降はすべて rg にそのまま渡す。"""
    opt = Options()
    i = 0
    flags = {
        "-dialog": "dialog", "-direct": "direct", "-stdout": "stdout",
        "-dryrun": "dry_run", "-wordjp": "word_jp",
    }
    takes_value = {"-word": "init_word", "-folder": "init_folder"}
    while i < len(argv):
        low = argv[i].lower()
        if low in flags:
            setattr(opt, flags[low], True)
            i += 1
        elif low in takes_value:
            setattr(opt, takes_value[low], argv[i + 1] if i + 1 < len(argv) else "")
            i += 2
        else:
            break
    opt.rg_args = list(argv[i:])
    return opt


def first_positional_index(args: list[str]) -> int:
    """位置引数（オプションでもその値でもないもの）の最初の位置。無ければ -1。"""
    after_dd = False
    i = 0
    while i < len(args):
        a = args[i]
        if not after_dd and a == "--":
            after_dd = True
        elif not after_dd and a.startswith("-") and len(a) > 1:
            if a in VALUE_OPTS:  # 大文字小文字を区別する
                i += 1
        else:
            return i
        i += 1
    return -1


def count_positionals(args: list[str]) -> int:
    n = 0
    after_dd = False
    i = 0
    while i < len(args):
        a = args[i]
        if not after_dd and a == "--":
            after_dd = True
        elif not after_dd and a.startswith("-") and len(a) > 1:
            if a in VALUE_OPTS:
                i += 1
        else:
            n += 1
        i += 1
    return n


def apply_word_jp(args: list[str], rg: str) -> list[str]:
    idx = first_positional_index(args)
    if idx < 0:
        return args
    if not has_pcre2(rg):
        return ["-w"] + args
    out = list(args)
    out[idx] = jp_word_pattern(out[idx])
    out = [a for a in out if a not in ("-F", "-w")]
    return ["-P"] + out


def ensure_search_path(args: list[str]) -> list[str]:
    """パス未指定なら ./ を足す。

    rg はパス未指定かつ標準入力がパイプだと標準入力を検索して固まるため。
    """
    if count_positionals(args) <= 1:
        return list(args) + ["./"]
    return list(args)


# --- 設定の永続化 -----------------------------------------------------------

# 既定値はサクラエディタの Grep ダイアログに合わせてある
DEFAULT_CONFIG = {
    "Word": "",
    "Folder": "",
    "Files": "*.*",
    "ExcludeFiles": "*.msi;*.exe;*.obj;*.pdb;*.ilk;*.res;*.pch;*.iobj;*.ipdb",
    "ExcludeDirs": ".git;.svn;.vs",
    "Case": False,       # 英大文字と小文字を区別する
    "Whole": False,      # 単語単位で探す
    "Regex": False,      # 正規表現
    "Sub": True,         # サブフォルダーも検索
    "Hidden": False,     # 隠しファイルも検索 (rg 固有)
    "Output": "line",    # 結果出力     line=該当行 / part=該当部分 / invert=否該当行
    "Format": "normal",  # 結果出力形式 normal=ノーマル / perfile=ファイル毎 / only=結果のみ
    "FirstOnly": False,  # ファイル毎最初のみ検索
    "Encoding": "",      # 文字コードセット（空なら ENCODINGS の先頭）
    "History": {},       # 各入力欄の履歴
}

# 文字コードセットの選択肢 -> rg の --encoding に渡す値のリスト
# None は「指定なし」(rg 既定の UTF-8 / BOM 判定)
ENCODINGS = [
    ("自動選択 (UTF-8)", [None]),
    ("自動選択 (UTF-8 + Shift_JIS)", [None, "sjis"]),
    ("UTF-8", ["utf-8"]),
    ("Shift_JIS", ["sjis"]),
    ("EUC-JP", ["euc-jp"]),
    ("UTF-16LE", ["utf-16le"]),
    ("UTF-16BE", ["utf-16be"]),
]
ENCODING_LABELS = [e[0] for e in ENCODINGS]
ENCODING_MAP = dict(ENCODINGS)

HISTORY_MAX = 16


def push_history(cfg: dict, key: str, value: str) -> None:
    """コンボボックスの履歴を先頭に積む。"""
    if not value:
        return
    hist = cfg.setdefault("History", {})
    items = [v for v in hist.get(key, []) if v != value]
    hist[key] = ([value] + items)[:HISTORY_MAX]


def config_path() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or "."
    return Path(base) / "rgs" / "dialog.json"


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        # PowerShell 版が BOM 付き UTF-8 で書くので utf-8-sig で読む
        with open(config_path(), encoding="utf-8-sig") as f:
            saved = json.load(f)
        for k in DEFAULT_CONFIG:
            if k in saved and saved[k] is not None:
                cfg[k] = saved[k]
    except Exception:
        pass
    return cfg


def save_config(cfg: dict) -> None:
    try:
        p = config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# --- 検索ダイアログ ---------------------------------------------------------

def engine_label(rg: str) -> str:
    """サクラエディタの「bregonig.dll ...」表示にあたる、検索エンジンの情報。"""
    def first_line(args):
        try:
            r = subprocess.run([rg] + args, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            if r.returncode != 0:
                return ""
            return r.stdout.decode("utf-8", "replace").splitlines()[0].strip()
        except Exception:
            return ""
    ver = first_line(["--version"]) or "ripgrep"
    pcre = first_line(["--pcre2-version"])
    if pcre:
        ver += " with " + pcre.split(" is ")[0]
    return ver


def show_dialog(cfg: dict, rg: str) -> dict | None:
    """サクラエディタの Grep ダイアログに似せた検索ダイアログ。"""
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    try:  # 高 DPI でぼやけないように
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    hist = cfg.get("History") or {}
    home_folder = cfg["Folder"]
    result: dict = {}

    root = tk.Tk()
    root.title("rgs - Grep")
    root.resizable(False, False)

    v_word = tk.StringVar(value=cfg["Word"])
    v_folder = tk.StringVar(value=cfg["Folder"])
    v_files = tk.StringVar(value=cfg["Files"])
    v_exfiles = tk.StringVar(value=cfg["ExcludeFiles"])
    v_exdirs = tk.StringVar(value=cfg["ExcludeDirs"])
    v_case = tk.BooleanVar(value=bool(cfg["Case"]))
    v_whole = tk.BooleanVar(value=bool(cfg["Whole"]))
    v_regex = tk.BooleanVar(value=bool(cfg["Regex"]))
    v_sub = tk.BooleanVar(value=bool(cfg["Sub"]))
    v_hidden = tk.BooleanVar(value=bool(cfg["Hidden"]))
    v_first = tk.BooleanVar(value=bool(cfg.get("FirstOnly")))
    v_output = tk.StringVar(value=cfg.get("Output", "line"))
    v_format = tk.StringVar(value=cfg.get("Format", "normal"))
    enc_label = cfg.get("Encoding") or ENCODING_LABELS[0]
    if enc_label not in ENCODING_MAP:
        enc_label = ENCODING_LABELS[0]
    v_enc = tk.StringVar(value=enc_label)

    outer = ttk.Frame(root, padding=10)
    outer.grid(sticky="nsew")
    left = ttk.Frame(outer)
    left.grid(row=0, column=0, sticky="nw")
    right = ttk.Frame(outer)
    right.grid(row=0, column=1, sticky="ne", padx=(10, 0))

    def combo(parent, var, key, width, row, column=1, columnspan=1):
        c = ttk.Combobox(parent, textvariable=var, width=width, values=hist.get(key, []))
        c.grid(row=row, column=column, columnspan=columnspan, sticky="we", pady=2)
        return c

    def label(parent, text, row, column=0, **kw):
        w = ttk.Label(parent, text=text, **kw)
        w.grid(row=row, column=column, sticky="e", padx=(0, 6), pady=2)
        return w

    def check(parent, text, var, row, column=1, sticky="w", **kw):
        c = ttk.Checkbutton(parent, text=text, variable=var, **kw)
        c.grid(row=row, column=column, sticky=sticky, pady=1)
        return c

    # --- 条件 ---------------------------------------------------------------
    label(left, "条件(N):", 0)
    c_word = combo(left, v_word, "Word", 54, 0, columnspan=2)
    check(left, "単語単位で探す(W)", v_whole, 1, underline=8)
    check(left, "英大文字と小文字を区別する(C)", v_case, 2, underline=14)
    check(left, "正規表現(E)", v_regex, 3, underline=5)
    ttk.Label(left, text=engine_label(rg), foreground="#777").grid(
        row=3, column=2, sticky="e", padx=(10, 0))

    # --- 検索場所 -----------------------------------------------------------
    label(left, "検索場所(L):", 4)
    c_folder = combo(left, v_folder, "Folder", 44, 4)

    def pick_folder():
        initial = v_folder.get() if os.path.isdir(v_folder.get()) else None
        chosen = filedialog.askdirectory(initialdir=initial, parent=root)
        if chosen:
            v_folder.set(os.path.normpath(chosen))

    def go_up():
        cur = v_folder.get().rstrip("/").rstrip("\\")
        parent = os.path.dirname(cur)
        if parent and parent != cur:
            v_folder.set(parent)

    ttk.Button(left, text="...", width=4, command=pick_folder).grid(
        row=4, column=2, sticky="w", padx=(6, 0))

    sub_row = ttk.Frame(left)
    sub_row.grid(row=5, column=1, columnspan=2, sticky="we")
    ttk.Checkbutton(sub_row, text="サブフォルダーも検索(S)", variable=v_sub, underline=11
                    ).grid(row=0, column=0, sticky="w")
    ttk.Button(sub_row, text="上階層へ(U)", width=13, underline=4, command=go_up
               ).grid(row=0, column=1, padx=(12, 4))
    ttk.Button(sub_row, text="現フォルダー(G)", width=15, underline=7,
               command=lambda: v_folder.set(home_folder)).grid(row=0, column=2)
    check(left, "隠しファイルも検索する (rg 固有)", v_hidden, 6)

    # --- 対象 / 除外 --------------------------------------------------------
    label(left, "対象ファイル(I):", 7)
    combo(left, v_files, "Files", 54, 7, columnspan=2)
    label(left, "除外ファイル(J):", 8)
    combo(left, v_exfiles, "ExcludeFiles", 54, 8, columnspan=2)
    label(left, "除外フォルダー(K):", 9)
    combo(left, v_exdirs, "ExcludeDirs", 54, 9, columnspan=2)

    # --- 下段のグループ -----------------------------------------------------
    groups = ttk.Frame(left)
    groups.grid(row=10, column=0, columnspan=3, sticky="we", pady=(10, 0))

    g_out = ttk.LabelFrame(groups, text="結果出力", padding=6)
    g_out.grid(row=0, column=0, sticky="nw")
    for i, (text, val) in enumerate([("該当行(1)", "line"), ("該当部分(2)", "part"),
                                     ("否該当行(3)", "invert")]):
        ttk.Radiobutton(g_out, text=text, value=val, variable=v_output).grid(
            row=i, column=0, sticky="w")

    g_fmt = ttk.LabelFrame(groups, text="結果出力形式", padding=6)
    g_fmt.grid(row=0, column=1, sticky="nw", padx=(8, 0))
    for i, (text, val) in enumerate([("ノーマル(4)", "normal"), ("ファイル毎(5)", "perfile"),
                                     ("結果のみ(6)", "only")]):
        ttk.Radiobutton(g_fmt, text=text, value=val, variable=v_format).grid(
            row=i, column=0, sticky="w")

    g_etc = ttk.LabelFrame(groups, text="その他", padding=6)
    g_etc.grid(row=0, column=2, sticky="nw", padx=(8, 0))
    ttk.Checkbutton(g_etc, text="ファイル毎最初のみ検索(7)", variable=v_first).grid(
        row=0, column=0, sticky="w")
    ttk.Label(g_etc, text="文字コードセット(A):").grid(row=1, column=0, sticky="w", pady=(8, 0))
    ttk.Combobox(g_etc, textvariable=v_enc, values=ENCODING_LABELS, state="readonly",
                 width=26).grid(row=2, column=0, sticky="w")

    # --- 右側のボタン -------------------------------------------------------
    def do_search(*_):
        word = v_word.get().strip()
        folder = v_folder.get().strip()
        if not word:
            messagebox.showinfo("rgs", "条件を入力してください。", parent=root)
            c_word.focus_set()
            return
        if folder and not os.path.isdir(folder):
            messagebox.showinfo("rgs", "検索場所が見つかりません:\n" + folder, parent=root)
            c_folder.focus_set()
            return
        result.update({
            "Word": word, "Folder": folder, "Files": v_files.get().strip(),
            "ExcludeFiles": v_exfiles.get().strip(), "ExcludeDirs": v_exdirs.get().strip(),
            "Case": v_case.get(), "Whole": v_whole.get(), "Regex": v_regex.get(),
            "Sub": v_sub.get(), "Hidden": v_hidden.get(), "FirstOnly": v_first.get(),
            "Output": v_output.get(), "Format": v_format.get(), "Encoding": v_enc.get(),
            "History": cfg.get("History") or {},
        })
        for key in ("Word", "Folder", "Files", "ExcludeFiles", "ExcludeDirs"):
            push_history(result, key, result[key])
        root.destroy()

    def open_help():
        readme = Path(__file__).resolve().parent.parent / "README.md"
        try:
            os.startfile(str(readme))
        except Exception:
            pass

    ttk.Button(right, text="検索(F)", width=14, underline=3, command=do_search
               ).grid(row=0, column=0, pady=2)
    ttk.Button(right, text="キャンセル(X)", width=14, underline=6, command=root.destroy
               ).grid(row=1, column=0, pady=2)
    ttk.Button(right, text="ヘルプ(H)", width=14, underline=4, command=open_help
               ).grid(row=2, column=0, pady=(12, 2))

    # --- キー割り当て -------------------------------------------------------
    root.bind("<Return>", do_search)
    root.bind("<Escape>", lambda e: root.destroy())
    root.bind("<Alt-f>", do_search)
    root.bind("<Alt-x>", lambda e: root.destroy())
    root.bind("<Alt-h>", lambda e: open_help())
    root.bind("<Alt-u>", lambda e: go_up())
    root.bind("<Alt-g>", lambda e: v_folder.set(home_folder))
    root.bind("<Alt-n>", lambda e: c_word.focus_set())
    root.bind("<Alt-l>", lambda e: c_folder.focus_set())
    for key, var in (("w", v_whole), ("c", v_case), ("e", v_regex), ("s", v_sub)):
        root.bind("<Alt-%s>" % key, lambda e, v=var: v.set(not v.get()))

    root.eval("tk::PlaceWindow . center")
    root.attributes("-topmost", True)
    c_word.focus_force()
    c_word.select_range(0, "end")
    root.mainloop()
    return result or None


def globs_from(text: str, negate: bool = False, as_dir: bool = False) -> list[str]:
    """"*.c;*.h" のような並びを rg の -g 引数に変換する。"""
    out: list[str] = []
    for g in re.split(r"[;,]", text or ""):
        g = g.strip()
        if not g:
            continue
        if as_dir:
            g = g.rstrip("/").rstrip("\\") + "/**"
        out += ["-g", ("!" + g) if negate else g]
    return out


def config_to_rg_args(cfg: dict, rg: str) -> list[str]:
    args = ["-s"] if cfg["Case"] else ["-i"]
    pattern = cfg["Word"]

    # 単語単位: サクラエディタ互換の文字種境界（正規表現と併用時は rg 標準の -w）
    if cfg["Whole"] and not cfg["Regex"] and has_pcre2(rg):
        args.append("-P")
        pattern = jp_word_pattern(pattern)
    elif cfg["Whole"]:
        args.append("-w")
        if not cfg["Regex"]:
            args.append("-F")
    elif not cfg["Regex"]:
        args.append("-F")

    if not cfg.get("Sub", True):
        args.append("--max-depth=1")
    if cfg.get("Hidden"):
        args.append("--hidden")
    if cfg.get("FirstOnly"):
        args += ["-m", "1"]

    # 結果出力
    output = cfg.get("Output", "line")
    if output == "part":
        args.append("-o")       # 該当部分
    elif output == "invert":
        args.append("-v")       # 否該当行

    # 対象ファイル。空欄と *.* は「すべて」の意味なので -g を付けない
    files = (cfg.get("Files") or "").strip()
    if files and files != "*.*":
        args += globs_from(files)
    args += globs_from(cfg.get("ExcludeFiles"), negate=True)
    args += globs_from(cfg.get("ExcludeDirs"), negate=True, as_dir=True)

    args += ["--", pattern, cfg["Folder"]]
    return args


# --- rg 実行と整形 ----------------------------------------------------------

def run_rg(rg: str, args: list[str]) -> tuple[int, list[str]]:
    """rg を実行し、出力を UTF-8 として復号した行のリストで返す。"""
    proc = subprocess.run(
        [rg] + RG_FIXED_ARGS + args,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
    )
    text = proc.stdout.decode("utf-8", errors="replace")
    lines = [ln.rstrip("\r") for ln in text.split("\n") if ln.strip()]
    return proc.returncode, lines


def is_utf8_like(path: str) -> bool:
    """rg が既定（UTF-8 / BOM 判定）で正しく読めるファイルかどうか。"""
    import codecs
    try:
        with open(path, "rb") as f:
            data = f.read(1024 * 1024)
    except OSError:
        return True
    if data[:3] == codecs.BOM_UTF8 or data[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        return True                      # BOM 付きは rg が自分で判定する
    try:
        codecs.getincrementaldecoder("utf-8")().decode(data, False)
        return True
    except UnicodeDecodeError:
        return False


def line_path(line: str) -> str | None:
    m = VIMGREP_RE.match(line) or VIMGREP_NOCOL_RE.match(line)
    return m.group(1) if m else None


def run_rg_encodings(rg: str, args: list[str], encodings: list) -> tuple[int, list[str]]:
    """複数の文字コードで検索して結果をマージする（「自動選択」用）。

    rg はサクラエディタのような 1 ファイルごとの文字コード判定ができないので
    UTF-8 と Shift_JIS で 2 回検索する。ただし単純に足すと ASCII の検索語では
    同じ箇所が両方の回で当たって重複するため、ファイルごとにどちらの回の結果を
    採用するかを、そのファイルが UTF-8 として読めるかどうかで決める。
    """
    if len(encodings) == 1:
        enc = encodings[0]
        extra = [] if enc is None else ["--encoding", enc]
        return run_rg(rg, extra + args)

    merged: list[str] = []
    rc_final = 1
    cache: dict[str, bool] = {}
    cwd = os.getcwd()
    for enc in encodings:
        extra = [] if enc is None else ["--encoding", enc]
        rc, lines = run_rg(rg, extra + args)
        if rc > 1:
            return rc, lines
        if rc == 0:
            rc_final = 0
        want_utf8 = enc is None or enc == "utf-8"
        for ln in lines:
            path = line_path(ln)
            if path is None:
                continue
            full = path if os.path.isabs(path) else os.path.join(cwd, path)
            ok = cache.get(full)
            if ok is None:
                ok = is_utf8_like(full)
                cache[full] = ok
            if ok == want_utf8:
                merged.append(ln)
    return rc_final, merged


def byte_col_to_char_col(text: str, byte_col: int) -> int:
    """rg のバイト単位の桁を、サクラエディタの文字単位の桁へ変換する。

    サクラエディタの桁は「行の先頭からの文字数(サロゲートは1文字で2換算)」なので
    UTF-16 コード単位で数える。
    """
    raw = text.encode("utf-8")
    n = max(0, min(byte_col - 1, len(raw)))
    prefix = raw[:n].decode("utf-8", errors="ignore")
    return len(prefix.encode("utf-16-le")) // 2 + 1


class Hit:
    __slots__ = ("path", "line", "col", "text")

    def __init__(self, path: str, line: int, col: int, text: str):
        self.path, self.line, self.col, self.text = path, line, col, text

    def format(self) -> str:
        return "%s(%d,%d): %s" % (self.path, self.line, self.col, self.text)


def parse_hits(lines: list[str], root: str) -> list[Hit]:
    hits = []
    for line in lines:
        m = VIMGREP_RE.match(line)
        if m:
            path, lno, bcol, text = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
        else:
            m = VIMGREP_NOCOL_RE.match(line)
            if not m:
                continue
            path, lno, bcol, text = m.group(1), int(m.group(2)), 1, m.group(3)
        if not os.path.isabs(path):
            path = os.path.normpath(os.path.join(root, path))
        path = path.replace("/", "\\")
        hits.append(Hit(path, lno, byte_col_to_char_col(text, bcol), text))
    return hits


def format_body(hits: list[Hit], fmt: str) -> list[str]:
    """結果出力形式に応じて本文を組み立てる。

    ノーマル以外は行にファイル名が無いのでタグジャンプは効かない。
    """
    if fmt == "only":                       # 結果のみ
        return [h.text for h in hits]
    if fmt == "perfile":                    # ファイル毎
        out: list[str] = []
        last = None
        for h in hits:
            if h.path != last:
                out.append(h.path)
                last = h.path
            out.append("\t(%d,%d): %s" % (h.line, h.col, h.text))
        return out
    return [h.format() for h in hits]       # ノーマル


def build_output(shown_args: list[str], root: str, rgrc: str | None,
                 hits: list[Hit], fmt: str = "normal"):
    header = ["□検索条件  " + " ".join(shown_args), "□フォルダ  " + root]
    if rgrc:
        header.append("□既定設定  " + rgrc)
    body = format_body(hits, fmt)
    footer = ["", "該当 %d 件" % len(hits)]
    return header, body, footer


def write_result_file(all_lines: list[str]) -> str:
    """BOM 付き UTF-8 で書く。サクラエディタの文字コード判定を確実にするため。"""
    out_dir = Path(os.environ.get("TEMP", ".")) / "rgs"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_") + "%03d" % (datetime.now().microsecond // 1000)
    out = out_dir / ("rg_%s.grepout.txt" % stamp)
    with open(out, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write("\n".join(all_lines) + "\n")
    return str(out)


# --- メイン -----------------------------------------------------------------

def main(argv: list[str]) -> int:
    opt = parse_args(argv)
    rep = Reporter(opt.stdout)

    if not opt.dialog and not opt.rg_args:
        rep.msg(USAGE)
        return 2

    sakura = find_sakura()
    if sakura is None and not opt.stdout and not opt.dry_run:
        rep.msg("sakura.exe が見つかりません。環境変数 SAKURA_EXE にパスを設定してください。")
        return 1

    rg = find_rg()
    if rg is None:
        rep.msg("rg.exe が見つかりません。winget install BurntSushi.ripgrep.MSVC で"
                "インストールするか、環境変数 RG_EXE にパスを設定してください。")
        return 1

    rg_args = opt.rg_args
    enc_list: list = [None]
    fmt = "normal"
    if opt.dialog:
        cfg = load_config()
        if opt.init_word:
            cfg["Word"] = opt.init_word
        if opt.init_folder:
            cfg["Folder"] = opt.init_folder
        if not cfg["Folder"]:
            cfg["Folder"] = os.getcwd()
        chosen = show_dialog(cfg, rg)
        if not chosen:
            return 0
        save_config(chosen)
        if chosen["Folder"] and os.path.isdir(chosen["Folder"]):
            os.chdir(chosen["Folder"])
        rg_args = config_to_rg_args(chosen, rg)
        enc_list = ENCODING_MAP.get(chosen.get("Encoding", ""), [None])
        fmt = chosen.get("Format", "normal")

    rgrc = apply_default_rgrc()

    if opt.word_jp:
        rg_args = apply_word_jp(rg_args, rg)

    shown_args = list(rg_args)
    rg_args = ensure_search_path(rg_args)

    if opt.dry_run:
        rep.msg("rg " + " ".join(RG_FIXED_ARGS + rg_args))
        rep.msg("cwd: " + os.getcwd())
        return 0

    rc, lines = run_rg_encodings(rg, rg_args, enc_list)
    if rc == 1:
        rep.msg("該当なし")
        return 1
    if rc > 1:
        rep.msg("\r\n".join(lines))
        return rc

    root = os.getcwd()
    hits = parse_hits(lines, root)
    # rg の走査順は並列処理のため一定しない。結果一覧として読みやすいよう常に並べ直す
    hits.sort(key=lambda h: (h.path.lower(), h.line, h.col))
    if not hits:
        rep.msg("該当なし")
        return 1

    if opt.direct:
        h = hits[0]
        subprocess.Popen([sakura, "-Y=%d" % h.line, "-X=%d" % h.col, "--", h.path])
        return 0

    header, body, footer = build_output(shown_args, root, rgrc, hits, fmt)

    if opt.stdout:
        write_utf8_stdout(header + body + footer)
        return 0

    out_file = write_result_file(header + body + footer)
    # -R = ビューモード / -Y = 先頭ヒット行にカーソル
    subprocess.Popen([sakura, "-R", "-Y=%d" % (len(header) + 1), "--", out_file])
    print("該当 %d 件 -> %s" % (len(hits), out_file))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
