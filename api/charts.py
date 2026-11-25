"""Charts in the chat. When a question asks for a graph, or for a breakdown ("by age", "males and females",
"by month"), the figures are worked out here, by the same engine as Reports, and sent to the chat as a
chart. The language model only writes the words around it, so the numbers are always exact.
"""
import re
from datetime import date

INDICATORS = [  # first match wins, so the more specific phrases come first
    ('maternalDeaths', r'maternal (death|mortality)|mothers? (who )?died'),
    ('stillBirths', r'still ?births?|stillborn'),
    ('liveBirths', r'live births?'),
    ('deliveries', r'deliver(y|ies)|maternity'),
    ('deaths', r'(?<!maternal )\bdeaths?\b|mortality|\bdied\b'),
    ('hivPositive', r'hiv[ -]?(positive|\+|cases?|infections?)|positive (for )?hiv|tested (hiv )?positive|positives?\b(?!.*malaria)'),
    ('htsTests', r'hiv test|\bhts\b|tested for hiv|hiv testing|\btested\b(?!.*positive)'),
    ('artNew', r'\bart\b|antiretroviral|initiat'),
    ('ancBookings', r'\banc\b|antenatal|bookings?|booked'),
    ('malariaPositive', r'malaria (positive|cases?)|positive (for )?malaria'),
    ('malariaTests', r'malaria'),
    ('tbRegistered', r'\btb\b|tuberculosis'),
    ('opd', r'\bopd\b|outpatient'),
    ('attendances', r'attendances?|visits?|patients? seen|footfall|attended'),
]
CHART_WORDS = r'\b(graph|chart|plot|draw|visuali[sz]e|pie|bar|histogram|diagram|trend|over time)\b'
SEX_SPLIT = r'(males?|men|boys) and (females?|women|girls)|(females?|women|girls) and (males?|men|boys)|\b(by|per|across|according to|split by)\s+(sex|gender)\b|\bgender\b'
DIMENSIONS = [
    ('period', r'\b(by|per|each|every)\s+(month|quarter|year)|monthly|quarterly|yearly|annual(ly)?|over time|trend|month by month'),
    ('province', r'\b(by|per|each|across)\s+provinces?|provinces\b'),
    ('district', r'\b(by|per|each|across)\s+districts?|districts\b'),
    ('facilityType', r'\b(by|per)\s+(facility )?type'),
    ('facility', r'\b(by|per|each|across)\s+(facilit(y|ies)|clinics?|sites?)'),
    ('ageGroup', r'\bage\b|age ?groups?|\bages\b|how old'),
]
SEX_COLOURS = {'Female': '#c0367a', 'Male': '#2563eb', 'Not recorded': '#94a3b8'}
PALETTE = ['#218a39', '#2563eb', '#d97706', '#c0367a', '#0d9488', '#7c3aed', '#dc2626', '#475569']


def _indicators(t):
    found = []
    for key, rx in INDICATORS:
        if re.search(rx, t) and key not in found:
            # "HIV positive" also matches the generic "tested"; keep the more specific one.
            if key == 'htsTests' and 'hivPositive' in found and not re.search(r'hiv test|\bhts\b|tests?\b', t.replace('tested positive', '')):
                continue
            if key == 'malariaTests' and 'malariaPositive' in found and not re.search(r'malaria tests?', t):
                continue
            if key == 'attendances' and 'opd' in found and not re.search(r'attendance', t):
                continue
            found.append(key)
    return found[:6]


