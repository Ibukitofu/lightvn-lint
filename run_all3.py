# -*- coding: utf-8 -*-
"""多游戏回归驱动：对多个游戏分别跑 linter，输出到 out3/<name>/。

用法:
    python run_all3.py                      # 用 GAMES 环境变量（分号分隔 name=path[:jp]）
    python run_all3.py --game name=path     # 可重复
    python run_all3.py --game name=path --game name2=path2|jppath

格式: name=gamepath[|jppath]
    name      输出子目录名
    gamepath  游戏根目录
    jppath    可选，日文基线目录；省略则该游戏以自身为基线
"""
import os, sys, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out3')


def parse_spec(spec):
    """name=path[:jp]  ->  (name, path, jp or None)"""
    if '=' not in spec:
        return None
    name, rest = spec.split('=', 1)
    name = name.strip()
    parts = rest.split('|', 1)   # Windows 盘符含冒号，改用 | 分隔
    game = parts[0].strip()
    jp = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
    return (name, game, jp) if name and game else None


def collect(argv):
    games = []
    i = 0
    while i < len(argv):
        if argv[i] == '--game' and i + 1 < len(argv):
            g = parse_spec(argv[i + 1])
            if g:
                games.append(g)
            i += 2
            continue
        i += 1
    if not games:
        env = os.environ.get('GAMES', '')
        for spec in env.split(';'):
            if not spec.strip():
                continue
            g = parse_spec(spec.strip())
            if g:
                games.append(g)
    return games


def main(argv):
    sys.stdout.reconfigure(encoding='utf-8')
    games = collect(argv)
    if not games:
        raise SystemExit(
            '未指定游戏。用法:\n'
            '    python run_all3.py --game name=/path/to/game[|/path/to/jp_baseline]\n'
            '或设置 GAMES="a=/path/a;b=/path/b|/path/jp"')

    results = []
    for name, game, jp in games:
        env = dict(os.environ)
        env['LV_GAME'] = game
        env['PYTHONIOENCODING'] = 'utf-8'
        if jp:
            env['LV_JP'] = jp
        else:
            env.pop('LV_JP', None)
        d = os.path.join(OUT, name)
        os.makedirs(d, exist_ok=True)
        print('=== %s ===' % name, flush=True)
        p = subprocess.run(
            [sys.executable, os.path.join(HERE, 'linter.py'), '--quiet', '--out-dir', d],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            env=env, cwd=HERE)
        line = (p.stdout or '').strip().splitlines()
        print(line[-1] if line else '(no output)')
        if p.returncode != 0:
            print('  FAILED rc=%d' % p.returncode)
            print((p.stderr or '')[-600:])
        results.append((name, p.returncode))

    bad = [n for n, rc in results if rc != 0]
    if bad:
        print('\n失败: %s' % ', '.join(bad))
        return 1
    print('\n全部通过: %d 个游戏' % len(results))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
