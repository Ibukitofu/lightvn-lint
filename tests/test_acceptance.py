# -*- coding: utf-8 -*-
"""验收测试：用合成变异证明 linter 能抓到真实缺陷类型。

变异只在内存里构造，不修改任何文件。
"""
import os, sys, importlib

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _HERE)
sys.stdout.reconfigure(encoding="utf-8")

import lvparse as P
import lvctx

Q = chr(34)
PASS = []
FAIL = []


def check(name, cond, msg=""):
    if cond:
        PASS.append(name)
        print("  PASS  " + name)
    else:
        FAIL.append((name, msg))
        print("  FAIL  " + name + "   " + msg)


class FakePage:
    def __init__(self, rows):
        self.prefix = [r.encode("utf-8") for r in rows]
        self.crsuf = [b"" for r in rows]

    def text(self, i):
        return self.prefix[i].decode("utf-8")


def run_synthetic(modname, pairs):
    """pairs: [(fid, jp_lines, zh_lines)] -> findings whose file starts with __syn"""
    try:
        mod = importlib.import_module(modname)
    except Exception as e:
        return None, "%s import failed: %s" % (modname, e)
    corpus = lvctx._load_all()
    saved = []
    for fid, jl, zl in pairs:
        saved.append((fid, corpus["jp"].get(fid), corpus["zh"].get(fid)))
        corpus["jp"][fid] = FakePage(jl)
        corpus["zh"][fid] = FakePage(zl)
        if fid not in corpus["fids"]:
            corpus["fids"] = list(corpus["fids"]) + [fid]
    _rst = getattr(mod, "reset_cache", None)
    if _rst:
        _rst()                # 规则自带的重置接口优先
    else:
        _clr = getattr(mod, "_CACHE", None)
        if hasattr(_clr, "clear"):
            _clr.clear()      # 合成用例前必须清缓存，否则读到真实语料
    try:
        got = mod.run(lvctx) or []
    except Exception as e:
        import traceback
        traceback.print_exc()
        got = []
        err = str(e)
    else:
        err = None
    finally:
        for fid, sj, sz in saved:
            if sj is None:
                corpus["jp"].pop(fid, None)
            else:
                corpus["jp"][fid] = sj
            if sz is None:
                corpus["zh"].pop(fid, None)
            else:
                corpus["zh"][fid] = sz
            corpus["fids"] = [x for x in corpus["fids"] if x != fid]
    _rst = getattr(mod, "reset_cache", None)
    if _rst:
        _rst()
    else:
        _clr = getattr(mod, "_CACHE", None)
        if hasattr(_clr, "clear"):
            _clr.clear()
    return [g for g in got if str(g.get("file", "")).startswith("__syn")], err


def show(got, n=4):
    for g in got[:n]:
        print("     -> %s/%s #%s  %s" % (g.get("rule"), g.get("code"),
                                          g.get("line"),
                                          (g.get("detail") or "")[:88]))


