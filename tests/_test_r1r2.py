# -*- coding: utf-8 -*-
"""R1 / R2 自测：真实语料统计 + 合成用例验证。

只读：合成用例只替换内存里的 lvctx 缓存（FakePage），不落盘。

用法: python -X utf8 _lint/_test_r1r2.py
"""
import os
import sys
import time
import collections

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _HERE)
sys.stdout.reconfigure(encoding="utf-8")

import lvparse as P
import lvctx
import lvfind
import rule_r1
import rule_r2

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
    """最小 Page 替身：只需 prefix + text()。"""

    def __init__(self, rows):
        self.prefix = [r.encode("utf-8") for r in rows]
        self.crsuf = [b"" for r in rows]

    def text(self, i):
        return self.prefix[i].decode("utf-8")


def with_fake(pairs, mods):
    """在 lvctx 缓存里临时塞入合成文件，跑 mods 里的模块，然后恢复。"""
    corpus = lvctx._load_all()
    saved = []
    for fid, jl, zl in pairs:
        saved.append((fid, corpus["jp"].get(fid), corpus["zh"].get(fid)))
        corpus["jp"][fid] = FakePage(jl)
        corpus["zh"][fid] = FakePage(zl)
        if fid not in corpus["fids"]:
            corpus["fids"] = list(corpus["fids"]) + [fid]
    try:
        got = []
        for mod in mods:
            got.extend(mod.run(lvctx) or [])
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
    return [g for g in got if str(g.get("file", "")).startswith("__syn")]


def reset_all():
    rule_r1.reset_cache()
    rule_r2.reset_cache()


def show(got, n=4):
    for g in got[:n]:
        print("     -> %s/%s #%s  %s" % (g.get("rule"), g.get("code"),
                                          g.get("line"),
                                          (g.get("detail") or "")[:100]))


def dist(findings):
    return dict(collections.Counter(f["code"] for f in findings))


# ------------------------------------------------------------------ 真实语料

def run_real():
    print("=" * 78)
    print("一、真实语料全量")
    print("=" * 78)

    reset_all()
    t0 = time.time()
    g1 = rule_r1.run(lvctx)
    t1 = time.time()
    g2 = rule_r2.run(lvctx)
    t2 = time.time()

    print()
    print("R1 资源引用完整性：%d 条  (%.1fs)" % (len(g1), t1 - t0))
    print("   按 code：%s" % (dist(g1) or "{}"))
    for f in g1[:8]:
        print("   %-8s:%-5d %-26s %s" % (f["file"], f["line"], f["code"],
                                         f["detail"][:110]))

    print()
    print("R2 跳转引用完整性：%d 条  (%.1fs)" % (len(g2), t2 - t1))
    print("   按 code：%s" % (dist(g2) or "{}"))
    for f in g2[:8]:
        print("   %-8s:%-5d %-26s %s" % (f["file"], f["line"], f["code"],
                                         f["detail"][:110]))

    st1 = rule_r1.stats()
    print()
    print("R1 统计：")
    for k in ("files", "files_skipped_nonscript", "tokens_jp", "tokens_zh",
              "static_paths_jp", "static_paths_zh", "templates_jp", "templates_zh",
              "refpaths_baseline", "both_absent_from_baseline",
              "jp_only_absent_from_baseline", "zh_only_absent_from_baseline",
              "var_templates_no_values", "findings_total"):
        if k in st1:
            print("   %-30s %s" % (k, st1[k]))
    gaps = st1.get("baseline_gap_samples") or []
    if gaps:
        print("   %-30s %s" % ("基准缺口样例", "；".join(gaps[:4])))

    st2 = rule_r2.stats()
    print()
    print("R2 统计：")
    for k in ("calls_jp", "calls_zh", "label_defs_jp", "label_defs_zh",
              "scripts_referenced", "mapping_unique", "mapping_ambiguous",
              "mapping_unresolved", "label_missing_in_both",
              "dynamic_values_checked", "dynamic_values_unmatched_jp",
              "dynamic_unresolvable", "skipped_dynamic_file",
              "skipped_dynamic_unmapped", "skipped_unmapped_ambiguous",
              "skipped_unmapped_unresolved", "findings_total"):
        if k in st2:
            print("   %-30s %s" % (k, st2[k]))

    # ---- mapping
    print()
    print("二、脚本名 -> fid 映射")
    print("=" * 78)
    reset_all()
    t3 = time.time()
    mp = rule_r2.build_mapping(lvctx)
    print("   build_mapping %.1fs，共 %d 个脚本名" % (time.time() - t3, len(mp)))
    st = collections.Counter(v["status"] for v in mp.values())
    print("   unique     %4d" % st.get("unique", 0))
    print("   ambiguous  %4d" % st.get("ambiguous", 0))
    print("   unresolved %4d" % st.get("unresolved", 0))
    print()
    print("   ambiguous 样例（按标签数降序）：")
    for k, v in sorted(mp.items(), key=lambda kv: -kv[1]["nlabels"]):
        if v["status"] == "ambiguous":
            print("     %-46s labels=%-3d cand=%s"
                  % (k, v["nlabels"], ",".join(v["candidates"][:4])))
            if sum(1 for x in mp.values() if x["status"] == "ambiguous") > 6 and \
                    list(mp).index(k) > 40:
                break
    print()
    print("   unresolved 样例：")
    n = 0
    for k, v in sorted(mp.items(), key=lambda kv: -kv[1]["nlabels"]):
        if v["status"] == "unresolved":
            print("     %-46s labels=%-3d inventory=%s"
                  % (k, v["nlabels"], v.get("inventory_fid")))
            n += 1
            if n >= 8:
                break
    return g1, g2, mp


