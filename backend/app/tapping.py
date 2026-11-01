"""A custom tapping metric: how close together a map's 1/4 tap groups are, as a value from 1 to 10.

It's unrelated to star rating: it doesn't use the rating calculator, and it doesn't change with mods or BPM. BPM only decides how long
1/4 of a beat is. The idea comes from https://github.com/Chiu76/osu-tapping-data. Several versions of the 1 to 10 calculation are kept
side by side for the Tapping tab, so they can be compared on real maps.
"""
import hashlib
import json
import math
import re
import time
from collections import Counter
from pathlib import Path

from .ratings import local_candidates

# Gaps on real maps cluster tightly: within this of 1/4 of a beat, with nothing near it until 1/3 and 1/6.
QUARTER_TOLERANCE = 0.032
# Hit object times are whole milliseconds, so very fast maps need a little more room than the share above.
QUARTER_TOLERANCE_MS = 1.5
BPM_TOLERANCE = 0.01
MAX_BPM_CHANGES = 2
# The client's own limits. Anything outside them is a broken line, or a gimmick that would only overflow the arithmetic.
MAX_PARSED_NUMBER = 2_147_483_647
BEAT_LENGTH_RANGE = (6.0, 60000.0)
# Distances are snapped to a fine grid first, so a millisecond of rounding can't move one into the next size.
DISTANCE_GRID = 48
TOP_SHARE = 0.99

KINDS = (
    {'key': 'stream', 'label': 'Streams', 'sizes': '9+ taps'},
    {'key': 'burst', 'label': 'Bursts', 'sizes': '4 to 8 taps'},
    {'key': 'dt', 'label': 'Doubles/triples', 'sizes': '2 or 3 taps'},
)
KIND_KEYS = tuple(kind['key'] for kind in KINDS)
# The repo's distance sizes. Closer scores higher, with the same step for each doubling of the distance.
BINS = ('under 1 beat', '1 to 2 beats', '2 to 4 beats', '4 to 8 beats', '8+ beats')
BINS_SHORT = ('under 1', '1 to 2', '2 to 4', '4 to 8', '8+')
BIN_SCORES = (10, 7.75, 5.5, 3.25, 1)

STATUSES = (
    {'key': 'rated', 'label': 'Rated'},
    {'key': 'no_quarter', 'label': 'Ignored: no 1/4 rhythms'},
    {'key': 'many_bpm_changes', 'label': f'Ignored: more than {MAX_BPM_CHANGES} BPM changes'},
    {'key': 'no_local_file', 'label': 'Ignored: no local .osu file'},
    {'key': 'not_std', 'label': 'Ignored: not osu!standard'},
    {'key': 'skipped', 'label': 'Not analysed at import'},
)


def parsed_number(text: str) -> float:
    value = float(text)
    if not math.isfinite(value) or abs(value) > MAX_PARSED_NUMBER:
        raise ValueError(text)
    return value


def parse_osu(data: bytes) -> tuple[list, list]:
    """Returns the red lines as [(offset, beat length)] and the hit objects as [(time, is a tap)], both in time order. Broken lines are skipped."""
    timing, objects, section = [], [], None
    for raw in data.decode('utf-8', 'replace').splitlines():
        line = raw.strip()
        if line.startswith('[') and line.endswith(']'):
            section = line
            continue
        if not line or line.startswith('//') or section not in ('[TimingPoints]', '[HitObjects]'):
            continue
        parts = line.split(',')
        try:
            if section == '[TimingPoints]':
                offset, beat = parsed_number(parts[0]), parsed_number(parts[1])
                inherited = len(parts) > 6 and parts[6].strip() == '0'
                if beat > 0 and not inherited:
                    timing.append((offset, min(BEAT_LENGTH_RANGE[1], max(BEAT_LENGTH_RANGE[0], beat))))
            else:
                when, kind = parsed_number(parts[2]), int(parts[3])
                # Sliders count as taps, since only the gaps between taps matter here. Spinners have no tap.
                if kind & 3:
                    objects.append((when, True))
                elif kind & 8:
                    objects.append((when, False))
        except (ValueError, IndexError):
            continue
    timing.sort(key=lambda point: point[0])
    objects.sort(key=lambda item: item[0])
    return timing, objects


