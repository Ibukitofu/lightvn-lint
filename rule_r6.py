# -*- coding: utf-8 -*-
"""R6 代码骨架一致性。

把双引号里的「显示文本」挖掉后，比较日文原版与汉化成品的代码骨架：
  - 相同 -> 引号外没被改动（显示文本翻译是安全的）
  - 不同 -> skeleton_changed：命令名/变量名/数字/路径/运算符被改动

行尾引号参数若本身是嵌套命令（如 ボタン / タッチ終了時 / 変数式確認機），
会递归展开再挖空，因此「引号内嵌命令被翻译」同样能被抓到。
"""
import collections
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


_LABEL_RE = re.compile(r'^(~?\s*(?:もし\s*\([^)]*\)\s*)*~?\s*)(栞|ラベル)\s+(\S+)\s*$')

# 引号外 【...】：说话人显示名。锚点要求前面是行首/~ /空白/右括号，避免误伤路径里的 【】。
_SPEAKER_RE = re.compile(r'(?:(?<=^)|(?<=[~\s(]))【[^】\n]*】')


def _blank_speaker_names(line):
    """把引号外的说话人 【名字】 挖成占位符；路径参数里的 【】 不动。"""
    if '【' not in line:
        return line
    blanks = _quote_spans_mask(line)

    def repl(m):
        if blanks(m.start()):
            return m.group(0)
        return '【\x00】'
    return _SPEAKER_RE.sub(repl, line)


def _quote_spans_mask(line):
    spans = P.quote_spans(line)
    def inside(pos):
        for a, b in spans:
            if a < pos < b:
                return True
        return False
    return inside


def _blank_label_name(line):
    """「栞 <标签>」的标签名挖空（前缀与命令名保留，所以前缀不一致仍会被抓）。

    注意：这里只是把**标签名本身**挖空，让「标签随译文同步改名」不被当成骨架损坏。
    改名是否安全由 run() 里的 _label_rename_breaks 单独判定 —— 它检查
    旧名是否还残留引用、新名是否真的被引用，所以豁免不是无条件的。
    """
    m = _LABEL_RE.match(line.strip())
    if not m:
        return line
    head, cmd, _name = m.group(1), m.group(2), m.group(3)
    return head + cmd + ' \x00'


def _skeleton_exempt(line):
    """对一行做骨架比对前的「精确豁免挖空」，然后取骨架。"""
    t = _blank_label_name(line)
    t = _blank_speaker_names(t)
    return skeleton(t)


def _collect_label_refs(ctx, side):
    """全库「可被引用到的标签」集合。

    = 静态字面量引用 ∪ 动态模板按赋值集合展开出的所有取值。
    动态那一半很关键：本作的技能说明/特效标签几乎全是
    「説明_{{bat_ヒロイン次スキル名}}」这种动态派发，字面量里根本看不到。
    """
    from rule_r1 import assignment_values
    from rule_r1 import _literal as _lit

    reach = set()
    templates = set()
    getter = ctx.jp_line if side == 'jp' else ctx.zh_line
    for fid in ctx.fids():
        if side == 'zh' and ctx.zh_page(fid) is None:
            continue
        n = ctx.line_count(fid)
        for i in range(n):
            line = getter(fid, i)
            if not line or not any(c in line for c in P._JUMP_NAMES):
                continue
            for _cmd, _file, label in P.iter_jump_refs(line):
                if not label:
                    continue
                if '{{' in label:
                    templates.add(label)
                else:
                    reach.add(label)
    if templates:
        vals = assignment_values(ctx, side)
        for t in templates:
            exp, _unknown = _expand_template(t, vals, _lit)
            reach |= exp
    return reach


def _expand_template(tpl, values, lit):
    """把模板里的 {{变量}} 用赋值表展开（只展开简单变量，最多 3 轮）。"""
    cur = {tpl}
    for _ in range(3):
        nxt = set()
        changed = False
        for s in cur:
            toks = P.var_tokens(s)
            if not toks:
                nxt.add(s)
                continue
            expr = toks[0].strip()
            vs = lit(values.get(expr, set())) if re.match(r'^[^\s+\-*/%<>=!&|()]+$', expr) else set()
            if not vs:
                nxt.add(s)
                continue
            for v in vs:
                nxt.add(s.replace('{{' + toks[0] + '}}', v, 1))
            changed = True
        cur = nxt
        if not changed:
            break
    return {s for s in cur if '{{' not in s}, set()



_SPK_NAME_RE = re.compile(r'【([^】]*)】')


def _speaker_name(line):
    """take the speaker name of a speaker-marker line; None if not a speaker line."""
    if not line or '【' not in line:
        return None
    _had, rest = P.strip_condition_prefix(line)
    rest = rest.lstrip(P.WS_SET)
    if rest.startswith('"') or not rest.startswith('【'):
        return None
    m = _SPK_NAME_RE.match(rest)
    if not m:
        return None
    return m.group(1)


