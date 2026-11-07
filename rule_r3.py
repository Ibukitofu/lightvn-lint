# -*- coding: utf-8 -*-
"""R3 变量引用完整性。

全部基于「日文原版 vs 汉化成品」对照，避免把原版本来就有的引擎内置
用法误报成汉化缺陷。

产出的 code：

1. var_renamed     high
   同一行里 {{旧名}} 被改成 {{新名}}：骨架（抹平变量名/引号内容/数字后）
   完全一致，说明确实只动了变量名。新名全量无赋值、旧名原版有赋值。

2. var_lost_in_zh  high
   变量在原版有赋值，成品里只剩使用、赋值点消失。

3. undefined_var   mid（出现 >= 3）/ low（1-2 次，标 low-frequency）
   成品使用但全量从未赋值。日文原版也没有这个名字 -> 成品新增。
   日文原版也有 -> 引擎内置或运行时/跨脚本注入，降为 low，
   在 severity_note 里用 engine-prefix / engine-or-external 标注，
   按任务要求「不排除但降级」而不是直接丢弃。

変数式確認機 不算赋值命令：它是变量变化监视器，第 1 个参数是模式名，
第 2 个才是被监视的变量。

只读：仅通过 lvctx / lvparse 读取语料，不写任何文件。
"""
import os
import re
import sys
import collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lvparse as P
import lvfind

# 赋值命令。変数式確認機 不算：它是「变量变化监视器」，
# 第 1 个参数是模式名、第 2 个才是被监视的变量。
ASSIGN_CMDS = ('保存変数', '変数', '臨時全域変数', '全域変数')
# 动态标签命令：这些命令的栞名位置允许写 {{变量}}
LABEL_CMDS = ('ジャンプ', 'スクリプト', '最終ジャンプ')
COND_KW = ('違ってももし', 'もし')

# 比较表达式 变量 == "字符串" / 变量 != "字符串"
CMP_RE = re.compile(r'([^\s()!&|<>=,"]+?)\s*(==|!=)\s*"([^"]*)"')
# 反向写法 "字符串" == 变量
CMP_REV_RE = re.compile(r'"([^"]*)"\s*(==|!=)\s*([^\s()!&|<>=,"]+)')
# 赋值：变量名 + 运算符 + 值（变量名不含空白）
OP_RE = re.compile(r'^([^\s]+?)\s*(<<=|>>=|<<|>>|\+=|-=|\*=|/=|\+\+|--|=)\s*(.*)$', re.S)
CMD_RE = re.compile(r'[^\s"]+')
WS = ' \t\u3000'

# 引擎/内部变量前缀：两侧都常见只使用不赋值，一律降级
ENGINE_PREFIX = ('_', 'vn_', 'lv')

_CACHE = {'key': None, 'idx': None}
MAX_SAMPLES = 6


def reset_cache():
    """清空索引缓存（测试用）。"""
    _CACHE['key'] = None
    _CACHE['idx'] = None


def _literal(tail):
    """从赋值运算符右侧提取字符串字面量。

    Light.vn 行尾引号约定：第一个引号是开始、最后一个引号是结束，
    内层引号不转义，因此用 rfind 取结束位置。
    不是字面量则返回 None。
    """
    t = tail.lstrip(WS)
    if not t.startswith('"'):
        return None
    last = t.rfind('"')
    if last <= 0:
        return None
    after = t[last + 1:].strip(WS)
    if after and not after.startswith('//'):
        return None
    return t[1:last]


def _strip_prefix(seg):
    """去掉前导空白与可选的 ~ 前缀。"""
    return P.strip_tilde(seg)[0].strip(WS)


def _is_command_line(line):
    """判断一行是不是命令调用行。

    不能只看 parse_segment 的 text_only：以「淫乱度 + {{x}}」这类
    无引号开头的对话文本也会被当成命令。这里要求首个 token
    必须真的是 Commands.xml 里登记的命令名。
    """
    segs = P.split_segments_v2(line)
    if not segs:
        return False
    s = _strip_prefix(segs[0]['head'])
    if not s or s.startswith('"'):
        return False
    guard = 0
    while guard < 8:
        kw = None
        for k in COND_KW:
            if s.startswith(k):
                kw = k
                break
        if not kw:
            break
        rest = s[len(kw):].lstrip(WS)
        if not rest.startswith('('):
            return False
        end = P._cond_end(rest, 0)
        if end <= 0:
            return False
        s = rest[end:].lstrip(WS)
        s = _strip_prefix(s)          # 条件后可能再造一个 ~
        guard += 1
    m = CMD_RE.match(s)
    if not m:
        return False
    return m.group(0) in P.command_table()


