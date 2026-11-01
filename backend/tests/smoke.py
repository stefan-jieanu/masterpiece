"""End-to-end checks against a real library. Run from backend/: python -m tests.smoke [stable_dir] [lazer_dir]

Includes the defects confirmed in the demo, so they stay fixed.
"""
import _thread
import hashlib
import http.client
import io
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
from pathlib import Path

from fastapi import HTTPException

from app import fields, importer, lazer, main, query, ratings, stable, tapping, tapping_query
from app.db import SCHEMA_VERSION, connect
from app.normalize import COMBOS, SKILLS, STABLE_GRADES, accuracy, bpm_from_timing_points, grade, legacy_mods, merge_beatmap, played_suffix

failures = []


def check(label, ok, detail=''):
    print(('ok  ' if ok else 'FAIL'), label, detail)
    if not ok:
        failures.append(label)


def count(conn, q='', view='beatmaps'):
    return query.list_rows(conn, view=view, q=q, limit=1)['total']


def rejects(conn, q):
    try:
        count(conn, q)
    except query.QueryError as e:
        return str(e)
    return None


stable_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else None
lazer_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else None

# Pure logic
check('acc weights 50s', abs(accuracy('osu', 90, 0, 10, 0, 0, 0) - (300 * 90 + 50 * 10) / 30000 * 100) < 1e-9)
check('taiko acc', accuracy('taiko', 50, 50, 0, 0, 0, 0) == 75)
check('catch acc', accuracy('catch', 90, 5, 4, 0, 1, 0) == 99)
check('mania acc', accuracy('mania', 0, 0, 0, 100, 0, 0) == 100)
check('no judgements is not a crash', accuracy('osu', 0, 0, 0, 0, 0, 0) is None and grade('osu', [], None, 0, 0, 0, 0) == 'D')
check('silver SS', grade('osu', ['HD'], 100, 100, 0, 0, 0) == 'XH')
check('silver S', grade('osu', ['FL'], 99, 95, 5, 0, 0) == 'SH')
check('mania FI is silver, osu FI is not', grade('mania', ['FI'], 100, 1, 0, 0, 0) == 'XH' and grade('osu', ['FI'], 100, 1, 0, 0, 0) == 'X')
check('catch A threshold', grade('catch', [], 95, 95, 0, 0, 5) == 'A')
check('NC hides DT, PF hides SD', legacy_mods(512 + 64) == ['NC'] and legacy_mods(16384 + 32) == ['PF'])
check('bpm by duration', bpm_from_timing_points([(375.0, 0, True), (300.0, 1000, True), (-100.0, 2000, False)], 60000) == 200)
check('bpm with no timing', bpm_from_timing_points([], 1000) is None)
merged = {}
merge_beatmap(merged, {'md5': 'a', 'in_stable': 1, 'in_lazer': 0, 'stars': 5.0, 'last_played': 10, 'date_added': None})
merge_beatmap(merged, {'md5': 'a', 'in_stable': 0, 'in_lazer': 1, 'stars': 6.0, 'last_played': 20, 'date_added': 7})
check('merge keeps both flags, first value, gaps filled, latest play', merged['a'] == {'md5': 'a', 'in_stable': 1, 'in_lazer': 1, 'stars': 5.0, 'last_played': 20, 'date_added': 7})

# As-played lookup
check('as played: the 8 combinations', played_suffix([]) == '' and played_suffix(['HD', 'DT']) == '_hddt' and played_suffix(['HR', 'DT', 'HD']) == '_hdhrdt' and played_suffix(['HR']) == '_hr')
check('as played: NC counts as DT', played_suffix(['NC']) == '_dt' and played_suffix(['HD', 'NC']) == '_hddt')
check('as played: NF, SD, PF, SO and CL are ignored', played_suffix(['NF', 'HD']) == '_hd' and played_suffix(['PF', 'HR']) == '_hr' and played_suffix(['SD']) == '' and played_suffix(['SO', 'DT']) == '_dt' and played_suffix(['CL', 'HD']) == '_hd' and played_suffix(['CL']) == '')
check('as played: anything else stays blank', all(played_suffix(m) is None for m in (['EZ'], ['HT'], ['FL'], ['HD', 'FL'], ['AP'], ['RX'], ['TD'], ['DA'], ['HR', 'EZ'])))
check('custom speed and HD settings change difficulty',
      lazer.changes_difficulty_settings([{'acronym': 'DT', 'settings': {'speed_change': 1.25}}])
      and lazer.changes_difficulty_settings([{'acronym': 'NC', 'settings': {'speed_change': 1.4}}])
      and lazer.changes_difficulty_settings([{'acronym': 'HD', 'settings': {'only_fade_approach_circles': True}}]))
check('default mod settings do not', not lazer.changes_difficulty_settings([{'acronym': 'DT'}, {'acronym': 'NC', 'settings': {'speed_change': 1.5}}, {'acronym': 'HD', 'settings': {}}, {'acronym': 'NF', 'settings': {'x': 1}}]))

# Field names
for alias, target in (('tap', 'tapping'), ('speed', 'tapping'), ('read', 'reading'), ('sr', 'stars'), ('aim_nm', 'aim'), ('aim_dthd', 'aim_hddt'), ('aim_hdnc', 'aim_hddt'), ('tap_hrdt', 'tapping_hrdt'),
                      ('speed_dthr', 'tapping_hrdt'), ('read_hrhd', 'reading_hdhr'), ('reading_nchrhd', 'reading_hdhrdt'), ('sr_dt', 'stars_dt'), ('star_rating_hr', 'stars_hr'), ('played_tap', 'played_tapping')):
    check(f'{alias} means {target}', fields.get(alias) is fields.get(target))
check('32 rating fields plus 4 as played', sum(f.get('column') is not None for f in fields.FIELDS) == 36)

# The Tapping tab's calculations, on small maps at 120 BPM unless stated, where 1/4 of a beat is 125 ms
def tap_map(objects, timing=((0, 500.0),), extra=()):
    """A minimal .osu file. Each object is a time, or (time, hit object type) for sliders and spinners."""
    lines = ['osu file format v14', '', '[TimingPoints]', *(f'{offset},{beat},4,2,1,60,1,0' for offset, beat in timing), *extra, '', '[HitObjects]']
    for item in objects:
        when, kind = item if isinstance(item, tuple) else (item, 1)
        lines.append(f'256,192,{when},{kind},0')
    return '\n'.join(lines).encode()


