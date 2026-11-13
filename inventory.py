"""The player's inventory and the run state, from save00/player.xml and save00/world_state.xml.

Both files are plain XML. They are not rewritten while you play (observed: they were days old during play and change on
save-and-quit), so everything here is "as of" their file time.

    python inventory.py [save00]      # print what was read
"""
import os, re, sys, xml.etree.ElementTree as ET

HP_SCALE = 25          # the game shows hp × 25


def _num(v, default=0.0):
    try: return float(v)
    except (TypeError, ValueError): return default


def _clean_name(s, fallback):
    """UI names that are translation keys ("$item_potion") are not useful; use the fallback."""
    s = (s or '').strip()
    return fallback if not s or s.startswith('$') else s


def read_wand(e):
    ab = e.find('AbilityComponent')
    g, ga = ab.find('gun_config'), ab.find('gunaction_config')
    g = g.attrib if g is not None else {}
    ga = ga.attrib if ga is not None else {}
    cards = []
    for c in e.findall('Entity'):
        a, ic = c.find('ItemActionComponent'), c.find('ItemComponent')
        if a is None or not a.get('action_id'): continue
        slot = int(_num(ic.get('inventory_slot.x'))) if ic is not None else 0
        perm = ic is not None and ic.get('permanently_attached') == '1'
        cards.append((perm, slot, a.get('action_id')))
    always = [aid for perm, _, aid in cards if perm]
    spells = [aid for perm, _, aid in sorted(cards, key=lambda c: c[1]) if not perm]
    return dict(kind='wand', name=_clean_name(ab.get('ui_name'), 'Wand'),
                mana_max=round(_num(ab.get('mana_max'))), mana_charge=round(_num(ab.get('mana_charge_speed'))),
                per_cast=int(_num(g.get('actions_per_round'), 1)), shuffle=g.get('shuffle_deck_when_empty') == '1',
                recharge=int(_num(g.get('reload_time'))), capacity=int(_num(g.get('deck_capacity'))) - len(always),
                cast_delay=int(_num(ga.get('fire_rate_wait'))), spread=round(_num(ga.get('spread_degrees')), 1),
                spells=spells, always=always)


def read_container(e):
    mi = e.find('MaterialInventoryComponent')
    cm = mi.find('count_per_material_type') if mi is not None else None
    contents = [] if cm is None else [(m.get('material'), int(_num(m.get('count')))) for m in cm.findall('Material')]
    contents = sorted([c for c in contents if c[0] and c[1] > 0], key=lambda c: -c[1])
    # pouches carry the powder_stash tag (data/entities/items/pickup/powder_stash.xml)
    pouch = 'powder_stash' in (e.get('tags') or '').split(',')
    return dict(kind='pouch' if pouch else 'flask', contents=contents, fill=sum(n for _, n in contents))


def _globals(wsc):
    lg = wsc.find('lua_globals') if wsc is not None else None
    return {} if lg is None else {E.get('key'): E.get('value') for E in lg.findall('E')}


def perks_from(globs):
    """{PERK_ID: times picked} from the PERK_PICKED_<ID>_PICKUP_COUNT globals."""
    out = {}
    for k, v in globs.items():
        m = re.match(r'PERK_PICKED_(.+)_PICKUP_COUNT$', k or '')
        if m and _num(v) > 0: out[m.group(1)] = int(_num(v))
    return out


def read_player(path):
    """Wands, spells, flasks/pouches and other items the player carries, plus hp and gold."""
    root = ET.parse(path).getroot()
    out = dict(wands=[], spells=[], containers=[], items=[], hp=None, max_hp=None, gold=None, perk_icons=[])
    dm, wallet = root.find('DamageModelComponent'), root.find('WalletComponent')
    if dm is not None:
        out['hp'] = round(_num(dm.get('hp')) * HP_SCALE); out['max_hp'] = round(_num(dm.get('max_hp')) * HP_SCALE)
    if wallet is not None: out['gold'] = int(_num(wallet.get('money')))
    for inv in root.findall('Entity'):
        name = inv.get('name')
        if name == 'inventory_quick':
            for e in inv.findall('Entity'):
                tags = (e.get('tags') or '').split(',')
                if 'wand' in tags and e.find('AbilityComponent') is not None:
                    out['wands'].append(read_wand(e))
                elif e.find('MaterialInventoryComponent') is not None:
                    out['containers'].append(read_container(e))
                else:
                    ic = e.find('ItemComponent')
                    out['items'].append(_clean_name(ic.get('item_name') if ic is not None else '', 'Item'))
        elif name == 'inventory_full':
            for e in inv.findall('Entity'):
                a, ic = e.find('ItemActionComponent'), e.find('ItemComponent')
                if a is not None and a.get('action_id'):
                    slot = int(_num(ic.get('inventory_slot.x'))) if ic is not None else 0
                    out['spells'].append((slot, a.get('action_id')))
        elif 'perk_entity' in (inv.get('tags') or ''):
            u = inv.find('UIIconComponent')
            if u is not None and u.get('is_perk') == '1' and (u.get('name') or '').startswith('$perk_'):
                out['perk_icons'].append(u.get('name')[6:].upper())
    out['spells'] = [aid for _, aid in sorted(out['spells'])]
    return out


