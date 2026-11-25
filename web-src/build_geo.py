"""Builds web/geo.html from web-src/geo.template.html, embedding the same province and district
outlines as the Overview map (taken from web/facility-dashboard.html so both maps always match)."""
import json, pathlib
root = pathlib.Path(__file__).resolve().parent.parent
dash = (root / 'web' / 'facility-dashboard.html').read_text(encoding='utf-8')
def grab(name):
    i = dash.index(f'const {name}=') + len(name) + 7
    return json.dumps(json.JSONDecoder().raw_decode(dash[i:])[0], separators=(',', ':'))
tpl = (root / 'web-src' / 'geo.template.html').read_text(encoding='utf-8')
out = tpl.replace('__PROVINCES__', grab('PROVINCES')).replace('__DISTRICTS__', grab('DISTRICTS'))
(root / 'web' / 'geo.html').write_text(out, encoding='utf-8')
print('web/geo.html', len(out) // 1024, 'KB')
