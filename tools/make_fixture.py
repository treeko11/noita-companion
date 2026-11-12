"""Build the sample data that lets the app run where Noita isn't installed (cloud sessions, CI).

    python tools/make_fixture.py            # uses the local save and Noita install

Writes:
  fixtures/save00/        a slice of the save: the start area, first Holy Mountains (x -3584..1536, y -512..3072),
                          plus player.xml, world_state.xml, mod_config.xml and the persistent spell flags
  fixtures/game_meta.json material names in the game's numbering and one average colour each, so the map can be
                          drawn with flat colours and flask contents decoded without data.wak
"""
import os, sys, re, glob, json, shutil
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import companion, noita_map, noita_wands
from wak import Wak

X0, X1, Y0, Y1 = -3584, 1536, -512, 3072
OUT = os.path.join(HERE, 'fixtures')

def main():
    save, wakp = companion.find_save(), companion.find_wak()
    if not wakp: sys.exit('Noita install not found')
    dst = os.path.join(OUT, 'save00')
    if os.path.isdir(dst): shutil.rmtree(dst)
    os.makedirs(os.path.join(dst, 'world')); os.makedirs(os.path.join(dst, 'persistent', 'flags'))
    world = os.path.join(save, 'world')
    n_chunks = n_ents = 0
    for p in glob.glob(os.path.join(world, 'world_*.png_petri')):
        x, y = map(int, re.search(r'world_(-?\d+)_(-?\d+)', p).groups())
        if X0 <= x < X1 and Y0 <= y < Y1:
            shutil.copy2(p, os.path.join(dst, 'world')); n_chunks += 1
    for p in glob.glob(os.path.join(world, 'entities_*.bin')):
        if any(X0 <= i['x'] < X1 and Y0 <= i['y'] < Y1 for i in noita_wands.scan_file(p)):
            shutil.copy2(p, os.path.join(dst, 'world')); n_ents += 1
    for f in ('player.xml', 'world_state.xml', 'mod_config.xml'):
        if os.path.exists(os.path.join(save, f)): shutil.copy2(os.path.join(save, f), dst)
    for f in os.listdir(os.path.join(save, 'persistent', 'flags')):
        if f.startswith(('action_', 'card_unlocked_', 'perk_')):
            open(os.path.join(dst, 'persistent', 'flags', f), 'w').close()

    wak = Wak(wakp)
    order, base = companion.material_order(wak)
    mats = noita_map.material_table(wak)
    colors = {}
    for k, arr in mats.items():
        px = arr.reshape(-1, 4)
        px = px[px[:, 3] > 0] if (px[:, 3] > 0).any() else px
        colors[k] = [int(c) for c in px.mean(0)]
    json.dump(dict(material_order=order, material_base=base, colors=colors), open(os.path.join(OUT, 'game_meta.json'), 'w'))
    print(f'{n_chunks} map areas, {n_ents} item files, {len(order)} materials -> {OUT}')

if __name__ == '__main__':
    main()
