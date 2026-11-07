# -*- coding: utf-8 -*-
"""R2：跳转引用完整性（栞 标签 / ジャンプ / スクリプト / 最終ジャンプ）。

核心难点：脚本名（battle0.txt 之类）到数字 fid（66424.mcdat）的映射。
原版把 1,079 个脚本打成数字文件，inventory.json 只覆盖 738 条且不完整
（battle0.txt / ability.txt 都不在里面），所以本模块自己用「标签集合匹配」
建立映射：

  对每个被引用的脚本名，收集所有「指向它的期望标签」；
  在所有 fid 里找「标签集合包含这些期望标签」的候选。
  唯一 → unique；多命中 → ambiguous；零命中 → unresolved。

只有 unique 的映射才用来判错；ambiguous / unresolved 只统计，不判错。

判错口径（关键，用于压误报）：
  * 只在「日文原版成立、成品不成立」时报告。
  * 静态标签：调用点写的标签在日文目标 fid 里存在，但在成品目标 fid 里消失
    → jump_target_missing。
  * 两边都不存在 → 是原版就有的悬挂引用（例如 map0.txt 的 主人公表情_），
    或映射选错了 fid —— 一律不报，只计入 stats。
  * 动态标签：{{var}} 代入后，日文能命中的标签在成品里不再命中 → jump_dynamic_missing。

只读：不写目标游戏的任何文件。
"""
import collections
import re

import lvparse as P
import lvfind
from rule_r1 import (parse_cmd, walk_segments, strip_comment, _clean_arg,
                     assignment_values, _literal, template_vars, nonscript_fids)
R1_ASSIGN = ('保存変数', '変数', '臨時全域変数', '全域変数', '変数式確認機')

RULE = 'R2'

JUMP_CMDS = ('ジャンプ', '最終ジャンプ', 'スクリプト')
LABEL_CMD = '栞'
WS = P.WS

_CACHE = {}

# 期望标签里，全局唯一度高的标签辨识力更强
STRONG_FREQ = 3


# ---------------------------------------------------------------- 采集

def _is_comment_line(line):
    s = line.strip()
    while True:
        t = s.lstrip(WS)
        if t.startswith('~'):
            s = t[1:]
            continue
        s = t
        break
    return s.startswith('//')


def labels_of(ctx, side):
    """{fid: set(栞 标签)}，只取日文/成品脚本（跳过着色器与 JSON）。"""
    key = ('labels', side)
    if key in _CACHE:
        return _CACHE[key]
    getter = ctx.jp_line if side == 'jp' else ctx.zh_line
    nonscript = nonscript_fids(ctx)
    out = {}
    for fid in ctx.fids():
        if fid in nonscript:
            out[fid] = set()
            continue
        ls = set()
        n = ctx.line_count(fid)
        for i in range(n):
            line = getter(fid, i)
            if not line or '栞' not in line:
                continue
            if _is_comment_line(line):
                continue
            for seg in walk_segments(line):
                cmd, args, _cond = parse_cmd(seg['head'])
                if cmd == LABEL_CMD and args:
                    lab = _clean_arg(args[0])
                    if lab:
                        ls.add(lab)
        out[fid] = ls
    _CACHE[key] = out
    return out


def calls_of(ctx, side='jp'):
    """收集全部跳转调用。

    返回 list of dict(fid, line, cmd, file, label, dynamic_file, dynamic_label)
    file 为 None 表示「当前文件」（ジャンプ <label> 形式）。
    """
    key = ('calls', side)
    if key in _CACHE:
        return _CACHE[key]
    getter = ctx.jp_line if side == 'jp' else ctx.zh_line
    nonscript = nonscript_fids(ctx)
    out = []
    for fid in ctx.fids():
        if fid in nonscript:
            continue
        n = ctx.line_count(fid)
        for i in range(n):
            line = getter(fid, i)
            if not line:
                continue
            # 快速预筛：行内必须出现某个跳转命令名
            if not any(c in line for c in JUMP_CMDS):
                continue
            if _is_comment_line(line):
                continue
            for seg in walk_segments(line):
                cmd, args, _cond = parse_cmd(seg['head'])
                if cmd not in JUMP_CMDS or not args:
                    continue
                a = [_clean_arg(x) for x in args]
                if not a:
                    continue
                if a[0].lower().endswith('.txt'):
                    target, label = a[0], (a[1] if len(a) > 1 else None)
                else:
                    target, label = None, a[0]
                if not label:
                    # ジャンプ <file>（无标签）= 从文件开头跑，合法
                    continue
                out.append({
                    'fid': fid, 'line': i + 1, 'cmd': cmd,
                    'file': target, 'label': label,
                    'dynamic_file': bool(target and '{{' in target),
                    'dynamic_label': '{{' in label,
                })
    _CACHE[key] = out
    return out


