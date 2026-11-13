"""Load Noita's compressed save files (world/entities_*.bin, world/world_*.png_petri).

File layout: <u32 compressed size><u32 raw size> (little-endian), then FastLZ data, or the raw bytes when both
sizes are equal. The FastLZ level is in the top bits of the first byte (0 -> level 1, 1 -> level 2).

    python noita_decomp.py <save00/world> <out dir>     # write decompressed entity files
"""
import sys, struct, os, glob


def fastlz_decompress(src):
    """FastLZ levels 1 and 2."""
    level = (src[0] >> 5) + 1
    n, ip = len(src), 1
    out = bytearray()
    ctrl = src[0] & 31
    while True:
        if ctrl >= 32:                                   # back-reference
            ln = (ctrl >> 5) - 1
            ofs = (ctrl & 31) << 8
            if ln == 6:
                if level == 1:
                    ln += src[ip]; ip += 1
                else:
                    while True:
                        code = src[ip]; ip += 1; ln += code
                        if code != 255: break
            code = src[ip]; ip += 1
            ref = len(out) - ofs - code - 1
            if level == 2 and code == 255 and ofs == (31 << 8):
                ofs = (src[ip] << 8) | src[ip + 1]; ip += 2
                ref = len(out) - ofs - 8191 - 1
            count, dist = ln + 3, len(out) - ref
            if dist >= count:
                out += out[ref:ref + count]
            else:                                        # overlapping copy repeats the last `dist` bytes
                pattern = out[ref:]
                out += (pattern * (count // dist + 1))[:count]
            if ip >= n: break
            ctrl = src[ip]; ip += 1
        else:                                            # literal run
            ctrl += 1
            out += src[ip:ip + ctrl]; ip += ctrl
            if ip >= n: break
            ctrl = src[ip]; ip += 1
    return bytes(out)


def load(path):
    with open(path, 'rb') as f:
        data = f.read()
    csize, dsize = struct.unpack('<II', data[:8])
    if csize == dsize:
        return data[8:]
    out = fastlz_decompress(data[8:8 + csize])
    if len(out) != dsize:
        raise ValueError(f'{path}: decompressed {len(out)} bytes, expected {dsize}')
    return out


if __name__ == '__main__':
    src, dst = sys.argv[1], sys.argv[2]
    os.makedirs(dst, exist_ok=True)
    for p in glob.glob(os.path.join(src, 'entities_*.bin')):
        with open(os.path.join(dst, os.path.basename(p)), 'wb') as f:
            f.write(load(p))
