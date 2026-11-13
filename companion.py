"""Noita Companion: a local web app that maps your save and rescans it while you play.

    python companion.py                 # finds the save and the game, opens the browser
    python companion.py --port 8765 --save <save00> --wak <Noita/data/data.wak> --no-browser
    python companion.py --export <dir>  # write a static snapshot (index.html + map.webp) and exit
"""
import os, sys, re, json, time, glob, io, base64, shutil, urllib.request, urllib.parse, threading, argparse, webbrowser
from concurrent.futures import ProcessPoolExecutor
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
FROZEN = getattr(sys, 'frozen', False)
RES = getattr(sys, '_MEIPASS', HERE)        # bundled read-only files: page template, seed wiki data and icons
# writable data: next to the scripts when run from source, %LOCALAPPDATA%\NoitaCompanion when packaged
DATA = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'NoitaCompanion') if FROZEN else HERE
APP_NAME = 'Noita Companion'
from wak import Wak
import noita_map, noita_wands, wiki_data, inventory
from noita_map import CH

DIV = 4                  # map tiles are drawn at 1/DIV scale
TS = CH // DIV           # tile size in pixels
WORLD_W = 35840          # width of one world; parallel worlds repeat every WORLD_W pixels
META = os.path.join(RES, 'fixtures', 'game_meta.json')   # material tables for machines without Noita
TYPES = ['Projectile', 'Static projectile', 'Projectile modifier', 'Multicast', 'Material', 'Other', 'Utility', 'Passive']

# ---------------------------------------------------------------- rarity rules (the user's choices; see HANDOVER.md)
RARE_SPELL = 0.003       # under 0.3% per spell roll at its best tier counts as rare
# Spells always flagged rare/notable, whatever their spawn odds (picked by hand).
NOTABLE_SPELLS = {'NOLLA', 'MANA_REDUCE', 'DIVIDE_10', 'ADD_TRIGGER', 'BLOOD_MAGIC', 'SUMMON_WANDGHOST',
                  'ENERGY_SHIELD', 'RAINBOW_TRAIL'}
# Spells never flagged, even when their spawn odds are low (picked by hand).
NOT_NOTABLE_SPELLS = {'DRAW_RANDOM', 'DRAW_RANDOM_X3', 'DRAW_3_RANDOM', 'RANDOM_SPELL', 'RANDOM_PROJECTILE',
                      'RANDOM_MODIFIER', 'TEMPORARY_PLATFORM', 'TEMPORARY_WALL', 'INFESTATION', 'ANTI_HOMING',
                      'BLOOD_TO_ACID', 'TOXIC_TO_ACID', 'WATER_TO_POISON',
                      'ACID_TRAIL', 'BURN_TRAIL', 'FIRE_TRAIL', 'GUNPOWDER_TRAIL', 'OIL_TRAIL', 'POISON_TRAIL', 'WATER_TRAIL',
                      'HITFX_CRITICAL_BLOOD', 'HITFX_CRITICAL_WATER', 'HITFX_EXPLOSION_SLIME', 'HITFX_EXPLOSION_SLIME_GIGA',
                      'HITFX_EXPLOSION_ALCOHOL', 'HITFX_EXPLOSION_ALCOHOL_GIGA',
                      'HITFX_TOXIC_CHARM'}
RARE_FLASK = 0.01        # a flask or pouch material counts as rare under 1% of that container
# Perks flagged notable. A starting list picked by Claude, not yet confirmed by the user; edit to taste.
NOTABLE_PERKS = {'EXTRA_PERK', 'EDIT_WANDS_EVERYWHERE', 'UNLIMITED_SPELLS', 'PERKS_LOTTERY', 'NO_MORE_SHUFFLE',
                 'PROTECTION_FIRE', 'PROTECTION_EXPLOSION', 'PROTECTION_MELEE', 'PROTECTION_ELECTRICITY',
                 'PROTECTION_RADIOACTIVITY', 'SAVING_GRACE', 'RESPAWN'}
