"""Read files from Noita's data.wak archive (a plain index of offsets and sizes, no compression or encryption).

    python wak.py <data.wak>        # list the first files
"""
import struct


class Wak:
    def __init__(self, path):
        self.f = open(path, 'rb')
        hdr = self.f.read(16)
        _, n, hsize, _ = struct.unpack('<IIII', hdr)
        raw = self.f.read(hsize - 16)
        self.files = {}
        p = 0
        for _ in range(n):
            off, size, nl = struct.unpack_from('<III', raw, p); p += 12
            name = raw[p:p + nl].decode('utf-8', 'replace'); p += nl
            self.files[name] = (off, size)

    def read(self, name):
        off, size = self.files[name]
        self.f.seek(off)
        return self.f.read(size)

if __name__ == '__main__':
    import sys
    w = Wak(sys.argv[1])
    print(len(w.files))
    for k in list(w.files)[:10]: print(k)
