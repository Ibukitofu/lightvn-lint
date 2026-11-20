# -*- coding: utf-8 -*-
"""Finding schema shared by every rule module."""

SEVERITY_RANK = {'high': 0, 'mid': 1, 'low': 2}


def make(rule, code, severity, file, line, jp=None, zh=None, detail='',
         severity_note=None):
    return {
        'rule': rule,
        'code': code,
        'severity': severity,
        'file': file,
        'line': int(line),
        'jp': '' if jp is None else jp,
        'zh': '' if zh is None else zh,
        'detail': detail,
        'severity_note': severity_note,
    }


def sort_key(f):
    return (f['rule'], f['file'], f['line'], f['code'])


def finalize(findings):
    return sorted(findings, key=sort_key)


def summarize(findings, rules=('R1', 'R2', 'R3', 'R4', 'R5', 'R6')):
    counts = {r: 0 for r in rules}
    sev = {r: {'high': 0, 'mid': 0, 'low': 0} for r in rules}
    codes = {r: {} for r in rules}
    for f in findings:
        r = f['rule']
        if r not in counts:
            counts[r] = 0
            sev[r] = {'high': 0, 'mid': 0, 'low': 0}
            codes[r] = {}
        counts[r] += 1
        sev[r][f['severity']] = sev[r].get(f['severity'], 0) + 1
        codes[r][f['code']] = codes[r].get(f['code'], 0) + 1
    return counts, sev, codes
