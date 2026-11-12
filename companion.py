"""Noita save companion: a local web app that maps your save and rescans it while you play.

    python companion.py                 # auto-detects the save and the game, opens the browser
    python companion.py --port 8765 --save <save00> --wak <Noita/data/data.wak> --no-browser
    python companion.py --export <dir>  # write a static snapshot (index.html + map.webp) and exit
"""
import os, sys, re, json, time, glob, struct, io, base64, urllib.request, urllib.parse, threading, argparse, webbrowser
from concurrent.futures import ProcessPoolExecutor
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
FROZEN = getattr(sys, 'frozen', False)
# bundled read-only files (page template, seed wiki data and icons)
RES = getattr(sys, '_MEIPASS', HERE)
# writable data: next to the scripts when run from source, %LOCALAPPDATA%\NoitaCompanion when packaged
DATA = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'NoitaCompanion') if FROZEN else HERE
APP_NAME = 'Noita Companion'
from noita_decomp import load
from wak import Wak
import noita_map, noita_wands, wiki_data

CH, DIV = 512, 4
WORLD_W = 35840          # width of one world; parallel worlds repeat every WORLD_W pixels
TS = CH // DIV
TYPES = ['Projectile', 'Static projectile', 'Projectile modifier', 'Multicast', 'Material', 'Other', 'Utility', 'Passive']
RARE_SPELL = 0.003       # under 0.3% per spell roll at its best tier counts as rare
RARE_FLASK = 0.01        # a flask or pouch material counts as rare under 1% of that container
# Hand-picked world materials with no flask odds that are still worth flagging as loose pixels.
# Metals (gold, silver, brass, diamond) are only notable inside a flask, so they are not listed here.
RARE_WORLD = {'magic_liquid_hp_regeneration_unstable', 'creepy_liquid',
              'material_darkness', 'midas', 'midas_precursor', 'void_liquid', 'just_death', 'magic_gas_hp_regeneration',
              'monster_powder_test', 'urine', 'poo', 'mammi', 'cheese_static'}

# ---------------------------------------------------------------- locating files
def find_save():
    return os.path.join(os.environ.get('USERPROFILE', os.path.expanduser('~')), 'AppData', 'LocalLow', 'Nolla_Games_Noita', 'save00')

def find_wak():
    roots = [r'C:\Program Files (x86)\Steam']
    vdf = os.path.join(roots[0], 'steamapps', 'libraryfolders.vdf')
    if os.path.exists(vdf):
        roots += [p.replace('\\\\', '\\') for p in re.findall(r'"path"\s+"([^"]+)"', open(vdf, encoding='utf-8', errors='replace').read())]
    for r in roots:
        p = os.path.join(r, 'steamapps', 'common', 'Noita', 'data', 'data.wak')
        if os.path.exists(p): return p
    return None

def cap(s): return s[:1].upper() + s[1:]

# ---------------------------------------------------------------- spell and material lists (from the wiki)
def load_spells(wiki):
    """Names, icons, types and spawn odds from the wiki's Spells table."""
    tot = {}
    for s in wiki['spells'].values():
        for t, p in zip(s['tiers'], s['weights']): tot[t] = tot.get(t, 0) + p
    out = {}
    for i, (k, s) in enumerate(wiki['spells'].items()):
        odds = {t: p / tot[t] for t, p in zip(s['tiers'], s['weights']) if 0 <= t <= 6 and p > 0 and tot.get(t)}
        best = max(odds.values()) if odds else 0
        out[k] = dict(name=s['name'], icon=s['icon'], type=s['type'], order=i, odds=round(best, 6),
                      tiers=sorted(t for t, v in odds.items() if v >= best * 0.999) if odds else [],
                      spawn_tiers=sorted(odds), rare=0 < best < RARE_SPELL)
    return out

