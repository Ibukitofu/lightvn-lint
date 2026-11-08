# -*- coding: utf-8 -*-
"""R8 续行台词未翻译。

形态（Light.vn）：
    ~もし (条件) -"台词\w     条件 + 续行台词
    -"台词\w                 无条件续行台词

减号后的引号表示「接上一句台词的延续」。这类行的分布很广（约 2,150 条），
一旦抽取器只认 " 开头而漏掉 -"，整批会保持日文，玩家看到中一日一日交替。

与 R7 的区别：
  R7  查两侧相同的行是否仍是日文（通用漏翻）
  R8  专门定位 -" 形态，并在两侧不同时也检查译文里是否夹着日文
"""
import re

import lvfind

RULE = "R8"

QUOTE = chr(34)
DQ = '-' + QUOTE
WS = ' \t\u3000'

KANA_RE = re.compile(r'[\u3040-\u309f\u30a0-\u30ff]')
VAR_RE = re.compile(r'\{\{[^}]*\}\}')
TAG_RE = re.compile(r'<[^<>]*>')
ESC_RE = re.compile(r'\\[a-z/]')

MIN_KANA = 2


def _strip_lead(s):
    s = s.lstrip(WS)
    while s[:1] in ('~', '.'):
        s = s[1:].lstrip(WS)
    return s


def _after_condition(s):
    """跳过 もし ( ... ) 条件括号，返回其后的内容；不是条件命令则原样返回。"""
    if not s.startswith('もし'):
        return s
    i = 2
    n = len(s)
    while i < n and s[i] in WS:
        i += 1
    if i >= n or s[i] != '(':
        return s
    d = 0
    while i < n:
        if s[i] == '(':
            d += 1
        elif s[i] == ')':
            d -= 1
            if d == 0:
                return s[i+1:].lstrip(WS)
        i += 1
    return s


def is_continuation(line):
    """该行是否为 -" 续行台词。"""
    return _after_condition(_strip_lead(line)).startswith(DQ)


def body_text(line):
    s = _after_condition(_strip_lead(line))
    if s.startswith(DQ):
        s = s[2:]
    s = VAR_RE.sub('', TAG_RE.sub('', s))
    return ESC_RE.sub('', s).strip()


def run(ctx):
    out = []
    for fid in ctx.fids():
        zpg = ctx.zh_page(fid)
        if zpg is None:
            continue
        n = min(ctx.line_count(fid), len(zpg.prefix))
        for i in range(n):
            jp = ctx.jp_line(fid, i)
            if not jp or not is_continuation(jp):
                continue
            zh = ctx.zh_line(fid, i) or ''
            body_jp = body_text(jp)
            if len(body_jp) < 3:
                continue
            body_zh = body_text(zh) if zh else ''
            kana_jp = len(KANA_RE.findall(body_jp))
            kana_zh = len(KANA_RE.findall(body_zh))
            if jp == zh:
                if kana_jp < MIN_KANA:
                    continue
                out.append(lvfind.make(
                    RULE, 'continuation_untranslated', 'mid', fid, i + 1,
                    jp=jp, zh=zh,
                    detail='续行台词未翻译（%d 个假名）：%s'
                           % (kana_jp, body_jp[:60]),
                    severity_note='该行 -" 形态，原版与成品完全相同'))
            else:
                # 已改动，但译文里仍残留假名
                if kana_zh < MIN_KANA:
                    continue
                out.append(lvfind.make(
                    RULE, 'continuation_partial', 'mid', fid, i + 1,
                    jp=jp, zh=zh,
                    detail='续行台词译文残留日文（%d 个假名）：%s'
                           % (kana_zh, body_zh[:60]),
                    severity_note='该行已改动，但译文里仍有假名'))
    return out
