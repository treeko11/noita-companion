"""Spell and material data from the Noita wiki (noita.wiki.gg), cached locally.

    python wiki_data.py            # refresh the cache (wiki_cache.json)

Sources:
  - Cargo table "Spells":    id, name, icon, type, tiers, spawn weights
  - Cargo table "Materials": id, name, type, tags, images
  - Page "Potions":          regular flask pools and odds
  - Page "Powder Pouch":     pouch pools and odds
  - Page "Items":            pickup items and their odds
  - Cargo table "Perks":     id, name, icon, description
"""
import os, re, json, time, urllib.request, urllib.parse

API = 'https://noita.wiki.gg/api.php'
UA = {'User-Agent': 'noita-save-companion/0.1 (local tool)'}
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'wiki_cache.json')
MAX_AGE = 7 * 24 * 3600

def api(**params):
    params['format'] = 'json'
    url = API + '?' + urllib.parse.urlencode(params)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.load(r)
        except Exception:
            if attempt == 2: raise
            time.sleep(2)

def cargo(table, fields):
    rows, offset = [], 0
    while True:
        j = api(action='cargoquery', tables=table, fields=fields, limit=500, offset=offset)
        got = [r['title'] for r in j.get('cargoquery', [])]
        rows += got
        if len(got) < 500: return rows
        offset += 500

def wikitext(page):
    return api(action='parse', page=page, prop='wikitext')['parse']['wikitext']['*']

def image_urls(files):
    """File names -> direct image URLs (50 per request)."""
    out, files = {}, sorted({f for f in files if f})
    for i in range(0, len(files), 50):
        chunk = files[i:i + 50]
        j = api(action='query', titles='|'.join('File:' + f for f in chunk), prop='imageinfo', iiprop='url')
        norm = {n['to']: n['from'] for n in j['query'].get('normalized', [])}
        for p in j['query']['pages'].values():
            if 'imageinfo' in p:
                title = norm.get(p['title'], p['title'])
                out[title[5:]] = p['imageinfo'][0]['url']
    return out

def section(text, heading):
    m = re.search(r'^=+\s*' + re.escape(heading) + r'\s*=+\s*$', text, re.M)
    if not m: return ''
    nxt = re.search(r'^==', text[m.end():], re.M)
    return text[m.end():m.end() + nxt.start()] if nxt else text[m.end():]

def templates(text, name):
    return [a.strip() for a in re.findall(r'\{\{' + name + r'\|([^}|]+)', text)]

def build():
    spells = cargo('Spells', 'id,name,image,type,spellTier,spawnProbability,price')
    mats = cargo('Materials', 'id,name,image,icon,type,tags,unused')
    ids = {m['id'].lower(): m['id'] for m in mats if m.get('id')}
    cands = {}
    for m in mats:
        if m.get('id'): cands.setdefault(m['name'].lower(), []).append(m)
    def resolve(n):
        n = n.strip().lower().replace('&#39;', "'")
        if n in ids: return ids[n]                       # template used the material id
        c = cands.get(n)
        if not c: return None
        plain = n.replace(' ', '_')
        for m in c:
            if m['id'] == plain: return m['id']
        # several ids share a display name: prefer used materials, then the shortest id
        return sorted(c, key=lambda m: (m.get('unused') == '1', len(m['id'])))[0]['id']

    # flask odds (regular potion, potion.lua) from the Potions page
    pt = wikitext('Potions')
    reg = section(pt, 'Regular Potions')
    pct = lambda s: float(s) / 100
    magic_total = pct(re.search(r'random \{\{section\|Magical Material\}\}.*?\(([\d.]+)%\)', reg).group(1))
    normal_total = pct(re.search(r'random \{\{section\|Normal Material\}\}.*?\(([\d.]+)%\)', reg).group(1))
    special = {resolve(n): pct(p) for n, p in re.findall(r'\{\{potion\|([^}|]+)\}\}\s*<small>\'\'\(([\d.]+)%\)', reg)}
    normal = [resolve(n) for n in templates(section(pt, 'Normal Material'), 'potion')]
    magic = [resolve(n) for n in templates(section(pt, 'Magical Material').split('====')[0], 'potion')]
    flask = dict(special)
    for x in normal: flask[x] = flask.get(x, 0) + normal_total / len(normal)
    for x in magic: flask[x] = flask.get(x, 0) + magic_total / len(magic)
    pools = {k: [resolve(n) for n in templates(section(pt, h), 'potion')]
             for k, h in [('secret', 'Secret Material'), ('aggressive', 'Aggressive Material'), ('random', 'Random Material')]}

    # pouch odds from the Powder Pouch page
    pp = wikitext('Powder Pouch')
    pouch = {}
    for share, block in re.findall(r'!\s*([\d.]+)%\s*\n(.*?)(?=\n\|-|\n\|\})', pp, re.S):
        items = [resolve(n) or n for n in templates(block, 'pouch')]
        for x in items: pouch[x] = pouch.get(x, 0) + pct(share) / len(items)

    items = parse_items(wikitext('Items'))
    perks = [p for p in cargo('Perks', '_pageName=page,Name,Image,Description,id,Stackable,NotAPerk,Unimplemented')
             if p.get('id') and p.get('NotAPerk') != '1' and p.get('Unimplemented') != '1']
    urls = image_urls([s['image'] for s in spells] + [m['icon'].replace('File:', '') for m in mats if m.get('icon')]
                      + [m['image'] for m in mats if m.get('image')] + [it['sprite'] + '.png' for it in items]
                      + [p['Image'] for p in perks if p.get('Image')])
    for it in items: it['icon'] = urls.get(it['sprite'] + '.png')
    out = dict(
        fetched=time.time(), source='https://noita.wiki.gg/',
        spells={s['id']: dict(name=s['name'], type=s['type'], icon=urls.get(s['image']),
                              tiers=[int(t) for t in s['spellTier'].split(',') if t.strip().lstrip('-').isdigit()],
                              weights=[float(p) for p in s['spawnProbability'].split(',') if p.strip()],
                              price=s.get('price')) for s in spells if s.get('id')},
        materials={m['id']: dict(name=m['name'], type=m['type'], tags=m.get('tags', ''),
                                 image=urls.get(m.get('image')), icon=urls.get(m.get('icon', '').replace('File:', '')),
                                 unused=m.get('unused') == '1') for m in mats if m.get('id')},
        flask={k: round(v, 7) for k, v in flask.items() if k},
        pouch={k: round(v, 7) for k, v in pouch.items() if k},
        pools={k: [x for x in v if x] for k, v in pools.items()},
        items=items,
        perks={p['id']: dict(name=p['Name'], page=p['page'], description=plain(p.get('Description') or ''),
                             icon=urls.get(p.get('Image')), stackable=p.get('Stackable') == '1') for p in perks},
    )
    return out

