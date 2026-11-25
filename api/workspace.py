"""Data workspace: explore the row-level data behind the portal, and (administrators) run read-only SQL.

  GET  /api/workspace/datasets                 datasets and their columns
  POST /api/workspace/query                    {dataset, from, to, columns, filters, search, sort, page, pageSize}
  POST /api/workspace/export?format=csv|xlsx   same body, every matching row (needs the raw-data download right)
  GET  /api/workspace/schema                   tables and columns the portal's database account can read (admins)
  POST /api/workspace/sql                      {sql} -> up to 5,000 rows (admins; SELECT only, 30-second limit)
  POST /api/workspace/sql/export?format=csv    the same query as a file (admins)

Patient details are hidden for people without patient-level access (see access.py), here as everywhere.
"""
import csv
import io
import re
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

import access
import geo
from auth import require_admin

router = APIRouter(prefix='/api/workspace', tags=['workspace'])

DATASETS = {
    'attendance':  ('Attendance register', 'attendance'),
    'opd-general': ('OPD General register', 'opd-general'),
    'hts':         ('HTS (HIV testing) register', 'hts'),
    'malaria':     ('Malaria register', 'malaria'),
    'anc':         ('ANC register', 'anc'),
    'art':         ('ART register', 'art'),
    'tb':          ('TB register', 'tb'),
    'delivery':    ('Delivery register', 'delivery'),
    'facilities':  ('Facility registry', None),
}
MAX_EXPORT = 200_000
SQL_ROWS = 5000


class Filter(BaseModel):
    column: str
    op: Literal['contains', 'equals', 'not', 'gt', 'lt', 'empty', 'notempty', 'starts'] = 'contains'
    value: str = ''


class QueryRequest(BaseModel):
    dataset: str
    from_: date | None = Field(None, alias='from')
    to: date | None = None
    columns: list[str] = []
    filters: list[Filter] = Field([], max_length=20)
    search: str = Field('', max_length=200)
    sort: str | None = None
    desc: bool = False
    page: int = Field(1, ge=1)
    pageSize: int = Field(100, ge=1, le=1000)
    model_config = {'populate_by_name': True}


class SqlRequest(BaseModel):
    sql: str = Field(max_length=20000)


# ---------------------------------------------------------------- loading rows
def _humanise(key):
    words = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', ' ', key).replace('_', ' ').strip()
    return words[:1].upper() + words[1:].lower()


def _labels():
    """Column names as the registers show them (taken from the register definitions where they exist)."""
    import main
    out = {}
    for name in dir(main):
        v = getattr(main, name)
        if name.endswith('FIELDS') and isinstance(v, list) and v and isinstance(v[0], tuple) and len(v[0]) == 2:
            for k, label in v: out.setdefault(k, label)
    return out


def _flat(row):
    out = {}
    for k, v in row.items():
        if isinstance(v, list): out[k + 'Count'] = len(v)
        elif isinstance(v, dict): continue
        else: out[k] = v
    return out


def _rows(dataset, start, end):
    m = geo._main()
    if dataset == 'facilities':
        rows = m._with_registry(lambda c: m._registry(c))
        return [{k: r.get(k) for k in ('id', 'name', 'type', 'typeLabel', 'typeGroup', 'province', 'provinceId', 'district', 'districtId',
                                       'latitude', 'longitude', 'hasData')} for r in rows]
    source = DATASETS[dataset][1]
    # Not cached across requests: rows are masked per caller.
    if source in ('attendance', 'opd-general', 'hts', 'malaria'): rows = m.corrected_data(source, start, end, None)[0]
    elif source == 'anc': rows = m.anc_data(start, end, None)[0]
    elif source == 'art': rows = m.art_data(start, end, None)[0]
    elif source == 'tb': rows = m.tb_data(start, end, None)[0]
    else: rows = m.delivery_data(start, end, None)[0]
    return [_flat(r) for r in rows]


def _period(req):
    m = geo._main()
    site = m._with_registry(lambda c: m._facility(c))
    latest = site['latestDate'] or date.today()
    end = min(req.to or latest, latest)
    start = req.from_ or m._month_start(end, 2)
    if start > end: raise HTTPException(400, "'From' must be on or before 'to'.")
    if (end - start).days > 366 * 11: raise HTTPException(400, 'Choose at most ten years at a time.')
    return start, end


def _text(v):
    if v is None: return ''
    if isinstance(v, (date, datetime)): return v.isoformat()
    return str(v)


