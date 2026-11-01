"""The Tapping tab's list. Uses the same filter language as the other tabs, but none of the Tapping values are fields in it."""
import json

from . import query
from .tapping import KIND_KEYS, STATUSES, VERSION_KEYS

BEATMAP_COLUMNS = ('md5', 'beatmap_id', 'set_id', 'artist', 'title', 'artist_unicode', 'title_unicode', 'difficulty', 'creator', 'mode', 'status', 'stars', 'bpm', 'length_s')
TAPPING_COLUMNS = ('source', 'bpm_changes', 'bpm_changes_in_play', 'doubles', 'triples', 'bursts', 'streams', 'longest', 'data')
OTHER_SORTS = {
    'count_stream': 't.streams', 'count_burst': 't.bursts', 'count_double': 't.doubles', 'count_triple': 't.triples', 'longest': 't.longest',
    'stars': 'b.stars', 'artist': 'b.artist COLLATE NOCASE', 'title': 'b.title COLLATE NOCASE',
}


def sort_sql(sort, version, compare, scale) -> str:
    suffix = '_s' if scale == 'scaled' else ''
    kind = sort.rpartition('_')[2]
    if sort.startswith('value_') and kind in KIND_KEYS:
        # Always by the plain value, since stretching gives the top of the library the same 10.
        return f't.{version}_{kind}'
    if sort.startswith('diff_') and kind in KIND_KEYS:
        if not compare:
            raise query.QueryError('Pick a version to compare with, to sort by the difference.')
        return f'ABS(t.{version}_{kind}{suffix} - t.{compare}_{kind}{suffix})'
    if sort in OTHER_SORTS:
        return OTHER_SORTS[sort]
    raise query.QueryError(f"Can't sort by '{sort}'")


def list_maps(conn, q='', status='rated', version='a', compare='', sort='value_stream', direction='desc', scale='scaled', offset=0, limit=100) -> dict:
    if version not in VERSION_KEYS or (compare and compare not in VERSION_KEYS):
        raise query.QueryError('Unknown version')
    if status not in {entry['key'] for entry in STATUSES}:
        raise query.QueryError(f"Unknown status '{status}'")
    order = sort_sql(sort, version, compare, scale)
    p = query.Params()
    beatmap_conds, score_conds = query.parse_query(q, p, 'beatmaps')
    cte, source, conds = query.source_sql('beatmaps', beatmap_conds, score_conds, 'score')
    source += ' JOIN tapping t ON t.md5 = b.md5'
    statuses = dict(conn.execute(f'{cte}SELECT t.status, COUNT(*) {source} {query.where(conds)} GROUP BY 1', p).fetchall())
    conds = conds + [f't.status = {p.add(status)}']
    columns = ', '.join([f'b.{c}' for c in BEATMAP_COLUMNS] + [f't.{c}' for c in TAPPING_COLUMNS] + ['t.status AS tap_status'])
    limit = max(1, min(int(limit), 500))
    rows = conn.execute(
        f"{cte}SELECT {columns} {source} {query.where(conds)} ORDER BY {order} {'ASC' if direction == 'asc' else 'DESC'} NULLS LAST, "
        f'b.artist COLLATE NOCASE, b.title COLLATE NOCASE, b.stars, b.pk LIMIT {limit} OFFSET {max(0, int(offset))}', p).fetchall()
    return {
        'rows': [{**dict(row), 'data': json.loads(row['data']) if row['data'] else None} for row in rows],
        'total': statuses.get(status, 0), 'statuses': statuses,
    }