# ------------------------------------------------------------------ 合成用例

def run_synthetic():
    print()
    print("=" * 78)
    print("三、合成用例（内存变异，不落盘）")
    print("=" * 78)

    # ---- R1-a: 静态路径被翻译
    print()
    print("[R1-a] 把 絵 的 png 路径改成中文 → 必须报 asset_path_changed")
    jp = ["~絵 背景 menu/m_アビリティ.png 0 0 231 カメラ付着"]
    zh = ["~絵 背景 menu/m_技能.png 0 0 231 カメラ付着"]
    reset_all()
    got = with_fake([("__syn_r1a", jp, zh)], [rule_r1])
    codes = {g["code"] for g in got}
    check("R1-a 报 asset_path_changed", "asset_path_changed" in codes,
          "实际: %r" % sorted(codes))
    check("R1-a 同时报 asset_ref_lost", "asset_ref_lost" in codes,
          "实际: %r" % sorted(codes))
    show(got)

    # ---- R1-b: 路径整体消失
    print()
    print("[R1-b] 删掉 効果音 的文件名 → 必须报 asset_ref_lost")
    jp = ["~効果音 SE 選択.ogg", "~画像 a b/c.png"]
    zh = ["~効果音 SE ", "~画像 a b/c.png"]
    reset_all()
    got = with_fake([("__syn_r1b", jp, zh)], [rule_r1])
    codes = {g["code"] for g in got}
    check("R1-b 报 asset_ref_lost", "asset_ref_lost" in codes, "实际: %r" % sorted(codes))
    show(got)

    # ---- R1-c: 变量拼接路径的取值被翻译
    print()
    print("[R1-c] 絵 bg map/{{時間帯}}_村.png，变量值 夜→夜晚 → 必须报 asset_var_value_changed")
    jp = ["保存変数 時間帯 = " + Q + "夜" + Q,
          "絵 bg map/{{時間帯}}_村.png 0 0 10"]
    zh = ["保存変数 時間帯 = " + Q + "夜晚" + Q,
          "絵 bg map/{{時間帯}}_村.png 0 0 10"]
    reset_all()
    got = with_fake([("__syn_r1c", jp, zh)], [rule_r1])
    codes = {g["code"] for g in got}
    check("R1-c 报 asset_var_value_changed", "asset_var_value_changed" in codes,
          "实际: %r" % sorted(codes))
    show(got)

    # ---- R1-d: 未改动的资源行绝不能报
    print()
    print("[R1-d] 资源行完全没动 → 必须 0 条")
    jp = ["~絵 背景 menu/m_アビリティ.png 0 0 231 カメラ付着",
          "~ボイス v voice_01.ogg 音量_x バックログ登録禁止"]
    zh = list(jp)
    reset_all()
    got = with_fake([("__syn_r1d", jp, zh)], [rule_r1])
    check("R1-d 不误报", len(got) == 0, "误报 %d 条: %r" % (len(got), dist(got)))
    if got:
        show(got)

    # ---- R1-e: 注释行里的路径变化绝不能报
    print()
    print("[R1-e] // 注释行里的路径变化 → 必须 0 条")
    jp = ["//~絵 x a.png 0 0 1"]
    zh = ["//~絵 x 中文.png 0 0 1"]
    reset_all()
    got = with_fake([("__syn_r1e", jp, zh)], [rule_r1])
    check("R1-e 注释不误报", len(got) == 0, "误报 %d 条: %r" % (len(got), dist(got)))
    if got:
        show(got)

    # ---- R2-a: 栞 标签被改名
    print()
    print("[R2-a] 栞 ターン終了 → 栞 回合结束 → 必须报 jump_target_missing")
    # 标签名必须全局唯一，否则映射会判 ambiguous（真实语料里 ターン終了 是常见标签）
    jp_def = ["栞 __synlab_ターン終了", "保存変数 x = 1"]
    zh_def = ["栞 __synlab_回合结束", "保存変数 x = 1"]
    jp_call = ["もし (a == 1) ジャンプ __syn_r2a.txt __synlab_ターン終了"]
    zh_call = ["もし (a == 1) ジャンプ __syn_r2a.txt __synlab_ターン終了"]
    reset_all()
    got = with_fake([("__syn_r2a", jp_def, zh_def),
                     ("__syn_r2a_call", jp_call, zh_call)], [rule_r2])
    codes = {g["code"] for g in got}
    check("R2-a 报 jump_target_missing", "jump_target_missing" in codes,
          "实际: %r" % sorted(codes))
    show(got)

    # ---- R2-b: 同文件内标签改名（ジャンプ <label> 省略文件）
    print()
    print("[R2-b] 同文件 ジャンプ <label>，标签改名 → 必须报 jump_target_missing")
    jp = ["栞 __synlab_表示_即時", "保存変数 x = 1", "ジャンプ __synlab_表示_即時"]
    zh = ["栞 __synlab_立即显示", "保存変数 x = 1", "ジャンプ __synlab_表示_即時"]
    reset_all()
    got = with_fake([("__syn_r2b", jp, zh)], [rule_r2])
    codes = {g["code"] for g in got}
    check("R2-b 报 jump_target_missing", "jump_target_missing" in codes,
          "实际: %r" % sorted(codes))
    show(got)

    # ---- R2-c: 按钮里嵌套命令的标签
    print()
    print("[R2-c] ボタン 引号内嵌套 ジャンプ，目标标签改名 → 必须报 jump_target_missing")
    jp_def = ["栞 __synlab_変換決定", "保存変数 y = 2"]
    zh_def = ["栞 __synlab_确认转换", "保存変数 y = 2"]
    jp_call = ["~ボタン b a.png a.png a.png 1 2 3 カメラ付着 "
               + Q + "もし (x == " + Q + "OFF" + Q + ") ジャンプ __syn_r2c.txt __synlab_変換決定" + Q]
    zh_call = list(jp_call)
    reset_all()
    got = with_fake([("__syn_r2c", jp_def, zh_def),
                     ("__syn_r2c_call", jp_call, zh_call)], [rule_r2])
    codes = {g["code"] for g in got}
    check("R2-c 报 jump_target_missing", "jump_target_missing" in codes,
          "实际: %r" % sorted(codes))
    show(got)

    # ---- R2-d: 动态标签的变量值被翻译
    print()
    print("[R2-d] スクリプト t.txt 説明_{{スキル名}}，スキル名 的赋值被翻译")
    print("        → 必须报 jump_dynamic_missing")
    # 目标文件里必须有一个「静态可辨识」的锚标签，否则映射无法确定 fid
    jp_def = ["栞 __synlab_説明_強打", "栞 __synlab_説明_裂斬",
              "栞 __synlab_锚点", "保存変数 x = 1"]
    zh_def = ["栞 __synlab_説明_強打", "栞 __synlab_説明_裂斬",
              "栞 __synlab_锚点", "保存変数 x = 1"]
    jp_call = ["臨時全域変数 __synvar_スキル名 = " + Q + "強打" + Q,
               "臨時全域変数 __synvar_スキル名 = " + Q + "裂斬" + Q,
               "ジャンプ __syn_r2d.txt __synlab_锚点",
               "スクリプト __syn_r2d.txt __synlab_説明_{{__synvar_スキル名}}"]
    zh_call = ["臨時全域変数 __synvar_スキル名 = " + Q + "强力打击" + Q,
               "臨時全域変数 __synvar_スキル名 = " + Q + "裂斬" + Q,
               "ジャンプ __syn_r2d.txt __synlab_锚点",
               "スクリプト __syn_r2d.txt __synlab_説明_{{__synvar_スキル名}}"]
    reset_all()
    got = with_fake([("__syn_r2d", jp_def, zh_def),
                     ("__syn_r2d_call", jp_call, zh_call)], [rule_r2])
    codes = {g["code"] for g in got}
    check("R2-d 报 jump_dynamic_missing", "jump_dynamic_missing" in codes,
          "实际: %r" % sorted(codes))
    show(got)

    # ---- R2-e: 原版就悬挂的标签绝不能报
    print()
    print("[R2-e] 原版就没有的标签（悬挂引用）→ 必须 0 条")
    jp = ["ジャンプ __syn_r2e.txt __synlab_不存在的标签"]
    zh = ["ジャンプ __syn_r2e.txt __synlab_不存在的标签"]
    reset_all()
    got = with_fake([("__syn_r2e", ["栞 __synlab_其他"], ["栞 __synlab_其他"]),
                     ("__syn_r2e_call", jp, zh)], [rule_r2])
    check("R2-e 悬挂引用不误报", len(got) == 0,
          "误报 %d 条: %r" % (len(got), dist(got)))
    if got:
        show(got)

    # ---- R2-f: 显示文本里的「跳转」字样绝不能报
    print()
    print("[R2-f] 文字 ... " + Q + "ロード" + Q + " 显示文本 → 必须 0 条")
    jp = ["~文字 t 454 207 81 Corporate_Yawamin.ttf 24 カメラ付着 " + Q + "ロード" + Q]
    zh = ["~文字 t 454 207 81 Corporate_Yawamin.ttf 24 カメラ付着 " + Q + "加载" + Q]
    reset_all()
    got = with_fake([("__syn_r2f", jp, zh)], [rule_r2])
    check("R2-f 显示文本不误报", len(got) == 0,
          "误报 %d 条: %r" % (len(got), dist(got)))
    if got:
        show(got)


def main():
    g1, g2, mp = run_real()
    run_synthetic()

    print()
    print("=" * 78)
    print("结果: PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    for name, msg in FAIL:
        print("  FAILED: " + name + "  " + msg)
    print("=" * 78)
    print("数字汇总：R1=%d  R2=%d  mapping unique=%d ambiguous=%d unresolved=%d"
          % (len(g1), len(g2),
             sum(1 for v in mp.values() if v["status"] == "unique"),
             sum(1 for v in mp.values() if v["status"] == "ambiguous"),
             sum(1 for v in mp.values() if v["status"] == "unresolved")))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