def _apply(rows, req, columns):
    def num(x):
        try: return float(str(x).replace(',', ''))
        except ValueError: return None
    for f in req.filters:
        if f.column not in columns: continue
        val = f.value.strip().lower()
        def keep(r, f=f, val=val):
            cell = _text(r.get(f.column)).lower()
            if f.op == 'empty': return cell == ''
            if f.op == 'notempty': return cell != ''
            if f.op == 'equals': return cell == val
            if f.op == 'not': return cell != val
            if f.op == 'starts': return cell.startswith(val)
            if f.op in ('gt', 'lt'):
                a, b = num(cell), num(val)
                if a is None or b is None: return (cell > val) if f.op == 'gt' else (cell < val)
                return a > b if f.op == 'gt' else a < b
            return val in cell
        rows = [r for r in rows if keep(r)]
    if req.search.strip():
        q = req.search.strip().lower()
        rows = [r for r in rows if any(q in _text(r.get(c)).lower() for c in columns)]
    if req.sort and req.sort in columns:
        def key(r):
            v = r.get(req.sort)
            n = None if isinstance(v, str) else v
            return (v is None or v == '', 0 if isinstance(n, (int, float)) else 1, n if isinstance(n, (int, float)) else _text(v).lower())
        rows = sorted(rows, key=key, reverse=req.desc)
        if req.desc:  # keep blanks last either way
            rows = [r for r in rows if r.get(req.sort) not in (None, '')] + [r for r in rows if r.get(req.sort) in (None, '')]
    return rows


def _run(req: QueryRequest):
    if req.dataset not in DATASETS: raise HTTPException(404, 'Unknown dataset.')
    start, end = _period(req) if req.dataset != 'facilities' else (None, None)
    rows = _rows(req.dataset, start, end)
    labels = _labels()
    all_cols = list(dict.fromkeys(k for r in rows[:500] for k in r))
    cols = [c for c in req.columns if c in all_cols] or all_cols
    out = _apply(rows, req, all_cols)
    masked = access.masking()
    meta = [{'key': c, 'label': labels.get(c) or _humanise(c), 'sensitive': c in access.SENSITIVE, 'masked': masked and c in access.SENSITIVE} for c in all_cols]
    return out, cols, meta, start, end


def _cell(v):
    if isinstance(v, (date, datetime)): return v.isoformat()
    if isinstance(v, Decimal): return float(v)
    return v


@router.get('/datasets')
def datasets(request: Request):
    perm = request.state.perm
    return {'datasets': [{'key': k, 'label': v[0], 'hasPeriod': k != 'facilities'} for k, v in DATASETS.items()],
            'patientData': perm['patientData'], 'canDownload': perm['downloads']['raw'],
            'isAdmin': request.state.user.get('role') == 'admin'}


@router.post('/query')
def query(req: QueryRequest):
    t0 = time.time()
    rows, cols, meta, start, end = _run(req)
    total = len(rows)
    a = (req.page - 1) * req.pageSize
    page = [{c: _cell(r.get(c)) for c in cols} for r in rows[a:a + req.pageSize]]
    return {'dataset': req.dataset, 'columns': meta, 'shown': cols, 'rows': page, 'total': total,
            'page': req.page, 'pageSize': req.pageSize, 'from': start, 'to': end, 'masked': access.masking(),
            'seconds': round(time.time() - t0, 2)}


def _filename(stem, ext):
    return re.sub(r'[^A-Za-z0-9-]+', '-', stem).strip('-')[:60] + f'-{date.today().isoformat()}.{ext}'