def material_order(wak):
    """Material numbering the game uses in flask contents: every <CellData> in file order, then every <CellDataChild>.
    The wiki has no numbering, so this is the one thing read from the game's own material file."""
    xml = re.sub(r'<!--.*?-->', '', wak.read('data/materials.xml').decode('utf-8', 'replace'), flags=re.S)
    seen, base, child = set(), [], []
    for m in re.finditer(r'<(CellData|CellDataChild)\b[^>]*?\bname="([^"]+)"', xml):
        if m.group(2) in seen: continue
        seen.add(m.group(2)); (base if m.group(1) == 'CellData' else child).append(m.group(2))
    return base + child, len(base)

def load_materials(wiki, mats):
    """Names from the wiki; flask and pouch odds from the wiki's Potions and Powder Pouch pages.
    Swatch colours match the map (they come from the textures the map is drawn with)."""
    info = {}
    for k, m in wiki['materials'].items():
        arr = mats.get(k)
        color = '#888888'
        if arr is not None:
            px = arr.reshape(-1, 4)
            px = px[px[:, 3] > 0] if (px[:, 3] > 0).any() else px
            color = '#%02x%02x%02x' % tuple(int(c) for c in px[:, :3].mean(0))
        v = dict(ui=m['name'], color=color, icon=m.get('icon'), type=m.get('type'))
        if k in wiki['flask']: v['flask'] = wiki['flask'][k]
        if k in wiki['pouch']: v['pouch'] = wiki['pouch'][k]
        v['rare'] = wiki['flask'][k] < RARE_FLASK if k in wiki['flask'] else k in RARE_WORLD
        info[k] = v
    return info

def container_note(kind, mat, wiki):
    """Odds of finding this material in this kind of container, and whether that is notable."""
    table = wiki['flask'] if kind == 'flask' else wiki['pouch']
    if mat in table:
        return dict(odds=table[mat], notable=table[mat] < RARE_FLASK, why=None)
    pools = [p for p, xs in wiki['pools'].items() if mat in xs] if kind == 'flask' else []
    why = ('Only from ' + ' or '.join({'secret': 'secret-material flasks (chests)', 'random': 'random-material flasks (chests)',
                                       'aggressive': 'Hiisi alchemist flasks'}[p] for p in pools)) if pools \
        else 'Not in any spawn pool: filled by you or by the world'
    return dict(odds=None, notable=True, why=why)

# ---------------------------------------------------------------- per-chunk work (runs in worker processes)
_mats = None
def _init_worker(wakp):
    global _mats
    _mats = noita_map.material_table(Wak(wakp))

def process_chunk(args):
    path, cx, cy = args
    d = load(path)
    w, h = struct.unpack('>II', d[4:12])
    cells = np.frombuffer(d, np.uint8, w * h, 12).reshape(h, w)
    p = 12 + w * h
    n = struct.unpack('>I', d[p:p + 4])[0]; p += 4
    names = []
    for _ in range(n):
        l = struct.unpack('>I', d[p:p + 4])[0]; p += 4
        names.append(d[p:p + l].decode('latin1')); p += l
    idx = (cells & 0x7f).ravel()
    ys, xs = np.divmod(np.arange(w * h), w)
    cnt = np.bincount(idx, minlength=len(names))
    sx = np.bincount(idx, weights=xs, minlength=len(names))
    sy = np.bincount(idx, weights=ys, minlength=len(names))
    counts = {names[i]: [int(cnt[i]), int(cx + sx[i] / cnt[i]), int(cy + sy[i] / cnt[i])]
              for i in range(len(names)) if cnt[i] and names[i] != 'air'}
    img = Image.fromarray(noita_map.render_chunk(path, cx, cy, _mats, {}))
    sky = Image.new('RGBA', img.size, (40, 36, 48, 255) if cy >= 0 else (60, 80, 110, 255))
    sky.alpha_composite(img)
    buf = io.BytesIO(); sky.resize((TS, TS), Image.BOX).convert('RGB').save(buf, 'PNG', optimize=False)
    return buf.getvalue(), counts

