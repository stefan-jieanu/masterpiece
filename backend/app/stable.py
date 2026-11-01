"""Reads osu!stable's osu!.db, collection.db and scores.db. Format versions before 20140609 are not supported."""
import struct
from pathlib import Path

from .normalize import (MODES, STABLE_GRADES, STABLE_STATUS, accuracy, add_collection, bpm_from_timing_points, grade,
                        legacy_mods, merge_beatmap, ticks_to_epoch)


class Reader:
    def __init__(self, data: bytes):
        self.d = data
        self.p = 0

    def take(self, n):
        if self.p + n > len(self.d):
            raise ValueError(f'unexpected end of file at byte {self.p}')
        chunk = self.d[self.p:self.p + n]
        self.p += n
        return chunk

    def u8(self): return self.take(1)[0]
    def flag(self): return self.u8() != 0
    def i16(self): return struct.unpack('<h', self.take(2))[0]
    def u16(self): return struct.unpack('<H', self.take(2))[0]
    def i32(self): return struct.unpack('<i', self.take(4))[0]
    def i64(self): return struct.unpack('<q', self.take(8))[0]
    def f32(self): return struct.unpack('<f', self.take(4))[0]
    def f64(self): return struct.unpack('<d', self.take(8))[0]

    def text(self):
        tag = self.u8()
        if tag == 0:
            return None
        if tag != 0x0b:
            raise ValueError(f'bad string tag {tag} at byte {self.p - 1}')
        size, shift = 0, 0
        while True:
            c = self.u8()
            size |= (c & 0x7f) << shift
            if not c & 0x80:
                break
            shift += 7
        return self.take(size).decode('utf-8', 'replace')


def parse_osu_db(path: Path) -> tuple[dict, list[dict]]:
    r = Reader(path.read_bytes())
    head = {'version': r.i32(), 'folders': r.i32()}
    r.flag(), r.i64()
    head['player'] = r.text()
    version = head['version']
    if version < 20140609:
        raise ValueError(f'osu!.db version {version} is too old to read')
    maps = []
    for _ in range(r.i32()):
        m = {}
        if version < 20191106:
            r.i32()
        for key in ('artist', 'artist_unicode', 'title', 'title_unicode', 'creator', 'difficulty', 'audio', 'md5', 'osu_file'):
            m[key] = r.text()
        m['status'] = r.u8()
        m['circles'], m['sliders'], m['spinners'] = r.u16(), r.u16(), r.u16()
        m['modified'] = r.i64()
        m['ar'], m['cs'], m['hp'], m['od'] = r.f32(), r.f32(), r.f32(), r.f32()
        m['sv'] = r.f64()
        m['stars'] = {}
        for mode in range(4):
            pairs = {}
            for _ in range(r.i32()):
                r.u8()
                mods = r.i32()
                tag = r.u8()
                pairs[mods] = r.f64() if tag == 0x0d else r.f32()
            m['stars'][mode] = pairs
        m['drain_s'], m['total_ms'], m['preview_ms'] = r.i32(), r.i32(), r.i32()
        m['timing'] = [(r.f64(), r.f64(), r.flag()) for _ in range(r.i32())]
        m['beatmap_id'], m['set_id'] = r.i32(), r.i32()
        r.i32()
        m['grades'] = [r.u8() for _ in range(4)]
        m['local_offset'] = r.i16()
        r.f32()
        m['mode'] = r.u8()
        m['source'], m['tags'] = r.text(), r.text()
        r.i16()
        r.text()
        r.flag()
        m['last_played'] = r.i64()
        r.flag()
        m['folder'] = r.text()
        r.i64()
        r.flag(), r.flag(), r.flag(), r.flag(), r.flag()
        r.i32()
        r.u8()
        maps.append(m)
    return head, maps


def parse_collection_db(path: Path) -> list[tuple[str, list[str]]]:
    r = Reader(path.read_bytes())
    r.i32()
    return [(r.text(), [r.text() for _ in range(r.i32())]) for _ in range(r.i32())]