# Hand-picked world materials with no flask odds that are still worth flagging as loose pixels.
# Metals (gold, silver, brass, diamond) are only notable inside a flask, so they are not listed here.
RARE_WORLD = {'magic_liquid_hp_regeneration_unstable', 'creepy_liquid',
              'material_darkness', 'midas', 'midas_precursor', 'void_liquid', 'just_death', 'magic_gas_hp_regeneration',
              'monster_powder_test', 'urine', 'poo', 'mammi', 'cheese_static'}


# ---------------------------------------------------------------- files
def find_save():
    real = os.path.join(os.environ.get('USERPROFILE', os.path.expanduser('~')), 'AppData', 'LocalLow', 'Nolla_Games_Noita', 'save00')
    sample = os.path.join(RES, 'fixtures', 'save00')     # bundled sample, for machines without Noita
    return real if os.path.isdir(os.path.join(real, 'world')) or not os.path.isdir(sample) else sample


def find_wak():
    roots = [r'C:\Program Files (x86)\Steam']
    vdf = os.path.join(roots[0], 'steamapps', 'libraryfolders.vdf')
    if os.path.exists(vdf):
        with open(vdf, encoding='utf-8', errors='replace') as f:
            roots += [p.replace('\\\\', '\\') for p in re.findall(r'"path"\s+"([^"]+)"', f.read())]
    for r in roots:
        p = os.path.join(r, 'steamapps', 'common', 'Noita', 'data', 'data.wak')
        if os.path.exists(p): return p
    return None


def has_game(wakp): return bool(wakp) and os.path.exists(wakp)


def read_bytes(path):
    with open(path, 'rb') as f: return f.read()


def write_atomic(path, data):
    """Write via a temporary file, so a crash never leaves a half-written file behind."""
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f: f.write(data)
    os.replace(tmp, path)


def cap(s): return s[:1].upper() + s[1:]


def pretty(k): return cap(k.replace('_', ' ').lower())


# ---------------------------------------------------------------- spells, materials and items (from the wiki)
def load_spells(wiki):
    """Names, icons, types and spawn odds from the wiki's Spells table. Odds are per spell roll, normalised per tier."""
    tot = {}
    for s in wiki['spells'].values():
        for t, p in zip(s['tiers'], s['weights']): tot[t] = tot.get(t, 0) + p
    out = {}
    for i, (k, s) in enumerate(wiki['spells'].items()):
        odds = {t: p / tot[t] for t, p in zip(s['tiers'], s['weights']) if 0 <= t <= 6 and p > 0 and tot.get(t)}
        best = max(odds.values()) if odds else 0
        out[k] = dict(name=s['name'], icon=s['icon'], type=s['type'], order=i, odds=round(best, 6),
                      tiers=sorted(t for t, v in odds.items() if v >= best * 0.999) if odds else [],
                      spawn_tiers=sorted(odds), notable=k in NOTABLE_SPELLS,
                      rare=k in NOTABLE_SPELLS or (0 < best < RARE_SPELL and k not in NOT_NOTABLE_SPELLS))
    return out


def unknown_spell(k, known):
    """A spell the wiki doesn't list (usually from a mod)."""
    return dict(name=pretty(k), icon=None, type='Other', order=9999, odds=0, tiers=[], spawn_tiers=[], rare=False, known=k in known)


def material_order(wak):
    """Material numbering the game uses in flask contents: every <CellData> in file order, then every <CellDataChild>.
    The wiki has no numbering, so this is the one thing read from the game's own material file."""
    xml = re.sub(r'<!--.*?-->', '', wak.read('data/materials.xml').decode('utf-8', 'replace'), flags=re.S)
    seen, base, child = set(), [], []
    for m in re.finditer(r'<(CellData|CellDataChild)\b[^>]*?\bname="([^"]+)"', xml):
        if m.group(2) in seen: continue
        seen.add(m.group(2)); (base if m.group(1) == 'CellData' else child).append(m.group(2))
    return base + child, len(base)


