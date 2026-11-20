# -*- coding: utf-8 -*-
"""Shared Light.vn script parsing for the static linter."""
import lvroot  # 负责定位 lvlib 与目标游戏
# 这些命令的参数里含「Light.vnコマンド」（引号内是又一段命令），需要递归校验
NESTED_CMD_COMMANDS = frozenset([
    "ボタン", "タッチ開始時", "タッチ終了時", "タッチ継続時",
    "タイル移動x", "タイル移動y", "ループ", "演出目録追加",
    "消滅時", "衝突開始時", "衝突継続時", "衝突終了時",
    "隣接方向衝突継続時", "続行", "キーダウン", "キーアップ",
    "キー長押し", "音源確認機", "もし", "違ってももし", "違ったら",
    "変数式確認機", "ダイアログ", "砂時計", "イベント", "lvCmd",
    "トラックバー", "ハイライト条件", "コマンドマクロ", "マクロ", "文字マクロ", "反復実行",
])


def needs_nested_parse(cmd):
    """该命令的行尾引号参数是否是嵌套命令（而非显示文本）？"""
    return cmd in NESTED_CMD_COMMANDS



import os, re, html, json

import lvroot
ROOT = lvroot.ROOT
JP_DIR = lvroot.JP_DIR
BLD_DIR = lvroot.DATA
CMD_XML = lvroot.CMD_XML
REF_BASE = os.environ.get('LV_REFPATHS') or os.path.join(lvroot.DATA_DIR, 'refpaths.json')

QUOTE = '"'
WS = ' \t\u3000'
SEP_RE = re.compile(r' \| ')
VAR_RE = re.compile(r'\{\{(.*?)\}\}')
ASSIGN_NAMES = ('保存変数', '変数', '臨時全域変数', '全域変数', '変数式確認機')
JUMP_NAMES = ('ジャンプ', 'スクリプト', '最終ジャンプ')
LABEL_NAME = '栞'
# commands whose quoted argument is display text (translatable)
DISPLAY_ASSIGN_HINT = ('スキル効果',)

_CMD_TABLE = None


def command_table():
    """解析目标游戏的 Commands.xml，兼容三种已见格式:
       A) <Command cmd="bg" name="背景">            (16.9 系)
       B) <Command cmd="..." name="...">            (16.4 系)
       C) <Command name="..."><explanation lang="jap" name="..." syntax="...">  (16.2 系)
    返回 {日文命令名: syntax}
    """
    global _CMD_TABLE
    if _CMD_TABLE is not None:
        return _CMD_TABLE
    table = {}
    try:
        with open(CMD_XML, encoding="utf-8", errors="replace") as stream:
            xml = stream.read()
    except Exception:
        _CMD_TABLE = table
        return table
    starts = [m.start() for m in re.finditer(r"<Command\b", xml)]
    for j, s in enumerate(starts):
        e = starts[j + 1] if j + 1 < len(starts) else len(xml)
        block = xml[s:e]
        syn = ""
        sm = re.search(r"<explanation\s+lang=\"jap\"[^>]*?syntax=\"([^\"]*)\"", block, re.S)
        if sm:
            syn = html.unescape(sm.group(1))
        name = None
        em = re.search(r"<explanation\s+lang=\"jap\"\s+name=\"([^\"]*)\"", block, re.S)
        if em:
            name = html.unescape(em.group(1))
        if not name:
            cm = re.search(r"<Command\b[^>]*?\bname=\"([^\"]*)\"", block)
            if cm:
                nm = html.unescape(cm.group(1))
                if not nm.startswith("--["):
                    name = nm
        if name and name not in table:
            table[name] = syn
    _CMD_TABLE = table
    return table

def _split_syntax(syntax):
    tokens = []
    buf = []
    depth = 0
    for ch in syntax:
        if ch in '[(<':
            depth += 1
            buf.append(ch)
        elif ch in ')]>':
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch in ' \t\n\r' and depth == 0:
            if buf:
                tokens.append(''.join(buf)); buf = []
        else:
            buf.append(ch)
    if buf:
        tokens.append(''.join(buf))
    return tokens