def _label_freq(labels):
    """{标签: 出现在多少个 fid 里}"""
    key = ('labfreq', id(labels))
    if key in _CACHE:
        return _CACHE[key]
    freq = collections.defaultdict(int)
    for ls in labels.values():
        for l in ls:
            freq[l] += 1
    _CACHE[key] = freq
    return freq


def _inventory_map():
    key = ('inventory',)
    if key in _CACHE:
        return _CACHE[key]
    m = {}
    try:
        inv = P.load_inventory()
        for k, v in inv.items():
            p = v.get('path')
            if p and p not in m:
                m[p] = k
    except Exception:
        m = {}
    _CACHE[key] = m
    return m


# ---------------------------------------------------------------- 映射

def _resolve_one(script, expected, labels, labfreq, invmap, all_fids):
    """给一个脚本名找 fid。返回 (fid|None, status, candidates)。"""
    # 只保留「全局至少定义过一次」的标签：原版就悬挂的标签帮不上辨识
    useful = [l for l in expected if labfreq.get(l, 0) > 0]
    if not useful:
        return None, 'unresolved', []
    # 稀有标签优先，能最快收敛
    useful.sort(key=lambda l: (labfreq.get(l, 0), l))

    cand = set(all_fids)
    for l in useful:
        cand &= {f for f in all_fids if l in labels[f]}
        if not cand:
            break

    inv_fid = invmap.get(script)

    if len(cand) == 1:
        return next(iter(cand)), 'unique', sorted(cand)
    if len(cand) > 1 and inv_fid in cand:
        return inv_fid, 'unique', sorted(cand)

    # 放宽：只用强标签（全局出现 <= STRONG_FREQ 个文件）
    strong = [l for l in useful if labfreq.get(l, 0) <= STRONG_FREQ]
    if strong and strong != useful:
        c2 = set(all_fids)
        for l in strong:
            c2 &= {f for f in all_fids if l in labels[f]}
            if not c2:
                break
        if len(c2) == 1:
            return next(iter(c2)), 'unique', sorted(c2)
        if len(c2) > 1 and inv_fid in c2:
            return inv_fid, 'unique', sorted(c2)
        if c2:
            cand = c2

    if cand:
        return None, 'ambiguous', sorted(cand)[:8]
    return None, 'unresolved', []


def build_mapping(ctx):
    """脚本名 -> {fid, status, candidates, nlabels, sample_labels}。

    只做日文侧（原版是权威），成品侧用同样的 fid 号即可（行对齐 100%）。
    """
    key = ('mapping', id(ctx))
    if key in _CACHE:
        return _CACHE[key]

    labels = labels_of(ctx, 'jp')
    labfreq = _label_freq(labels)
    invmap = _inventory_map()
    all_fids = list(ctx.fids())

    expected = collections.defaultdict(set)
    for c in calls_of(ctx, 'jp'):
        f = c['file']
        if not f or c['dynamic_file'] or c['dynamic_label']:
            continue
        expected[f].add(c['label'])

    mapping = {}
    for script in sorted(expected):
        fid, status, cand = _resolve_one(script, expected[script], labels,
                                         labfreq, invmap, all_fids)
        mapping[script] = {
            'fid': fid,
            'status': status,
            'candidates': cand,
            'nlabels': len(expected[script]),
            'sample_labels': sorted(expected[script])[:5],
            'inventory_fid': invmap.get(script),
        }
    _CACHE[key] = mapping
    return mapping


# ---------------------------------------------------------------- 变量展开

