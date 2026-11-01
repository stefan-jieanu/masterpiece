"""Turns the query language, sort and grouping requests into SQL. Field SQL comes only from the registry; values are always bound.

In the beatmap view, score conditions pick which scores count, and the best of those is attached to each beatmap.
"""
import re
from datetime import datetime, timedelta, timezone

from . import fields as F
from .db import BEATMAP_ROW_COLS, SCORE_ROW_COLS
from .normalize import normalize_mods

GROUP_LIMIT = 5000
BEST = {
    'score': 's.total_score DESC', 'accuracy': 's.accuracy DESC', 'pp': 's.pp DESC NULLS LAST',
    'combo': 's.max_combo DESC', 'recent': 's.played_at DESC',
}
TOKEN = re.compile(r'(?P<key>[A-Za-z0-9_]+)(?P<op>==|!=|>=|<=|!:|=|:|>|<|~)(?P<val>"[^"]*"|\S*)|"(?P<phrase>[^"]*)"|(?P<word>\S+)')
ORDERING = ('>', '>=', '<', '<=')


class QueryError(ValueError):
    pass


class Params(dict):
    def add(self, value) -> str:
        key = f'p{len(self)}'
        self[key] = value
        return ':' + key


def like(value: str) -> str:
    escaped = value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f'%{escaped}%'


def unsupported(f, op):
    return QueryError(f"{f['key']}: the {op} operator doesn't apply to {f['type']} fields")


def text_condition(f, expr, op, value, p):
    if op in ('=', ':'):
        return f"{expr} LIKE {p.add(like(value))} ESCAPE '\\'"
    if op == '==':
        return f'{expr} = {p.add(value)} COLLATE NOCASE'
    if op in ('!=', '!:'):
        return f"({expr} IS NULL OR {expr} NOT LIKE {p.add(like(value))} ESCAPE '\\')"
    if op in ORDERING:
        return f'{expr} COLLATE NOCASE {op} {p.add(value)}'
    raise unsupported(f, op)


def numeric_condition(f, expr, op, value: float, tolerance: float, p):
    if op in ('=', ':', '=='):
        return f'({expr} >= {p.add(value - tolerance)} AND {expr} < {p.add(value + tolerance)})'
    if op == '!=':
        return f'NOT ({expr} >= {p.add(value - tolerance)} AND {expr} < {p.add(value + tolerance)})'
    if op == '~':
        low, high = sorted((value * 0.95, value * 1.05))
        return f'{expr} BETWEEN {p.add(low)} AND {p.add(high)}'
    if op in ORDERING:
        return f'{expr} {op} {p.add(value)}'
    raise unsupported(f, op)


def number_condition(f, expr, op, value, p):
    if not re.fullmatch(r'-?\d+(\.\d+)?', value):
        raise QueryError(f"{f['key']}: expected a number, got '{value}'")
    decimals = len(value.split('.')[1]) if '.' in value else 0
    tolerance = f.get('tol', 0.5 * 10 ** -decimals)
    return numeric_condition(f, expr, op, float(value), tolerance, p)


def parse_duration(f, value) -> float:
    match = re.fullmatch(r'(\d+):(\d{1,2})', value)
    if match:
        return int(match[1]) * 60 + int(match[2])
    match = re.fullmatch(r'(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s?)?', value)
    if not match or not value:
        raise QueryError(f"{f['key']}: expected a duration such as 90, 90s, 2m30s or 2:30, got '{value}'")
    return int(match[1] or 0) * 3600 + int(match[2] or 0) * 60 + float(match[3] or 0)


