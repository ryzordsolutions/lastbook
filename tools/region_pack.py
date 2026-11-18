#!/usr/bin/env python3
"""Region packs for the regional coverage prototype (docs/REGIONAL-COVERAGE.md, Prototype).

  python3 tools/region_pack.py test-library /path/to/test-library     # a tiny library to try this on, if you have no other
  python3 tools/region_pack.py build app/tests/fixtures/region-US-PA Lastbook-Region-US-PA.zip
  python3 tools/region_pack.py install --data /path/to/LastbookData Lastbook-Region-US-PA.zip
  python3 tools/region_pack.py select  --data /path/to/LastbookData US-PA-lehigh-pocono     # or: none
  python3 tools/region_pack.py remove  --data /path/to/LastbookData
  python3 tools/region_pack.py show    --data /path/to/LastbookData [--ask "When can I plant tomatoes?"]

`show` prints what the app would show: the region, the Grow guide's "What can I plant now where I live?" as the server
resolves it, and, with --ask, the sources Chat would be given for that question, each with the place it is written for.
It reads the library the way the app does and changes nothing. The app must be closed while install, select or remove
run, as for setup's own switch. Nothing here uses the network.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# A tiny library for trying regions without a real one: the general chapter and the California documents the Grow guide
# cites, under their real ids, with short stand-in text. Every title says it is a stand-in.
TEST_DOCS = [
    ('med-2ca695c9db63d020', 'Sustainable farming (test stand-in for the Hesperian chapter)', 'Test library',
     'Growing food anywhere: soil, compost, water, seed saving. Plant crops in the right season for your place. Tomatoes need warm weather.'),
    ('coastal-growing-6c1e6263ed3907b61d56', 'Vegetable planting chart (test stand-in for UC Ventura County)', 'Test library',
     'Southern California planting chart by month. Plant tomatoes from March in the coastal frost-free zone. Last frost rarely matters on the coast.'),
    ('coastal-growing-2bf5bc7fde86306a742c', 'October planting list (test stand-in for UC Los Angeles County)', 'Test library',
     'Los Angeles County: cool-season crops to plant in October. Peas, lettuce and onions; tomatoes wait for spring.'),
    ('coastal-growing-7e742f7e7ec7d1b6ee65', 'Potato planting months (test stand-in for UC IPM)', 'Test library',
     'Potato planting windows for the South Coast of California. Check locally with a Master Gardener program.'),
]


def test_library(folder):
    import sqlite3
    from engine import regions
    lib = Path(folder) / 'library-data'
    if lib.exists():
        raise regions.Refused(f'{lib} already exists. Choose an empty folder.')
    (lib / 'documents' / 'test').mkdir(parents=True)
    (lib / 'text').mkdir()
    recs = []
    con = sqlite3.connect(lib / 'search.sqlite')
    con.execute('CREATE VIRTUAL TABLE pages USING fts5(doc_id UNINDEXED,title,collection,category,path UNINDEXED,page UNINDEXED,body,tokenize="unicode61")')
    for i, title, coll, body in TEST_DOCS:
        path = f'documents/test/{i}.html'
        (lib / path).write_text(f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title></head><body><p>{body}</p></body></html>\n', encoding='utf-8')
        (lib / 'text' / f'{i}.txt').write_text(body + '\n', encoding='utf-8')
        recs.append({'id': i, 'kind': 'html', 'title': title, 'publisher': coll, 'collection': coll, 'category': 'test', 'path': path, 'text_path': f'text/{i}.txt'})
        con.execute('INSERT INTO pages VALUES (?,?,?,?,?,?,?)', (i, title, coll, 'test', path, None, body))
    con.commit()
    con.close()
    (lib / 'catalog.json').write_text(json.dumps(recs, indent=1) + '\n', encoding='utf-8')
    (Path(folder) / 'derived').mkdir(exist_ok=True)
    return lib


def show(data, ask, guide, choice):
    os.environ['LASTBOOK_DATA'] = str(Path(data).resolve())
    os.environ.setdefault('SURVIVAL_DISABLE_INDEX_WATCH', '1')
    import server  # noqa: E402  (its paths follow LASTBOOK_DATA, read at import)
    import assistant  # noqa: E402
    assistant.init(server, None)
    info = server.region_info()
    print(f"Region: {info['name'] or 'not chosen'}{' (' + info['region'] + ')' if info['region'] else ''}")
    print(f"Region pack installed: {info['pack'] or 'none'}")
    t = next(x for x in server.topics_resolved() if x['id'] == guide)
    c = next((x for x in t['choices'] if x['id'] == choice), None)
    if c:
        print(f"\nGuide “{t['title']}” → “{c['label']}”")
        for i in c['read']:
            print(f"  - {'[' + i['place'] + '] ' if i.get('place') else '[general] '}{i['label']}")
        if c.get('regionNote'):
            print(f"  Note: {c['regionNote']}")
        for i in c.get('elsewhere', []):
            print(f"  Written for other places: [{i['place']}] {i['label']}")
    if ask:
        print(f"\nChat sources for “{ask}”:")
        for r in assistant.hybrid(ask, 6):
            fit = {'mine': '', 'other': ', not the area asked about', 'unset': ', the reader has not chosen an area'}.get(r.get('regionFit'), '')
            print(f"  - {r['title']}{' — written for ' + r['region'] + fit if r.get('region') else ' — general'}")
        where = assistant.reader_area()
        if where:
            print(f"  Told to the model: {where}")


def main(argv=None):
    from engine import regions
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build', help='make a region pack from a folder')
    b.add_argument('folder')
    b.add_argument('zip')
    t = sub.add_parser('test-library', help='make a tiny library to try regions on')
    t.add_argument('folder')
    for name in ('install', 'select', 'remove', 'show'):
        p = sub.add_parser(name)
        p.add_argument('--data', required=True, help='the data folder, the one that holds library-data/')
        if name == 'install':
            p.add_argument('zip')
        if name == 'select':
            p.add_argument('region', help='a region code such as US-PA-lehigh-pocono, or none')
        if name == 'show':
            p.add_argument('--ask', help='a question: list the sources Chat would get for it')
            p.add_argument('--guide', default='grow')
            p.add_argument('--choice', default='when-to-plant')
    a = ap.parse_args(argv)
    try:
        if a.cmd == 'test-library':
            print(f'Made a test library in {test_library(a.folder)}')
        elif a.cmd == 'build':
            regions.build(a.folder, a.zip)
            print(f'Built {a.zip}')
        elif a.cmd == 'install':
            s = regions.install(a.data, a.zip)
            print(f"Installed the {s['pack']['region']} region pack: {len(s['pack']['ids'])} documents. Region: {s['region']}.")
        elif a.cmd == 'select':
            s = regions.select(a.data, None if a.region == 'none' else a.region)
            print(f"Region: {s['region'] or 'not chosen'}.")
        elif a.cmd == 'remove':
            code = regions.remove(a.data)
            print(f'Removed the {code} region pack.' if code else 'No region pack was installed.')
        else:
            show(a.data, a.ask, a.guide, a.choice)
    except regions.Refused as e:
        sys.exit(str(e))
    return 0


if __name__ == '__main__':
    sys.exit(main())
