# -*- coding: utf-8 -*-
"""R1：资源引用完整性（贴图 / 音频 / 视频 / 字体路径）。

资源在成品里是打包成数字 .mcdat 的（66,596 PNG / 4,908 OGG / 59 RIFF），
原版加密时已剥离原始文件名，既没有「文件名 -> 文件号」映射也无法反推，
所以本规则**不做「文件是否存在」校验**，只做两件事：

1. 「原版脚本里的路径集合」vs「汉化成品里的路径集合」差异比对；
2. 含 {{变量}} 的拼接路径，把变量取值代入后再比对。

因此本规则能回答的唯一问题是：翻译过程有没有把「脚本里写的资源路径」改坏。
它不保证资源真的在包里（那是打包问题，不是翻译问题）。

只读：不写目标游戏的任何文件。
"""
import os
import re
import collections

import lvparse as P
import lvfind

RULE = 'R1'

# 路径扩展名。规格给定的清单 + mpg/mpeg（動画 命令真实使用 .mpg，见 72717.txt:4）
EXT_LIST = ('png', 'ogg', 'wav', 'jpg', 'jpeg', 'mp3', 'webm', 'mp4',
            'ani', 'bmp', 'ttf', 'otf', 'mpg', 'mpeg')
ASSET_EXT_RE = re.compile(r'\.(?:' + '|'.join(EXT_LIST) + r')(?![A-Za-z0-9])', re.I)
PATH_RE = re.compile(r'[^\s"|]+\.(?:' + '|'.join(EXT_LIST) + r')(?![A-Za-z0-9])', re.I)

# 资源类命令（参数里带文件名）
ASSET_CMDS = frozenset((
    '画像', '絵', '絵0', '背景', 'ボイス', '背景音', '効果音', '動画',
    'アニメ登録', '音声絵', 'タイル', 'ムービー', '背景ボイス', '文字0',
))

# 赋值类命令
ASSIGN_CMDS = ('保存変数', '変数', '臨時全域変数', '全域変数', '変数式確認機')

WS = P.WS
_SIMPLE_VAR = re.compile(r'^[^\s+\-*/%<>=!&|()]+$')

# ---------------------------------------------------------------- 共享工具
# 说明：rule_r2 直接复用这里的 parse_cmd / walk_segments / strip_comment，
# 保证两条规则的「命令解析」口径完全一致。


def strip_comment(line):
    """去掉引号外的 // 注释尾巴（引号内的 // 属于文本，保留）。"""
    if '//' not in line:
        return line
    spans = P.quote_spans(line)
    i, n = 0, len(line)
    while i < n - 1:
        k = line.find('//', i)
        if k < 0:
            return line
        inside = False
        for a, b in spans:
            if a < k < b:
                inside = True
                break
        if not inside:
            return line[:k]
        i = k + 2
    return line


def _match_paren(s, start):
    """s[start] == '('；返回匹配 ')' 之后的下标，失败返回 -1。"""
    depth = 0
    inq = False
    i = start
    n = len(s)
    while i < n:
        c = s[i]
        if c == '\\':
            i += 2
            continue
        if c == '"':
            inq = not inq
        elif not inq:
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    return i + 1
        i += 1
    return -1


def parse_cmd(head):
    """解析一段的行首命令。

    比 lvparse.parse_segment 多处理两种真实语料里的写法：
      * 多个 ~ 前缀（'~もし (...) ~スクリプト ...'）
      * 连续多个 もし(...) 前缀（全库 12 处）

    返回 (command, args, cond)；纯文本段返回 ('', [], cond)。
    """
    t = head.strip(WS)
    conds = []
    for _ in range(4):
        t2 = t.lstrip(WS)
        while t2.startswith('~'):
            t2 = t2[1:].lstrip(WS)
        kw = None
        if t2.startswith('違ってももし'):
            kw = '違ってももし'
        elif t2.startswith('もし'):
            kw = 'もし'
        if not kw:
            t = t2
            break
        rest = t2[len(kw):].lstrip(WS)
        if not rest.startswith('('):
            t = t2
            break
        end = _match_paren(rest, 0)
        if end <= 0:
            t = t2
            break
        conds.append(rest[1:end - 1].strip())
        t = rest[end:]
    t = t.lstrip(WS)
    if not t or t.startswith('"'):
        return '', [], (' && '.join(conds) if conds else None)
    m = re.match(r'[^ \t\u3000"]+', t)
    if not m:
        return '', [], (' && '.join(conds) if conds else None)
    return m.group(0), P.argv(t[m.end():]), (' && '.join(conds) if conds else None)


