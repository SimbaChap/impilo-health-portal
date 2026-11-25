"""Geospatial intelligence: indicators over time on the map, split by sex, plus patient movements.

  GET /api/geo/indicators   indicators the map can show, with definitions
  GET /api/geo/series       ?indicator=&from=&to=&grain=month|quarter|year&province=&district=&type=
                            per facility in scope: values per period, and the same split by sex
  GET /api/geo/movements    ?indicator=&from=&to=&grain=
                            where the patients counted live (home place -> facility), and transfers
                            between facilities (ART and TB registers)
  GET /api/geo/profile      ?from=&to=&grain=   every indicator for the facilities with data, for area profiles
  GET /api/geo/places       the place list used to read home addresses (api/places.csv)

The page adds facilities up by province and by district boundary itself, so one answer serves
every zoom level.
"""
import csv
import re
import time
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix='/api/geo', tags=['geospatial'])

POSITIVE = lambda field: lambda r: (r.get(field) or '').upper() == 'POSITIVE'
MATERNAL_DIAGNOSIS = re.compile(r'pregnan|obstet|matern|eclamp|postpartum|post-partum|puerper|abortion|miscarr|ectopic|antepartum|intrapartum|uterine rupture', re.I)

# key: (label, group, source, keep-filter, how to count, definition)
INDICATORS = {
    'attendances':    ('Attendances', 'Service use', 'attendance', None, 'rows', 'Every recorded patient attendance.'),
    'opd':            ('OPD General visits', 'Service use', 'opd-general', None, 'rows', 'Visits recorded in the OPD General register.'),
    'htsTests':       ('HIV tests (HTS)', 'HIV', 'hts', None, 'rows', 'HIV tests recorded in the HTS register.'),
    'hivPositive':    ('HIV positive cases', 'HIV', 'hts', POSITIVE('testResult'), 'rows', 'HTS tests with a positive result.'),
    'artNew':         ('New on ART', 'HIV', 'art', None, 'rows', 'People registered on ART.'),
    'ancBookings':    ('ANC registrations', 'Maternal health', 'anc', None, 'rows', 'Antenatal care registrations.'),
    'deliveries':     ('Deliveries', 'Maternal health', 'delivery', None, 'deliveries', 'Deliveries recorded in the delivery register.'),
    'liveBirths':     ('Live births', 'Maternal health', 'delivery', lambda r: r.get('birthKind') == 'live', 'rows', 'Live births recorded at delivery.'),
    'stillBirths':    ('Still births', 'Maternal health', 'delivery', lambda r: r.get('birthKind') == 'still', 'rows', 'Still births recorded at delivery.'),
    'maternalDeaths': ('Maternal deaths', 'Maternal health', 'maternal-deaths', None, 'rows',
                       'Mothers discharged as deceased after delivery, plus deaths in the death register whose cause is pregnancy-related. '
                       'Counted once per woman.'),
    'deaths':         ('All deaths', 'Mortality', 'deaths', None, 'rows', 'Entries in the death register.'),
    'tbRegistered':   ('TB registrations', 'TB & malaria', 'tb', None, 'rows', 'People registered for TB treatment.'),
    'malariaTests':   ('Malaria tests', 'TB & malaria', 'malaria', None, 'rows', 'Malaria tests recorded.'),
    'malariaPositive':('Malaria positive cases', 'TB & malaria', 'malaria', POSITIVE('investigationResult'), 'rows', 'Malaria tests with a positive result.'),
}

CACHE_SECONDS = 600
_cache = {}


