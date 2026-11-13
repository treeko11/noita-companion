# Noita Companion

A map of your Noita save that refreshes while you play: every wand (with stats and always-casts), loose spell, flask, pouch and item the game has loaded, with spell, item and material tracking and rarity from the Noita wiki.

## Features

**The map**
- Draws every area the game has saved, including parallel worlds, from your save's own world files
- Markers for every wand, loose spell, flask, pouch, item, perk and the game's orb-map locations, each with its own layer you can switch off
- Pan, zoom and hover for details; world coordinates under the cursor
- Zoom past 1× for full detail: the saved terrain at one world pixel per pixel, drawn on demand and cached (physics objects such as boulders aren't drawn yet)

![The whole explored world, with every wand the game has loaded and two tracked items](screenshots/overview.png)

**Wands**
- Full stats: spells per cast, cast delay, recharge, capacity, max mana, mana charge, spread, shuffle
- Always-cast spells listed separately from the rest
- Filter by non-shuffle, always-casts, rare spells, spells you've never picked up, or wands you've never held; sort by any stat

![A wand's stats, always-cast and spells, with a rare spell's odds](screenshots/wand.png)

**Spells**
- Library of all 422 spells, grouped by type, with how many of each are on the map
- Spawn odds per spell roll from the wiki's tier weights; spells under 0.3% are marked rare/notable, along with a hand-picked list of standouts like Add Mana, Divide By 10 and Nolla (and a few low-odds spells that aren't worth chasing, like the random-spell and trail modifiers, are left out)
- Highlights spells you've never picked up in any run, read from your save's progress

![The spell library sorted rarest first](screenshots/spells.png)

**Flasks and pouches**
- Contents of every flask and pouch, with how full it is
- Odds from the wiki's flask and pouch tables: anything under 1% is rare (Healthium is 0.0383%), and anything outside the normal spawn pool, like a flask of gold, is flagged as unusual

![A rare Diminution flask among every flask and pouch](screenshots/flasks.png)

**Items**
- Every item from the wiki's item list, with its icon, description, effect and odds from pedestals and chests
- Hearts, chests, Spell Refreshers, essences and other pickups too

![Tracking Kiuaskivi: 8 found, stepping through them with Show next](screenshots/items.png)

**Materials**
- Every material in the loaded world and inside flasks, rarest first, with pixel counts
- Marks every spot a material appears on the map

![Tracking Lively Concoction across the world](screenshots/materials.png)

**Perks and shops**
- Every perk the game has saved in the world (nearly all on Holy Mountain altars), with its name, icon and description from the wiki; a short starter list of standout perks (Extra Perk, Unlimited Spells, the immunities…) is marked notable
- Shop prices on wands, spells and items; filter to what’s for sale or sort by price
- Collapsed Holy Mountains are marked on the map

**Your run**
- The **Run** tab shows what you carry: wands with full stats, spare spells, flasks, health and gold, plus your perks, orbs, fungal shifts and Holy Mountain progress
- Spells you carry get a small blue mark everywhere, and **Hide carried** drops loose spells (and wands) you already have
- Noita doesn't rewrite these files while you play, so the tab shows when they were last written (save and quit to update)

**Tracking**
- Click any spell, item or material to track it: everything containing it gets a pink ring and a count in the tracking bar
- **Show next** and **Previous** step through every copy, centring the map on each one
- Your tracking choices are remembered between runs

**While you play**
- **Refresh** rescans the save in under a second when nothing has changed; only areas that changed are redrawn
- **Auto** rescans every 10 seconds and pops up a notice when a tracked or rare thing appears
- Works offline: wiki data and icons ship with the app and refresh weekly when you're online

## Download

**[Download Noita-Companion.exe](https://github.com/treeko11/noita-companion/releases/latest/download/Noita-Companion.exe)** (Windows 10/11), or see [all releases](https://github.com/treeko11/noita-companion/releases).

Needs Noita installed through Steam and the Microsoft Edge WebView2 runtime (already on most PCs). The exe isn't code-signed, so Windows SmartScreen may say "Windows protected your PC": choose **More info → Run anyway**.

## Using it

Run `Noita-Companion.exe` (or `dist\Noita Companion.exe` if you built it yourself). It finds your save (`%USERPROFILE%\AppData\LocalLow\Nolla_Games_Noita\save00`) and your Steam install of Noita on its own and opens in its own window.

- The first start draws every explored area (around 20–60 seconds). Later starts take seconds.
- **Refresh** rescans now; **Auto** rescans every 10 seconds. The game writes areas to disk every few minutes while you play, so the area around you can lag behind.
- Tabs: **Found** (wands, loose spells, flasks & pouches, other items, perks), **Spells**, **Items** and **Materials** libraries, and **Run** (your inventory and run state as of your last save-and-quit). Click any spell, item or material to track it. Click a marker, then **Show next** to step through all copies.
- The legend toggles map layers.

Its cache, settings and wiki data live in `%LOCALAPPDATA%\NoitaCompanion`. Delete that folder to start fresh.

Options (from a command prompt): `--browser` opens it in your web browser instead of a window, `--save <folder>` and `--wak <path to data.wak>` point it at a different save or install, `--export <folder>` writes a static snapshot page.

## Where the data comes from

- Spell, material, item and perk names, icons and spawn odds: the Noita wiki (noita.wiki.gg), cached for a week and shipped inside the app for offline use.
- Your inventory, perks, orbs and fungal shifts: the save's `player.xml` and `world_state.xml`.
- From the game files (`data.wak`): only the textures used to draw the map, and the material numbering the save uses for flask contents.

## Credits

- Spell, item, material and perk data and descriptions (`wiki_cache.json`) come from [the Noita Wiki](https://noita.wiki.gg/), available under [CC BY-NC-SA 3.0](https://creativecommons.org/licenses/by-nc-sa/3.0/).
- The icons in `seed_icons/` are Noita's own sprites, © Nolla Games, fetched from the wiki.
- This is an unofficial fan tool, not affiliated with Nolla Games.

## Rebuilding

Run `build.bat`. It sets up a private Python environment in `.venv`, refreshes the bundled wiki data, and writes a new `dist\Noita Companion.exe`. To run from source instead: `python companion.py` (opens in your browser).

Files: `companion.py` (app and local server), `noita_wands.py` (reads items from the save), `noita_map.py` (draws the map), `noita_decomp.py` (save decompression), `wak.py` (reads `data.wak`), `wiki_data.py` (wiki data), `map_template.html` (the interface).
