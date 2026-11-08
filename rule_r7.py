# -*- coding: utf-8 -*-
"""R7 未翻译检测。

比对日文原版与成品：**这一行两边完全相同**，但正文里仍有假名，
说明该行的译文没有写回去（翻译丢失），玩家会直接看到日文。

与 R6 的区别：
  R6  查「引号外」的代码骨架是否被改动
  R7  查「引号内」的显示文本是否漏翻（两侧相同 = 没被翻译）

只报告台词行与非命令开头的裸文本行。命令行、注释、着色器、
■开头的作者标注一律跳过，避免大量误报。
"""
import os, re

import lvparse as P
import lvfind

RULE = "R7"

QUOTE = chr(34)
WS = ' \t\u3000'

# 假名（含小字），用作"仍是日文"的判据
KANA_RE = re.compile(r'[\u3040-\u309f\u30a0-\u30ff]')
VAR_RE = re.compile(r'\{\{[^}]*\}\}')
TAG_RE = re.compile(r'<[^<>]*>')
ESC_RE = re.compile(r'\\[a-z/]')

# 至少这么多个假名才算"没翻译"——单个假名可能是「ッ」这类语气残留
MIN_KANA = 2

# [xxx] 是作者留的占位名（如 [あいうえおかきくけこ]），不是待译文本
PLACEHOLDER_RE = re.compile(r'\[[^\]]*\]')

# 这些假名在中文里常用来表拟声/语气，残留不算漏译
OK_KANA = set('っー・゛゜あいうえおん')


def _strip_prefix(line):
    s = line.lstrip(WS)
    while s[:1] in ('~', '.'):
        s = s[1:].lstrip(WS)
    return s


def _skip_condition(s):
    """跳过 もし ( ... ) 条件括号，返回其后的内容。"""
    if not s.startswith('もし'):
        return s
    i = s.find('(')
    if i < 0:
        return s
    d = 0
    for k in range(i, len(s)):
        if s[k] == '(':
            d += 1
        elif s[k] == ')':
            d -= 1
            if d == 0:
                return s[k+1:].lstrip(WS)
    return s


def body_text(line):
    """取出这一行里"给人看"的部分，剥离占位符/标签/转义。"""
    s = _strip_prefix(line)
    s = _skip_condition(s)
    if s.startswith('-' + QUOTE):
        s = s[1:]
    if s.startswith(QUOTE):
        s = s[1:]
    s = PLACEHOLDER_RE.sub('', VAR_RE.sub('', TAG_RE.sub('', s)))
    return ESC_RE.sub('', s).strip()


def classify_line(line):
    """返回 'dlg' / 'text' / None。None 表示不检查。"""
    s = _strip_prefix(line)
    if not s:
        return None
    if s.startswith('//'):
        return None
    if s.startswith('#'):
        return None                       # 着色器
    if s.startswith('\u25a0'):
        return None                       # ■ 作者标注
    if s.startswith(QUOTE) or s.startswith('-' + QUOTE):
        return 'dlg'
    # 条件命令后面直接跟台词（如 ~もし (x == 1) "……台词），也是显示文本
    _rest = _skip_condition(s)
    if _rest is not s and (_rest.startswith(QUOTE) or _rest.startswith('-' + QUOTE)):
        return 'dlg'
    if s.startswith('|'):
        return None                       # 命令续行
    if s.startswith('【'):
        return None                       # 说话人标签
    tok = re.split(r'[ \t\u3000"\u3010]+', s, maxsplit=1)[0]
    if not tok:
        return None
    if P.is_known_command(tok):
        return None                       # 命令行的显示文本由 R6/R4 覆盖
    if tok.startswith('((') or tok.startswith('=='):
        return None                       # 表达式片段
    if re.match(r'^[A-Za-z0-9_.$]+[_.][A-Za-z0-9_.$]+$', tok):
        return None                       # 标识符/资源名/表情名
    if re.match(r'^[''ARTICLE　 ]*[0-9]+$', s):
        return None                       # 纯数字参数
    if tok.isascii() and not KANA_RE.search(tok):
        return None                       # 英文/路径/参数
    return 'text'


def run(ctx, baseline=None):
    """ctx 提供 jp/zh 两侧；baseline 为 None 时只比对两侧是否相同。"""
    out = []
    for fid in ctx.fids():
        zpg = ctx.zh_page(fid)
        if zpg is None:
            continue
        n = min(ctx.line_count(fid), len(zpg.prefix))
        for i in range(n):
            jp = ctx.jp_line(fid, i)
            zh = ctx.zh_line(fid, i)
            if zh is None:
                continue
            kind = classify_line(jp)
            if kind is None:
                continue
            if jp != zh:
                # 已改动，但译文里仍残留假名（多半是漏译了一部分）
                if kind != 'dlg':
                    continue
                zb = body_text(zh)
                kz = [c for c in KANA_RE.findall(zb) if c not in OK_KANA]
                if len(kz) < MIN_KANA:
                    continue
                out.append(lvfind.make(
                    RULE, 'partial_translation', 'mid', fid, i + 1,
                    jp=jp, zh=zh,
                    detail='译文残留日文（%d 个假名）：%s'
                           % (len(kz), zb[:60]),
                    severity_note='该行已改动，但译文里仍有假名，说明只译了一部分'))
                continue
            body = body_text(jp)
            if len(body) < 3:
                continue
            kana = KANA_RE.findall(body)
            if len(kana) < MIN_KANA:
                continue
            # 非台词行要求更严：至少 6 个假名，且出现日文标点，避免误收参数/资源名
            if kind == 'text':
                if len(kana) < 6:
                    continue
                if not re.search(r'[、。「」！？]', body):
                    continue
            sev = 'mid' if kind == 'dlg' else 'low'
            what = '台词' if kind == 'dlg' else '正文'
            out.append(lvfind.make(
                RULE, 'untranslated_line', sev, fid, i + 1,
                jp=jp, zh=zh,
                detail='%s仍是日文（%d 个假名）：%s' % (what, len(kana), body[:60]),
                severity_note='原版与成品该行完全相同，说明译文没有写回'))
    return out
