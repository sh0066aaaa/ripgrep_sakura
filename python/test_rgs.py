# -*- coding: utf-8 -*-
import io, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import rgs

ok = True
def eq(name, got, want):
    global ok
    mark = "OK " if got == want else "NG "
    if got != want:
        ok = False
    print("%s %-34s got=%r" % (mark, name, got))
    if got != want:
        print("     want=%r" % (want,))

# 桁変換: rg のバイト桁 -> サクラエディタの文字桁
line = "  // TODO: 日本語のコメント TODO here"
eq("col ascii",  rgs.byte_col_to_char_col(line, 6), 6)
eq("col after jp", rgs.byte_col_to_char_col(line, 21 + 8 * 2), 21)   # 日本語8文字=24byte
eq("col head",   rgs.byte_col_to_char_col(line, 1), 1)
eq("col emoji",  rgs.byte_col_to_char_col("a😀b", 6), 4)             # サロゲートは2換算

# 単語境界パターン
eq("jp ascii", rgs.jp_word_pattern("test"), "(?<![0-9A-Za-z_])test(?![0-9A-Za-z_])")
kanji = rgs.CHAR_CLASSES[3][1]
eq("jp kanji", rgs.jp_word_pattern("用"), "(?<![" + kanji + "])用(?![" + kanji + "])")

# 位置引数の数え方（-F を -f と誤判定しないこと）
eq("pos -i -F test",     rgs.count_positionals(["-i", "-F", "test"]), 1)
eq("pos -f x test",      rgs.count_positionals(["-f", "x", "test"]), 1)
eq("pos -g *.c foo src", rgs.count_positionals(["-g", "*.c", "foo", "src"]), 2)
eq("idx -i -F test",     rgs.first_positional_index(["-i", "-F", "test"]), 2)
eq("path added",         rgs.ensure_search_path(["-i", "test"]), ["-i", "test", "./"])
eq("path kept",          rgs.ensure_search_path(["-i", "test", "src"]), ["-i", "test", "src"])

# ダイアログ設定 -> rg 引数
rg = rgs.find_rg()
def cfg(**kw):
    base = {"Word": "test", "Folder": r"C:\t", "Files": "",
            "Case": False, "Whole": False, "Regex": False, "Sub": True, "Hidden": False}
    base.update(kw)
    return base

eq("dlg default",   rgs.config_to_rg_args(cfg(), rg), ["-i", "-F", "--", "test", r"C:\t"])
eq("dlg case+whole", rgs.config_to_rg_args(cfg(Case=True, Whole=True), rg),
   ["-s", "-P", "--", "(?<![0-9A-Za-z_])test(?![0-9A-Za-z_])", r"C:\t"])
eq("dlg regex+nosub", rgs.config_to_rg_args(cfg(Regex=True, Sub=False), rg),
   ["-i", "--max-depth=1", "--", "test", r"C:\t"])
eq("dlg glob+hidden", rgs.config_to_rg_args(cfg(Files="*.c;*.h", Hidden=True), rg),
   ["-i", "-F", "--hidden", "-g", "*.c", "-g", "*.h", "--", "test", r"C:\t"])
eq("dlg whole+regex", rgs.config_to_rg_args(cfg(Whole=True, Regex=True), rg),
   ["-i", "-w", "--", "test", r"C:\t"])

# 引数解析
o = rgs.parse_args(["-Dialog", "-DryRun", "-Word", "foo", "-Folder", r"C:\x", "-i", "bar"])
eq("parse flags", (o.dialog, o.dry_run, o.init_word, o.init_folder, o.rg_args),
   (True, True, "foo", r"C:\x", ["-i", "bar"]))
o2 = rgs.parse_args(["-i", "-Dialog"])
eq("parse stops at rg arg", o2.rg_args, ["-i", "-Dialog"])

# --- 対象/除外ファイルの glob 変換 ---
eq("glob files",   rgs.globs_from("*.c;*.h"), ["-g", "*.c", "-g", "*.h"])
eq("glob exclude", rgs.globs_from("*.exe,*.obj", negate=True), ["-g", "!*.exe", "-g", "!*.obj"])
eq("glob dirs",    rgs.globs_from(".git;.svn", negate=True, as_dir=True),
   ["-g", "!.git/**", "-g", "!.svn/**"])
eq("glob empty",   rgs.globs_from(""), [])

