# -*- coding: utf-8 -*-
"""Light.vn 汉化脚本静态校验器（linter）。

用法:
    python linter.py                 # 全量跑，输出 report.json / report.md / mapping.json
    python linter.py --only R1       # 只跑某条规则
    python linter.py --quiet         # 只打印最后汇总行

只读工具：不会修改目标游戏的任何文件。
"""
import os, sys, json, time, argparse, importlib, traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.stdout.reconfigure(encoding="utf-8")

import lvparse as P
import lvctx
import lvfind

RULES = ["R1", "R2", "R3", "R4", "R5", "R6", "R7"]
RULE_MODULES = {
    "R1": "rule_r1", "R2": "rule_r2", "R3": "rule_r3",
    "R4": "rule_r4", "R5": "rule_r5", "R6": "rule_r6", "R7": "rule_r7",
}
RULE_TITLES = {
    "R1": "资源引用完整性（贴图/音频丢失、路径被翻译）",
    "R2": "跳转引用完整性（黑屏、卡死）",
    "R3": "变量引用完整性（变量名被翻译、使用未赋值）",
    "R4": "命令语法校验（命令被翻译、参数缺失）",
    "R5": "逻辑键一致性（分支永远不成立）",
    "R6": "代码骨架一致性（引号外被改动）",
    "R7": "未翻译检测（两侧相同但仍是日文）",
}


def parse_args(argv):
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--only", action="append", default=None,
                    help="只跑指定规则，如 R1 或 R1,R2；可重复")
    ap.add_argument("--quiet", action="store_true", help="只打印最后汇总行")
    ap.add_argument("--out-dir", default=None, help="输出目录，默认工作区根")
    return ap.parse_args(argv)


def resolve_rules(only):
    if not only:
        return list(RULES)
    wanted = []
    for item in only:
        for part in item.replace("，", ",").split(","):
            p = part.strip().upper()
            if not p:
                continue
            if p not in RULES:
                raise SystemExit("未知规则 %r，可选: %s" % (part, ", ".join(RULES)))
            if p not in wanted:
                wanted.append(p)
    return wanted


_TIMED = {}


def run_rule(name, ctx):
    modname = RULE_MODULES[name]
    try:
        mod = importlib.import_module(modname)
    except Exception:
        print("!! 规则模块 %s 导入失败:" % modname, file=sys.stderr)
        traceback.print_exc()
        return [], "import-failed"
    fn = getattr(mod, "run", None)
    if fn is None:
        print("!! %s 缺少 run(ctx)" % modname, file=sys.stderr)
        return [], "no-run"
    t0 = time.time()
    try:
        out = fn(ctx) or []
    except Exception:
        print("!! 规则 %s 运行失败:" % name, file=sys.stderr)
        traceback.print_exc()
        return [], "run-failed"
    _TIMED[name] = time.time() - t0
    clean = []
    for f in out:
        if not isinstance(f, dict):
            continue
        f.setdefault("rule", name)
        f.setdefault("severity", "mid")
        f.setdefault("jp", "")
        f.setdefault("zh", "")
        f.setdefault("detail", "")
        if "code" not in f:
            f["code"] = "unspecified"
        if "file" not in f or "line" not in f:
            continue
        clean.append(f)
    if not clean and out:
        print("!! 规则 %s 返回了 %d 条但全部格式不合法" % (name, len(out)),
              file=sys.stderr)
    return clean, "ok"


