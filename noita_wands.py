"""Read wands, loose spells, flasks, pouches, perks and other pickups from a save's world/entities_*.bin files.

Entity file: u32 2, schema hash string, u32 entity count, then entities. Strings are u32 big-endian length + bytes;
numbers are big-endian. Entity header: name, u8, file name, tags, then x, y, scale x, scale y, rotation (floats).
Components follow in alphabetical order (name, 2 flag bytes, tags string, then fields in schema order); children
follow their parent. Field layouts: data/schemas/c8ecfb341d22516067569b04563bff9c.xml and
data/scripts/gun/gunaction_generated.lua in the Noita install.

    python noita_wands.py <save00/world> [ACTION_ID_TO_HIGHLIGHT] [--only] [--json]
"""
import sys, os, glob, re, struct, bisect
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from noita_decomp import load

HDR = re.compile(rb'\x00\x00\x00([\x08-\xff])((?:data|mods)/[ -~]+?\.xml)')
# Entities with these tags are real items. Strings inside components can look like entity headers (a projectile
# path inside a bomb card, say), so only these mark where one item's data ends and the next begins.
ITEM_TAGS = {'card_action', 'wand', 'teleportable_NOT', 'item_pickup', 'item_physics'}
PERK_ID = struct.pack('>I', 7) + b'perk_id'


class Reader:
    def __init__(self, d, p): self.d, self.p = d, p
    def int(self): v = struct.unpack_from('>i', self.d, self.p)[0]; self.p += 4; return v
    def float(self): v = struct.unpack_from('>f', self.d, self.p)[0]; self.p += 4; return v
    def bool(self): v = self.d[self.p]; self.p += 1; return bool(v)
    def str(self):
        n = struct.unpack_from('>I', self.d, self.p)[0]
        if n > 4096: raise ValueError('bad string')
        v = self.d[self.p + 4:self.p + 4 + n].decode('latin1'); self.p += 4 + n; return v
    def skip(self, n): self.p += n


def read_str(d, p):
    n = struct.unpack_from('>I', d, p)[0]
    return d[p + 4:p + 4 + n].decode('latin1'), p + 4 + n


def component(d, name, start, end):
    """Reader at the first field of component `name` within d[start:end], or None."""
    key = struct.pack('>I', len(name)) + name.encode()
    k = d.find(key, start, end)
    if k < 0: return None
    r = Reader(d, k + len(key))
    r.skip(2)       # enabled flags
    r.str()         # component tags
    return r


def read_ability(r):
    """AbilityComponent -> wand stats. deck_capacity still includes always-casts here."""
    I, F, B, S = r.int, r.float, r.bool, r.str
    I(); S(); S(); I(); B(); I()                         # cooldown, entity_file, sprite_file, entity_count, never_reload, reload_time_frames
    mana, mana_max, charge = F(), F(), F()
    B(); F(); F(); B(); F(); I(); I()                    # rotate, fast_projectile, swim, max_charged, charge_wait
    F(); F(); F(); F(); S(); B(); B()                    # recoil x4, base_item_file, use_entity_file, click_to_use
    I(); I(); B(); B(); B(); I(); I(); B()
    ui_name = S(); B(); B()                              # use_gun_script, is_petris_gun
    per_cast, shuffle, recharge, capacity = I(), B(), I(), I()     # ConfigGun
    for _ in range(5): S()                               # action_id .. unidentified sprite
    I(); S(); S(); S(); B(); I(); S()                    # type, spawn level/prob/flag, manual_unlock, max_uses, custom_xml
    F(); B(); I(); B(); B(); B(); I(); B(); B()
    delay, speed = I(), F(); F(); F(); F(); spread = F()  # ConfigGunActionInfo: fire_rate_wait .. spread_degrees
    return dict(name=ui_name, mana=round(mana), mana_max=round(mana_max), mana_charge=round(charge),
                per_cast=per_cast, shuffle=shuffle, recharge=recharge, capacity=capacity, cast_delay=delay,
                spread=round(spread, 1), speed=round(speed, 2))


def read_item(r):
    """ItemComponent -> (permanently_attached, has_been_picked_by_player)."""
    r.str(); r.skip(4); perm = r.bool(); r.int(); r.skip(11); r.skip(8); r.int(); r.str(); r.str(); r.int()
    r.skip(3); r.str(); r.bool(); r.skip(8); r.int(); r.int(); r.bool(); r.bool(); r.float(); r.float(); r.float()
    return perm, r.bool()


def read_cost(d, start, end):
    """Shop price (ItemCostComponent: int64 cost, bool stealable) inside d[start:end], or None."""
    r = component(d, 'ItemCostComponent', start, end)
    if not r: return None
    try: return struct.unpack_from('>q', d, r.p)[0]
    except struct.error: return None


def read_perk_id(d, start, end):
    """Perk entities keep their perk in a VariableStorageComponent named perk_id; the value string follows the name."""
    k = d.find(PERK_ID, start, end)
    if k < 0: return None
    try:
        v = read_str(d, k + len(PERK_ID))[0]
        return v if re.fullmatch(r'[A-Z0-9_]+', v) else None
    except Exception:
        return None


def read_contents(d, start, end):
    """MaterialInventoryComponent -> {material number: amount}. The numbers follow the game's material order."""
    r = component(d, 'MaterialInventoryComponent', start, end)
    if not r: return {}
    try:
        r.skip(3); [r.float() for _ in range(6)]; r.skip(2); r.int(); r.skip(2); r.int(); r.skip(1)
        r.skip(8)                                        # max_capacity (double)
        n = r.int()
        if not 0 < n < 4096: return {}
        return {i: round(a) for i, a in enumerate(struct.unpack_from('>%dd' % n, d, r.p)) if a >= 1}
    except Exception:
        return {}


