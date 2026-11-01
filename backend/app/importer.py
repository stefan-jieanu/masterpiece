"""Rebuilds the library from scratch. Usage: python -m app.importer [--stable DIR] [--lazer DIR]

Writes to a temporary file and swaps it in only on success, so a failed import keeps the previous library
and re-imports never leave stale rows behind.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

from . import lazer, ratings, stable, tapping
from .db import BEATMAP, DATA_DIR, DB_PATH, SCHEMA, SCHEMA_VERSION, SCORE
from .normalize import search_text


def insert(conn, table, columns, rows):
    cols = [c for c in columns if c not in ('pk', 'id')]
    sql = f'INSERT OR IGNORE INTO {table} ({", ".join(cols)}) VALUES ({", ".join(":" + c for c in cols)})'
    before = conn.total_changes
    conn.executemany(sql, ({c: r.get(c) for c in cols} for r in rows))
    return conn.total_changes - before


def write_library(path: Path, beatmaps: dict, scores: list, collections: dict, report: dict, tapping_rows=()):
    tmp = path.with_suffix('.tmp')
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp)
    conn.executescript(SCHEMA)

    diffs = Counter(row['set_key'] for row in beatmaps.values())
    resolved = [s for s in scores if s['md5'] in beatmaps]
    score_counts = Counter(s['md5'] for s in resolved)
    for row in beatmaps.values():
        row['diffs'] = diffs[row['set_key']]
        row['score_count'] = score_counts[row['md5']]
        row['search_text'] = search_text(row)
    insert(conn, 'beatmap', BEATMAP, beatmaps.values())
    insert(conn, 'tapping', tapping.TAPPING, tapping_rows)

    pk_by_md5 = dict(conn.execute('SELECT md5, pk FROM beatmap'))
    for s in resolved:
        s['beatmap_pk'] = pk_by_md5[s['md5']]
    score_rows = insert(conn, 'score', SCORE, resolved)

    tags = {(pk_by_md5[r['md5']], t) for r in beatmaps.values() for t in (r.get('tags') or '').lower().split()}
    conn.executemany('INSERT INTO beatmap_tag VALUES (?, ?)', sorted(tags))

    unresolved_items = 0
    for name, entry in sorted(collections.items()):
        source = 'both' if len(entry['sources']) > 1 else next(iter(entry['sources']))
        cid = conn.execute('INSERT INTO collection (name, source) VALUES (?, ?)', (name, source)).lastrowid
        pks = [pk_by_md5[h] for h in entry['hashes'] if h in pk_by_md5]
        unresolved_items += len(entry['hashes']) - len(pks)
        conn.executemany('INSERT INTO collection_item VALUES (?, ?)', [(cid, pk) for pk in pks])

    report['library'] = {
        'beatmaps': len(beatmaps), 'sets': len(diffs), 'scores': score_rows, 'collections': len(collections),
        'in_both': sum(1 for r in beatmaps.values() if r['in_stable'] and r['in_lazer']),
        'scores_without_beatmap': len(scores) - len(resolved), 'duplicate_scores_skipped': len(resolved) - score_rows,
        'collection_items_without_beatmap': unresolved_items,
    }
    conn.execute('INSERT INTO import_info VALUES (?, ?)', ('report', json.dumps(report)))
    conn.execute('INSERT INTO import_info VALUES (?, ?)', ('schema_version', str(SCHEMA_VERSION)))
    conn.commit()
    conn.close()
    replace_library(tmp, path)


def replace_library(tmp: Path, path: Path, attempts=120, delay=0.25):
    """Swaps in the new library. Windows refuses while anything has the old file open, which a request can briefly do."""
    for attempt in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                tmp.unlink(missing_ok=True)
                raise RuntimeError(f"Something has {path.name} open, so it can't be replaced. Stop any running server and import again.") from None
            time.sleep(delay)


def run(stable_dir=None, lazer_dir=None, path: Path = DB_PATH, songs_dir=None, download=True, calculate_ratings=True, data_dir: Path = DATA_DIR, calculate_tapping=True) -> dict:
    if not stable_dir and not lazer_dir:
        raise ValueError('Give at least one of --stable or --lazer.')
    started = time.perf_counter()
    if calculate_ratings:
        ratings.describe()  # Fail before the slow work if the calculator isn't built.
    beatmaps, scores, collections, report = {}, [], {}, {}
    if stable_dir:
        stable.load(Path(stable_dir), beatmaps, scores, collections, report)
    if lazer_dir:
        lazer.load(Path(lazer_dir), beatmaps, scores, collections, report)
    songs = songs_dir or (Path(stable_dir) / 'Songs' if stable_dir else None)
    if calculate_ratings:
        report['ratings'] = ratings.apply(beatmaps, scores, songs, Path(data_dir), download)
    else:
        report['ratings'] = {'skipped': True}
        for row in beatmaps.values():
            if row.get('mode') == 'osu':
                row['rating_status'] = 'skipped'
    if calculate_tapping:
        tapping_rows, report['tapping'] = tapping.apply(beatmaps, songs, Path(data_dir) / 'osu_cache')
    else:
        tapping_rows, report['tapping'] = tapping.skipped(beatmaps), {'skipped': True}
    report['imported_at'] = int(time.time())
    report['seconds'] = None
    path.parent.mkdir(parents=True, exist_ok=True)
    write_library(path, beatmaps, scores, collections, report, tapping_rows)
    report['seconds'] = round(time.perf_counter() - started, 2)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stable', help='folder holding osu!.db, collection.db and scores.db')
    parser.add_argument('--lazer', help='osu!lazer data folder holding client.realm and files/ (usually %%APPDATA%%/osu)')
    parser.add_argument('--songs', help="osu!stable's Songs folder, if it isn't inside the --stable folder")
    parser.add_argument('--no-download', action='store_true', help="don't download missing .osu files from osu.ppy.sh")
    parser.add_argument('--no-ratings', action='store_true', help='skip calculating aim, tapping, reading and modded stars')
    parser.add_argument('--no-tapping', action='store_true', help='skip the analysis behind the Tapping tab')
    args = parser.parse_args()
    try:
        report = run(args.stable, args.lazer, songs_dir=args.songs, download=not args.no_download, calculate_ratings=not args.no_ratings, calculate_tapping=not args.no_tapping)
    except (OSError, ValueError, RuntimeError) as e:
        sys.exit(f'Import failed, previous library kept: {e}')
    except KeyboardInterrupt:
        sys.exit('Import cancelled, previous library kept. Downloads and ratings finished so far are saved for next time.')
    print(json.dumps(report, indent=2))
    for warning in report['ratings'].get('warnings', []):
        print(f'WARNING: {warning}', file=sys.stderr)


if __name__ == '__main__':
    main()
