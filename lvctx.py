# -*- coding: utf-8 -*-
"""Cached corpus access for the Light.vn linter.

Loads every script pair once (Japanese source + Chinese build) and exposes the
indexes the rule modules need. Everything here is read-only.
"""
import os, re, sys, json, collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lvparse as P

_CACHE = {}


def _load_all():
    if 'corpus' in _CACHE:
        return _CACHE['corpus']
    fids = P.list_fids()
    jp = {}
    zh = {}
    zh_missing = []
    for fid in fids:
        jp[fid] = P.load_page_jp(fid)
        zp = os.path.join(P.BLD_DIR, fid + '.mcdat')
        if os.path.exists(zp):
            zh[fid] = P.load_page_zh(fid)
        else:
            zh_missing.append(fid)
    corpus = {'fids': fids, 'jp': jp, 'zh': zh, 'zh_missing': zh_missing}
    _CACHE['corpus'] = corpus
    return corpus


def fids():
    return _load_all()['fids']


def jp_page(fid):
    return _load_all()['jp'][fid]


def zh_page(fid):
    return _load_all()['zh'].get(fid)


def jp_line(fid, i):
    return _load_all()['jp'][fid].text(i)


def zh_line(fid, i):
    pg = _load_all()['zh'].get(fid)
    if pg is None or i >= len(pg.prefix):
        return None
    return pg.text(i)


def line_count(fid):
    return len(_load_all()['jp'][fid].prefix)


def command_table():
    return P.command_table()


# ---------------------------------------------------------------- keywords

COMMENT_PREFIX = '//'


def classify(line):
    """Classify one raw line.

    Returns dict(kind, cmd, args, cond, arg, segs) where kind is one of
    'blank' | 'comment' | 'text' | 'command'.
    """
    s = line.strip()
    if not s:
        return {'kind': 'blank'}
    if s.startswith(COMMENT_PREFIX):
        return {'kind': 'comment'}
    segs = P.split_segments_v2(line)
    first = P.parse_segment(segs[0]['head']) if segs else None
    if first is None:
        return {'kind': 'text'}
    if first['text_only']:
        # a dialogue line: optional もし(...) then an opening quote
        return {'kind': 'text', 'cond': first['cond']}
    return {'kind': 'command', 'cmd': first['command'], 'args': first['args'],
            'cond': first['cond'], 'segs': segs}