ODDS_COLS = ['pedestal', 'lab_pedestal', 'chest', 'great_chest']

def plain(s):
    s = re.sub(r'<!--.*?-->', '', s, flags=re.S)
    s = re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]+)\]\]', r'\1', s)
    s = re.sub(r'\{\{[^{}]*\}\}', '', s)
    s = re.sub(r'<[^>]+>', '', s)
    return ' '.join(s.split())

def parse_items(text):
    """Rows of the tables on the wiki's Items page: sprite, name, description, effect, odds per source."""
    out = []
    for row in re.split(r'\n\|-[^\n]*', text):
        m = re.search(r'\{\{sprite\|([^|}]+)[^}]*\}\}', row)
        if not m or row.lstrip().startswith('!'): continue
        cells = re.split(r'\n[|!](?!-|\})', '\n' + row.split(m.group(0), 1)[1])
        cells = [re.sub(r'^\s*(?:scope=row|rowspan=\d+|align=\w+)\s*\|', '', c).strip() for c in cells[1:]]
        if not cells: continue
        name_m = re.search(r'\[\[([^|\]]+)(?:\|([^\]]+))?\]\]', cells[0])
        name = (name_m.group(2) or name_m.group(1)) if name_m else plain(cells[0])
        odds = {}
        for col, c in zip(ODDS_COLS, cells[3:7]):
            p = re.search(r'<big>\s*([\d.]+)%', c)
            odds[col] = float(p.group(1)) / 100 if p else None
        out.append(dict(sprite=m.group(1).strip(), name=name.strip(), page=name_m.group(1).strip() if name_m else name,
                        description=plain(cells[1]) if len(cells) > 1 else '', effect=plain(cells[2]) if len(cells) > 2 else '',
                        odds=odds, other=plain(cells[7]) if len(cells) > 7 else ''))
    return out

def read_cache():
    with open(CACHE, encoding='utf-8') as f: return json.load(f)


def load(refresh=False):
    """The cached wiki data, rebuilt from the wiki when older than a week (or missing a newer table, e.g. perks)."""
    if not refresh and os.path.exists(CACHE):
        data = read_cache()
        if time.time() - data.get('fetched', 0) < MAX_AGE and 'perks' in data: return data
    try:
        data = build()
    except Exception as ex:
        if os.path.exists(CACHE):
            print('wiki unreachable, using cached data:', ex)
            return read_cache()
        raise
    tmp = CACHE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f: json.dump(data, f)
    os.replace(tmp, CACHE)
    return data

if __name__ == '__main__':
    d = load(refresh=True)
    print(len(d['spells']), 'spells,', len(d['materials']), 'materials')
    print('flask odds:', {k: f'{v*100:.4f}%' for k, v in sorted(d['flask'].items(), key=lambda x: x[1])})
    print('pouch odds:', {k: f'{v*100:.2f}%' for k, v in d['pouch'].items()})
    print('pools:', {k: len(v) for k, v in d['pools'].items()})
    print('spell icons:', sum(1 for s in d['spells'].values() if s['icon']), 'material icons:', sum(1 for m in d['materials'].values() if m['icon']))
