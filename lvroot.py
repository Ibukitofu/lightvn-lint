# -*- coding: utf-8 -*-
"""目标游戏定位：全部通过环境变量指定，无硬编码路径。

环境变量：
    LV_GAME   游戏根目录（含 Config/ 与 Data/_）      必填
    LV_JP     日文纯文本基线目录（<fid>.txt）          可选；不给则自动解密 LV_GAME
    LV_LIB    lvlib.py 所在目录（加解密实现）          可选；默认自动查找
"""
import os, sys, io, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))


def _env(name, default=None):
    v = os.environ.get(name)
    return v if v else default


def _find_lvlib():
    """在常见位置寻找 lvlib.py 并加入 sys.path。"""
    cands = [
        _env('LV_LIB'),
        HERE,
        os.path.join(os.path.dirname(HERE), 'lvlib'),
        os.path.dirname(HERE),
    ]
    for c in cands:
        if c and os.path.isfile(os.path.join(c, 'lvlib.py')):
            if c not in sys.path:
                sys.path.insert(0, c)
            return c
    return None


LVLIB_DIR = _find_lvlib()

GAME = _env('LV_GAME')
if GAME:
    GAME = os.path.normpath(os.path.abspath(GAME))
DATA = os.path.join(GAME, 'Data', '_') if GAME else None
CONFIG = os.path.join(GAME, 'Config') if GAME else None
CMD_XML = os.path.join(CONFIG, 'Commands.xml') if CONFIG else None


def _cache_jp(game_dir):
    """没有日文基线时：把该游戏自己的脚本解密成纯文本作为基线。
    对未改动的原版游戏来说，这就是正确基线。"""
    try:
        import lvlib
    except ImportError:
        raise SystemExit(
            "未找到 lvlib.py（用于解密 .mcdat）。请设置 LV_LIB 指向它所在目录，"
            "或改用 --jp 指定已解包的纯文本目录。")
    import lvlib
    tag = hashlib.sha256(game_dir.encode('utf-8')).hexdigest()[:10]
    out = os.path.join(HERE, '_cache', tag)
    os.makedirs(out, exist_ok=True)
    data = os.path.join(game_dir, 'Data', '_')
    if not os.path.isdir(data):
        return out
    fns = [f for f in os.listdir(data) if f.endswith('.mcdat')]
    marker = os.path.join(out, '.cache_done')   # 不能以 .txt 结尾，否则会被当成脚本
    for _stale in ('_done.txt', '.done'):              # 旧版标记，清掉
        _sp = os.path.join(out, _stale)
        if os.path.exists(_sp):
            os.remove(_sp)
    if os.path.exists(marker) and io.open(marker, encoding='utf-8').read().strip() == str(len(fns)):
        return out
    for fn in fns:
        try:
            pt = lvlib.xor_crypt(open(os.path.join(data, fn), 'rb').read())
            txt = pt.decode('utf-8')
        except Exception:
            continue
        io.open(os.path.join(out, fn[:-6] + '.txt'), 'w', encoding='utf-8', newline='').write(txt)
    io.open(marker, 'w', encoding='utf-8').write(str(len(fns)))
    return out


JP_DIR = _env('LV_JP')
if not JP_DIR and GAME:
    JP_DIR = _cache_jp(GAME)

INTENTIONAL = (_env('LV_INTENTIONAL')
               or os.path.join(HERE, 'intentional.json'))
ROOT = _env('LV_ROOT', os.path.dirname(HERE))
DATA_DIR = os.path.join(HERE, 'data')   # 仓库自带的数据文件
IS_SELF_BASELINE = (_env('LV_JP') is None)


def require_game():
    """使用前检查必填项，给出可操作的报错。"""
    if not GAME:
        raise SystemExit(
            '未指定目标游戏。请设置环境变量 LV_GAME 指向游戏根目录，例如:\n'
            '    LV_GAME=/path/to/game python linter.py\n'
            '游戏根目录下应同时存在 Config/Commands.xml 与 Data/_/*.mcdat。')
    if not os.path.isdir(DATA or ''):
        raise SystemExit('LV_GAME=%s 下找不到 Data/_ 目录，请检查路径。' % GAME)
    if not os.path.isfile(CMD_XML or ''):
        raise SystemExit('LV_GAME=%s 下找不到 Config/Commands.xml，请检查路径。' % GAME)


def info():
    return {'game': GAME, 'data': DATA, 'config': CONFIG, 'jp': JP_DIR,
            'lvlib': LVLIB_DIR, 'self_baseline': IS_SELF_BASELINE}


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    require_game()
    for k, v in info().items():
        print('%-14s %s' % (k, v))