def read_world_state(path, save=None):
    """Perks picked, orbs, fungal shifts and Holy Mountain flags."""
    root = ET.parse(path).getroot()
    wsc = root.find('WorldStateComponent')
    g = _globals(wsc)
    orbs_run = []
    ofr = wsc.find('orbs_found_thisrun') if wsc is not None else None
    for p in (ofr if ofr is not None else []):
        try: orbs_run.append(int((p.text or '').strip()))
        except ValueError: pass
    # fungal shifts: <changed_materials> is a list of strings (schema: vector<string>). Read as from/to pairs;
    # UNVERIFIED: no save with a shift has been seen yet.
    cm = wsc.find('changed_materials') if wsc is not None else None
    strs = [(s.text or '').strip() for s in (cm if cm is not None else [])]
    shifts = [[a, b] for a, b in zip(strs[0::2], strs[1::2]) if a and b]
    # the game's orb map: ORB_MAP_STRING is "x,y x,y ..." in 512-pixel chunks (orb_list in data/scripts/lib/utilities.lua).
    # Which orb id sits at which entry is not known.
    rooms = []
    for word in (g.get('ORB_MAP_STRING') or '').split():
        try:
            a, b = (int(v) for v in word.split(',')[:2]); rooms.append([a * 512 + 256, b * 512 + 256])
        except ValueError: pass
    # TEMPLE_LEAKED_ / TEMPLE_COLLAPSED_ are keyed "<w>_<depth in chunks>" (temple_pos_to_id in temple_shared.lua), where
    # w = floor((x + 16384) / 32768): a 32768-px column, not a parallel world (worlds are 35840 px wide).
    # LEAKED is set when a mountain's material checker fails, i.e. the gods were angered (temple_check_for_leaks.lua).
    def temples(prefix):
        out = []
        for k, v in g.items():
            m = re.match(prefix + r'(-?\d+)_(-?\d+)$', k or '')
            if m and v == '1': out.append([int(m.group(1)), int(m.group(2)) * 512])
        return sorted(out)
    # Collapse (data/scripts/buildings/workshop_exit.lua) sets TEMPLE_COLLAPSED_<id> = 1 and TEMPLE_ACTIVE_<x>_<y> = 0;
    # entering a mountain sets TEMPLE_ACTIVE back to 1 (temple_enter.lua). Saves seen so far keep only the
    # TEMPLE_ACTIVE one, which also has the exact position, so both are read.
    active = []
    for k, v in g.items():
        m = re.match(r'TEMPLE_ACTIVE_(-?\d+)_(-?\d+)$', k or '')
        if m: active.append([int(m.group(1)), int(m.group(2)), v == '1'])
    pos_id = lambda x, y: [(x + 32 * 512) // (64 * 512), (y // 512) * 512]     # temple_pos_to_id
    collapsed = [dict(x=x, y=y) for x, y, on in sorted(active) if not on]
    known = [pos_id(c['x'], c['y']) for c in collapsed]
    collapsed += [dict(x=None, y=y, x_near=w * 32768) for w, y in temples('TEMPLE_COLLAPSED_') if [w, y] not in known]
    # persistent/orbs_new/<id>: one file per orb id. No game script reads it (the engine does), so "orbs found in any run"
    # is inferred from the name and contents, not confirmed.
    orbs_ever = []
    if save:
        od = os.path.join(save, 'persistent', 'orbs_new')
        if os.path.isdir(od): orbs_ever = sorted(int(n) for n in os.listdir(od) if n.isdigit())
    day = wsc.get('day_count') if wsc is not None else None
    # HOLY_MOUNTAIN_VISITS goes up only on reaching a Holy Mountain (or victory room) deeper than the deepest so far
    # (player_biome_check.lua): mountains at the same depth in other worlds don't count.
    return dict(perks=perks_from(g), orbs_run=sorted(orbs_run), orbs_ever=orbs_ever, orb_rooms=rooms, shifts=shifts,
                temples_visited=int(_num(g.get('HOLY_MOUNTAIN_VISITS'))), temples_leaked=temples('TEMPLE_LEAKED_'),
                temples_collapsed=collapsed, day=int(_num(day)) if day else None)


def read(save):
    """Everything above, with the file times. Missing files give None for their part."""
    out = dict(player=None, world=None, player_time=None, world_time=None)
    p, w = os.path.join(save, 'player.xml'), os.path.join(save, 'world_state.xml')
    if os.path.exists(p):
        try: out['player'] = read_player(p); out['player_time'] = os.path.getmtime(p)
        except Exception as ex: print('player.xml failed:', ex)
    if os.path.exists(w):
        try: out['world'] = read_world_state(w, save); out['world_time'] = os.path.getmtime(w)
        except Exception as ex: print('world_state.xml failed:', ex)
    return out


if __name__ == '__main__':
    import json
    save = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ.get('USERPROFILE', '~'), 'AppData', 'LocalLow', 'Nolla_Games_Noita', 'save00')
    print(json.dumps(read(save), indent=1))