def tap_run(start, taps, gap=125.0):
    return [start + i * gap for i in range(taps)]


sizes = tapping.analyse(tap_map([*tap_run(0, 2), *tap_run(1000, 3), *tap_run(2000, 4), *tap_run(3000, 8), *tap_run(5000, 9)]))
check('2 taps is a double, 3 a triple, 4 to 8 a burst, 9+ a stream', (sizes['doubles'], sizes['triples'], sizes['bursts'], sizes['streams']) == (1, 1, 2, 1), str(sizes.get('status')))
check('doubles and triples share a type of group', sizes['kinds']['dt']['count'] == 2 and sizes['kinds']['burst']['count'] == 2 and sizes['kinds']['stream']['count'] == 1)
check('the longest group is tracked', sizes['longest'] == 9 and sizes['kinds']['burst']['longest'] == 8)
slider = tapping.analyse(tap_map([0, (125, 2), 250, 1000]))
check('a slider counts as a tap, whatever its length', (slider['triples'], slider['doubles']) == (1, 0))
spinner = tapping.analyse(tap_map([0, 125, (250, 8), 375, 500]))
check('a spinner has no tap, so it splits a group', (spinner['doubles'], spinner['triples']) == (2, 0) and spinner['groups'] == [[0, 125, 2], [375, 500, 2]])
other = [tap_run(0, 20, gap) for gap in (250.0, 166.67, 83.33, 62.5)]
check('1/2, 1/3, 1/6 and 1/8 are not 1/4', all(tapping.analyse(tap_map(objects))['status'] == 'no_quarter' for objects in other))
check('an empty map has no 1/4 rhythms', tapping.analyse(b'osu file format v14\n')['status'] == 'no_quarter')
check('rounding of a millisecond is tolerated', tapping.analyse(tap_map([0, 124, 250, 376, 500]))['bursts'] == 1)
check('fast maps stay within rounding', tapping.analyse(tap_map([round(i * 41.667) for i in range(12)], timing=((0, 166.667),)))['streams'] == 1)
check('a stream at 110 BPM is a stream, so BPM never decides the type', tapping.analyse(tap_map(tap_run(0, 12, 136.36), timing=((0, 545.45),)))['streams'] == 1)
check('a gap slightly off 1/4 breaks the run', tapping.analyse(tap_map([0, 125, 250, 400, 525]))['groups'] == [[0, 250, 3], [400, 525, 2]])
check('a group can run to the very last object', tapping.analyse(tap_map([*tap_run(0, 2), *tap_run(1000, 4)]))['groups'] == [[0, 125, 2], [1000, 1375, 4]])

changes = lambda *beats: tuple((i * 1000, beat) for i, beat in enumerate(beats))
many = tapping.analyse(tap_map(tap_run(5000, 12), timing=changes(500, 400, 300, 250)))
check('more than 2 BPM changes is ignored, and says how many', many['status'] == 'many_bpm_changes' and many['bpm_changes'] == 3 and many['bpm_changes_in_play'] == 0)
check('exactly 2 BPM changes is still rated', tapping.analyse(tap_map(tap_run(5000, 12), timing=changes(500, 400, 500)))['status'] == 'rated')
check('a red line repeating the same BPM is not a change', tapping.analyse(tap_map(tap_run(0, 12), timing=changes(500, 500, 500, 500, 500)))['bpm_changes'] == 0)
check('green lines are not BPM changes', tapping.analyse(tap_map(tap_run(0, 12), extra=[f'{i * 100},-50,4,2,1,60,0,0' for i in range(1, 8)]))['bpm_changes'] == 0)
check('old timing lines without the inherited column are understood', tapping.parse_osu(b'[TimingPoints]\n0,500\n1000,-100\n')[0] == [(0.0, 500.0)])
flip = tapping.analyse(tap_map(tap_run(5000, 12), timing=changes(500, 250, 500, 250)))
check('going back and forth counts every change', flip['status'] == 'many_bpm_changes' and flip['bpm_changes'] == 3)
inside = tapping.analyse(tap_map(tap_run(1500, 12), timing=changes(500, 500, 400, 300)))
check('changes between first and last tap are counted apart', inside['bpm_changes'] == 2 and inside['bpm_changes_in_play'] == 1, str(inside['bpm_changes_in_play']))
check('broken lines are skipped, not fatal', tapping.parse_osu(b'[TimingPoints]\nx,y\nnan,500\n0,inf\n0,500\n[HitObjects]\n1,2\n1,2,z,1\n256,192,1e99,1,0\n256,192,10,1,0\n') == ([(0.0, 500.0)], [(10.0, True)]))
check('a gimmick beat length cannot overflow anything', tapping.analyse(tap_map(tap_run(0, 12), timing=((0, 1e-300),)))['status'] in ('rated', 'no_quarter'))

crossing = tapping.analyse(tap_map([*tap_run(0, 9), 1500, *tap_run(3000, 9, 62.5)], timing=((0, 500.0), (2000, 250.0))))
# From 1000 to 2000 at 500 ms a beat is 2 beats, and from 2000 to 3000 at 250 ms a beat is 4 more.
check('distances are in beats across a BPM change', crossing['kinds']['stream']['gaps'] == [6.0], str(crossing['kinds']['stream']['gaps']))
check('distance sizes', [tapping.bin_index(d) for d in (0.5, 0.99, 1, 1.5, 2, 3.75, 4, 7.5, 8, 64)] == [0, 1, 1, 1, 2, 2, 3, 3, 4, 4])
check('a millisecond of rounding cannot change a size', tapping.bin_index(0.998) == 1 and tapping.bin_index(1.998) == 2)
same_type = tapping.analyse(tap_map([*tap_run(0, 2), *tap_run(1000, 3), 5000]))
check('distances run between a double and a triple', same_type['kinds']['dt']['gaps'] == [1.75], str(same_type['kinds']['dt']['gaps']))