# ---------------------------------------------------------------- items (wiki Items page + the save's entity files)
# Entity files whose name differs from the wiki's sprite name ("Item <file name>").
ITEM_ALIASES = {'thunderstone': 'Ukkoskivi', 'poopstone': 'Kakkakikkare', 'egg_monster': 'Egg', 'egg_worm': 'Wiggling Egg',
                'egg_hollow': 'Hollow Egg', 'physics_die': 'Chaos Die', 'chaos_die': 'Chaos Die', 'physics_greed_die': 'Greed Die',
                'physics_gold_orb': 'Shiny Orb', 'physics_gold_orb_greed': 'Cruel Orb', 'sun': 'Auringonsiemen',
                'sunseed': 'Auringonsiemen', 'sunstone': 'Aurinkokivi', 'emerald_tablet': 'Emerald Tablet', 'book': 'Book',
                'broken_spell': 'Broken Spell', 'key': 'Crystal Key', 'sampo': 'Sampo', 'stonestone': 'Tannerkivi',
                'powder_stash': 'Powder Pouch', 'potion': 'Potion'}
# Pickups the Items page does not cover: (name, wiki page, quiet). Quiet ones only show on the map when tracked.
EXTRA_ITEMS = {'heart_fullhp': ('Extra Max Health', 'Extra Max Health', False),
               'heart_fullhp_temple': ('Holy Mountain heart', 'Extra Max Health', True),
               'heart': ('Heart', 'Pickups', False), 'heart_better': ('Heart', 'Pickups', False),
               'spell_refresh': ('Spell Refresher', 'Spell Refresher', True),
               'perk_reroll': ('Perk Reroll', 'Holy Mountain', True),
               'chest_random': ('Treasure Chest', 'Treasure Chest', False),
               'chest_random_super': ('Great Treasure Chest', 'Great Treasure Chest', False),
               'goldnugget': ('Gold nugget', 'Gold', True)}

def item_catalog(wiki):
    cat = {}
    for it in wiki.get('items', []):
        if it['name'] in cat: continue
        odds = {k: v for k, v in it['odds'].items() if v}
        cat[it['name']] = dict(name=it['name'], icon=it.get('icon'), page=it['page'], description=it['description'],
                               effect=it['effect'], other=it['other'], odds=odds, quiet=False,
                               rare=bool(odds) and max(odds.values()) < RARE_FLASK)
    for name, page, quiet in EXTRA_ITEMS.values():
        cat.setdefault(name, dict(name=name, icon=None, page=page, description='', effect='', other='', odds={}, quiet=quiet, rare=False))
    return cat

def item_key(xml, cat, by_sprite):
    base = xml.rsplit('/', 1)[-1][:-4]
    if '/runestones/' in xml: return 'Runestone'
    if base.startswith('goldnugget'): return 'Gold nugget'
    if base in EXTRA_ITEMS: return EXTRA_ITEMS[base][0]
    if base in ITEM_ALIASES and ITEM_ALIASES[base] in cat: return ITEM_ALIASES[base]
    hit = by_sprite.get('item ' + base.replace('_', ' '))
    if hit: return hit
    return cap(base.replace('_', ' '))     # not on the wiki's Items page

# ---------------------------------------------------------------- wiki icons (cached on disk)
ICON_RE = re.compile(r'[^A-Za-z0-9._-]')
def icon_name(url): return ICON_RE.sub('_', urllib.parse.unquote(url.rsplit('/', 1)[-1].split('?')[0]))

def icon_bytes(cache_dir, url):
    path = os.path.join(cache_dir, 'icons', icon_name(url))
    if os.path.exists(path): return open(path, 'rb').read()
    seed = os.path.join(RES, 'seed_icons', icon_name(url))      # icons shipped with the app
    if os.path.exists(seed): return open(seed, 'rb').read()
    req = urllib.request.Request(url, headers=wiki_data.UA)
    data = urllib.request.urlopen(req, timeout=20).read()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'wb').write(data)
    return data

