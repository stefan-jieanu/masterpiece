"""Conversions from each client's native values to the app's normalized ones."""
from datetime import datetime

MODES = ('osu', 'taiko', 'catch', 'mania')
LAZER_MODES = {'osu': 'osu', 'taiko': 'taiko', 'fruits': 'catch', 'mania': 'mania'}

STABLE_STATUS = {0: 'unknown', 1: 'unsubmitted', 2: 'pending', 3: 'unknown', 4: 'ranked', 5: 'approved', 6: 'qualified', 7: 'loved'}
LAZER_STATUS = {-4: 'modified', -3: 'unsubmitted', -2: 'graveyard', -1: 'wip', 0: 'pending', 1: 'ranked', 2: 'approved', 3: 'qualified', 4: 'loved'}

STABLE_GRADES = {0: 'XH', 1: 'SH', 2: 'X', 3: 'S', 4: 'A', 5: 'B', 6: 'C', 7: 'D', 8: 'F'}
LAZER_GRADES = {-1: 'F', 0: 'D', 1: 'C', 2: 'B', 3: 'A', 4: 'S', 5: 'SH', 6: 'X', 7: 'XH'}

LEGACY_MODS = [
    (1, 'NF'), (2, 'EZ'), (4, 'TD'), (8, 'HD'), (16, 'HR'), (32, 'SD'), (64, 'DT'), (128, 'RX'), (256, 'HT'), (512, 'NC'),
    (1024, 'FL'), (2048, 'AT'), (4096, 'SO'), (8192, 'AP'), (16384, 'PF'), (32768, '4K'), (65536, '5K'), (131072, '6K'),
    (262144, '7K'), (524288, '8K'), (1048576, 'FI'), (2097152, 'RD'), (4194304, 'CN'), (8388608, 'TP'), (16777216, '9K'),
    (33554432, 'CP'), (67108864, '1K'), (134217728, '3K'), (268435456, '2K'), (536870912, 'V2'), (1073741824, 'MR'),
]
MOD_ORDER = {acronym: i for i, (_, acronym) in enumerate(LEGACY_MODS)}

# The mod combinations ratings are calculated for: column suffix, legacy mod bits, label, mods.
# The order must match MOD_COMBOS in rating_calculator/src/main.rs.
COMBOS = (
    ('', 0, 'NM', ()),
    ('_hd', 8, 'HD', ('HD',)),
    ('_dt', 64, 'DT', ('DT',)),
    ('_hddt', 72, 'HDDT', ('HD', 'DT')),
    ('_hr', 16, 'HR', ('HR',)),
    ('_hdhr', 24, 'HDHR', ('HD', 'HR')),
    ('_hrdt', 80, 'HRDT', ('HR', 'DT')),
    ('_hdhrdt', 88, 'HDHRDT', ('HD', 'HR', 'DT')),
)
# Tapping is the calculator's speed skill.
SKILLS = ('stars', 'aim', 'tapping', 'reading')
RATING_COLUMNS = tuple(f'{skill}{combo[0]}' for combo in COMBOS for skill in SKILLS)

# These don't change anything the calculator measures.
NEUTRAL_MODS = {'NF', 'SD', 'PF', 'SO', 'CL'}
SUFFIX_BY_MODS = {frozenset(combo[3]): combo[0] for combo in COMBOS}


def played_suffix(mods) -> str | None:
    """The rating column suffix for a score's mods, or None if they're outside the calculated combinations. NC is DT's rate."""
    effective = {'DT' if mod == 'NC' else mod for mod in mods} - NEUTRAL_MODS
    return SUFFIX_BY_MODS.get(frozenset(effective))


def normalize_mods(acronyms) -> list[str]:
    """Canonical order; NC implies DT and PF implies SD; lazer's CL is dropped so classic scores compare with stable."""
    mods = {a.upper() for a in acronyms} - {'CL', 'NM'}
    if 'NC' in mods:
        mods.discard('DT')
    if 'PF' in mods:
        mods.discard('SD')
    return sorted(mods, key=lambda a: (MOD_ORDER.get(a, 999), a))


def legacy_mods(bits: int) -> list[str]:
    return normalize_mods(a for bit, a in LEGACY_MODS if bits & bit)


