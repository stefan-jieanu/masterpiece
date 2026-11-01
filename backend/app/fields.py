"""The field registry. Every attribute is declared once; filtering, sorting and grouping all derive from its data type."""
from itertools import permutations

from .normalize import COMBOS

MODES =[('osu', 'osu!'), ('taiko', 'osu!taiko'), ('catch', 'osu!catch'), ('mania', 'osu!mania')]
STATUSES = [('ranked', 'Ranked'), ('approved', 'Approved'), ('qualified', 'Qualified'), ('loved', 'Loved'), ('pending', 'Pending'),
            ('wip', 'WIP'), ('graveyard', 'Graveyard'), ('unsubmitted', 'Unsubmitted'), ('modified', 'Modified'), ('unknown', 'Unknown')]
GRADES = [('F', 'F'), ('D', 'D'), ('C', 'C'), ('B', 'B'), ('A', 'A'), ('S', 'S'), ('SH', 'S silver'), ('X', 'SS'), ('XH', 'SS silver')]
GRADE_ALIASES = {'ss': ['X', 'XH'], 's': ['S', 'SH']}
LIBRARY = [('stable', 'stable only'), ('lazer', 'lazer only'), ('both', 'both')]
SOURCES = [('stable', 'stable'), ('lazer', 'lazer')]

MULTI_VALUED = ('tag', 'collection')
FIELDS = []


def field(key, label, cat, type, sql, aliases='', **options):
    FIELDS.append({'key': key, 'label': label, 'cat': cat, 'type': type, 'sql': sql, 'aliases': aliases.split(),
                   'score': sql.startswith('s.') or 's.' in sql.split('b.')[0], **options})


# Song
field('artist', 'Artist', 'Song', 'text', 'b.artist', default='first')
field('title', 'Title', 'Song', 'text', 'b.title', default='first')
field('artist_unicode', 'Artist (unicode)', 'Song', 'text', 'b.artist_unicode', 'au', default='first')
field('title_unicode', 'Title (unicode)', 'Song', 'text', 'b.title_unicode', 'tu', default='first')
field('creator', 'Mapper', 'Song', 'text', 'b.creator', 'mapper owner author set_owner set_mapper')
field('difficulty', 'Difficulty name', 'Song', 'text', 'b.difficulty', 'diff version difficulty_name')
field('source', 'Song source', 'Song', 'text', 'b.song_source', 'song_source')
field('tag', 'Tag', 'Song', 'tag', 'b.tags', 'tags song_tags')
field('audio', 'Audio file', 'Song', 'text', 'b.audio_file', 'audio_file')
field('collection', 'Collection', 'Library', 'collection', 'b.pk', 'col collections')

# Difficulty
field('mode', 'Mode', 'Difficulty', 'enum', 'b.mode', 'm ruleset', values=MODES)
field('bpm', 'BPM', 'Difficulty', 'number', 'b.bpm', widths=(20, 10, 50), unit=' BPM')
field('length', 'Length', 'Difficulty', 'duration', 'b.length_s', 'len time total_time')
field('drain', 'Drain time', 'Difficulty', 'duration', 'b.drain_s', 'drain_time')
for key, label, aliases in (('ar', 'Approach rate', 'approach_rate'), ('cs', 'Circle size', 'circle_size'),
                            ('od', 'Overall difficulty', 'overall_difficulty'), ('hp', 'HP drain', 'hp_drain')):
    field(key, label, 'Difficulty', 'number', f'b.{key}', aliases, widths=(1, 0.5), tol=0.05)
field('keys', 'Keys (mania)', 'Difficulty', 'number', "CASE WHEN b.mode = 'mania' THEN b.cs END", 'key', widths=(1,), whole=True)
field('sv', 'Slider velocity', 'Difficulty', 'number', 'b.sv', 'slider_velocity', widths=(0.5, 0.25), tol=0.005)
field('objects', 'Object count', 'Difficulty', 'number', 'b.objects', 'object_count', widths=(250, 100, 500, 1000), whole=True)
field('circles', 'Circles', 'Difficulty', 'number', 'b.circles', 'hitcircles num_hitcircles', widths=(100, 50, 250, 500), whole=True)
field('sliders', 'Sliders', 'Difficulty', 'number', 'b.sliders', 'num_sliders', widths=(100, 50, 250, 500), whole=True)
field('spinners', 'Spinners', 'Difficulty', 'number', 'b.spinners', 'num_spinners', widths=(1, 5, 10), whole=True)