def load_materials(wiki, textures):
    """Names from the wiki; flask and pouch odds from the wiki's Potions and Powder Pouch pages.
    Swatch colours match the map (they come from the textures the map is drawn with)."""
    info = {}
    for k, m in wiki['materials'].items():
        arr, color = textures.get(k), '#888888'
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
    names = {'secret': 'secret-material flasks (chests)', 'random': 'random-material flasks (chests)', 'aggressive': 'Hiisi alchemist flasks'}
    why = 'Only from ' + ' or '.join(names[p] for p in pools) if pools else 'Not in any spawn pool: filled by you or by the world'
    return dict(odds=None, notable=True, why=why)


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
               'goldnugget': ('Gold nugget', 'Gold', True),
               'greed_curse': ('Curse of Greed', 'Curse of Greed', False),
               'essence_laser': ('Essence of Earth', 'Essences', False)}


def catalog_entry(name, page=None, quiet=False, **kw):
    return dict(dict(name=name, icon=None, page=page, description='', effect='', other='', odds={}, quiet=quiet, rare=False), **kw)


def item_catalog(wiki):
    cat = {}
    for it in wiki.get('items', []):
        if it['name'] in cat: continue
        odds = {k: v for k, v in it['odds'].items() if v}
        cat[it['name']] = catalog_entry(it['name'], it['page'], icon=it.get('icon'), description=it['description'],
                                        effect=it['effect'], other=it['other'], odds=odds,
                                        rare=bool(odds) and max(odds.values()) < RARE_FLASK)
    for name, page, quiet in EXTRA_ITEMS.values():
        cat.setdefault(name, catalog_entry(name, page, quiet))
    return cat


def item_key(xml, cat, by_sprite):
    """The catalog name for a pickup's entity file."""
    base = xml.rsplit('/', 1)[-1][:-4]
    if '/runestones/' in xml: return 'Runestone'
    if base.startswith('goldnugget'): return 'Gold nugget'
    if base in EXTRA_ITEMS: return EXTRA_ITEMS[base][0]
    if base in ITEM_ALIASES and ITEM_ALIASES[base] in cat: return ITEM_ALIASES[base]
    return by_sprite.get('item ' + base.replace('_', ' ')) or cap(base.replace('_', ' '))


# ---------------------------------------------------------------- game tables and map tiles (tiles run in worker processes)
def game_tables(wakp):
    """(material textures, material numbering, CellData count). From Noita's data.wak when installed;
    otherwise from fixtures/game_meta.json (flat colours), so the app also runs without the game (cloud sessions)."""
    if has_game(wakp):
        wak = Wak(wakp)
        order, base = material_order(wak)
        return noita_map.material_table(wak), order, base
    with open(META, encoding='utf-8') as f: meta = json.load(f)
    textures = {k: np.array(c, np.uint8).reshape(1, 1, 4) for k, c in meta['colors'].items()}
    return textures, meta['material_order'], meta['material_base']


_textures = None             # per process: material textures for drawing tiles

def _init_worker(wakp):
    global _textures
    if _textures is None: _textures = game_tables(wakp)[0]


def png(img, **kw):
    buf = io.BytesIO(); img.save(buf, 'PNG', **kw); return buf.getvalue()


def process_chunk(args):
    """(quarter-scale PNG, {material: [pixels, mean x, mean y]}) for one chunk."""
    path, cx, cy = args
    cells, names, custom = noita_map.read_chunk(path)
    idx = (cells & 0x7f).ravel()
    ys, xs = np.divmod(np.arange(CH * CH), CH)
    cnt = np.bincount(idx, minlength=len(names))
    sx = np.bincount(idx, weights=xs, minlength=len(names))
    sy = np.bincount(idx, weights=ys, minlength=len(names))
    counts = {names[i]: [int(cnt[i]), int(cx + sx[i] / cnt[i]), int(cy + sy[i] / cnt[i])]
              for i in range(len(names)) if cnt[i] and names[i] != 'air'}
    img = noita_map.on_sky(noita_map.render_cells(cells, names, custom, cx, cy, _textures), cy)
    return png(img.resize((TS, TS), Image.BOX), optimize=False), counts