def walk_segments(text, max_depth=4):
    """按 | 切分，并递归展开行尾引号内的嵌套命令（返回所有层级的 segment）。"""
    out = []
    if max_depth < 0:
        return out
    for seg in P.split_segments_v2(text):
        out.append(seg)
        if seg['arg'] is not None:
            out.extend(walk_segments(seg['arg'], max_depth - 1))
    return out


def _clean_arg(s):
    """去掉参数里的 // 注释尾巴与首尾空白。"""
    k = s.find('//')
    if k >= 0:
        s = s[:k]
    return s.strip(WS)


def nonscript_fids(ctx):
    """着色器（#version 开头）与 JSON 数据块（{ 或 [ 开头）不是脚本。"""
    key = ('nonscript', id(ctx))
    if key in _CACHE:
        return _CACHE[key]
    out = set()
    for fid in ctx.fids():
        first = ''
        n = ctx.line_count(fid)
        for i in range(min(5, n)):
            t = ctx.jp_line(fid, i)
            if t and t.strip():
                first = t.strip()
                break
        if first.startswith('#version') or first.startswith('{') or first.startswith('['):
            out.add(fid)
    _CACHE[key] = out
    return out


_CACHE = {}


def assignment_values(ctx, side):
    """全局赋值表：{变量名: set(字面量取值)}。

    只收集 '=' 赋值的右值；含 {{ }}、( )、空格的视为表达式，原样保留在集合里
    （调用方用 _literal 过滤），以便统计口径稳定。
    """
    key = ('assign', id(ctx), side)
    if key in _CACHE:
        return _CACHE[key]
    getter = ctx.jp_line if side == 'jp' else ctx.zh_line
    table = collections.defaultdict(set)
    for fid in ctx.fids():
        n = ctx.line_count(fid)
        for i in range(n):
            line = getter(fid, i)
            if not line or '=' not in line:
                continue
            s = line.strip()
            if not s or s.startswith('//'):
                continue
            # 快速预筛：行内必须出现某个赋值命令名
            if not any(c in line for c in ASSIGN_CMDS):
                continue
            for seg in walk_segments(line):
                cmd, args, _cond = parse_cmd(seg['head'])
                if cmd not in ASSIGN_CMDS or len(args) < 3:
                    continue
                if args[1] != '=':
                    continue
                table[args[0]].add(' '.join(args[2:]))
    _CACHE[key] = table
    return table


def _literal(values):
    """挑出可以代入路径模板的字面量取值。"""
    out = set()
    for v in values:
        if not v:
            continue
        if '{{' in v or '(' in v or ')' in v or ' ' in v:
            continue
        out.add(v)
    return out


def template_vars(tpl):
    """路径模板里的简单变量名（表达式型占位符跳过）。"""
    out = []
    for expr in P.var_tokens(tpl):
        e = expr.strip()
        if _SIMPLE_VAR.match(e):
            out.append(e)
    return out


def primary_asset_arg(cmd, args):
    """用 Commands.xml 的 asset_pos 取主资源参数；取不到时回退到「带扩展名的参数」。"""
    sig = P.signature(cmd)
    pos = sig.get('asset_pos') if sig else None
    if pos is not None and pos < len(args):
        return args[pos]
    for a in args:
        if ASSET_EXT_RE.search(a):
            return a
    return None


# ---------------------------------------------------------------- 规则主体

def _load_refpaths():
    key = ('refpaths',)
    if key in _CACHE:
        return _CACHE[key]
    import json
    try:
        with open(P.REFPATHS, encoding='utf-8') as fh:
            data = json.load(fh)
        out = set(data) if isinstance(data, dict) else set(data)
    except Exception:
        out = set()
    _CACHE[key] = out
    return out


def _tokens_of(line):
    """一行里出现的全部资源路径 token（保持出现顺序）。"""
    body = strip_comment(line)
    if not body:
        return []
    return PATH_RE.findall(body)