# One tap at 0, a stream from 8000 to 9000, another from 9250 to 10250 (half a beat later), and a last tap 8 beats after that.
versions = tapping.analyse(tap_map([0, *tap_run(8000, 9), *tap_run(9250, 9), 14250]))['kinds']['stream']
values = {key: entry['raw'] for key, entry in versions['versions'].items()}
check('A counts the distance and the song start', values['a'] == round((10 + 1) / 2, 4), str(values))
check('B counts only the distance between groups', values['b'] == 10)
check('C counts the distance and both ends', values['c'] == round((10 + 1 + 1) / 3, 4))
check('D counts every beat of the map', values['d'] == round((4.5 * 10 + 24 * 1) / 28.5, 4))
check('E leaves out the beats inside groups', values['e'] == round((0.5 * 10 + 24 * 1) / 24.5, 4))
check('shares add up to 1', all(abs(sum(entry['shares']) - 1) < 0.001 for entry in versions['versions'].values()))
check('the edge distances are reported', (versions['song_lead'], versions['first_lead'], versions['tail']) == (16.0, 16.0, 8.0) and versions['distances'] == [1, 0, 0, 0, 0])
single = tapping.analyse(tap_map([0, *tap_run(8000, 9), 20000]))['kinds']['stream']['versions']
check('one group has no distance between groups, so B gives the lowest value', single['b']['raw'] == 1 and single['b']['shares'] is None)
ends_on_stream = tapping.analyse(tap_map([0, *tap_run(8000, 9), *tap_run(40000, 9)]))['kinds']['stream']['versions']
check("a map ending on a group looks closer to C than to A, which is C's flaw", ends_on_stream['c']['raw'] > ends_on_stream['a']['raw'] == 1)
late_red_line = tapping.analyse(tap_map(tap_run(0, 12), timing=((1000, 500.0),)))
check('before the first red line its BPM applies', late_red_line['streams'] == 1 and late_red_line['kinds']['stream']['song_lead'] == 0)
only_group = tapping.analyse(tap_map(tap_run(0, 12)))['kinds']['stream']['versions']
check('a map that is one stream is the top value in D and the lowest in E', only_group['d']['raw'] == 10 and only_group['e']['raw'] == 1)

scaled_maps = [{'kinds': {'stream': {'versions': {key: {'raw': raw} for key in tapping.VERSION_KEYS}}}} for raw in (1.0, 2.0, 3.0, 5.0)]
tops = tapping.add_stretched_values(scaled_maps)
entries = [m['kinds']['stream']['versions']['a'] for m in scaled_maps]
check('stretching reaches 10 at the top, keeps 1 and stays in order', tops['a_stream'] == 5 and [e['scaled'] for e in entries] == [1, 3.25, 5.5, 10], str([e['scaled'] for e in entries]))
check('stretching is capped at 10', tapping.stretched(7, 5) == 10 and tapping.stretched(1, 1) == 1)
check('no Tapping value is a field of the other tabs', not any(f['key'].startswith(('stream', 'burst', 'double', 'triple')) or 'tapping.' in f['sql'] or 't.' == f['sql'][:2] for f in fields.FIELDS))

