# -*- coding: utf-8 -*-
"""演示：把历史真实 bug 注入真实语料行，验证 linter 检出能力。

为了跑得快，只用一个「单文件迷你 ctx」，每条规则仍调用其真实 run()。
全部在内存里进行，不落盘。
"""
import os, sys, importlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

import lvparse as P
import lvctx

Q = chr(34)


class FakePage:
    def __init__(self, rows):
        self.prefix = [r.encode("utf-8") for r in rows]
        self.crsuf = [b"" for r in rows]
    def text(self, i):
        return self.prefix[i].decode("utf-8")


class MiniCtx:
    """只有一份文件的上下文，接口与 lvctx 兼容。"""
    def __init__(self, fid, jp_rows, zh_rows):
        self._fid = fid
        self._jp = FakePage(jp_rows)
        self._zh = FakePage(zh_rows)
    def fids(self):
        return [self._fid]
    def jp_page(self, fid):
        return self._jp
    def zh_page(self, fid):
        return self._zh
    def jp_line(self, fid, i):
        return self._jp.text(i)
    def zh_line(self, fid, i):
        if i >= len(self._zh.prefix):
            return None
        return self._zh.text(i)
    def line_count(self, fid):
        return len(self._jp.prefix)
    def command_table(self):
        return P.command_table()


def demo(title, fid, idx, mutate, rules, expect="hit"):
    print("=" * 78)
    print(title)
    print("=" * 78)
    jp_all = [lvctx.jp_line(fid, i) for i in range(lvctx.line_count(fid))]
    zh_all = list(jp_all)
    original = jp_all[idx]
    broken = mutate(original)
    zh_all[idx] = broken
    print("源文件 %s.txt   行号 %d (1-based)" % (fid, idx + 1))
    print("原版 : " + original[:200])
    print("注入 : " + broken[:200])
    print()
    ctx = MiniCtx(fid, jp_all, zh_all)
    total = 0
    for mod, rname in rules:
        try:
            m = importlib.import_module(mod)
            got = m.run(ctx) or []
            got = [g for g in got if str(g.get("file")) == fid]
        except Exception as e:
            print("  [%s] 异常 %s: %s" % (rname, type(e).__name__, str(e)[:110]))
            continue
        miss = [g for g in got if g.get("line") == idx + 1]
        total += len(miss)
        if miss:
            print("  [%s] 抓到 %d 条" % (rname, len(miss)))
            for g in miss[:3]:
                print("      %-26s %s" % (g.get("code"), (g.get("detail") or "")[:130]))
        else:
            print("  [%s] 未报" % rname)
    ok = (total > 0) if expect == "hit" else (total == 0)
    print("  => 本行 %d 条  [%s]" % (total, "OK" if ok else "!!"))
    print()
    return total, ok


def main():
    results = []
    r = demo("BUG 1  ボタン 内嵌 ジャンプ -> 跳转   （回合结束按钮失效）",
             "66424", 554,
             lambda s: s.replace("ジャンプ battle0.txt ターン終了",
                                 "跳转 battle0.txt ターン終了"),
             [("rule_r6", "R6"), ("rule_r4", "R4")])
    results.append(("BUG1", r))

    r = demo("BUG 2  " + Q + "なし" + Q + " -> " + Q + "无" + Q + "   （条件永不成立 / 地图黑屏）",
             "67245", 22,
             lambda s: s.replace("会話相手 != " + Q + "なし" + Q,
                                 "会話相手 != " + Q + "无" + Q),
             [("rule_r5", "R5")])
    results.append(("BUG2", r))

    r = demo("BUG 3  变量名 地図_エニシダ -> 地図_埃尼西达   （变量不存在）",
             "67245", 123,
             lambda s: s.replace("地図_エニシダ", "地図_埃尼西达"),
             [("rule_r3", "R3"), ("rule_r6", "R6")])
    results.append(("BUG3", r))

    r = demo("BUG 4  [反向验证] スキル効果 显示文本翻译  -> 必须 0 条",
             "66422", 592,
             lambda s: s.replace("剣装備時の攻撃力", "剑装备时的攻击力"),
             [("rule_r5", "R5"), ("rule_r6", "R6")], expect="clean")
    results.append(("BUG4", r))

    r = demo("BUG 5  变量拼接资源路径 {{使用中BGM}} -> {{已翻译}}",
             "100", 4,
             lambda s: s.replace("{{使用中BGM}}", "{{已翻译}}"),
             [("rule_r1", "R1"), ("rule_r6", "R6")])
    results.append(("BUG5", r))

    r = demo("BUG 6  静态资源路径 menu/m_ターンエンド1.png -> 中文",
             "66424", 554,
             lambda s: s.replace("menu/m_ターンエンド1.png", "menu/m_回合结束1.png"),
             [("rule_r1", "R1"), ("rule_r6", "R6")])
    results.append(("BUG6", r))

    found = None
    for i in range(lvctx.line_count("66424")):
        l = lvctx.jp_line("66424", i).strip()
        if l.startswith("栞 ") and "終了" in l:
            found = (i, l.split()[1]); break
    if found:
        idx7, lab = found
        r = demo("BUG 7  栞 " + lab + " 标签被翻译   （跳转断裂）",
                 "66424", idx7,
                 lambda s: s.replace("栞 " + lab, "栞 结束"),
                 [("rule_r2", "R2"), ("rule_r6", "R6")])
        results.append(("BUG7", r))

    print("=" * 78)
    print("汇总")
    for name, (n, ok) in results:
        print("  %-6s %s  (%d 条)" % (name, "检出" if ok else "未检出", n))
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