# ---------------------------------------------------------------- wiki icons (cached on disk)
ICON_RE = re.compile(r'[^A-Za-z0-9._-]')
def icon_name(url): return ICON_RE.sub('_', urllib.parse.unquote(url.rsplit('/', 1)[-1].split('?')[0]))


def icon_bytes(cache_dir, url):
    path = os.path.join(cache_dir, 'icons', icon_name(url))
    for p in (path, os.path.join(RES, 'seed_icons', icon_name(url))):     # cache, then icons shipped with the app
        if os.path.exists(p): return read_bytes(p)
    with urllib.request.urlopen(urllib.request.Request(url, headers=wiki_data.UA), timeout=20) as r:
        data = r.read()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_atomic(path, data)
    return data


# ---------------------------------------------------------------- live state
class State:
    FULL_KEEP = 800      # full-detail tiles kept on disk (100-300 KB each); the oldest are deleted past this

    def __init__(self, save, wakp, cache_dir, workers):
        self.save, self.wakp, self.cache_dir, self.workers = save, wakp, cache_dir, workers
        self.world = os.path.join(save, 'world')
        self.lock, self.full_lock = threading.Lock(), threading.Lock()
        self.version, self.last = 0, {}
        self.ents, self.flags, self.icon_urls = {}, set(), {}
        self.pool = None
        self.run, self.run_key = {}, None
        self.full_writes = 0
        for d in ('tiles', 'tiles_full'): os.makedirs(os.path.join(cache_dir, d), exist_ok=True)
        self.wiki = wiki_data.load()
        textures, self.mat_order, self.mat_base = game_tables(wakp)
        self.spells = load_spells(self.wiki)
        self.mat_info = load_materials(self.wiki, textures)
        self.catalog = item_catalog(self.wiki)
        self.perks = {k: dict(v, notable=k in NOTABLE_PERKS) for k, v in self.wiki.get('perks', {}).items()}
        self.by_sprite = {}
        for it in self.wiki.get('items', []): self.by_sprite.setdefault(it['sprite'].lower(), it['name'])
        self.index_path = os.path.join(cache_dir, 'chunks.json')
        try:
            with open(self.index_path) as f:
                self.chunks = {tuple(map(int, k.split('_'))): v for k, v in json.load(f).items()}
        except Exception:
            self.chunks = {}

    # -------------------------------------------------------- rescanning
    def refresh(self):
        """Re-read whatever changed on disk since the last call. Returns a summary for the page's status line."""
        with self.lock:
            t0 = time.time()
            chunks_changed = self._scan_world()
            ents_changed = self._scan_entities()
            flags = self._read_flags()
            if flags != self.flags: self.flags = flags; ents_changed += 1
            if self._scan_run(): ents_changed += 1
            if chunks_changed or ents_changed: self.version += 1
            self.last = dict(time=time.time(), chunks=chunks_changed, entity_files=ents_changed,
                             took=round(time.time() - t0, 2), version=self.version)
            return self.last

    def _scan_world(self):
        """Redraw tiles for new or changed chunks; returns how many chunks changed or disappeared."""
        todo, seen = [], set()
        for e in os.scandir(self.world):
            m = re.match(r'world_(-?\d+)_(-?\d+)\.png_petri$', e.name)
            if not m: continue
            c = (int(m.group(1)), int(m.group(2))); seen.add(c)
            st = e.stat(); key = [st.st_mtime_ns, st.st_size]
            old = self.chunks.get(c)
            if not old or old['key'] != key or not os.path.exists(self._tile_path(c)):
                todo.append((e.path, c, key))
        gone = set(self.chunks) - seen
        for c in gone: del self.chunks[c]
        if todo:
            jobs = [(p, c[0], c[1]) for p, c, _ in todo]
            if len(todo) > 8 and self.workers > 1:
                if not self.pool: self.pool = ProcessPoolExecutor(self.workers, initializer=_init_worker, initargs=(self.wakp,))
                results = self.pool.map(process_chunk, jobs, chunksize=4)
            else:
                _init_worker(self.wakp)
                results = map(process_chunk, jobs)
            for (_, c, key), (tile, counts) in zip(todo, results):
                try:
                    write_atomic(self._tile_path(c), tile)
                except OSError as ex:
                    print('tile write failed', c, ex); continue
                try: os.remove(self._full_path(c))       # the full-detail tile is redrawn on next request
                except OSError: pass
                self.chunks[c] = dict(key=key, counts=counts, v=self.version + 1)
        if todo or gone:
            write_atomic(self.index_path, json.dumps({f'{a}_{b}': v for (a, b), v in self.chunks.items()}).encode())
        return len(todo) + len(gone)

    def _scan_entities(self):
        """Rescan changed entity files; returns how many changed or disappeared."""
        changed, seen = 0, set()
        for e in os.scandir(self.world):
            if not (e.name.startswith('entities_') and e.name.endswith('.bin')): continue
            seen.add(e.path)
            st = e.stat(); key = [st.st_mtime_ns, st.st_size]
            old = self.ents.get(e.path)
            if old and old[0] == key: continue
            try: self.ents[e.path] = (key, noita_wands.scan_file(e.path)); changed += 1
            except Exception as ex: print('entity file failed', e.name, ex)
        for p in set(self.ents) - seen: del self.ents[p]; changed += 1
        return changed

    def _read_flags(self):
        f = os.path.join(self.save, 'persistent', 'flags')
        return set(os.listdir(f)) if os.path.isdir(f) else set()

    def _scan_run(self):
        """Re-read player.xml / world_state.xml when either changed (they are not rewritten while playing)."""
        key = []
        for n in ('player.xml', 'world_state.xml'):
            try: st = os.stat(os.path.join(self.save, n)); key.append((st.st_mtime_ns, st.st_size))
            except OSError: key.append(None)
        if key == self.run_key: return False
        self.run, self.run_key = inventory.read(self.save), key
        return True

    # -------------------------------------------------------- tiles
    def _tile_path(self, c): return os.path.join(self.cache_dir, 'tiles', f'{c[0]}_{c[1]}.png')
    def _full_path(self, c): return os.path.join(self.cache_dir, 'tiles_full', f'{c[0]}_{c[1]}.png')

    def tile(self, c):
        try: return read_bytes(self._tile_path(c))
        except OSError: return None

    def full_tile(self, c):
        """The chunk at 1 pixel per world pixel. Drawn on first request and cached on disk."""
        try: return read_bytes(self._full_path(c))
        except OSError: pass
        src = os.path.join(self.world, f'world_{c[0]}_{c[1]}.png_petri')
        if c not in self.chunks or not os.path.exists(src): return None
        with self.full_lock: _init_worker(self.wakp)
        try:
            data = png(noita_map.on_sky(noita_map.render_chunk(src, c[0], c[1], _textures), c[1]), compress_level=1)
        except Exception as ex:
            print('full tile failed', c, ex); return None
        try:
            write_atomic(self._full_path(c), data)
            self._prune_full()
        except OSError as ex:
            print('full tile write failed', c, ex)
        return data

    def _prune_full(self):
        with self.full_lock:
            self.full_writes += 1
            if self.full_writes % 50: return
        files = sorted(os.scandir(os.path.join(self.cache_dir, 'tiles_full')), key=lambda e: e.stat().st_mtime)
        for e in files[:max(0, len(files) - self.FULL_KEEP)]:
            try: os.remove(e.path)
            except OSError: pass

    # -------------------------------------------------------- page data
    def data(self):
        """Everything the page shows, as one JSON-able dict."""
        with self.lock:
            known = {f[7:].upper() for f in self.flags if f.startswith('action_')}
            spells = {k: dict(v, known=k in known) for k, v in self.spells.items()}
            items, catalog, mats = self._items()
            self._add_world_materials(mats)
            for k, v in self.catalog.items(): catalog.setdefault(k, dict(v, n=0))
            inv, run = self._inventory(), self._run_state()
            carried = [s for w in (inv['wands'] if inv else []) for s in w['spells'] + w['always']] + (inv['spells'] if inv else [])
            for s in [s for it in items for s in it['spells'] + it['always']] + carried:
                if s not in spells: spells[s] = unknown_spell(s, known)
            perks = {k: dict(v) for k, v in self.perks.items()}
            for k in {it['perk'] for it in items if it['kind'] == 'perk' and it.get('perk')} | set(run['perks']):
                perks.setdefault(k, dict(name=pretty(k), page=None, description='', icon=None, stackable=False, notable=k in NOTABLE_PERKS))
            for group in (spells, mats, catalog, perks):
                for v in group.values(): v['icon'] = self._icon_url(v.get('icon'))
            return dict(live=True, div=DIV, tile=TS, world_w=WORLD_W, types=TYPES, rare_threshold=RARE_SPELL,
                        version=self.version, last=self.last,
                        tiles=[[cx, cy, ch.get('v', 0)] for (cx, cy), ch in self.chunks.items()],
                        items=items, spells=spells, materials=mats, catalog=catalog, perks=perks, inventory=inv, run=run)

    def _icon_url(self, url):
        """The page loads wiki icons through /icon/<name>, which serves them from the disk cache."""
        if not url: return None
        n = icon_name(url); self.icon_urls[n] = url
        return '/icon/' + n

    def _mat_label(self, k):
        m = self.mat_info.get(k)
        return (m['ui'], m['color']) if m else (cap(k.replace('_', ' ')), '#888888')

    def _items(self):
        """Items for the page, the per-item catalog counts, and the materials found in containers."""
        items, catalog, mats = [], {}, {}
        for path, (_, found) in self.ents.items():
            for raw in found:
                it = {k: v for k, v in raw.items() if k not in ('file', 'xml', 'contents')}
                it['src'] = os.path.basename(path)
                if it['kind'] == 'perk':
                    p = self.perks.get(it.get('perk') or '')
                    it['name'] = p['name'] if p else pretty(it.get('perk') or 'unknown perk')
                    it['notable'] = bool(p and p['notable'])
                elif it['kind'] == 'item':
                    key = item_key(raw.get('xml', ''), self.catalog, self.by_sprite)
                    it['item'] = it['name'] = key
                    catalog.setdefault(key, dict(self.catalog.get(key) or catalog_entry(key), n=0))['n'] += 1
                elif it['kind'] in ('flask', 'pouch'):
                    self._decode_container(it, raw.get('contents', {}))
                    for k, _ in it['contents']:
                        mats.setdefault(k, dict(n=0, spots=[], containers=0))['containers'] += 1
                items.append(it)
        return items, catalog, mats

    def _decode_container(self, it, raw):
        """Material numbers -> names, plus odds and a name for the flask or pouch."""
        cont = {self.mat_order[i] if i < len(self.mat_order) else f'material_{i}': n for i, n in raw.items()}
        it['unverified'] = bool(raw) and max(raw, key=raw.get) >= self.mat_base   # numbers past <CellData> are unchecked
        it['contents'] = sorted(cont.items(), key=lambda kv: -kv[1])
        it['fill'] = sum(cont.values())
        if not cont:
            it.update(main=None, odds=None, notable=False, why=None, name='Empty ' + it['kind'])
            return
        main = it['contents'][0][0]
        it.update(main=main, **container_note(it['kind'], main, self.wiki))
        it['name'] = self._mat_label(main)[0] + (' pouch' if it['kind'] == 'pouch' else ' flask')

    def _add_world_materials(self, mats):
        """Add pixel counts and spots from the map chunks, and names, colours and odds for every material."""
        for ch in self.chunks.values():
            for name, (n, x, y) in ch['counts'].items():
                m = mats.setdefault(name, dict(n=0, spots=[], containers=0))
                m['n'] += n; m['spots'].append([x, y, n])
        for name, m in mats.items():
            mi = self.mat_info.get(name)
            ui, color = self._mat_label(name)
            m.update(ui=ui, color=color, rare=bool(mi and mi['rare']), flask=mi and mi.get('flask'),
                     pouch=mi and mi.get('pouch'), icon=mi and mi.get('icon'))
            m['spots'].sort(key=lambda s: -s[2])

    def _inventory(self):
        p = self.run.get('player')
        if not p: return None
        containers = []
        for c in p['containers']:
            cont = [[k, *self._mat_label(k), n] for k, n in c['contents']]
            name = (cont[0][1] if cont else 'Empty') + (' pouch' if c['kind'] == 'pouch' else ' flask')
            containers.append(dict(kind=c['kind'], name=name, contents=cont, fill=c['fill']))
        return dict(time=self.run.get('player_time'), wands=p['wands'], spells=p['spells'], containers=containers,
                    items=p['items'], hp=p['hp'], max_hp=p['max_hp'], gold=p['gold'])

    def _run_state(self):
        w, p = self.run.get('world'), self.run.get('player')
        run = dict(time=self.run.get('world_time'), perks={}, orbs_run=[], orbs_ever=[], orb_rooms=[], shifts=[],
                   temples_visited=0, temples_leaked=0, temples_collapsed=[], day=None)
        if w:
            run.update(w, temples_leaked=len(w['temples_leaked']),
                       shifts=[[[a, *self._mat_label(a)], [b, *self._mat_label(b)]] for a, b in w['shifts']])
        if not run['perks'] and p: run['perks'] = {k: 1 for k in p['perk_icons']}
        return run