# --- 結果出力 / ファイル毎最初のみ / 除外 ---
eq("dlg 該当部分", rgs.config_to_rg_args(cfg(Output="part"), rg)[:3], ["-i", "-F", "-o"])
eq("dlg 否該当行", rgs.config_to_rg_args(cfg(Output="invert"), rg)[:3], ["-i", "-F", "-v"])
eq("dlg 最初のみ", rgs.config_to_rg_args(cfg(FirstOnly=True), rg)[:4], ["-i", "-F", "-m", "1"])
eq("dlg *.* は全ファイル", rgs.config_to_rg_args(cfg(Files="*.*"), rg),
   ["-i", "-F", "--", "test", r"C:\t"])
eq("dlg 除外", rgs.config_to_rg_args(cfg(Files="", ExcludeFiles="*.exe", ExcludeDirs=".git"), rg),
   ["-i", "-F", "-g", "!*.exe", "-g", "!.git/**", "--", "test", r"C:\t"])

# --- 結果出力形式 ---
hits = [rgs.Hit("X:/a.c", 1, 2, "aaa"), rgs.Hit("X:/a.c", 3, 4, "bbb"),
        rgs.Hit("X:/b.c", 5, 6, "ccc")]
eq("fmt ノーマル", rgs.format_body(hits, "normal"),
   ["X:/a.c(1,2): aaa", "X:/a.c(3,4): bbb", "X:/b.c(5,6): ccc"])
eq("fmt ファイル毎", rgs.format_body(hits, "perfile"),
   ["X:/a.c", "\t(1,2): aaa", "\t(3,4): bbb", "X:/b.c", "\t(5,6): ccc"])
eq("fmt 結果のみ", rgs.format_body(hits, "only"), ["aaa", "bbb", "ccc"])

# --- 文字コードセット ---
eq("enc 自動選択", rgs.ENCODING_MAP["自動選択 (UTF-8)"], [None])
eq("enc 自動+SJIS", rgs.ENCODING_MAP["自動選択 (UTF-8 + Shift_JIS)"], [None, "sjis"])
eq("enc Shift_JIS", rgs.ENCODING_MAP["Shift_JIS"], ["sjis"])

# --- -v のときの桁なし形式 ---
parsed = rgs.parse_hits([r"C:\x\a.c:12:no column here"], r"C:\x")
eq("parse 桁なし", [(h.path, h.line, h.col, h.text) for h in parsed],
   [(r"C:\x\a.c", 12, 1, "no column here")])

# --- UTF-8 判定 ---
import tempfile
d = tempfile.mkdtemp()
u8 = os.path.join(d, "u8.txt")
sj = os.path.join(d, "sj.txt")
open(u8, "wb").write("日本語".encode("utf-8"))
open(sj, "wb").write("日本語".encode("cp932"))
eq("utf8 判定 (UTF-8)", rgs.is_utf8_like(u8), True)
eq("utf8 判定 (SJIS)", rgs.is_utf8_like(sj), False)

# --- ダイアログを出すモニターの決定 ---
eq("pos モード一覧", rgs.WINDOW_POS_MODES, ("active", "cursor", "primary"))
eq("primary は Tk 任せ", rgs.work_area("primary", None), None)
_area = rgs.work_area("cursor", None)
eq("cursor は矩形を返す", (isinstance(_area, tuple), len(_area or ()) == 4), (True, True))
eq("cursor の矩形は正の大きさ", (_area[2] > 0, _area[3] > 0), (True, True))
o = rgs.parse_args(["-Dialog", "-Pos", "cursor", "foo"])
eq("parse -Pos", (o.dialog, o.pos, o.rg_args), (True, "cursor", ["foo"]))

# --- 検索場所の初期値 ---
eq("初期値=前回のフォルダー（既定）",
   rgs.initial_folder({"Folder": r"D:\last", "CurrentFolderDefault": False}, r"D:\now"), r"D:\last")
eq("初期値=現フォルダー（チェック ON）",
   rgs.initial_folder({"Folder": r"D:\last", "CurrentFolderDefault": True}, r"D:\now"), r"D:\now")
eq("前回が無ければ現フォルダー",
   rgs.initial_folder({"Folder": "", "CurrentFolderDefault": False}, r"D:\now"), r"D:\now")
eq("既定値は前回のフォルダー", rgs.DEFAULT_CONFIG["CurrentFolderDefault"], False)

print()
print("ALL PASS" if ok else "FAILED")
sys.exit(0 if ok else 1)
