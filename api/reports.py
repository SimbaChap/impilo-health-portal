"""Custom reports: build tables and charts from the portal's indicators, save them, share them, export them.

  GET    /api/reports/meta              indicators, ways to group, starter templates, data range
  POST   /api/reports/run               {period, scope, sex, section} -> one section's table
  GET    /api/reports                   reports you can open: your own plus those shared with everyone
  GET    /api/reports/{id}
  PUT    /api/reports/{id}              create or update (owner only)
  DELETE /api/reports/{id}              owner or an administrator
  POST   /api/reports/export?format=xlsx|csv|pdf   {report} -> file (saved or not)

Reports are stored in api/report-data/reports.json. Figures come from the same register logic as the
registers, Overview and map (see geo.py), so totals match across the portal.
"""
import csv
import io
import json
import re
import secrets
import threading
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

import geo
from auth import current_user

router = APIRouter(prefix='/api/reports', tags=['reports'])

DATA_DIR = Path(__file__).with_name('report-data')
DATA_DIR.mkdir(exist_ok=True)
STORE = DATA_DIR / 'reports.json'
_lock = threading.Lock()

DIMENSIONS = {
    'none': 'Nothing (one total)',
    'period': 'Period',
    'province': 'Province',
    'district': 'District',
    'facility': 'Facility',
    'facilityType': 'Facility type',
    'sex': 'Sex',
    'ageGroup': 'Age group',
}
AGE_GROUPS = [('Under 5', 0, 4), ('5–14', 5, 14), ('15–24', 15, 24), ('25–49', 25, 49), ('50+', 50, None)]
MAX_INDICATORS = 8

TEMPLATES = [
    {'id': 'hiv', 'title': 'HIV summary', 'description': 'Testing, positives and ART by period and by sex.',
     'sections': [
         {'type': 'data', 'title': 'HIV testing and positives by period', 'indicators': ['htsTests', 'hivPositive', 'artNew'], 'rows': 'period', 'columns': 'none', 'chart': 'line'},
         {'type': 'data', 'title': 'HIV positive cases by age group and sex', 'indicators': ['hivPositive'], 'rows': 'ageGroup', 'columns': 'sex', 'chart': 'bar'},
         {'type': 'text', 'text': 'Notes: add your comments on the HIV figures here.'}]},
    {'id': 'maternal', 'title': 'Maternal health', 'description': 'ANC, deliveries, births and maternal deaths.',
     'sections': [
         {'type': 'data', 'title': 'Maternal health by period', 'indicators': ['ancBookings', 'deliveries', 'liveBirths', 'stillBirths', 'maternalDeaths'], 'rows': 'period', 'columns': 'none', 'chart': 'bar'},
         {'type': 'data', 'title': 'Maternal deaths by province', 'indicators': ['maternalDeaths'], 'rows': 'province', 'columns': 'none', 'chart': 'bar', 'hideEmpty': False}]},
    {'id': 'service', 'title': 'Monthly service use', 'description': 'Attendances and OPD visits per month, by sex.',
     'sections': [
         {'type': 'data', 'title': 'Attendances and OPD visits', 'indicators': ['attendances', 'opd'], 'rows': 'period', 'columns': 'none', 'chart': 'line'},
         {'type': 'data', 'title': 'Attendances by sex', 'indicators': ['attendances'], 'rows': 'period', 'columns': 'sex', 'chart': 'bar'}]},
    {'id': 'tbmalaria', 'title': 'TB and malaria', 'description': 'TB registrations and malaria tests and positives.',
     'sections': [
         {'type': 'data', 'title': 'TB and malaria by period', 'indicators': ['tbRegistered', 'malariaTests', 'malariaPositive'], 'rows': 'period', 'columns': 'none', 'chart': 'bar'},
         {'type': 'data', 'title': 'Malaria positive cases by age group', 'indicators': ['malariaPositive'], 'rows': 'ageGroup', 'columns': 'none', 'chart': 'pie'}]},
]


# ---------------------------------------------------------------- models
class Period(BaseModel):
    from_: date | None = Field(None, alias='from')
    to: date | None = None
    grain: Literal['month', 'quarter', 'year'] = 'month'
    model_config = {'populate_by_name': True}