# Calculated ratings, one field per skill and mod combination. Values are the calculator's own, rounded to 2 decimals.
RATING_SKILLS = (
    # key, label, label on rows, other names, bucket widths, unit
    ('stars', 'Star rating', '★', ('sr', 'star', 'star_rating'), (1, 0.5, 2), '★'),
    ('aim', 'Aim', 'aim', (), (0.5, 0.25, 1), ''),
    ('tapping', 'Tapping', 'tap', ('tap', 'speed'), (0.5, 0.25, 1), ''),
    ('reading', 'Reading', 'read', ('read',), (0.25, 0.5, 1), ''),
)


def combo_spellings(mods) -> list[str]:
    """Every way to write a combination in a field name: any order, and NC for DT."""
    if not mods:
        return ['nm']
    orders = [''.join(order).lower() for order in permutations(mods)]
    return sorted({*orders, *(order.replace('dt', 'nc') for order in orders)})


for key, label, short, names, widths, unit in RATING_SKILLS:
    for suffix, _, combo, mods in COMBOS:
        hidden = [f'{name}_{spelling}' for name in (key, *names) for spelling in combo_spellings(mods)]
        field(f'{key}{suffix}', f'{label} ({combo})', label, 'number', f'b.{key}{suffix}', ' '.join(f'{name}{suffix}' for name in names),
              widths=widths, unit=unit, column=f'{key}{suffix}', short=f'{short} {combo}', hidden_aliases=hidden, blank_label='(not rated)')

# Library
field('status', 'Ranked status', 'Library', 'enum', 'b.status', 'ranked_status', values=STATUSES)
field('library', 'In library', 'Library', 'enum', "CASE WHEN b.in_stable AND b.in_lazer THEN 'both' WHEN b.in_stable THEN 'stable' ELSE 'lazer' END", 'lib', values=LIBRARY)
field('beatmap_id', 'Beatmap ID', 'Library', 'number', 'b.beatmap_id', 'id', widths=(500000, 100000, 1000000), whole=True)
field('set_id', 'Set ID', 'Library', 'number', 'b.set_id', 'beatmap_set_id beatmapset', widths=(250000, 100000, 500000), whole=True)
field('diffs', 'Difficulties in set', 'Library', 'number', 'b.diffs', 'set_size', widths=(1, 5), whole=True)
field('offset', 'Local offset', 'Library', 'number', 'b.local_offset', 'local_offset', widths=(10, 5, 25))
field('last_played', 'Last played', 'Library', 'date', 'b.last_played', 'lastplayed')
field('last_modified', 'Last modified', 'Library', 'date', 'b.last_modified', 'updated')
field('date_added', 'Date added', 'Library', 'date', 'b.date_added', 'added')
field('date_ranked', 'Date ranked', 'Library', 'date', 'b.date_ranked', 'ranked')
field('date_submitted', 'Date submitted', 'Library', 'date', 'b.date_submitted', 'submitted')
field('played', 'Played', 'Library', 'bool', '(b.last_played IS NOT NULL)')
field('has_score', 'Has a score', 'Library', 'bool', '(b.score_count > 0)', 'scored')
field('score_count', 'Score count', 'Library', 'number', 'b.score_count', 'plays', widths=(1, 5, 10), whole=True)
field('rank_achieved', 'Rank achieved (stable)', 'Library', 'enum', 'b.rank_achieved', '', values=GRADES, ordered=True, value_aliases=GRADE_ALIASES)

