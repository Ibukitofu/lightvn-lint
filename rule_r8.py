# -*- coding: utf-8 -*-
"""R8 译文对齐检测。

检查「译文和原文对不上」。前三类结构检查（R1-R6）和未翻译检测（R7）
都通过时，仍然可能出现这种问题：这一行的中文是别的行的内容。

最典型的成因是批量翻译时模型把相邻几条窗口的译文合并/串位，
于是「原文是喘息拟声，译文却是上一段的叙述」。

判据（纯结构，不依赖任何项目数据）
--------------------------------
    本条译文 <> 前面 1~4 条译文拼接    （字符覆盖率 >= COV_ZH）
    且 本条原文 <> 那几条原文           （字符覆盖率 <  COV_JP）

只有「译文像、原文不像」才是错位；原文本身重复的拟声台词会被第二
个条件排除。

为什么不用长度/句数
------------------
错位后的译文长度可能完全正常（比如把上一段的一条换成另一段的一条），
按长度比例判断抓不到。字符覆盖比对的是「这段字是不是那些字拼的」，
与长度无关。

已知局限
--------
- 短拟声台词（「啊啊啊」「不要不要」）前后天然高度相似，两个条件会同时
  触发，因此漏检。这类只能靠语义审校。
- 只能发现「复用前文」；若内容来自别的文件或别的场景，抓不到。
"""
import os
import re

import lvfind

RULE = "R8"

QUOTE = chr(34)

# 归一化时剥掉的东西：标签、占位符、转义、空白与标点
NOISE_RE = re.compile(r'<[^<>]*>|\{\{[^}]*\}\}|\\[a-z/]')
PUNCT_RE = re.compile(r'[\s\u3000。、！？…ー〜～♪♥・「」『』（）()]')

# 判据参数（各项含义见下方注释）
# 阈值可由环境变量 LV_R8_COV 覆盖（0~1，默认 0.75）。
# 调高 -> 只报高度重合的；调低 -> 更灵敏但误报增加。
COV_ZH = 0.75    # 译文的字符有多少能在前文译文里找到（可由 LV_R8_COV 覆盖）
try:
    COV_ZH = float(os.environ.get('LV_R8_COV', COV_ZH))
except (TypeError, ValueError):
    pass
COV_JP = 0.50        # 原文相似到这个程度就视为「原文本身重复」，放过
MAX_SPAN = 4         # 最多向前看几条
MAX_LINE_GAP = 14    # 两条之间的行号间隔上限
MIN_ZH_LEN = 20      # 译文太短没有可比信息
MIN_PREV_LEN = 15    # 前文拼接太短不可靠



def norm(s):
    return PUNCT_RE.sub('', NOISE_RE.sub('', s or ''))


def cov(sub, whole):
    """sub 的字符有多少能在 whole 里找到（多重集扣除）。

    用扣除而不是 in 判断，是因为中文的「的」「了」重复度高，
    简单包含会造成大量误报。
    """
    if not sub:
        return 0.0
    pool = list(whole)
    hit = 0
    for c in sub:
        if c in pool:
            pool.remove(c)
            hit += 1
    return hit / len(sub)


HAN_RE = re.compile(r'[\u4e00-\u9fff]')
KANA_RE = re.compile(r'[\u3040-\u309f\u30a0-\u30ff]')


def _is_onomatopoeia(jp, zh):
    """原文和译文都是拟声/喘息时，前后雷同属于正常，不该报。"""
    jh = len(HAN_RE.findall(jp))
    jk = len(KANA_RE.findall(jp))
    if jh > 1 or jk < 4 or jk / max(1, jh + jk) < 0.7:
        return False
    zh_han = HAN_RE.findall(zh)
    if not zh_han:
        return True
    distinct = len(set(zh_han))
    # 拟声译文的用字重复度极高（「啊啊啊」「不要不要」）
    return distinct <= max(3, len(zh_han) // 4)


def _is_dialogue(raw):

    t = (raw or '').lstrip()
    while t[:1] in ('~', '.'):
        t = t[1:].lstrip()
    return t.startswith(QUOTE) or t.startswith('-' + QUOTE)


def run(ctx):
    out = []
    for fid in ctx.fids():
        zpg = ctx.zh_page(fid)
        if zpg is None:
            continue
        n = min(ctx.line_count(fid), len(zpg.prefix))

        # 先收集本文件里的台词行（按行号升序），R8 只在台词内比对
        items = []
        for i in range(n):
            jp = ctx.jp_line(fid, i)
            if not jp or not _is_dialogue(jp):
                continue
            zh = ctx.zh_line(fid, i)
            if zh is None or not norm(zh):
                continue
            items.append((i, jp, zh))
        if len(items) < 2:
            continue

        cache = [(i, jp, zh, norm(zh), norm(jp)) for i, jp, zh in items]
        for k in range(1, len(cache)):
            i, jp, zh, vz, vj = cache[k]
            if len(vz) < MIN_ZH_LEN:
                continue
            for span in range(1, max(1, min(MAX_SPAN, k)) + 1):
                prev = cache[k - span:k]
                if i - prev[-1][0] > MAX_LINE_GAP:
                    continue
                vp = norm(''.join(p[2] for p in prev))
                if len(vp) < MIN_PREV_LEN:
                    continue
                c = cov(vz, vp)
                if c < COV_ZH:
                    continue
                # 原文层面必须「不像」，否则是原文本身重复
                jp_prev = norm(''.join(p[1] for p in prev))
                if cov(vj, jp_prev) >= COV_JP:
                    continue
                if _is_onomatopoeia(jp, zh):
                    continue
                out.append(lvfind.make(
                    RULE, 'content_reused', 'high', fid, i + 1, jp=jp, zh=zh,
                    detail='译文与前面 %d 条译文高度重合（%.0f%% 的字符来自前文），'
                           '但原文并不相似：疑似把别处的译文用到了这一行'
                           % (span, c * 100),
                    severity_note='玩家会看到与本行原文无关的台词'))
                break
    return out