class Scope(BaseModel):
    province: str | None = None
    district: str | None = None
    type: str | None = None
    facility: str | None = None


class Section(BaseModel):
    id: str | None = None
    type: Literal['data', 'text'] = 'data'
    title: str = Field('', max_length=200)
    text: str = Field('', max_length=20000)
    indicators: list[str] = []
    rows: str = 'period'
    columns: str = 'none'
    chart: Literal['table', 'bar', 'line', 'pie'] = 'table'
    showTable: bool = True
    hideEmpty: bool = True


class Report(BaseModel):
    id: str | None = None
    title: str = Field('Untitled report', max_length=200)
    description: str = Field('', max_length=2000)
    period: Period = Period()
    scope: Scope = Scope()
    sex: Literal['all', 'female', 'male'] = 'all'
    shared: bool = False
    sections: list[Section] = Field([], max_length=30)


class RunRequest(BaseModel):
    period: Period = Period()
    scope: Scope = Scope()
    sex: Literal['all', 'female', 'male'] = 'all'
    section: Section


# ---------------------------------------------------------------- engine
def _age_group(age):
    if age is None: return 'Unknown'
    for label, lo, hi in AGE_GROUPS:
        if hi is None or age <= hi: return label
    return 'Unknown'


def _context(period: Period, scope: Scope):
    m = geo._main()
    from_date, to_date, latest, periods = geo._range(period.from_, period.to, period.grain)
    def load(c):
        return (m._registry(c, scope.facility, scope.province, scope.district, scope.type),
                {p['province_id']: p['province_name'] for p in m._query(c, 'SELECT * FROM registry.province')},
                {d['district_id']: d for d in m._query(c, 'SELECT * FROM registry.district')},
                {t['type_code']: t for t in m._query(c, 'SELECT * FROM registry.facility_type ORDER BY sort_order')})
    facilities, provinces, districts, types = m._with_registry(load)
    names = []
    if scope.facility and facilities: names.append(facilities[0]['name'])
    else:
        if scope.province and scope.province in provinces: names.append(provinces[scope.province])
        if scope.district and scope.district in districts: names.append(districts[scope.district]['district_name'])
    if scope.type:
        names.append('Hospitals (all levels)' if scope.type.upper() == 'HOSPITAL' else types.get(scope.type.upper(), {}).get('type_label', scope.type))
    return {'m': m, 'from': from_date, 'to': to_date, 'latest': latest, 'periods': periods, 'facilities': facilities,
            'reporting': [f for f in facilities if f['hasData']], 'provinces': provinces, 'districts': districts, 'types': types,
            'scopeLabel': 'Zimbabwe' + (' › ' + ' › '.join(names) if names else '')}


def _axis(dim, ctx, hide_empty):
    """The categories for one way of grouping, in display order. Areas without data are kept (as
    'no data') unless hide_empty is set."""
    rep, fac = ctx['reporting'], ctx['facilities']
    if dim == 'none': return [{'key': 'all', 'label': 'Total'}]
    if dim == 'period': return [{'key': p['key'], 'label': p['label']} for p in ctx['periods']]
    if dim == 'sex': return [{'key': 'FEMALE', 'label': 'Female'}, {'key': 'MALE', 'label': 'Male'}, {'key': 'OTHER', 'label': 'Not recorded'}]
    if dim == 'ageGroup': return [{'key': g[0], 'label': g[0]} for g in AGE_GROUPS] + [{'key': 'Unknown', 'label': 'Age not recorded'}]
    if dim == 'province':
        ids = sorted({f['provinceId'] for f in (rep if hide_empty else fac) if f['provinceId']}, key=lambda i: ctx['provinces'].get(i, i))
        return [{'key': i, 'label': ctx['provinces'].get(i, i)} for i in ids]
    if dim == 'district':
        ids = sorted({f['districtId'] for f in (rep if hide_empty else fac) if f['districtId']}, key=lambda i: ctx['districts'].get(i, {}).get('district_name', i))
        return [{'key': i, 'label': ctx['districts'].get(i, {}).get('district_name', i)} for i in ids]
    if dim == 'facility':
        # Listing every facility without data would bury the table, so only those with data are shown.
        return [{'key': f['id'], 'label': f['name']} for f in sorted(rep, key=lambda f: f['name'])]
    if dim == 'facilityType':
        codes = {f['type'] for f in (rep if hide_empty else fac)}
        return [{'key': t, 'label': v['type_label']} for t, v in ctx['types'].items() if t in codes]
    raise HTTPException(400, f'Unknown grouping {dim!r}.')