def signature(name):
    """Parse the signature of one command -> dict or None."""
    table = command_table()
    syn = table.get(name)
    if syn is None:
        return None
    toks = [t for t in _split_syntax(syn) if t[:1] in '[(<']
    req = 0
    variadic = False
    asset_pos = None
    label_pos = None
    idx = 0
    for t in toks:
        body = t[1:-1] if t[-1:] in ')]>' else t[1:]
        if '...' in body or '…' in body:
            variadic = True
        if t[0] == '[':
            req += 1
        if asset_pos is None and ('ファイル名' in body):
            asset_pos = idx
        if label_pos is None and '栂' in body:
            label_pos = idx
        idx += 1
    return {'req': req, 'total': len(toks), 'variadic': variadic,
            'asset_pos': asset_pos, 'label_pos': label_pos, 'syntax': syn}


def strip_tilde(line):
    s = line
    had = False
    while True:
        t = s.lstrip(WS)
        if t.startswith('~'):
            had = True
            s = t[1:]
        elif t != s:
            s = t
            break
        else:
            break
    return s, had


def quote_spans(s):
    """Spans (start, end) inclusive of the quote characters."""
    spans = []
    i, n = 0, len(s)
    while i < n:
        if s[i] == '\\':
            i += 2
            continue
        if s[i] == QUOTE:
            j = i + 1
            while j < n:
                if s[j] == '\\':
                    j += 2
                    continue
                if s[j] == QUOTE:
                    break
                j += 1
            if j < n:
                spans.append((i, j)); i = j + 1
            else:
                spans.append((i, n - 1)); break
        else:
            i += 1
    return spans


def blank_quotes(s):
    """Replace every quoted span with a single marker so skeletons compare."""
    out = []
    last = 0
    for a, b in quote_spans(s):
        out.append(s[last:a])
        out.append('\x00')
        last = b + 1
    out.append(s[last:])
    return ''.join(out)


def split_segments(s):
    """Split a line on ' | ' outside quotes; returns list of segment strings."""
    spans = quote_spans(s)

    def inside(pos):
        for a, b in spans:
            if a < pos < b:
                return True
        return False

    segs = []
    last = 0
    for m in SEP_RE.finditer(s):
        if inside(m.start()):
            continue
        segs.append(s[last:m.start()])
        last = m.end()
    segs.append(s[last:])
    return segs


def _cond_end(s, start):
    """Given index of '(' in s[start:], return index just past matching ')'."""
    depth = 0
    inq = False
    i = start
    n = len(s)
    while i < n:
        c = s[i]
        if c == '\\':
            i += 2; continue
        if c == QUOTE:
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


def parse_segment(seg):
    """Analyse one command segment.

    Returns dict(command, args, rest, cond, text_only) where:
      command  -- command name ('' for pure text)
      args     -- argv list after the command name
      cond     -- condition expression text for もし/違ってももし, else None
      text_only-- True when this segment is dialogue text, not a command
    """
    s = seg.lstrip(WS)
    if not s:
        return {'command': '', 'args': [], 'cond': None, 'text_only': True}
    s, _ = strip_tilde(s)
    s = s.lstrip(WS)
    if not s:
        return {'command': '', 'args': [], 'cond': None, 'text_only': True}
    cond = None
    if s.startswith('もし') or s.startswith('違ってももし'):
        kw = 'もし' if s.startswith('もし') else '違ってももし'
        rest = s[len(kw):].lstrip(WS)
        if rest.startswith('('):
            end = _cond_end(rest, 0)
            if end > 0:
                cond = rest[1:end - 1].strip()
                s = rest[end:].lstrip(WS)
    if not s:
        return {'command': '', 'args': [], 'cond': cond, 'text_only': True}
    if s.startswith(QUOTE):
        return {'command': '', 'args': [], 'cond': cond, 'text_only': True}
    m = re.match(r'[^ \t\u3000"]+', s)
    if not m:
        return {'command': '', 'args': [], 'cond': cond, 'text_only': True}
    cmd = m.group(0)
    rest = s[m.end():]
    args = argv(rest)
    return {'command': cmd, 'args': args, 'cond': cond, 'text_only': False}


