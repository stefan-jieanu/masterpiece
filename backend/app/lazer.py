"""Reads osu!lazer's client.realm through the Node helper. The live folder is never opened: only a temporary copy is."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .normalize import (LAZER_GRADES, LAZER_MODES, LAZER_STATUS, add_collection, iso_to_epoch, merge_beatmap, normalize_mods)

READER_DIR = Path(__file__).resolve().parents[1] / 'lazer_reader'


def export_realm(root: Path, out: Path):
    realm = root / 'client.realm'
    if not realm.exists():
        raise FileNotFoundError(f'No client.realm in {root}')
    if not (READER_DIR / 'node_modules' / 'realm').exists():
        raise RuntimeError('The lazer reader is not set up. Run: node backend/lazer_reader/setup.mjs')
    copy = out / 'client.realm'
    shutil.copyfile(realm, copy)
    result = subprocess.run(
        ['node', str(READER_DIR / 'export-realm.mjs'), '--module', str(READER_DIR), '--realm', str(copy), '--out', str(out)],
        capture_output=True, text=True, encoding='utf-8',
    )
    lines = result.stdout.strip().splitlines()
    outcome = json.loads(lines[-1]) if lines else {'ok': False, 'message': result.stderr.strip()}
    if not outcome.get('ok'):
        raise RuntimeError(f'Could not read client.realm: {outcome.get("message")}')
    return outcome


def count_objects(path: Path):
    """Lazer stores only totals, so split circles/sliders/spinners from the .osu file itself."""
    circles = sliders = spinners = 0
    inside = False
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            if not inside:
                inside = line.startswith('[HitObjects]')
                continue
            parts = line.split(',')
            if len(parts) < 4 or not parts[3].strip().isdigit():
                continue
            kind = int(parts[3])
            if kind & 1:
                circles += 1
            elif kind & (2 | 128):
                sliders += 1
            elif kind & 8:
                spinners += 1
    return circles, sliders, spinners


def beatmap_row(b: dict, s: dict, root: Path) -> dict:
    meta, diff = b['Metadata'], b['Difficulty']
    author = meta.get('Author') or {}
    set_id = s['OnlineID'] if s['OnlineID'] > 0 else None
    online_id = b['OnlineID'] if b['OnlineID'] > 0 else None
    objects = b['TotalObjectCount'] if b['TotalObjectCount'] >= 0 else None
    tags = ' '.join(filter(None, [meta.get('Tags'), ' '.join(meta.get('UserTags') or [])]))
    circles = sliders = spinners = None
    osu_file = root / 'files' / b['Hash'][0] / b['Hash'][:2] / b['Hash']
    if osu_file.exists():
        circles, sliders, spinners = count_objects(osu_file)
    return {
        'md5': b['MD5Hash'], 'beatmap_id': online_id, 'set_id': set_id, 'set_key': f'id:{set_id}' if set_id else f'lazer:{s["ID"]}',
        'artist': meta['Artist'], 'artist_unicode': meta['ArtistUnicode'] or None, 'title': meta['Title'], 'title_unicode': meta['TitleUnicode'] or None,
        'creator': author.get('Username'), 'difficulty': b['DifficultyName'], 'song_source': meta['Source'] or None, 'tags': tags or None,
        'audio_file': meta['AudioFile'] or None, 'mode': LAZER_MODES.get(b['Ruleset'], b['Ruleset']), 'status': LAZER_STATUS.get(b['Status'], 'unknown'),
        'stars': round(b['StarRating'], 2) if b['StarRating'] >= 0 else None,
        '_client_stars': b['StarRating'] if b['StarRating'] >= 0 else None, '_osu_path': osu_file if osu_file.exists() else None,
        'bpm': round(b['BPM'], 2) if b['BPM'] > 0 else None, 'length_s': b['Length'] / 1000 if b['Length'] > 0 else None, 'drain_s': None,
        'ar': round(diff['ApproachRate'], 2), 'cs': round(diff['CircleSize'], 2), 'od': round(diff['OverallDifficulty'], 2),
        'hp': round(diff['DrainRate'], 2), 'sv': round(diff['SliderMultiplier'], 2),
        'objects': objects, 'circles': circles, 'sliders': sliders, 'spinners': spinners,
        'local_offset': (b.get('UserSettings') or {}).get('Offset'), 'last_played': iso_to_epoch(b['LastPlayed']), 'last_modified': iso_to_epoch(b['LastLocalUpdate']),
        'date_added': iso_to_epoch(s['DateAdded']), 'date_ranked': iso_to_epoch(s['DateRanked']), 'date_submitted': iso_to_epoch(s['DateSubmitted']),
        'in_stable': 0, 'in_lazer': 1,
    }


def hit_counts(ruleset: str, stats: dict) -> dict:
    g = lambda key: stats.get(key, 0)
    if ruleset == 'fruits':
        return {'n300': g('great'), 'n100': g('large_tick_hit'), 'n50': g('small_tick_hit'), 'ngeki': 0, 'nkatu': g('small_tick_miss'), 'nmiss': g('miss') + g('large_tick_miss')}
    if ruleset == 'mania':
        return {'n300': g('great'), 'n100': g('ok'), 'n50': g('meh'), 'ngeki': g('perfect'), 'nkatu': g('good'), 'nmiss': g('miss')}
    return {'n300': g('great'), 'n100': g('ok'), 'n50': g('meh'), 'ngeki': 0, 'nkatu': 0, 'nmiss': g('miss')}


def changes_difficulty_settings(mods: list[dict]) -> bool:
    """Whether mod settings make a score's difficulty differ from the plain mods, such as DT at a custom speed."""
    for mod in mods:
        settings = mod.get('settings') or {}
        if mod['acronym'] in ('DT', 'NC') and settings.get('speed_change', 1.5) != 1.5:
            return True
        if mod['acronym'] == 'HD' and settings.get('only_fade_approach_circles'):
            return True
    return False