# Score. In the beatmap view these describe the beatmap's best matching score.
field('player', 'Player', 'Score', 'text', 's.player', 'player_name')
field('grade', 'Grade', 'Score', 'enum', 's.grade', 'rank', values=GRADES, ordered=True, value_aliases=GRADE_ALIASES)
field('accuracy', 'Accuracy', 'Score', 'number', 's.accuracy', 'acc', widths=(5, 1, 10), unit='%', tol=0.005)
field('score', 'Score', 'Score', 'number', 's.total_score', 'replay_score total_score', widths=(100000, 50000, 250000, 1000000), whole=True)
field('combo', 'Max combo', 'Score', 'number', 's.max_combo', 'max_combo', widths=(250, 100, 500, 1000), whole=True)
field('fc', 'Full combo', 'Score', 'bool', 's.perfect', 'perfect pfc is_fc perfect_combo')
field('mods', 'Mods', 'Score', 'mods', 's.mods')
field('pp', 'Performance', 'Score', 'number', 's.pp', '', widths=(50, 25, 100), unit='pp', blank_label='(no pp)')
field('played_at', 'Played at', 'Score', 'date', 's.played_at', 'date')
field('n300', '300s', 'Score', 'number', 's.n300', '300s num_300s', widths=(250, 100, 500), whole=True)
field('n100', '100s', 'Score', 'number', 's.n100', '100s num_100s', widths=(10, 5, 25), whole=True)
field('n50', '50s', 'Score', 'number', 's.n50', '50s num_50s', widths=(5, 1, 10), whole=True)
field('geki', 'Gekis', 'Score', 'number', 's.ngeki', 'gekis num_gekis', widths=(50, 10, 100), whole=True)
field('katu', 'Katus', 'Score', 'number', 's.nkatu', 'katus num_katus', widths=(10, 5, 25), whole=True)
field('miss', 'Misses', 'Score', 'number', 's.nmiss', 'misses num_misses', widths=(1, 5, 10), whole=True)
field('score_source', 'Score from', 'Score', 'enum', 's.source', '', values=SOURCES)
field('score_mode', 'Score mode', 'Score', 'enum', 's.mode', '', values=MODES)

# The beatmap's rating under the score's own mods. Blank when those mods aren't a calculated combination.
for key, label, short, names, widths, unit in RATING_SKILLS:
    field(f'played_{key}', f'{label} as played', 'Score', 'number', f's.played_{key}', ' '.join(f'played_{name}' for name in names),
          widths=widths, unit=unit, column=f's_played_{key}', short=f'played {short}', blank_label='(not rated)')

BY_KEY = {f['key']: f for f in FIELDS}
LOOKUP = {name: f for f in FIELDS for name in [f['key'], *f['aliases'], *f.get('hidden_aliases', [])]}


def get(name: str):
    return LOOKUP.get(name.lower())


def duration_label(seconds) -> str:
    seconds = int(seconds)
    return f'{seconds // 60}:{seconds % 60:02d}'


def bucket_options(f) -> list[dict]:
    kind = f['type']
    if kind == 'text':
        options = [('value', 'Exact value'), ('first', 'First letter')]
        if f.get('default') == 'first':
            options.reverse()
    elif kind == 'number':
        options = [(str(w), f'Every {w:g}{f.get("unit", "")}') for w in f['widths']]
    elif kind == 'duration':
        options = [(str(w), f'Every {duration_label(w)}') for w in (60, 30, 120, 300)]
    elif kind == 'date':
        options = [('month', 'By month'), ('year', 'By year'), ('day', 'By day')]
    else:
        options = [('value', 'Each value')]
    return [{'value': v, 'label': label} for v, label in options]


def public() -> list[dict]:
    return [{
        'key': f['key'], 'label': f['label'], 'cat': f['cat'], 'type': f['type'], 'aliases': f['aliases'], 'score': f['score'],
        'buckets': bucket_options(f), 'values': [{'value': v, 'label': label} for v, label in f.get('values', [])],
        'sortable': f['type'] not in MULTI_VALUED, 'column': f.get('column'), 'short': f.get('short'),
    } for f in FIELDS]