# ---------------------------------------------------------------- live state
class State:
    def __init__(self, save, wakp, cache_dir, workers):
        self.save, self.wakp, self.cache_dir, self.workers = save, wakp, cache_dir, workers
        self.world = os.path.join(save, 'world')
        self.lock = threading.Lock()
        self.version, self.last = 0, {}
        self.chunks, self.tiles, self.ents = {}, {}, {}
        self.pool = None
        os.makedirs(os.path.join(cache_dir, 'tiles'), exist_ok=True)
        wak = Wak(wakp)
        self.wiki = wiki_data.load()
        mats = noita_map.material_table(wak)
        self.spells = load_spells(self.wiki)
        self.mat_info = load_materials(self.wiki, mats)
        self.mat_order, self.mat_base = material_order(wak)
        self.catalog = item_catalog(self.wiki)
        self.by_sprite = {}
        for it in self.wiki.get('items', []): self.by_sprite.setdefault(it['sprite'].lower(), it['name'])
        self.index_path = os.path.join(cache_dir, 'chunks.json')
        try: self.chunks = {tuple(map(int, k.split('_'))): v for k, v in json.load(open(self.index_path)).items()}
        except Exception: self.chunks = {}

    def _flags(self):
        f = os.path.join(self.save, 'persistent', 'flags')
        return set(os.listdir(f)) if os.path.isdir(f) else set()

    def refresh(self):
        with self.lock:
            t0 = time.time()
            todo, seen, ents_changed = [], set(), 0
            ent_seen = set()
            for e in os.scandir(self.world):
                st = e.stat(); key = [st.st_mtime_ns, st.st_size]
                m = re.match(r'world_(-?\d+)_(-?\d+)\.png_petri$', e.name)
                if m:
                    c = (int(m.group(1)), int(m.group(2))); seen.add(c)
                    old = self.chunks.get(c)
                    tile_ok = os.path.exists(self._tile_path(c))
                    if not old or old['key'] != key or not tile_ok:
                        todo.append((e.path, c, key))
                elif e.name.startswith('entities_') and e.name.endswith('.bin'):
                    ent_seen.add(e.path)
                    old = self.ents.get(e.path)
                    if not old or old[0] != key:
                        try: self.ents[e.path] = (key, noita_wands.scan_file(e.path)); ents_changed += 1
                        except Exception as ex: print('entity file failed', e.name, ex)
            for p in set(self.ents) - ent_seen: del self.ents[p]; ents_changed += 1
            gone = set(self.chunks) - seen
            for c in gone: del self.chunks[c]
            if todo:
                if len(todo) > 8 and self.workers > 1:
                    if not self.pool: self.pool = ProcessPoolExecutor(self.workers, initializer=_init_worker, initargs=(self.wakp,))
                    results = self.pool.map(process_chunk, [(p, c[0], c[1]) for p, c, _ in todo], chunksize=4)
                else:
                    if _mats is None: _init_worker(self.wakp)
                    results = map(process_chunk, [(p, c[0], c[1]) for p, c, _ in todo])
                for (p, c, key), (png, counts) in zip(todo, results):
                    try:
                        open(self._tile_path(c), 'wb').write(png)
                    except OSError as ex:
                        print('tile write failed', c, ex); continue
                    self.tiles[c] = png
                    self.chunks[c] = dict(key=key, counts=counts, v=self.version + 1)
                json.dump({f'{a}_{b}': v for (a, b), v in self.chunks.items()}, open(self.index_path, 'w'))
            flags = self._flags()
            if flags != getattr(self, 'flags', None): self.flags = flags; ents_changed += 1
            changed = bool(todo or gone or ents_changed)
            if changed: self.version += 1
            self.last = dict(time=time.time(), chunks=len(todo) + len(gone), entity_files=ents_changed,
                             took=round(time.time() - t0, 2), version=self.version)
            return self.last

    def _tile_path(self, c): return os.path.join(self.cache_dir, 'tiles', f'{c[0]}_{c[1]}.png')

    def tile(self, c):
        if c not in self.tiles:
            try: self.tiles[c] = open(self._tile_path(c), 'rb').read()
            except OSError: return None
        return self.tiles[c]

    def data(self):
        with self.lock:
            known = {f[7:].upper() for f in self.flags if f.startswith('action_')}
            spells = {}
            for k, v in self.spells.items():
                spells[k] = dict(v, known=k in known)
            items = [dict(it, src=os.path.basename(p), xml_full=it.get('xml', '')) for p, (_, its) in self.ents.items() for it in its]
            catalog = {}
            unnamed = lambda k: dict(ui=cap(k.replace('_', ' ')), color='#888888', rare=False, icon=None)
            mats = {}
            for it in items:
                it.pop('file', None); it.pop('xml', None)
                if it['kind'] != 'item': it.pop('xml_full', None)
                for s in it['spells'] + it['always']:
                    spells.setdefault(s, dict(name=cap(s.replace('_', ' ').lower()), icon=None, type='Other', order=9999,
                                              odds=0, tiers=[], spawn_tiers=[], rare=False, known=s in known))
                if it['kind'] == 'item':
                    key = item_key(it.get('xml_full', ''), self.catalog, self.by_sprite)
                    it['item'] = key; it['name'] = key
                    if key not in catalog:
                        catalog[key] = dict(self.catalog.get(key) or dict(name=key, icon=None, page=None, description='', effect='',
                                                                          other='', odds={}, quiet=False, rare=False), n=0)
                    catalog[key]['n'] += 1
                if it['kind'] in ('flask', 'pouch'):
                    raw = it.pop('contents', {})
                    cont = {self.mat_order[i] if i < len(self.mat_order) else f'material_{i}': n for i, n in raw.items()}
                    # numbers past the <CellData> block could not be checked against spawn tables
                    it['unverified'] = bool(raw) and max(raw, key=raw.get) >= self.mat_base
                    it['contents'] = sorted(cont.items(), key=lambda kv: -kv[1])
                    it['fill'] = sum(cont.values())
                    if cont:
                        main = it['contents'][0][0]
                        it.update(main=main, **container_note(it['kind'], main, self.wiki))
                        it['name'] = self.mat_info.get(main, unnamed(main))['ui'] + (' pouch' if it['kind'] == 'pouch' else ' flask')
                    else:
                        it.update(main=None, odds=None, notable=False, why=None, name='Empty ' + it['kind'])
                    for k in cont:
                        mats.setdefault(k, dict(n=0, spots=[], containers=0))['containers'] += 1
            for (cx, cy), ch in self.chunks.items():
                for name, (n, x, y) in ch['counts'].items():
                    m = mats.setdefault(name, dict(n=0, spots=[], containers=0))
                    m['n'] += n; m['spots'].append([x, y, n])
            for name, m in mats.items():
                mi = self.mat_info.get(name, unnamed(name))
                m.update(ui=mi['ui'], color=mi['color'], rare=mi['rare'], flask=mi.get('flask'), pouch=mi.get('pouch'), icon=mi.get('icon'))
                m['spots'].sort(key=lambda s: -s[2])
            for it in items: it.pop('xml_full', None)
            for k, v in self.catalog.items():
                catalog.setdefault(k, dict(v, n=0))
            self.icon_urls = getattr(self, 'icon_urls', {})
            def local(url):
                if not url: return None
                n = icon_name(url); self.icon_urls[n] = url; return '/icon/' + n
            for v in spells.values(): v['icon'] = local(v.get('icon'))
            for m in mats.values(): m['icon'] = local(m.get('icon'))
            for c in catalog.values(): c['icon'] = local(c.get('icon'))
            tiles = [[cx, cy, ch.get('v', 0)] for (cx, cy), ch in self.chunks.items()]
            return dict(live=True, div=DIV, tile=TS, world_w=WORLD_W, tiles=tiles, items=items, spells=spells, materials=mats, catalog=catalog,
                        types=TYPES, version=self.version, last=self.last, rare_threshold=RARE_SPELL)

