# -*- coding: utf-8 -*-
"""R3 / R5 合成用例自测 + 真实语料汇总（只读）。

用法：
    python -X utf8 _test_r3r5.py            # 合成用例 + 真实语料统计
    python -X utf8 _test_r3r5.py --syn      # 只跑合成用例（快）
"""
import os
import sys
import importlib

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _HERE)
sys.stdout.reconfigure(encoding='utf-8')

import lvparse as P
import lvctx

Q = chr(34)
PASS = []
FAIL = []


def check(name, cond, msg=''):
    if cond:
        PASS.append(name)
        print('  PASS  ' + name)
    else:
        FAIL.append((name, msg))
        print('  FAIL  ' + name + '   ' + msg)


class FakePage:
    """最小可用的 Page 替身：只需要 prefix / text()。"""

    def __init__(self, rows):
        self.prefix = [r.encode('utf-8') for r in rows]

    def text(self, i):
        return self.prefix[i].decode('utf-8')


def run_synthetic(modname, pairs):
    """在内存里临时替换语料，跑一遍规则，返回 __syn 前缀的 findings。

    pairs: [(fid, jp_lines, zh_lines)]
    """
    try:
        mod = importlib.import_module(modname)
    except Exception as e:
        return None, '%s import failed: %s' % (modname, e)

    corpus = lvctx._load_all()
    saved = []
    added = []
    for fid, jl, zl in pairs:
        saved.append((fid, corpus['jp'].get(fid), corpus['zh'].get(fid)))
        corpus['jp'][fid] = FakePage(jl)
        corpus['zh'][fid] = FakePage(zl)
        if fid not in corpus['fids']:
            corpus['fids'] = list(corpus['fids']) + [fid]
            added.append(fid)
    # 索引缓存按 (fid, 行数) 指纹失效，必须显式清空
    if hasattr(mod, 'reset_cache'):
        mod.reset_cache()

    try:
        got = mod.run(lvctx) or []
        err = None
    except Exception as e:
        import traceback
        traceback.print_exc()
        got = []
        err = str(e)
    finally:
        for fid, sj, sz in saved:
            if sj is None:
                corpus['jp'].pop(fid, None)
            else:
                corpus['jp'][fid] = sj
            if sz is None:
                corpus['zh'].pop(fid, None)
            else:
                corpus['zh'][fid] = sz
        for fid in added:
            corpus['fids'] = [x for x in corpus['fids'] if x != fid]
        if hasattr(mod, 'reset_cache'):
            mod.reset_cache()

    return [g for g in got if str(g.get('file', '')).startswith('__syn')], err


def show(got, n=6):
    for g in got[:n]:
        print('     -> %s/%s #%s [%s]  %s' % (
            g.get('rule'), g.get('code'), g.get('line'), g.get('severity'),
            (g.get('detail') or '')[:100]))


def codes_of(got):
    return sorted({g.get('code') for g in got})


# ---------------------------------------------------------------- 合成用例

def case_r5_translated_high():
    """もし (x == 'OFF') + 保存変数 x = '无' -> logic_key_translated / high。"""
    print()
    print('[R5-a] 比较键 OFF 被翻译成中文值，分支永远不成立 -> high')
    jp = ['保存変数 x = ' + Q + 'OFF' + Q,
          'もし (x == ' + Q + 'OFF' + Q + ') ジャンプ a.txt 分支A']
    zh = ['保存変数 x = ' + Q + '无' + Q,
          'もし (x == ' + Q + 'OFF' + Q + ') ジャンプ a.txt 分支A']
    got, err = run_synthetic('rule_r5', [('__syn_r5a', jp, zh)])
    if err:
        check('R5-a 可运行', False, err)
        return
    show(got)
    hi = [g for g in got if g['code'] == 'logic_key_translated']
    check('R5-a 报出 logic_key_translated/high',
          len(hi) == 1 and hi[0]['severity'] == 'high',
          '实际 %r' % [(g['code'], g['severity']) for g in got])
    check('R5-a 行号 1-based 且指向比较行', bool(hi) and hi[0]['line'] == 2,
          'line=%r' % (hi[0]['line'] if hi else None))


def case_r5_display_text_no_report():
    """臨時全域変数 スキル効果 = 中文说明，从没被比较 -> 必须不报。"""
    print()
    print('[R5-b] 纯显示用赋值（スキル効果）无比较点 -> 必须不报')
    jp = ['臨時全域変数 スキル効果 = ' + Q + '敵1体に大ダメージを与える' + Q,
          '文字 t 0 0 0 f 20 ' + Q + '{{スキル効果}}' + Q]
    zh = ['臨時全域変数 スキル効果 = ' + Q + '对 1 名敌人造成大量伤害' + Q,
          '文字 t 0 0 0 f 20 ' + Q + '{{スキル効果}}' + Q]
    got, err = run_synthetic('rule_r5', [('__syn_r5b', jp, zh)])
    if err:
        check('R5-b 可运行', False, err)
        return
    if got:
        show(got)
    check('R5-b 纯显示赋值不报', len(got) == 0,
          '误报 %d 条：%r' % (len(got), codes_of(got)))