def _cached(key, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS: return hit[1]
    value = fn()
    _cache[key] = (time.time(), value)
    if len(_cache) > 200:  # keep memory bounded: drop the oldest entries
        for k in sorted(_cache, key=lambda k: _cache[k][0])[:50]: _cache.pop(k, None)
    return value


def _main():
    import main  # the app module; imported late to avoid a circular import
    return main


def _query(sql, args=()):
    m = _main(); c = None
    try:
        c = m.connect()
        return m._query(c, sql, args)
    finally:
        if c is not None: c.close()


def _deaths(start, end, maternal_only=False):
    rows = _query("""SELECT d.person_id,d.date_of_death,d.diagnosis,p.sex FROM report.death_register d
                     LEFT JOIN report.person_demographic p ON p.person_id=d.person_id WHERE d.date_of_death BETWEEN %s AND %s""", (start, end))
    if maternal_only: rows = [r for r in rows if MATERNAL_DIAGNOSIS.search(r['diagnosis'] or '')]
    return [{'eventDate': r['date_of_death'], 'personId': r['person_id'], 'sex': r['sex']} for r in rows]


def _source_rows(source, start, end):
    """Register rows for one source and period, shared by every indicator built on that source."""
    def load():
        m = _main()
        if source in ('attendance', 'opd-general', 'hts', 'malaria'): return m.corrected_data(source, start, end, None)[0]
        if source == 'anc': return m.anc_data(start, end, None)[0]
        if source == 'art': return m.art_data(start, end, None)[0]
        if source == 'tb': return m.tb_data(start, end, None)[0]
        if source == 'delivery': return m.delivery_data(start, end, None)[0]
        if source == 'deaths': return _deaths(start, end)
        if source == 'maternal-deaths':
            out, seen = [], set()
            for r in _source_rows('delivery', start, end):
                if r.get('mothersConditionOnDischarge') == 'Deceased' and r.get('personId') not in seen:
                    seen.add(r.get('personId')); out.append({**r, 'sex': 'FEMALE'})
            return out + [r for r in _deaths(start, end, True) if r['personId'] not in seen]
        raise ValueError(source)
    try:
        return _cached(('rows', source, start, end), load)
    except HTTPException: raise
    except Exception as e:  # database unavailable or a register without read access
        raise HTTPException(503, f'Map data unavailable: {e}')


def _indicator_rows(key, start, end):
    _, _, source, keep, how, _ = INDICATORS[key]
    rows = [r for r in _source_rows(source, start, end) if r.get('eventDate')]
    if keep: rows = [r for r in rows if keep(r)]
    if how == 'deliveries':
        seen, out = set(), []
        for r in rows:
            if r.get('deliveryId') in seen: continue
            seen.add(r.get('deliveryId')); out.append(r)
        rows = out
    return rows


def _sex(r):
    s = (r.get('maternalSex') or r.get('sex') or '').upper()
    return 'male' if s == 'MALE' else 'female' if s == 'FEMALE' else 'other'


def _periods(start, end, grain):
    out = []
    if grain == 'year':
        for y in range(start.year, end.year + 1):
            out.append({'key': str(y), 'label': str(y), 'from': max(start, date(y, 1, 1)), 'to': min(end, date(y, 12, 31))})
    elif grain == 'quarter':
        y, q = start.year, (start.month - 1) // 3
        while date(y, q * 3 + 1, 1) <= end:
            a = date(y, q * 3 + 1, 1); b = date(y + (q == 3), (q * 3 + 3) % 12 + 1, 1) - timedelta(days=1)
            out.append({'key': f'{y}-Q{q + 1}', 'label': f'Q{q + 1} {y}', 'from': max(start, a), 'to': min(end, b)})
            y, q = y + (q == 3), (q + 1) % 4
    else:
        m = date(start.year, start.month, 1)
        while m <= end:
            nxt = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
            out.append({'key': m.strftime('%Y-%m'), 'label': m.strftime('%b %Y'), 'from': max(start, m), 'to': min(end, nxt - timedelta(days=1))})
            m = nxt
    return out


def _bucketer(periods):
    bounds = [(p['from'].isoformat(), p['to'].isoformat()) for p in periods]
    def index(d):
        d = str(d)[:10]
        for i, (a, b) in enumerate(bounds):
            if a <= d <= b: return i
        return None
    return index


def _range(from_date, to_date, grain):
    m = _main()
    site = m._with_registry(lambda c: m._facility(c))
    latest = site['latestDate'] or date.today()
    to_date = min(to_date or latest, latest)
    from_date = from_date or m._month_start(to_date, 11)
    if from_date > to_date: raise HTTPException(400, "'from' must be on or before 'to'.")
    periods = _periods(from_date, to_date, grain)
    if len(periods) > 240: raise HTTPException(400, 'That is too many periods to animate. Use quarters or years.')
    return from_date, to_date, latest, periods


def _split(rows, periods):
    """Counts per period, in total and by sex."""
    n = len(periods); idx = _bucketer(periods)
    out = {'total': [0] * n, 'male': [0] * n, 'female': [0] * n}
    for r in rows:
        i = idx(r['eventDate'])
        if i is None: continue
        out['total'][i] += 1
        s = _sex(r)
        if s != 'other': out[s][i] += 1
    return out


def _period_json(periods):
    return [{'key': p['key'], 'label': p['label'], 'from': p['from'], 'to': p['to']} for p in periods]


# ---------------------------------------------------------------- home places (api/places.csv)
PLACES_FILE = Path(__file__).with_name('places.csv')
_norm = lambda s: ' ' + re.sub(r'\s+', ' ', re.sub(r'\bmt\b', 'mount', re.sub(r'[^a-z]+', ' ', (s or '').lower()))).strip() + ' '


def _places():
    def load():
        out = []
        with PLACES_FILE.open(encoding='utf-8') as f:
            for r in csv.DictReader(f):
                aliases = [r['name']] + [a for a in (r.get('aliases') or '').split('|') if a.strip()]
                out.append({'id': re.sub(r'[^a-z]+', '-', r['name'].lower()).strip('-'), 'name': r['name'], 'kind': r['kind'],
                            'latitude': float(r['latitude']), 'longitude': float(r['longitude']), 'source': r.get('source') or '',
                            'patterns': sorted({_norm(a) for a in aliases}, key=len, reverse=True)})
        return out
    return _cached(('places', PLACES_FILE.stat().st_mtime), load)


def _match_place(address, places):
    """The most specific place named in an address: the last suburb or town mentioned, else the city."""
    text = _norm(address)
    if not text.strip(): return None
    best = None
    for p in places:
        for pat in p['patterns']:
            i = text.rfind(pat)
            if i < 0: continue
            rank = (p['kind'] != 'city', i, len(pat))
            if best is None or rank > best[0]: best = (rank, p)
            break
    return best[1] if best else None


def _addresses():
    rows = _cached(('addresses',), lambda: _query("SELECT person_id,address FROM report.person_demographic WHERE NULLIF(TRIM(address),'') IS NOT NULL"))
    return {r['person_id']: r['address'] for r in rows}


# ---------------------------------------------------------------- endpoints
@router.get('/indicators')
def indicators():
    m = _main()
    site = m._with_registry(lambda c: m._facility(c))
    first = None
    try: first = _query('SELECT MIN(date) AS d FROM report.patient_diagnosis_report')[0]['d']
    except Exception: pass
    return {'indicators': [{'key': k, 'label': v[0], 'group': v[1], 'definition': v[5]} for k, v in INDICATORS.items()],
            'dataStart': first, 'dataEnd': site['latestDate'], 'tenantFacility': m.TENANT_FACILITY_ID}


@router.get('/series')
def series(indicator: str, from_date: date | None = Query(None, alias='from'), to_date: date | None = Query(None, alias='to'),
           grain: Literal['month', 'quarter', 'year'] = 'month', province: str | None = None, district: str | None = None,
           type: str | None = None):
    m = _main()
    if indicator not in INDICATORS: raise HTTPException(404, f'Unknown indicator {indicator!r}.')
    from_date, to_date, latest, periods = _range(from_date, to_date, grain)
    scope = m._with_registry(lambda c: m._registry(c, None, province, district, type))
    reporting = [r for r in scope if r['hasData']]
    split = _split(_indicator_rows(indicator, from_date, to_date), periods) if reporting else None
    # Single-tenant database: everything loaded belongs to the tenant facility. Multi-tenant: split per facility.
    values = {m.TENANT_FACILITY_ID: split} if split else {}
    label, group, *_, definition = INDICATORS[indicator]
    return {
        'indicator': {'key': indicator, 'label': label, 'group': group, 'definition': definition},
        'grain': grain,
        'period': {'from': from_date, 'to': to_date, 'latestDataDate': latest},
        'periods': _period_json(periods),
        'facilities': [{k: r[k] for k in ('id', 'name', 'provinceId', 'province', 'districtId', 'district', 'type', 'typeLabel',
                                          'typeGroup', 'latitude', 'longitude', 'hasData')}
                       | ({'values': values[r['id']]['total'], 'male': values[r['id']]['male'], 'female': values[r['id']]['female']}
                          if r['hasData'] and r['id'] in values else {'values': None})
                       for r in scope],
        'total': sum(split['total']) if split else 0,
    }


@router.get('/movements')
def movements(indicator: str, from_date: date | None = Query(None, alias='from'), to_date: date | None = Query(None, alias='to'),
              grain: Literal['month', 'quarter', 'year'] = 'month'):
    """Where the patients behind an indicator live, and transfers between facilities, per period."""
    m = _main()
    if indicator not in INDICATORS: raise HTTPException(404, f'Unknown indicator {indicator!r}.')
    from_date, to_date, latest, periods = _range(from_date, to_date, grain)
    n = len(periods); idx = _bucketer(periods)
    places = _places(); addresses = _addresses()
    tenant = m._with_registry(lambda c: m._registry(c, m.TENANT_FACILITY_ID))
    to = tenant[0] if tenant else None
    origins = {}
    no_address = [0] * n; unrecognised = [0] * n; unrecognised_text = Counter()
    for r in _indicator_rows(indicator, from_date, to_date):
        i = idx(r['eventDate'])
        if i is None: continue
        addr = r.get('address') or addresses.get(r.get('personId'))
        if not addr: no_address[i] += 1; continue
        p = _match_place(addr, places)
        if not p:
            unrecognised[i] += 1; unrecognised_text[' '.join(_norm(addr).split()[-2:])] += 1; continue
        o = origins.setdefault(p['id'], {'id': p['id'], 'name': p['name'], 'kind': p['kind'], 'latitude': p['latitude'],
                                         'longitude': p['longitude'], 'source': p['source'],
                                         'values': [0] * n, 'male': [0] * n, 'female': [0] * n})
        o['values'][i] += 1
        s = _sex(r)
        if s != 'other': o[s][i] += 1

    # Transfers between facilities (sex isn't recorded on these registers).
    transfers = {}
    def facility_point(fid):
        rows = m._with_registry(lambda c: m._registry(c, fid))
        if not rows: return None
        f = rows[0]
        lat, lon = f['latitude'], f['longitude']
        if lat is None:  # no coordinates in the registry: fall back to a town of the same name
            p = _match_place(f['name'], places)
            if p and p['kind'] != 'suburb': lat, lon = p['latitude'], p['longitude']
        return {'id': f['id'], 'name': f['name'], 'district': f['district'], 'province': f['province'],
                'latitude': float(lat) if lat is not None else None, 'longitude': float(lon) if lon is not None else None}
    def add_transfer(kind, from_id, when):
        i = idx(when)
        if i is None or not from_id or from_id == m.TENANT_FACILITY_ID: return
        key = (kind, from_id)
        if key not in transfers:
            transfers[key] = {'kind': kind, 'from': facility_point(from_id) or {'id': from_id, 'name': from_id, 'latitude': None, 'longitude': None},
                              'values': [0] * n}
        transfers[key]['values'][i] += 1
    try:
        for r in _cached(('art-transfers', from_date, to_date), lambda: _query(
                'SELECT transfer_facility_id f,date_of_transfer_in d FROM report.art_transfer_in_register WHERE date_of_transfer_in BETWEEN %s AND %s',
                (from_date, to_date))):
            add_transfer('ART transfer in', r['f'], r['d'])
        for r in _cached(('tb-transfers', from_date, to_date), lambda: _query(
                'SELECT transfer_from f,COALESCE(transfer_in_date,`date`) d FROM report.tb_register WHERE transfer_from IS NOT NULL AND COALESCE(transfer_in_date,`date`) BETWEEN %s AND %s',
                (from_date, to_date))):
            add_transfer('TB transfer in', r['f'], r['d'])
    except Exception:
        pass  # transfers are optional: keep the home-place movements even if these registers aren't readable

    label = INDICATORS[indicator][0]
    return {
        'indicator': {'key': indicator, 'label': label},
        'grain': grain, 'periods': _period_json(periods),
        'to': {'id': to['id'], 'name': to['name'], 'latitude': to['latitude'], 'longitude': to['longitude']} if to else None,
        'origins': sorted(origins.values(), key=lambda o: -sum(o['values'])),
        'noAddress': no_address, 'unrecognised': unrecognised,
        'unrecognisedExamples': [k for k, _ in unrecognised_text.most_common(8)],
        'transfers': sorted(transfers.values(), key=lambda t: -sum(t['values'])),
    }


@router.get('/profile')
def profile(from_date: date | None = Query(None, alias='from'), to_date: date | None = Query(None, alias='to'),
            grain: Literal['month', 'quarter', 'year'] = 'month', place: str | None = None):
    """Every indicator per period (and by sex) for the facilities with data, or for patients living in one home place."""
    m = _main()
    from_date, to_date, latest, periods = _range(from_date, to_date, grain)
    places = _places(); addresses = _addresses() if place else None
    out = {}
    for key, (label, group, *_rest) in INDICATORS.items():
        rows = _indicator_rows(key, from_date, to_date)
        if place:
            rows = [r for r in rows if (p := _match_place(r.get('address') or addresses.get(r.get('personId')) or '', places)) and p['id'] == place]
        out[key] = {'key': key, 'label': label, 'group': group, **_split(rows, periods)}
    return {'facility': m.TENANT_FACILITY_ID, 'place': place, 'grain': grain, 'periods': _period_json(periods), 'indicators': out}


@router.get('/places')
def places_list():
    return {'places': [{k: p[k] for k in ('id', 'name', 'kind', 'latitude', 'longitude', 'source')} for p in _places()],
            'file': 'api/places.csv'}