def _var_split(line):
    """按 {{...}} 切开：返回 (非变量片段列表, 变量名列表)。

    片段数恒为 len(变量名)+1。
    """
    chunks = []
    names = []
    last = 0
    for m in P.VAR_RE.finditer(line):
        chunks.append(line[last:m.start()])
        names.append(m.group(1).strip())
        last = m.end()
    chunks.append(line[last:])
    return chunks, names


def _decorate(line):
    """把一行归一成骨架：变量名抹成占位符、引号内容挖空、数字抹平。

    返回 (骨架字符串, 变量名列表)。骨架相同才说明两行是同一处代码，
    可以安全地逐位比较变量名。
    """
    names = P.var_refs(line)
    skel = P.VAR_RE.sub(chr(3), line)
    skel = P.blank_quotes(skel)
    skel = re.sub('[0-9]+(?:[.][0-9]+)?', chr(2), skel)
    skel = re.sub('[ \t' + chr(0x3000) + ']+', ' ', skel).strip()
    return skel, names


def _new_side():
    """一侧（日文或成品）的索引容器。"""
    return {
        'assign_names': set(),
        'assign_lit': collections.defaultdict(set),
        'assign_dyn': set(),
        'assign_where': collections.defaultdict(list),
        'cmp': collections.defaultdict(set),
        'cmp_where': collections.defaultdict(list),   # (变量, 键) -> [(fid, 行, 键)]
        'uses': collections.Counter(),
        'use_where': collections.defaultdict(list),
        'label_vars': collections.defaultdict(list),
    }


def _scan_cond(cond, side, fid, line_no):
    """提取条件里的字符串比较键，写进 side['cmp']。

    cmp_where 按 (变量, 键) 记首次出现位置，R5 才能把每条 finding
    定位到真正比较这个键的那一行，而不是变量第一次出现的地方。
    """
    for m in CMP_RE.finditer(cond):
        var, key = m.group(1), m.group(3)
        side['cmp'][var].add(key)
        slots = side['cmp_where'][(var, key)]
        if len(slots) < MAX_SAMPLES:
            slots.append((fid, line_no, key))


def _record_assign(cmd, rest, side, fid, line_no, collected):
    """记录一条赋值命令；返回 (变量名, 字面量|None)。"""
    text = rest.lstrip(WS)
    om = OP_RE.match(text)
    if not om:
        # 形如 臨時全域変数 付与_チャージ：只声明变量名，不赋值
        names = P.argv(text)
        if names:
            side['assign_names'].add(names[0])
            if len(side['assign_where'][names[0]]) < 3:
                side['assign_where'][names[0]].append((fid, line_no, cmd))
            return names[0], None
        return None, None
    name, op, tail = om.group(1), om.group(2), om.group(3)
    side['assign_names'].add(name)
    if len(side['assign_where'][name]) < 3:
        side['assign_where'][name].append((fid, line_no, cmd))
    if op != '=':
        return name, None
    if '{{' in tail:
        # 运行时才能确定的取值：静态集合不完整，R5 必须标 ambiguous
        side['assign_dyn'].add(name)
    lit = _literal(tail)
    if lit is None:
        return name, None
    side['assign_lit'][name].add(lit)
    collected.append((name, lit))
    return name, lit


def _scan_segment(seg, side, fid, line_no, depth, collected):
    """递归扫描一个段：条件 -> 命令 -> 行尾引号内的嵌套命令。"""
    if depth > 6:
        return
    s = _strip_prefix(seg)
    if not s:
        return

    # 条件前缀（可能连写多个 もし）
    guard = 0
    while guard < 8:
        kw = None
        for k in COND_KW:
            if s.startswith(k):
                kw = k
                break
        if not kw:
            break
        rest = s[len(kw):].lstrip(WS)
        if not rest.startswith('('):
            break
        end = P._cond_end(rest, 0)
        if end <= 0:
            break
        _scan_cond(rest[1:end - 1], side, fid, line_no)
        s = _strip_prefix(rest[end:].lstrip(WS))
        guard += 1

    if not s or s.startswith('"'):
        # 空段或纯对话文本行
        return
    m = CMD_RE.match(s)
    if not m:
        return
    cmd = m.group(0)
    rest = s[m.end():]

    # 行尾引号内容：可能是显示文本，也可能是嵌套命令
    arg = None
    body = rest.rstrip(WS)
    if body.endswith('"'):
        i = 0
        n = len(body)
        while i < n:
            if body[i] == '\\':
                i += 2
                continue
            if body[i] == '"':
                arg = body[i + 1:-1]
                break
            i += 1

    if cmd in ASSIGN_CMDS:
        _record_assign(cmd, rest, side, fid, line_no, collected)
    elif cmd in LABEL_CMDS:
        # 第 1 个参数是脚本名，之后的参数才可能是标签位置
        for pos, a in enumerate(P.argv(rest)):
            if pos == 0:
                continue
            if '{{' in a:
                for v in P.var_refs(a):
                    side['label_vars'][v].append((fid, line_no, cmd, a))

    if arg is not None:
        first = arg.lstrip(WS)
        head = CMD_RE.match(first)
        nested = first.startswith('もし') or first.startswith('違ってももし')
        if not nested and head and head.group(0) in P.command_table():
            nested = True
        if nested:
            for sub in P.split_segments(arg):
                _scan_segment(sub, side, fid, line_no, depth + 1, collected)