def ticks_to_epoch(ticks: int):
    return int((ticks - 621355968000000000) / 10_000_000) if ticks > 0 else None


def iso_to_epoch(value):
    if not value:
        return None
    ts = datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    return int(ts) if ts > 0 else None


def accuracy(mode, n300, n100, n50, ngeki, nkatu, nmiss):
    """Percent, 0-100, by each mode's own formula. None when there were no judgements."""
    if mode == 'taiko':
        total = n300 + n100 + nmiss
        return (n300 + 0.5 * n100) / total * 100 if total else None
    if mode == 'catch':
        total = n300 + n100 + n50 + nkatu + nmiss
        return (n300 + n100 + n50) / total * 100 if total else None
    if mode == 'mania':
        total = ngeki + n300 + nkatu + n100 + n50 + nmiss
        return (300 * (ngeki + n300) + 200 * nkatu + 100 * n100 + 50 * n50) / (300 * total) * 100 if total else None
    total = n300 + n100 + n50 + nmiss
    return (300 * n300 + 100 * n100 + 50 * n50) / (300 * total) * 100 if total else None


def grade(mode, mods, acc, n300, n100, n50, nmiss):
    """Stable stores no per-score grade, so derive it. Rules differ per mode; HD/FL (and FI in mania) give silver."""
    if acc is None:
        return 'D'
    silver = bool({'HD', 'FL'} & set(mods)) or (mode == 'mania' and 'FI' in mods)
    if mode in ('catch', 'mania'):
        thresholds = (98, 94, 90, 85) if mode == 'catch' else (95, 90, 80, 70)
        if acc >= 100:
            letter = 'X'
        else:
            letter = next((g for g, t in zip('SABC', thresholds) if acc > t), 'D')
    else:
        total = n300 + n100 + n50 + nmiss
        r300, r50 = n300 / total, n50 / total
        if r300 == 1:
            letter = 'X'
        elif r300 > 0.9 and nmiss == 0 and (mode == 'taiko' or r50 <= 0.01):
            letter = 'S'
        elif (r300 > 0.8 and nmiss == 0) or r300 > 0.9:
            letter = 'A'
        elif (r300 > 0.7 and nmiss == 0) or r300 > 0.8:
            letter = 'B'
        elif r300 > 0.6:
            letter = 'C'
        else:
            letter = 'D'
    return letter + 'H' if silver and letter in ('X', 'S') else letter


def bpm_from_timing_points(points, end_ms):
    """Most common BPM by duration, ignoring inherited points. points: (beat_length, offset, uninherited)."""
    timed = sorted(((bl, off) for bl, off, uninherited in points if uninherited and bl > 0), key=lambda p: p[1])
    if not timed:
        return None
    durations = {}
    for i, (beat_length, offset) in enumerate(timed):
        start = offset if i else 0
        end = timed[i + 1][1] if i + 1 < len(timed) else end_ms
        key = round(beat_length, 3)
        durations[key] = durations.get(key, 0) + max(0, end - start)
    return round(60000 / max(durations, key=durations.get), 2)


def search_text(row) -> str:
    parts = [row.get(k) for k in ('artist', 'artist_unicode', 'title', 'title_unicode', 'creator', 'difficulty', 'song_source', 'tags', 'beatmap_id', 'set_id')]
    return ' '.join(str(p) for p in parts if p not in (None, '')).lower()


def merge_beatmap(beatmaps: dict, row: dict):
    """One row per md5. First source wins; the second only fills gaps, except flags and last_played."""
    existing = beatmaps.get(row['md5'])
    if existing is None:
        beatmaps[row['md5']] = row
        return
    for key, value in row.items():
        if key in ('in_stable', 'in_lazer'):
            existing[key] = existing.get(key, 0) or value
        elif key == 'last_played':
            existing[key] = max([v for v in (existing.get(key), value) if v], default=None)
        elif existing.get(key) in (None, ''):
            existing[key] = value


def add_collection(collections: dict, name: str, source: str, hashes):
    entry = collections.setdefault(name, {'sources': set(), 'hashes': set()})
    entry['sources'].add(source)
    entry['hashes'].update(h for h in hashes if h)