def case_r5_mismatch_mid():
    """键被删掉（非翻译）-> logic_key_mismatch / mid。"""
    print()
    print('[R5-c] 比较键在成品里被删除、且不是翻译 -> logic_key_mismatch / mid')
    jp = ['保存変数 モード = ' + Q + 'battle' + Q,
          'もし (モード == ' + Q + 'battle' + Q + ') ジャンプ a.txt 戦闘']
    zh = ['保存変数 モード = ' + Q + 'fight' + Q,
          'もし (モード == ' + Q + 'battle' + Q + ') ジャンプ a.txt 戦闘']
    got, err = run_synthetic('rule_r5', [('__syn_r5c', jp, zh)])
    if err:
        check('R5-c 可运行', False, err)
        return
    show(got)
    mid = [g for g in got if g['code'] == 'logic_key_mismatch']
    check('R5-c 报出 logic_key_mismatch/mid',
          len(mid) == 1 and mid[0]['severity'] == 'mid',
          '实际 %r' % [(g['code'], g['severity']) for g in got])


def case_r5_dynamic_ambiguous():
    """成品存在动态赋值 -> 不能硬判，标 ambiguous。"""
    print()
    print('[R5-d] 成品有 {{...}} 动态赋值 -> mid + ambiguous')
    jp = ['保存変数 ラベル = ' + Q + 'A' + Q,
          'もし (ラベル == ' + Q + 'A' + Q + ') ジャンプ a.txt x']
    zh = ['保存変数 ラベル = {{別の変数}}',
          'もし (ラベル == ' + Q + 'A' + Q + ') ジャンプ a.txt x']
    got, err = run_synthetic('rule_r5', [('__syn_r5d', jp, zh)])
    if err:
        check('R5-d 可运行', False, err)
        return
    show(got)
    check('R5-d 存在 ambiguous 标记',
          all(g.get('severity_note') == 'ambiguous' for g in got) and len(got) > 0,
          '实际 %r' % [(g['code'], g.get('severity_note')) for g in got])


def case_r3_undefined():
    """使用 {{未定义}} 无赋值（日文侧也没有）-> undefined_var。"""
    print()
    print('[R3-a] 成品新增 {{未定义}} 无任何赋值 -> undefined_var')
    jp = ['文字 t 0 0 0 f 20 ' + Q + 'こんにちは' + Q]
    zh = ['文字 t 0 0 0 f 20 ' + Q + '{{未定义变量甲}}' + Q]
    got, err = run_synthetic('rule_r3', [('__syn_r3a', jp, zh)])
    if err:
        check('R3-a 可运行', False, err)
        return
    show(got)
    und = [g for g in got if g['code'] == 'undefined_var']
    check('R3-a 报出 undefined_var', len(und) == 1,
          '实际 %r' % [(g['code'], g['severity']) for g in got])
    check('R3-a 出现 1 次 -> low + low-frequency',
          bool(und) and und[0]['severity'] == 'low'
          and und[0].get('severity_note') == 'low-frequency',
          '实际 %r' % ([(g['severity'], g.get('severity_note')) for g in und]))


def case_r3_undefined_frequent():
    """出现 >= 3 次 -> mid，不标 low-frequency。"""
    print()
    print('[R3-b] 成品新增变量出现 3 次 -> mid')
    jp = ['文字 t 0 0 0 f 20 ' + Q + 'あ' + Q] * 3
    zh = ['文字 t 0 0 0 f 20 ' + Q + '{{新增变量乙}}' + Q] * 3
    got, err = run_synthetic('rule_r3', [('__syn_r3b', jp, zh)])
    if err:
        check('R3-b 可运行', False, err)
        return
    show(got)
    und = [g for g in got if g['code'] == 'undefined_var']
    check('R3-b 报出 undefined_var/mid',
          len(und) == 1 and und[0]['severity'] == 'mid',
          '实际 %r' % [(g['code'], g['severity']) for g in got])


def case_r3_engine_var_low():
    """两侧都只使用不赋值（引擎内置）-> 不排除，但降为 low。"""
    print()
    print('[R3-c] vn_sysVoiceVolume 两侧都只使用 -> undefined_var/low + engine-prefix')
    jp = ['文字 t 0 0 0 f 20 ' + Q + '{{vn_syn_probe_zz}}' + Q]
    zh = ['文字 t 0 0 0 f 20 ' + Q + '{{vn_syn_probe_zz}}' + Q]
    got, err = run_synthetic('rule_r3', [('__syn_r3c', jp, zh)])
    if err:
        check('R3-c 可运行', False, err)
        return
    eng = [g for g in got if g['code'] == 'undefined_var'
           and 'vn_syn_probe_zz' in g.get('detail', '')]
    check('R3-c 引擎变量降级为 low',
          len(eng) == 1 and eng[0]['severity'] == 'low',
          '实际 %r' % [(g['code'], g['severity']) for g in got])


