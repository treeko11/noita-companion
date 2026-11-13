# Handover: Noita Companion

This file is for the next agent picking up work on this repo. Read **Project context** once, then jump to a numbered backlog item. Every item lists what is already verified, where the code goes, and how to check it, so you can start without re-exploring.

Status as of 2026-11-13: v1.1.0 released. Repo: `github.com/treeko11/noita-companion` (public), branch `main`.

---

## Project context

### What it is
A local Windows app that reads a Noita save (`%USERPROFILE%\AppData\LocalLow\Nolla_Games_Noita\save00`) and shows, on a pannable map, every wand, loose spell, flask, pouch and pickup item the game has written to disk, with tracking and rarity. It runs a small HTTP server (`companion.py`) and shows `map_template.html` in a pywebview window (packaged exe) or the browser (from source).

### Files

| File | Role |
|---|---|
| `companion.py` | App: rarity rules, wiki → app tables (`load_spells`, `load_materials`, `item_catalog`), tile building (`process_chunk`, runs in worker processes), `State` (`refresh()` = `_scan_world` + `_scan_entities` + `_scan_run`; `data()` builds the page JSON from `_items`, `_add_world_materials`, `_inventory`, `_run_state`), HTTP API, export, `main()` |
| `noita_wands.py` | Parses `world/entities_*.bin`: wands (`read_ability`), cards, flasks/pouches (MaterialInventoryComponent), Holy Mountain perk altars (`read_perk_id`), shop prices (`read_cost`), other items. `scan_file(path)` → list of dicts |
| `inventory.py` | Parses `player.xml` (carried wands, spells, flasks, hp, gold) and `world_state.xml` (perks picked, orbs, fungal shifts, Holy Mountain flags). `read(save)`; `python inventory.py` prints it |
| `noita_map.py` | Map chunks: `read_chunk` (cells, material names, custom colours), `render_cells` (RGBA with textures from `material_table`), `on_sky` (opaque, over the background colour) |
| `noita_decomp.py` | FastLZ decompression (levels 1 and 2) + `load(path)` for the save's `.bin`/`.png_petri` files. Copies use slices, about 3.5× faster than byte-by-byte |
| `wak.py` | Reads `data.wak` (game data archive; plain index, no encryption) |
| `wiki_data.py` | Fetches spell/material/item data and odds from noita.wiki.gg and caches `wiki_cache.json` (7 days) |
| `map_template.html` | The whole UI (single file, vanilla JS + canvas). `/*DATA*/null` is replaced with JSON only for static `--export`; live mode fetches `/api/data`. Found-tab filters live in one `FILTERS` table (test + which kinds it applies to; chips hide themselves); sorting per kind in `SORTS`; DOM built with `el()` / `tag()` |
| `build.bat` | Recreates `.venv`, refreshes wiki data + `seed_icons/`, runs PyInstaller → `dist\Noita Companion.exe` |
| `tools/screenshots.py`, `tools/scenes.json` | Headless Edge/Chrome screenshot tool (used for the README images and for visual checks) |
| `tools/make_fixture.py`, `fixtures/` | Sample save + material table so the app runs without Noita (cloud sessions) |
| `CLAUDE.md` | Short instructions every Claude session loads; points here |
| `seed_icons/`, `wiki_cache.json` | Bundled into the exe so the first run works offline |