def _scan_line(line, side, fid, line_no):
    """扫描整行；返回本行的字符串赋值字面量 [(变量, 字面量)]。"""
    collected = []
    for seg in P.split_segments(line):
        _scan_segment(seg, side, fid, line_no, 0, collected)
    for v in P.var_refs(line):
        side['uses'][v] += 1
        if len(side['use_where'][v]) < MAX_SAMPLES:
            side['use_where'][v].append((fid, line_no, line.strip()[:200]))
    return collected


def _script_fids(ctx):
    """排除着色器（#version）与 JSON 数据块（首行 { / [）。"""
    out = []
    for fid in ctx.fids():
        n = ctx.line_count(fid)
        head = ''
        for i in range(min(n, 5)):
            t = (ctx.jp_line(fid, i) or '').strip()
            if t:
                head = t
                break
        if head.startswith('#version'):
            continue
        if head.startswith('{') or head.startswith('['):
            continue
        out.append(fid)
    return out


def build_index(ctx):
    """全量扫描日文与成品语料，建立 R3/R5 共用索引。"""
    fids = list(ctx.fids())
    key = tuple((fid, ctx.line_count(fid)) for fid in fids)
    if _CACHE['key'] == key and _CACHE['idx'] is not None:
        return _CACHE['idx']

    jp = _new_side()
    zh = _new_side()
    asg_pairs = []   # [(fid, 行号, 变量, 日文字面量, 成品字面量)]
    renames = []     # [(fid, 行号, 旧名, 新名, 日文行, 成品行)]

    for fid in _script_fids(ctx):
        n = ctx.line_count(fid)
        for i in range(n):
            line_no = i + 1
            jl = ctx.jp_line(fid, i)
            zl = ctx.zh_line(fid, i)
            jblank = (not jl) or (not jl.strip()) or jl.lstrip().startswith('//')
            zblank = (not zl) or (not zl.strip()) or zl.lstrip().startswith('//')
            jc = [] if jblank else _scan_line(jl, jp, fid, line_no)
            zc = [] if zblank else _scan_line(zl, zh, fid, line_no)
            if jblank or zblank:
                continue

            # 同行同变量的字面量对照 -> R5 逻辑键翻译候选
            if jc and zc:
                jmap = {}
                for v, lit in jc:
                    jmap.setdefault(v, lit)
                zmap = {}
                for v, lit in zc:
                    zmap.setdefault(v, lit)
                for v in jmap:
                    if v in zmap and jmap[v] != zmap[v]:
                        asg_pairs.append((fid, line_no, v, jmap[v], zmap[v]))

            # 命令行内的 {{变量}} 逐位置对照 -> R3 变量名被改写候选。
            # 必须要求「变量以外的所有字面完全一致」：行内换序（翻译重排）
            # 会让同位 {{}} 名字错位，那不是改名缺陷。
            if '{{' in jl and '{{' in zl:
                jskel, jv = _decorate(jl)
                zskel, zv = _decorate(zl)
                if jv and len(jv) == len(zv) and jskel == zskel:
                    for a, b in zip(jv, zv):
                        if a != b:
                            renames.append((fid, line_no, a, b, jl, zl))

    idx = {'jp': jp, 'zh': zh, 'asg_pairs': asg_pairs,
           'renames': renames, 'fids': fids}
    _CACHE['key'] = key
    _CACHE['idx'] = idx
    return idx


def _engine_like(name):
    """引擎内置变量前缀判断。"""
    for pre in ENGINE_PREFIX:
        if name.startswith(pre):
            return True
    return False