def _key_fn(dim, ctx, tenant):
    idx = geo._bucketer(ctx['periods'])
    if dim == 'none': return lambda r: 'all'
    if dim == 'period': return lambda r: (ctx['periods'][i]['key'] if (i := idx(r['eventDate'])) is not None else None)
    if dim == 'sex': return lambda r: {'male': 'MALE', 'female': 'FEMALE'}.get(geo._sex(r), 'OTHER')
    if dim == 'ageGroup': return lambda r: _age_group(r.get('age'))
    # Single-tenant database: every loaded row belongs to the tenant facility.
    # Multi-tenant: read the facility from the row instead.
    value = {'province': tenant.get('provinceId'), 'district': tenant.get('districtId'), 'facility': tenant.get('id'),
             'facilityType': tenant.get('type')}[dim]
    return lambda r: value


def run_section(section: Section, period: Period, scope: Scope, sex: str, ctx=None):
    ctx = ctx or _context(period, scope)
    if section.type != 'data': return None
    inds = [i for i in section.indicators if i in geo.INDICATORS][:MAX_INDICATORS]
    if not inds: raise HTTPException(400, 'Choose at least one indicator.')
    for d in (section.rows, section.columns):
        if d not in DIMENSIONS: raise HTTPException(400, f'Unknown grouping {d!r}.')
    if section.rows == section.columns and section.rows != 'none': raise HTTPException(400, 'Rows and columns need to be grouped differently.')
    rows_axis = _axis(section.rows, ctx, section.hideEmpty)
    cols_axis = _axis(section.columns, ctx, section.hideEmpty)
    tenant = next((f for f in ctx['reporting'] if f['id'] == ctx['m'].TENANT_FACILITY_ID), None)
    geo_dims = {'province', 'district', 'facility', 'facilityType'}
    cells = {}
    for ind in inds:
        grid = {r['key']: {c['key']: None for c in cols_axis} for r in rows_axis}
        if tenant:
            rk, ck = _key_fn(section.rows, ctx, tenant), _key_fn(section.columns, ctx, tenant)
            counts = Counter()
            for r in geo._indicator_rows(ind, ctx['from'], ctx['to']):
                if sex != 'all' and geo._sex(r) != sex: continue
                counts[(rk(r), ck(r))] += 1
            # Cells that belong to the facility with data are real numbers (0 if nothing was recorded);
            # cells for areas with no data stay empty.
            for r in rows_axis:
                for c in cols_axis:
                    in_scope = (section.rows not in geo_dims or r['key'] == rk({})) and (section.columns not in geo_dims or c['key'] == ck({}))
                    if in_scope: grid[r['key']][c['key']] = counts.get((r['key'], c['key']), 0)
        cells[ind] = grid
    # Drop "Not recorded"/"Unknown" categories that are empty everywhere, to keep tables tidy.
    def used(axis, is_row):
        def total(a):
            if is_row: return sum((cells[i][a['key']][c['key']] or 0) for i in inds for c in cols_axis)
            return sum((cells[i][r['key']][a['key']] or 0) for i in inds for r in rows_axis)
        return [a for a in axis if a['key'] not in ('OTHER', 'Unknown') or total(a)]
    rows_axis, cols_axis = used(rows_axis, True), used(cols_axis, False)
    return {
        'title': section.title, 'rows': section.rows, 'columns': section.columns, 'chart': section.chart,
        'indicators': [{'key': i, 'label': geo.INDICATORS[i][0]} for i in inds],
        'rowAxis': rows_axis, 'colAxis': cols_axis,
        'cells': {i: {r['key']: {c['key']: cells[i][r['key']][c['key']] for c in cols_axis} for r in rows_axis} for i in inds},
        'reporting': len(ctx['reporting']), 'facilities': len(ctx['facilities']),
    }