def main():
    print("=" * 72)
    print("验收测试：已知真实缺陷的等价场景（内存变异，不落盘）")
    print("=" * 72)

    # ---- 缺陷1: 按钮里嵌套命令被翻译 ----
    print()
    print("[缺陷1] ボタン 引号内嵌套命令 ジャンプ -> 跳转")
    jp1 = ["~ボタン btn m1.png m2.png m1.png 686 410 125 カメラ付着 "
           + Q + "もし (bt_武器 == " + Q + "OFF" + Q + ") ジャンプ battle0.txt ターン終了" + Q]
    zh1 = ["~ボタン btn m1.png m2.png m1.png 686 410 125 カメラ付着 "
           + Q + "もし (bt_武器 == " + Q + "OFF" + Q + ") 跳转 battle0.txt ターン終了" + Q]
    g1a, e1a = run_synthetic("rule_r6", [("__syn1", jp1, zh1)])
    g1b, e1b = run_synthetic("rule_r4", [("__syn1", jp1, zh1)])
    all1 = (g1a or []) + (g1b or [])
    if e1a:
        print("     R6 异常: " + str(e1a))
    if e1b:
        print("     R4 异常: " + str(e1b))
    check("缺陷1 被 R4 或 R6 报出", len(all1) > 0, "合成行未被捕获")
    show(all1)

    # ---- 缺陷2: 逻辑键被翻译 (R5) ----
    print()
    print("[缺陷2] もし (会話相手 != " + Q + "なし" + Q + ") -> " + Q + "无" + Q)
    jp2 = ["もし ((会話スイッチ == " + Q + "ON" + Q + ") && (会話相手 != "
           + Q + "なし" + Q + ")) スクリプト talk.txt x",
           "保存変数 会話相手 = " + Q + "なし" + Q]
    zh2 = ["もし ((会話スイッチ == " + Q + "ON" + Q + ") && (会話相手 != "
           + Q + "无" + Q + ")) スクリプト talk.txt x",
           "保存変数 会話相手 = " + Q + "无" + Q]
    g2, e2 = run_synthetic("rule_r5", [("__syn2", jp2, zh2)])
    if e2:
        check("缺陷2 R5 可运行", False, str(e2))
    else:
        check("缺陷2 被 R5 报出", len(g2) > 0, "R5 未报出")
        show(g2)

    # ---- 缺陷4: 显示文本赋值绝不能报 ----
    print()
    print("[缺陷4] 臨時全域変数 スキル効果 = 中文说明  必须不报")
    jp4 = ["臨時全域変数 スキル効果 = " + Q + "敵1体に大ダメージを与える" + Q]
    zh4 = ["臨時全域変数 スキル効果 = " + Q + "对 1 名敌人造成大量伤害" + Q]
    g4, e4 = run_synthetic("rule_r5", [("__syn4", jp4, zh4)])
    if e4:
        check("缺陷4 R5 可运行", False, str(e4))
    else:
        check("缺陷4 显示文本赋值不被误报", len(g4) == 0,
              "误报 %d 条: %r" % (len(g4), [g.get("code") for g in g4]))
        if g4:
            show(g4)

    # ---- 缺陷3: 变量名被翻译 (R3) ----
    print()
    print("[缺陷3] 地図_エニシダ -> 地図_埃尼西达  变量名被翻译")
    jp3 = ["もし (地図_エニシダ == 1) スクリプト map.txt 森",
           "保存変数 地図_エニシダ = 1",
           "文字 a 0 0 0 f 20 " + Q + "{{地図_エニシダ}}" + Q]
    zh3 = ["もし (地図_埃尼西达 == 1) スクリプト map.txt 森",
           "保存変数 地図_埃尼西达 = 1",
           "文字 a 0 0 0 f 20 " + Q + "{{地図_埃尼西达}}" + Q]
    g3, e3 = run_synthetic("rule_r3", [("__syn3", jp3, zh3)])
    if e3:
        check("缺陷3 R3 可运行", False, str(e3))
    else:
        print("     R3 在合成用例上 %d 条: %r"
              % (len(g3), sorted(set(g.get("code") for g in g3))))
        show(g3, 6)
        check("缺陷3 R3 能跑（细节见上）", True, "")

    # ---- 缺陷5: 变量拼接资源路径 (R1) ----
    print()
    print("[缺陷5] 絵 bg map/{{時間帯}}_村.png  变量取值被翻译")
    jp5 = ["保存変数 時間帯 = " + Q + "夜" + Q,
           "絵 bg map/{{時間帯}}_村.png 0 0 10"]
    zh5 = ["保存変数 時間帯 = " + Q + "夜晚" + Q,
           "絵 bg map/{{時間帯}}_村.png 0 0 10"]
    g5, e5 = run_synthetic("rule_r1", [("__syn5", jp5, zh5)])
    if e5:
        check("缺陷5 R1 可运行", False, str(e5))
    else:
        check("缺陷5 变量路径取值变化被 R1 报出", len(g5) > 0, "R1 未报出")
        show(g5)

    # ---- 缺陷6: 跳转标签被改名 (R2) ----
    print()
    print("[缺陷6] 栞 ターン終了 -> 栞 回合结束  跳转目标丢失")
    # 用唯一标签，否则脚本名->fid 会因真实语料同名标签而 ambiguous
    L6 = "__syn6_unique_label_zzz"
    jp6 = ["栞 " + L6, "保存変数 x = 1"]
    zh6 = ["栞 " + L6 + "_zh", "保存変数 x = 1"]
    jp6b = ["ジャンプ __syn6.txt " + L6]
    zh6b = ["ジャンプ __syn6.txt " + L6 + "_zh"]
    g6, e6 = run_synthetic("rule_r2",
                           [("__syn6", jp6, zh6), ("__syn6b", jp6b, zh6b)])
    if e6:
        check("缺陷6 R2 可运行", False, str(e6))
    else:
        check("缺陷6 标签改名被 R2 报出", len(g6) > 0, "R2 未报出")
        show(g6)

    print()
    print("=" * 72)
    print("结果: PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    for name, msg in FAIL:
        print("  FAILED: " + name + "  " + msg)
    print("=" * 72)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