def segments(timing: list) -> list:
    """One (start, beat length, beats before start) per stretch of constant BPM. A red line repeating the same BPM isn't a change."""
    result = []
    for offset, beat in timing:
        if result:
            start, previous, before = result[-1]
            if abs(60000 / beat - 60000 / previous) <= BPM_TOLERANCE:
                continue
            result.append((offset, beat, before + (offset - start) / previous))
        else:
            result.append((offset, beat, 0.0))
    return result


def segment_at(parts: list, when: float) -> tuple:
    """Before the first red line, its BPM applies."""
    current = parts[0]
    for part in parts[1:]:
        if part[0] > when:
            break
        current = part
    return current


def beat_position(parts: list, when: float) -> float:
    start, beat, before = segment_at(parts, when)
    return before + (when - start) / beat


def is_quarter(gap: float, beat: float) -> bool:
    return abs(gap - beat / 4) <= max(beat / 4 * QUARTER_TOLERANCE, QUARTER_TOLERANCE_MS)


def find_groups(objects: list, parts: list) -> list:
    """Every run of taps with 1/4 of a beat from each tap to the next, as (first tap, last tap, taps). 1/4 is of the map's own timing."""
    groups, start, taps = [], None, 0
    for (when, tap), (following, next_tap) in zip(objects, objects[1:]):
        if tap and next_tap and is_quarter(following - when, segment_at(parts, when)[1]):
            if start is None:
                start, taps = when, 1
            taps += 1
        elif start is not None:
            groups.append((start, when, taps))
            start = None
    if start is not None:
        groups.append((start, objects[-1][0], taps))
    return groups


def kind_of(taps: int) -> str:
    """Doubles and triples are different things but the same type of group, so distances run between both."""
    return 'dt' if taps <= 3 else 'burst' if taps <= 8 else 'stream'


def bin_index(distance: float) -> int:
    snapped = round(distance * DISTANCE_GRID) / DISTANCE_GRID
    return 0 if snapped < 1 else 1 if snapped < 2 else 2 if snapped < 4 else 3 if snapped < 8 else 4


def measure(groups: list, parts: list, first_tap: float, last_tap: float) -> dict:
    """The distances for one type of group, in beats. Each runs from the last tap of a group to the first tap of the next group of this type."""
    at = lambda when: beat_position(parts, when)
    return {
        'gaps': [at(following[0]) - at(group[1]) for group, following in zip(groups, groups[1:])],
        'song_lead': max(0.0, at(groups[0][0]) - at(0.0)),
        'first_lead': at(groups[0][0]) - at(first_tap),
        'tail': at(last_tap) - at(groups[-1][1]),
        'inside': sum(at(group[1]) - at(group[0]) for group in groups),
    }


def shares_of(weights: list) -> list | None:
    total = sum(weights)
    return [weight / total for weight in weights] if total else None


def count_shares(distances: list) -> list | None:
    """Each distance counts once, however long it is."""
    counts = [0] * len(BINS)
    for distance in distances:
        counts[bin_index(distance)] += 1
    return shares_of(counts)


def beat_shares(distances: list, inside: float = 0.0) -> list | None:
    """Each beat counts once, so a long distance counts for more. Beats inside groups count as the closest size."""
    beats = [0.0] * len(BINS)
    beats[0] = inside
    for distance in distances:
        beats[bin_index(distance)] += distance
    return shares_of(beats)


def edges(m: dict) -> list:
    return [*m['gaps'], m['first_lead'], m['tail']]


