"""Regions: which place a library is for, and the regional material installed for it (docs/REGIONAL-COVERAGE.md, Design).

A prototype for review. It keeps to the design's parts, at the smallest size that shows them working:

- A region has an ISO 3166 style code: a country (`US`), a state (`US-PA`) or a local area inside one, a state code and a
  short lowercase name (`US-PA-lehigh-pocono`). Its chain runs from the most local level up to the country.
- A region file (`<code>.json`) names the region and, per guide slot, its readings and what is missing. The app's own
  region files are in `content/regions/`; a region pack brings its own, installed to `library-data/regions/`.
- A region pack (`Lastbook-Region-<code>.zip`) adds catalog records, search rows, document files and region files to a
  library. One pack per library, as with state maps: installing another removes the first.
- `region-set.json`, in the data folder beside `library-data/` (where `map-set.json` lives), records the region the
  person chose and the pack installed. Lastbook never detects location: the region is only ever what the person picked.

Installing checks every file before it touches the library, and is all or nothing: if any step fails, the library is
left as it was, with the pack that was there before still installed. The real version would go through setup's staged switch (docs/SETUP-API.md), like the state maps.
Nothing here opens a connection: a pack is a file already on this computer.
"""
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

SET_FILE = 'region-set.json'
PACK_FILE = 'region-pack.json'
CODE = re.compile(r'^[A-Z]{2}(-[A-Z0-9]{1,3}(-[a-z0-9]+(-[a-z0-9]+)*)?)?$')
SLOTS = ('growing-season', 'keeping-the-harvest', 'local-hazards', 'warnings', 'wildlife-and-plants', 'water-and-floods')
SEARCH_FIELDS = ('doc_id', 'title', 'collection', 'category', 'path', 'page', 'body')
MAX_FILE = 200 * 1024 * 1024
# Names for codes with no region file, so a reader in a state with no material is told its name, not its code.
US_STATES = dict(AL='Alabama', AK='Alaska', AZ='Arizona', AR='Arkansas', CA='California', CO='Colorado', CT='Connecticut',
                 DE='Delaware', DC='District of Columbia', FL='Florida', GA='Georgia', HI='Hawaii', ID='Idaho', IL='Illinois',
                 IN='Indiana', IA='Iowa', KS='Kansas', KY='Kentucky', LA='Louisiana', ME='Maine', MD='Maryland',
                 MA='Massachusetts', MI='Michigan', MN='Minnesota', MS='Mississippi', MO='Missouri', MT='Montana',
                 NE='Nebraska', NV='Nevada', NH='New Hampshire', NJ='New Jersey', NM='New Mexico', NY='New York',
                 NC='North Carolina', ND='North Dakota', OH='Ohio', OK='Oklahoma', OR='Oregon', PA='Pennsylvania',
                 PR='Puerto Rico', RI='Rhode Island', SC='South Carolina', SD='South Dakota', TN='Tennessee', TX='Texas',
                 UT='Utah', VT='Vermont', VA='Virginia', WA='Washington', WV='West Virginia', WI='Wisconsin', WY='Wyoming')


class Refused(Exception):
    """A pack or a choice that cannot be used, with a plain sentence saying why."""


def valid(code):
    return isinstance(code, str) and bool(CODE.match(code))


def chain(code):
    """The region and every level above it, most local first: US-PA-lehigh-pocono, US-PA, US."""
    if not valid(code):
        return []
    parts = code.split('-')
    out = [code]
    if len(parts) > 2:
        out.append('-'.join(parts[:2]))
    if len(parts) > 1:
        out.append(parts[0])
    return out


def state_of(code):
    """The state or province level of a code, or None for a country."""
    c = chain(code)
    return next((x for x in c if x.count('-') == 1), None)


def id_prefix(code):
    """Every record a pack adds starts with this, so a later removal takes out exactly what it added."""
    return 'region-' + code.lower() + '-'


