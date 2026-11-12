import sys, struct, os, glob

def fastlz_decompress(src):
    ip = 0
    n = len(src)
    level = (src[0] >> 5) + 1
    out = bytearray()
    ctrl = src[ip] & 31; ip += 1
    loop = True
    while loop:
        if ctrl >= 32:
            ln = (ctrl >> 5) - 1
            ofs = (ctrl & 31) << 8
            if ln == 6 and level == 1:
                ln += src[ip]; ip += 1
            elif ln == 6:
                while True:
                    code = src[ip]; ip += 1; ln += code
                    if code != 255: break
            code = src[ip]; ip += 1
            ref = len(out) - ofs - code
            if level == 2 and code == 255 and ofs == (31 << 8):
                ofs = (src[ip] << 8) | src[ip + 1]; ip += 2
                ref = len(out) - ofs - 8191
            if ip < n:
                ctrl = src[ip]; ip += 1
            else:
                loop = False
            ref -= 1
            for _ in range(ln + 3):
                out.append(out[ref]); ref += 1
        else:
            ctrl += 1
            out += src[ip:ip + ctrl]; ip += ctrl
            loop = ip < n
            if loop:
                ctrl = src[ip]; ip += 1
    return bytes(out)

def load(path):
    data = open(path, 'rb').read()
    csize, dsize = struct.unpack('<II', data[:8])
    if csize == dsize:
        return data[8:]
    out = fastlz_decompress(data[8:8 + csize])
    assert len(out) == dsize, (path, len(out), dsize)
    return out

if __name__ == '__main__':
    src, dst = sys.argv[1], sys.argv[2]
    os.makedirs(dst, exist_ok=True)
    for p in glob.glob(os.path.join(src, 'entities_*.bin')):
        open(os.path.join(dst, os.path.basename(p)), 'wb').write(load(p))