def _where(sites, n=5):
    """把取样点格式化成 detail 里的位置串。"""
    return ', '.join('%s:%d' % (s[0], s[1]) for s in sites[:n])


def run(ctx):
    idx = build_index(ctx)
    jp, zh = idx['jp'], idx['zh']
    out = []

    # ---- 1) 命令行内变量名被改写（最高优先级，逐行定位）
    rename_hits = collections.defaultdict(list)
    for fid, line_no, old, new, jl, zl in idx['renames']:
        if new in zh['assign_names']:
            continue                    # 新名有赋值 -> 可能是有意的重排，不判错
        if old not in jp['assign_names']:
            continue                    # 旧名日文也没赋值 -> 无法归因
        if _engine_like(new) and _engine_like(old):
            continue                    # 引擎变量之间的替换，跳过
        rename_hits[(old, new)].append((fid, line_no, jl, zl))

    renamed_new = set()
    for (old, new), hits in sorted(rename_hits.items()):
        renamed_new.add(new)
        fid, line_no, jl, zl = hits[0]
        _use_n = zh['uses'].get(new, 0)
        _zh_asg = len(zh['assign_where'].get(new, []))
        _jp_asg = len(jp['assign_where'].get(old, []))
        detail = ('命令行里的变量引用被改写：{{%s}} -> {{%s}}，共 %d 行。'
                  '判定依据：新名 {{%s}} 在成品有 %d 处使用、%d 处赋值；'
                  '旧名 {{%s}} 在日文有 %d 处赋值。'
                  '使用 >0 且赋值 =0，属于「改名后漏写赋值」，运行时取不到值。' % (
                      old, new, len(hits), new, _use_n, _zh_asg, old, _jp_asg))
        others = _where([(h[0], h[1]) for h in hits[1:]])
        if others:
            detail += ' 其它位置：' + others
        out.append(lvfind.make(
            'R3', 'var_renamed', 'high', fid, line_no,
            jl.strip(), zl.strip(), detail))

    # ---- 2) 成品里使用但从未赋值
    for var in sorted(zh['uses']):
        if var in zh['assign_names']:
            continue
        n = zh['uses'][var]
        sites = zh['use_where'].get(var) or []
        if sites:
            fid, line_no = sites[0][0], sites[0][1]
        else:
            fid, line_no = idx['fids'][0], 1
        jl = ctx.jp_line(fid, line_no - 1) or ''
        zl = ctx.zh_line(fid, line_no - 1) or ''
        where = _where(sites)

        if var in renamed_new:
            continue                    # 已由 var_renamed 精确覆盖

        if var in jp['assign_names']:
            # 日文有赋值、成品没有 -> 变量在成品里丢失
            out.append(lvfind.make(
                'R3', 'var_lost_in_zh', 'high', fid, line_no,
                jl.strip(), zl.strip(),
                '变量 %s 在日文原版有 %d 处赋值，成品里只被使用、从未赋值；'
                '共 %d 处引用：%s' % (
                    var, len(jp['assign_where'].get(var, [])), n, where)))
            continue

        if var in jp['uses']:
            # 两侧一致地只使用不赋值 -> 引擎内置或运行时注入。
            # 用 severity='low' + severity_note 分档，不排除、不误判成缺陷。
            note = ('与日文原版一致：两侧都只使用、从未赋值，疑似引擎内置变量或'
                    '运行时/跨脚本注入，非汉化引入')
            if _engine_like(var):
                note = '引擎内置变量前缀（_ / vn_ / lv）；' + note
                severity_note = 'engine-prefix'
            elif n < 3:
                note += '；low-frequency'
                severity_note = 'low-frequency'
            else:
                severity_note = 'engine-or-external'
            out.append(lvfind.make(
                'R3', 'undefined_var', 'low', fid, line_no,
                jl.strip(), zl.strip(),
                '变量 %s 共 %d 处引用：%s。%s' % (var, n, where, note),
                severity_note=severity_note))
            continue

        # 日文原版根本没有这个变量名 -> 成品新增的未定义引用
        sev = 'mid' if n >= 3 else 'low'
        out.append(lvfind.make(
            'R3', 'undefined_var', sev, fid, line_no,
            jl.strip(), zl.strip(),
            '变量 %s 共 %d 处引用，全量无任何赋值：%s。'
            '成品新增的未定义变量（日文原版无此名字），可能是变量名被翻译、'
            '拼写改动或文本重排造成，请人工确认' % (var, n, where),
            severity_note=None if n >= 3 else 'low-frequency'))

    return lvfind.finalize(out)