# ------------------------------------------------------------------------------------------------------ region files --
def load_definitions(*folders):
    """{code: region file}, later folders overriding earlier ones. A file that does not parse or names a bad code is skipped."""
    out = {}
    for folder in folders:
        try:
            files = sorted(Path(folder).glob('*.json'))
        except OSError:
            continue
        for f in files:
            try:
                d = json.loads(f.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if isinstance(d, dict) and valid(d.get('id')) and f.stem == d['id']:
                out[d['id']] = d
    return out


def name(code, defs):
    """A region's name for people: its file's name, else a US state's name, else the code."""
    if code in defs and defs[code].get('name'):
        return defs[code]['name']
    st = code.split('-')
    if len(st) == 2 and st[0] == 'US' and st[1] in US_STATES:
        return US_STATES[st[1]]
    if len(st) > 2:   # a local area with no region file here: say which state it is in
        return f"{name('-'.join(st[:2]), defs)} ({' '.join(st[2:])} area)"
    return 'the United States' if code == 'US' else code


def named_in(text, defs):
    """Region codes whose places (a region file's `places`, or its name) a question names: "Easton", "the Poconos". Whole words,
    any case. A question about a named place is about that place, whatever region the library is set to."""
    text = ' ' + re.sub(r'[^a-z0-9]+', ' ', (text or '').lower()) + ' '
    out = []
    for code, d in defs.items():
        words = [d.get('name') or ''] + list(d.get('places') or [])
        if any(w and ' ' + re.sub(r'[^a-z0-9]+', ' ', w.lower()).strip() + ' ' in text for w in words):
            out.append(code)
    return sorted(out)


def doc_regions(defs):
    """{doc id: region code} for every document a region file names, in a slot or in its `documents` list."""
    out = {}
    for code, d in defs.items():
        for doc in d.get('documents', []):
            out.setdefault(doc, code)
        for slot in (d.get('slots') or {}).values():
            for item in slot.get('read', []):
                if item.get('doc'):
                    out.setdefault(item['doc'], code)
    return out


# ------------------------------------------------------------------------------------------------- the chosen region --
def read_set(data):
    try:
        d = json.loads((Path(data) / SET_FILE).read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path, obj):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '.')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(obj, f, indent=1, ensure_ascii=False)
            f.write('\n')
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def chosen(data):
    """The region the person chose, or None."""
    code = read_set(data).get('region')
    return code if valid(code) else None


def select(data, code):
    """Choose a region. It needs no download: with no pack for it, the guides fall back to general readings and say so."""
    if code is not None and not valid(code):
        raise Refused(f'“{code}” is not a region code. Use a code such as US-PA or US-PA-lehigh-pocono.')
    s = read_set(data)
    s['region'] = code
    _write_json(Path(data) / SET_FILE, s)
    return s


# ------------------------------------------------------------------------------------------------------ region packs --
def _pack_manifest(z):
    try:
        m = json.loads(z.read(PACK_FILE))
    except (KeyError, ValueError):
        raise Refused('The region pack has no readable region-pack.json in it.')
    code = m.get('region') if isinstance(m, dict) else None
    if not valid(code) or code.count('-') != 1:
        raise Refused('A region pack is for one state or province, such as US-PA.')
    return m


def _member_ok(name, code):
    """The only paths a region pack may hold: its manifest, its catalog and search rows, its region files and its documents."""
    p = PurePosixPath(name)
    if name.endswith('/'):
        return True
    if p.is_absolute() or '..' in p.parts or '\\' in name or ':' in name:
        return False
    if name in (PACK_FILE, 'catalog.json', 'search.json'):
        return True
    if len(p.parts) == 2 and p.parts[0] == 'regions' and p.suffix == '.json':
        return p.stem == code or p.stem.startswith(code + '-')
    if len(p.parts) == 2 and p.parts[0] == 'text' and p.suffix == '.txt':
        return p.name.startswith(id_prefix(code))
    return len(p.parts) >= 4 and p.parts[:3] == ('documents', 'regions', code.lower())