def _is_dialogue(line):
    """纯对话行（去 ~ / もし(...) 后以引号开头）——文本内容不参与资源路径比对。

    用 parse_cmd 判定而不是简单 startswith('"')，这样
    'もし (x == 1) "台词"' 这类条件对话也能正确识别。
    """
    cmd, _args, _cond = parse_cmd(line)
    if cmd:
        return False
    # cmd == '' 有两种：真对话（引号开头）和空行/纯条件行
    s = line.lstrip(WS)
    while True:
        t = s.lstrip(WS)
        if t.startswith('~'):
            s = t[1:]
            continue
        if t.startswith('もし') or t.startswith('違ってももし'):
            kw = 'もし' if t.startswith('もし') else '違ってももし'
            rest = t[len(kw):].lstrip(WS)
            if rest.startswith('('):
                end = _match_paren(rest, 0)
                if end > 0:
                    s = rest[end:]
                    continue
        s = t
        break
    return s.startswith('"')


def reset_cache():
    """清空模块级缓存（自测里替换语料后必须调用）。"""
    _CACHE.clear()
    LAST_STATS.clear()


LAST_STATS = {}


def stats():
    """最近一次 run() 的统计（供报告/自测使用）。"""
    return dict(LAST_STATS)


def run(ctx):
    findings = []
    refpaths = _load_refpaths()
    nonscript = nonscript_fids(ctx)
    fids = ctx.fids()

    st = collections.Counter()
    st['files'] = len(fids)
    st['files_skipped_nonscript'] = len(nonscript)
    st['refpaths_baseline'] = len(refpaths)

    # 全局集合（用于「两边都没有」「行间移动」判断）
    jp_all = collections.defaultdict(list)   # path -> [(fid,line)]
    zh_all = collections.defaultdict(list)
    jp_tpl_all = collections.defaultdict(list)
    zh_tpl_all = collections.defaultdict(list)

    # 先扫一遍收集全局集合，同时保留每行 token 便于逐行比对
    jp_lines = {}
    zh_lines = {}
    for fid in fids:
        if fid in nonscript:
            continue
        n = ctx.line_count(fid)
        for i in range(n):
            for side, getter, store_all, store_tpl, store_line in (
                    ('jp', ctx.jp_line, jp_all, jp_tpl_all, jp_lines),
                    ('zh', ctx.zh_line, zh_all, zh_tpl_all, zh_lines)):
                line = getter(fid, i)
                if not line:
                    continue
                s = line.strip()
                if not s or s.startswith('//'):
                    continue
                toks = _tokens_of(line)
                if not toks:
                    continue
                if _is_dialogue(line):
                    st['skipped_dialogue_paths'] += len(toks)
                    continue
                store_line[(fid, i + 1)] = toks
                for tk in toks:
                    if '{{' in tk:
                        store_tpl[tk].append((fid, i + 1))
                    else:
                        store_all[tk].append((fid, i + 1))
                    st['tokens_' + side] += 1

    st['static_paths_jp'] = len(jp_all)
    st['static_paths_zh'] = len(zh_all)
    st['templates_jp'] = len(jp_tpl_all)
    st['templates_zh'] = len(zh_tpl_all)

    # ---------- 1) 逐行静态路径比对（行对齐 100%，所以行号可直接对应）
    jp_static_set = set(jp_all)
    for key in sorted(set(jp_lines) | set(zh_lines)):
        fid, ln = key
        a = jp_lines.get(key, [])
        b = zh_lines.get(key, [])
        if a == b:
            continue
        sa = {t for t in a if '{{' not in t}
        sb = {t for t in b if '{{' not in t}
        ta = {t for t in a if '{{' in t}
        tb = {t for t in b if '{{' in t}

        # a) 成品里新出现的静态路径
        for p in sorted(sb - sa):
            in_base = p in refpaths
            if p in jp_static_set:
                # 原版别处也有 -> 只是行间移动，不是被翻译
                findings.append(lvfind.make(
                    RULE, 'asset_path_changed', 'mid', fid, ln,
                    jp=' | '.join(a), zh=' | '.join(b),
                    detail='资源路径在行间移动：%s（原版其他行有同一路径，未丢失）' % p,
                    severity_note='路径仍在原版集合内，通常只是排版调整；确认无引用丢失即可。'))
                continue
            sev = 'high' if not in_base else 'low'
            note = None if not in_base else '该路径不在原版基准集合 _refpaths2.json 内（基准缺口），仅作提示。'
            findings.append(lvfind.make(
                RULE, 'asset_path_changed', sev, fid, ln,
                jp=' | '.join(a), zh=' | '.join(b),
                detail='成品出现原版没有的资源路径：%s' % p,
                severity_note=note))

        # b) 原版有、成品消失的静态路径
        for p in sorted(sa - sb):
            if p in set(b):
                continue
            findings.append(lvfind.make(
                RULE, 'asset_ref_lost', 'high', fid, ln,
                jp=' | '.join(a), zh=' | '.join(b),
                detail='资源引用丢失：%s（成品同一行已无该路径，贴图/音频会不显示）' % p))

        # c) 模板（变量名保留原样）差异
        for t in sorted(tb - ta):
            findings.append(lvfind.make(
                RULE, 'asset_template_changed', 'high', fid, ln,
                jp=' | '.join(a), zh=' | '.join(b),
                detail='资源路径模板被改动：%s' % t))
        for t in sorted(ta - tb):
            _zl = ctx.zh_line(fid, ln - 1) or ''
            _commented = _zl.lstrip().startswith('//')
            findings.append(lvfind.make(
                RULE,
                'line_commented_out' if _commented else 'asset_template_changed',
                'low' if _commented else 'high', fid, ln,
                jp=' | '.join(a), zh=' | '.join(b),
                detail=('原版该行是有效命令，成品把它注释掉了（疑似有意禁用）：%s' % t)
                if _commented else ('资源路径模板丢失：%s' % t),
                severity_note='成品为注释行；若非有意禁用，该贴图会缺失' if _commented else None))

    # ---------- 2) 含变量路径：变量取值展开后比对
    jp_vals = assignment_values(ctx, 'jp')
    zh_vals = assignment_values(ctx, 'zh')

    # 收集「路径模板 -> 使用点」与「模板里用到的变量」
    tpl_use = collections.defaultdict(list)     # tpl -> [(fid,line)]
    tpl_vars = {}
    for tpl, uses in jp_tpl_all.items():
        tpl_vars[tpl] = template_vars(tpl)
        tpl_use[tpl].extend(uses)
    for tpl, uses in zh_tpl_all.items():
        tpl_vars.setdefault(tpl, template_vars(tpl))
        tpl_use[tpl].extend(uses)

    # 变量 -> 受影响的模板
    var_tpls = collections.defaultdict(set)
    for tpl, vs in tpl_vars.items():
        for v in vs:
            var_tpls[v].add(tpl)

    # 变量取值差异（只关心真正用在路径里的变量）
    for var in sorted(var_tpls):
        jv = _literal(jp_vals.get(var, set()))
        zv = _literal(zh_vals.get(var, set()))
        if not jv and not zv:
            st['var_templates_no_values'] += len(var_tpls[var])
            continue
        only_zh = zv - jv
        if not only_zh:
            continue
        # 取该变量第一次被用作路径的行作为报告位置（确定性）
        firsts = []
        for tpl in var_tpls[var]:
            firsts.extend(tpl_use.get(tpl, []))
        if not firsts:
            continue
        fid, ln = sorted(firsts)[0]
        tpl_sample = sorted(var_tpls[var])[0]
        detail = ('路径变量 {{%s}} 在成品里出现了原版没有的取值：%s；'
                  '受影响模板如 %s（共 %d 个模板 / %d 处使用）。'
                  '变量值被翻译会让资源名对不上，贴图/音频将找不到。'
                  % (var, '、'.join('「%s」' % v for v in sorted(only_zh)),
                     tpl_sample, len(var_tpls[var]), len(firsts)))
        findings.append(lvfind.make(
            RULE, 'asset_var_value_changed', 'high', fid, ln,
            jp=' | '.join(sorted(jv)[:6]), zh=' | '.join(sorted(zv)[:6]),
            detail=detail))

    # ---------- 3) 统计：两边都没有基准的孤立路径（不判错）
    both = (set(jp_all) | set(zh_all)) - refpaths
    st['both_absent_from_baseline'] = len(both)
    st['baseline_gap_samples'] = sorted(both)[:20]
    st['jp_only_absent_from_baseline'] = len(set(jp_all) - refpaths)
    st['zh_only_absent_from_baseline'] = len(set(zh_all) - refpaths)

    codes = collections.Counter(f['code'] for f in findings)
    for k, v in codes.items():
        st['findings_' + k] = v
    st['findings_total'] = len(findings)

    LAST_STATS.clear()
    LAST_STATS.update(st)
    return lvfind.finalize(findings)