def score_row(s: dict, md5: str) -> dict:
    stats = json.loads(s['Statistics'] or '{}')
    raw_mods = json.loads(s['Mods'] or '[]')
    mods = normalize_mods(m['acronym'] for m in raw_mods)
    online = next((i for i in (s['OnlineID'], s['LegacyOnlineID']) if i and i > 0), None)
    full_combo = stats.get('miss', 0) == 0 and stats.get('large_tick_miss', 0) == 0 and stats.get('combo_break', 0) == 0
    return {
        'md5': md5, 'source': 'lazer', 'hash': s['Hash'] or s['ID'], 'mode': LAZER_MODES.get(s['Ruleset'], s['Ruleset']),
        'player': (s.get('User') or {}).get('Username'), 'total_score': s['TotalScore'], 'max_combo': s['MaxCombo'],
        'accuracy': round(s['Accuracy'] * 100, 4), 'grade': LAZER_GRADES.get(s['Rank'], 'D'), 'mods': ','.join(mods),
        **hit_counts(s['Ruleset'], stats),
        'perfect': int(full_combo), 'played_at': iso_to_epoch(s['Date']), 'pp': s['PP'], 'online_id': online,
        '_custom_settings': changes_difficulty_settings(raw_mods),
    }


def load(root: Path, beatmaps: dict, scores: list, collections: dict, report: dict):
    with tempfile.TemporaryDirectory(prefix='masterpiece-realm-') as tmp:
        out = Path(tmp)
        manifest = export_realm(root, out)
        read = lambda name: json.loads((out / name).read_text(encoding='utf-8'))
        sets, maps, raw_scores, raw_collections = read('beatmapsets.json'), read('beatmaps.json'), read('scores.json'), read('collections.json')

    live_sets = {s['ID']: s for s in sets if not s['DeletePending']}
    md5_by_id, md5_by_hash = {}, {}
    imported = 0
    for b in maps:
        s = live_sets.get(b['BeatmapSet'])
        if not s or not b['MD5Hash']:
            continue
        merge_beatmap(beatmaps, beatmap_row(b, s, root))
        md5_by_id[b['ID']] = md5_by_hash[b['Hash']] = b['MD5Hash']
        imported += 1

    kept = orphaned = 0
    for s in raw_scores:
        if s['DeletePending']:
            continue
        md5 = md5_by_id.get(s['BeatmapInfo']) or md5_by_hash.get(s['BeatmapHash'])
        if not md5:
            orphaned += 1
            continue
        scores.append(score_row(s, md5))
        kept += 1

    for c in raw_collections:
        add_collection(collections, c['Name'], 'lazer', c['BeatmapMD5Hashes'])

    report['lazer'] = {
        'schema_version': manifest.get('schemaVersion'), 'beatmaps': imported, 'scores': kept,
        'scores_for_missing_beatmaps': orphaned, 'collections': len(raw_collections),
    }