def case_r3_lost_in_zh():
    """日文有赋值、成品只使用 -> var_lost_in_zh / high。"""
    print()
    print('[R3-d] 日文赋值、成品只使用 -> var_lost_in_zh/high')
    jp = ['保存変数 地図_ゾンビ = 1',
          '文字 t 0 0 0 f 20 ' + Q + '{{地図_ゾンビ}}' + Q]
    zh = ['保存変数 地図_丧尸 = 1',
          '文字 t 0 0 0 f 20 ' + Q + '{{地図_ゾンビ}}' + Q]
    got, err = run_synthetic('rule_r3', [('__syn_r3d', jp, zh)])
    if err:
        check('R3-d 可运行', False, err)
        return
    show(got)
    lost = [g for g in got if g['code'] == 'var_lost_in_zh']
    check('R3-d 报出 var_lost_in_zh/high',
          len(lost) == 1 and lost[0]['severity'] == 'high',
          '实际 %r' % [(g['code'], g['severity']) for g in got])


def case_r3_renamed():
    """命令行内 {{旧名}} 被做成 {{新名}}，新名没有赋值 -> var_renamed / high。"""
    print()
    print('[R3-e] 命令行内 {{胎児}} -> {{胎児_表示}} -> var_renamed/high')
    jp = ['保存変数 胎児x = ' + Q + 'a' + Q,
          '文字 t 0 0 0 f 20 ' + Q + '{{胎児x}}' + Q]
    zh = ['保存変数 胎児x = ' + Q + 'a' + Q,
          '文字 t 0 0 0 f 20 ' + Q + '{{胎児x_表示}}' + Q]
    got, err = run_synthetic('rule_r3', [('__syn_r3e', jp, zh)])
    if err:
        check('R3-e 可运行', False, err)
        return
    show(got)
    ren = [g for g in got if g['code'] == 'var_renamed']
    check('R3-e 报出 var_renamed/high',
          len(ren) == 1 and ren[0]['severity'] == 'high',
          '实际 %r' % [(g['code'], g['severity']) for g in got])


def case_r3_text_reorder_silent():
    """文本行内变量换序（不是改名）-> 不能报 var_renamed。"""
    print()
    print('[R3-f] 对话文本行内变量换序 -> 不得报 var_renamed')
    jp = ['淫乱度 + {{上昇淫乱度}}']
    zh = ['{{上昇セクハラ}} 性骚扰 +']
    got, err = run_synthetic('rule_r3', [('__syn_r3f', jp, zh)])
    if err:
        check('R3-f 可运行', False, err)
        return
    if got:
        show(got)
    check('R3-f 换序不报 var_renamed',
          not [g for g in got if g['code'] == 'var_renamed'],
          '误报：%r' % codes_of(got))


# ---------------------------------------------------------------- 真实语料

def real_corpus():
    """跑真实语料，打印数量 / 分布 / 前 5 条样例。"""
    import time
    import collections

    print()
    print('=' * 72)
    print('真实语料统计（1114 个文件 / 736,569 行）')
    print('=' * 72)

    for name, modname in (('R3', 'rule_r3'), ('R5', 'rule_r5')):
        mod = importlib.import_module(modname)
        if hasattr(mod, 'reset_cache'):
            mod.reset_cache()
        t0 = time.time()
        got = mod.run(lvctx) or []
        dt = time.time() - t0

        codes = collections.Counter(g['code'] for g in got)
        sev = collections.Counter(g['severity'] for g in got)
        notes = collections.Counter(str(g.get('severity_note')) for g in got)
        files = len({g['file'] for g in got})

        print()
        print('--- %s: %d 条，%.1fs，涉及 %d 个文件' % (name, len(got), dt, files))
        print('    按 code：' + ', '.join(
            '%s=%d' % kv for kv in sorted(codes.items(), key=lambda kv: -kv[1])))
        print('    按严重度：high=%d mid=%d low=%d' % (
            sev['high'], sev['mid'], sev['low']))
        print('    按 note：' + ', '.join(
            '%s=%d' % kv for kv in sorted(notes.items(), key=lambda kv: -kv[1])))
        print('    前 5 条样例：')
        for g in got[:5]:
            print('      %s:%d  %s/%s' % (
                g['file'], g['line'], g['code'], g['severity']))
            print('        JP: ' + (g.get('jp') or '')[:110])
            print('        ZH: ' + (g.get('zh') or '')[:110])
            print('        ' + (g.get('detail') or '')[:170])


def main(argv):
    only_syn = '--syn' in argv

    print('=' * 72)
    print('R3 / R5 合成用例自测（内存变异，不落盘）')
    print('=' * 72)

    case_r5_translated_high()
    case_r5_display_text_no_report()
    case_r5_mismatch_mid()
    case_r5_dynamic_ambiguous()
    case_r3_undefined()
    case_r3_undefined_frequent()
    case_r3_engine_var_low()
    case_r3_lost_in_zh()
    case_r3_renamed()
    case_r3_text_reorder_silent()

    if not only_syn:
        real_corpus()

    print()
    print('=' * 72)
    print('结果: PASS %d / FAIL %d' % (len(PASS), len(FAIL)))
    for name, msg in FAIL:
        print('  FAILED: ' + name + '  ' + msg)
    print('=' * 72)
    return 0 if not FAIL else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
