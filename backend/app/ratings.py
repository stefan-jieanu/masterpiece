"""Calculates std star ratings and their aim, tapping and reading parts at import.

The calculation itself is the rating_calculator binary (rosu-pp, see its vendor/rosu-pp/ORIGIN.md). It runs in separate
processes with a time limit, because a malicious map can keep a calculation busy for a very long time.
"""
import hashlib
import http.client
import json
import os
import queue
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

from .normalize import COMBOS, RATING_COLUMNS, SKILLS, played_suffix

CALCULATOR = Path(__file__).resolve().parents[1] / 'rating_calculator' / 'target' / 'release' / ('rating-calculator.exe' if os.name == 'nt' else 'rating-calculator')
BUILD_COMMAND = 'cargo build --release --manifest-path rating_calculator/Cargo.toml'
MAP_TIMEOUT_SECONDS = 60
WORKERS = min(8, max(1, (os.cpu_count() or 2) - 1))
DOWNLOAD_INTERVAL_SECONDS = 1.0
MISS_RETRY_SECONDS = 7 * 24 * 3600
OSU_FILE_URL = 'https://osu.ppy.sh/osu/{}'
USER_AGENT = 'masterpiece-local-browser/0.1'
DRIFT_LIMIT = 0.05
DRIFT_WARN_SHARE = 0.05
EXAMPLE_LIMIT = 20


def describe(command=None) -> str:
    """The calculator's identity, which keys the rating cache. Raises if it's missing or disagrees on the combinations."""
    command = command or [str(CALCULATOR)]
    try:
        done = subprocess.run([*command, '--describe'], capture_output=True, text=True, timeout=30, check=True)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f'The rating calculator is not built. From backend/, run: {BUILD_COMMAND} (or import with --no-ratings). Cause: {e}') from e
    version, _, combos = done.stdout.strip().partition('\t')
    if combos != ','.join(str(combo[1]) for combo in COMBOS):
        raise RuntimeError(f"The rating calculator uses mod combinations '{combos}', which this app doesn't expect. Rebuild it: {BUILD_COMMAND}")
    return version


class CalculatorProcess:
    """One calculator process answering a request at a time. It's replaced if it hangs or dies."""

    def __init__(self, command, timeout=MAP_TIMEOUT_SECONDS):
        self.command, self.timeout = command, timeout
        self.process = None
        self.lines = None

    def _start(self):
        self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        text=True, encoding='utf-8', bufsize=1)
        self.lines = queue.Queue()
        threading.Thread(target=self._pump, args=(self.process.stdout, self.lines), daemon=True).start()

    @staticmethod
    def _pump(stream, lines):
        for line in stream:
            lines.put(line.rstrip('\n'))
        lines.put(None)

    def calculate(self, path) -> tuple[list[float] | None, str | None]:
        """Returns (ratings, None), or (None, reason)."""
        text = str(path)
        if '\n' in text or '\r' in text:
            return None, 'bad_path'
        try:
            if self.process is None or self.process.poll() is not None:
                self._start()
            self.process.stdin.write(f'{text}\n')
            self.process.stdin.flush()
            line = self.lines.get(timeout=self.timeout)
        except queue.Empty:
            self.close()
            return None, 'timeout'
        except (OSError, ValueError, AttributeError):
            self.close()
            return None, 'crashed'
        if line is None:
            self.close()
            return None, 'crashed'
        status, _, rest = line.partition('\t')
        if status == 'skip':
            return None, rest
        try:
            values = [float(value) for value in rest.split('\t')]
        except ValueError:
            return None, 'bad_output'
        if status != 'ok' or len(values) != len(RATING_COLUMNS):
            return None, 'bad_output'
        return values, None

    def close(self):
        process, self.process = self.process, None
        if process is None:
            return
        process.kill()
        process.wait()
        for stream in (process.stdin, process.stdout):
            try:
                stream.close()
            except (OSError, ValueError):
                pass


def calculate_all(jobs: dict, command, timeout=MAP_TIMEOUT_SECONDS, workers=WORKERS, results=None, reasons=None) -> tuple[dict, dict]:
    """Calculates {md5: path}. Returns ({md5: ratings}, {md5: reason it failed}). Dicts passed in keep what finished if this is interrupted."""
    results = {} if results is None else results
    reasons = {} if reasons is None else reasons
    tasks = queue.SimpleQueue()
    for job in jobs.items():
        tasks.put(job)
    stop = threading.Event()

    def work(process):
        while not stop.is_set():
            try:
                md5, path = tasks.get_nowait()
            except queue.Empty:
                return
            values, reason = process.calculate(path)
            if stop.is_set():
                return
            if values is None:
                reasons[md5] = reason
            else:
                results[md5] = values

    processes = [CalculatorProcess(command, timeout) for _ in range(min(workers, len(jobs)))]
    threads = [threading.Thread(target=work, args=(process,), daemon=True) for process in processes]
    try:
        for thread in threads:
            thread.start()
        # Timed joins, because Ctrl-C can't interrupt an untimed one on Windows.
        while any(thread.is_alive() for thread in threads):
            for thread in threads:
                thread.join(0.1)
    finally:
        stop.set()
        for process in processes:
            process.close()
    return results, reasons