def argv(rest):
    """Split the remainder of a command into argv, honouring quotes.

    Quoted tokens keep their quotes stripped; the original text is preserved
    in parallel so callers can inspect raw form.
    """
    out = []
    i, n = 0, len(rest)
    while i < n:
        while i < n and rest[i] in WS:
            i += 1
        if i >= n:
            break
        if rest[i] == QUOTE:
            j = i + 1
            buf = []
            while j < n:
                if rest[j] == '\\' and j + 1 < n:
                    buf.append(rest[j:j + 2]); j += 2; continue
                if rest[j] == QUOTE:
                    break
                buf.append(rest[j]); j += 1
            out.append(''.join(buf))
            i = j + 1
        else:
            j = i
            while j < n and rest[j] not in WS:
                j += 1
            out.append(rest[i:j])
            i = j
    return out


def load_page_jp(fid):
    import lvpage
    return lvpage.Page.load(os.path.join(JP_DIR, fid + ".txt"))


WS_SET = ' \t\u3000'
_SENT_PUNCT = set('。、！？…，,．.')
_CTRL_ESCAPE = re.compile(r'(?:\\[wentvs])+\s*$')
_ASCII_TOKEN = re.compile(r'^[\x20-\x7e\d\.\-:/_]+$')


def strip_condition_prefix(line):
    """去掉行首的 ~ 与连续的 もし(...) 前缀。

    返回 (是否有前缀, 剩余文本)。剩余的 lstrip 过。
    """
    t = line
    had = False
    for _ in range(4):
        t2 = t.lstrip(WS_SET)
        while t2.startswith('~'):
            t2 = t2[1:].lstrip(WS_SET)
            had = True
        kw = None
        if t2.startswith('違ってももし'):
            kw = '違ってももし'
        elif t2.startswith('もし'):
            kw = 'もし'
        if not kw:
            t = t2
            break
        rest = t2[len(kw):].lstrip(WS_SET)
        if not rest.startswith('('):
            t = t2
            break
        end = _cond_end(rest, 0)
        if end <= 0:
            t = t2
            break
        had = True
        t = rest[end:]
    return had, t.lstrip(WS_SET)


# 说话人行「前缀」允许出现的字符：~ 空白 もし 条件表达式字符 汉字/假名
_COND_ONLY = re.compile(
    r'^[~\s\t\u3000' + 'もしでなくても違わったら' + r'()&|=!<>"\w\-+.\u3040-\u30ff\u4e00-\u9fff]*$')


def _prefix_before_bracket(line):
    """第一个「引号外」的【 之前的那段文本；没有则返回 None。"""
    s = blank_quotes(line)
    k = s.find('【')
    if k < 0:
        return None
    return line[:k]


def is_speaker_marker(line):
    """「~【说话人名】」形式的说话人行（可带 ~ / もし(...) 条件前缀）。

    这类行里 【...】 的内容是**显示用的人名**，本来就该翻译；
    它既不是命令，也不是代码骨架。

    判据（要求同时满足，避免把「命令参数里的【】」也算进来）：
      * 【 出现在引号外；
      * 【 之前的整段只有 ~ / 空白 / もし / 条件表达式字符，**没有引号**；
      * 去掉 ~ 与已知条件关键字后，前缀里没有任何「已知命令名」。
    资源路径里的【】（如 効果音 nc213827_【効果音】xxx.ogg）因前缀含命令名而不算。
    """
    if not line or '【' not in line:
        return False
    pre = _prefix_before_bracket(line)
    if pre is None:
        return False
    if '"' in pre:
        return False
    if not _COND_ONLY.match(pre):
        return False
    # 前缀里不能有「已知命令名」（効果音 / 絵 / 画像 …）
    probe = pre
    for _ in range(4):
        probe = probe.lstrip(WS_SET)
        while probe.startswith('~'):
            probe = probe[1:].lstrip(WS_SET)
        if probe.startswith('違ってももし'):
            probe = probe[len('違ってももし'):]
            continue
        if probe.startswith('もし'):
            probe = probe[len('もし'):]
            continue
        break
    body = probe.lstrip(WS_SET)
    if body.startswith('(') or body.startswith('&') or body.startswith('|'):
        return True
    tok = re.split(r'[ \t\u3000"]+', body, maxsplit=1)[0] if body else ''
    return not tok or not is_known_command(tok)