# ---------------------------------------------------------------- http
def make_handler(state, page):
    def send_json(h, code, obj): h.send(code, json.dumps(obj, separators=(',', ':')).encode(), 'application/json')

    class Handler(BaseHTTPRequestHandler):
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
                if not state.ready: return send_json(self, 503, dict(status=state.status, error=state.error))
                return send_json(self, 200, state.data())
            m = re.match(r'/icon/([A-Za-z0-9._-]+)$', path)
            if m:
                url = state.icon_urls.get(m.group(1)) if state.ready else None
                if not url: return self.send(404, b'', 'text/plain')
                try: return self.send(200, icon_bytes(state.cache_dir, url), 'image/png', cache=True)
                except Exception: return self.send(502, b'', 'text/plain')
            m = re.match(r'/tile/(full/)?(-?\d+)_(-?\d+)\.png$', path)
            if m:
                if not state.ready: return self.send(503, b'', 'text/plain')
                c = (int(m.group(2)), int(m.group(3)))
                data = state.full_tile(c) if m.group(1) else state.tile(c)
                return self.send(200, data, 'image/png', cache=True) if data else self.send(404, b'', 'text/plain')
            self.send(404, b'not found', 'text/plain')

        def do_POST(self):
            if self.path.split('?')[0] != '/api/refresh': return self.send(404, b'', 'text/plain')
            if not state.ready: return send_json(self, 503, dict(status=state.status))
            try: return send_json(self, 200, state.refresh())
            except Exception as ex: return send_json(self, 500, dict(error=str(ex)))
    return Handler