def _speaker_name_breaks(ctx):
    """Speaker names translated badly -- checked per line against the majority spelling.

    Background (历史错误 2.2): a slicing bug once turned the speaker marker
    into just one character, corrupting all 17,483 speaker rows. R6 used to skip
    speaker lines entirely, so that whole class of damage was invisible.

    Criterion (falsifiable, needs no hand-maintained mapping table):
      1) count every (japanese speaker name -> chinese speaker name) co-occurrence;
      2) for each japanese name take the most frequent chinese name as the majority;
      3) per line, if the japanese name has a majority and this line's chinese name
         differs -> report speaker_name_inconsistent (high).
    Falsifiable: the current corpus has 0 deviating rows out of 35,789, while
    rewriting one name to a stray character immediately deviates.
    """
    pairs = collections.defaultdict(collections.Counter)
    rows = []
    for fid in ctx.fids():
        zpg = ctx.zh_page(fid)
        if zpg is None:
            continue
        n = min(ctx.line_count(fid), len(zpg.prefix))
        for i in range(n):
            jn = _speaker_name(ctx.jp_line(fid, i) or '')
            zn = _speaker_name(zpg.text(i) or '')
            if jn is None or zn is None:
                continue
            pairs[jn][zn] += 1
            rows.append((fid, i, jn, zn))

    major = {}
    for jn, c in pairs.items():
        zh, cnt = c.most_common(1)[0]
        if cnt >= 2:            # a single sample is not enough to be a standard
            major[jn] = zh

    out = []
    for fid, i, jn, zn in rows:
        want = major.get(jn)
        if want is None or zn == want:
            continue
        out.append(lvfind.make(
            RULE, 'speaker_name_inconsistent', 'high', fid, i + 1,
            ctx.jp_line(fid, i) or '', ctx.zh_line(fid, i) or '',
            'speaker name ' + repr(jn) + ' is written ' + repr(want) + ' everywhere else'
            ' (' + str(pairs[jn][want]) + ' rows) but ' + repr(zn) + ' here -- possible'
            ' truncated character, missing mapping entry, or a hand edit.',
            severity_note='falsifiable: restoring the majority spelling passes'))
    return out


def _label_rename_breaks(ctx):
    """标签改名是否断链 —— **逐行**比对同一行的日文标签名与中文标签名。

    不用「文件级标签集合差」：本作的技能说明表整份都翻译过（66429 一行一个标签，
    117 个全部译了），集合差会被这 100 多个正常改名淹没，看不出问题。

    逐行判据（可证伪）：
      * JP 行是「栞 X」、ZH 行是「栞 Y」、X != Y：
          - 若成品全库**仍有引用指向 X**（静态字面量或动态模板展开值）
            → 定义改了但调用点没跟 → 断链，high
          - 若 Y 在成品全库**完全没有任何引用**
            → 这个入口没有跳转目标（改名后没人指向它）→ 悬空入口，mid
    两条都能被真实语料证伪：协调改名时 X 不再被引用、Y 一定有引用。
    """
    zh_refs = _collect_label_refs(ctx, 'zh')
    out = []
    for fid in ctx.fids():
        if ctx.zh_page(fid) is None:
            continue
        n = min(ctx.line_count(fid), len(ctx.zh_page(fid).prefix))
        for i in range(n):
            jl = ctx.jp_line(fid, i) or ''
            zl = ctx.zh_line(fid, i) or ''
            mj = _LABEL_RE.match(jl.strip())
            mz = _LABEL_RE.match(zl.strip())
            if not mj or not mz:
                continue
            old, new = mj.group(3), mz.group(3)
            if old == new:
                continue
            if old in zh_refs:
                out.append(lvfind.make(
                    RULE, 'label_rename_dangling', 'high', fid, i + 1, jl, zl,
                    '标签「%s」被改名为「%s」，但成品全库仍有引用指向旧名'
                    '（静态字面量或动态模板展开值）。这些跳转会落到错误位置或直接卡死。'
                    % (old, new),
                    severity_note='可证伪：协调改名时旧名必定不再被任何引用命中'))
            elif new not in zh_refs:
                out.append(lvfind.make(
                    RULE, 'label_rename_unreferenced', 'mid', fid, i + 1, jl, zl,
                    '标签「%s」被改名为「%s」，但成品全库没有任何引用指向新名，'
                    '也没有引用指向旧名 —— 这个入口没有跳转目标。' % (old, new),
                    severity_note='可证伪：协调改名时新名必定被新引用命中'))
    return out


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
            # 无引号的台词续行不是代码（与 R4 共用同一判据）。
            if P.is_dialogue_continuation(jline, side='jp') and P.is_dialogue_continuation(zline, side='zh', peer=jline):
                continue
            # 无引号的台词续行（接在上一条台词后面、不以命令开头）也是显示文本,
            # 翻译后骨架必然整体改变，不属于代码损坏。
            if not jst.startswith('~') and not jst.startswith('.'):
                _tok = jst.split(' ')[0].split(chr(0x3000))[0]
                try:
                    import lvparse as _P
                    _known = _P.is_known_command(_tok)
                except Exception:
                    _known = True
                if not _known:
                    continue

            # 非对话行即视为代码行，比对骨架。
            # 比对前先给「本来就该翻译的显示位」挖空：
            #   * 引号外的 【...】：说话人显示名（可带 ~ / もし(...) 前缀）
            #   * 栞/ラベル 后面的标签名：允许随译文同步改名（lvverify 的 Label names
            #     层已证明 22,219 条标签全部可解析）
            # 这两处挖空后仍要求其余骨架逐字符相同，所以不是「放宽」，是「精确豁免」。
            jb = _skeleton_exempt(jline)
            zb = _skeleton_exempt(zline)
            if jb == zb:
                continue
            out.append(lvfind.make(
                RULE, "skeleton_changed", "high", fid, i + 1,
                jline, zline,
                "引号外的代码骨架被改动。" + _first_diff(jb, zb),
                severity_note="骨架里 {T}=显示文本（已挖空），{S:...}=嵌套命令（保留结构）"))
    # 标签改名单独做「可证伪」检查：改完必须在成品全库真的被引用、
    # 旧名必须真的不再被引用。这样标签豁免才不是无条件的。
    out.extend(_label_rename_breaks(ctx))
    out.extend(_speaker_name_breaks(ctx))
    return lvfind.finalize(out)