# 行尾标点（命令名被翻译后常被补上这些）
_TRAIL_PUNCT = '\u3002\u3001\uff01\uff1f\u2026\uff0c,\uff0e.\uff1a:\uff1b;'

_edit1_cache = {}


def _edit_distance_le1(a, b):
    """编辑距离 <= 1（插入/删除/替换单字符）。"""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(1 for x, y in zip(a, b) if x != y) <= 1
    if la > lb:
        a, b = b, a
    i = j = 0
    skipped = False
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        if skipped:
            return False
        skipped = True
        j += 1
    return True


def looks_like_known_command(token):
    """token（去尾标点后）是否像是某个已知命令 —— 含命令名被翻译的形态。

    用途：is_dialogue_continuation 放行前的反向约束。
    历史错误 1.1 的形态是「命令名被翻译」；被翻出来的词往往不是已知命令，
    旧判据（只查 is_known_command）会把它当台词放过。这里再补两道：
      * 去尾标点后是已知命令（'~脚本终了。' 这类「命令名 + 句号」）
      * 与某个已知命令的编辑距离 <= 1（少一个字 / 错一个字）
    """
    t = token.strip(WS_SET).rstrip(_TRAIL_PUNCT)
    if not t:
        return False
    if is_known_command(t):
        return True
    if len(t) < 2:
        return False
    has_cjk = any('\u3040' <= ch <= '\u30ff' or '\u4e00' <= ch <= '\u9fff' for ch in t)
    if not has_cjk:
        return False
    if t in _edit1_cache:
        return _edit1_cache[t]
    table = command_table()
    hit = False
    for cmd in table:
        if abs(len(cmd) - len(t)) > 1:
            continue
        if cmd[0] != t[0]:
            continue
        if _edit_distance_le1(t, cmd):
            hit = True
            break
    _edit1_cache[t] = hit
    return hit

def _first_token_after_prefix(line):
    _had, rest = strip_condition_prefix(line)
    rest = rest.lstrip(WS_SET)
    if not rest or rest.startswith(chr(34)):
        return None
    body = rest
    if body.startswith(chr(45)):
        body = body[1:].lstrip(WS_SET)
        if not body or body.startswith(chr(34)):
            return None
    return re.split(r'[ \t\u3000"]+', body, maxsplit=1)[0]


def is_dialogue_continuation(line, side=None, peer=None):
    """无引号的台词续行（承接上一条对话），不是命令。

    参数 side：
      * 'jp' / None —— 日文侧：要求含假名。日文台词几乎必含假名（实测 293 条续行里 290 条有假名），
        纯汉字 token 更像是「命令名被翻译成中文」，不能当台词放行（审计 D-1）。
      * 'zh' —— 中文侧：译文本来就是纯汉字，不要求假名。

    其余条件（两侧都适用）：
      * 必须带 ~ / もし(...) 前缀
      * 去掉前缀后不是引号开头
      * 以 '-' 续行标记开头，或整段是无空白单 token 且不是已知命令
      * 去尾标点后不能像任何已知命令（含编辑距离 <= 1）
    """
    if not line or not line.strip():
        return False
    had, rest = strip_condition_prefix(line)
    if not had or not rest:
        return False
    if rest.startswith(chr(34)):
        return False

    # 反向约束（审计 D-1）：对侧同行的首 token 是已知命令、本行首 token 不是
    # -> 这是「命令名被翻译」，不能当台词放行。
    if peer:
        peer_tok = _first_token_after_prefix(peer)
        if peer_tok and is_known_command(peer_tok):
            my_tok = _first_token_after_prefix(line)
            if my_tok and not is_known_command(my_tok):
                return False

    def _ok(body, head_tok):
        if looks_like_known_command(head_tok):
            return False
        if _ASCII_TOKEN.match(body):
            return False
        has_kana = bool(re.search(r"[\u3040-\u309f\u30a0-\u30ff]", body))
        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", body))
        if not has_kana and not has_cjk:
            return False
        if side != 'zh' and not has_kana:
            # 日文侧纯汉字：口语化风险太大，不当续行
            return False
        return bool(_CTRL_ESCAPE.search(body) or any(ch in _SENT_PUNCT for ch in body))

    body = rest
    if body.startswith(chr(45)):
        body = body[1:].lstrip(WS_SET)
        if not body or body.startswith(chr(34)):
            return False
        head_tok = re.split(r"[ \t\u3000\"]+", body, maxsplit=1)[0]
        return _ok(body, head_tok)
    if re.search(r"[ \t\u3000]", body):
        return False
    return _ok(body, body)