### Running and checking
- **Without Noita (cloud sessions)**: `find_save()` falls back to `fixtures/save00` (a slice of the user's save: the start area and first Holy Mountains, x -3584..1536, y -512..3072: 61 map areas, 24 wands, 55 loose spells, 7 flasks, 2 pouches, 14 items, plus `player.xml` and `world_state.xml`). `game_tables()` falls back to `fixtures/game_meta.json` (material numbering + one colour per material), so the map draws in flat colours. Regenerate both on the user's PC with `python tools/make_fixture.py`.
- From source: `python companion.py` (browser) or `python companion.py --no-browser --port 8799` (server only). Source mode keeps its tile cache in `./cache` (git-ignored).
- API: `GET /api/data` (503 + `{status}` while the first scan runs), `POST /api/refresh`, `GET /tile/<cx>_<cy>.png` (¼ scale), `GET /tile/full/<cx>_<cy>.png` (1:1, drawn on first request, cached in `tiles_full/`, oldest pruned past 800 files, deleted when the chunk changes), `GET /icon/<name>`.
- Quick data check: `curl -s http://127.0.0.1:8799/api/data > d.json` and inspect with Python.
- Visual check: `tools/screenshots.py` with your own scenes file (see its docstring). The page exposes its globals (`items`, `S` spells, `M` materials, `C` item catalog, `goTo`, `flyTo`, `render`, `showTip`, `toggleItem`...).
- Syntax check of the page script: extract the last `<script>` block to a file and run `node --check` on it.
- Packaged app: `build.bat`, then `"dist\Noita Companion.exe" --no-browser --port 8791` to test headless. The exe keeps data in `%LOCALAPPDATA%\NoitaCompanion`.

### Release process (what the user expects)
1. Commit and push only when the user asks (they have been asking each time).
2. Rebuild with `build.bat` (or the same PyInstaller command it contains).
3. `gh release create vX.Y.Z "dist/Noita-Companion.exe" --title "Noita Companion X.Y.Z" --notes-file notes.md --target main`. Copy the exe to `dist/Noita-Companion.exe` first: the README links `releases/latest/download/Noita-Companion.exe`, so **the asset name must stay `Noita-Companion.exe`**.
4. `dist/` is git-ignored; never commit the exe.
5. Commit messages end with a `Co-Authored-By:` trailer.

### User preferences and decisions (keep these)
- **Spell, material and item names, icons and odds come from the Noita wiki**, not the game files. The game's `data.wak` is used only for map textures and the material numbering in flask contents (the wiki has no numbering).
- **Rarity rules**: spells are rare/notable under 0.3% per spell roll at their best tier (tiers 0–6, weights normalised per tier over all wiki spells), plus the hand-picked `NOTABLE_SPELLS`, minus `NOT_NOTABLE_SPELLS` (both in `companion.py`). Flask and pouch contents count as rare under 1% of that container type. Anything outside the regular pool is "Unusual". Gold, silver, brass and diamond are only notable **inside a flask**, never as loose pixels. Loose-pixel materials use `RARE_WORLD` (hand-picked). Items count as rare when every wiki source is under 1%.
- UI labels for spell rarity say **"Rare/notable"**; items, flasks and materials still say "Rare".
- No GitHub Pages (it can't run the app). Distribution is GitHub Releases only.
- The user plays with mods; the multiplayer mod folders exist in the save (`evaisa.mp_*`). Don't assume a vanilla install.

### Verified file-format facts (save you hours)
- **Compressed save files** (`entities_*.bin`, `world_*.png_petri`): 8-byte header `<u32 compressed size><u32 raw size>` (little-endian), then FastLZ. The level is in the first byte's top bits (`>>5`: 0 → level 1, 1 → level 2). Both levels occur. If compressed == raw size, the data is stored uncompressed.
- **Entity files**: `u32 2`, string schema hash (`c8ecfb341d22516067569b04563bff9c`; the matching field list is `Noita/data/schemas/<hash>.xml` in the install), `u32 entity count`, then entities. Strings are `u32 BE length + bytes`; numbers are big-endian. Entity header: name, `u8`, filename, tags, then 5 floats (x, y, scale x, scale y, rotation). Components are alphabetical; each is name, 2 flag bytes, tags string, then fields in schema order. Children follow their parent. Child card positions are unreliable (0,0 or offset), so cards are attached to the preceding wand when within 32 px or at (0,0).
- `noita_wands.entities()` finds entity headers by regex. **Strings inside components can look like headers** (for example a projectile path inside a bomb card), so segment boundaries only use "real" entities: tags with `card_action`, `wand`, `teleportable_NOT`, `item_pickup` or `item_physics`.
- **AbilityComponent → wand stats**: see `read_ability`. ConfigGun is `actions_per_round int, shuffle bool, reload_time int, deck_capacity int`. ConfigGunActionInfo field order is in `data/scripts/gun/gunaction_generated.lua` (`ConfigGunActionInfo_Init`); `fire_rate_wait` is cast delay. `deck_capacity` **includes** always-casts; the app subtracts them.
- **ItemComponent**: `permanently_attached` (always-cast) and `has_been_picked_by_player` are read in `read_item`.
- **MaterialInventoryComponent** (flasks): after the fixed fields comes `max_capacity` (double), then `u32 n` and `n` doubles indexed by material number. n is 466 with the user's mods.
- **Material numbering** (`companion.material_order`): every `<CellData>` in `data/materials.xml` order, then every `<CellDataChild>`, comments stripped, duplicate names dropped. Verified on 50 of 52 world flasks against the flask/pouch spawn tables. **Numbers ≥ the CellData count are unverified** (see backlog R1).
- **World chunks** (`world_X_Y.png_petri`, 512×512 world pixels each): `u32 24, u32 512, u32 512`, then 512×512 bytes (low 7 bits = index into this chunk's material list, high bit = custom colour), then `u32 n` + n material names, then `u32 n` + n ARGB custom colours (consumed in order by high-bit pixels). Physics objects follow and are **not parsed yet** (backlog 8).
- **World geometry**: one world is 35,840 px wide (`WORLD_W`); parallel worlds repeat every 35,840 px east and west. The user has visited ones near x = 36,000, 71,000 and 107,000.
- **Persistent progress**: `save00/persistent/flags/action_<id>` (lowercase) exists for every spell ever picked up. That drives "never picked up". `card_unlocked_*` flags are unlocks.
- **Perks in the world**: entity `data/entities/items/pickup/perk.xml`, tags `teleportable_NOT,perk,item_perk`. Not all are on Holy Mountain altars (the user's save has Leggy Mutation at (-2730, 2022) and Moon Radar at (16120, 3328)); collapsed mountains keep theirs. The id follows the string `perk_id` in its VariableStorageComponent (`read_perk_id`). Perk reroll machines are ordinary items (`perk_reroll.xml`, tag `item_shop`).
- **Shop prices**: `ItemCostComponent` is `cost` (int64, big-endian) then `stealable` (bool). Present on shop wands, cards and reroll machines; searched only inside the entity's own segment. Not every priced item is in a Holy Mountain (four priced cards near (-2350, 5490)), and the pickup script (`shop_effect.lua`) doesn't remove it, so the UI says "has a price", not "in a shop".
- **player.xml**: plain XML. `inventory_quick` children are wands (`AbilityComponent` + `gun_config`/`gunaction_config`) and flasks (`MaterialInventoryComponent/count_per_material_type/Material material= count=`, names not numbers); `inventory_full` children are spare cards. Card order is `ItemComponent inventory_slot.x`. Hp in `DamageModelComponent` is ×25 in the UI; gold is `WalletComponent money`.
- **world_state.xml**: `WorldStateComponent/lua_globals/E key= value=` holds `PERK_PICKED_<ID>_PICKUP_COUNT`, `HOLY_MOUNTAIN_VISITS`, `ORB_MAP_STRING`, `TEMPLE_LEAKED_<w>_<depth chunk>` (set when a mountain's material checker fails, i.e. the gods were angered: `temple_check_for_leaks.lua`; key from `temple_pos_to_id` in `temple_shared.lua`, where `w = floor((x + 16384) / 32768)` is a 32768-px column, **not** a parallel-world index) `TEMPLE_ACTIVE_<x>_<y>` (position of the mountain's `workshop_aabb`). A collapse (`data/scripts/buildings/workshop_exit.lua`) sets `TEMPLE_COLLAPSED_<w>_<depth chunk>` = 1 **and** `TEMPLE_ACTIVE_<x>_<y>` = 0; entering a mountain sets `TEMPLE_ACTIVE` to 1 (`temple_enter.lua`). The user's save keeps only `TEMPLE_ACTIVE_2233_13078 = 0` (the last main-world mountain, which the user confirmed collapsed), so `inventory.py` treats `TEMPLE_ACTIVE = 0` as collapsed and also reads any `TEMPLE_COLLAPSED_*`. `orbs_found_thisrun` is a `<primitive>` list of orb ids; `save00/persistent/orbs_new/<id>`: one file per orb id; no game script reads it, so "orbs found in any run" is inferred, not confirmed. `HOLY_MOUNTAIN_VISITS` goes up only when you reach a Holy Mountain (or victory room) deeper than the deepest so far (`player_biome_check.lua`), so it is "depths reached", not visits.
- **Orb map**: `ORB_MAP_STRING` is `"x,y x,y …"` in 512 px chunks (`orb_list` in `data/scripts/lib/utilities.lua`). Verified: entry `-9,7` contains the two `orb_room_materialchecker` entities at (-4550, 3909) and (-4155, 3909). Which orb id belongs to which entry is **not** known, so the map marks the locations without saying which orb or whether it was taken. The game has 12 `orbroom_NN` biomes but the list has 11 entries, so don't call these "all orb rooms".
- **When files are written**: the game writes world and entity files every few minutes while playing (as areas unload), so Refresh is useful mid-run. `player.xml` and `world_state.xml` are only written on save-and-quit (the user's were two days old during development).

### Environment gotchas (this machine)
- Windows 10. `python` is 3.9 (`python3` is a Store stub). Set `PYTHONIOENCODING=utf-8` when printing wiki names, or cp1252 crashes.
- The Bash tool's heredocs break on some quote mixes. For anything non-trivial, write the script to a file with the Write tool and run it.
- Noita: `G:\SteamLibrary\steamapps\common\Noita` (found via `libraryfolders.vdf`). Workshop mods: `G:\SteamLibrary\steamapps\workshop\content\881100\<id>\`. Enabled mods: `save00\mod_config.xml`.
- The packaged window is WebView2 (installed). The computer-use tool can't target the exe by name; to see the app window, capture it with `PrintWindow` (flag 2) via PowerShell, or use `tools/screenshots.py`.

---

## Backlog

Effort: **S** = under an hour, **M** = a few hours, **L** = a day or more. Each item is independent unless it says otherwise.

### Done (2026-11-13, branch `inventory-perks-runstate-zoom`)
- **Refactor (2026-11-13)**: whole codebase reviewed and restructured. Verified with before/after snapshots of `/api/data` (identical for the real save and the sample), pixel-identical tiles on 40 chunks, byte-identical decompression on 300 files, and the same page script run against the old and new UI (all lists, filters, sorts, searches, tabs, tooltips identical except the intended changes). Fixed on the way: chunks were decompressed twice per tile; quarter tiles were also all held in memory; spell-type headings never got their plural names; the Found and Materials lists stopped at 300 rows without saying so; "Has new spells" filtered loose spells while its chip was hidden; a view change made before the window was laid out was overwritten by the first fit; the filter box was read from the DOM once per item per frame; cache and wiki files were written non-atomically.
- **Fact check (2026-11-13)**: every label added in this branch was checked against the game scripts and the save; overclaims ("walls broken", "visited", "Holy Mountain perks", "in a shop", "orb rooms", "perks are gone") were reworded. Still inferred, not proven: the fungal-shift pair layout, the meaning of `orbs_new`, and that `player.xml` is only rewritten on save-and-quit (observed, not from a script).
- **Inventory (was 1)**: Run tab, "Carried" marks on spells, "Hide carried" (Found) and "Carried" (Spells) chips.
- **Holy Mountain perks and shop prices (was 3)**: "Holy Mountain perks" in the Found selector, Perks map layer, `$price` tags, "In a shop" chip, "Price (low)" sort. `NOTABLE_PERKS` in `companion.py` is a **starter list picked by Claude; confirm it with the user**.
- **Run state (was 4)**: perks, orbs (this run / all runs), fungal shifts, Holy Mountain visits and broken walls in the Run tab; main-world orb rooms as a map layer; shifted materials tagged "→ now X" in Materials. Collapsed mountains are listed in the Run tab and marked on the map (Holy Mountains layer). Still unverified: the `<changed_materials>` pair layout (empty in every save so far); re-check on a save that has a fungal shift.
- **Full-detail zoom (was 6)**: past 1×, visible chunks plus one ring around them load from `/tile/full/` (LRU of 96 in the page); quarter tiles show until each arrives. First draw is about 0.2–0.35 s per chunk on the user's PC.

### 5. Mod spells (M)
**Goal**: names and icons for spells from enabled mods (currently shown as raw IDs with "?" icons).

**Data**: `save00/mod_config.xml` lists mods with `enabled="1"` and `workshop_item_id`. A mod's files are in `G:\SteamLibrary\steamapps\workshop\content\881100\<id>\` (or `Noita\mods\<name>\` for local ones). Mods add spells by appending Lua to `data/scripts/gun/gun_actions.lua` (look for `ModLuaFileAppend` in their `init.lua`). Parse the appended file for `id`, `name` (often a `$translation` key, defined in the mod's `translations.csv`), `sprite`, `type`, `spawn_level`, `spawn_probability`.

**Where**: new `mod_data.py`, merged into `load_spells()` in `companion.py` for IDs the wiki lacks. Icons come from the mod folder. Remember the user's wiki-first preference: mods have no wiki pages, so reading mod files is fine here.

### 7. Biome and landmark labels, parallel-world jumps (S–M)
**Data**: the biome map is `data/biome_impl/biome_map.png` in `data.wak` (one pixel per 512×512 chunk). Colours map to biomes in `data/biome/_biomes_all.xml`. Biome names: game translations or the wiki (prefer wiki per user preference; the wiki has a page per biome). Holy Mountains sit at fixed depths; perk altars from item 3 give exact positions.

**Where**: a label layer in `draw()` with a layer toggle in the legend. PW jump buttons next to the zoom controls use `flyTo(k * WORLD_W / DIV, …)`.

### 8. Physics objects on the map (M–L)
**Data**: after the custom colours in each `world_*.png_petri` chunk there is a physics-object list (u32 count, then per object: ids, position, rotation, several floats/bools, `u32 w, u32 h`, then w×h ARGB pixels). **The exact header layout is unverified.** Decode by trial against a chunk that visibly contains a boulder or minecart; cross-check with community parsers if available.

**Where**: `noita_map.render_chunk` (draw objects onto the chunk image after terrain).

### 9. One search across everything ★ (S)
**Goal**: a single search showing matching spells, items, materials, flasks and wands together.

**Where**: front end only. A search box above the tabs (or reuse `#q` on a new "Search" tab) that queries `S` (spell names), `C` (item catalog), `M` (materials, including flask contents) and `items`. Results are rows that call `toggleTrack` / `toggleItem` / `toggleMat` or `goTo(item)`.

### 10. Combined spell filters and a wand score (S–M)
**Where**: front end. Let the Found search accept several spells joined by "and" (or chips), matching wands containing all of them (`matches()`). Wand score: a weighted mix of capacity, mana charge, spells/cast, cast delay, recharge and non-shuffle; add it to the `#sort` options. Ask the user for weights or start with something simple and show the formula in a tooltip.

### 11. Windows notifications (S)
**Goal**: notify when Auto finds a tracked or rare thing while the game has focus.

**Where**: the toast is built in the auto-refresh code in `map_template.html` (search for `freshTracked`). Add `POST /api/notify {title, body}` in `make_handler`. The server shows a Windows toast; simplest is a PowerShell BurntToast-free call via `Windows.UI.Notifications` (no new dependency), or add a small dependency such as `win11toast` to the build venv. Keep it opt-in (a bell toggle next to Auto).

### 12. Update check (S)
**Where**: add `VERSION = '1.1.0'` to `companion.py` (there is no version constant yet; bump it on every release). On startup, fetch `https://api.github.com/repos/treeko11/noita-companion/releases/latest` (no personal data in the request) and expose `{latest, url}` in `/api/data`. Show "Version X available" next to the title. Fail silently offline.

### 13. Pins and notes on the map (S)
**Where**: front end only. Right-click (or long-press) on the map to add a pin with text, stored with `store.set('pins', [...])` (localStorage; per machine is fine). Draw pins in `draw()` and list them in a small panel or a Found selector option.

### Known rough edges

- **R1. Unverified flask materials (S, needs the user)**: flasks whose contents number is ≥ the `<CellData>` count are labelled "Unverified" (`it.unverified`, set in `State.data()`). Two "Slimy Meat" flasks in the current save are at index 365. To verify: ask the user to fill a flask with a known child material (for example water variants or a `CellDataChild` such as `blood_fungi`), drop it, save and quit, then read its index in `entities_*.bin`. If the numbering differs, children are probably ordered differently (for example right after their parent). Adjust `material_order()` and remove the label once confirmed.
- **R2. Pickups with no wiki entry** show a cleaned-up file name (`item_key()` fallback). Map them in `EXTRA_ITEMS`/`ITEM_ALIASES` to wiki pages when found. Done 2026-11-13: `essence_laser` → Essence of Earth (user confirmed), `greed_curse` → Curse of Greed. None left in the current save; `EXTRA_ITEMS` entries still have no icons.
- **R3. Static export (`--export`)** clips to the main world because WebP is limited to 16,383 px. Fine for snapshots; the live app shows everything.
