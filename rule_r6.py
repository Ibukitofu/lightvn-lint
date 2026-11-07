# -*- coding: utf-8 -*-
"""R6 代码骨架一致性。

把双引号里的「显示文本」挖掉后，比较日文原版与汉化成品的代码骨架：
  - 相同 -> 引号外没被改动（显示文本翻译是安全的）
  - 不同 -> skeleton_changed：命令名/变量名/数字/路径/运算符被改动

行尾引号参数若本身是嵌套命令（如 ボタン / タッチ終了時 / 変数式確認機），
会递归展开再挖空，因此「引号内嵌命令被翻译」同样能被抓到。
"""
import os, sys, difflib, re

import lvparse as P
import lvfind

RULE = "R6"

_TABLE = None


def _table():
    global _TABLE
    if _TABLE is None:
        _TABLE = P.command_table()
    return _TABLE


def _strip_lead(s):
    s = s.lstrip(" \t\u3000")
    while s.startswith("~"):
        s = s[1:].lstrip(" \t\u3000")
    return s


def _starts_with_command(text):
    return P._starts_with_command(text)


def _lvlib():
    import lvlib
    return lvlib


def is_command_line(line):
    s = _strip_lead(line)
    if not s or s.startswith(chr(34)):
        return False
    tok = re.split(r"[ \t\u3000\"]+", s, maxsplit=1)[0]
    return P.is_known_command(tok) or tok in ("栞", "もし", "違ってももし", "違ったら", "bookmark", "if")



def _line_command(line):
    """该行第一个命令名（去掉 ~ 前缀）。"""
    s = _strip_lead(line)
    if not s or s.startswith(chr(34)):
        return ""
    tok = re.split(r"[ \t\u3000\"]+", s, maxsplit=1)[0]
    return tok


def skeleton(line, depth=0):
    """整行骨架：挖空「显示文本」，但保留「嵌套命令」的代码结构。"""
    if depth > 4:
        return " ".join(P.blank_quotes(line).split())
    pieces = []
    _inner_cmd = _line_command(line)
    _nested_ok = bool(_inner_cmd) and P.needs_nested_parse(_inner_cmd)
    for it in P.split_segments_v2(line):
        head = it.get("head") or ""
        arg = it.get("arg")
        hs = " ".join(P.blank_quotes(head).split())
        if arg is None:
            pieces.append(hs)
            continue
        if _nested_ok and _starts_with_command(arg):
            # 嵌套命令：递归取骨架（而不是整段挖掉）
            pieces.append(hs + " {S:" + skeleton(arg, depth + 1) + "}")
        else:
            # 纯显示文本：整段挖掉
            pieces.append(hs + " {T}")
    return "\x01".join(p for p in pieces if p.strip())


def _first_diff(a, b, pad=48):
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        left = a[max(0, i1 - pad):i2 + pad]
        right = b[max(0, j1 - pad):j2 + pad]
        return ("第 %d 字符起（%s）：JP %r -> ZH %r"
                % (i1, tag, left, right))
    return ""


def run(ctx):
    out = []
    for fid in ctx.fids():
        zpg = ctx.zh_page(fid)
        if zpg is None:
            continue
        text_lines = set()
        try:
            blocks = _lvlib().parse_blocks(ctx.jp_page(fid))
        except Exception:
            blocks = []
        for b in blocks:
            if b.get('kind') == 'dlg':
                for seg in b.get('segs', []):
                    text_lines.add(seg['line'])
        n = min(ctx.line_count(fid), len(zpg.prefix))
        for i in range(n):
            jline = ctx.jp_line(fid, i)
            zline = ctx.zh_line(fid, i)
            if zline is None:
                continue
            if i in text_lines:
                continue                        # 纯对话文本，不比对骨架
            if (not jline.strip()) and (not zline.strip()):
                continue
            jst = jline.strip()
            zst = zline.strip()
            if jst.startswith('//') or zst.startswith('//'):
                continue
            if jst.startswith('~【') or jst.startswith('【'):
                continue                        # 说话人标记，本来就该翻译
            # 非对话行即视为代码行，直接比对骨架
            jb = skeleton(jline)
            zb = skeleton(zline)
            if jb == zb:
                continue
            out.append(lvfind.make(
                RULE, "skeleton_changed", "high", fid, i + 1,
                jline, zline,
                "引号外的代码骨架被改动。" + _first_diff(jb, zb),
                severity_note="骨架里 {T}=显示文本（已挖空），{S:...}=嵌套命令（保留结构）"))
    return out