_JUMP_NAMES = ('ジャンプ', '最終ジャンプ', 'スクリプト')


def _looks_like_script(name):
    return bool(name) and name.lower().endswith('.txt')


def iter_jump_refs(line):
    """Yield (cmd, file_or_None, label) for every jump in the line.

    覆盖两种真实写法:
      1) 引号内的嵌套命令：  ~ボタン ... "もし (...) ジャンプ battle0.txt ターン終了"
      2) **引号外的参数链**：~ボタン アビ_釦閉じる 838 17 233 カメラ付着 ジャンプ ability.txt 閉じる
                             キーダウン r.click ジャンプ ability.txt 閉じる
        第 2 类在语料里有 5,218 处，旧实现（只看行尾引号参数）完全看不见它们。

    判定：把行按 ' | ' 切段；每段先剥 ~ 与 もし(...) 前缀，再按空白/引号切 token。
    遇到 _JUMP_NAMES 里的命令名，就取它后面的 1~2 个 token 作为 file/label：
      * 第 1 个以 .txt 结尾 -> 它是 file，第 2 个是 label
      * 否则第 1 个就是 label（省略文件名 = 当前文件）
    """
    for seg in split_segments_v2(line):
        for text in _seg_texts(seg):
            _had, rest = strip_condition_prefix(text)
            if not rest:
                continue
            toks = re.findall(r'"[^"]*"|[^\s"|]+', rest)
            for k, tok in enumerate(toks):
                if tok not in _JUMP_NAMES:
                    continue
                nxt = [t for t in toks[k + 1:] if not t.startswith('"')]
                if not nxt:
                    continue
                if _looks_like_script(nxt[0]):
                    file_ = nxt[0]
                    label = nxt[1] if len(nxt) > 1 else None
                else:
                    file_, label = None, nxt[0]
                if label:
                    yield tok, file_, label


def _seg_texts(seg):
    """一段里的「可解析文本」：head 本身 + 行尾引号参数（如果有）。"""
    out = [seg.get('head') or '']
    arg = seg.get('arg')
    if arg:
        out.append(arg)
    return out


def load_page_zh_raw(fid):
    """成品页的「原始」版本：不做 lvalign 剥离，行数与 .mcdat 一致。

    用途：R3 需要看插行块（特性表/技能名表初始化、lvHashMapHasKey 回退补键）里的
    赋值，这些行被 lvalign 当成「纯新增行」剥掉了。对齐后的页用于逐行对照，
    原始页用于收集赋值证据 —— 两者缺一不可。

    文件不存在（合成语料 / 单测的虚构 fid）时返回 None，调用方必须容忍。
    """
    import lvpage
    p = os.path.join(BLD_DIR, fid + ".mcdat")
    if not os.path.isfile(p):
        return None
    with open(p, "rb") as stream:
        raw = stream.read()
    try:
        import lvlib
        raw = lvlib.xor_crypt(raw)
    except ImportError:
        raise SystemExit("读取 .mcdat 需要解密实现；请先用 unpacker 解包并设置 LV_JP，或把 lvlib.py 放到 LV_LIB 指定的目录。")
    return lvpage.Page(raw)


def dropped_zh_lines(fid):
    """被 lvalign 剥掉的行文本（成品侧纯新增行），保持原顺序。

    成品文件不存在时返回 []（合成语料 / 单测场景）。
    """
    raw_page = load_page_zh_raw(fid)
    if raw_page is None:
        return []
    texts = [raw_page.text(i) for i in range(len(raw_page.prefix))]
    try:
        import lvalign
    except ImportError:
        return []
    keep, _ = lvalign.align(list(range(len(texts))), texts)
    kept = set(keep)
    return [texts[i] for i in range(len(texts)) if i not in kept]