STUB = '''
import sys, time
if '--describe' in sys.argv:
    print('stub-1\\t0,8,64,72,16,24,80,88')
    sys.exit()
for line in sys.stdin:
    if 'slow' in line:
        time.sleep(0.2)
    if 'hang' in line:
        time.sleep(60)
    if 'crash' in line:
        sys.exit(3)
    if 'skip' in line:
        print('skip\\tsuspicious', flush=True)
    elif 'garbage' in line:
        print('ok\\tnot numbers', flush=True)
    else:
        print('ok\\t' + '\\t'.join(['1.5'] * 32), flush=True)
'''

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    (tmp / 'stub.py').write_text(STUB)
    command = [sys.executable, str(tmp / 'stub.py')]

    # Calculator process handling
    check('calculator identity is read', ratings.describe(command) == 'stub-1')
    try:
        ratings.describe(['no-such-calculator'])
        hint = False
    except RuntimeError as e:
        hint = 'cargo build' in str(e)
    check('missing calculator explains how to build it', hint)
    started = time.perf_counter()
    done, why = ratings.calculate_all({'a': 'ok-a', 'b': 'hang', 'c': 'crash', 'd': 'skip', 'e': 'garbage', 'f': 'ok-f', 'g': 'line\nbreak'}, command, timeout=3, workers=2)
    check('hung and crashed calculators only lose their own map', set(done) == {'a', 'f'} and why == {'b': 'timeout', 'c': 'crashed', 'd': 'suspicious', 'e': 'bad_output', 'g': 'bad_path'}, f'{why} {time.perf_counter() - started:.1f}s')

    partial, interrupted = {}, False
    started = time.perf_counter()
    threading.Timer(0.8, _thread.interrupt_main).start()
    try:
        ratings.calculate_all({f'm{i}': f'slow-{i}' for i in range(200)}, command, timeout=3, workers=2, results=partial)
    except KeyboardInterrupt:
        interrupted = True
    elapsed = time.perf_counter() - started
    check('Ctrl-C stops calculating soon and keeps finished work', interrupted and 0 < len(partial) < 200 and elapsed < 3, f'{len(partial)} kept, stopped after {elapsed:.1f}s')

    # Finding .osu files
    good = b'osu file format v14\n'
    good_md5 = hashlib.md5(good).hexdigest()
    requests = []

    class BrokenBody(io.BytesIO):
        def read(self, *args):
            raise http.client.IncompleteRead(b'partial')

    def fake_urlopen(request, timeout=0):
        requests.append(request.full_url)
        beatmap = int(request.full_url.rsplit('/', 1)[1])
        if beatmap in (404, 429):
            raise urllib.error.HTTPError(request.full_url, beatmap, 'error', None, None)
        if beatmap == 500:
            raise urllib.error.URLError('no network')
        if beatmap == 600:
            return io.BytesIO(b'')
        if beatmap == 601:
            return io.BytesIO(b'<html>Sign in to this network</html>')
        if beatmap == 602:
            return BrokenBody()
        if beatmap == 603:
            return io.BytesIO(b'\xef\xbb\xbf' + good)
        return io.BytesIO(good)

    real_urlopen = ratings.urllib.request.urlopen
    ratings.urllib.request.urlopen = fake_urlopen
    store = ratings.Store(tmp / 'cache.sqlite', 'v1')
    files = ratings.OsuFiles(None, tmp / 'osu', store, True, interval=0)
    path, reason = files.download({'md5': good_md5, 'beatmap_id': 1})
    check('download is saved when its MD5 matches', path and path.read_bytes() == good and reason is None)
    check('a downloaded file is found locally next time', files.find_local({'md5': good_md5}) == (path, 'cache'))
    check('a map updated online is refused', files.download({'md5': '0' * 32, 'beatmap_id': 2}) == (None, 'updated_online') and not (tmp / 'osu' / f"{'0' * 32}.osu").exists())
    check('not found online', files.download({'md5': '1' * 32, 'beatmap_id': 404}) == (None, 'not_online'))
    check('no online id means no request', files.download({'md5': '2' * 32, 'beatmap_id': None}) == (None, 'no_online_id') and len(requests) == 3)
    again = ratings.OsuFiles(None, tmp / 'osu', store, True, interval=0)
    check('misses are not retried soon', again.download({'md5': '0' * 32, 'beatmap_id': 2}) == (None, 'updated_online') and len(requests) == 3)
    limited = ratings.OsuFiles(None, tmp / 'osu', store, True, interval=0)
    check('rate limiting stops downloads for the run', limited.download({'md5': '3' * 32, 'beatmap_id': 429}) == (None, 'download_stopped') and limited.download({'md5': '4' * 32, 'beatmap_id': 5}) == (None, 'download_stopped') and len(requests) == 4)
    offline = ratings.OsuFiles(None, tmp / 'osu', store, True, interval=0)
    check('network trouble stops downloads for the run', offline.download({'md5': '5' * 32, 'beatmap_id': 500}) == (None, 'download_stopped') and offline.stop_reason)
    no_download = ratings.OsuFiles(None, tmp / 'osu', store, False, interval=0)
    check('downloads can be turned off', no_download.download({'md5': '6' * 32, 'beatmap_id': 1}) == (None, 'download_disabled'))
    empty = ratings.OsuFiles(None, tmp / 'osu', store, True, interval=0)
    check("an empty reply means the map isn't online, and downloads continue", empty.download({'md5': '7' * 32, 'beatmap_id': 600}) == (None, 'not_online') and not empty.stop_reason)
    portal = ratings.OsuFiles(None, tmp / 'osu', store, True, interval=0)
    check('a page that is not a beatmap stops downloads and blames no map', portal.download({'md5': '8' * 32, 'beatmap_id': 601}) == (None, 'download_stopped') and portal.stop_reason and '8' * 32 not in store.misses())
    dropped = ratings.OsuFiles(None, tmp / 'osu', store, True, interval=0)
    check('a dropped connection stops downloads instead of crashing', dropped.download({'md5': '9' * 32, 'beatmap_id': 602}) == (None, 'download_stopped') and dropped.stop_reason)
    with_bom = b'\xef\xbb\xbf' + good
    bom = ratings.OsuFiles(None, tmp / 'osu', store, True, interval=0)
    check('a beatmap starting with a byte order mark is accepted', bom.download({'md5': hashlib.md5(with_bom).hexdigest(), 'beatmap_id': 603})[0] is not None)
    ratings.urllib.request.urlopen = real_urlopen

    songs = tmp / 'Songs' / 'set'
    songs.mkdir(parents=True)
    (songs / 'a.osu').write_bytes(good)
    (songs / 'edited.osu').write_bytes(b'something else')
    with_songs = ratings.OsuFiles(tmp / 'Songs', tmp / 'osu', store, False, interval=0)
    check('Songs folder wins over the cache', with_songs.find_local({'md5': good_md5, '_folder': 'set', '_osu_file': 'a.osu'})[1] == 'songs')
    check('a Songs file with the wrong MD5 is not used', with_songs.find_local({'md5': good_md5, '_folder': 'set', '_osu_file': 'edited.osu'})[1] == 'cache')
    check('lazer file store wins over Songs', with_songs.find_local({'md5': good_md5, '_osu_path': songs / 'a.osu', '_folder': 'set', '_osu_file': 'a.osu'})[1] == 'lazer')
    check('a damaged lazer file is not used, and a good copy is', with_songs.find_local({'md5': good_md5, '_osu_path': songs / 'edited.osu', '_folder': 'set', '_osu_file': 'a.osu'})[1] == 'songs' and with_songs.rejected['lazer'] == 1)
    (tmp / 'Songs' / 'loose.osu').write_bytes(good)
    check('a map directly in Songs is found', with_songs.find_local({'md5': good_md5, '_folder': '', '_osu_file': 'loose.osu'}) == (tmp / 'Songs' / 'loose.osu', 'songs'))
    check('a backslash in the folder is understood', with_songs.find_local({'md5': good_md5, '_folder': 'set\\', '_osu_file': 'a.osu'})[1] == 'songs')
    (tmp / 'osu' / f"{'a' * 32}.osu").write_bytes(b'osu file format v14\ndamaged')
    damaged = ratings.OsuFiles(None, tmp / 'osu', store, False, interval=0)
    check('a damaged cached file is not used', damaged.find_local({'md5': 'a' * 32}) == (None, None))
    jobs, reasons, _ = ratings.locate_files([{'md5': '../../evil', 'beatmap_id': 1}], with_songs)
    check('a malformed MD5 never becomes a file path', not jobs and reasons == {'../../evil': 'bad_md5'})
    store.close()

    # Whole rating step
    def beatmap(md5, mode='osu', stars=4.0, **extra):
        return {'md5': md5, 'mode': mode, 'stars': stars, '_client_stars': stars, 'artist': 'a', 'title': 't', 'difficulty': 'd', 'beatmap_id': None, **extra}

    def osu_file(name):
        path = tmp / name
        path.write_bytes(f'osu file format v14\n{name}'.encode())
        return path, hashlib.md5(path.read_bytes()).hexdigest()

    ok_path, ok_md5 = osu_file('ok-file')
    skip_path, skip_md5 = osu_file('skip-file')
    maps = {
        ok_md5: beatmap(ok_md5, _osu_path=ok_path),
        'b' * 32: beatmap('b' * 32, mode='taiko', stars=3.0),
        'c' * 32: beatmap('c' * 32, stars=2.5),
        skip_md5: beatmap(skip_md5, _osu_path=skip_path),
    }
    plays = [
        {'md5': ok_md5, 'mode': 'osu', 'mods': 'HD,NC'},
        {'md5': ok_md5, 'mode': 'osu', 'mods': 'AP'},
        {'md5': ok_md5, 'mode': 'osu', 'mods': 'DT', '_custom_settings': True},
        {'md5': ok_md5, 'mode': 'taiko', 'mods': ''},
        {'md5': 'b' * 32, 'mode': 'taiko', 'mods': ''},
        {'md5': 'c' * 32, 'mode': 'osu', 'mods': ''},
        {'md5': 'c' * 32, 'mode': 'osu', 'mods': 'DT'},
    ]
    part = ratings.apply(maps, plays, None, tmp / 'data', False, command=command, timeout=3)
    rated, taiko, unrated, skipped = maps[ok_md5], maps['b' * 32], maps['c' * 32], maps[skip_md5]
    check('a calculated map gets the calculator stars and all 32 values', rated['stars'] == 1.5 and all(rated[c] == 1.5 for c in ratings.RATING_COLUMNS) and rated.get('rating_status') is None)
    check('other modes keep stored stars and stay blank', taiko['stars'] == 3.0 and taiko.get('aim') is None and taiko.get('stars_dt') is None and taiko.get('rating_status') is None)
    check('an uncalculated map keeps stored nomod stars, modded empty', unrated['stars'] == 2.5 and unrated.get('stars_dt') is None and unrated.get('aim') is None and unrated['rating_status'] == 'no_online_id')
    check('a skipped map is treated the same', skipped['stars'] == 4.0 and skipped.get('aim') is None and skipped['rating_status'] == 'suspicious')
    check('reasons are reported', part['not_rated'] == {'no_online_id': 1, 'suspicious': 1} and len(part['not_rated_examples']) == 2 and part['rated'] == 1, str(part['not_rated']))
    check('NC score is rated as DT', plays[0]['played_aim'] == rated['aim_hddt'] and plays[0]['played_stars'] == rated['stars_hddt'])
    check('AP and custom speed scores stay blank', all(plays[i].get('played_aim') is None and plays[i].get('played_stars') is None for i in (1, 2)))
    check('converted and other-mode scores stay blank', all(plays[i].get('played_aim') is None for i in (3, 4)))
    check('as played on an uncalculated map: nomod falls back like the map, modded blank', plays[5].get('played_stars') == 2.5 and plays[5].get('played_aim') is None and plays[6].get('played_stars') is None)
    second = ratings.apply({ok_md5: beatmap(ok_md5, _osu_path=ok_path)}, [], None, tmp / 'data', False, command=command, timeout=3)
    check('ratings come from the cache on re-import', second['from_cache'] == 1 and second['calculated'] == 0)
    changed = ratings.apply({ok_md5: beatmap(ok_md5, _osu_path=ok_path, _client_stars=1.0)}, [], None, tmp / 'data', False, command=command, timeout=3)
    check('drift from the client is warned about', 'warnings' in changed and changed['stars_vs_client']['over_0.05'] == 1)
    corrupt_path, _ = osu_file('corrupt-file')
    corrupt = {'e' * 32: beatmap('e' * 32, _osu_path=corrupt_path)}
    part = ratings.apply(corrupt, [], None, tmp / 'data', False, command=command, timeout=3)
    check('a damaged lazer file gives no rating and stored stars stay', corrupt['e' * 32]['stars'] == 4.0 and corrupt['e' * 32].get('aim') is None and part['local_files_with_wrong_md5'] == {'lazer': 1})

    # The Tapping tab only reads what's on this machine
    tap_file = tmp / 'tap-file'
    tap_file.write_bytes(tap_map(tap_run(0, 12)))
    tap_md5 = hashlib.md5(tap_file.read_bytes()).hexdigest()
    (tmp / 'wrong-file').write_bytes(b'not this map')
    calls = []
    ratings.urllib.request.urlopen = lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError('no downloads'))
    tap_rows, tap_report = tapping.apply({
        tap_md5: {'md5': tap_md5, 'mode': 'osu', '_osu_path': tap_file},
        'd' * 32: {'md5': 'd' * 32, 'mode': 'osu', 'beatmap_id': 5},
        'f' * 32: {'md5': 'f' * 32, 'mode': 'osu', '_osu_path': tmp / 'wrong-file'},
        '1' * 32: {'md5': '1' * 32, 'mode': 'taiko', '_osu_path': tap_file},
        '../../evil': {'md5': '../../evil', 'mode': 'osu'},
    }, None, tmp / 'no-cache')
    ratings.urllib.request.urlopen = real_urlopen
    by_md5 = {row['md5']: row for row in tap_rows}
    check('nothing is ever downloaded for the Tapping tab', not calls)
    check('a local file is analysed and says where it came from', by_md5[tap_md5]['status'] == 'rated' and by_md5[tap_md5]['source'] == 'lazer' and tap_report['osu_files'] == {'lazer': 1})
    check('a map with no local file is marked as ignored', by_md5['d' * 32]['status'] == 'no_local_file' and by_md5['d' * 32].get('a_stream') is None)
    check('a file with the wrong MD5 is not used', by_md5['f' * 32]['status'] == 'no_local_file')
    check('other modes are marked as ignored', by_md5['1' * 32]['status'] == 'not_std')
    check('a malformed MD5 never becomes a path', by_md5['../../evil']['status'] == 'no_local_file')
    check('the report counts maps by status', tap_report['maps'] == {'rated': 1, 'no_local_file': 3, 'not_std': 1})
    check('skipping marks every map', {row['status'] for row in tapping.skipped({'x': {'md5': 'x'}})} == {'skipped'})

    # Re-importing while a server is running
    target = tmp / 'library.sqlite'
    conn_writer = __import__('sqlite3').connect(target)
    conn_writer.executescript('CREATE TABLE t (a); CREATE TABLE import_info (key TEXT PRIMARY KEY, value TEXT);')
    conn_writer.close()
    main.DB_PATH = target
    try:
        with main.library():
            told = ''
    except HTTPException as e:
        told = e.detail if e.status_code == 503 else ''
    check('a library from an older version asks for a re-import', 'importer again' in told, told[:60])
    conn_writer = __import__('sqlite3').connect(target)
    conn_writer.execute('INSERT INTO import_info VALUES (?, ?)', ('schema_version', str(SCHEMA_VERSION)))
    conn_writer.commit()
    conn_writer.close()
    with main.library() as served:
        served.execute('SELECT 1').fetchall()
    shutil.copyfile(target, tmp / 'new.tmp')
    importer.replace_library(tmp / 'new.tmp', target, attempts=1)
    check('a finished request leaves the library replaceable', not (tmp / 'new.tmp').exists() and target.exists())
    if os.name == 'nt':
        held = connect(target)
        shutil.copyfile(target, tmp / 'new.tmp')
        try:
            importer.replace_library(tmp / 'new.tmp', target, attempts=2, delay=0.01)
            message = ''
        except RuntimeError as e:
            message = str(e)
        held.close()
        check('a held library gives a clear error and cleans up', 'Stop any running server' in message and not (tmp / 'new.tmp').exists())

