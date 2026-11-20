# -*- coding: utf-8 -*-
r'''lvalign.py -- 让 linter 看见「已对齐」的成品页

背景：
  linter 的假设是 JP 与 ZH 逐行一一对应（翻译只替换行内文本，不增删行）。
  汉化过程中我们为「特性表 / 技能名表」插入了守卫行与建表块，ZH 比 JP 多若干行，
  于是 linter 从第一个守卫开始就整体错位，把后面每一行都报成 skeleton_changed /
  asset_ref_lost（实测 11,072 条里 9,680 条 = 87.4% 落在 9 个插行文件）。

本模块把成品页剥离这些「纯新增行」，还原成与 JP 等长的序列。
剥离规则与 _lint/align.py 完全一致（那边已验证 1114 文件对齐后行数与 JP 全等）。
'''
import os, re, sys
sys.stdout.reconfigure(encoding='utf-8', write_through=True)

GUARD_RE = [
    re.compile(r'^// zh-(?:trait|skillname)-guard (?:begin|end)$'),
    re.compile(r'^もし \(特性表_ready != "ON"\) スクリプト system/events\.txt 特性表初期化$'),
    re.compile(r'^もし \(技能名表_ready != "ON"\) スクリプト system/events\.txt 技能名表初期化$'),
    re.compile(r'^もし \(lvHashMapHasKey\(特性表, [^)]+\) == false\) 臨時全域変数 特性表\[[^\]]+\] = [^\s]+$'),
    re.compile(r'^もし \(lvHashMapHasKey\(技能名表, [^)]+\) == false\) 臨時全域変数 技能名表\[[^\]]+\] = [^\s]+$'),
]
BEGIN = ('//=== ZH_TRAIT_INIT BEGIN', '//=== ZH_SKILLNAME_INIT BEGIN')
END = ('//=== ZH_TRAIT_INIT END ===', '//=== ZH_SKILLNAME_INIT END ===')


def is_added(line):
    s = line.strip()
    return any(r.match(s) for r in GUARD_RE)


def align(index_text, text):
    '''返回 (keep_index, keep_text)：保留的原始下标与文本'''
    ki, kt = [], []
    skip = False
    for i, s in enumerate(text):
        t = s.strip()
        if any(t.startswith(b) for b in BEGIN):
            skip = True
            continue
        if skip:
            if any(t == e for e in END):
                skip = False
            continue
        if is_added(s):
            continue
        ki.append(index_text[i])
        kt.append(s)
    return ki, kt