# ---------------------------------------------------------------- http
def make_handler(state, page):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def send(self, code, body, ctype, cache=False):
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'max-age=31536000' if cache else 'no-store')
            self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            path = self.path.split('?')[0]
            if path in ('/', '/index.html'):
                return self.send(200, page().encode('utf-8'), 'text/html; charset=utf-8')
            if path == '/api/data':
                if not state.ready:
                    return self.send(503, json.dumps(dict(status=state.status, error=state.error)).encode(), 'application/json')
                return self.send(200, json.dumps(state.data(), separators=(',', ':')).encode(), 'application/json')
            m = re.match(r'/icon/([A-Za-z0-9._-]+)$', path)
            if m:
                url = getattr(state, 'icon_urls', {}).get(m.group(1))
                if not url: return self.send(404, b'', 'text/plain')
                try: return self.send(200, icon_bytes(state.cache_dir, url), 'image/png', cache=True)
                except Exception: return self.send(502, b'', 'text/plain')
            m = re.match(r'/tile/(-?\d+)_(-?\d+)\.png$', path)
            if m:
                png = state.tile((int(m.group(1)), int(m.group(2))))
                return self.send(200, png, 'image/png', cache=True) if png else self.send(404, b'', 'text/plain')
            self.send(404, b'not found', 'text/plain')
        def do_POST(self):
            if self.path == '/api/refresh':
                if not state.ready: return self.send(503, json.dumps(dict(status=state.status)).encode(), 'application/json')
                try: last = state.refresh()
                except Exception as ex: return self.send(500, json.dumps(dict(error=str(ex))).encode(), 'application/json')
                return self.send(200, json.dumps(last).encode(), 'application/json')
            self.send(404, b'', 'text/plain')
    return H

