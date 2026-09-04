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

DEFAULT_CONFIG = {
    "Word": "", "Folder": "", "Files": "",
    "Case": False, "Whole": False, "Regex": False, "Sub": True, "Hidden": False,
}


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

def show_dialog(cfg: dict) -> dict | None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    try:  # 高 DPI でぼやけないように
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = tk.Tk()
    root.title("rgs - ripgrep 検索")
    root.resizable(False, False)

    word = tk.StringVar(value=cfg["Word"])
    folder = tk.StringVar(value=cfg["Folder"])
    files = tk.StringVar(value=cfg["Files"])
    case_ = tk.BooleanVar(value=bool(cfg["Case"]))
    whole = tk.BooleanVar(value=bool(cfg["Whole"]))
    regex = tk.BooleanVar(value=bool(cfg["Regex"]))
    sub = tk.BooleanVar(value=bool(cfg["Sub"]))
    hidden = tk.BooleanVar(value=bool(cfg["Hidden"]))
    result: dict = {}

    frm = ttk.Frame(root, padding=14)
    frm.grid(sticky="nsew")

    def add_row(r, label, var, browse=False):
        ttk.Label(frm, text=label).grid(row=r, column=0, sticky="w", pady=5)
        entry = ttk.Entry(frm, textvariable=var, width=48)
        entry.grid(row=r, column=1, sticky="we", pady=5, padx=(8, 0))
        if browse:
            ttk.Button(frm, text="参照...", width=9, command=pick_folder
                       ).grid(row=r, column=2, padx=(6, 0))
        else:
            ttk.Frame(frm, width=76).grid(row=r, column=2)
        return entry

    def pick_folder():
        initial = folder.get() if os.path.isdir(folder.get()) else None
        chosen = filedialog.askdirectory(initialdir=initial, parent=root)
        if chosen:
            folder.set(os.path.normpath(chosen))

    entry_word = add_row(0, "検索文字列", word)
    add_row(1, "フォルダ", folder, browse=True)
    add_row(2, "ファイル", files)
    ttk.Label(frm, text="例: *.c;*.h   空欄ならすべてのファイル", foreground="#777"
              ).grid(row=3, column=1, sticky="w", padx=(8, 0))

    opts = ttk.LabelFrame(frm, text="オプション", padding=10)
    opts.grid(row=4, column=0, columnspan=3, sticky="we", pady=(12, 0))
    ttk.Checkbutton(opts, text="大文字小文字を区別する", variable=case_).grid(row=0, column=0, sticky="w", padx=4, pady=3)
    ttk.Checkbutton(opts, text="正規表現", variable=regex).grid(row=0, column=1, sticky="w", padx=18, pady=3)
    ttk.Checkbutton(opts, text="単語単位で探す", variable=whole).grid(row=1, column=0, sticky="w", padx=4, pady=3)
    ttk.Checkbutton(opts, text="サブフォルダも検索", variable=sub).grid(row=1, column=1, sticky="w", padx=18, pady=3)
    ttk.Checkbutton(opts, text="隠しファイルも含める", variable=hidden).grid(row=2, column=0, sticky="w", padx=4, pady=3)

    def do_search(*_):
        w = word.get().strip()
        d = folder.get().strip()
        if not w:
            messagebox.showinfo("rgs", "検索文字列を入力してください。", parent=root)
            entry_word.focus_set()
            return
        if d and not os.path.isdir(d):
            messagebox.showinfo("rgs", "フォルダが見つかりません:\n" + d, parent=root)
            return
        result.update({
            "Word": w, "Folder": d, "Files": files.get().strip(),
            "Case": case_.get(), "Whole": whole.get(), "Regex": regex.get(),
            "Sub": sub.get(), "Hidden": hidden.get(),
        })
        root.destroy()

    btns = ttk.Frame(frm)
    btns.grid(row=5, column=0, columnspan=3, sticky="e", pady=(14, 0))
    ttk.Button(btns, text="検索", width=12, command=do_search).grid(row=0, column=0, padx=4)
    ttk.Button(btns, text="キャンセル", width=12, command=root.destroy).grid(row=0, column=1)

    root.bind("<Return>", do_search)
    root.bind("<Escape>", lambda e: root.destroy())
    root.eval("tk::PlaceWindow . center")
    root.attributes("-topmost", True)
    entry_word.focus_force()
    entry_word.select_range(0, "end")
    root.mainloop()
    return result or None


def config_to_rg_args(cfg: dict, rg: str) -> list[str]:
    args = ["-s"] if cfg["Case"] else ["-i"]
    pattern = cfg["Word"]

    if cfg["Whole"] and not cfg["Regex"] and has_pcre2(rg):
        args.append("-P")
        pattern = jp_word_pattern(pattern)
    elif cfg["Whole"]:
        args.append("-w")
        if not cfg["Regex"]:
            args.append("-F")
    elif not cfg["Regex"]:
        args.append("-F")

    if not cfg["Sub"]:
        args.append("--max-depth=1")
    if cfg["Hidden"]:
        args.append("--hidden")
    for g in re.split(r"[;,]", cfg["Files"]):
        g = g.strip()
        if g:
            args += ["-g", g]

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
        if not m:
            continue
        path, lno, bcol, text = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
        if not os.path.isabs(path):
            path = os.path.normpath(os.path.join(root, path))
        path = path.replace("/", "\\")
        hits.append(Hit(path, lno, byte_col_to_char_col(text, bcol), text))
    return hits


def build_output(shown_args: list[str], root: str, rgrc: str | None, hits: list[Hit]):
    header = ["□検索条件  " + " ".join(shown_args), "□フォルダ  " + root]
    if rgrc:
        header.append("□既定設定  " + rgrc)
    body = [h.format() for h in hits]
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
    if opt.dialog:
        cfg = load_config()
        if opt.init_word:
            cfg["Word"] = opt.init_word
        if opt.init_folder:
            cfg["Folder"] = opt.init_folder
        if not cfg["Folder"]:
            cfg["Folder"] = os.getcwd()
        chosen = show_dialog(cfg)
        if not chosen:
            return 0
        save_config(chosen)
        if chosen["Folder"] and os.path.isdir(chosen["Folder"]):
            os.chdir(chosen["Folder"])
        rg_args = config_to_rg_args(chosen, rg)

    rgrc = apply_default_rgrc()

    if opt.word_jp:
        rg_args = apply_word_jp(rg_args, rg)

    shown_args = list(rg_args)
    rg_args = ensure_search_path(rg_args)

    if opt.dry_run:
        rep.msg("rg " + " ".join(RG_FIXED_ARGS + rg_args))
        rep.msg("cwd: " + os.getcwd())
        return 0

    rc, lines = run_rg(rg, rg_args)
    if rc == 1:
        rep.msg("該当なし")
        return 1
    if rc > 1:
        rep.msg("\r\n".join(lines))
        return rc

    root = os.getcwd()
    hits = parse_hits(lines, root)
    if not hits:
        rep.msg("該当なし")
        return 1

    if opt.direct:
        h = hits[0]
        subprocess.Popen([sakura, "-Y=%d" % h.line, "-X=%d" % h.col, "--", h.path])
        return 0

    header, body, footer = build_output(shown_args, root, rgrc, hits)

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
