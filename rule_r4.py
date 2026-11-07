# -*- coding: utf-8 -*-
"""R4 命令语法校验。

规则：
  1. 行首命令名不在官方命令表 -> unknown_command（命令被翻译/拼错）
  2. 参数个数少于签名必填数 -> missing_argument
  3. 递归校验引号内嵌套命令（ボタン/タッチ終了時/もし 等）

文本行（对话）不参与；// 注释跳过；着色器/JSON 数据文件自动跳过。
"""
import os, sys, re

import lvparse as P
import lvfind

RULE = "R4"

# 这些命令允许「只给变量名」的简写形式（声明/清空变量）
DECLARE_OK = frozenset([
    "保存変数", "変数", "臨時全域変数", "全域変数", "変数式確認機",
])


def _is_non_script(fid, ctx):
    """粗判：前 40 行里没有出现任何已知命令，就是着色器或数据文件。"""
    n = min(ctx.line_count(fid), 40)
    table = P.command_table()
    hit = 0
    for i in range(n):
        s = ctx.jp_line(fid, i).strip()
        if not s or s.startswith("//"):
            continue
        if s.startswith("#") or s.startswith("precision ") or s.startswith("uniform "):
            return True
        tok = re.split(r"[ \t\u3000\"]+", s, maxsplit=1)[0]
        if tok in table or tok in ("栞", "もし"):
            hit += 1
    return hit == 0


def _line_starts_as_command(line, table):
    """整行是否以命令开头（而非对话文本）。"""
    s = line.lstrip(" \t\u3000")
    if not s:
        return False
    while s.startswith("~"):
        s = s[1:].lstrip(" \t\u3000")
    if not s or s.startswith(chr(34)):
        return False
    tok = re.split(r"[ \t\u3000\"]+", s, maxsplit=1)[0]
    if tok in table or tok in ("栞", "もし", "違ってももし"):
        return True
    return False


def _lead_token(line):
    """行首 token（去掉 ~ 前缀与空白）；文本行返回空串。"""
    s = line.lstrip(" " + chr(9) + chr(0x3000))
    while s.startswith("~"):
        s = s[1:].lstrip(" " + chr(9) + chr(0x3000))
    if not s or s.startswith(chr(34)):
        return ""
    parts = s.split(None, 1)
    return parts[0] if parts else ""


def _iter_commands(line, table):
    """Yield (cmd, argc, nested, head, arg).

    只有确认为命令行的行才解析；对话文本一律跳过。
    """
    if not _line_starts_as_command(line, table):
        return
    for it in P.split_segments_v2(line):
        head = it.get("head") or ""
        arg = it.get("arg")
        r = P.parse_segment(head)
        if not r["text_only"]:
            yield r["command"], len(r["args"]) + (1 if arg is not None else 0), 0, head, arg
            if arg is not None and P.needs_nested_parse(r["command"]):
                for it2 in P.split_segments_v2(arg):
                    h2 = it2.get("head") or ""
                    a2 = it2.get("arg")
                    r2 = P.parse_segment(h2)
                    if r2["text_only"]:
                        continue
                    yield (r2["command"], len(r2["args"]) + (1 if a2 is not None else 0),
                           1, h2, a2)


def _looks_like_command(tok):
    if not tok or len(tok) < 2:
        return False
    if re.match(r"^[A-Za-z][A-Za-z0-9_]*$", tok):
        return True
    if re.match(r"^[\u30a0-\u30ff]", tok):
        return True
    if re.match(r"^[\u4e00-\u9fff]", tok):
        return True
    return False


def _min_required(sig):
    """多签名（1. ... 2. ...）时取最小必填数，避免误报。"""
    if sig is None:
        return None
    syn = sig.get("syntax") or ""
    # 用正则找出所有数字序号分隔的候选签名块
    parts = re.split(r"(?:^|\s)\d+\.\s", syn)
    if len(parts) <= 1:
        return sig["req"]
    best = None
    for part in parts:
        part = part.strip()
        if not part:
            continue
        tmp = P.signature.__wrapped__(part) if hasattr(P.signature, "__wrapped__") else None
        # 直接数方括号
        toks = [t for t in P._split_syntax(part) if t[:1] in "[(<"]
        reqs = [t for t in toks if t[0] == "["]
        if best is None or len(reqs) < best:
            best = len(reqs)
    return sig["req"] if best is None else min(best, sig["req"])


