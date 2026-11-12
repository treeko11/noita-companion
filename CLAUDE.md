# Noita Companion

A local app that maps a Noita save and tracks wands, spells, flasks, items and materials. **Read `HANDOVER.md` first**: it has the architecture, verified file-format facts, the user's decisions, and a numbered backlog with pointers for each item.

## Where you are running

- **Cloud session (Linux, no Noita):** there is no real save and no game install. `python companion.py` automatically uses the sample save in `fixtures/save00` and `fixtures/game_meta.json` (the map is drawn in flat colours instead of textures; everything else behaves the same). You can't build the Windows exe or publish releases here.
- **The user's PC (Windows):** the real save and Noita install are found automatically. `build.bat` builds `dist\Noita Companion.exe`.

## Setup and checks

```bash
pip install -r requirements.txt
python companion.py --no-browser --port 8799        # server only; GET /api/data, POST /api/refresh
```

- Data check: `curl -s http://127.0.0.1:8799/api/data` and inspect it with Python.
- Page script syntax: extract the last `<script>` block of `map_template.html` and run `node --check` on it.
- Visual check: `pip install websocket-client` and run `tools/screenshots.py` (needs Edge, Chrome or Chromium; see its docstring), then look at the PNGs.
- After changing `noita_wands.py` or `companion.py`, run the server against the fixtures and confirm `/api/data` still returns items of every kind (wand, card, flask, pouch, item).

## Working rules

- Spell, item and material names, icons and spawn odds come from the Noita wiki (`wiki_data.py`, cached in `wiki_cache.json`), not the game files. Rarity rules and the hand-picked spell lists live in `companion.py`; don't change them without the user asking.
- Keep `fixtures/` in sync if you change what the scanner reads: `tools/make_fixture.py` regenerates it, but only on the user's PC (it needs the real save and Noita install).
- Commit only when the user asks. In a cloud session, work on your own branch and push it (or open a pull request) so the user can test on their PC; don't push to `main` directly from the cloud.
- Releases (`gh release create`, asset name `Noita-Companion.exe`) happen from the user's PC after they've tested a build.
- Commit messages end with the `Co-Authored-By:` trailer given in your instructions.
- When a backlog item in `HANDOVER.md` is done, move it to a "Done" note there or delete it, so the next session doesn't redo it.
