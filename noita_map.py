"""Render a Noita save's explored world (world/world_*.png_petri) to an image.
Usage: python noita_map.py <save00/world> <data.wak> <out.png> [scale_divisor]
"""
import sys, os, re, glob, struct, zlib, io
import numpy as np
from PIL import Image
import xml.etree.ElementTree as ET
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from noita_decomp import load
from wak import Wak

CH = 512

def argb(s):
    v = int(s, 16)
    return ((v >> 16) & 255, (v >> 8) & 255, v & 255, (v >> 24) & 255)

def material_table(wak):
    xml = wak.read('data/materials.xml').decode('utf-8', 'replace')
    xml = re.sub(r'<!--.*?-->', '', xml, flags=re.S)
    root = ET.fromstring(xml)
    mats = {}
    for el in root:
        if el.tag not in ('CellData', 'CellDataChild'):
            continue
        name = el.get('name')
        g = el.find('Graphics')
        info = dict(parent=el.get('_parent'), wang=el.get('wang_color'),
                    color=g.get('color') if g is not None else None,
                    tex=g.get('texture_file') if g is not None else None,
                    type=el.get('cell_type'))
        mats[name] = info

    def resolve(name, key, depth=0):
        m = mats.get(name)
        if not m or depth > 10: return None
        return m[key] if m[key] else (resolve(m['parent'], key, depth + 1) if m['parent'] else None)

    texcache = {}
    out = {}
    for name in mats:
        tex = resolve(name, 'tex')
        col = resolve(name, 'color')
        arr = None
        if tex:
            if tex not in texcache:
                try:
                    texcache[tex] = np.array(Image.open(io.BytesIO(wak.read(tex))).convert('RGBA'))
                except Exception:
                    texcache[tex] = None
            arr = texcache[tex]
        if arr is None:
            c = argb(col) if col else (argb(resolve(name, 'wang')) if resolve(name, 'wang') else None)
            if c is None: continue
            if resolve(name, 'type') in ('liquid', 'gas') and not col:
                c = c[:3] + (200,)
            arr = np.array(c, np.uint8).reshape(1, 1, 4)
        out[name] = arr
    return out

def fallback(name):
    h = zlib.crc32(name.encode())
    return np.array([(h >> 16) & 255, (h >> 8) & 255, h & 255, 255], np.uint8).reshape(1, 1, 4)

def tile_tex(tex, x0, y0):
    th, tw = tex.shape[:2]
    if th == 1 and tw == 1:
        return np.broadcast_to(tex, (CH, CH, 4))
    ys = (np.arange(CH) + y0) % th
    xs = (np.arange(CH) + x0) % tw
    return tex[ys[:, None], xs[None, :]]

def render_chunk(path, cx, cy, mats, cache):
    d = load(path)
    ver, w, h = struct.unpack('>III', d[:12])
    assert (w, h) == (CH, CH), path
    cells = np.frombuffer(d, np.uint8, w * h, 12).reshape(h, w)
    p = 12 + w * h
    n = struct.unpack('>I', d[p:p + 4])[0]; p += 4
    names = []
    for _ in range(n):
        l = struct.unpack('>I', d[p:p + 4])[0]; p += 4
        names.append(d[p:p + l].decode('latin1')); p += l
    nc = struct.unpack('>I', d[p:p + 4])[0]; p += 4
    custom = np.frombuffer(d, '>u4', nc, p)
    img = np.zeros((h, w, 4), np.uint8)
    idx = cells & 0x7f
    for i, name in enumerate(names):
        if name == 'air': continue
        mask = (idx == i) & (cells < 0x80)
        if not mask.any(): continue
        tex = mats.get(name)
        if tex is None:
            tex = cache.setdefault(name, fallback(name))
        img[mask] = tile_tex(tex, cx, cy)[mask]
    cm = cells >= 0x80
    if cm.any() and nc:
        cols = custom[:cm.sum()]
        rgba = np.stack([(cols >> 16) & 255, (cols >> 8) & 255, cols & 255, (cols >> 24) & 255], 1).astype(np.uint8)
        ys, xs = np.nonzero(cm)
        k = min(len(ys), len(rgba))
        img[ys[:k], xs[:k]] = rgba[:k]
    return img

def main():
    world, wakp, outp = sys.argv[1:4]
    div = int(sys.argv[4]) if len(sys.argv) > 4 else 4
    print(render(world, wakp, outp, div))

def render(world, wakp, outp, div=4):
    wak = Wak(wakp)
    mats = material_table(wak)
    chunks = []
    for p in glob.glob(os.path.join(world, 'world_*.png_petri')):
        m = re.search(r'world_(-?\d+)_(-?\d+)\.png_petri$', p)
        chunks.append((int(m.group(1)), int(m.group(2)), p))
    x0 = min(c[0] for c in chunks); y0 = min(c[1] for c in chunks)
    x1 = max(c[0] for c in chunks) + CH; y1 = max(c[1] for c in chunks) + CH
    s = CH // div
    W, H = (x1 - x0) // div, (y1 - y0) // div
    bg = np.zeros((H, W, 4), np.uint8); bg[...] = (18, 16, 22, 255)
    canvas = Image.fromarray(bg)
    cache = {}
    for cx, cy, p in chunks:
        img = Image.fromarray(render_chunk(p, cx, cy, mats, cache))
        sky = Image.new('RGBA', img.size, (40, 36, 48, 255) if cy >= 0 else (60, 80, 110, 255))
        sky.alpha_composite(img)
        canvas.paste(sky.resize((s, s), Image.BOX), ((cx - x0) // div, (cy - y0) // div))
    canvas.save(outp)
    return dict(origin=[x0, y0], div=div, size=[W, H], chunks=len(chunks), unknown_materials=sorted(cache))

if __name__ == '__main__':
    main()