def flat_table(res):
    """The section as one header row plus body rows and a total row, as shown on screen and in exports."""
    one_col = res['columns'] == 'none'
    cols = [(i, c) for i in res['indicators'] for c in res['colAxis']]
    def head(i, c):
        if one_col: return i['label']
        return c['label'] if len(res['indicators']) == 1 else f"{i['label']} · {c['label']}"
    header = [DIMENSIONS[res['rows']] if res['rows'] != 'none' else ''] + [head(i, c) for i, c in cols]
    body = []
    for r in res['rowAxis']:
        body.append([r['label']] + [res['cells'][i['key']][r['key']][c['key']] for i, c in cols])
    total = None
    if res['rows'] not in ('none',) and len(body) > 1:
        total = ['Total'] + [None if all(row[k + 1] is None for row in body) else sum(row[k + 1] or 0 for row in body) for k in range(len(cols))]
    return header, body, total


# ---------------------------------------------------------------- storage
def _load():
    if not STORE.exists(): return []
    try: return json.loads(STORE.read_text(encoding='utf-8'))
    except Exception: return []


def _save(items):
    tmp = STORE.with_suffix('.tmp')
    tmp.write_text(json.dumps(items, indent=1, default=str), encoding='utf-8')
    tmp.replace(STORE)


def _visible(r, user):
    return r['owner'] == user['username'] or r.get('shared')


def _summary(r, user):
    return {k: r.get(k) for k in ('id', 'title', 'description', 'owner', 'ownerName', 'shared', 'created', 'updated')} | {
        'mine': r['owner'] == user['username'], 'sections': len(r.get('sections', []))}


# ---------------------------------------------------------------- endpoints
@router.get('/meta')
def meta():
    m = geo._main()
    info = geo.indicators()
    return {'indicators': info['indicators'], 'dataStart': info['dataStart'], 'dataEnd': info['dataEnd'],
            'dimensions': [{'key': k, 'label': v} for k, v in DIMENSIONS.items()], 'templates': TEMPLATES,
            'maxIndicators': MAX_INDICATORS}


@router.post('/run')
def run(body: RunRequest):
    ctx = _context(body.period, body.scope)
    res = run_section(body.section, body.period, body.scope, body.sex, ctx)
    return res | {'scopeLabel': ctx['scopeLabel'], 'period': {'from': ctx['from'], 'to': ctx['to'], 'grain': body.period.grain}}


@router.get('')
def list_reports(request: Request):
    user = current_user(request)
    items = [_summary(r, user) for r in _load() if _visible(r, user)]
    return {'reports': sorted(items, key=lambda r: (not r['mine'], r.get('updated') or ''), reverse=False)}


@router.get('/{report_id}')
def get_report(report_id: str, request: Request):
    user = current_user(request)
    r = next((x for x in _load() if x['id'] == report_id), None)
    if not r or not _visible(r, user): raise HTTPException(404, 'Report not found.')
    return r | {'mine': r['owner'] == user['username']}


@router.put('/{report_id}')
def save_report(report_id: str, body: Report, request: Request):
    user = current_user(request)
    if not re.fullmatch(r'[a-z0-9-]{4,40}', report_id): raise HTTPException(400, 'Bad report id.')
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    with _lock:
        items = _load()
        old = next((x for x in items if x['id'] == report_id), None)
        if old and old['owner'] != user['username']:
            raise HTTPException(403, 'Only the person who made this report can change it. Make a copy to edit it.')
        data = body.model_dump(by_alias=True, mode='json')
        for s in data['sections']: s['id'] = s.get('id') or secrets.token_hex(4)
        rec = data | {'id': report_id, 'owner': user['username'], 'ownerName': user.get('name') or user['username'],
                      'created': old['created'] if old else now, 'updated': now}
        items = [x for x in items if x['id'] != report_id] + [rec]
        _save(items)
    return rec | {'mine': True}


@router.delete('/{report_id}')
def delete_report(report_id: str, request: Request):
    user = current_user(request)
    with _lock:
        items = _load()
        r = next((x for x in items if x['id'] == report_id), None)
        if not r or not _visible(r, user): raise HTTPException(404, 'Report not found.')
        if r['owner'] != user['username'] and user['role'] != 'admin':
            raise HTTPException(403, 'Only the person who made this report, or an administrator, can delete it.')
        _save([x for x in items if x['id'] != report_id])
    return {'deleted': report_id}


