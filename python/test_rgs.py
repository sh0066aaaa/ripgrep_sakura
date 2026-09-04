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

print()
print("ALL PASS" if ok else "FAILED")
sys.exit(0 if ok else 1)
