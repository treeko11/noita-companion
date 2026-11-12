# Noita Companion

A map of your Noita save that refreshes while you play: every wand (with stats and always-casts), loose spell, flask, pouch and item the game has loaded, with spell, item and material tracking and rarity from the Noita wiki.

## Using it

Run `dist\Noita Companion.exe`. It finds your save (`%USERPROFILE%\AppData\LocalLow\Nolla_Games_Noita\save00`) and your Steam install of Noita on its own and opens in its own window.

- The first start draws every explored area (around 20–60 seconds). Later starts take seconds.
- **Refresh** rescans now; **Auto** rescans every 10 seconds. The game writes areas to disk every few minutes while you play, so the area around you can lag behind.
- Tabs: **Found** (wands, loose spells, flasks & pouches, other items), **Spells**, **Items** and **Materials** libraries. Click any spell, item or material to track it. Click a marker, then **Show next** to step through all copies.
- The legend toggles map layers.

Its cache, settings and wiki data live in `%LOCALAPPDATA%\NoitaCompanion`. Delete that folder to start fresh.

Options (from a command prompt): `--browser` opens it in your web browser instead of a window, `--save <folder>` and `--wak <path to data.wak>` point it at a different save or install, `--export <folder>` writes a static snapshot page.

## Where the data comes from

- Spell, material and item names, icons and spawn odds: the Noita wiki (noita.wiki.gg), cached for a week and shipped inside the app for offline use.
- From the game files (`data.wak`): only the textures used to draw the map, and the material numbering the save uses for flask contents.

## Rebuilding

Run `build.bat`. It sets up a private Python environment in `.venv`, refreshes the bundled wiki data, and writes a new `dist\Noita Companion.exe`. To run from source instead: `python companion.py` (opens in your browser).

Files: `companion.py` (app and local server), `noita_wands.py` (reads items from the save), `noita_map.py` (draws the map), `noita_decomp.py` (save decompression), `wak.py` (reads `data.wak`), `wiki_data.py` (wiki data), `map_template.html` (the interface).