# ---------------------------------------------------------------- export
def _safe(v):
    """Spreadsheet-safe text: never let a cell start like a formula."""
    if isinstance(v, str) and v[:1] in ('=', '+', '-', '@', '\t', '\r'): return "'" + v
    return v


def _filename(title, ext):
    return (re.sub(r'[^A-Za-z0-9]+', '-', title).strip('-') or 'report')[:60] + f'-{date.today().isoformat()}.{ext}'


def _render(report: Report):
    ctx = _context(report.period, report.scope)
    out = []
    for s in report.sections:
        if s.type == 'text': out.append(('text', s, None))
        elif s.indicators: out.append(('data', s, run_section(s, report.period, report.scope, report.sex, ctx)))
    return ctx, out


def _period_text(ctx, grain):
    f = lambda d: d.strftime('%d %b %Y')
    return f"{f(ctx['from'])} – {f(ctx['to'])} · by {grain}"


@router.post('/export')
def export(report: Report, format: Literal['xlsx', 'csv', 'pdf'] = Query(...)):
    ctx, parts = _render(report)
    sex_note = {'female': ' · females only', 'male': ' · males only'}.get(report.sex, '')
    meta_line = f"{ctx['scopeLabel']} · {_period_text(ctx, report.period.grain)}{sex_note}"
    if format == 'csv': return _csv(report, meta_line, parts)
    if format == 'xlsx': return _xlsx(report, meta_line, parts)
    return _pdf(report, meta_line, parts)