def template():
    return open(os.path.join(RES, 'map_template.html'), encoding='utf-8').read()

class Lazy:
    """Stands in for State while the game data loads and the first scan runs, so the window can open at once."""
    def __init__(self, args):
        self.ready, self.status, self.error, self.state = False, 'Loading game data…', None, None
        self.args = args
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        a = self.args
        try:
            if not a.wak or not os.path.exists(a.wak):
                raise RuntimeError("Could not find Noita's game data (data.wak). Is Noita installed through Steam?")
            if not os.path.isdir(os.path.join(a.save, 'world')):
                raise RuntimeError(f'No Noita save found at {a.save}. Start a run in Noita first.')
            st = State(a.save, a.wak, a.cache, a.workers)
            self.status = 'Scanning your save. The first scan draws every explored area and takes about a minute; later starts take seconds.'
            st.refresh()
            self.state, self.ready = st, True
        except Exception as ex:
            self.error = str(ex); self.status = 'Could not start'

    def __getattr__(self, k):
        return getattr(self.__dict__['state'], k)

def seed_data():
    """First run of the packaged app: copy the bundled wiki data so it works offline straight away."""
    os.makedirs(DATA, exist_ok=True)
    wiki_data.CACHE = os.path.join(DATA, 'wiki_cache.json')
    seed = os.path.join(RES, 'wiki_cache.json')
    if not os.path.exists(wiki_data.CACHE) and os.path.exists(seed) and os.path.abspath(seed) != os.path.abspath(wiki_data.CACHE):
        import shutil
        shutil.copyfile(seed, wiki_data.CACHE)

def free_port(preferred):
    import socket
    for port in (preferred, 0):
        sock = socket.socket()
        try:
            sock.bind(('127.0.0.1', port)); got = sock.getsockname()[1]; sock.close(); return got
        except OSError:
            sock.close()
    return preferred

def message(text):
    if FROZEN and os.name == 'nt':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, 0x10)
    else:
        print(text)