with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / 'library.sqlite'
    report = importer.run(stable_dir, lazer_dir, db)
    print(report['library'])
    conn = connect(db)
    total = count(conn)
    check('library not empty', total > 0, f'{total} beatmaps')

    # Every field must filter-parse, sort and group, in both views, for every bucket option.
    samples = {'text': 'a', 'number': '1', 'duration': '60', 'date': '2026', 'bool': 'yes', 'mods': 'HD', 'tag': 'a', 'collection': 'a'}
    bad = []
    for f in fields.FIELDS:
        value = f['values'][0][0] if f['type'] == 'enum' else samples[f['type']]
        for view in ('beatmaps', 'scores'):
            try:
                query.list_rows(conn, view=view, q=f'{f["key"]}={value}', limit=1)
                if f['type'] not in fields.MULTI_VALUED:
                    query.list_rows(conn, view=view, sort=[{'key': f['key'], 'dir': 'desc'}], limit=1)
                for option in fields.bucket_options(f):
                    result = query.groups(conn, f['key'], option['value'], view)
                    keys = [r['key'] for r in result['rows']][:2]
                    query.list_rows(conn, view=view, group={'by': f['key'], 'bucket': option['value'], 'keys': keys}, limit=1)
            except Exception as e:
                bad.append(f'{f["key"]}/{view}: {e}')
    check(f'all {len(fields.FIELDS)} fields filter, sort and group in both views', not bad, '; '.join(bad[:5]))

    # Bucket counts must add up: numeric and enum grouping partitions the library.
    for by in ('stars', 'bpm', 'status', 'mode', 'length', 'grade', 'aim', 'tapping_dt', 'reading_hd', 'stars_hdhrdt', 'played_aim'):
        rows = query.groups(conn, by)['rows']
        check(f'group {by} partitions the library', sum(r['total'] for r in rows) == total, f'{len(rows)} buckets')

    # Demo defects, now fixed
    check('numeric = is exact, not substring', count(conn, 'bpm=18') == 0 or count(conn, 'bpm=18') < count(conn, 'bpm>=18 bpm<19') + 1)
    exact = query.list_rows(conn, q='bpm=180', limit=1000)['rows']
    check('bpm=180 only matches about 180', all(179.5 <= r['bpm'] < 180.5 for r in exact), f'{len(exact)} rows')
    check('<= and >= parse', count(conn, 'ar<=9') + count(conn, 'ar>9') == count(conn, 'ar>=0'))
    check('misses and num_misses agree', count(conn, 'misses=0') == count(conn, 'num_misses=0') > 0)
    check('misses=0 is not a substring match', count(conn, 'misses=0') == count(conn, 'misses<1'))
    check('search matches words across columns', count(conn, 'prince doll') == count(conn, '"prince" "doll"') > 0 if stable_dir else True)
    check('bad date is an error, not ignored', rejects(conn, 'last_played>yesterday'))
    check('unknown field is an error', rejects(conn, 'nonsense=1'))
    check('unknown enum value is an error', rejects(conn, 'status=rankedd'))
    check('rx2-style unknown mod does not crash', count(conn, 'mods:XX') == 0)
    check('quoted values', count(conn, 'artist="a"') >= 0)
    check('duration units agree', count(conn, 'length<2m30s') == count(conn, 'length<150') == count(conn, 'length<2:30'))
    check('grade order', count(conn, 'grade>=S', 'scores') == count(conn, 'grade=S', 'scores') + count(conn, 'grade=SS', 'scores'))
    check('score filters pick scores, not just the best', count(conn, 'mods:HD') >= query.list_rows(conn, group={'by': 'mods', 'keys': ['HD']}, limit=1)['total'])
    check('DT filter includes NC', count(conn, 'mods:DT', 'scores') >= count(conn, 'mods:NC', 'scores'))
    hd = query.list_rows(conn, view='scores', q='mods:HD', limit=1000)['rows']
    check('mods:HD rows all contain HD', all('HD' in r['s_mods'].split(',') for r in hd), f'{len(hd)} scores')

    # Pinning a bucket must select the same rows as the bucket itself.
    mismatches = []
    for by, bucket in (('stars', '1'), ('bpm', '20'), ('status', 'value'), ('length', '60'), ('creator', 'value'), ('mods', 'value'), ('grade', 'value'), ('last_played', 'month'), ('tag', 'value'), ('collection', 'value')):
        for row in query.groups(conn, by, bucket)['rows'][:6]:
            if row['pin'] and count(conn, row['pin']) != row['total']:
                # A pinned score filter may match more beatmaps than the attached-best bucket: it asks "has any such score".
                if not fields.get(by)['score'] and fields.get(by)['type'] != 'tag':
                    mismatches.append(f"{row['pin']}: pin={count(conn, row['pin'])} bucket={row['total']}")
    check('pins match their buckets', not mismatches, '; '.join(mismatches[:4]))

    if lazer_dir and stable_dir:
        check('both sources present', count(conn, 'library=stable') > 0 and count(conn, 'library=lazer') > 0)
        check('score sources present', count(conn, 'score_source=stable', 'scores') > 0 and count(conn, 'score_source=lazer', 'scores') > 0)
    if lazer_dir:
        check('lazer object split matches its totals', count(conn, 'library=lazer circles>=0') > 0)

    # Ratings, on real data
    part = report['ratings']
    print('ratings report:', {k: part.get(k) for k in ('calculator', 'std_maps', 'rated', 'from_cache', 'calculated', 'osu_files', 'not_rated', 'stars_vs_client', 'seconds')})
    std_total = conn.execute("SELECT COUNT(*) FROM beatmap WHERE mode = 'osu'").fetchone()[0]
    rows = conn.execute('SELECT * FROM beatmap WHERE aim IS NOT NULL').fetchall()
    check('nearly every std map is rated', len(rows) >= std_total * 0.99 and part['rated'] == len(rows), f'{len(rows)}/{std_total}')
    check('other modes have no calculated ratings', conn.execute("SELECT COUNT(*) FROM beatmap WHERE mode != 'osu' AND (aim IS NOT NULL OR reading IS NOT NULL OR stars_dt IS NOT NULL)").fetchone()[0] == 0)
    check('modded values are only blank together with nomod', conn.execute('SELECT COUNT(*) FROM beatmap WHERE aim IS NULL AND (aim_dt IS NOT NULL OR stars_hdhrdt IS NOT NULL)').fetchone()[0] == 0)
    check("calculated nomod stars match what the clients stored", part['stars_vs_client']['max'] <= 0.01, str(part['stars_vs_client']))
    check('no drift warning on current data', 'warnings' not in part, str(part.get('warnings')))

    # The calculator's own formula: stars = 1.6485 * (aim^3.3 + speed^3.3 + reading^3.3)^(1/3.3), up to the rounding of stored values.
    worst = 0
    for row in rows:
        for suffix, *_ in COMBOS:
            parts = [row[f'{skill}{suffix}'] for skill in SKILLS[1:]]
            worst = max(worst, abs(1.6485 * sum(p ** 3.3 for p in parts) ** (1 / 3.3) - row[f'stars{suffix}']))
    check('stars recombine from aim, tapping and reading', worst <= 0.03, f'worst {worst:.4f}')
    same = all(row[f'{skill}{a}'] == row[f'{skill}{b}'] for row in rows for skill in ('aim', 'tapping') for a, b in (('', '_hd'), ('_dt', '_hddt'), ('_hr', '_hdhr'), ('_hrdt', '_hdhrdt')))
    check('HD changes neither aim nor tapping', same)
    check('HD raises reading on most maps', sum(row['reading_hd'] > row['reading'] for row in rows) > len(rows) * 0.8)
    check('DT raises aim and tapping', all(row['aim_dt'] >= row['aim'] and row['tapping_dt'] >= row['tapping'] for row in rows))

    # Filter, alias and sort behaviour
    direct = lambda column, value: conn.execute(f'SELECT COUNT(*) FROM beatmap WHERE {column} >= ?', (value,)).fetchone()[0]
    check('aim_dt filter', count(conn, 'aim_dt>=3') == direct('aim_dt', 3) > 0, str(direct('aim_dt', 3)))
    check('tapping and speed agree', count(conn, 'tapping_hr>=2') == count(conn, 'speed_hr>=2') == count(conn, 'tap_hr>=2') == direct('tapping_hr', 2))
    check('mixing mods in one query', count(conn, 'aim_dt>=3 reading_hd>=2') == conn.execute('SELECT COUNT(*) FROM beatmap WHERE aim_dt >= 3 AND reading_hd >= 2').fetchone()[0])
    check('a blank rating never matches', conn.execute("SELECT COUNT(*) FROM beatmap WHERE aim IS NULL").fetchone()[0] == count(conn, '') - count(conn, 'aim>=0'))
    ordered = [r['aim_dt'] for r in query.list_rows(conn, sort=[{'key': 'aim_dt', 'dir': 'desc'}], limit=1000)['rows']]
    check('sorting by a modded rating', ordered == sorted(ordered, reverse=True) and ordered[0] is not None)

    # As played, on real scores
    played = conn.execute('SELECT s.*, b.mode AS map_mode, ' + ', '.join(f'b.{skill}{c[0]} AS {skill}{c[0]}' for skill in SKILLS for c in COMBOS) + ' FROM score s JOIN beatmap b ON b.pk = s.beatmap_pk').fetchall()
    wrong = blank = 0
    for s in played:
        suffix = played_suffix(s['mods'].split(',') if s['mods'] else [])
        if s['played_stars'] is None:
            blank += 1
            continue
        if suffix is None or any(s[f'played_{skill}'] != s[f'{skill}{suffix}'] for skill in SKILLS):
            wrong += 1
    check('every as-played value matches its mods', wrong == 0, f'{len(played) - blank}/{len(played)} scores rated')
    check('as-played fields filter', count(conn, 'played_aim>=3', 'scores') == conn.execute('SELECT COUNT(*) FROM score WHERE played_aim >= 3').fetchone()[0] > 0)
    labels = {r['label']: r['total'] for r in query.groups(conn, 'played_aim', view='scores')['rows']}
    check('unrated scores are not called "no score"', '(no score)' not in labels and labels.get('(not rated)') == blank, str(labels.get('(not rated)')))
    check('other blank score values are not either', all(r['label'] != '(no score)' for by in ('pp', 'accuracy') for r in query.groups(conn, by, view='scores')['rows']))
    check('maps with no score still say so', any(r['label'] == '(no score)' for r in query.groups(conn, 'grade')['rows']))
    first = conn.execute('SELECT md5, title FROM beatmap LIMIT 1').fetchone()
    check('details are found by MD5', query.beatmap_detail(conn, first['md5'])['beatmap']['title'] == first['title'] and query.beatmap_detail(conn, 'nope') is None)

    # The Tapping tab, on real maps
    tap = report['tapping']
    print('tapping report:', {k: tap.get(k) for k in ('maps', 'osu_files', 'seconds')})
    tap_counts = dict(conn.execute('SELECT status, COUNT(*) FROM tapping GROUP BY status').fetchall())
    check('every beatmap has a Tapping row', sum(tap_counts.values()) == total and conn.execute('SELECT COUNT(*) FROM beatmap b JOIN tapping t ON t.md5 = b.md5').fetchone()[0] == total)
    check('only std maps are analysed', tap_counts.get('not_std', 0) == conn.execute("SELECT COUNT(*) FROM beatmap WHERE mode != 'osu'").fetchone()[0])
    rated_maps = tapping_query.list_maps(conn, limit=500)
    check('the list defaults to rated maps with a count per status', rated_maps['total'] == tap_counts['rated'] and rated_maps['statuses'] == tap_counts)
    value_columns = [f'{column}{suffix}' for column in tapping.VALUE_COLUMNS for suffix in ('', '_s')]
    outside = conn.execute('SELECT COUNT(*) FROM tapping WHERE ' + ' OR '.join(f'{c} < 1 OR {c} > 10' for c in value_columns)).fetchone()[0]
    check('every value is between 1 and 10', outside == 0)
    check('ignored maps have no values', conn.execute("SELECT COUNT(*) FROM tapping WHERE status != 'rated' AND (" + ' OR '.join(f'{c} IS NOT NULL' for c in value_columns) + ' OR data IS NOT NULL)').fetchone()[0] == 0)
    for kind, column in (('stream', 'streams'), ('burst', 'bursts')):
        check(f'{kind} values are blank exactly when a map has none', conn.execute(f"SELECT COUNT(*) FROM tapping WHERE status = 'rated' AND (a_{kind} IS NULL) != ({column} = 0)").fetchone()[0] == 0)
    check('a rated map always has a group', conn.execute("SELECT COUNT(*) FROM tapping WHERE status = 'rated' AND doubles + triples + bursts + streams = 0").fetchone()[0] == 0)
    reread = rated_maps['rows'][0]
    check('a row carries groups that match its counts', len(reread['data']['groups']) == reread['doubles'] + reread['triples'] + reread['bursts'] + reread['streams'])
    for version in tapping.VERSION_KEYS:
        values = [r['data']['kinds'].get('stream', {}).get('versions', {}).get(version, {}).get('raw') for r in tapping_query.list_maps(conn, version=version, limit=500)['rows']]
        present = [v for v in values if v is not None]
        check(f'version {version} sorts by its own stream value, blanks last', present == sorted(present, reverse=True) and values[:len(present)] == present and len(present) > 0)
    ascending = tapping_query.list_maps(conn, sort='value_burst', direction='asc', limit=50)['rows']
    check('sorting can go the other way', ascending[0]['data']['kinds']['burst']['versions']['a']['raw'] <= ascending[-1]['data']['kinds']['burst']['versions']['a']['raw'])
    differences = [abs(r['data']['kinds']['stream']['versions']['a']['scaled'] - r['data']['kinds']['stream']['versions']['b']['scaled'])
                   for r in tapping_query.list_maps(conn, version='a', compare='b', sort='diff_stream', limit=100)['rows'] if 'stream' in r['data']['kinds']]
    check('sorting by the difference between two versions', differences == sorted(differences, reverse=True) and differences[0] > 0, f'largest {differences[0]:.2f}')
    filtered = tapping_query.list_maps(conn, q='stars>=6')
    check('the normal filter works on this tab', 0 < filtered['total'] < rated_maps['total'] and all(r['stars'] >= 6 for r in filtered['rows']))
    check('a score filter works on this tab too', tapping_query.list_maps(conn, q='grade>=S')['total'] <= rated_maps['total'])
    many_rows = tapping_query.list_maps(conn, status='many_bpm_changes', limit=500)['rows']
    check('ignored maps explain themselves', all(r['bpm_changes'] > 2 and r['data'] is None for r in many_rows) and len(many_rows) > 0, f'{len(many_rows)} maps')
    for bad, arguments in (('sort', {'sort': "stars; DROP TABLE tapping"}), ('version', {'version': 'z'}), ('compare', {'compare': 'z'}), ('status', {'status': 'nope'}), ('difference without a second version', {'sort': 'diff_stream'})):
        try:
            tapping_query.list_maps(conn, **arguments)
            refused = False
        except query.QueryError:
            refused = True
        check(f'a bad {bad} is refused', refused)
    check('the other tabs are not affected by the Tapping table', 'data' not in query.list_rows(conn, limit=1)['rows'][0] and 'a_stream' not in query.list_rows(conn, limit=1)['rows'][0])

    # Against stable's own stored stars for the same combinations. Rounding to 2 decimals allows up to 0.005 on top of real differences.
    if stable_dir:
        _, stored_maps = stable.parse_osu_db(stable_dir / 'osu!.db')
        gaps = {combo[2]: 0 for combo in COMBOS}
        compared = 0
        for m in stored_maps:
            row = conn.execute('SELECT * FROM beatmap WHERE md5 = ?', (m['md5'],)).fetchone()
            if row is None or row['aim'] is None:
                continue
            compared += 1
            for suffix, bits, label, _ in COMBOS:
                gaps[label] = max(gaps[label], abs(row[f'stars{suffix}'] - m['stars'][0][bits]))
        check("stars match stable's stored values in all 8 combinations", compared > 0 and max(gaps.values()) <= 0.02, f'{compared} maps, worst by combination {({k: round(v, 3) for k, v in gaps.items()})}')

    # Against stable's own stored grade per beatmap. It can come from a play missing from scores.db, so full agreement isn't expected.
    if stable_dir:
        _, maps = stable.parse_osu_db(stable_dir / 'osu!.db')
        scores = stable.parse_scores_db(stable_dir / 'scores.db')
        order = ['XH', 'X', 'SH', 'S', 'A', 'B', 'C', 'D', 'F']
        stored = {m['md5']: STABLE_GRADES.get(m['grades'][m['mode']]) for m in maps}
        plays = {}
        for s in scores:
            plays.setdefault(s['md5'], []).append((s['score'], stable.score_row(s)['grade'], s['mods']))
        by_score = {k: max(v)[1] for k, v in plays.items()}
        same = sum(stored[k] == g for k, g in by_score.items())
        print('grade of top-scoring play equals stored grade:', f'{same}/{len(by_score)}')
        above = {k: g for k, g in by_score.items() if stored[k] and order.index(g) < order.index(stored[k])}
        print('top-scoring play graded above stored:', [(k[:8], stored[k], plays[k]) for k in above][:5])
        check('grades agree with most of what stable stored', same >= len(by_score) * 0.75, f'{same}/{len(by_score)}')
    conn.close()

print(f'\n{len(failures)} failure(s)')
sys.exit(1 if failures else 0)
