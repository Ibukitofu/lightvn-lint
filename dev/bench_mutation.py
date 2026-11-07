# -*- coding: utf-8 -*-
"""通用性基准：随机采样真实语料行 + 随机变异，统计各规则召回率。

变异类型与"已知 bug"无关，是为了检验规则是否真的在做结构判定。
全部在内存中进行，不落盘、不改动目标游戏。
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
CJK = '汉化测试'


class FakePage:
    def __init__(self, rows):
        self.prefix = [r.encode("utf-8") for r in rows]
        self.crsuf = [b"" for r in rows]

    def text(self, i):
        return self.prefix[i].decode("utf-8")


class MiniCtx:
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
        return self._zh.text(i) if i < len(self._zh.prefix) else None

    def line_count(self, fid):
        return len(self._jp.prefix)

    def command_table(self):
        return P.command_table()


def cmd_token(line):
    s = line.lstrip(" \t\u3000")
    while s.startswith("~"):
        s = s[1:].lstrip(" \t\u3000")
    if not s or s.startswith(Q):
        return ""
    parts = s.split(None, 1)
    return parts[0] if parts else ""


def mutate_command(line, table):
    tok = cmd_token(line)
    if tok not in table or len(tok) < 2:
        return None
    idx = line.index(tok)
    return line[:idx] + "中文命令" + line[idx + len(tok):]


def mutate_cmp(line):
    import re
    m = re.search(r'(==|!=)\s*"([^"]+)"', line)
    if not m:
        return None
    return line[:m.start(2)] + "汉化键" + line[m.end(2):]


def mutate_var(line):
    import re
    m = re.search(r'\{\{([A-Za-z_\u3040-\u30ff\u4e00-\u9fff][^}]*?)\}\}', line)
    if not m:
        return None
    return line[:m.start(1)] + "汉化变量" + line[m.end(1):]


def mutate_path(line):
    import re
    m = re.search(r'[^\s"|]+\.(png|ogg|wav|jpg)', line)
    if not m:
        return None
    stem, ext = m.group(0).rsplit('.', 1)
    return line[:m.start(0)] + "汉化路径." + ext + line[m.end(0):]


def pick_lines(pred, limit, rng, max_files=250):
    """在语料里挑满足 pred 的行，随机采样。"""
    fids = lvctx.fids()
    rng.shuffle(fids)
    hits = []
    for fid in fids[:max_files]:
        n = lvctx.line_count(fid)
        for i in range(n):
            line = lvctx.jp_line(fid, i)
            if pred(line):
                hits.append((fid, i))
        if len(hits) > limit * 8:
            break
    rng.shuffle(hits)
    return hits[:limit]


def run_case(modname, fid, jp_rows, zh_rows):
    try:
        mod = importlib.import_module(modname)
        got = mod.run(MiniCtx(fid, jp_rows, zh_rows)) or []
    except Exception:
        return []
    return [g for g in got if str(g.get("file")) == fid]


def evaluate(name, modname, pred, mutate, make_args, samples, rng):
    table = P.command_table()
    picked = pick_lines(pred, samples, rng)
    hit = 0
    tried = 0
    misses = []
    for fid, idx in picked:
        jp_all = [lvctx.jp_line(fid, i) for i in range(lvctx.line_count(fid))]
        original = jp_all[idx]
        broken = mutate(original, table) if make_args else mutate(original)
        if broken is None or broken == original:
            continue
        zh_all = list(jp_all)
        zh_all[idx] = broken
        tried += 1
        got = run_case(modname, fid, jp_all, zh_all)
        line_hits = [g for g in got if g.get("line") == idx + 1]
        if line_hits:
            hit += 1
        elif len(misses) < 3:
            misses.append((fid, idx + 1, original[:90]))
    rate = (100.0 * hit / tried) if tried else 0.0
    print("  %-28s 样本 %3d  检出 %3d  召回 %5.1f%%" % (name, tried, hit, rate))
    for m in misses:
        print("      漏检: %s:%d  %r" % m)
    return tried, hit, rate


def main():
    rng = random.Random(SEED)
    table = P.command_table()
    print("=" * 76)
    print("通用性基准：随机采样真实语料行 + 随机变异（种子 %d）" % SEED)
    print("=" * 76)

    print("\n[命令名被翻译]  期望 R4 或 R6 检出")
    evaluate("命令名 -> 中文命令", "rule_r4",
             lambda l: cmd_token(l) in table,
             mutate_command, True, 25, rng)
    evaluate("命令名 -> 中文命令", "rule_r6",
             lambda l: cmd_token(l) in table,
             mutate_command, True, 25, rng)

    print("\n[比较字面量被翻译]  期望 R5 检出")
    evaluate("条件比较值 -> 中文", "rule_r5",
             lambda l: ('==' in l or '!=' in l) and Q in l,
             mutate_cmp, False, 30, rng)

    print("\n[变量名被翻译]  期望 R3 或 R6 检出")
    evaluate("{{变量}} -> {{汉化变量}}", "rule_r3",
             lambda l: '{{' in l,
             mutate_var, False, 30, rng)
    evaluate("{{变量}} -> {{汉化变量}}", "rule_r6",
             lambda l: '{{' in l,
             mutate_var, False, 30, rng)

    print("\n[资源路径被翻译]  期望 R1 或 R6 检出")
    evaluate("路径 -> 汉化路径", "rule_r1",
             lambda l: ('.png' in l or '.ogg' in l or '.wav' in l),
             mutate_path, False, 25, rng)
    evaluate("路径 -> 汉化路径", "rule_r6",
             lambda l: ('.png' in l or '.ogg' in l or '.wav' in l),
             mutate_path, False, 25, rng)

    print("\n[未改动对照]  期望全部规则 0 报（误报检查）")
    for modname in ("rule_r1", "rule_r3", "rule_r4", "rule_r5", "rule_r6"):
        picked = pick_lines(lambda l: bool(l.strip()), 12, rng, max_files=60)
        fp = 0
        for fid, idx in picked:
            jp_all = [lvctx.jp_line(fid, i) for i in range(lvctx.line_count(fid))]
            got = run_case(modname, fid, jp_all, list(jp_all))
            if any(g.get("line") == idx + 1 for g in got):
                fp += 1
        print("  %-28s 对照样本 %3d  误报 %3d" % (modname, len(picked), fp))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