def parse_scores_db(path: Path) -> list[dict]:
    r = Reader(path.read_bytes())
    r.i32()
    scores = []
    for _ in range(r.i32()):
        r.text()
        for _ in range(r.i32()):
            s = {'mode': r.u8()}
            r.i32()
            s['md5'], s['player'], s['replay_md5'] = r.text(), r.text(), r.text()
            s['n300'], s['n100'], s['n50'], s['ngeki'], s['nkatu'], s['nmiss'] = r.u16(), r.u16(), r.u16(), r.u16(), r.u16(), r.u16()
            s['score'] = r.i32()
            s['max_combo'] = r.u16()
            s['perfect'] = r.flag()
            s['mods'] = r.i32()
            r.text()
            s['played_at'] = r.i64()
            r.i32()
            s['online_id'] = r.i64()
            if s['mods'] & (1 << 23):
                r.f64()
            scores.append(s)
    return scores


def beatmap_row(m: dict) -> dict:
    nomod_stars = m['stars'].get(m['mode'], {}).get(0)
    set_id = m['set_id'] if m['set_id'] > 0 else None
    return {
        'md5': m['md5'], 'beatmap_id': m['beatmap_id'] if m['beatmap_id'] > 0 else None, 'set_id': set_id,
        'set_key': f'id:{set_id}' if set_id else f'stable:{m["folder"]}',
        'artist': m['artist'], 'artist_unicode': m['artist_unicode'] or None, 'title': m['title'], 'title_unicode': m['title_unicode'] or None,
        'creator': m['creator'], 'difficulty': m['difficulty'], 'song_source': m['source'] or None, 'tags': m['tags'] or None,
        'audio_file': m['audio'], 'mode': MODES[m['mode']], 'status': STABLE_STATUS.get(m['status'], 'unknown'),
        'stars': round(nomod_stars, 2) if nomod_stars is not None else None,
        'bpm': bpm_from_timing_points(m['timing'], m['total_ms']), 'length_s': m['total_ms'] / 1000, 'drain_s': m['drain_s'],
        'ar': round(m['ar'], 2), 'cs': round(m['cs'], 2), 'od': round(m['od'], 2), 'hp': round(m['hp'], 2), 'sv': round(m['sv'], 2),
        'objects': m['circles'] + m['sliders'] + m['spinners'], 'circles': m['circles'], 'sliders': m['sliders'], 'spinners': m['spinners'],
        'local_offset': m['local_offset'], 'last_played': ticks_to_epoch(m['last_played']), 'last_modified': ticks_to_epoch(m['modified']),
        'rank_achieved': STABLE_GRADES.get(m['grades'][m['mode']]), 'in_stable': 1, 'in_lazer': 0,
        '_client_stars': nomod_stars, '_folder': m['folder'], '_osu_file': m['osu_file'],
    }


def score_row(s: dict) -> dict:
    mode = MODES[s['mode']]
    mods = legacy_mods(s['mods'])
    acc = accuracy(mode, s['n300'], s['n100'], s['n50'], s['ngeki'], s['nkatu'], s['nmiss'])
    return {
        'md5': s['md5'], 'source': 'stable', 'hash': s['replay_md5'], 'mode': mode, 'player': s['player'],
        'total_score': s['score'], 'max_combo': s['max_combo'], 'accuracy': round(acc, 4) if acc is not None else None,
        'grade': grade(mode, mods, acc, s['n300'], s['n100'], s['n50'], s['nmiss']), 'mods': ','.join(mods),
        'n300': s['n300'], 'n100': s['n100'], 'n50': s['n50'], 'ngeki': s['ngeki'], 'nkatu': s['nkatu'], 'nmiss': s['nmiss'],
        'perfect': int(s['perfect']), 'played_at': ticks_to_epoch(s['played_at']), 'pp': None,
        'online_id': s['online_id'] if s['online_id'] > 0 else None,
    }


def load(root: Path, beatmaps: dict, scores: list, collections: dict, report: dict):
    osu_db = root / 'osu!.db'
    if not osu_db.exists():
        raise FileNotFoundError(f'No osu!.db in {root}')
    head, maps = parse_osu_db(osu_db)
    for m in maps:
        if m['md5']:
            merge_beatmap(beatmaps, beatmap_row(m))
    report['stable'] = {'version': head['version'], 'beatmaps': len(maps), 'scores': 0, 'collections': 0}

    if (root / 'scores.db').exists():
        rows = [score_row(s) for s in parse_scores_db(root / 'scores.db')]
        scores.extend(rows)
        report['stable']['scores'] = len(rows)
    if (root / 'collection.db').exists():
        for name, hashes in parse_collection_db(root / 'collection.db'):
            add_collection(collections, name, 'stable', hashes)
            report['stable']['collections'] += 1
