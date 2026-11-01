# masterpiece

A local osu! library browser: filter, sort and group beatmaps and scores on every attribute, from osu!stable and osu!lazer together. This is a proof of concept, built from the ideas in `custom-osu-level-browser`.

Unofficial fan tool. Not affiliated with ppy or osu!.

## Run it

Needs [uv](https://docs.astral.sh/uv/), Node and [Rust](https://rustup.rs/). Run these from `backend/`:

```sh
uv sync
node lazer_reader/setup.mjs        # only needed for lazer import
cargo build --release --manifest-path rating_calculator/Cargo.toml

# Give either source, or both. Each run rebuilds the library from scratch.
uv run python -m app.importer --stable "PATH/TO/osu!" --lazer "%APPDATA%/osu"

uv run uvicorn app.main:app --port 8000
```

Then open http://127.0.0.1:8000.

`--stable` is a folder holding `osu!.db`, `collection.db` and `scores.db`. `--lazer` is the folder holding `client.realm` and `files/`. Your osu! files are only read: lazer's database is copied to a temporary folder and the copy is what gets opened. If an import fails, the previous library is kept. If a lazer import fails while the game is running, close the game and retry.

Ratings need each map's `.osu` file. Lazer's come from `files/`. Stable's come from `Songs/` inside `--stable` (or `--songs DIR`), and otherwise they are downloaded from osu.ppy.sh at about one per second and cached in `backend/data/osu_cache/`. Every file is checked against the map's MD5 before it's used. Maps that couldn't be downloaded are retried after a week, or sooner if downloads were interrupted. Use `--no-download` to stay offline, or `--no-ratings` to skip ratings altogether. The first import of a large stable library can take a while; later ones reuse the cache.

The import prints a report with how many maps couldn't be rated, by reason, plus the first 20 of them. Any map's details panel says why it wasn't rated.

After updating the app, run the importer again. If the library is too old for the code, the page tells you so.

Run the checks with `uv run python -m tests.smoke "PATH/TO/osu!" "%APPDATA%/osu"`.

## Using it

**Filter box.** Words search all song text. `field op value` filters on a field. The **fields** button lists every field.

```
camellia stars>=5 bpm=180 length<2m30s status=ranked,loved
acc>98 grade>=S mods:HD mods!:FL played_at=2026-10 tag=="vocaloid" collection=="pp"
```

| Operator | Meaning |
|---|---|
| `=` | contains (text), about equal (numbers), within that period (dates) |
| `==` | exact |
| `!=`, `>`, `>=`, `<`, `<=` | as written; `grade` uses rank order |
| `~` | within 5% |
| `mods=HDDT`, `mods:HD`, `mods!:FL` | exact set, includes, excludes (`DT` also matches NC, `SD` also matches PF) |

Filtering on a score field means "has a score like this". The best matching score, by the **Best score by** setting, is then the one shown on the beatmap. Anything invalid shows an error under the box rather than being ignored.

**Groups.** Pick any field in **Group by**. The bucket type follows the data type:

| Data type | Buckets | Examples |
|---|---|---|
| Text | exact value or first letter | artist, title, mapper, difficulty name |
| Number | width you choose | stars, BPM, AR/CS/OD/HP, accuracy, combo |
| Duration | 30s to 5min | length, drain |
| Date | day, month or year | last played, date added, played at |
| Enum / yes-no | each value | status, mode, grade, full combo |
| Mods | each exact combination | `HD DT` |
| Multi-valued | one row per membership | tags, collections |

Click groups to narrow the list; selecting several means any of them. Counts read `matching / total`. Hovering a group shows **+**, which moves it into the filter box, so you can group by another field and drill down. This replaces the demo's right-click "source".

The **Beatmaps** and **Scores** tabs accept the same filters and groupings.

## Difficulty ratings

Each osu!standard map gets its overall star rating and the three skills behind it, for 8 mod combinations. They work like every other number: in the filter box, **Sort** and **Group by**.

| Field | Meaning | Also accepted |
|---|---|---|
| `stars` | Overall star rating | `sr`, `star` |
| `aim` | Aim skill | |
| `tapping` | Tapping, which is the calculator's speed skill | `tap`, `speed` |
| `reading` | Reading skill | `read` |

Without a suffix a field means nomod. Add `_hd`, `_dt`, `_hddt`, `_hr`, `_hdhr`, `_hrdt` or `_hdhrdt` for the other combinations, in any order and with `nc` for `dt`. One query can mix them:

```
aim>=3
aim_dt>=3 reading_hd>=2
played_aim>=3 grade>=S
```

**The scale.** Aim, tapping and reading are the calculator's own values, unconverted apart from rounding to 2 decimals. They share a scale with each other but not with stars: a 5.01★ map might be aim 2.74, tapping 2.07, reading 0.58. Without FL they combine as `stars = 1.6485 × (aim^3.3 + tapping^3.3 + reading^3.3)^(1/3.3)`.

**What changes them.** HD changes reading and stars, but not aim or tapping. DT and NC change all of them. HR changes aim, reading and stars, and leaves tapping unchanged on most maps.

**On rows.** Beatmap rows show nomod aim, tapping and reading, plus any other rating you're sorting by. The details panel has a table of all 8 combinations.

**As played.** `played_stars`, `played_aim`, `played_tapping` and `played_reading` are the beatmap's ratings under a score's own mods. NC counts as DT, and NF, SD, PF, SO and CL are ignored because they don't change difficulty. Scores with anything else, such as EZ, HT, FL, AP or a custom DT speed, stay blank.

**Overall stars and other modes.** On std maps `stars` is the calculator's value. If a map can't be calculated, `stars` falls back to what the client stored and modded values stay empty. Taiko, catch and mania keep the client's stored nomod stars, and their rating fields are blank.

**How close it is.** All on the sample libraries:

| Compared with | Maps | Largest difference |
|---|---|---|
| Lazer's stored nomod stars | 1,741 | 0.0003 |
| Stable's stored stars, all 8 combinations | 245 | 0.015, including rounding to 2 decimals |

If a future import finds calculated stars drifting from what your client stored, it prints a warning, which usually means lazer's algorithm has moved on again.

## Tapping tab

A custom metric, separate from star rating: how close together a map's 1/4 tap groups are, as a value from 1 to 10 for streams, bursts and doubles/triples. It doesn't use the rating calculator and doesn't change with mods or BPM. The idea comes from [osu-tapping-data](https://github.com/Chiu76/osu-tapping-data).

This is a test bench: it shows several versions of the calculation, so they can be checked on real maps before one is chosen. The filter box narrows its list as usual, but its values aren't filter fields.

| Group | Taps with 1/4 of a beat between them |
|---|---|
| Double | 2 |
| Triple | 3 |
| Burst | 4 to 8 |
| Stream | 9+ |

Sliders count as taps, with gaps measured from tap to tap, and spinners split a group. 1/4 means 1/4 of the map's own timing. Doubles and triples are counted apart but share one value, and distances run between both.

Distances are measured in beats from the last tap of a group to the first tap of the next group of that type, then sorted into sizes: under 1, 2, 4 or 8 beats, or 8+. Closer scores higher: 10, 7.75, 5.5, 3.25 and 1.

| Version | What it takes the share of |
|---|---|
| A | Each distance once, plus song start to first group. Nothing after the last group |
| B | Each distance between two groups once. One group means no distance, so a value of 1 |
| C | Each distance once, plus first tap to first group and last group to last tap |
| D | Every beat from first to last tap, with beats inside groups as the closest size |
| E | Every beat in a gap, leaving out beats inside groups |

**Ignored maps** say why: no 1/4 rhythms, more than 2 BPM changes (counted across the whole file), no local `.osu` file, or not osu!standard. Nothing is downloaded for this tab, but a file the rating step already downloaded counts as local. Use `--no-tapping` to skip the analysis.

## How it fits together

| Path | Role |
|---|---|
| `backend/app/tapping.py`, `tapping_query.py` | The Tapping tab's calculations and list, kept apart from the field registry and ratings. `frontend/tapping.js` is its UI |
| `backend/app/fields.py` | The field registry. Each attribute is declared once with its SQL and type; filtering, sorting, grouping and the UI all derive from it. **Adding a field here is normally all it takes**, if its column already exists |
| `backend/app/query.py` | Query language and grouping to SQL. Values are always bound |
| `backend/app/normalize.py` | Mapping both clients to one model: mods, grades, accuracy per mode, BPM, statuses, dates |
| `backend/app/stable.py`, `lazer.py` | The two importers. `lazer_reader/` is a small Node helper, because Realm has no Python SDK |
| `backend/app/ratings.py` | Finds `.osu` files, runs the calculator in isolated processes with a time limit, and caches results |
| `backend/rating_calculator/` | A small Rust program around a vendored copy of rosu-pp. `vendor/rosu-pp/ORIGIN.md` records the commit, the one change we made and how to update |
| `backend/app/importer.py` | Merges by beatmap MD5 and writes one SQLite file, `backend/data/library.sqlite` |
| `frontend/` | Vanilla JS and plain CSS with no build step, served by the backend |

## Fixed from the demo

Accuracy misweighted 50s and used the std formula for all modes. Grades had no silver and were std-only. BPM ignored durations. `=` on numbers matched substrings. `<=` and `>=` were rejected in the UI, per the code (read, not run). `num_misses` and `relax2` caused server errors. Star ratings were std-only. Search couldn't match words across columns. Non-collection sources did nothing. Bad dates were silently ignored. Re-imports left stale rows. Beatmaps with no online ID collided on a unique key, also from reading the code. `count` was capped at 500.

## Known limits

- **Unreleased calculator:** released rosu-pp has no std reading skill and predates lazer's 2026 rating rework, so this uses a copy of its `pp-update` branch. It's std-validated, but taiko isn't ported yet on that branch, which is why other modes keep their stored stars.
- **The HD slider fix is unconfirmed by data:** the one change we made to rosu-pp follows lazer's source code. Lazer stores no modded ratings, and none of the 245 sample stable maps are affected by it, so no stored value could confirm it. It only touches reading and stars on HD combinations, about 5% of maps, with a largest change of 0.125 reading and 0.058 stars.
- **Excluded mods:** EZ, HT and FL aren't calculated, so scores using them have no as-played ratings. A custom speed like DT at 1.25x stays blank for the same reason.
- **Malicious maps:** the calculator has a time limit per map, 60 seconds, and suspicious maps are skipped and reported, but nothing limits its memory use.
- **Attributes that exist on one side only:** drain time and rank achieved come from stable. Date added, date ranked, date submitted and pp come from lazer. These are empty for the other client.
- **Numbers that don't compare across clients:** stable scores are classic totals while lazer scores are standardised. When a beatmap is in both, the stable values win. `length` is total time from stable and playable length from lazer.
- **Duplicates:** a score present in both clients appears twice, because there's no reliable shared key.
- **Lazer versions:** only Realm file format 24 and schema 52 are tested. A newer format stops the import with a clear message until `realm` is updated in `lazer_reader/`.
- **Internet use:** cover art comes from assets.ppy.sh (which sees the set IDs) and the font from Google Fonts. Without them you get placeholders and a fallback font. Sets with no online ID always show a placeholder.
- **Tested at about 2,000 beatmaps.** The list re-renders as pages load, so very large libraries would need real virtualization.
- **Test coverage gaps:** the sample stable files are all std and ranked, and shared no beatmaps with the sample lazer library. Stable grades for other modes and the merge path have unit checks only, and stable files older than version 20191106 have never been read.
# masterpiece