def run(ctx):
    table = P.command_table()
    sig_cache = {}

    def sig(cmd):
        if cmd not in sig_cache:
            sig_cache[cmd] = P.signature(cmd)
        return sig_cache[cmd]

    scripts = [fid for fid in ctx.fids() if not _is_non_script(fid, ctx)]

    jp_cmds = set()
    for fid in scripts:
        for i in range(ctx.line_count(fid)):
            line = ctx.jp_line(fid, i)
            if not line.strip() or line.strip().startswith("//"):
                continue
            for cmd, argc, nested, head, arg in _iter_commands(line, table):
                jp_cmds.add(cmd)

    out = []
    for fid in scripts:
        n = ctx.line_count(fid)
        for i in range(n):
            jline = ctx.jp_line(fid, i)
            if not jline.strip() or jline.strip().startswith("//"):
                continue
            zline = ctx.zh_line(fid, i) or ""
            jcmds = []
            for cmd, argc, nested, head, arg in _iter_commands(jline, table):
                jcmds.append(cmd)
                s = sig(cmd)
                if s is None:
                    if not P.is_known_command(cmd) and _looks_like_command(cmd) and cmd not in jp_cmds:
                        out.append(lvfind.make(
                            RULE, "unknown_command", "high", fid, i + 1,
                            jline, zline,
                            "命令 %r 不在 Commands.xml 命令表中" % cmd,
                            severity_note="日文原版整份语料都没出现过这个命令名"))
                    continue
                need = _min_required(s)
                # 变量声明类命令允许只写变量名（引擎会把变量置空/声明）
                if cmd in DECLARE_OK and argc == 1:
                    continue
                # 赋值语法(X = / X +=): 引擎允许右侧为空或为表达式, 不按位置参数校验
                if _is_assignment_form(head):
                    continue
                if argc < need:
                    out.append(lvfind.make(
                        RULE, "missing_argument", "mid", fid, i + 1,
                        jline, zline,
                        "命令 %r 需要 >= %d 个必填参数，实际 %d 个；签名: %s"
                        % (cmd, need, argc,
                           " ".join((s.get("syntax") or "").split())[:150])))

            # 成品侧：命令名被翻译
            if not zline.strip() or zline.strip().startswith("//"):
                continue
            for cmd, argc, nested, head, arg in _iter_commands(zline, table):
                if P.is_known_command(cmd) or cmd in jp_cmds or not _looks_like_command(cmd):
                    continue
                out.append(lvfind.make(
                    RULE, "unknown_command", "high", fid, i + 1,
                    jline, zline,
                    "成品出现未知命令 %r；日文原版同一行的命令是 %r（疑似命令被翻译）"
                    % (cmd, jcmds[:3]),
                    severity_note="该命令名不在官方命令表，且只出现在成品里"))
    # ---- 3) 命令名被翻译：同一行首 token 原版是官方命令、成品不是 ----
    for fid in scripts:
        n = ctx.line_count(fid)
        for i in range(n):
            jl = ctx.jp_line(fid, i)
            zl = ctx.zh_line(fid, i)
            if not jl or not zl:
                continue
            jt = _lead_token(jl)
            zt = _lead_token(zl)
            if not jt or not zt or jt == zt:
                continue
            if not P.is_known_command(jt) or P.is_known_command(zt):
                continue
            if zt in jp_cmds:
                continue
            if not _looks_like_command(zt):
                continue
            out.append(lvfind.make(
                RULE, "unknown_command", "high", fid, i + 1, jl, zl,
                "命令名被翻译：原版 %s -> 成品 %s" % (jt, zt),
                severity_note="该行首 token 在原版是官方命令、在成品不是"))

    return out

ASSIGN_OPS = ("+=", "-=", "*=", "/=", "%=", "=")

def _is_assignment_form(head):
    """seg 头部是否为 变量 X = 值 形式。"""
    if not head:
        return False
    parts = head.split()
    for k, tok in enumerate(parts):
        if tok in ASSIGN_OPS:
            return True
    return False
