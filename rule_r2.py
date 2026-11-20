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

动态标签判据（2026-11-18 修正，原判据是错的）：
  原判据拿「日文取值展开集」减「中文取值展开集」：
      missing = {v for v in jp_exp if v in tgt_jp} - {v for v in zh_exp if v in tgt_zh}
  日文标签串「説明_ぶっ叩く」与中文标签串「説明_猛击」语言不同，永远不可能相等，
  只要该值被翻译过就必然报错 —— 这是判据错误，不是缺陷（实测 39 条全部是它）。

  正确口径是「按赋值行对齐」：
    对变量 X，在**全库**逐行找出「JP 与 ZH 同一行都有 X 的字符串赋值」的行；
    若 JP 侧取值 v 的标签 前缀+v+后缀 存在（在目标 fid 的 JP 标签集里），
    则 ZH 侧同一行的取值 w 的标签 前缀+w+后缀 必须存在（在目标 fid 的 ZH 标签集里）。
  变量的赋值点常常不在跳转点所在文件里（例：跳转在 66424、赋值全在 66428），
  所以必须全库找 owner，不能只看调用点文件。

  无法静态判定的情形（不报错，只计数）：
    * 该变量在全库找不到任何「JP/ZH 同行都有字面量赋值」的行；
    * 模板里含表达式型占位符（如 {{a+b}}）。
  这两种是「证据不足」，不是「已确认断链」，按「宁可不报」处理。

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
            # 用共享的 iter_jump_refs：它同时覆盖
            #   * 引号内的嵌套命令（ボタン "もし (...) ジャンプ ..."）
            #   * **引号外的参数链**（~ボタン ... カメラ付着 ジャンプ ability.txt 閉じる）
            # 后者在语料里有 5,000+ 处，旧实现完全看不见 —— 这正是 BUG7 漏检的原因。
            for cmd, target, label in P.iter_jump_refs(line):
                if not label:
                    continue
                out.append({
                    'fid': fid, 'line': i + 1, 'cmd': cmd,
                    'file': target, 'label': _clean_arg(label),
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


# ---------------------------------------------------------------- 动态标签：按赋值行对齐

# 全库「同一行两侧都有字符串赋值」索引：
#   {变量名: {(fid, 行下标): ([jp 值...], [zh 值...])}}
# 只收「同一行 JP 与 ZH 都出现」的行 —— 这是唯一能把日文取值与中文取值对上号的锚点。
# 一次全库扫描建好，避免每个变量重复扫（332 个模板 × 全库 = 太慢）。
_LITERAL_ASSIGN_INDEX = None
_INDEX_KEY = None

# 赋值命令名（字符串赋值只出现在这些命令后）
_ASSIGN_CMDS = ('保存変数', '変数', '臨時全域変数', '全域変数')

# 一次匹配：命令名 + 变量名 + '=' + 引号字符串
_ASSIGN_SCAN = re.compile(
    r'(?:保存変数|変数|臨時全域変数|全域変数)\s+'
    r'([^\s=]+?)\s*=(?!=)\s*"([^"]*)"')


def _build_literal_index(ctx):
    """一次全库扫描：{(fid, i): {var: [(jp_val, zh_val), ...]}} 的反向索引。"""
    idx = collections.defaultdict(lambda: collections.defaultdict(list))
    for fid in ctx.fids():
        zpg = ctx.zh_page(fid)
        if zpg is None:
            continue
        n = min(ctx.line_count(fid), len(zpg.prefix))
        for i in range(n):
            jl = ctx.jp_line(fid, i) or ''
            if '=' not in jl:
                continue
            jhits = _ASSIGN_SCAN.findall(jl)
            if not jhits:
                continue
            zl = zpg.text(i) or ''
            zhits = dict(_ASSIGN_SCAN.findall(zl)) if zl else {}
            for var, jv in jhits:
                zv = zhits.get(var)
                if zv is None:
                    continue
                idx[var][(fid, i)].append((jv, zv))
    return dict(idx)


def _literal_assign_rows(ctx, var):
    """某个变量的「JP/ZH 同行字面量赋值」：{(fid, 行下标): [(jp,zh), ...]}"""
    global _LITERAL_ASSIGN_INDEX, _INDEX_KEY
    key = (id(ctx), len(ctx.fids()), ctx.fids()[0], ctx.fids()[-1])
    if _LITERAL_ASSIGN_INDEX is None or _INDEX_KEY != key:
        _LITERAL_ASSIGN_INDEX = _build_literal_index(ctx)
        _INDEX_KEY = key
    return _LITERAL_ASSIGN_INDEX.get(var, {})


def _split_template(tpl):
    """把模板拆成 prefix + 变量槽序列 + suffix，并判断是否全为简单变量。

    返回 (prefix, suffix, [(lit_before, var), ...]) 或 None（含表达式型占位符）。
    """
    toks = list(P.VAR_RE.finditer(tpl))
    if not toks:
        return None
    simple = re.compile(r'^[^\s+\-*/%<>=!&|()]+$')
    slots = []
    last = 0
    for m in toks:
        inner = m.group(1).strip()
        if not simple.match(inner):
            return None
        slots.append((tpl[last:m.start()], inner))
        last = m.end()
    return slots, tpl[last:]


def _aligned_dynamic_missing(ctx, tpl, tgt_fid):
    """按赋值行对齐判定动态标签是否断链。

    返回 (missing 列表, 已检查行数, 说明)。
    missing 里每项是 dict(var, values, jp_label, zh_value, fid, line)。
    """
    parsed = _split_template(tpl)
    if parsed is None:
        return [], 0, 'template-expression'
    slots, suffix = parsed
    vars_ = []
    for _lit, v in slots:
        if v not in vars_:
            vars_.append(v)

    row_maps = {v: _literal_assign_rows(ctx, v) for v in vars_}
    if not all(row_maps.values()):
        return [], 0, 'no-aligned-assignment-rows'

    # 多变量模板：取各变量都有对齐行的 (fid, 行) 交集，且各变量的行号必须一致
    common = None
    for v in vars_:
        keys = set(row_maps[v].keys())
        common = keys if common is None else (common & keys)
    if not common:
        return [], 0, 'no-common-aligned-row'

    tgt_jp = _get_label_set(ctx, tgt_fid, 'jp')
    tgt_zh = _get_label_set(ctx, tgt_fid, 'zh')

    # 第一个槽的取值是可对齐的那个；其余槽保持 {{变量}} 原样，
    # 这样 jlab/zlab 只在「原版确实存在该标签」时才参与比较。
    missing = []
    checked = 0
    for (fid, i) in sorted(common):
        pairs = row_maps[vars_[0]][(fid, i)]
        for jv, zv in pairs:
            jlab = _render(slots, jv)
            zlab = _render(slots, zv)
            if jlab not in tgt_jp:
                continue
            checked += 1
            if zlab in tgt_zh:
                continue
            missing.append({
                'var': vars_[0], 'jp_value': jv, 'zh_value': zv,
                'jp_label': jlab, 'zh_label': zlab, 'fid': fid, 'line': i + 1,
            })
    return missing, checked, 'ok'


def _render(slots, value):
    """替换第一个槽的取值，其余槽保留 {{变量}} 原样。

    只对齐第一个槽：多变量模板的其余变量没有同样可靠的「同行两侧」锚点，
    硬拼会制造新的假阳性，所以宁可只检查一个槽并如实统计。
    """
    lit, _var = slots[0]
    tail = ''.join(l + '{{' + v + '}}' for l, v in slots[1:])
    return lit + value + tail


_LABEL_SETS = {}


def _get_label_set(ctx, fid, side):
    key = (fid, side)
    if key in _LABEL_SETS:
        return _LABEL_SETS[key]
    sets = labels_of(ctx, side)
    _LABEL_SETS.update({(f, side): s for f, s in sets.items()})
    return _LABEL_SETS.get(key, set())


# ---------------------------------------------------------------- 规则主体

def reset_cache():
    """清空模块级缓存（自测里替换语料后必须调用）。"""
    global _LITERAL_ASSIGN_INDEX, _INDEX_KEY
    _CACHE.clear()
    _LABEL_SETS.clear()
    _LITERAL_ASSIGN_INDEX = None
    _INDEX_KEY = None
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

        jp_exp, _jp_unknown = _expand(lab, jp_vals)
        zh_exp, _zh_unknown = _expand(lab, zh_vals)
        tgt_jp = jp_labels.get(tgt_fid, set())
        tgt_zh = zh_labels.get(tgt_fid, set())

        # 旧口径只留作对照统计：它拿日文串减中文串，语言不同必然非空，不判错。
        jp_hit_old = {v for v in jp_exp if v in tgt_jp}
        zh_hit_old = {v for v in zh_exp if v in tgt_zh}
        st['legacy_setdiff_would_report'] += len(jp_hit_old - zh_hit_old)
        st['dynamic_values_checked'] += len(jp_exp)

        # 正确口径：按赋值行对齐（详见文件头「动态标签判据」）
        missing, checked, why = _aligned_dynamic_missing(ctx, lab, tgt_fid)
        st['dynamic_aligned_checked'] += checked
        st['dynamic_aligned_broken'] += len(missing)
        st['dynamic_%s' % why] += 1

        if missing:
            jp_line = ctx.jp_line(fid, ln - 1) or ''
            zh_line = ctx.zh_line(fid, ln - 1) or ''
            head = missing[:8]
            detail = ('动态跳转标签「%s」按赋值行对齐检查：有 %d 个取值在目标 fid %s 里'
                      '断了链。例如 %s。' % (
                          lab, len(missing), tgt_fid,
                          '；'.join('%s.txt:%d「%s」原版有标签「%s」，成品同位置取值「%s」'
                                    '拼出的「%s」在目标脚本里找不到'
                                    % (m['fid'], m['line'], m['var'],
                                       m['jp_label'], m['zh_value'], m['zh_label'])
                                    for m in head)))
            if len(missing) > len(head):
                detail += ' 等 %d 个。' % len(missing)
            findings.append(lvfind.make(
                RULE, 'jump_dynamic_missing', 'high', fid, ln,
                jp=jp_line, zh=zh_line, detail=detail,
                severity_note='动态标签的取值来自变量赋值，按赋值行对齐后发现该取值的'
                              '中文标签在目标脚本里不存在；跳转会落到脚本开头或直接报错。'))

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