def template():
    with open(os.path.join(RES, 'map_template.html'), encoding='utf-8') as f: return f.read()


class Lazy:
    """Stands in for State while the game data loads and the first scan runs, so the window can open at once."""
    def __init__(self, args):
        self.ready, self.status, self.error, self.state = False, 'Loading game data…', None, None
        self.args = args
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        a = self.args
        try:
            if not has_game(a.wak) and not os.path.exists(META):
                raise RuntimeError("Could not find Noita's game data (data.wak). Is Noita installed through Steam?")
            if not os.path.isdir(os.path.join(a.save, 'world')):
                raise RuntimeError(f'No Noita save found at {a.save}. Start a run in Noita first.')
            st = State(a.save, a.wak, a.cache, a.workers)
            self.status = 'Scanning your save. The first scan draws every explored area and takes about a minute; later starts take seconds.'
            st.refresh()
            self.state, self.ready = st, True
        except Exception as ex:
            self.error, self.status = str(ex), 'Could not start'

    def __getattr__(self, k):       # only reached for State attributes, once ready
        return getattr(self.__dict__['state'], k)


# ---------------------------------------------------------------- app
def seed_data():
    """First run of the packaged app: copy the bundled wiki data so it works offline straight away."""
    os.makedirs(DATA, exist_ok=True)
    wiki_data.CACHE = os.path.join(DATA, 'wiki_cache.json')
    seed = os.path.join(RES, 'wiki_cache.json')
    if not os.path.exists(wiki_data.CACHE) and os.path.exists(seed) and os.path.abspath(seed) != os.path.abspath(wiki_data.CACHE):
        shutil.copyfile(seed, wiki_data.CACHE)