def _csv(report, meta_line, parts):
    buf = io.StringIO(); w = csv.writer(buf)
    w.writerow([_safe(report.title)]); w.writerow([_safe(meta_line)])
    if report.description: w.writerow([_safe(report.description)])
    for kind, s, res in parts:
        w.writerow([])
        if kind == 'text': w.writerow([_safe(s.text)]); continue
        w.writerow([_safe(s.title or 'Table')])
        header, body, total = flat_table(res)
        w.writerow([_safe(h) for h in header])
        for row in body + ([total] if total else []): w.writerow([_safe(v) if v is not None else '' for v in row])
    return Response('﻿' + buf.getvalue(), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="{_filename(report.title, "csv")}"'})


def _xlsx(report, meta_line, parts):
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, LineChart, PieChart, Reference
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook(); ws = wb.active; ws.title = 'Report'
    ws['A1'] = _safe(report.title); ws['A1'].font = Font(bold=True, size=15, color='14532D')
    ws['A2'] = _safe(meta_line); ws['A2'].font = Font(color='64748B')
    row = 3
    if report.description: ws.cell(row, 1, _safe(report.description)).alignment = Alignment(wrap_text=True); row += 1
    head_fill = PatternFill('solid', fgColor='218A39')
    widest = 1
    for kind, s, res in parts:
        row += 1
        if kind == 'text':
            c = ws.cell(row, 1, _safe(s.text)); c.alignment = Alignment(wrap_text=True, vertical='top')
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=8); ws.row_dimensions[row].height = max(30, 15 * (1 + len(s.text) // 110))
            row += 1; continue
        ws.cell(row, 1, _safe(s.title or 'Table')).font = Font(bold=True, size=12); row += 1
        header, body, total = flat_table(res)
        widest = max(widest, len(header))
        for j, h in enumerate(header, 1):
            c = ws.cell(row, j, _safe(h)); c.font = Font(bold=True, color='FFFFFF'); c.fill = head_fill; c.alignment = Alignment(wrap_text=True)
        top = row
        for r in body:
            row += 1
            for j, v in enumerate(r, 1): ws.cell(row, j, _safe(v) if v is not None else '–' if j > 1 else '')
        last_body = row
        if total:
            row += 1
            for j, v in enumerate(total, 1): ws.cell(row, j, v if v is not None else '').font = Font(bold=True)
        if s.chart != 'table' and body:
            chart = {'bar': BarChart, 'line': LineChart, 'pie': PieChart}[s.chart]()
            chart.title = s.title or None; chart.height = 7.5; chart.width = 16
            ncols = 1 if s.chart == 'pie' else len(header) - 1
            chart.add_data(Reference(ws, min_col=2, max_col=1 + ncols, min_row=top, max_row=last_body), titles_from_data=True)
            chart.set_categories(Reference(ws, min_col=1, min_row=top + 1, max_row=last_body))
            ws.add_chart(chart, f'{get_column_letter(len(header) + 2)}{top}')
            row = max(row, top + 15)
        row += 1
    ws.column_dimensions['A'].width = 28
    for j in range(2, widest + 1): ws.column_dimensions[get_column_letter(j)].width = 16
    buf = io.BytesIO(); wb.save(buf)
    return Response(buf.getvalue(), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': f'attachment; filename="{_filename(report.title, "xlsx")}"'})


def _colour(name, k):
    """Female and male keep the portal's colours whenever a table is split by sex."""
    for suffix, col in (('Female', '#c0367a'), ('Male', '#2563eb'), ('Not recorded', '#94a3b8')):
        if name == suffix or name.endswith(' · ' + suffix): return col
    return PALETTE[k % len(PALETTE)]


PALETTE = ['#218a39', '#2563eb', '#d97706', '#c0367a', '#0d9488', '#7c3aed', '#dc2626', '#475569']


def _pdf(report, meta_line, parts):
    from xml.sax.saxutils import escape
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics.charts.legends import Legend
    from reportlab.graphics.charts.linecharts import HorizontalLineChart
    from reportlab.graphics.charts.piecharts import Pie
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    wide = any(kind == 'data' and len(flat_table(res)[0]) > 7 for kind, _, res in parts)
    size = landscape(A4) if wide else A4
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=size, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=26 * mm, bottomMargin=16 * mm,
                            title=report.title, author='National Health Portal')
    width = size[0] - 32 * mm
    H = ParagraphStyle('h', fontName='Helvetica-Bold', fontSize=17, leading=21, textColor=colors.HexColor('#14532d'))
    M = ParagraphStyle('m', fontName='Helvetica', fontSize=9, leading=12, textColor=colors.HexColor('#64748b'))
    S = ParagraphStyle('s', fontName='Helvetica-Bold', fontSize=12, leading=15, spaceBefore=10, spaceAfter=5, textColor=colors.HexColor('#0f172a'))
    T = ParagraphStyle('t', fontName='Helvetica', fontSize=10, leading=14, textColor=colors.HexColor('#1f2937'))
    C = ParagraphStyle('c', fontName='Helvetica', fontSize=8, leading=10)
    CB = ParagraphStyle('cb', parent=C, fontName='Helvetica-Bold', textColor=colors.white)

    def chrome(canvas, d):
        canvas.saveState()
        stripe = ['#218a39', '#f5c400', '#d21f26', '#000000', '#d21f26', '#f5c400', '#218a39']
        w = size[0] / len(stripe)
        for k, col in enumerate(stripe):
            canvas.setFillColor(colors.HexColor(col)); canvas.rect(k * w, size[1] - 4, w + 1, 4, stroke=0, fill=1)
        canvas.setFillColor(colors.HexColor('#14532d')); canvas.setFont('Helvetica-Bold', 9)
        canvas.drawString(16 * mm, size[1] - 13 * mm, 'MINISTRY OF HEALTH AND CHILD CARE')
        canvas.setFillColor(colors.HexColor('#64748b')); canvas.setFont('Helvetica', 8.5)
        canvas.drawString(16 * mm, size[1] - 17.5 * mm, 'National Health Portal · Custom report')
        canvas.drawRightString(size[0] - 16 * mm, size[1] - 13 * mm, datetime.now().strftime('Printed %d %b %Y, %H:%M'))
        canvas.setStrokeColor(colors.HexColor('#e2e8f0')); canvas.line(16 * mm, size[1] - 20 * mm, size[0] - 16 * mm, size[1] - 20 * mm)
        canvas.setFont('Helvetica', 8); canvas.drawRightString(size[0] - 16 * mm, 9 * mm, f'Page {d.page}')
        canvas.drawString(16 * mm, 9 * mm, 'Figures from facilities with data loaded; empty cells mean no data, not zero.')
        canvas.restoreState()

    story = [Paragraph(escape(report.title), H), Spacer(1, 2), Paragraph(escape(meta_line), M)]
    if report.description: story += [Spacer(1, 6), Paragraph(escape(report.description), T)]
    for kind, s, res in parts:
        if kind == 'text':
            story += [Spacer(1, 8)] + [Paragraph(escape(p).replace('\n', '<br/>'), T) for p in s.text.split('\n\n') if p.strip()]
            continue
        header, body, total = flat_table(res)
        block = [Paragraph(escape(s.title or 'Table'), S)]
        if s.chart != 'table' and body:
            d = Drawing(width, 62 * mm)
            series = [[(row[k] or 0) for row in body] for k in range(1, len(header))]
            cats = [str(row[0]) for row in body]
            if s.chart == 'pie':
                pie = Pie(); pie.x, pie.y, pie.width, pie.height = 10 * mm, 4 * mm, 52 * mm, 52 * mm
                pie.data = series[0] if sum(series[0]) else [1]; pie.labels = None
                for k in range(len(pie.data)): pie.slices[k].fillColor = colors.HexColor(PALETTE[k % len(PALETTE)]); pie.slices[k].strokeColor = colors.white
                d.add(pie)
                lg = Legend(); lg.x, lg.y = 72 * mm, 54 * mm; lg.fontName = 'Helvetica'; lg.fontSize = 8; lg.alignment = 'right'
                lg.colorNamePairs = [(colors.HexColor(PALETTE[k % len(PALETTE)]), f'{cats[k]}: {series[0][k]:,}') for k in range(len(cats))]
                d.add(lg)
            else:
                ch = VerticalBarChart() if s.chart == 'bar' else HorizontalLineChart()
                ch.x, ch.y, ch.width, ch.height = 12 * mm, 12 * mm, width - (58 * mm if len(series) > 1 else 16 * mm), 46 * mm
                ch.data = series
                ch.categoryAxis.categoryNames = cats; ch.categoryAxis.labels.fontSize = 7; ch.categoryAxis.labels.fontName = 'Helvetica'
                if len(cats) > 8: ch.categoryAxis.labels.angle = 35; ch.categoryAxis.labels.boxAnchor = 'ne'
                ch.valueAxis.labels.fontSize = 7; ch.valueAxis.valueMin = 0; ch.valueAxis.gridStrokeColor = colors.HexColor('#e2e8f0'); ch.valueAxis.visibleGrid = 1
                for k in range(len(series)):
                    col = colors.HexColor(_colour(header[k + 1], k))
                    if s.chart == 'bar': ch.bars[k].fillColor = col; ch.bars[k].strokeColor = None
                    else: ch.lines[k].strokeColor = col; ch.lines[k].strokeWidth = 1.8
                d.add(ch)
                if len(series) > 1:
                    lg = Legend(); lg.x, lg.y = width - 44 * mm, 56 * mm; lg.fontName = 'Helvetica'; lg.fontSize = 7.5; lg.alignment = 'right'
                    lg.colorNamePairs = [(colors.HexColor(_colour(header[k + 1], k)), header[k + 1][:34]) for k in range(len(series))]
                    d.add(lg)
            block.append(d)
        if s.showTable or s.chart == 'table':
            data = [[Paragraph(escape(str(h)), CB) for h in header]]
            for r in body + ([total] if total else []):
                data.append([Paragraph(escape(str(r[0])), C)] + [f'{v:,}' if isinstance(v, int) else '–' for v in r[1:]])
            first = min(55 * mm, width * .32)
            rest = (width - first) / max(1, len(header) - 1)
            t = Table(data, colWidths=[first] + [rest] * (len(header) - 1), repeatRows=1)
            style = [('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#218a39')), ('FONTSIZE', (0, 1), (-1, -1), 8),
                     ('ALIGN', (1, 1), (-1, -1), 'RIGHT'), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                     ('GRID', (0, 0), (-1, -1), .4, colors.HexColor('#e2e8f0')),
                     ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8faf9')])]
            if total: style += [('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'), ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#ecf7ee'))]
            t.setStyle(TableStyle(style))
            block.append(t)
        story += [KeepTogether(block[:2]) if len(block) > 2 else KeepTogether(block)] + (block[2:] if len(block) > 2 else [])
    doc.build(story, onFirstPage=chrome, onLaterPages=chrome)
    return Response(buf.getvalue(), media_type='application/pdf',
                    headers={'Content-Disposition': f'attachment; filename="{_filename(report.title, "pdf")}"'})
