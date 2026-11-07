# -*- coding: utf-8 -*-
"""R5 逻辑键一致性。

核心思想：只有「被 == / != 比较过的变量」才做检查。
纯显示用途的赋值（例如 スキル効果 = 中文说明）永远没有比较点，
因此不会被本规则误报 —— 这是任务里明确要求避免的误报。

检查三类：

1. logic_key_translated  high
   某个比较键在日文原版里是被赋过值的（分支本来能成立），
   在成品里该变量已被赋值成中文、这个键再也没有任何赋值点。
   等价于「逻辑键被翻译掉了，分支永远不成立」。

2. logic_key_mismatch    mid
   同一个键在成品侧也没有赋值点，但不是「被翻译成中文」的情况：
   - 成品该变量存在 {{...}} 动态赋值 -> severity_note='ambiguous'，
     静态集合不完整，运行时是否还能取到该键无法静态判定，不硬判成错误。
   - 否则视为键被删除/改写，留作中危供人工确认。

   判定前置条件（缺一不可），保证只在「原版真的能成立」时才报：
   键在成品里没有赋值点 + 键在原版有赋值点 + 键在原版被比较过。

3. dynamic_label_var     low（信息性）
   被当作 ジャンプ / スクリプト / 最終ジャンプ 的栞名使用的变量，
   列出它在原版/成品里的字面量赋值集合，便于核对每个取值都能对应到
   目标脚本里存在的 栞。
   两侧都没有字面量赋值的变量（引擎注入/存档读取/跨脚本赋值）不输出，
   避免制造无法核对的噪声。

只读：仅通过 lvctx / lvparse 读取语料，不写任何文件。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lvparse as P
import lvfind
import rule_r3

# 假名 -> 判定「日文原文」
KANA_RE = re.compile(r'[\u3040-\u309f\u30a0-\u30ff]')
# 汉字 -> 判定「中文」（中文没有假名，见 is_chinese）
CJK_RE = re.compile(r'[\u4e00-\u9fff]')

MAX_LIST = 8


def _is_chinese(s):
    """汉字且不含假名，视为中文/中文混合。"""
    return bool(CJK_RE.search(s)) and not KANA_RE.search(s)


def _is_japanese(s):
    """含假名即视为日文原文。"""
    return bool(KANA_RE.search(s))


def _clip_list(items, n=MAX_LIST):
    """格式化候选值列表，超出截断。"""
    vals = sorted(items)
    shown = vals[:n]
    text = ' / '.join(repr(v) for v in shown)
    if len(vals) > n:
        text += ' ...（共 %d 个）' % len(vals)
    return text


def _where(sites, n=5):
    return ', '.join('%s:%d' % (s[0], s[1]) for s in sites[:n])


def _is_dynamic(vals):
    """赋值集合里出现 {{...}} 说明运行时可拼出更多取值，静态集合不完整。"""
    return any('{{' in v for v in vals)


def _pick_site(zside, var, key):
    """返回该变量比较该键的首次出现位置 (fid, 1-based 行号)。"""
    slots = zside['cmp_where'].get((var, key)) or []
    if slots:
        return slots[0][0], slots[0][1]
    fallback = zside['use_where'].get(var) or []
    if fallback:
        return fallback[0][0], fallback[0][1]
    return None, None


def _line_cmp(line):
    """解析单行的条件比较键 -> {var: set(keys)}。"""
    side = rule_r3._new_side()
    try:
        rule_r3._scan_line(line, side, '-', 0)
    except Exception:
        return {}
    return {k: set(v) for k, v in side['cmp'].items()}


def _cmp_diff_scan(ctx):
    """逐行比对同一行里被比较的字符串字面量。

    汉化不应改动任何比较键；同一行的比较键一变，逻辑键就被改写。
    这是纯 diff 判定，不依赖赋值集合是否完整，也不针对任何特定变量名。
    """
    out = []
    for fid in ctx.fids():
        n = ctx.line_count(fid)
        for i in range(n):
            jl = ctx.jp_line(fid, i)
            zl = ctx.zh_line(fid, i)
            if not jl or not zl:
                continue
            if '==' not in jl and '!=' not in jl:
                continue
            if jl.strip() == zl.strip():
                continue
            jcmp = _line_cmp(jl)
            if not jcmp:
                continue
            zcmp = _line_cmp(zl)
            for var in sorted(jcmp):
                jk = jcmp[var]
                zk = zcmp.get(var, set())
                if jk == zk:
                    continue
                if not (zk - jk) and not (jk - zk):
                    continue
                out.append(lvfind.make(
                    'R5', 'logic_key_changed', 'high', fid, i + 1,
                    jl.strip(), zl.strip(),
                    '变量 %s 的比较键被改动：原版 %r -> 成品 %r'
                    % (var, sorted(jk), sorted(zk)),
                    severity_note='同一行的比较字面量发生变化；汉化不应改动逻辑键'))
    return out


def run(ctx):
    idx = rule_r3.build_index(ctx)
    jp, zh = idx['jp'], idx['zh']
    out = []

    # ---- 1/2) 被比较过的变量：比较键在成品里还可以被赋到吗
    #
    # 只在「被比较过」的变量上做判定，纯显示用赋值永远不进循环，
    # 因此 スキル効果 这类显示文本赋值不会被误报。
    for var in sorted(zh['cmp']):
        zkeys = zh['cmp'][var]
        if not zkeys:
            continue
        zlit = zh['assign_lit'].get(var, set())
        jlit = jp['assign_lit'].get(var, set())
        jkeys = jp['cmp'].get(var, set())
        # 成品该变量存在 {{...}} 赋值 -> 静态集合不完整
        ambiguous = var in zh['assign_dyn']

        # 键在成品里一定赋不到、且日文原版确实赋过并比较过 = 真断链
        broken = []
        for key in sorted(zkeys):
            if key in zlit:
                continue                 # 成品里仍有赋值，分支可成立
            if key not in jlit:
                continue                 # 日文原版也没赋过，原版就不成立
            if key not in jkeys:
                continue                 # 原版没比较过，无法证明曾经成立
            broken.append(key)
        if not broken:
            continue

        zh_vals = sorted(v for v in zlit if '{{' not in v)
        has_chinese = any(_is_chinese(v) for v in zh_vals)

        if ambiguous:
            code, sev = 'logic_key_mismatch', 'mid'
            reason = ('成品该变量存在动态赋值（{{...}}），静态集合不完整，'
                      '无法确定运行时是否还能取到该键')
        elif has_chinese:
            code, sev = 'logic_key_translated', 'high'
            reason = ('该比较键在原版可被赋到，成品里对应变量已被赋成中文、'
                      '这个键再无任何赋值点，分支永远不成立')
        else:
            code, sev = 'logic_key_mismatch', 'mid'
            reason = ('该键在成品侧没有任何赋值点，且成品赋值不是中文，'
                      '更像是键被删除/改写')

        for key in broken:
            fid, line_no = _pick_site(zh, var, key)
            if fid is None:
                continue
            jl = ctx.jp_line(fid, line_no - 1) or ''
            zl = ctx.zh_line(fid, line_no - 1) or ''
            detail = ('变量 %s 的比较键 %r 在成品里无法被赋到。%s。'
                      '日文原版赋值集合里有该键；成品赋值集合为 %s。' % (
                          var, key, reason, _clip_list(set(zh_vals))))
            if ambiguous:
                detail += '（ambiguous：该变量存在动态赋值模板，需人工核对运行时取值）'
            out.append(lvfind.make(
                'R5', code, sev, fid, line_no, jl.strip(), zl.strip(), detail,
                severity_note='ambiguous' if ambiguous else None))

    # ---- 3) 动态标签变量：信息性输出赋值集合
    #
    # 只输出「确有字面量赋值」的变量。两侧都没有字面量赋值的（引擎注入、
    # 存档读取、跨脚本赋值）无法静态核对标签集合，不产生噪声。
    for var in sorted(jp['label_vars']):
        sites = jp['label_vars'][var]
        if not sites:
            continue
        lits = jp['assign_lit'].get(var, set())
        zh_lits = zh['assign_lit'].get(var, set())
        if not lits and not zh_lits:
            continue
        fid, line_no, cmd, arg = sites[0]
        jl = ctx.jp_line(fid, line_no - 1) or ''
        zl = ctx.zh_line(fid, line_no - 1) or ''
        detail = ('变量 %s 被用作 %s 的栞名（共 %d 处，例：%r）。'
                  '日文原版赋值集合：%s；成品赋值集合：%s。'
                  '请核对每个取值都能对应到目标脚本里存在的 栞 标签' % (
                      var, cmd, len(sites), arg,
                      _clip_list(lits) if lits else '（无字面量赋值）',
                      _clip_list(zh_lits) if zh_lits else '（无字面量赋值）'))
        out.append(lvfind.make(
            'R5', 'dynamic_label_var', 'low', fid, line_no,
            jl.strip(), zl.strip(), detail,
            severity_note='informational'))

    out.extend(_cmp_diff_scan(ctx))
    return lvfind.finalize(out)