def date_range(f, value):
    match = re.fullmatch(r'(\d{4})(?:[-./](\d{1,2}))?(?:[-./](\d{1,2}))?', value)
    if not match:
        raise QueryError(f"{f['key']}: expected a date such as 2026, 2026-10 or 2026-10-28, got '{value}'")
    year, month, day = int(match[1]), int(match[2] or 1), int(match[3] or 1)
    try:
        start = datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        raise QueryError(f"{f['key']}: '{value}' is not a real date") from None
    if match[3]:
        end = start + timedelta(days=1)
    elif match[2]:
        end = datetime(year + month // 12, month % 12 + 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    return int(start.timestamp()), int(end.timestamp())


def date_condition(f, expr, op, value, p):
    start, end = date_range(f, value)
    if op in ('=', ':', '=='):
        return f'({expr} >= {p.add(start)} AND {expr} < {p.add(end)})'
    if op == '!=':
        return f'NOT ({expr} >= {p.add(start)} AND {expr} < {p.add(end)})'
    if op == '>':
        return f'{expr} >= {p.add(end)}'
    if op == '>=':
        return f'{expr} >= {p.add(start)}'
    if op == '<':
        return f'{expr} < {p.add(start)}'
    if op == '<=':
        return f'{expr} < {p.add(end)}'
    raise unsupported(f, op)


def resolve_enum(f, token: str) -> list[str]:
    needle = token.strip().lower()
    aliases = f.get('value_aliases', {})
    if needle in aliases:
        return aliases[needle]
    values = [v for v, _ in f['values']]
    exact = [v for v in values if v.lower() == needle]
    if exact:
        return exact
    prefixed = [v for v in values if v.lower().startswith(needle)] if needle else []
    if len(prefixed) == 1:
        return prefixed
    raise QueryError(f"{f['key']}: unknown value '{token}'. Use one of: {', '.join(values)}")


def enum_condition(f, expr, op, value, p):
    chosen = [v for token in value.split(',') for v in resolve_enum(f, token)]
    if op in ORDERING:
        if not f.get('ordered'):
            raise unsupported(f, op)
        order = [v for v, _ in f['values']]
        low, high = min(map(order.index, chosen)), max(map(order.index, chosen))
        chosen = {'>': order[high + 1:], '>=': order[low:], '<': order[:low], '<=': order[:high + 1]}[op]
        op = '='
    if not chosen:
        return '0'
    placeholders = ', '.join(p.add(v) for v in chosen)
    if op in ('=', ':', '=='):
        return f'{expr} IN ({placeholders})'
    if op in ('!=', '!:'):
        return f'({expr} IS NULL OR {expr} NOT IN ({placeholders}))'
    raise unsupported(f, op)


def bool_condition(f, expr, op, value, p):
    truth = {'yes': True, 'y': True, 'true': True, '1': True, 'no': False, 'n': False, 'false': False, '0': False}.get(value.lower())
    if truth is None:
        raise QueryError(f"{f['key']}: expected yes or no, got '{value}'")
    if op in ('!=', '!:'):
        truth = not truth
    elif op not in ('=', ':', '=='):
        raise unsupported(f, op)
    return f'{expr} = 1' if truth else f'({expr} = 0 OR {expr} IS NULL)'


def parse_mods(f, value) -> list[str]:
    parts = re.sub(r'[,+\s]', ' ', value.upper()).split()
    if not parts or any(len(part) % 2 or not part.isalnum() for part in parts):
        raise QueryError(f"{f['key']}: expected acronyms such as HD, HDDT, HD,DT or NM, got '{value}'")
    return normalize_mods(part[i:i + 2] for part in parts for i in range(0, len(part), 2))


def mods_condition(f, expr, op, value, p):
    mods = parse_mods(f, value)

    def contains(mod):
        variants = {'DT': ['DT', 'NC'], 'SD': ['SD', 'PF']}.get(mod, [mod])
        return '(' + ' OR '.join(f"(',' || {expr} || ',') LIKE {p.add(f'%,{m},%')}" for m in variants) + ')'

    if op in ('=', '=='):
        return f"{expr} = {p.add(','.join(mods))}"
    if op == '!=':
        return f"{expr} != {p.add(','.join(mods))}"
    if op in (':', '>='):
        return ' AND '.join(contains(m) for m in mods) if mods else f"{expr} = ''"
    if op == '!:':
        return ' AND '.join(f'NOT {contains(m)}' for m in mods) if mods else f"{expr} != ''"
    raise unsupported(f, op)


def membership_condition(f, op, value, p):
    table, column = ('beatmap_tag t', 't.tag') if f['type'] == 'tag' else ('collection_item t JOIN collection c ON c.id = t.collection_id', 'c.name')
    if op == '==':
        match = f'{column} = {p.add(value)} COLLATE NOCASE'
    elif op in ('=', ':', '!=', '!:'):
        match = f"{column} LIKE {p.add(like(value))} ESCAPE '\\'"
    else:
        raise unsupported(f, op)
    negate = 'NOT ' if op in ('!=', '!:') else ''
    return f'b.pk {negate}IN (SELECT t.beatmap_pk FROM {table} WHERE {match})'


def condition(f, op, value, p):
    kind, expr = f['type'], f['sql']
    if kind == 'text':
        return text_condition(f, expr, op, value, p)
    if kind == 'number':
        return number_condition(f, expr, op, value, p)
    if kind == 'duration':
        return numeric_condition(f, expr, op, parse_duration(f, value), 0.5, p)
    if kind == 'date':
        return date_condition(f, expr, op, value, p)
    if kind == 'enum':
        return enum_condition(f, expr, op, value, p)
    if kind == 'bool':
        return bool_condition(f, expr, op, value, p)
    if kind == 'mods':
        return mods_condition(f, expr, op, value, p)
    return membership_condition(f, op, value, p)


def parse_query(q: str, p: Params, view: str):
    beatmap, score = [], []
    for match in TOKEN.finditer(q or ''):
        if match['key']:
            f = F.get(match['key'])
            if not f:
                raise QueryError(f"Unknown field '{match['key']}'. To search for that text literally, put it in quotes.")
            value = match['val'].strip('"')
            if not value:
                raise QueryError(f"{f['key']}: missing a value after {match['op']}")
            (score if f['score'] else beatmap).append(condition(f, match['op'], value, p))
        else:
            word = (match['phrase'] if match['phrase'] is not None else match['word']).lower()
            if word:
                placeholder = p.add(like(word))
                text = f"b.search_text LIKE {placeholder} ESCAPE '\\'"
                beatmap.append(f"({text} OR lower(s.player) LIKE {placeholder} ESCAPE '\\')" if view == 'scores' else text)
    return beatmap, score


def where(conditions) -> str:
    return 'WHERE ' + ' AND '.join(conditions) if conditions else ''


def source_sql(view, beatmap_conds, score_conds, best_by):
    """Returns (cte, from_clause, outer_conditions). Both views expose the aliases b and s."""
    if view == 'scores':
        return '', 'FROM score s JOIN beatmap b ON b.pk = s.beatmap_pk', beatmap_conds + score_conds
    best = BEST.get(best_by, BEST['score'])
    cte = (f'WITH best AS (SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s.beatmap_pk ORDER BY {best}, s.played_at DESC) AS rn '
           f'FROM score s {where(score_conds)}) ')
    join = 'JOIN' if score_conds else 'LEFT JOIN'
    return cte, f'FROM beatmap b {join} best s ON s.beatmap_pk = b.pk AND s.rn = 1', beatmap_conds


def bucket_sql(f, bucket):
    """Returns (bucket expression, raw label expression, extra joins)."""
    kind, expr = f['type'], f['sql']
    if kind == 'tag':
        return 'gt.tag', 'gt.tag', 'JOIN beatmap_tag gt ON gt.beatmap_pk = b.pk'
    if kind == 'collection':
        return 'gc.name', 'gc.name', 'JOIN collection_item gi ON gi.beatmap_pk = b.pk JOIN collection gc ON gc.id = gi.collection_id'
    allowed = [o['value'] for o in F.bucket_options(f)]
    bucket = bucket if bucket in allowed else allowed[0]
    if kind == 'text':
        if bucket == 'first':
            first = f'upper(substr({expr}, 1, 1))'
            return (f"CASE WHEN {expr} IS NULL OR {expr} = '' THEN NULL WHEN {first} BETWEEN 'A' AND 'Z' THEN {first} ELSE '#' END", None, '')
        return f"lower(NULLIF({expr}, ''))", expr, ''
    if kind in ('number', 'duration'):
        width = float(bucket)
        return f'ROUND((CAST({expr} / {width} AS INTEGER) - (CASE WHEN {expr} < 0 THEN 1 ELSE 0 END)) * {width}, 6)', None, ''
    if kind == 'date':
        fmt = {'year': '%Y', 'month': '%Y-%m', 'day': '%Y-%m-%d'}[bucket]
        return f"strftime('{fmt}', {expr}, 'unixepoch')", None, ''
    return expr, None, ''


def group_condition(group, p: Params):
    if not group or not group.get('keys'):
        return None
    f = F.get(group['by'])
    if not f:
        raise QueryError(f"Unknown grouping field '{group['by']}'")
    keys = group['keys']
    values = [k for k in keys if k is not None]
    placeholders = ', '.join(p.add(k) for k in values)
    if f['type'] == 'tag':
        return f'b.pk IN (SELECT beatmap_pk FROM beatmap_tag WHERE tag IN ({placeholders}))'
    if f['type'] == 'collection':
        return f'b.pk IN (SELECT i.beatmap_pk FROM collection_item i JOIN collection c ON c.id = i.collection_id WHERE c.name IN ({placeholders}))'
    expr, _, _ = bucket_sql(f, group.get('bucket'))
    parts = [f'{expr} IN ({placeholders})'] if values else []
    if len(values) < len(keys):
        parts.append(f'{expr} IS NULL')
    return '(' + ' OR '.join(parts) + ')'


def order_sql(sort, view) -> str:
    parts = []
    for item in sort or []:
        f = F.get(item.get('key', ''))
        if not f or f['type'] in F.MULTI_VALUED:
            raise QueryError(f"Can't sort by '{item.get('key')}'")
        expr = f['sql'] + (' COLLATE NOCASE' if f['type'] == 'text' else '')
        parts.append(f"{expr} {'DESC' if item.get('dir') == 'desc' else 'ASC'} NULLS LAST")
    parts += ['b.artist COLLATE NOCASE', 'b.title COLLATE NOCASE', 'b.set_key', 'b.stars', 'b.pk']
    if view == 'scores':
        parts.append('s.id')
    return ', '.join(parts)


def list_rows(conn, view='beatmaps', q='', group=None, sort=None, best_by='score', offset=0, limit=200) -> dict:
    view = 'scores' if view == 'scores' else 'beatmaps'
    p = Params()
    beatmap_conds, score_conds = parse_query(q, p, view)
    cte, source, conds = source_sql(view, beatmap_conds, score_conds, best_by)
    extra = group_condition(group, p)
    if extra:
        conds = conds + [extra]
    columns = ', '.join([f'b.{c}' for c in BEATMAP_ROW_COLS] + [f's.{c} AS s_{c}' for c in SCORE_ROW_COLS])
    limit = max(1, min(int(limit), 1000))
    rows = conn.execute(f'{cte}SELECT {columns} {source} {where(conds)} ORDER BY {order_sql(sort, view)} LIMIT {limit} OFFSET {max(0, int(offset))}', p).fetchall()
    total, sets = conn.execute(f'{cte}SELECT COUNT(*), COUNT(DISTINCT b.set_key) {source} {where(conds)}', p).fetchone()
    return {'rows': [dict(r) for r in rows], 'total': total, 'sets': sets}


def format_number(n) -> str:
    return f'{n:,.0f}' if abs(n) >= 1000 else f'{n:g}'


def bucket_label(f, bucket, key, raw, view='beatmaps') -> str:
    if key is None:
        if f.get('blank_label'):
            return f['blank_label']
        return '(no score)' if f['score'] and view == 'beatmaps' else '(none)'
    kind = f['type']
    if kind == 'number':
        width = float(bucket)
        if f.get('whole') and width == 1:
            return format_number(key)
        return f"{format_number(key)}–{format_number(key + width)}{f.get('unit', '')}"
    if kind == 'duration':
        return f'{F.duration_label(key)}–{F.duration_label(key + float(bucket))}'
    if kind == 'enum':
        return dict(f['values']).get(key, str(key))
    if kind == 'bool':
        return 'Yes' if key else 'No'
    if kind == 'mods':
        return key.replace(',', ' ') or 'NM'
    return str(raw if raw is not None else key)


def bucket_pin(f, bucket, key, raw):
    """The query text equivalent to a bucket, so a click can turn it into a filter and allow drilling into another field."""
    name, kind = f['key'], f['type']
    if key is None or (kind == 'text' and bucket == 'first'):
        return None
    if kind in ('number', 'duration'):
        if f.get('whole') and float(bucket) == 1:
            return f'{name}={key:g}'
        return f'{name}>={key:g} {name}<{key + float(bucket):g}'
    if kind == 'bool':
        return f"{name}={'yes' if key else 'no'}"
    if kind == 'mods':
        return f"{name}={key or 'NM'}"
    if kind in ('enum', 'date'):
        return f'{name}={key}'
    text = str(raw if raw is not None else key)
    return None if '"' in text else f'{name}=="{text}"'


def groups(conn, by, bucket=None, view='beatmaps', q='', best_by='score') -> dict:
    view = 'scores' if view == 'scores' else 'beatmaps'
    f = F.get(by)
    if not f:
        raise QueryError(f"Unknown grouping field '{by}'")
    allowed = [o['value'] for o in F.bucket_options(f)]
    bucket = bucket if bucket in allowed else allowed[0]
    expr, raw, joins = bucket_sql(f, bucket)
    raw = raw or expr

    def run(query):
        p = Params()
        beatmap_conds, score_conds = parse_query(query, p, view)
        cte, source, conds = source_sql(view, beatmap_conds, score_conds, best_by)
        return conn.execute(
            f'{cte}SELECT {expr} AS k, MIN({raw}) AS raw, COUNT(*) AS n {source} {joins} {where(conds)} GROUP BY 1 LIMIT {GROUP_LIMIT + 1}', p
        ).fetchall()

    totals = run('')
    selected = {r['k']: r['n'] for r in (run(q) if q and q.strip() else totals)}
    rows = [{
        'key': r['k'], 'label': bucket_label(f, bucket, r['k'], r['raw'], view), 'count': selected.get(r['k'], 0), 'total': r['n'],
        'pin': bucket_pin(f, bucket, r['k'], r['raw']),
    } for r in totals[:GROUP_LIMIT]]

    if f['type'] == 'enum':
        order = {v: i for i, (v, _) in enumerate(f['values'])}
        rows.sort(key=lambda r: order.get(r['key'], 999), reverse=bool(f.get('ordered')))
    elif f['type'] in ('number', 'duration', 'bool'):
        rows.sort(key=lambda r: (r['key'] is None, r['key'] or 0))
    else:
        rows.sort(key=lambda r: (r['key'] is None, str(r['label']).lower()))
    return {'rows': rows, 'bucket': bucket, 'truncated': len(totals) > GROUP_LIMIT}


def beatmap_detail(conn, md5: str):
    """By MD5, because each import renumbers the primary keys, and a page can stay open across imports."""
    row = conn.execute(f"SELECT {', '.join(BEATMAP_ROW_COLS)} FROM beatmap WHERE md5 = ?", (md5,)).fetchone()
    if not row:
        return None
    scores = conn.execute('SELECT * FROM score WHERE beatmap_pk = ? ORDER BY total_score DESC', (row['pk'],)).fetchall()
    names = conn.execute('SELECT c.name FROM collection c JOIN collection_item i ON i.collection_id = c.id WHERE i.beatmap_pk = ? ORDER BY c.name', (row['pk'],)).fetchall()
    return {'beatmap': dict(row), 'scores': [dict(s) for s in scores], 'collections': [n['name'] for n in names]}


def meta(conn) -> dict:
    one = lambda sql: conn.execute(sql).fetchone()[0]
    return {
        'beatmaps': one('SELECT COUNT(*) FROM beatmap'), 'sets': one('SELECT COUNT(DISTINCT set_key) FROM beatmap'),
        'scores': one('SELECT COUNT(*) FROM score'), 'collections': one('SELECT COUNT(*) FROM collection'),
        'report': one("SELECT value FROM import_info WHERE key = 'report'"),
    }