# The versions under comparison. Shares are of the sizes in BINS, and a map with nothing to share gets the lowest value.
VERSIONS = (
    {'key': 'a', 'family': 'Share of distances', 'name': 'Distances + song start',
     'note': "Each distance between two groups counts once, plus the stretch from the song start to the first group. Nothing after the last group counts. This is the repo's way.",
     'shares': lambda m: count_shares([*m['gaps'], m['song_lead']])},
    {'key': 'b', 'family': 'Share of distances', 'name': 'Distances only',
     'note': 'Only the distances between two groups count. A map with a single group has no distance, so it gets 1. A few close groups can score very high.',
     'shares': lambda m: count_shares(m['gaps'])},
    {'key': 'c', 'family': 'Share of distances', 'name': 'Distances + both ends',
     'note': "Each distance between two groups counts once, plus first tap to first group and last group to last tap. A map starting or ending on a group gets a false 'very close' distance.",
     'shares': lambda m: count_shares(edges(m))},
    {'key': 'd', 'family': 'Earlier options, not picked', 'name': "Share of the map's beats",
     'note': 'Every beat from first to last tap is either inside a group or in a gap of some size, and each beat counts once. Long streams count, and maps with few groups stay low.',
     'shares': lambda m: beat_shares(edges(m), m['inside'])},
    {'key': 'e', 'family': 'Earlier options, not picked', 'name': 'Share of gap beats',
     'note': 'Like the version above, but beats inside groups are left out, so it only looks at distances. A map of a few very long streams stays low.',
     'shares': lambda m: beat_shares(edges(m))},
)
VERSION_KEYS = tuple(version['key'] for version in VERSIONS)
VALUE_COLUMNS = tuple(f'{version}_{kind}' for version in VERSION_KEYS for kind in KIND_KEYS)

TAPPING = {
    'md5': 'TEXT PRIMARY KEY', 'status': 'TEXT NOT NULL', 'source': 'TEXT', 'bpm_changes': 'INTEGER', 'bpm_changes_in_play': 'INTEGER',
    'doubles': 'INTEGER', 'triples': 'INTEGER', 'bursts': 'INTEGER', 'streams': 'INTEGER', 'longest': 'INTEGER',
    **{column: 'REAL' for column in VALUE_COLUMNS}, **{f'{column}_s': 'REAL' for column in VALUE_COLUMNS}, 'data': 'TEXT',
}


def rate(shares: list | None) -> float:
    return 1.0 if shares is None else sum(share * score for share, score in zip(shares, BIN_SCORES))


def value_entry(shares: list | None) -> dict:
    return {'raw': round(rate(shares), 4), 'shares': [round(share, 4) for share in shares] if shares else None}


def analyse(data: bytes) -> dict:
    """Everything the Tapping tab shows for one .osu file, before the values are stretched."""
    timing, objects = parse_osu(data)
    taps = [when for when, tap in objects if tap]
    parts = segments(timing)
    if not parts or not taps:
        return {'status': 'no_quarter', 'bpm_changes': max(0, len(parts) - 1), 'bpm_changes_in_play': 0}
    first_tap, last_tap = taps[0], taps[-1]
    result = {'bpm_changes': len(parts) - 1, 'bpm_changes_in_play': sum(first_tap < part[0] <= last_tap for part in parts[1:])}
    if result['bpm_changes'] > MAX_BPM_CHANGES:
        return {**result, 'status': 'many_bpm_changes'}
    groups = find_groups(objects, parts)
    if not groups:
        return {**result, 'status': 'no_quarter'}

    sizes = Counter(group[2] for group in groups)
    kinds = {}
    for kind in KIND_KEYS:
        chosen = [group for group in groups if kind_of(group[2]) == kind]
        if not chosen:
            continue
        m = measure(chosen, parts, first_tap, last_tap)
        counts = [0] * len(BINS)
        for gap in m['gaps']:
            counts[bin_index(gap)] += 1
        kinds[kind] = {
            'count': len(chosen), 'longest': max(group[2] for group in chosen), 'distances': counts, 'gaps': [round(gap, 2) for gap in m['gaps']],
            'song_lead': round(m['song_lead'], 2), 'first_lead': round(m['first_lead'], 2), 'tail': round(m['tail'], 2),
            'versions': {version['key']: value_entry(version['shares'](m)) for version in VERSIONS},
        }
    return {
        **result, 'status': 'rated', 'first_tap': first_tap, 'last_tap': last_tap, 'groups': [[round(a), round(b), n] for a, b, n in groups], 'kinds': kinds,
        'doubles': sizes[2], 'triples': sizes[3], 'bursts': sum(n for size, n in sizes.items() if 4 <= size <= 8),
        'streams': sum(n for size, n in sizes.items() if size >= 9), 'longest': max(group[2] for group in groups),
    }


