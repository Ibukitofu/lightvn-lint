# -*- coding: utf-8 -*-
"""干净测量：' | ' 切分对 R3 / R4 的影响（每次运行前清空索引缓存）。"""
import os
import sys
import importlib
import collections

_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PARENT)
sys.stdout.reconfigure(encoding="utf-8")

import lvparse as P
import lvctx
import rule_r3


class NoPipe(object):
    def __enter__(self):
        self.s1, self.s2 = P.split_segments, P.split_segments_v2

        def one(s):
            return [s]

        def one2(s):
            return [{"seg": s, "head": s, "arg": None}]

        P.split_segments = one
        P.split_segments_v2 = one2
        return self

    def __exit__(self, *a):
        P.split_segments, P.split_segments_v2 = self.s1, self.s2


def run_clean(modname):
    try:
        rule_r3.reset_cache()
    except Exception:
        pass
    m = importlib.import_module(modname)
    return m.run(lvctx) or []


def main():
    print("=" * 72)
    print("' | ' 切分影响（每轮清空缓存）")
    print("=" * 72)
    for modname, label in (("rule_r3", "R3"), ("rule_r4", "R4")):
        with NoPipe():
            off = run_clean(modname)
        on = run_clean(modname)
        print("  %s: 不切分 %3d 条  ->  切分 %3d 条  (差 %+d)" % (
            label, len(off), len(on), len(on) - len(off)))
        print("     不切分 code:", collections.Counter(x["code"] for x in off).most_common(4))
        print("     切分   code:", collections.Counter(x["code"] for x in on).most_common(4))
        # 差异样例
        off_keys = set((x.get("file"), x.get("line"), x.get("code")) for x in off)
        on_keys = set((x.get("file"), x.get("line"), x.get("code")) for x in on)
        new = sorted(on_keys - off_keys)[:5]
        gone = sorted(off_keys - on_keys)[:5]
        if new:
            print("     切分后新增:", new)
        if gone:
            print("     切分后消失:", gone)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