def _file(header, rows, fmt, stem, title):
    if fmt == 'csv':
        buf = io.StringIO(); w = csv.writer(buf)
        safe = lambda v: "'" + v if isinstance(v, str) and v[:1] in ('=', '+', '-', '@') else v
        w.writerow(header)
        for r in rows: w.writerow([safe(_text(v)) if not isinstance(v, (int, float)) else v for v in r])
        return Response('﻿' + buf.getvalue(), media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition': f'attachment; filename="{_filename(stem, "csv")}"'})
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font, PatternFill
    wb = Workbook(write_only=True); ws = wb.create_sheet(title[:31])
    head = []
    for h in header:
        c = WriteOnlyCell(ws, value=h); c.font = Font(bold=True, color='FFFFFF'); c.fill = PatternFill('solid', fgColor='218A39'); head.append(c)
    ws.append(head)
    for r in rows:
        ws.append([("'" + v if isinstance(v, str) and v[:1] in ('=', '+', '-', '@') else v) if not isinstance(v, (date, datetime, Decimal)) else _cell(v) for v in r])
    buf = io.BytesIO(); wb.save(buf)
    return Response(buf.getvalue(), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': f'attachment; filename="{_filename(stem, "xlsx")}"'})


@router.post('/export')
def export(req: QueryRequest, format: Literal['csv', 'xlsx'] = Query('csv')):
    rows, cols, meta, start, end = _run(req)
    if len(rows) > MAX_EXPORT: raise HTTPException(400, f'That is {len(rows):,} rows. Narrow the period or add filters (limit {MAX_EXPORT:,}).')
    labels = {c['key']: c['label'] for c in meta}
    stem = req.dataset + (f'-{start}-{end}' if start else '')
    return _file([labels.get(c, c) for c in cols], ([_cell(r.get(c)) for c in cols] for r in rows), format, stem, DATASETS[req.dataset][0])


# ---------------------------------------------------------------- SQL (administrators)
_ALLOWED = re.compile(r'^\s*(select|with|show|describe|desc|explain)\b', re.I)
_FORBIDDEN = re.compile(r'\b(into\s+(out|dump)file|load_file|sleep|benchmark|insert|update|delete|drop|alter|create|truncate|grant|revoke|lock|unlock|call|handler|load)\b', re.I)


def _strip_comments(sql):
    sql = re.sub(r'/\*.*?\*/', ' ', sql, flags=re.S)
    return re.sub(r'(--|#)[^\n]*', ' ', sql)


def _check_sql(sql):
    body = _strip_comments(sql).strip().rstrip(';').strip()
    if not body: raise HTTPException(400, 'Write a query first.')
    # Quoted text can contain anything; check the statement with string literals blanked out.
    bare = re.sub(r"'(?:''|\\.|[^'])*'|\"(?:\"\"|\\.|[^\"])*\"", "''", body)
    if ';' in bare: raise HTTPException(400, 'Run one statement at a time.')
    if not _ALLOWED.match(bare): raise HTTPException(400, 'Only read-only queries are allowed (SELECT, WITH, SHOW, DESCRIBE, EXPLAIN).')
    m = _FORBIDDEN.search(bare)
    if m: raise HTTPException(400, f'"{m.group(0).upper()}" is not allowed here. The workspace is read-only.')
    return body


def _execute(sql, limit):
    m = geo._main(); c = None
    try:
        c = m.connect(); cur = c.cursor()
        is_select = re.match(r'^\s*(select|with)\b', sql, re.I)
        # MariaDB stops the statement after 30 seconds.
        cur.execute(('SET STATEMENT max_statement_time=30 FOR ' if is_select else '') + sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(limit + 1) if cols else []
        return cols, rows
    except HTTPException: raise
    except Exception as e:
        msg = getattr(e, 'msg', None) or str(e)
        raise HTTPException(400, f'The database said: {msg}')
    finally:
        if c is not None:
            try: c.close()
            except Exception: pass


@router.get('/schema')
def schema(request: Request):
    require_admin(request)
    cols, rows = _execute("""SELECT table_schema,table_name,column_name,data_type FROM information_schema.columns
        WHERE table_schema NOT IN ('information_schema','mysql','performance_schema','sys') ORDER BY table_schema,table_name,ordinal_position""", 100000)
    tables = {}
    for s, t, c, d in rows:
        tables.setdefault(f'{s}.{t}', []).append({'name': c, 'type': d})
    return {'tables': [{'name': k, 'columns': v} for k, v in tables.items()]}


@router.post('/sql')
def run_sql(req: SqlRequest, request: Request):
    require_admin(request)
    sql = _check_sql(req.sql)
    t0 = time.time()
    cols, rows = _execute(sql, SQL_ROWS)
    more = len(rows) > SQL_ROWS
    return {'columns': cols, 'rows': [[_cell(v) for v in r] for r in rows[:SQL_ROWS]], 'truncated': more,
            'count': min(len(rows), SQL_ROWS), 'seconds': round(time.time() - t0, 2)}


@router.post('/sql/export')
def export_sql(req: SqlRequest, request: Request, format: Literal['csv', 'xlsx'] = Query('csv')):
    require_admin(request)
    sql = _check_sql(req.sql)
    cols, rows = _execute(sql, MAX_EXPORT)
    return _file(cols, ([_cell(v) for v in r] for r in rows[:MAX_EXPORT]), format, 'query', 'Query')
