# -*- coding: utf-8 -*-
"""最小 Page 实现：只做行结构解析，不含任何加解密。

linter 处理的是已经解包出来的纯文本脚本，不需要碰 .mcdat 容器。
"""

CRLF = '\r\n'


class Page:
    """把脚本按 CRLF 切成行，保留原始的尾部 \r 与末尾残段。

    与原版 lvlib.Page 行为一致：dump() 能逐字节还原。
    """

    def __init__(self, data):
        if isinstance(data, str):
            data = data.encode('utf-8', 'surrogateescape')
        parts = data.split(b'\r\n')
        self.tail = parts[-1]
        raw = parts[:-1]
        self.prefix = [p[:-1] if p.endswith(b'\r') else p for p in raw]
        self.crsuf = [b'\r' if p.endswith(b'\r') else b'' for p in raw]

    @classmethod
    def load(cls, path):
        with open(path, 'rb') as fh:
            return cls(fh.read())

    def text(self, i):
        return self.prefix[i].decode('utf-8', 'surrogateescape')

    def set_text(self, i, s):
        self.prefix[i] = s.encode('utf-8', 'surrogateescape')

    def dump(self):
        out = bytearray()
        for i, p in enumerate(self.prefix):
            out += p + self.crsuf[i] + b'\r\n'
        out += self.tail
        return bytes(out)