class Store:
    """Keeps calculated ratings and failed download lookups between imports, since each import rebuilds the library."""

    def __init__(self, path: Path, version: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.version = version
        self.conn = sqlite3.connect(path)
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS rating (md5 TEXT NOT NULL, version TEXT NOT NULL, ratings TEXT NOT NULL, PRIMARY KEY (md5, version));
            CREATE TABLE IF NOT EXISTS osu_miss (md5 TEXT PRIMARY KEY, reason TEXT NOT NULL, checked_at INTEGER NOT NULL);
        ''')

    def ratings(self) -> dict:
        rows = self.conn.execute('SELECT md5, ratings FROM rating WHERE version = ?', (self.version,))
        return {md5: json.loads(values) for md5, values in rows}

    def save_ratings(self, results: dict):
        self.conn.executemany('INSERT OR REPLACE INTO rating VALUES (?, ?, ?)',
                              [(md5, self.version, json.dumps(values)) for md5, values in results.items()])
        self.conn.commit()

    def misses(self) -> dict:
        return {md5: (reason, checked_at) for md5, reason, checked_at in self.conn.execute('SELECT md5, reason, checked_at FROM osu_miss')}

    def save_miss(self, md5: str, reason: str):
        self.conn.execute('INSERT OR REPLACE INTO osu_miss VALUES (?, ?, ?)', (md5, reason, int(time.time())))
        self.conn.commit()

    def close(self):
        self.conn.close()


def file_md5(path: Path) -> str | None:
    try:
        return hashlib.md5(path.read_bytes()).hexdigest()
    except OSError:
        return None


def local_candidates(row, songs_dir, cache_dir: Path) -> list:
    """Where a beatmap's .osu file could be on this machine, as (path, source), best first."""
    candidates = []
    if row.get('_osu_path'):
        candidates.append((Path(row['_osu_path']), 'lazer'))
    if songs_dir and row.get('_osu_file'):
        folder = (row.get('_folder') or '').replace('\\', '/')
        candidates.append((Path(songs_dir) / folder / row['_osu_file'], 'songs'))
    candidates.append((cache_dir / f"{row['md5']}.osu", 'cache'))
    return candidates


class OsuFiles:
    """Finds a beatmap's .osu file: lazer's file store, then the Songs folder, then the download cache, then osu.ppy.sh."""

    def __init__(self, songs_dir, cache_dir: Path, store: Store, allow_download=True, interval=DOWNLOAD_INTERVAL_SECONDS):
        self.songs_dir = Path(songs_dir) if songs_dir else None
        self.cache_dir = cache_dir
        self.store = store
        self.allow_download = allow_download
        self.interval = interval
        self.misses = store.misses()
        self.stop_reason = None
        self.last_request = 0.0
        self.rejected = Counter()

    def find_local(self, row) -> tuple[Path | None, str | None]:
        for path, source in local_candidates(row, self.songs_dir, self.cache_dir):
            if path.is_file():
                # Damaged files and files edited since the database was written must not produce a rating for this map.
                if file_md5(path) == row['md5']:
                    return path, source
                self.rejected[source] += 1
        return None, None

    def download(self, row) -> tuple[Path | None, str | None]:
        """Returns (path, None), or (None, reason). Rate limiting or network trouble stops downloading for this run."""
        md5, beatmap_id = row['md5'], row.get('beatmap_id')
        if not beatmap_id:
            return None, 'no_online_id'
        if not self.allow_download:
            return None, 'download_disabled'
        if self.stop_reason:
            return None, 'download_stopped'
        if md5 in self.misses and time.time() - self.misses[md5][1] < MISS_RETRY_SECONDS:
            return None, self.misses[md5][0]

        wait = self.last_request + self.interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.last_request = time.monotonic()
        request = urllib.request.Request(OSU_FILE_URL.format(int(beatmap_id)), headers={'User-Agent': USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                self.store.save_miss(md5, 'not_online')
                return None, 'not_online'
            self.stop_reason = f'HTTP {e.code} from osu.ppy.sh'
            return None, 'download_stopped'
        except (OSError, http.client.HTTPException) as e:
            self.stop_reason = f'could not reach osu.ppy.sh: {e}'
            return None, 'download_stopped'

        # The address answers with an empty 200 for maps it doesn't have.
        if not data.strip():
            self.store.save_miss(md5, 'not_online')
            return None, 'not_online'
        # Anything else that isn't a beatmap, such as a captive portal page, says nothing about this map.
        if b'osu file format' not in data[:64].replace(b'\x00', b''):
            self.stop_reason = "osu.ppy.sh returned something that isn't a beatmap"
            return None, 'download_stopped'
        # The address serves the map's current version, which isn't this one if it has been updated online since.
        if hashlib.md5(data).hexdigest() != md5:
            self.store.save_miss(md5, 'updated_online')
            return None, 'updated_online'
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f'{md5}.osu'
        partial = path.with_suffix('.part')
        partial.write_bytes(data)
        os.replace(partial, path)
        return path, None


def locate_files(rows, files: OsuFiles) -> tuple[dict, dict, Counter]:
    """Returns ({md5: path}, {md5: reason it's unavailable}, count per source)."""
    jobs, reasons, sources = {}, {}, Counter()
    missing = []
    for row in rows:
        if not re.fullmatch(r'[0-9a-f]{32}', row['md5']):
            reasons[row['md5']] = 'bad_md5'
            continue
        path, source = files.find_local(row)
        if path:
            jobs[row['md5']] = path
            sources[source] += 1
        else:
            missing.append(row)

    if missing and files.allow_download:
        print(f'{len(missing)} .osu files not found locally. Downloading about one per second...', file=sys.stderr)
    for done, row in enumerate(missing, 1):
        path, reason = files.download(row)
        if path:
            jobs[row['md5']] = path
            sources['downloaded'] += 1
        else:
            reasons[row['md5']] = reason
        if done % 25 == 0:
            print(f'  {done}/{len(missing)}', file=sys.stderr)
    return jobs, reasons, sources


def attach_played(scores, beatmaps):
    """Gives each std score the ratings of the beatmap under that score's own mods, where they were calculated."""
    for score in scores:
        row = beatmaps.get(score['md5'])
        if not row or row.get('mode') != 'osu' or score.get('mode') != 'osu' or score.get('_custom_settings'):
            continue
        suffix = played_suffix(score['mods'].split(',') if score['mods'] else [])
        if suffix is None:
            continue
        for skill in SKILLS:
            score[f'played_{skill}'] = row.get(f'{skill}{suffix}')


def drift_report(differences: list[float]) -> dict:
    if not differences:
        return {'compared': 0}
    ordered = sorted(differences)
    return {
        'compared': len(ordered), 'max': round(ordered[-1], 4), 'p99': round(ordered[int(len(ordered) * 0.99)], 4),
        f'over_{DRIFT_LIMIT}': sum(d > DRIFT_LIMIT for d in ordered),
    }


def apply(beatmaps: dict, scores: list, songs_dir, data_dir: Path, allow_download=True, command=None, timeout=MAP_TIMEOUT_SECONDS, interval=DOWNLOAD_INTERVAL_SECONDS) -> dict:
    """Fills in the rating columns of std beatmaps and the as-played columns of their scores. Returns the import report part."""
    command = command or [str(CALCULATOR)]
    version = describe(command)
    started = time.perf_counter()
    std = [row for row in beatmaps.values() if row.get('mode') == 'osu']

    fresh, failures = {}, {}
    store = Store(data_dir / 'ratings_cache.sqlite', version)
    try:
        known = store.ratings()
        pending = [row for row in std if row['md5'] not in known]
        files = OsuFiles(songs_dir, data_dir / 'osu_cache', store, allow_download, interval)
        jobs, reasons, sources = locate_files(pending, files)
        calculate_all(jobs, command, timeout, results=fresh, reasons=failures)
    finally:
        # Finished work survives an interrupted import.
        store.save_ratings(fresh)
        store.close()
    reasons.update(failures)

    differences = []
    rated = 0
    examples = []
    for row in std:
        values = fresh.get(row['md5']) or known.get(row['md5'])
        if values is None:
            row['rating_status'] = reasons.get(row['md5'], 'unknown')
            if len(examples) < EXAMPLE_LIMIT:
                examples.append(f"{row['artist']} - {row['title']} [{row['difficulty']}]: {row['rating_status']}")
            continue
        rated += 1
        if row.get('_client_stars') is not None:
            differences.append(abs(values[0] - row['_client_stars']))
        row.update(zip(RATING_COLUMNS, (round(value, 2) for value in values)))
    attach_played(scores, beatmaps)

    drift = drift_report(differences)
    warnings = []
    if differences and drift[f'over_{DRIFT_LIMIT}'] > len(differences) * DRIFT_WARN_SHARE:
        warnings.append(f"Calculated stars differ from your client's by over {DRIFT_LIMIT} on {drift[f'over_{DRIFT_LIMIT}']} of {len(differences)} maps. "
                        "The vendored rosu-pp may be behind your client's rating algorithm.")
    if files.stop_reason:
        warnings.append(f'Stopped downloading .osu files: {files.stop_reason}. Import again later to fill in the rest.')

    report = {
        'calculator': version, 'std_maps': len(std), 'rated': rated, 'from_cache': sum(row['md5'] in known for row in std),
        'calculated': len(fresh), 'osu_files': dict(sources), 'not_rated': dict(Counter(reasons.values())),
        'stars_vs_client': drift, 'seconds': round(time.perf_counter() - started, 2),
    }
    if files.rejected:
        report['local_files_with_wrong_md5'] = dict(files.rejected)
    if examples:
        report['not_rated_examples'] = examples
    if warnings:
        report['warnings'] = warnings
    return report