def load_page_zh(fid):
    import lvpage
    p = os.path.join(BLD_DIR, fid + ".mcdat")
    with open(p, "rb") as stream:
        raw = stream.read()
    try:
        import lvlib
        raw = lvlib.xor_crypt(raw)
    except ImportError:
        raise SystemExit("读取 .mcdat 需要解密实现；请先用 unpacker 解包并设置 LV_JP，或把 lvlib.py 放到 LV_LIB 指定的目录。")
    page = lvpage.Page(raw)
    if os.environ.get('LV_NOALIGN'):
        return page
    try:
        import lvalign
    except ImportError:
        return page
    texts = [page.text(i) for i in range(len(page.prefix))]
    ok = os.environ.get('LV_NOALIGN')
    keep, _ = lvalign.align(list(range(len(texts))), texts)
    if len(keep) == len(texts):
        return page
    np = lvpage.Page(b'')
    np.prefix = [page.prefix[i] for i in keep]
    np.crsuf = [page.crsuf[i] for i in keep]
    np.tail = page.tail
    return np


def list_fids():
    out = []
    for fn in sorted(os.listdir(JP_DIR)):
        if fn.endswith('.txt'):
            out.append(fn[:-4])
    return out


def var_tokens(s):
    """All {{...}} variable names inside s (inner expressions kept verbatim)."""
    return [m.group(1).strip() for m in VAR_RE.finditer(s)]


def has_var(s):
    return '{{' in s


def var_refs(s):
    """Variable names referenced via {{...}}, dropping arithmetic suffixes."""
    names = []
    for expr in var_tokens(s):
        m = re.match(r'^([^\s+\-*/%<>=!&|()]+)', expr)
        if m:
            names.append(m.group(1))
    return names


def _ends_with_quote(s):
    t = s.rstrip(WS)
    if not t.endswith(QUOTE):
        return False
    n = len(t) - 1
    k = n - 1
    bs = 0
    while k >= 0 and t[k] == '\\':
        bs += 1
        k -= 1
    return bs % 2 == 0



def _head_command_accepts_nested(body):
    """行首命令是否接受 Light.vn コマンド 作为参数。"""
    t = body.lstrip(WS)
    while t.startswith("~"):
        t = t[1:].lstrip(WS)
    if not t or t.startswith(QUOTE):
        return False
    m = re.match(r"[^ \t\u3000\"]+", t)
    if not m:
        return False
    return needs_nested_parse(m.group(0))


def split_arg_quote(seg):
    """Split one segment into (head, quoted_arg | None).

    Light.vn 允许行尾引号参数内部再嵌命令、且内层引号不转义，例如：
        ~ボタン b m1.png 1 2 3 カメラ付着 "もし (x == "OFF") ジャンプ a.txt b"
    这种行用普通配对会切错。判定方法：看第一个引号内的内容是否以「命令名」开头
    （含 もし / 栞 等关键字），是则视为嵌套参数（结尾取行尾），否则按普通配对。
    """
    body = seg.rstrip(WS)
    if not body or not _ends_with_quote(body):
        return seg, None
    i, n = 0, len(body)
    first = -1
    while i < n:
        if body[i] == "\\":
            i += 2
            continue
        if body[i] == QUOTE:
            first = i
            break
        i += 1
    if first < 0:
        return seg, None
    inner = body[first + 1:len(body) - 1]
    if _ends_with_quote(body) and _starts_with_command(inner):
        # 只有当「该行的命令」本身接受 Light.vn コマンド 参数时才递归展开，
        # 否则像 文字 ... "ロード" 这种显示文本会被误当成嵌套命令。
        if _head_command_accepts_nested(body):
            return body[:first], inner
    return seg, None


def _starts_with_command(text):
    t = text.lstrip(WS)
    if not t:
        return False
    while t.startswith("~"):
        t = t[1:].lstrip(WS)
    if not t or t.startswith(QUOTE):
        return False
    m = re.match(r"[^ \t\u3000\"]+", t)
    if not m:
        return False
    tok = m.group(0)
    if tok in ("もし", "違ってももし", "違ったら", "栞", "bookmark", "if"):
        return True
    return tok in command_table() or tok in engine_keywords()


