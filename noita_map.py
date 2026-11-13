"""Draw a Noita save's explored world (world/world_*.png_petri) with material textures from data.wak.

Chunk layout (512x512 world pixels): u32 24, u32 512, u32 512, then 512x512 bytes (low 7 bits index this chunk's
material list, the high bit means "custom colour"), u32 n + n material names, u32 n + n ARGB custom colours used in
order by the high-bit pixels. Physics objects follow and are not read.

    python noita_map.py <save00/world> <data.wak> <out.png> [scale_divisor]
"""
import sys, os, re, glob, struct, zlib, io
import numpy as np
from PIL import Image
import xml.etree.ElementTree as ET
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from noita_decomp import load
from wak import Wak

CH = 512                                   # chunk size in world pixels
SKY_BELOW, SKY_ABOVE = (40, 36, 48, 255), (60, 80, 110, 255)   # behind empty pixels, under/over ground level


def argb(s):
    v = int(s, 16)
    return ((v >> 16) & 255, (v >> 8) & 255, v & 255, (v >> 24) & 255)


def material_table(wak):
    """{material name: RGBA texture array (or a 1x1 colour)} from data/materials.xml and its textures."""
    xml = re.sub(r'<!--.*?-->', '', wak.read('data/materials.xml').decode('utf-8', 'replace'), flags=re.S)
    mats = {}
    for el in ET.fromstring(xml):
        if el.tag not in ('CellData', 'CellDataChild'): continue
        g = el.find('Graphics')
        mats[el.get('name')] = dict(parent=el.get('_parent'), wang=el.get('wang_color'), type=el.get('cell_type'),
                                    color=g.get('color') if g is not None else None,
                                    tex=g.get('texture_file') if g is not None else None)

    def inherited(name, key, depth=0):
        m = mats.get(name)
        if not m or depth > 10: return None
        return m[key] or (inherited(m['parent'], key, depth + 1) if m['parent'] else None)

    textures, out = {}, {}
    for name in mats:
        tex, col = inherited(name, 'tex'), inherited(name, 'color')
        arr = None
        if tex:
            if tex not in textures:
                try: textures[tex] = np.array(Image.open(io.BytesIO(wak.read(tex))).convert('RGBA'))
                except Exception: textures[tex] = None
            arr = textures[tex]
        if arr is None:
            wang = inherited(name, 'wang')
            c = argb(col) if col else argb(wang) if wang else None
            if c is None: continue
            if inherited(name, 'type') in ('liquid', 'gas') and not col:
                c = c[:3] + (200,)
            arr = np.array(c, np.uint8).reshape(1, 1, 4)
        out[name] = arr
    return out


def fallback(name):
    """A stable made-up colour for materials with no texture or colour (e.g. from mods)."""
    h = zlib.crc32(name.encode())
    return np.array([(h >> 16) & 255, (h >> 8) & 255, h & 255, 255], np.uint8).reshape(1, 1, 4)


def tile_tex(tex, x0, y0):
    th, tw = tex.shape[:2]
    if th == 1 and tw == 1:
        return np.broadcast_to(tex, (CH, CH, 4))
    return tex[((np.arange(CH) + y0) % th)[:, None], ((np.arange(CH) + x0) % tw)[None, :]]


def read_chunk(path):
    """(cells as a 512x512 uint8 array, material names, custom ARGB colours) from one chunk file."""
    d = load(path)
    _, w, h = struct.unpack('>III', d[:12])
    if (w, h) != (CH, CH): raise ValueError(f'{path}: unexpected chunk size {w}x{h}')
    cells = np.frombuffer(d, np.uint8, w * h, 12).reshape(h, w)
    p = 12 + w * h
    n = struct.unpack_from('>I', d, p)[0]; p += 4
    names = []
    for _ in range(n):
        ln = struct.unpack_from('>I', d, p)[0]; p += 4
        names.append(d[p:p + ln].decode('latin1')); p += ln
    nc = struct.unpack_from('>I', d, p)[0]; p += 4
    return cells, names, np.frombuffer(d, '>u4', nc, p)


def render_cells(cells, names, custom, cx, cy, mats):
    """RGBA image of a chunk at (cx, cy); transparent where there is air."""
    img = np.zeros((CH, CH, 4), np.uint8)
    idx = cells & 0x7f
    plain = cells < 0x80
    for i, name in enumerate(names):
        if name == 'air': continue
        mask = (idx == i) & plain
        if not mask.any(): continue
        tex = mats.get(name)
        img[mask] = tile_tex(tex if tex is not None else fallback(name), cx, cy)[mask]
    cm = ~plain
    if cm.any() and len(custom):
        cols = custom[:cm.sum()]
        rgba = np.stack([(cols >> 16) & 255, (cols >> 8) & 255, cols & 255, (cols >> 24) & 255], 1).astype(np.uint8)
        ys, xs = np.nonzero(cm)
        k = min(len(ys), len(rgba))
        img[ys[:k], xs[:k]] = rgba[:k]
    return img


def on_sky(rgba, cy):
    """The chunk as an opaque RGB image over the background colour for its depth."""
    img = Image.new('RGBA', (CH, CH), SKY_BELOW if cy >= 0 else SKY_ABOVE)
    img.alpha_composite(Image.fromarray(rgba))
    return img.convert('RGB')


def render_chunk(path, cx, cy, mats):
    return render_cells(*read_chunk(path), cx, cy, mats)


def render(world, wakp, outp, div=4):
    """The whole explored world as one image (for debugging; the app draws tiles instead)."""
    mats = material_table(Wak(wakp))
    chunks = []
    for p in glob.glob(os.path.join(world, 'world_*.png_petri')):
        m = re.search(r'world_(-?\d+)_(-?\d+)\.png_petri$', p)
        chunks.append((int(m.group(1)), int(m.group(2)), p))
    x0 = min(c[0] for c in chunks); y0 = min(c[1] for c in chunks)
    x1 = max(c[0] for c in chunks) + CH; y1 = max(c[1] for c in chunks) + CH
    s = CH // div
    canvas = Image.new('RGB', ((x1 - x0) // div, (y1 - y0) // div), (18, 16, 22))
    for cx, cy, p in chunks:
        canvas.paste(on_sky(render_chunk(p, cx, cy, mats), cy).resize((s, s), Image.BOX), ((cx - x0) // div, (cy - y0) // div))
    canvas.save(outp)
    return dict(origin=[x0, y0], div=div, size=list(canvas.size), chunks=len(chunks))


if __name__ == '__main__':
    print(render(sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 4))
