"""List wands (and loose spell cards) stored in a Noita save's world/entities_*.bin files.
Usage: python noita_wands.py <save00/world> [ACTION_ID_TO_HIGHLIGHT] [--only] [--json]
Field layouts follow data/schemas/c8ecfb341d22516067569b04563bff9c.xml and data/scripts/gun/gunaction_generated.lua.
"""
import sys, os, glob, re, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from noita_decomp import load

HDR = re.compile(rb'\x00\x00\x00([\x08-\xff])((?:data|mods)/[ -~]+?\.xml)')

class R:
    def __init__(self, d, p): self.d, self.p = d, p
    def i(self): v = struct.unpack_from('>i', self.d, self.p)[0]; self.p += 4; return v
    def f(self): v = struct.unpack_from('>f', self.d, self.p)[0]; self.p += 4; return v
    def b(self): v = self.d[self.p]; self.p += 1; return bool(v)
    def s(self):
        n = struct.unpack_from('>I', self.d, self.p)[0]
        if n > 4096: raise ValueError('bad string')
        v = self.d[self.p + 4:self.p + 4 + n].decode('latin1'); self.p += 4 + n; return v
    def skip(self, n): self.p += n

def component(d, name, start, end):
    """Reader positioned at the first field of component `name` within d[start:end], or None."""
    key = struct.pack('>I', len(name)) + name.encode()
    k = d.find(key, start, end)
    if k < 0: return None
    r = R(d, k + len(key))
    r.skip(2)  # enabled flags
    r.s()      # component tags
    return r

def read_ability(r):
    r.i(); r.s(); r.s(); r.i(); r.b(); r.i()            # cooldown, entity_file, sprite_file, entity_count, never_reload, reload_time_frames
    mana = r.f(); mana_max = r.f(); charge = r.f()
    r.b(); r.f(); r.f(); r.b(); r.f(); r.i(); r.i()     # rotate.., fast_projectile, swim, max_charged, charge_wait
    r.f(); r.f(); r.f(); r.f(); r.s(); r.b(); r.b()     # recoil x4, base_item_file, use_entity_file.., click_to_use
    r.i(); r.i(); r.b(); r.b(); r.b(); r.i(); r.i(); r.b()
    ui_name = r.s(); r.b(); r.b()                       # use_gun_script, is_petris_gun
    apr = r.i(); shuffle = r.b(); reload = r.i(); cap = r.i()   # ConfigGun
    for _ in range(5): r.s()                            # action_id .. unidentified sprite
    r.i(); r.s(); r.s(); r.s(); r.b(); r.i(); r.s()     # type, spawn level/prob/flag, manual_unlock, max_uses, custom_xml
    r.f(); r.b(); r.i(); r.b(); r.b(); r.b(); r.i(); r.b(); r.b()
    delay = r.i(); speed = r.f(); r.f(); r.f(); r.f(); spread = r.f()
    return dict(name=ui_name, mana=round(mana), mana_max=round(mana_max), mana_charge=round(charge),
                per_cast=apr, shuffle=shuffle, recharge=reload, capacity=cap, cast_delay=delay,
                spread=round(spread, 1), speed=round(speed, 2))

def read_item(r):
    """Returns (permanently_attached, has_been_picked_by_player)."""
    r.s(); r.skip(4); perm = r.b(); r.i(); r.skip(11); r.skip(8); r.i(); r.s(); r.s(); r.i()
    r.skip(3); r.s(); r.b(); r.skip(8); r.i(); r.i(); r.b(); r.b(); r.f(); r.f(); r.f()
    picked = r.b()
    return perm, picked

def rstr(d, p):
    n = struct.unpack('>I', d[p:p + 4])[0]
    return d[p + 4:p + 4 + n].decode('latin1'), p + 4 + n

def entities(d):
    for m in HDR.finditer(d):
        if m.group(1)[0] != len(m.group(2)):
            continue
        p = m.end()
        try:
            tags, p = rstr(d, p)
            if len(tags) > 400 or not all(32 <= ord(c) < 127 for c in tags):
                continue
            x, y = struct.unpack('>ff', d[p:p + 8])
        except Exception:
            continue
        yield m.start(), m.group(2).decode(), tags, x, y

def scan(world):
    wands = []
    for path in sorted(glob.glob(os.path.join(world, 'entities_*.bin'))):
        wands += scan_file(path)
    return wands

