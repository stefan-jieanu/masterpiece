"""HTTP API plus the static frontend, all on one origin. Run: uvicorn app.main:app --port 8000"""
import json
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import fields, query, tapping, tapping_query
from .db import DB_PATH, SCHEMA_VERSION, connect, schema_version

app = FastAPI(title='masterpiece')


class ListRequest(BaseModel):
    view: str = 'beatmaps'
    q: str = ''
    group: dict | None = None
    sort: list[dict] = []
    best_by: str = 'score'
    offset: int = 0
    limit: int = 200


class GroupRequest(BaseModel):
    by: str
    bucket: str | None = None
    view: str = 'beatmaps'
    q: str = ''
    best_by: str = 'score'


class TappingRequest(BaseModel):
    q: str = ''
    status: str = 'rated'
    version: str = 'a'
    compare: str = ''
    sort: str = 'value_stream'
    direction: str = 'desc'
    scale: str = 'scaled'
    offset: int = 0
    limit: int = 100


@app.exception_handler(query.QueryError)
def bad_query(_: Request, error: query.QueryError):
    return JSONResponse(status_code=400, content={'detail': str(error)})


@contextmanager
def library():
    if not DB_PATH.exists():
        raise HTTPException(503, 'No library yet. Run the importer first: python -m app.importer --stable DIR --lazer DIR')
    conn = connect(DB_PATH)
    try:
        if schema_version(conn) != SCHEMA_VERSION:
            raise HTTPException(503, 'This library was built by an older version of the app. Run the importer again: python -m app.importer --stable DIR --lazer DIR')
        yield conn
    finally:
        # A connection left open stops a re-import from replacing the file on Windows.
        conn.close()


@app.get('/api/fields')
def get_fields():
    return fields.public()


@app.get('/api/meta')
def get_meta():
    with library() as conn:
        data = query.meta(conn)
    data['report'] = json.loads(data['report'])
    return data


@app.post('/api/list')
def post_list(req: ListRequest):
    with library() as conn:
        return query.list_rows(conn, **req.model_dump())


@app.post('/api/groups')
def post_groups(req: GroupRequest):
    with library() as conn:
        return query.groups(conn, **req.model_dump())


@app.get('/api/beatmap/{md5}')
def get_beatmap(md5: str):
    with library() as conn:
        data = query.beatmap_detail(conn, md5)
    if not data:
        raise HTTPException(404, 'No such beatmap')
    return data


@app.get('/api/tapping/meta')
def get_tapping_meta():
    return tapping.public()


@app.post('/api/tapping/list')
def post_tapping_list(req: TappingRequest):
    with library() as conn:
        return tapping_query.list_maps(conn, **req.model_dump())


app.mount('/', StaticFiles(directory=Path(__file__).resolve().parents[2] / 'frontend', html=True))