def stretched(raw: float, top: float) -> float:
    return raw if top <= 1 else min(10.0, 1 + (raw - 1) * 9 / (top - 1))


def top_of_scale(values: list) -> float:
    ordered = sorted(values)
    return ordered[int(len(ordered) * TOP_SHARE)]


def add_stretched_values(analyses: list) -> dict:
    """Stretches each version and type of group so the top 1% of this library reach 10. Returns the value each one's 10 corresponds to."""
    tops = {}
    for version in VERSION_KEYS:
        for kind in KIND_KEYS:
            entries = [a['kinds'][kind]['versions'][version] for a in analyses if kind in a.get('kinds', {})]
            if not entries:
                continue
            top = top_of_scale([entry['raw'] for entry in entries])
            tops[f'{version}_{kind}'] = round(top, 4)
            for entry in entries:
                entry['scaled'] = round(stretched(entry['raw'], top), 4)
    return tops


def read_local(row: dict, songs_dir, cache_dir: Path) -> tuple[bytes | None, str | None]:
    """The map's .osu file if this machine has it. Nothing is ever downloaded for this metric."""
    if not re.fullmatch(r'[0-9a-f]{32}', row['md5']):
        return None, None
    for path, source in local_candidates(row, songs_dir, cache_dir):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if hashlib.md5(data).hexdigest() == row['md5']:
            return data, source
    return None, None


def table_row(md5: str, source: str | None, a: dict) -> dict:
    row = {'md5': md5, 'source': source, **{key: a.get(key) for key in ('status', 'bpm_changes', 'bpm_changes_in_play', 'doubles', 'triples', 'bursts', 'streams', 'longest')}}
    for kind, entry in a.get('kinds', {}).items():
        for version, value in entry['versions'].items():
            row[f'{version}_{kind}'], row[f'{version}_{kind}_s'] = value['raw'], value['scaled']
    row['data'] = json.dumps({key: a[key] for key in ('first_tap', 'last_tap', 'groups', 'kinds')}, separators=(',', ':')) if a['status'] == 'rated' else None
    return row


def apply(beatmaps: dict, songs_dir, cache_dir: Path) -> tuple[list, dict]:
    """Returns (rows for the tapping table, the import report part)."""
    started = time.perf_counter()
    found = []
    sources = Counter()
    for row in beatmaps.values():
        if row.get('mode') != 'osu':
            found.append((row['md5'], None, {'status': 'not_std'}))
            continue
        data, source = read_local(row, songs_dir, cache_dir)
        if data is None:
            found.append((row['md5'], None, {'status': 'no_local_file'}))
            continue
        sources[source] += 1
        found.append((row['md5'], source, analyse(data)))
    tops = add_stretched_values([a for _, _, a in found])
    rows = [table_row(md5, source, a) for md5, source, a in found]
    report = {'maps': dict(Counter(row['status'] for row in rows)), 'osu_files': dict(sources), 'top_of_scale': tops, 'seconds': round(time.perf_counter() - started, 2)}
    return rows, report


def skipped(beatmaps: dict) -> list:
    return [table_row(row['md5'], None, {'status': 'skipped'}) for row in beatmaps.values()]


def public() -> dict:
    return {
        'kinds': list(KINDS), 'bins': list(BINS), 'bins_short': list(BINS_SHORT), 'scores': list(BIN_SCORES), 'statuses': list(STATUSES), 'max_bpm_changes': MAX_BPM_CHANGES,
        'versions': [{key: version[key] for key in ('key', 'family', 'name', 'note')} for version in VERSIONS],
    }
