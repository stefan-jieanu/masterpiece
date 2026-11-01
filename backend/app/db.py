"""SQLite schema and connections. The whole library is one rebuildable file."""
import os
import sqlite3
from pathlib import Path

from .normalize import RATING_COLUMNS, SKILLS
from .tapping import TAPPING

# Bump when the tables change, so the server can ask for a re-import rather than fail on a missing column.
SCHEMA_VERSION = 3
DATA_DIR = Path(__file__).resolve().parents[1] / 'data'
DB_PATH = Path(os.environ.get('MASTERPIECE_DB') or DATA_DIR / 'library.sqlite')

BEATMAP = {
    'pk': 'INTEGER PRIMARY KEY', 'md5': 'TEXT NOT NULL UNIQUE',
    'beatmap_id': 'INTEGER', 'set_id': 'INTEGER', 'set_key': 'TEXT NOT NULL',
    'artist': 'TEXT', 'artist_unicode': 'TEXT', 'title': 'TEXT', 'title_unicode': 'TEXT',
    'creator': 'TEXT', 'difficulty': 'TEXT', 'song_source': 'TEXT', 'tags': 'TEXT', 'audio_file': 'TEXT',
    'mode': 'TEXT', 'status': 'TEXT',
    **{column: 'REAL' for column in RATING_COLUMNS},
    'bpm': 'REAL', 'length_s': 'REAL', 'drain_s': 'REAL',
    'ar': 'REAL', 'cs': 'REAL', 'od': 'REAL', 'hp': 'REAL', 'sv': 'REAL',
    'objects': 'INTEGER', 'circles': 'INTEGER', 'sliders': 'INTEGER', 'spinners': 'INTEGER', 'local_offset': 'REAL',
    'last_played': 'INTEGER', 'last_modified': 'INTEGER',
    'date_added': 'INTEGER', 'date_ranked': 'INTEGER', 'date_submitted': 'INTEGER',
    'rank_achieved': 'TEXT', 'in_stable': 'INTEGER NOT NULL DEFAULT 0', 'in_lazer': 'INTEGER NOT NULL DEFAULT 0',
    'diffs': 'INTEGER', 'score_count': 'INTEGER NOT NULL DEFAULT 0', 'rating_status': 'TEXT', 'search_text': 'TEXT',
}

SCORE = {
    'id': 'INTEGER PRIMARY KEY', 'beatmap_pk': 'INTEGER NOT NULL REFERENCES beatmap(pk)',
    'source': 'TEXT NOT NULL', 'hash': 'TEXT', 'mode': 'TEXT', 'player': 'TEXT',
    'total_score': 'INTEGER', 'max_combo': 'INTEGER', 'accuracy': 'REAL', 'grade': 'TEXT', 'mods': 'TEXT NOT NULL',
    'n300': 'INTEGER', 'n100': 'INTEGER', 'n50': 'INTEGER', 'ngeki': 'INTEGER', 'nkatu': 'INTEGER', 'nmiss': 'INTEGER',
    'perfect': 'INTEGER', 'played_at': 'INTEGER', 'pp': 'REAL', 'online_id': 'INTEGER',
    **{f'played_{skill}': 'REAL' for skill in SKILLS},
}

BEATMAP_ROW_COLS = [c for c in BEATMAP if c != 'search_text']
SCORE_ROW_COLS = list(SCORE)


def _table(name, columns, extra=''):
    body = ', '.join(f'{k} {v}' for k, v in columns.items())
    return f'CREATE TABLE {name} ({body}{extra});'


SCHEMA = '\n'.join([
    _table('beatmap', BEATMAP),
    _table('score', SCORE, ', UNIQUE (source, hash)'),
    _table('tapping', TAPPING),
    'CREATE TABLE collection (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, source TEXT NOT NULL);',
    'CREATE TABLE collection_item (collection_id INTEGER NOT NULL REFERENCES collection(id), beatmap_pk INTEGER NOT NULL REFERENCES beatmap(pk), PRIMARY KEY (collection_id, beatmap_pk));',
    'CREATE TABLE beatmap_tag (beatmap_pk INTEGER NOT NULL REFERENCES beatmap(pk), tag TEXT NOT NULL, PRIMARY KEY (beatmap_pk, tag));',
    'CREATE TABLE import_info (key TEXT PRIMARY KEY, value TEXT);',
    'CREATE INDEX ix_score_beatmap ON score (beatmap_pk);',
    'CREATE INDEX ix_beatmap_set ON beatmap (set_key);',
    'CREATE INDEX ix_tag ON beatmap_tag (tag);',
])


def connect(path=DB_PATH):
    """Read-only connection, one per request."""
    conn = sqlite3.connect(f'{Path(path).as_uri()}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def schema_version(conn) -> int | None:
    try:
        row = conn.execute("SELECT value FROM import_info WHERE key = 'schema_version'").fetchone()
    except sqlite3.OperationalError:
        return None
    return int(row[0]) if row else None