def free_port(preferred):
    import socket
    for port in (preferred, 0):
        with socket.socket() as sock:
            try:
                sock.bind(('127.0.0.1', port)); return sock.getsockname()[1]
            except OSError:
                pass
    return preferred


def message(text):
    if FROZEN and os.name == 'nt':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, 0x10)
    else:
        print(text)


def export(state, outdir):
    """Static snapshot: one map image of the main world plus the page with the data embedded."""
    os.makedirs(outdir, exist_ok=True)
    d = state.data()
    cs = [c for c in state.chunks if -WORLD_W // 2 <= c[0] < WORLD_W // 2]   # main world only (WebP size limit)
    x0 = min(c[0] for c in cs); y0 = min(c[1] for c in cs)
    x1 = max(c[0] for c in cs) + CH; y1 = max(c[1] for c in cs) + CH
    W, H = (x1 - x0) // DIV, (y1 - y0) // DIV
    canvas = Image.new('RGB', (W, H), (18, 16, 22))
    for c in cs:
        tile = state.tile(c)
        if tile: canvas.paste(Image.open(io.BytesIO(tile)), ((c[0] - x0) // DIV, (c[1] - y0) // DIV))
    canvas.save(os.path.join(outdir, 'map.webp'), quality=85, method=6)
    d.update(live=False, image=dict(x=x0, y=y0, w=W, h=H), snapshot=time.strftime('%Y-%m-%d %H:%M'))
    for m in d['materials'].values(): m['spots'] = m['spots'][:60]

    def inline(local):
        if not local: return None
        try: return 'data:image/png;base64,' + base64.b64encode(icon_bytes(state.cache_dir, state.icon_urls[local[6:]])).decode()
        except Exception: return None
    for group in ('spells', 'materials', 'catalog', 'perks'):
        for v in d[group].values(): v['icon'] = inline(v['icon'])
    html = template().replace('/*DATA*/null', json.dumps(d, separators=(',', ':')))
    # the snapshot is a page body: drop the document wrapper the local server needs
    html = re.sub(r'<!doctype html>|</?html[^>]*>|</?head>|</?body>|<meta charset[^>]*>|<meta name="viewport"[^>]*>', '', html, flags=re.I)
    with open(os.path.join(outdir, 'index.html'), 'w', encoding='utf-8') as f: f.write(html)
    print('exported', outdir, f'{W}x{H}')


def main():
    import multiprocessing
    multiprocessing.freeze_support()
    ap = argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument('--save', default=find_save())
    ap.add_argument('--wak', default=find_wak())
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--cache', default=os.path.join(DATA, 'cache'))
    ap.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument('--browser', action='store_true', help='open in your web browser instead of an app window')
    ap.add_argument('--no-browser', action='store_true', help='run the server only')
    ap.add_argument('--export', metavar='DIR', help='write a static snapshot to DIR and exit')
    a = ap.parse_args()
    seed_data()
    if a.export:
        if not has_game(a.wak) and not os.path.exists(META): sys.exit("Could not find Noita's data.wak. Pass --wak <path>.")
        state = State(a.save, a.wak, a.cache, a.workers)
        state.refresh()
        return export(state, a.export)
    lazy = Lazy(a)
    port = free_port(a.port)
    srv = ThreadingHTTPServer(('127.0.0.1', port), make_handler(lazy, template))
    url = f'http://127.0.0.1:{port}/'
    print('companion running at', url)
    print('save:', a.save)
    print('game data:', a.wak if has_game(a.wak) else 'not found, using fixtures/game_meta.json (flat map colours)')
    window = FROZEN and not a.browser and not a.no_browser
    if window:
        try: import webview
        except Exception: window = False
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