def entities(d):
    """(offset, file name, tags, x, y) for everything that looks like an entity header."""
    for m in HDR.finditer(d):
        if m.group(1)[0] != len(m.group(2)): continue
        try:
            tags, p = read_str(d, m.end())
            if len(tags) > 400 or not all(32 <= ord(c) < 127 for c in tags): continue
            x, y = struct.unpack_from('>ff', d, p)
        except Exception:
            continue
        yield m.start(), m.group(2).decode(), tags, x, y


def scan_file(path):
    """Every wand, loose spell, flask/pouch, perk and other pickup in one entity file."""
    d = load(path)
    ents = list(entities(d))
    starts = [e[0] for e in ents if ITEM_TAGS & set(e[2].split(','))]
    src = os.path.basename(path)
    found, wand = [], None                               # wand: the last wand, which following cards belong to

    def add(kind, fn, x, y, **extra):
        it = dict(kind=kind, file=src, xml=fn, x=round(x), y=round(y), name='', spells=[], always=[], picked=False)
        it.update(extra)
        found.append(it)
        return it

    for off, fn, tags, x, y in ents:
        tl = tags.split(',')
        i = bisect.bisect_right(starts, off)
        end = starts[i] if i < len(starts) else len(d)  # this item's data ends where the next item starts
        short = fn.split('/')[-1]
        if 'wand' in tl:
            wand = add('wand', short, x, y)
            try:
                r = component(d, 'AbilityComponent', off, end)
                wand.update(read_ability(r))
                ri = component(d, 'ItemComponent', r.p, end)
                if ri: wand['picked'] = read_item(ri)[1]
            except Exception as e:
                wand['error'] = str(e)
            cost = read_cost(d, off, end)
            if cost is not None: wand['cost'] = cost
        elif 'card_action' in tl:
            aid, perm = '?', False
            try:
                r = component(d, 'ItemActionComponent', off, end)
                if r: aid = r.str()
                ri = component(d, 'ItemComponent', off, end)
                if ri: perm = read_item(ri)[0]
            except Exception:
                pass
            # child card positions are unreliable (0,0 or offset), so a card at (0,0) or near the wand is the wand's
            if wand and ((x == 0 and y == 0) or (abs(wand['x'] - x) < 32 and abs(wand['y'] - y) < 32)):
                (wand['always'] if perm else wand['spells']).append(aid)
            else:
                card = add('card', '', x, y, spells=[aid])
                cost = read_cost(d, off, end)
                if cost is not None: card['cost'] = cost
                wand = None
        elif 'item_perk' in tl:
            add('perk', short, x, y, perk=read_perk_id(d, off, end))
            wand = None
        elif ('potion' in tl or 'powder_stash' in fn) and 'mimic' not in fn:       # potion mimics are enemies
            add('pouch' if 'powder_stash' in fn else 'flask', short, x, y, contents=read_contents(d, off, end))
            wand = None
        elif ({'item_pickup', 'item_physics', 'item_shop'} & set(tl)) and 'mimic' not in fn and \
                (fn.startswith('data/entities/items/') or fn.endswith('boss_alchemist/key.xml')):
            # any other pickup: stones, eggs, hearts, chests, orbs, essences, nuggets...
            it = add('item', fn, x, y)
            cost = read_cost(d, off, end)
            if cost is not None: it['cost'] = cost
            wand = None
        elif ITEM_TAGS & set(tl):
            wand = None
    for w in found:
        if w['kind'] == 'wand' and 'capacity' in w:
            w['capacity'] -= len(w['always'])               # deck_capacity counts always-casts
    return found


def scan(world):
    return [it for p in sorted(glob.glob(os.path.join(world, 'entities_*.bin'))) for it in scan_file(p)]


def main():
    world = sys.argv[1]
    args = [a for a in sys.argv[2:] if not a.startswith('--')]
    want = args[0].upper() if args else None
    found = scan(world)
    if '--json' in sys.argv:
        import json
        print(json.dumps(found))
        return
    for w in found:
        if w['kind'] not in ('wand', 'card'): continue
        hit = want and want in w['spells'] + w['always']
        if want and '--only' in sys.argv and not hit: continue
        mark = '>>> ' if hit else '    '
        if w['kind'] == 'wand':
            st = (f"{'shuffle' if w.get('shuffle') else 'no-shuf'} cast {w.get('per_cast')} "
                  f"delay {w.get('cast_delay', 0) / 60:.2f}s rech {w.get('recharge', 0) / 60:.2f}s "
                  f"mana {w.get('mana_max')}/{w.get('mana_charge')} cap {w.get('capacity')} spread {w.get('spread')}")
            ac = f" [always: {', '.join(w['always'])}]" if w['always'] else ''
            print(f"{mark}({w['x']:6},{w['y']:6}) {w['name'][:28]:28s} {st}{ac}  {', '.join(w['spells'])}")
        else:
            print(f"{mark}({w['x']:6},{w['y']:6}) loose spell: {w['spells'][0]}")
    kinds = {}
    for w in found: kinds[w['kind']] = kinds.get(w['kind'], 0) + 1
    print('\n' + ', '.join(f'{n} {k}' for k, n in sorted(kinds.items())))


if __name__ == '__main__':
    main()