def read_pack(zip_path):
    """Check a region pack without touching any library. Returns (manifest, records, search rows, region files, files)."""
    try:
        z = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile):
        raise Refused('That file is not a region pack (not a zip file).')
    with z:
        m = _pack_manifest(z)
        code = m['region']
        bad = [i.filename for i in z.infolist() if not _member_ok(i.filename, code)]
        if bad:
            raise Refused(f'The region pack holds files a region pack may not hold: {", ".join(bad[:3])}.')
        try:
            records = json.loads(z.read('catalog.json'))
            rows = json.loads(z.read('search.json')) if 'search.json' in z.namelist() else []
        except (KeyError, ValueError):
            raise Refused('The region pack has no readable catalog.json.')
        prefix = id_prefix(code)
        if not isinstance(records, list) or not records:
            raise Refused('The region pack lists no documents.')
        files = {}
        for r in records:
            if not isinstance(r, dict) or not str(r.get('id', '')).startswith(prefix):
                raise Refused(f'Every document in a {code} pack needs an id starting “{prefix}”.')
            if r.get('kind') not in ('pdf', 'html'):
                raise Refused(f'{r["id"]}: a region pack may hold PDF or HTML documents only.')
            if chain(r.get('region') or '')[-2:] != chain(code)[-2:] and r.get('region') != code:
                raise Refused(f'{r["id"]}: its region must be {code} or a local area inside it.')
            path = r.get('path', '')
            if not path.startswith('documents/') or not _member_ok(path, code) or path not in z.namelist():
                raise Refused(f'{r["id"]}: its file {path} is not in the pack.')
            if z.getinfo(path).file_size > MAX_FILE:
                raise Refused(f'{r["id"]}: its file is larger than a region document may be.')
            data = z.read(path)
            if hashlib.sha256(data).hexdigest() != r.get('sha256'):
                raise Refused(f'{r["id"]}: its file does not match its checksum.')
            files[path] = data
            tp = r.get('text_path')
            if tp:   # the text Chat reads a document's pages from (server.py page_text)
                if tp != f'text/{r["id"]}.txt' or tp not in z.namelist():
                    raise Refused(f'{r["id"]}: its text file {tp} is not in the pack.')
                files[tp] = z.read(tp)
        ids = {r['id'] for r in records}
        for row in rows:
            if not isinstance(row, dict) or row.get('doc_id') not in ids or set(row) - set(SEARCH_FIELDS):
                raise Refused('The region pack has a search row for a document it does not hold.')
        regions = {}
        for n in z.namelist():
            if n.startswith('regions/') and n.endswith('.json'):
                try:
                    d = json.loads(z.read(n))
                except ValueError:
                    raise Refused(f'{n} in the region pack does not parse.')
                if d.get('id') != PurePosixPath(n).stem:
                    raise Refused(f'{n}: its id must match its file name.')
                regions[n] = z.read(n)
        return m, records, rows, regions, files


def _prune(lib, rels):
    """Remove the folders that held rels (a pack's own folders under documents/regions/), once they are empty."""
    stop = {lib, lib / 'documents', lib / 'text', lib / 'regions'}
    for folder in sorted({(lib / rel).parent for rel in rels}, key=lambda p: -len(p.parts)):
        while folder not in stop and lib in folder.parents:
            try:
                folder.rmdir()
            except OSError:
                break
            folder = folder.parent


def remove(data):
    """Take the installed pack's records, search rows, files and region files out of the library. Returns the code removed."""
    data = Path(data)
    lib = data / 'library-data'
    s = read_set(data)
    pack = s.get('pack')
    if not pack:
        return None
    code, prefix = pack['region'], id_prefix(pack['region'])
    cat = json.loads((lib / 'catalog.json').read_text(encoding='utf-8'))
    _write_json(lib / 'catalog.json', [r for r in cat if not str(r.get('id', '')).startswith(prefix)])
    con = sqlite3.connect(lib / 'search.sqlite')
    with con:
        con.execute('DELETE FROM pages WHERE doc_id LIKE ?', (prefix + '%',))
    con.close()
    for rel in pack.get('files', []) + pack.get('regions', []):
        try:
            (lib / rel).unlink()
        except FileNotFoundError:
            pass
    _prune(lib, pack.get('files', []))
    s.pop('pack', None)
    _write_json(data / SET_FILE, s)
    return code