def split_segments_v2(s):
    """Split on ' | ' at top level, then separate each segment's quoted arg."""
    raw = split_segments(s)
    out = []
    for seg in raw:
        head, arg = split_arg_quote(seg)
        out.append({'seg': seg, 'head': head, 'arg': arg})
    return out

INVENTORY = os.environ.get('LV_INVENTORY') or os.path.join(lvroot.DATA_DIR, 'inventory.json')
REFPATHS = os.environ.get('LV_REFPATHS') or os.path.join(lvroot.DATA_DIR, 'refpaths.json')


def load_inventory():
    with open(INVENTORY, encoding='utf-8') as stream:
        return json.load(stream)


def segment_arginfo(it):
    """由 split_segments_v2 的一项还原「参数个数」。

    行尾引号参数在 Light.vn 里是最后 1 个参数（可能是嵌套命令，也可能是显示文本），
    所以参数个数 = head 里的参数数 + (1 if 有引号尾参数 else 0)。
    """
    head = it.get("head") or ""
    r = parse_segment(head)
    n = len(r["args"])
    if it.get("arg") is not None:
        n += 1
    return r, n


ENGINE_KW = os.environ.get('LV_KEYWORDS') or os.path.join(lvroot.DATA_DIR, 'engine_keywords.json')
_ENGINE_KW = None


def engine_keywords():
    """引擎编辑器分词表（Syntax_core.xshd 导出，含日文名与英文 id 别名）。"""
    global _ENGINE_KW
    if _ENGINE_KW is None:
        try:
            with open(ENGINE_KW, encoding='utf-8') as stream:
                d = json.load(stream)
            _ENGINE_KW = set(d.get('keywords_all') or [])
        except Exception:
            _ENGINE_KW = set()
    return _ENGINE_KW


_MACRO_NAMES = None

def macro_names():
    """从脚本里学到的 コマンドマクロ/マクロ 自定义命令名。"""
    global _MACRO_NAMES
    if _MACRO_NAMES is not None:
        return _MACRO_NAMES
    out = set()
    try:
        for fn in sorted(os.listdir(JP_DIR)):
            if not fn.endswith(".txt"):
                continue
            try:
                with open(os.path.join(JP_DIR, fn), encoding="utf-8", errors="replace") as stream:
                    txt = stream.read()
            except Exception:
                continue
            for ln in txt.replace(chr(13) + chr(10), chr(10)).split(chr(10)):
                s = ln.strip().lstrip("~.")
                if not s.startswith("コマンドマクロ") and not s.startswith("マクロ"):
                    continue
                parts = s.split()
                if len(parts) >= 2:
                    nm = parts[1].strip(chr(34))
                    if nm and not nm.startswith(chr(126)):
                        out.add(nm)
    except Exception:
        out = set()
    _MACRO_NAMES = out
    return out

_KNOWN_PREFIX = None

def _known_prefixes():
    global _KNOWN_PREFIX
    if _KNOWN_PREFIX is None:
        base = set(command_table()) | set(engine_keywords())
        _KNOWN_PREFIX = tuple(sorted(base, key=len, reverse=True))
    return _KNOWN_PREFIX

SENT_PUNCT = set("，。、！？…「」『』（）" + chr(34))
def command_variant_match(name):
    """引擎扩展命名: 移動x2 / 文字窓下段。
    严格限制, 避免把台词续行(イン... 开头)误判成命令:
      - 不含句子标点/引号
      - 后缀 1~4 字符
      - 不含反斜杠转义
    """
    if not name or len(name) < 3:
        return None
    for ch in name:
        if ch in SENT_PUNCT or ch == chr(92):
            return None
    for pre in _known_prefixes():
        if len(pre) < 2 or not name.startswith(pre) or name == pre:
            continue
        suffix = name[len(pre):]
        if 1 <= len(suffix) <= 4:
            return pre
    return None

def is_known_command(name):
    """命令表 / 引擎关键字 / 脚本宏 / 引擎扩展命名，任一命中即已知。"""
    if not name:
        return False
    if name in command_table() or name in engine_keywords():
        return True
    if name in macro_names():
        return True
    return command_variant_match(name) is not None