def _expand(tpl, values, max_rounds=3):
    """把模板里的 {{var}} 用赋值表展开；返回 (展开集合, 是否有未解析变量)。"""
    cur = {tpl}
    unresolved = False
    for _ in range(max_rounds):
        nxt = set()
        changed = False
        for s in cur:
            toks = P.var_tokens(s)
            if not toks:
                nxt.add(s)
                continue
            expr = toks[0].strip()
            if not re.match(r'^[^\s+\-*/%<>=!&|()]+$', expr):
                # 表达式型占位符：无法静态求值
                nxt.add(s.replace('{{' + toks[0] + '}}', '\x00', 1))
                unresolved = True
                changed = True
                continue
            vs = _literal(values.get(expr, set()))
            if not vs:
                nxt.add(s.replace('{{' + toks[0] + '}}', '\x00', 1))
                unresolved = True
                changed = True
                continue
            for v in vs:
                nxt.add(s.replace('{{' + toks[0] + '}}', v, 1))
            changed = True
        cur = nxt
        if not changed:
            break
    concrete = {s for s in cur if '\x00' not in s}
    return concrete, unresolved


# ---------------------------------------------------------------- 规则主体

def reset_cache():
    """清空模块级缓存（自测里替换语料后必须调用）。"""
    _CACHE.clear()
    LAST_STATS.clear()


def _changed_assignment_sites(ctx, var):
    """找出某个变量「日文取值 vs 成品取值」不一致的赋值行，返回 [(fid,line,jp,zh)]。"""
    jp_vals = assignment_values(ctx, 'jp')
    zh_vals = assignment_values(ctx, 'zh')
    jv = _literal(jp_vals.get(var, set()))
    zv = _literal(zh_vals.get(var, set()))
    only_zh = zv - jv
    if not only_zh:
        return []
    out = []
    for fid in ctx.fids():
        n = ctx.line_count(fid)
        for i in range(n):
            zl = ctx.zh_line(fid, i)
            if not zl or var not in zl:
                continue
            for seg in walk_segments(zl):
                cmd, args, _cond = parse_cmd(seg['head'])
                if cmd not in R1_ASSIGN or len(args) < 3:
                    continue
                if args[0] != var or args[1] != '=':
                    continue
                val = ' '.join(args[2:])
                if val in only_zh:
                    out.append((fid, i + 1, (ctx.jp_line(fid, i) or '').strip(), zl.strip()))
                    if len(out) >= 5:
                        return out
    return out


LAST_STATS = {}


def stats():
    return dict(LAST_STATS)