def install(data, zip_path, select_region=True):
    """Install a region pack into the library at data, replacing any other region pack there. Returns region-set.json."""
    data = Path(data)
    lib = data / 'library-data'
    if not (lib / 'catalog.json').is_file() or not (lib / 'search.sqlite').is_file():
        raise Refused(f'No library at {data} (no library-data/catalog.json and search.sqlite).')
    m, records, rows, regions, files = read_pack(zip_path)   # everything is checked before the library is touched
    code = m['region']
    cat = json.loads((lib / 'catalog.json').read_text(encoding='utf-8'))
    prefix = id_prefix(code)
    clash = {r['id'] for r in records} & {r['id'] for r in cat if not str(r.get('id', '')).startswith(prefix)}
    if clash:
        raise Refused(f'The library already has a document with the id {sorted(clash)[0]}.')
    # All or nothing: until the last step, any failure puts the library back as it was, the old pack still installed.
    # 1. Write every new file beside where it goes, under a hidden name. The library is unchanged until this is done.
    # 2. Set the old pack's files aside, and move the new files into place.
    # 3. Change the search rows in one transaction, left open; write the catalog and region-set.json, each in one rename.
    # 4. Commit the search rows. Only then are the old pack's files thrown away.
    s = read_set(data)
    old = s.get('pack') or {}
    old_prefix = id_prefix(old['region']) if old.get('region') else None
    old_rels = old.get('files', []) + old.get('regions', [])
    new = {**files, **regions}
    staged, aside, placed = {}, [], []
    con = None
    try:
        for rel, blob in new.items():
            p = lib / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name('.' + p.name + '.installing')
            staged[rel] = tmp
            tmp.write_bytes(blob)
        for rel in old_rels:
            p = lib / rel
            if p.exists():
                keep = p.with_name('.' + p.name + '.previous')
                os.replace(p, keep)
                aside.append((p, keep))
        for rel, tmp in staged.items():
            os.replace(tmp, lib / rel)
            placed.append(lib / rel)
        con = sqlite3.connect(lib / 'search.sqlite')
        if old_prefix:
            con.execute('DELETE FROM pages WHERE doc_id LIKE ?', (old_prefix + '%',))
        con.executemany(f'INSERT INTO pages({",".join(SEARCH_FIELDS)}) VALUES ({",".join("?" * len(SEARCH_FIELDS))})',
                        [tuple(row.get(f) for f in SEARCH_FIELDS) for row in rows])
        kept = [r for r in cat if not (old_prefix and str(r.get('id', '')).startswith(old_prefix))]
        _write_json(lib / 'catalog.json', kept + records)
        had_set, before = (data / SET_FILE).exists(), dict(s)
        s['pack'] = {'region': code, 'revision': m.get('revision', ''), 'ids': sorted(r['id'] for r in records),
                     'files': sorted(files), 'regions': sorted(regions)}
        if select_region and (not valid(s.get('region')) or state_of(s.get('region')) != code):
            s['region'] = m.get('default_region') if valid(m.get('default_region')) and state_of(m['default_region']) == code else code
        try:
            _write_json(data / SET_FILE, s)
            con.commit()
        except BaseException:
            _write_json(lib / 'catalog.json', cat)
            if had_set:
                _write_json(data / SET_FILE, before)
            else:
                (data / SET_FILE).unlink(missing_ok=True)
            raise
    except BaseException:
        if con is not None:
            con.rollback()
            con.close()
        for p in placed:
            p.unlink(missing_ok=True)
        for p, keep in aside:
            os.replace(keep, p)
        for tmp in staged.values():
            tmp.unlink(missing_ok=True)
        _prune(lib, [rel for rel in new if rel not in old_rels])
        raise
    con.close()
    for _, keep in aside:
        keep.unlink(missing_ok=True)
    _prune(lib, old_rels)
    return s


def build(folder, zip_path):
    """Make a region pack from a folder laid out as the pack is (region-pack.json, catalog.json, search.json, regions/,
    documents/regions/<code>/). Fills each record's bytes and sha256 from its file."""
    folder = Path(folder)
    m = json.loads((folder / PACK_FILE).read_text(encoding='utf-8'))
    records = json.loads((folder / 'catalog.json').read_text(encoding='utf-8'))
    for r in records:
        blob = (folder / r['path']).read_bytes()
        r['bytes'], r['sha256'] = len(blob), hashlib.sha256(blob).hexdigest()
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(PACK_FILE, json.dumps(m, indent=1))
        z.writestr('catalog.json', json.dumps(records, indent=1, ensure_ascii=False))
        if (folder / 'search.json').is_file():
            z.write(folder / 'search.json', 'search.json')
        for p in sorted(folder.rglob('*')):
            rel = p.relative_to(folder).as_posix()
            if p.is_file() and rel.split('/')[0] in ('regions', 'documents', 'text'):
                z.write(p, rel)
    read_pack(zip_path)   # the pack it made must pass the same checks as any other
    return zip_path