def scan_file(path):
    wands = []
    if True:
        d = load(path)
        ents = list(entities(d))
        cur = None
        for idx, (off, fn, tags, x, y) in enumerate(ents):
            tl = tags.split(',')
            # segment end: next entity that is a real item (strings inside components can look like headers)
            end = next((e[0] for e in ents[idx + 1:] if {'card_action', 'wand', 'teleportable_NOT', 'item_pickup', 'item_physics'} & set(e[2].split(','))), len(d))
            if 'wand' in tl:
                cur = dict(kind='wand', file=os.path.basename(path), xml=fn.split('/')[-1], x=round(x), y=round(y),
                           name='', spells=[], always=[], picked=False)
                try:
                    r = component(d, 'AbilityComponent', off, len(d))
                    cur.update(read_ability(r))
                    ri = component(d, 'ItemComponent', r.p, len(d))
                    if ri: cur['picked'] = read_item(ri)[1]
                except Exception as e:
                    cur['error'] = str(e)
                wands.append(cur)
            elif 'card_action' in tl:
                aid, perm = '?', False
                try:
                    r = component(d, 'ItemActionComponent', off, end)
                    if r: aid = r.s()
                    ri = component(d, 'ItemComponent', off, end)
                    if ri: perm = read_item(ri)[0]
                except Exception:
                    pass
                if cur and ((x == 0 and y == 0) or (abs(cur['x'] - x) < 32 and abs(cur['y'] - y) < 32)):
                    (cur['always'] if perm else cur['spells']).append(aid)
                else:
                    wands.append(dict(kind='card', file=os.path.basename(path), xml='', x=round(x), y=round(y),
                                      name='', spells=[aid], always=[], picked=False))
                    cur = None
            elif ('potion' in tl or 'powder_stash' in fn) and 'mimic' not in fn:   # potion mimics are enemies
                contents = {}
                try:
                    r = component(d, 'MaterialInventoryComponent', off, end)
                    if r:
                        r.skip(3); [r.f() for _ in range(6)]; r.skip(2); r.i(); r.skip(2); r.i(); r.skip(1); r.skip(8)
                        n = r.i()
                        if 0 < n < 4096:
                            v = struct.unpack_from('>%dd' % n, d, r.p)
                            contents = {i: round(a) for i, a in enumerate(v) if a >= 1}
                except Exception:
                    pass
                wands.append(dict(kind='pouch' if 'powder_stash' in fn else 'flask', file=os.path.basename(path), xml=fn.split('/')[-1],
                                  x=round(x), y=round(y), name='', spells=[], always=[], picked=False, contents=contents))
                cur = None
            elif ('item_pickup' in tl or 'item_physics' in tl or 'item_shop' in tl) and \
                    (fn.startswith('data/entities/items/') or fn.endswith('boss_alchemist/key.xml')) and 'mimic' not in fn:
                # any other pickup: stones, eggs, hearts, chests, orbs, essences, nuggets...
                wands.append(dict(kind='item', file=os.path.basename(path), xml=fn, x=round(x), y=round(y),
                                  name='', spells=[], always=[], picked=False))
                cur = None
            elif 'teleportable_NOT' in tl or 'item_pickup' in tl or 'item_physics' in tl:
                cur = None
    for w in wands:
        if w['kind'] == 'wand' and 'capacity' in w:
            w['capacity'] -= len(w['always'])  # always-casts are counted in deck_capacity
    return wands

def main():
    world = sys.argv[1]
    args = [a for a in sys.argv[2:] if not a.startswith('--')]
    want = args[0].upper() if args else None
    wands = scan(world)
    if '--json' in sys.argv:
        import json
        print(json.dumps(wands))
        return
    for w in wands:
        allsp = w['spells'] + w['always']
        hit = want and want in allsp
        if want and '--only' in sys.argv and not hit:
            continue
        mark = '>>> ' if hit else '    '
        if w['kind'] == 'wand':
            st = (f"{'shuffle' if w.get('shuffle') else 'no-shuf'} cast {w.get('per_cast')} "
                  f"delay {w.get('cast_delay', 0)/60:.2f}s rech {w.get('recharge', 0)/60:.2f}s "
                  f"mana {w.get('mana_max')}/{w.get('mana_charge')} cap {w.get('capacity')} spread {w.get('spread')}")
            ac = f" [always: {', '.join(w['always'])}]" if w['always'] else ''
            print(f"{mark}({w['x']:6},{w['y']:6}) {w['name'][:28]:28s} {st}{ac}  {', '.join(w['spells'])}")
        else:
            print(f"{mark}({w['x']:6},{w['y']:6}) loose spell: {w['spells'][0]}")
    nw = sum(1 for w in wands if w['kind'] == 'wand')
    print(f"\n{nw} wands, {len(wands) - nw} loose cards")

if __name__ == '__main__':
    main()