def run(ctx):
    findings = []
    st = collections.Counter()

    mapping = build_mapping(ctx)
    st['mapping_unique'] = sum(1 for m in mapping.values() if m['status'] == 'unique')
    st['mapping_ambiguous'] = sum(1 for m in mapping.values() if m['status'] == 'ambiguous')
    st['mapping_unresolved'] = sum(1 for m in mapping.values() if m['status'] == 'unresolved')
    st['scripts_referenced'] = len(mapping)

    jp_labels = labels_of(ctx, 'jp')
    zh_labels = labels_of(ctx, 'zh')
    jp_calls = calls_of(ctx, 'jp')
    zh_calls = calls_of(ctx, 'zh')
    st['calls_jp'] = len(jp_calls)
    st['calls_zh'] = len(zh_calls)

    jp_vals = assignment_values(ctx, 'jp')
    zh_vals = assignment_values(ctx, 'zh')

    # 成品调用按 (fid,line,cmd) 建索引，便于取成品原文做报告
    zh_index = {}
    for c in zh_calls:
        zh_index.setdefault((c['fid'], c['line'], c['cmd']), []).append(c)

    # ---------- 1) 静态标签
    both_samples = []
    for c in jp_calls:
        if c['dynamic_label']:
            continue
        fid, ln, cmd, lab = c['fid'], c['line'], c['cmd'], c['label']
        target = c['file']

        if target is None or c['dynamic_file']:
            # 省略文件 = 当前文件；动态文件名无法确定目标
            if target is None:
                tgt_fid = fid
            else:
                st['skipped_dynamic_file'] += 1
                continue
        else:
            info = mapping.get(target)
            if not info or info['status'] != 'unique' or not info['fid']:
                st['skipped_unmapped_%s' % (info['status'] if info else 'unresolved')] += 1
                continue
            tgt_fid = info['fid']

        in_jp = lab in jp_labels.get(tgt_fid, set())
        in_zh = lab in zh_labels.get(tgt_fid, set())
        if in_jp and not in_zh:
            jp_line = ctx.jp_line(fid, ln - 1) or ''
            zh_line = ctx.zh_line(fid, ln - 1) or ''
            where = ('当前文件' if target is None else '目标 %s(fid %s)' % (target, tgt_fid))
            findings.append(lvfind.make(
                RULE, 'jump_target_missing', 'high', fid, ln,
                jp=jp_line, zh=zh_line,
                detail='跳转目标标签「%s」在 %s 中已不存在（原版存在）。'
                       '该跳转会落到错误位置或直接卡死。' % (lab, where)))
        elif not in_jp and not in_zh:
            # 原版就悬挂（或映射选错 fid）—— 不判错，只统计
            st['label_missing_in_both'] += 1
            if len(both_samples) < 15:
                both_samples.append({'file': fid, 'line': ln, 'cmd': cmd,
                                     'target': target, 'label': lab,
                                     'tgt_fid': tgt_fid})

    # ---------- 2) 动态标签
    for c in jp_calls:
        if not c['dynamic_label']:
            continue
        fid, ln, cmd, lab = c['fid'], c['line'], c['cmd'], c['label']
        target = c['file']

        if target is not None and not c['dynamic_file']:
            info = mapping.get(target)
            if not info or info['status'] != 'unique' or not info['fid']:
                st['skipped_dynamic_unmapped'] += 1
                continue
            tgt_fid = info['fid']
        elif target is None:
            tgt_fid = fid
        else:
            st['skipped_dynamic_file'] += 1
            continue

        jp_exp, jp_unknown = _expand(lab, jp_vals)
        zh_exp, zh_unknown = _expand(lab, zh_vals)
        if not jp_exp:
            st['dynamic_unresolvable'] += 1
            continue

        tgt_jp = jp_labels.get(tgt_fid, set())
        tgt_zh = zh_labels.get(tgt_fid, set())

        # 只关心「原版能命中」的那些取值
        jp_hit = {v for v in jp_exp if v in tgt_jp}
        zh_hit = {v for v in zh_exp if v in tgt_zh}
        missing = jp_hit - zh_hit

        # 原版自身就命不中的取值（运行时才产生的）不参与判断
        st['dynamic_values_checked'] += len(jp_exp)
        st['dynamic_values_unmatched_jp'] += len(jp_exp) - len(jp_hit)

        if missing:
            jp_line = ctx.jp_line(fid, ln - 1) or ''
            zh_line = ctx.zh_line(fid, ln - 1) or ''
            shown = sorted(missing)
            head = shown[:8]
            detail = ('动态跳转标签「%s」代入变量后有 %d 个取值在目标 fid %s 里找不到：%s%s。'
                      % (lab, len(shown), tgt_fid,
                         '、'.join('「%s」' % v for v in head),
                         ' 等' if len(shown) > len(head) else ''))
            if zh_unknown:
                detail += '（部分变量取值无法静态确定，已跳过。）'

            # 根因定位：哪个变量的赋值被翻译了
            causes = []
            for var in sorted(set(template_vars(lab))):
                sites = _changed_assignment_sites(ctx, var)
                if sites:
                    causes.append((var, sites))
            if causes:
                parts = []
                for var, sites in causes:
                    s0 = sites[0]
                    parts.append('变量 {{%s}} 的赋值被翻译（如 %s.txt:%d「%s」→「%s」，共 %d 处）'
                                 % (var, s0[0], s0[1], s0[2][:60], s0[3][:60], len(sites)))
                detail += '根因：' + '；'.join(parts) + '。'
            findings.append(lvfind.make(
                RULE, 'jump_dynamic_missing', 'high', fid, ln,
                jp=jp_line, zh=zh_line, detail=detail,
                severity_note='动态标签的取值来自变量赋值；赋值被翻译后，标签拼不出来，'
                              '跳转会落到脚本开头或直接报错。'))

    # ---------- 3) 统计
    codes = collections.Counter(f['code'] for f in findings)
    for k, v in codes.items():
        st['findings_' + k] = v
    st['findings_total'] = len(findings)
    st['label_defs_jp'] = sum(len(v) for v in jp_labels.values())
    st['label_defs_zh'] = sum(len(v) for v in zh_labels.values())
    st['both_missing_samples'] = both_samples
    st['fids_with_label_loss'] = len({
        f['file'] for f in findings if f['code'] == 'jump_target_missing'})

    LAST_STATS.clear()
    LAST_STATS.update(st)
    return lvfind.finalize(findings)