def write_report_json(findings, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(findings, f, ensure_ascii=False, indent=1)


def _dist(findings):
    codes = {}
    sev = {"high": 0, "mid": 0, "low": 0}
    files = {}
    for f in findings:
        codes[f["code"]] = codes.get(f["code"], 0) + 1
        sev[f["severity"]] = sev.get(f["severity"], 0) + 1
        files[f["file"]] = files.get(f["file"], 0) + 1
    return codes, sev, files


def _clip(s, n=320):
    s = (s or "").replace("\r", " ").replace("\n", "\\n")
    if len(s) <= n:
        return s
    return s[:n] + " ..."


def write_report_md(findings, path, meta):
    L = []
    A = L.append
    A("# Light.vn 汉化脚本静态校验报告")
    A("")
    A("- 日文原版脚本：%d 个文件 / %d 行" % (meta["files"], meta["lines"]))
    A("- 汉化成品脚本：%d 个文件" % meta["zh_files"])
    A("- 生成方式：python linter.py（只读，不修改任何脚本）")
    A("- 行号约定：file#N 为 1-based（首行为 1）；report.json 每项含 line_base: 1")

    _int = sum(1 for f in findings if f.get("origin") == "intentional")
    _tr = sum(1 for f in findings if f.get("origin") == "translation")
    _ih = sum(1 for f in findings if f.get("origin") == "inherited")
    A("")
    A("## 核心结论")
    A("")
    A("- **翻译引入的问题：%d 条** —— 这些是日文与成品存在差异、可能影响游戏功能的问题，应当优先看。" % _tr)
    A("- 原版固有 / 仅供参考：%d 条 —— 日文与成品该行完全一致，属于原版本身就有的写法，不是汉化造成的。" % _ih)
    A("- 有意变更（白名单）：%d 条 —— 已确认是有意为之，不计入翻译引入。" % _int)

    if meta.get("only"):
        A("- 本次仅运行：%s" % ", ".join(meta["only"]))
    A("")
    A("## 总览")
    A("")
    A("| 规则 | 说明 | 总数 | 翻译引入 | 原版固有 | high | mid | low |")
    A("|---|---|---|---|---|---|---|---|")
    for r in RULES:
        sub = [f for f in findings if f["rule"] == r]
        _, sev, _ = _dist(sub)
        A("| %s | %s | %d | %d | %d | %d | %d | %d |" % (
            r, RULE_TITLES[r], len(sub),
            sum(1 for f in sub if f.get("origin") == "translation"),
            sum(1 for f in sub if f.get("origin") == "inherited"),
            sev["high"], sev["mid"], sev["low"]))
    A("| **合计** | | **%d** | **%d** | **%d** | | | |" % (
        len(findings),
        sum(1 for f in findings if f.get("origin") == "translation"),
        sum(1 for f in findings if f.get("origin") == "inherited")))
    A("")
    for r in RULES:
        sub = [f for f in findings if f["rule"] == r]
        if not sub and r not in (meta.get("only") or RULES):
            continue
        A("## %s %s" % (r, RULE_TITLES[r]))
        A("")
        codes, sev, files = _dist(sub)
        A("**总数：%d**（high %d / mid %d / low %d）" % (
            len(sub), sev["high"], sev["mid"], sev["low"]))
        A("")
        if not sub:
            A("无问题。")
            A("")
            continue
        A("### 按类型分布")
        A("")
        A("| code | 数量 |")
        A("|---|---|")
        for k, v in sorted(codes.items(), key=lambda kv: (-kv[1], kv[0])):
            A("| %s | %d |" % (k, v))
        A("")
        A("### 涉及文件分布（前 15）")
        A("")
        top = sorted(files.items(), key=lambda kv: (-kv[1], kv[0]))[:15]
        A("| 文件 | 条数 |")
        A("|---|---|")
        for k, v in top:
            A("| %s | %d |" % (k, v))
        if len(files) > 15:
            A("")
            A("（共 %d 个文件命中，仅列前 15）" % len(files))
        A("")
        A("### 样例（前 20 条）")
        A("")
        for f in sub[:20]:
            A("#### %s #%d --- %s (%s, %s)" % (
                f["file"], f["line"], f["code"], f["severity"],
                f.get("origin") or "?"))
            if f.get("severity_note"):
                A("")
                A("> %s" % f["severity_note"])
            A("")
            A("```")
            A("JP: " + _clip(f.get("jp")))
            A("ZH: " + _clip(f.get("zh")))
            A("```")
            if f.get("detail"):
                A("")
                A(_clip(f["detail"], 700))
            A("")
        A("")
    A("---")
    A("")
    A("## 覆盖范围与已知限制")
    A("")
    L.extend(meta.get("coverage_notes") or [])
    A("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))


_ASSIGN_CMD = ("保存変数", "変数", "臨時全域変数", "全域変数")
_VAR_IN_DETAIL = None


def _build_assign_diff(ctx):
    """变量 -> 它在成品里被改写过的赋值行 [(fid, line0), ...]。

    只扫描 JP != ZH 的行，并正确处理 もし(...) 前缀与 ' | ' 多段赋值。
    """
    out = {}
    for fid in ctx.fids():
        n = ctx.line_count(fid)
        for i in range(n):
            jl = ctx.jp_line(fid, i)
            if not jl:
                continue
            head0 = jl.lstrip(" " + chr(9) + chr(0x3000))
            if head0.startswith("//"):
                continue
            zl = ctx.zh_line(fid, i)
            if zl is None or zl == jl:
                continue
            for seg in P.split_segments_v2(jl):
                r = P.parse_segment(seg.get("head") or "")
                if r["text_only"] or r["command"] not in _ASSIGN_CMD:
                    continue
                if r["args"]:
                    out.setdefault(r["args"][0], []).append((fid, i))
    return out


INTENTIONAL = (os.environ.get('LV_INTENTIONAL')
               or os.path.join(_HERE, 'intentional.json'))
_INTENTIONAL = None


def _intentional_set():
    global _INTENTIONAL
    if _INTENTIONAL is None:
        try:
            d = json.load(open(INTENTIONAL, encoding='utf-8'))
            _INTENTIONAL = {(str(e.get('file')), int(e.get('line'))): e for e in d.get('entries', [])}
        except Exception:
            _INTENTIONAL = {}
    return _INTENTIONAL


def classify_origin(findings, ctx, assign_diff):
    """标注每条 finding 的来源，并回填 cause 行号。

    translation = 该行 JP/ZH 不同，或该 finding 涉及变量的赋值行被改写过；
    inherited   = 两侧完全一致，属于原版本身就有的现象，不是汉化引入。
    """
    global _VAR_IN_DETAIL
    if _VAR_IN_DETAIL is None:
        import re as _re
        _VAR_IN_DETAIL = _re.compile(r'变量\s+([^\s，,。；;]+)')

    import re
    _intent = _intentional_set()
    for f in findings:
        fid = f.get("file")
        ln = f.get("line")
        _key = (str(fid), int(ln)) if isinstance(ln, int) else None
        if _key is not None and _key in _intent:
            _e = _intent[_key]
            f["origin"] = "intentional"
            f["severity"] = "low"
            f["severity_note"] = "有意变更白名单：%s" % _e.get("reason", "")
            continue
        jp = f.get("jp") or ""
        zh = f.get("zh") or ""
        cause = []
        if jp != zh:
            f["origin"] = "translation"
            continue
        detail = f.get("detail") or ""
        if "非汉化引入" in detail:
            f["origin"] = "inherited"
            continue
        m = _VAR_IN_DETAIL.search(detail) if _VAR_IN_DETAIL else None
        var = m.group(1) if m else None
        if var is None:
            mv = re.search(r"\{\{([^}]+)\}\}", detail)
            if mv:
                var = mv.group(1)
        if var:
            mv2 = re.search(r"\{\{([^}]+)\}\}", var)
            if mv2:
                var = mv2.group(1)
            var = var.strip().strip("「」:：").split("+")[0].split("-")[0].strip()
        if var and var in assign_diff:
            cause = [list(x) for x in assign_diff[var][:5]]
            f["origin"] = "translation"
            f["cause"] = cause
        else:
            f["origin"] = "inherited"
    return findings


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    rules = resolve_rules(args.only)
    out_dir = args.out_dir or P.ROOT
    os.makedirs(out_dir, exist_ok=True)
    t_start = time.time()
    ctx = lvctx
    fids = ctx.fids()
    total_lines = sum(ctx.line_count(fid) for fid in fids)
    findings = []
    status = {}
    for r in rules:
        got, st = run_rule(r, ctx)
        status[r] = st
        findings.extend(got)
        if not args.quiet:
            print("[%s] %-58s %6d 条  (%.1fs)" % (
                r, RULE_TITLES[r], len(got), _TIMED.get(r, 0.0)))
    findings = classify_origin(findings, ctx, _build_assign_diff(ctx))
    findings = lvfind.finalize(findings)
    counts, sev, codes = lvfind.summarize(findings)
    mapping_path = os.path.join(out_dir, "mapping.json")
    mapping = None
    if "R2" in rules:
        try:
            m = importlib.import_module("rule_r2")
            if hasattr(m, "build_mapping"):
                mapping = m.build_mapping(ctx)
        except Exception:
            print("!! build_mapping 失败:", file=sys.stderr)
            traceback.print_exc()
    json_path = os.path.join(out_dir, "report.json")
    md_path = os.path.join(out_dir, "report.md")
    for _f in findings:
        _f["line_base"] = 1
    write_report_json(findings, json_path)
    meta = {
        "files": len(fids),
        "lines": total_lines,
        "zh_files": sum(1 for fid in fids if ctx.zh_page(fid) is not None),
        "only": rules if args.only else None,
        "coverage_notes": build_coverage_notes(ctx, mapping),
    }
    write_report_md(findings, md_path, meta)
    if mapping is not None:
        with open(mapping_path, "w", encoding="utf-8") as fh:
            json.dump(mapping, fh, ensure_ascii=False, indent=1)
    if not args.quiet:
        print()
        print("输出: %s" % json_path)
        print("输出: %s" % md_path)
        if mapping is not None:
            print("输出: %s" % mapping_path)
        bad = [r for r, st in status.items() if st != "ok"]
        if bad:
            print("!! 以下规则未正常完成: %s" % ", ".join(bad), file=sys.stderr)
        print("总耗时 %.1fs" % (time.time() - t_start))
    _rule_parts = " ".join("%s=%d" % (r, counts.get(r, 0)) for r in rules)
    print("files=%d lines=%d  %s" % (len(fids), total_lines, _rule_parts))
    return 0


def build_coverage_notes(ctx, mapping):
    n = []
    A = n.append
    A("### 已覆盖")
    A("")
    A("- **R1**：资源命令（絵/画像/背景/背景音/効果音/ボイス/動画/アニメ登録 等）的路径引用，")
    A("  包含 {{变量}} 拼接路径的取值展开；以「原版路径集合」为基准做差异比对。")
    A("- **R2**：栞 标签定义、ジャンプ/スクリプト/最終ジャンプ 跳转（含引号内嵌套命令、")
    A("  | 串联、もし(...) 之后的命令），动态标签变量取值展开。")
    A("- **R3**：{{变量}} 使用点 vs 赋值点，报「使用但从未赋值」。")
    A("- **R4**：行首命令名 vs 官方 Commands.xml（419 条），含必填参数数量下限检查。")
    A("- **R5**：被 ==/!= 比较过的字符串键 vs 赋值字面量，捕获「逻辑键被翻译」。")
    A("- **R6**：逐行挖空引号内容后比对日文/成品骨架，捕获变量名、命令、数字、路径被改动。")
    A("")
    A("### 未覆盖")
    A("")
    A("- **资源文件存在性**：资源以数字 .mcdat 打包（66,596 PNG / 4,908 OGG），")
    A("  原版加密已剥离文件名，不存在文件名到文件号的映射，也无法反推。")
    A("  因此 R1 只能做「原版引用 vs 成品引用」的集合差异，不能判断具体文件是否在包内。")
    A("- **运行时行为**：不执行游戏，不覆盖变量实际取值、条件实际求值、状态机可达性。")
    A("- **非脚本资源内的文本**：着色器、JSON 数据块、字体等不在本 linter 范围。")
    A("- **脚本名到文件号映射**：只能用标签集合启发式建立，无法完全消歧；")
    A("  ambiguous / unresolved 的条目不计入错误。")
    A("")
    A("### 已知误报来源")
    A("")
    A("- R3：引擎内置变量（vn_sys*、lv* 等）可能被报为未赋值；已按前缀与出现频次分档降级。")
    A("- R4：命令表未收录的自定义/宏命令可能被报为 unknown_command；")
    A("  已限制为「日文原版也有」或「形似命令」的才报。")
    A("- R5：同一变量在不同分支被赋予「比较键」和「显示文本」两种用途时可能误报，")
    A("  已通过「仅检查被比较过的变量」收敛。")
    A("- R1：变量取值集合可能不完整（赋值来自运行时或存档），展开结果偏少时宁可不报。")
    if mapping:
        c = {}
        for v in mapping.values():
            k = v.get("status", "?")
            c[k] = c.get(k, 0) + 1
        A("")
        A("### R2 映射统计")
        A("")
        A("- 已建立映射的脚本名：%d（unique %d / ambiguous %d / unresolved %d）" % (
            len(mapping), c.get("unique", 0), c.get("ambiguous", 0), c.get("unresolved", 0)))
    return n


if __name__ == "__main__":
    raise SystemExit(main())