def export(state, outdir):
    """Static snapshot: one map image plus the page with data embedded."""
    os.makedirs(outdir, exist_ok=True)
    d = state.data()
    cs = [c for c in state.chunks if -WORLD_W // 2 <= c[0] < WORLD_W // 2]   # main world only (image size limit)
    x0 = min(c[0] for c in cs); y0 = min(c[1] for c in cs)
    x1 = max(c[0] for c in cs) + CH; y1 = max(c[1] for c in cs) + CH
    W, H = (x1 - x0) // DIV, (y1 - y0) // DIV
    canvas = Image.new('RGB', (W, H), (18, 16, 22))
    for c in cs:
        canvas.paste(Image.open(io.BytesIO(state.tile(c))), ((c[0] - x0) // DIV, (c[1] - y0) // DIV))
    canvas.save(os.path.join(outdir, 'map.webp'), quality=85, method=6)
    d.update(live=False, image=dict(x=x0, y=y0, w=W, h=H), snapshot=time.strftime('%Y-%m-%d %H:%M'))
    for m in d['materials'].values(): m['spots'] = m['spots'][:60]
    def inline(local):
        if not local: return None
        try: return 'data:image/png;base64,' + base64.b64encode(icon_bytes(state.cache_dir, state.icon_urls[local[6:]])).decode()
        except Exception: return None
    for v in d['spells'].values(): v['icon'] = inline(v['icon'])
    for m in d['materials'].values(): m['icon'] = inline(m['icon'])
    for c in d['catalog'].values(): c['icon'] = inline(c['icon'])
    html = template().replace('/*DATA*/null', json.dumps(d, separators=(',', ':')))
    # the snapshot is a page body: drop the document wrapper the local server needs
    html = re.sub(r'<!doctype html>|</?html[^>]*>|</?head>|</?body>|<meta charset[^>]*>|<meta name="viewport"[^>]*>', '', html, flags=re.I)
    open(os.path.join(outdir, 'index.html'), 'w', encoding='utf-8').write(html)
    print('exported', outdir, f'{W}x{H}')

def main():
    import multiprocessing
    multiprocessing.freeze_support()
    ap = argparse.ArgumentParser(description='Noita save companion')
    ap.add_argument('--save', default=find_save())
    ap.add_argument('--wak', default=find_wak())
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--cache', default=os.path.join(DATA, 'cache'))
    ap.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument('--browser', action='store_true', help='open in your web browser instead of an app window')
    ap.add_argument('--no-browser', action='store_true', help='run the server only')
    ap.add_argument('--export')
    a = ap.parse_args()
    seed_data()
    if a.export:
        if not a.wak or not os.path.exists(a.wak): sys.exit("Could not find Noita's data.wak. Pass --wak <path>.")
        state = State(a.save, a.wak, a.cache, a.workers)
        state.refresh()
        return export(state, a.export)
    lazy = Lazy(a)
    port = free_port(a.port)
    srv = ThreadingHTTPServer(('127.0.0.1', port), make_handler(lazy, template))
    url = f'http://127.0.0.1:{port}/'
    print('companion running at', url)
    window = FROZEN and not a.browser and not a.no_browser
    if window:
        try:
            import webview
        except Exception:
            window = False
    if not window:
        if not a.no_browser: webbrowser.open(url)
        try: srv.serve_forever()
        except KeyboardInterrupt: pass
    else:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            webview.create_window(APP_NAME, url, width=1400, height=900, min_size=(800, 560), background_color='#0f0d14')
            webview.start(private_mode=False, storage_path=os.path.join(DATA, 'webview'))
        except Exception as ex:
            message(f'Could not open the app window ({ex}). Opening it in your browser instead.')
            webbrowser.open(url)
            try: srv.serve_forever()
            except KeyboardInterrupt: pass
        srv.shutdown()
    st = lazy.__dict__.get('state')
    if st and st.pool: st.pool.shutdown(cancel_futures=True)

if __name__ == '__main__':
    main()