def _period(t, latest, grain):
    y = r'(20\d\d)'
    m = (re.search(rf'(?:from|since|between)\s+{y}\s*(?:to|until|and|-|–)\s*({y[1:-1]}|now|today|date)', t)
         or re.search(rf'\b{y}\s*(?:to|-|–)\s*({y[1:-1]}|now)', t))
    if m:
        a, b = int(m.group(1)), m.group(2)
        return date(a, 1, 1), (date(int(b), 12, 31) if b.isdigit() else None)
    if (m := re.search(rf'(?:since|from)\s+{y}', t)): return date(int(m.group(1)), 1, 1), None
    if (m := re.search(rf'\b(?:in|for|during)\s+{y}\b', t)): return date(int(m.group(1)), 1, 1), date(int(m.group(1)), 12, 31)
    if (m := re.search(r'last\s+(\d+|two|three|four|five|six|twelve)\s+(month|year)s?', t)):
        n = {'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'twelve': 12}.get(m.group(1)) or int(m.group(1))
        months = n * 12 if m.group(2) == 'year' else n
        idx = latest.year * 12 + latest.month - 1 - (months - 1)
        return date(idx // 12, idx % 12 + 1, 1), None
    if re.search(r'this year', t): return date(latest.year, 1, 1), None
    back = {'month': 11, 'quarter': 35, 'year': 59}[grain]
    idx = latest.year * 12 + latest.month - 1 - back
    return date(idx // 12, idx % 12 + 1, 1) if grain == 'month' else date(idx // 12, 1, 1), None


def plan(message, history=()):
    """What to chart for this question, or None when no chart is wanted."""
    t = ' ' + re.sub(r'\s+', ' ', message.lower()) + ' '
    inds = _indicators(t)
    wants_chart = bool(re.search(CHART_WORDS, t))
    dims = [d for d, rx in DIMENSIONS if re.search(rx, t)]
    if re.search(SEX_SPLIT, t): dims.append('sex')
    # Follow-ups such as "now show it by year" or "as a pie chart" reuse the last question's indicator.
    if not inds and (wants_chart or dims):
        for h in reversed(list(history)):
            if h.get('role') == 'user':
                inds = _indicators(' ' + h.get('content', '').lower() + ' ')
                if inds: break
    if not inds or not (wants_chart or dims): return None
    grain = 'quarter' if 'quarter' in t else 'year' if re.search(r'\b(year|yearly|annual)', t) and not re.search(r'last \w+ years?', t) else 'month'
    if wants_chart and not dims: dims = ['period']  # "draw a graph of OPD visits" -> over time
    # Rows: the first grouping; sex goes across as columns whenever there is another grouping.
    order = ['period', 'province', 'district', 'facilityType', 'facility', 'ageGroup', 'sex']
    dims = sorted(set(dims), key=order.index)
    if len(dims) > 2:
        dims = [d for d in dims if d != 'period'][:2] if 'sex' in dims else dims[:2]
    rows = dims[0]
    columns = dims[1] if len(dims) > 1 else 'none'
    if len(inds) > 1 and columns != 'none':  # several indicators already make the columns
        columns = 'none'
    sex = 'all'
    if 'sex' not in dims:
        if re.search(r'\b(women|females?|girls)\b', t) and not re.search(r'\b(men|males?|boys)\b', t): sex = 'female'
        elif re.search(r'\b(men|males?|boys)\b', t) and not re.search(r'\b(women|females?|girls)\b', t): sex = 'male'
    kind = 'pie' if re.search(r'\bpie\b', t) else 'line' if re.search(r'\bline\b|trend|over time', t) else 'bar' if re.search(r'\bbar\b|histogram', t) else ('line' if rows == 'period' and columns == 'none' else 'bar')
    if kind == 'pie' and (columns != 'none' or len(inds) > 1): kind = 'bar'
    return {'indicators': inds, 'rows': rows, 'columns': columns, 'grain': grain, 'sex': sex, 'kind': kind, 'text': t}


def build(p, latest):
    """Run the plan through the report engine. Returns (chart spec for the page, figures for the model)."""
    import reports
    start, end = _period(p['text'], latest, p['grain'])
    period = reports.Period(**{'from': start, 'to': end, 'grain': p['grain']})
    section = reports.Section(type='data', indicators=p['indicators'], rows=p['rows'], columns=p['columns'], chart=p['kind'], hideEmpty=True)
    ctx = reports._context(period, reports.Scope())
    res = reports.run_section(section, period, reports.Scope(), p['sex'], ctx)
    header, body, total = reports.flat_table(res)
    if not body or all(all(v is None for v in r[1:]) for r in body): return None, None
    names = [i['label'] for i in res['indicators']]
    by = [reports.DIMENSIONS[d].lower() for d in (p['rows'], p['columns']) if d != 'none']
    sex_note = {'female': ' (females)', 'male': ' (males)'}.get(p['sex'], '')
    title = (' and '.join(names) if len(names) < 3 else ', '.join(names[:-1]) + ' and ' + names[-1]) + sex_note + (' by ' + ' and '.join(by) if by else '')
    f = lambda d: d.strftime('%b %Y')
    subtitle = f"{ctx['scopeLabel']} · {f(ctx['from'])} – {f(ctx['to'])}"
    series = [{'name': h, 'values': [r[k + 1] or 0 for r in body],
               'color': SEX_COLOURS.get(h.split(' · ')[-1]) or PALETTE[k % len(PALETTE)]} for k, h in enumerate(header[1:])]
    spec = {'type': p['kind'], 'title': title[:1].upper() + title[1:], 'subtitle': subtitle,
            'categories': [str(r[0]) for r in body], 'series': series,
            'table': {'header': header, 'rows': body, 'total': total}}
    lines = [', '.join(str(h) for h in header)] + [', '.join('' if v is None else str(v) for v in r) for r in body + ([total] if total else [])]
    figures = (f"A {p['kind']} chart has already been drawn for the user: \"{spec['title']}\" ({subtitle}). Its exact figures:\n"
               + '\n'.join(lines) +
               "\nIn 2–4 short sentences, say what the chart shows: the largest and smallest groups and any clear difference "
               "(for example between females and males). Use only these figures. Don't draw the chart in text and don't list every number.")
    return spec, figures
