# -*- coding: utf-8 -*-
"""量化：
  A) ' | ' 切分对 R3 / R4 的影响（切开 vs 不切开）
  B) 命令表加入 engine_keywords.json 后，絵0 等别名的召回率
全部只读，不落盘 corpus。
"""
import os
import sys
import random
import importlib

_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PARENT)
sys.stdout.reconfigure(encoding="utf-8")

import lvparse as P
import lvctx

Q = chr(34)
SEED = 20261107


class FakePage:
    def __init__(self, rows):
        self.prefix = [r.encode("utf-8") for r in rows]
        self.crsuf = [b"" for r in rows]

    def text(self, i):
        return self.prefix[i].decode("utf-8")


class MiniCtx:
    def __init__(self, fid, jp, zh):
        self._fid, self._jp, self._zh = fid, FakePage(jp), FakePage(zh)

    def fids(self):
        return [self._fid]

    def jp_page(self, f):
        return self._jp

    def zh_page(self, f):
        return self._zh

    def jp_line(self, f, i):
        return self._jp.text(i)

    def zh_line(self, f, i):
        return self._zh.text(i) if i < len(self._zh.prefix) else None

    def line_count(self, f):
        return len(self._jp.prefix)


class NoPipe(object):
    """上下文：临时关闭 ' | ' 切分，模拟改动前的行为。"""

    def __enter__(self):
        self.s1 = P.split_segments
        self.s2 = P.split_segments_v2

        def one_seg(s):
            return [s]

        def one_v2(s):
            return [{"seg": s, "head": s, "arg": None}]

        P.split_segments = one_seg
        P.split_segments_v2 = one_v2
        return self

    def __exit__(self, *a):
        P.split_segments = self.s1
        P.split_segments_v2 = self.s2


class NoEngineKw(object):
    """上下文：临时关闭引擎关键字表，模拟改动前的行为。"""

    def __enter__(self):
        self.k = P.engine_keywords
        self.known = P.is_known_command
        P.engine_keywords = lambda: set()
        P.is_known_command = lambda n: bool(n) and n in P.command_table()
        return self

    def __exit__(self, *a):
        P.engine_keywords = self.k
        P.is_known_command = self.known


def run_rule(modname):
    m = importlib.import_module(modname)
    return m.run(lvctx) or []


def cmd_token(line):
    s = line.lstrip(" " + chr(9) + chr(0x3000))
    while s.startswith("~"):
        s = s[1:].lstrip(" " + chr(9) + chr(0x3000))
    if not s or s.startswith(Q):
        return ""
    parts = s.split(None, 1)
    return parts[0] if parts else ""


def main():
    print("=" * 74)
    print("A) ' | ' 切分对 R3 / R4 的影响")
    print("=" * 74)
    print("  （关闭切分 = 每条物理行整体当成一条命令）")
    with NoPipe():
        r3_off = run_rule("rule_r3")
        r4_off = run_rule("rule_r4")
    r3_on = run_rule("rule_r3")
    r4_on = run_rule("rule_r4")
    print("  R3: 不切分 %d 条 -> 切分 %d 条  (差 %+d)" % (
        len(r3_off), len(r3_on), len(r3_on) - len(r3_off)))
    print("  R4: 不切分 %d 条 -> 切分 %d 条  (差 %+d)" % (
        len(r4_off), len(r4_on), len(r4_on) - len(r4_off)))
    import collections
    print("      R3 code(切分):", collections.Counter(x["code"] for x in r3_on).most_common(4))
    print("      R4 code(切分):", collections.Counter(x["code"] for x in r4_on).most_common(4))

    print()
    print("=" * 74)
    print("B) 絵0 等别名的召回率（引擎关键字表 开 / 关）")
    print("=" * 74)
    rng = random.Random(SEED)
    # 收集以「引擎关键字但不在 Commands.xml」开头的行
    alias_set = set()
    table = P.command_table()
    for kw in ("絵0", "ボタン0", "文字窓", "bookmark", "if", "cg", "var", "textbox"):
        if kw not in table:
            alias_set.add(kw)
    picked = []
    fids = lvctx.fids()
    rng.shuffle(fids)
    for fid in fids[:400]:
        n = lvctx.line_count(fid)
        for i in range(n):
            t = cmd_token(lvctx.jp_line(fid, i))
            if t in alias_set:
                picked.append((fid, i, t))
        if len(picked) >= 60:
            break
    rng.shuffle(picked)
    picked = picked[:30]
    print("  别名集合(不在 Commands.xml 中):", sorted(alias_set))
    print("  采样:", len(picked), "行")

    def recall_for(modname):
        hit = 0
        tried = 0
        for fid, idx, tok in picked:
            jp = [lvctx.jp_line(fid, k) for k in range(lvctx.line_count(fid))]
            zh = list(jp)
            zh[idx] = jp[idx].replace(tok, "中文命令", 1)
            if zh[idx] == jp[idx]:
                continue
            tried += 1
            try:
                mod = importlib.import_module(modname)
                got = mod.run(MiniCtx(fid, jp, zh)) or []
            except Exception:
                got = []
            if any(g.get("line") == idx + 1 for g in got):
                hit += 1
        return tried, hit

    for modname in ("rule_r4", "rule_r6"):
        with NoEngineKw():
            t0, h0 = recall_for(modname)
        t1, h1 = recall_for(modname)
        r0 = 100.0 * h0 / t0 if t0 else 0.0
        r1 = 100.0 * h1 / t1 if t1 else 0.0
        print("  %s  关闭引擎表 %d/%d = %.1f%%  ->  启用后 %d/%d = %.1f%%" % (
            modname, h0, t0, r0, h1, t1, r1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
