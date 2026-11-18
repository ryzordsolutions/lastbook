"""Builds Lastbook's download packs from derived/packs/pack-spec.json. A pack is a data folder: point LASTBOOK_DATA at it.

  python -I -B build_packs.py snapshot
  python -I -B build_packs.py map base|us|<state code, such as RI>
  python -I -B build_packs.py build small|medium|all|corporals-corner|firearms-hunting [--map base|us|RI] [--sample N] [--snapshot DIR] [--fetched DIR]

Everything comes from one frozen snapshot of the catalog and search file, so the indexer can run throughout. The library is only read: files that do not change are APFS
clones, and a page that changes is written as a new file here. Run it again and it does only what changed, and anything no longer in the pack goes.
Writes derived/packs/<pack>/ (the pack: pack.json, pack-manifest.tsv, pack-contents.tsv, the Sources page) and derived/packs/<pack>-build/ (for the owner, never shipped: what was
left out and why). --sample takes a few records of each kind that are on this computer, into <pack>-sample. A video on a Studio is copied only if it is already under --fetched;
this tool opens no network connection. Without it the record is left out, reported, and the build exits 1.
The owner's renames always apply (pack-spec.json collection_renames, path_renames, note_edits, and packaging/ship_edits.json id_prefixes, which the shipped guides use too).
Needs a Python whose html.parser reads as a browser does: derived/app-build/mac-venv/bin/python3 or the library's own.
"""
import argparse
import csv
import ctypes
import datetime
import fnmatch
import hashlib
import html
import importlib.util
import io
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import zipfile
import zlib
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
LIB = PROJECT / 'library-data'
GEO = PROJECT / 'derived' / 'geo'
OUT = PROJECT / 'derived' / 'packs'
SPEC = json.loads((OUT / 'pack-spec.json').read_text(encoding='utf-8'))
SIZES, ADDONS = SPEC['sizes'], SPEC['addons']
FLOOR = SPEC['disk']['floor_gb'] * 10**9
DOC_KINDS = {'pdf', 'html', 'pptx'}
SETTLING = 600       # a video changed this recently may still be being written
SOURCES = SPEC['sources_page']
SOURCES_REL = SOURCES['path'].removeprefix('library-data/')
LEAVE_OUT_IDS = tuple(SPEC['leave_out_id_prefixes'])


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SHIP = load_module('ship_app_files', PROJECT / 'packaging' / 'ship_app_files.py')    # what an app build ships: the guides' IDs and the ID renames come from here
PATH_RENAMES = {**SPEC['path_renames'], **{f'text/{a}': f'text/{b}' for a, b in SHIP.PREFIXES.items()}}   # a record's text file is named by its ID
MAP_CREDIT = 'OpenStreetMap contributors · Microsoft buildings · USGS · USDA · NOAA · USACE · CDFW · CalGEM · Natural Earth'   # as on the map itself, web/map.js


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(1 << 22):
            h.update(chunk)
    return h.hexdigest()


def refused_name(name):
    """Never opened, copied or read, wherever it is. The sign-in file's name is enough."""
    low = name.lower()
    return any(fnmatch.fnmatchcase(low, p.lower()) for p in SPEC['paths_excluded']['anywhere'] + ['video-auth*', '*cookie*'])


def inside(path, root):
    return os.path.realpath(path).startswith(os.path.realpath(root) + os.sep)


_libc = ctypes.CDLL(None, use_errno=True)


def clone(src, dst):
    """An APFS clone, or a real copy across disks. Never a hard link: the engine refuses those. Never a write into the library."""
    if inside(dst, LIB):
        raise SystemExit(f'refusing to write inside the library: {dst}')
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = f'{dst}.tmp{os.getpid()}'
    if not hasattr(_libc, 'clonefile') or _libc.clonefile(os.fsencode(src), os.fsencode(tmp), 0) != 0:
        need_space(os.path.getsize(src))
        shutil.copy2(src, tmp)
    os.chmod(tmp, 0o644)
    os.replace(tmp, dst)


def need_space(n):
    free = shutil.disk_usage(OUT).free
    if free - n < FLOOR:
        raise SystemExit(f'stopping: {n} more bytes would leave under {FLOOR // 10**9} GB free ({free // 10**9} GB free now)')


# ---------------------------------------------------------------- snapshot ----
def stat_key(*paths):
    return [(p.stat().st_size, p.stat().st_mtime_ns) for p in paths]


def indexer_busy():
    if any(n.endswith('.building.sqlite') for n in os.listdir(LIB)):
        return True
    return subprocess.run(['lsof', '-t', str(LIB / 'index.lock')], capture_output=True, text=True).stdout.strip() != ''


def take_snapshot():
    """No lock is created or taken, and only the clones are ever opened. catalog.json is written in place, so a change while cloning means start again."""
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    snap = OUT / 'snapshot' / stamp
    names = ['catalog.json', 'search.sqlite', 'summary.json', 'video-exclusions.json', 'remote-videos.json']
    live = [LIB / 'catalog.json', LIB / 'search.sqlite']
    for attempt in range(120):
        if not indexer_busy():
            before = stat_key(*live)
            for n in names:
                clone(LIB / n, snap / n)
            if not indexer_busy() and stat_key(*live) == before:
                break
        print('library is being indexed; waiting', flush=True)
        time.sleep(30)
    else:
        raise SystemExit('the indexer never stopped long enough to take a snapshot')
    catalog = json.loads((snap / 'catalog.json').read_text(encoding='utf-8'))
    db = sqlite3.connect(f'file:{snap}/search.sqlite?immutable=1', uri=True)
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise SystemExit('search.sqlite clone fails its integrity check')
    ids = {r['id'] for r in catalog}
    stray = [i for (i,) in db.execute('SELECT DISTINCT doc_id FROM pages') if i not in ids]
    if stray:
        raise SystemExit(f'{len(stray)} search documents are not in the catalog; snapshot not usable')
    (snap / 'snapshot.json').write_text(json.dumps({'taken': stamp, 'catalog_sha256': sha256_file(snap / 'catalog.json'), 'records': len(catalog)}, indent=1), encoding='utf-8')
    print(snap)
    return snap


def latest_snapshot():
    found = sorted(p for p in (OUT / 'snapshot').glob('*') if (p / 'snapshot.json').exists())
    return found[-1] if found else take_snapshot()


# ---------------------------------------------------------------- membership ----
def guide_ids():
    """The card IDs of the guides as an app build ships them: IDs renamed, cards for what no pack holds already out (ship_app_files.py)."""
    ids = set()

    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get('doc'), str):
                ids.add(o['doc'])
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    for f in sorted((PROJECT / 'content' / 'topics').glob('*.json')) + sorted((PROJECT / 'content' / 'regions').glob('*.json')):
        walk(SHIP.clean(json.loads(f.read_text(encoding='utf-8'))))   # a region file's readings are guide readings too
    return ids


def pack_id(i):
    return SHIP.new_id(i)


def pack_path(rel):
    return rename(rel, PATH_RENAMES)


def gone_paths(catalog):
    """Library paths no pack holds, whether a record names them or a page tree holds them."""
    return (set(SPEC['known_gap_documents']) | set(SPEC['pdf_one_offs']['leave_out']) | set(SPEC['leave_out_one_file'])
            | {r['path'] for r in catalog if r['id'].startswith(LEAVE_OUT_IDS)})


def course_records(catalog):
    """One record per course folder with a home page, so its pages can be reached. Every value is one that most of the course's own records carry."""
    by = defaultdict(list)
    for r in catalog:
        parts = r['path'].split('/')
        if parts[0] == 'courses' and len(parts) > 2:
            by[parts[1]].append(r)
    out = []
    for slug, rs in sorted(by.items()):
        home = f'courses/{slug}/index.html'
        if not (LIB / home).is_file():
            continue

        def most(k):
            v = Counter(r.get(k) for r in rs if r.get(k)).most_common(1)
            return v[0][0] if v else None
        rec = {'id': f'course-{slug}', 'kind': 'html', 'title': Counter(re.split(r' [\u2014-] ', r['title'])[0] for r in rs).most_common(1)[0][0], 'path': home}
        for k in ('publisher', 'collection', 'category', 'course', 'level', 'source_url', 'license_url', 'retrieved_at'):
            if most(k):
                rec[k] = most(k)
        out.append(rec)
    return out


def video_groups(catalog, snap):
    """The owner's rules. The 28 must match both of their sources, and the spec, or nothing is built."""
    verdicts = json.loads((PROJECT / SPEC['sources']['verdicts']).read_text(encoding='utf-8'))
    with open(PROJECT / SPEC['sources']['triage'], newline='', encoding='utf-8') as f:
        triage = {r['id'] for r in csv.DictReader(f) if r['group'] == 'flagged-dangerous'}
    flagged = set(SPEC['video_filters']['flagged_28']['ids'])
    if flagged != triage or flagged != {k for k, v in verdicts.items() if v.get('dangerous')} or len(flagged) != 28:
        raise SystemExit('the flagged videos no longer match verdicts.json, safety-triage.csv and the spec')
    excluded = set(json.loads((snap / 'video-exclusions.json').read_text(encoding='utf-8')).get('videos', {}))
    reason = {}
    for r in catalog:
        if r['kind'] != 'video':
            continue
        verdict = (verdicts.get(r['id']) or {}).get('verdict')
        reason[r['id']] = ('flagged, until the redone review' if r['id'] in flagged else 'owner removed' if r['id'] in excluded else 'no verdict, until the redone review' if verdict is None
                           else f'verdict {verdict}' if verdict not in ('keep', 'reference') else '')
    return verdicts, reason


def is_corporals(r):
    return r.get('publisher') == 'Corporals Corner'


def is_firearms(r):
    return r.get('category') in ('firearm-safety-maintenance', 'hunting-game-processing')


def select(pack, catalog, snap, sample):
    """(records in this pack, [(id, path, why not)])."""
    verdicts, why_out = video_groups(catalog, snap)
    remote = json.loads((snap / 'remote-videos.json').read_text(encoding='utf-8'))
    filters = SPEC['video_filters']
    guides = guide_ids()
    gone = gone_paths(catalog)
    kept = [r for r in catalog if r['kind'] == 'video' and not why_out[r['id']]]
    ordinary = [r for r in kept if not is_corporals(r) and not is_firearms(r)]
    doc = [r for r in catalog if r['kind'] in DOC_KINDS and r['path'] not in gone]
    course_tx = [r for r in catalog if r['kind'] == 'transcript' and not r.get('video_path')]
    guide_videos = [r for r in ordinary if pack_id(r['id']) in guides]
    if pack == 'small':
        chapters = tuple(SIZES['small']['with_chapters'])
        docs = [r for r in doc if pack_id(r['id']) in guides or r['path'].startswith(chapters)]
        include = set(SIZES['small'].get('video_include', []))
        exclude = set(SIZES['small'].get('video_exclude', []))
        eligible = {r['id']: r for r in ordinary}
        if include - eligible.keys():
            raise SystemExit(f'Small video additions are not eligible: {sorted(include - eligible.keys())}')
        videos = [r for r in ordinary if (r in guide_videos or r['id'] in include) and r['id'] not in exclude]
    elif pack == 'medium':
        docs = [r for r in doc if r['id'] not in filters['firearms_documents']] + course_tx
        videos = [r for r in ordinary if r['category'] in filters['medium_topic_categories'] and verdicts[r['id']]['verdict'] == 'keep']
        videos += [r for r in guide_videos if r not in videos]
    elif pack == 'all':
        docs, videos = doc + course_tx, kept
    elif pack == 'corporals-corner':
        docs, videos = [], [r for r in kept if is_corporals(r)]
    elif pack == 'firearms-hunting':
        docs, videos = [r for r in doc if r['id'] in filters['firearms_documents']], [r for r in kept if is_firearms(r) and not is_corporals(r)]
    else:
        raise SystemExit(f'unknown pack {pack}')
    zim_names = SIZES.get(pack, {}).get('zim', [])
    zims = [r for r in catalog if r['kind'] == 'zim' and os.path.basename(r['path']) in zim_names]
    if sample:
        def pick(rs, n):
            return sorted(rs, key=lambda r: hashlib.sha1(r['id'].encode()).hexdigest())[:n]
        by_kind = defaultdict(list)
        for r in docs:
            by_kind[r['kind']].append(r)
        docs = [x for rs in by_kind.values() for x in pick(rs, sample)]
        here = [r for r in videos if r['path'] not in remote and os.path.exists(LIB / r['path'])]
        videos = pick(here, max(2, sample // 10))
        zims = sorted(zims, key=lambda r: r['bytes'])[:1]
    chosen = {r['id']: r for r in docs + videos + zims}
    by_video = {r['path'] for r in videos}
    for r in catalog:
        if r['kind'] == 'transcript' and r.get('video_path') in by_video:
            chosen[r['id']] = r
        elif r['kind'] == 'metadata' and r.get('video_path') in by_video and r.get('thumbnail_path') and os.path.exists(LIB / r['thumbnail_path']):
            chosen[r['id']] = r
    out = []
    for r in catalog:
        if r['id'] not in chosen:
            why = why_out.get(r['id']) or ('known gap' if r['path'] in SPEC['known_gap_documents'] else 'one-off left out' if r['path'] in gone
                                           else 'video metadata: only thumbnails ship' if r['kind'] == 'metadata' else 'not in this pack')
            out.append((r['id'], r['path'], why))
    return chosen, out, remote


# ---------------------------------------------------------------- stripping ----
REMOTE = re.compile(r'^[\x00-\x20]*(?:[a-z][a-z0-9+.\-]*:|[/\\][/\\])', re.I)
INERT_DATA = re.compile(r'^[\x00-\x20]*data:image/(?:png|jpe?g|gif|webp)[;,]', re.I)
CSS_DATA = re.compile(r'^\s*data:(?:image|font|application/(?:x-)?font)', re.I)
WEB_VALUE = re.compile(r'^\s*(?:[a-z]+://|//)', re.I)
ALWAYS_OFF = {'action', 'formaction', 'ping', 'manifest', 'codebase', 'classid', 'archive', 'profile'}
URL_ATTRS = {'src', 'poster', 'background', 'data', 'lowsrc', 'dynsrc', 'longdesc', 'cite', 'xml:base'}
OFF_META = {'refresh', 'set-cookie', 'content-security-policy', 'x-ua-compatible'}
UNWRAP = {'object', 'applet', 'embed', 'param'}
CSS_IMPORT = re.compile(r'@import\b[^;]*;?', re.I)
CSS_URL = re.compile(r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^)]*))\s*\)""", re.I)


def no_ws(v):
    return re.sub(r'[\t\r\n]', '', v or '')


def is_remote(v):
    v = no_ws(v)
    return bool(REMOTE.match(v)) and not INERT_DATA.match(v)


def clean_css(s, changes):
    def one(m):
        url = m.group(1) or m.group(2) or m.group(3) or ''
        if is_remote(url) and not CSS_DATA.match(url):
            changes['css_remote_url'] += 1
            return 'none'
        return m.group(0)
    new, n = CSS_IMPORT.subn('', s)
    changes['css_import'] += n
    return CSS_URL.sub(one, new)


def srcset_entries(v):
    """A list of (url, rest). The URL runs to white space, and a comma can only end it when it is the URL's last character."""
    out, i, n = [], 0, len(v)
    while i < n:
        while i < n and (v[i].isspace() or v[i] == ','):
            i += 1
        j = i
        while j < n and not v[j].isspace():
            j += 1
        url = v[i:j]
        if url.endswith(','):
            out.append((url.rstrip(','), ''))
            i = j
            continue
        k = j
        while k < n and v[k] != ',':
            k += 1
        if url:
            out.append((url, v[j:k].strip()))
        i = k + 1
    return out


class Stripper(HTMLParser):
    """Copies the page's own text, except where a handler says otherwise: updatepos is the one place the parser reports each stretch of input as done."""

    def __init__(self, changes, dead=None, web=None):
        super().__init__(convert_charrefs=False)
        self.out, self.changes, self.refs, self.dead = [], changes, [], dead or (lambda ref: False)   # dead(ref): the pack does not hold what this local reference names
        self.web = web or {}    # a catalog record's source_url or final_url -> its library path
        self.swap, self.skip, self.in_style = None, None, False
        self.bare_a = False     # the open <a> lost its tag, so its end tag goes too

    def updatepos(self, i, j):
        if i < j:
            if self.swap is not None:
                self.out.append(self.swap)
            elif not self.skip:
                self.out.append(self.rawdata[i:j])
        self.swap = None
        return super().updatepos(i, j)

    def attrs_ok(self, tag, attrs):
        """Attributes that may stay, and whether any had to change."""
        keep, c, changed = [], self.changes, False
        for k, v in attrs:
            low, val = k.lower(), v or ''
            if low.startswith('on'):
                c['event_attribute'] += 1
            elif low in ALWAYS_OFF:
                c['attr_' + low] += 1
            elif low in ('href', 'xlink:href') and tag == 'base':
                c['base_href'] += 1
            elif low in ('href', 'xlink:href') and tag in ('a', 'area'):
                base, frag = urllib.parse.urldefrag(no_ws(val)) if REMOTE.match(no_ws(val)) else ('', '')
                held = self.web.get(base)
                if held and not self.dead('/' + urllib.parse.quote(held)):   # the library holds that address's copy: the link opens it, as the server maps it today, and no address remains
                    self.refs.append(('a', '/' + urllib.parse.quote(held)))
                    keep.append((k, '/' + urllib.parse.quote(pack_path(held)) + ('#' + frag if frag else '')))
                    c['link_local'] += 1
                elif REMOTE.match(no_ws(val)):     # the web, javascript:, data:, mailto: and the rest: owner decision A, the words stay and the address goes
                    c['link_out'] += 1
                elif self.dead(val):
                    c['dead_link'] += 1
                else:
                    if val:
                        self.refs.append(('a', val))
                    keep.append((k, v))
                    continue
            elif low in ('href', 'xlink:href') or low in URL_ATTRS:
                if is_remote(val):
                    c['remote_' + low] += 1
                elif tag in ('source', 'img') and low == 'src' and self.dead(val):
                    c['dead_source'] += 1
                else:
                    if val and low not in ('longdesc', 'cite') and not val.startswith('data:'):
                        self.refs.append((tag, val))
                    keep.append((k, v))
                    continue
            elif low in ('srcset', 'imagesrcset'):
                entries = srcset_entries(val)
                good = [(u, d) for u, d in entries if not is_remote(u) and not self.dead(u)]
                self.refs += [(tag, u) for u, _ in good if not u.startswith('data:')]
                if len(good) == len(entries):
                    keep.append((k, v))
                    continue
                c['remote_srcset'] += sum(is_remote(u) for u, _ in entries)
                c['dead_source'] += sum(not is_remote(u) and self.dead(u) for u, _ in entries)
                if good:
                    keep.append((k, ', '.join(f'{u} {d}'.strip() for u, d in good)))
            elif low == 'style' and val:
                new = clean_css(val, c)
                self.refs += [('css', x) for x in css_refs(new)]
                keep.append((k, new))
                changed = changed or new != val
                continue
            elif low.startswith('data-') and WEB_VALUE.match(val):
                c['data_attribute'] += 1
            elif tag in ('animate', 'set') and low in ('to', 'from', 'by', 'values') and is_remote(val):
                c['animated_url'] += 1
            else:
                keep.append((k, v))
                continue
            changed = True
        return keep, changed

    def start(self, tag, attrs, closed):
        c = self.changes
        if self.skip:
            return
        a = {k.lower(): v or '' for k, v in reversed(attrs)}
        drop = False
        if tag == 'script' or (tag == 'iframe' and (is_remote(a.get('src')) or not a.get('src'))):
            c['element_' + tag] += 1
            self.swap = ''
            if not closed:
                self.skip = tag
            return
        if tag in UNWRAP:
            c['element_' + tag] += 1
            drop = True
        elif tag == 'frame' and is_remote(a.get('src')):
            c['element_frame'] += 1
            drop = True
        elif tag == 'link' and not ('stylesheet' in a.get('rel', '').lower() and a.get('href') and not is_remote(a['href'])):
            c['element_link'] += 1
            drop = True
        elif tag == 'meta' and a.get('http-equiv', '').strip().lower() in OFF_META:
            c['element_meta'] += 1
            drop = True
        elif tag == 'source' and is_remote(a.get('src')):
            c['element_source'] += 1
            drop = True
        if drop:
            self.swap = ''
            return
        if tag == 'a':
            self.bare_a = False      # a new <a> closes any open one
        keep, changed = self.attrs_ok(tag, attrs)
        names = {k.lower() for k, v in keep if v and no_ws(v).strip()}     # attributes left with a value: <img src> shows nothing
        if (tag == 'base' and not keep) or (tag == 'source' and not names & {'src', 'srcset'}):   # a browser takes a <source> over the <img> beside it, even when it can only fail
            self.swap = ''
        elif tag == 'img' and not names & {'src', 'srcset'}:      # nothing it could show: an empty box
            c['image_with_no_source'] += 1
            self.swap = ''
        elif tag in ('a', 'area') and changed and not names & {'href', 'xlink:href'} and not (tag == 'a' and names & {'id', 'name'}):   # its link went: plain text
            self.swap = ''
            self.bare_a = tag == 'a' and not closed
        elif changed:
            inner = ''.join(f' {k}' if v is None else ' {}="{}"'.format(k, v.replace('&', '&amp;').replace('"', '&quot;')) for k, v in keep)
            self.swap = f'<{tag}{inner}{"/" if closed else ""}>'
        if tag == 'style' and not closed:
            self.in_style = True

    def handle_starttag(self, tag, attrs):
        self.start(tag, attrs, False)

    def handle_startendtag(self, tag, attrs):
        self.start(tag, attrs, True)

    def handle_endtag(self, tag):
        if self.skip:
            if tag == self.skip:
                self.skip = None
                self.swap = ''
            return
        if tag in UNWRAP or tag == 'script' or (tag == 'a' and self.bare_a):
            self.swap = ''
            self.bare_a = self.bare_a and tag != 'a'
        elif tag == 'style':
            self.in_style = False

    def handle_data(self, data):
        if self.in_style and not self.skip:
            new = clean_css(data, self.changes)
            self.refs += [('css', x) for x in css_refs(new)]
            if new != data:
                self.swap = new

    def handle_comment(self, data):
        self.changes['comment'] += 1
        self.swap = ''

    def handle_pi(self, data):
        self.changes['processing_instruction'] += 1
        self.swap = ''

    def unknown_decl(self, data):
        self.changes['marked_section'] += 1
        self.swap = ''


def css_refs(css):
    out = []
    for m in CSS_URL.finditer(css):
        url = (m.group(1) or m.group(2) or m.group(3) or '').strip()
        if url and not url.startswith('data:') and not is_remote(url):
            out.append(url)
    return out


def strip_page(data, saved_pages, dead=None, web=None):
    """(new bytes, what changed, local references). Bytes that decode badly survive untouched: surrogateescape round-trips them."""
    changes = Counter()
    if len(data) > saved_pages.MAX_PAGE:
        data, n = saved_pages._BLANK_ROW.subn(b'', data)
        changes['blank_row'] += n
    p = Stripper(changes, dead, web)
    p.feed(data.decode('utf-8', 'surrogateescape'))
    p.close()
    if p.rawdata and not p.skip:   # an unclosed style or the like, which the parser holds back
        p.out.append(p.rawdata)
    new = ''.join(p.out).encode('utf-8', 'surrogateescape')
    return new, +changes, p.refs


EXTERNAL_REL = re.compile(rb"""<Relationship\b(?:[^>"']|"[^"]*"|'[^']*')*\bTargetMode\s*=\s*["']External["'](?:[^>"']|"[^"]*"|'[^']*')*>""", re.I)
REL_ID = re.compile(rb"""\bId\s*=\s*["']([^"']*)["']""")
OFFICE_FILES = ('.pptx', '.docx', '.xlsx')


def strip_office(data):
    """(new bytes, links taken out, references left). An Office file's links to the web are relationships marked External.
    Owner decision A, as for saved pages: the words stay, the addresses go. Each such relationship goes, with the hyperlink
    elements that use it; every other part is copied as it is, with its own name, date and compression."""
    src = zipfile.ZipFile(io.BytesIO(data))
    parts = {i.filename: src.read(i) for i in src.infolist()}
    gone = {}     # part name -> ids taken out of its relationships
    for name, body in parts.items():
        if name.endswith('.rels') and EXTERNAL_REL.search(body):
            ids = [REL_ID.search(m.group(0)).group(1) for m in EXTERNAL_REL.finditer(body) if REL_ID.search(m.group(0))]
            parts[name] = EXTERNAL_REL.sub(b'', body)
            folder, rels = name.rsplit('_rels/', 1) if '_rels/' in name else ('', name)
            gone[folder + rels[:-len('.rels')]] = ids
    links = left = 0
    for name, ids in gone.items():
        body = parts.get(name, b'')
        for rid in ids:
            q = re.escape(rid)
            body, n1 = re.subn(rb'<a:hlink(?:Click|Hover)\b[^>]*\br:id="' + q + rb'"[^>]*/>', b'', body)
            body, n2 = re.subn(rb'<a:hlink(?:Click|Hover)\b[^>]*\br:id="' + q + rb'"[^>]*>.*?</a:hlink(?:Click|Hover)>', b'', body, flags=re.S)
            links += 1
            left += len(re.findall(rb'"' + q + rb'"', body))
        if name in parts:
            parts[name] = body
    if not gone:
        return data, 0, 0
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for i in src.infolist():
            z.writestr(i, parts[i.filename], compress_type=i.compress_type)
    return out.getvalue(), links, left


PDF_STREAM = re.compile(rb'(?<![A-Za-z])stream\r?\n')     # found as scan_pack.pdf_views finds them, so both read the same parts
PDF_URI = re.compile(rb'/(?:URI|Base)(?![A-Za-z0-9#])\s*')   # a link's address, and a file's base address for its links
PDF_REF = re.compile(rb'(\d+)\s+(\d+)\s+R')


def blank_string(buf, i):
    """The end of the string at buf[i] after emptying it, padded with spaces to its old length; (end, emptied) or (None, False)."""
    if buf[i:i + 1] == b'(':
        j, depth = i + 1, 1
        while j < len(buf) and depth:
            if buf[j] == 0x5c:
                j += 1
            elif buf[j] == 0x28:
                depth += 1
            elif buf[j] == 0x29:
                depth -= 1
            j += 1
        if depth:
            return None, False
        empty = b'()'
    elif buf[i:i + 1] == b'<' and buf[i + 1:i + 2] != b'<':
        j = buf.find(b'>', i)
        if j < 0:
            return None, False
        j += 1
        empty = b'<>'
    else:
        return None, False
    if not bytes(buf[i + 1:j - 1]).strip():
        return j, False
    buf[i:j] = empty + b' ' * (j - i - 2)
    return j, True


def blank_uris(buf, lo=0, hi=None):
    """In place, in buf[lo:hi], a bytearray: every /URI or /Base string becomes an empty string padded with spaces to its old
    length, so every offset in the file stays right. (taken out, left in, [(object number, buf, start, end)] of each address held elsewhere)."""
    out = left = 0
    refs = []
    for m in list(PDF_URI.finditer(buf, lo, len(buf) if hi is None else hi)):
        i = m.end()
        j, done = blank_string(buf, i)
        if done:
            out += 1
        elif j is None:
            ref = PDF_REF.match(buf, i)
            if ref:
                refs.append((int(ref.group(1)), buf, ref.start(), ref.end()))
            elif buf[i:i + 1] in (b'(', b'<') and buf[i:i + 2] != b'<<':
                left += 1                                # a string with no end
            # otherwise /S /URI names the action, and its address follows
    return out, left, refs


PDF_SPACE = bytes(b' \n\r\t\f\0'[(i * 7 + i // 5) % 6] for i in range(4096))   # white space in a mix that packs poorly


def deflate_exactly(plain, size):
    """A zlib stream of exactly size bytes that inflates to plain followed by white space, or None. The same length keeps
    /Length and every offset in the file right, and white space after the last object of an object stream is nothing to
    its reader. Plenty of room: a final stored block of spaces. Little room: white space added until the length is right."""
    co = zlib.compressobj(9, zlib.DEFLATED, 15, 9)
    body = co.compress(plain) + co.flush(zlib.Z_SYNC_FLUSH)    # ends on a byte boundary, not final
    room = size - len(body) - 4
    if room >= 5:
        blocks, pad = [], 0
        while room:
            n = min(65535, room - 5) if room - 5 <= 65535 else min(65535, room - 10)
            room -= 5 + n
            blocks.append(bytes([0 if room else 1]) + n.to_bytes(2, 'little') + (n ^ 0xffff).to_bytes(2, 'little') + b' ' * n)
            pad += n
        full = plain + b' ' * pad
        new = body + b''.join(blocks) + zlib.adler32(full).to_bytes(4, 'big')
        return new if len(new) == size and zlib.decompress(new) == full else None
    for level in (9, 8, 7, 6):
        for mem in (9, 8):
            for mixed in range(24):
                for spaces in range(0, 96):
                    full = plain + PDF_SPACE[:mixed] + b' ' * spaces
                    co = zlib.compressobj(level, zlib.DEFLATED, 15, mem)
                    new = co.compress(full) + co.flush()
                    if len(new) == size:
                        return new if zlib.decompress(new) == full else None
                    if len(new) > size + 1:
                        break
    return None


def strip_pdf(data):
    """(new bytes, links taken out, links left in). Owner decision, 2026-11-03, 'Take them out': a PDF keeps no address
    of a link to the web. Each /URI address is emptied where a reader finds it, in the file's own objects and in its
    object streams, and nothing else changes: same length, same offsets, same pages. Links inside the file are /GoTo and stay."""
    if b'/URI' not in data and b'/ObjStm' not in data:
        return data, 0, 0
    buf = bytearray(data)
    out = left = 0
    raw, objstms, pos = [], [], 0             # raw: (lo, hi) of the file's own objects; objstms: [start, size, plain, first, changed]
    for m in PDF_STREAM.finditer(data):
        if m.start() < pos:
            continue
        end = data.find(b'endstream', m.end())
        if end < 0:
            break
        head = data[max(pos, m.start() - 2048):m.start()]
        head = head[head.rfind(b' obj'):] if b' obj' in head else head
        if b'/Filter' not in head:
            continue                          # unfiltered: read as it is, like the file's own objects
        raw.append((pos, m.end()))
        pos = end + 9
        if b'/ObjStm' not in head:
            continue
        body = data[m.end():end]
        flate_only = re.search(rb'/Filter\s*(?:/FlateDecode|\[\s*/FlateDecode\s*\])', head) and b'/DecodeParms' not in head
        if b'/Encrypt' in data or not flate_only:
            left += len(PDF_URI.findall(body))   # cannot be read here; the scan fails it if it holds an address
            continue
        try:
            d = zlib.decompressobj()
            plain = bytearray(d.decompress(body))
        except zlib.error:
            continue
        first = re.search(rb'/First\s+(\d+)', head)
        objstms.append([m.end(), len(body) - len(d.unused_data), plain, int(first.group(1)) if first else None, 0])
    raw.append((pos, len(data)))

    refs = []
    for lo, hi in raw:
        n, k, r = blank_uris(buf, lo, hi)
        out, left, refs = out + n, left + k, refs + r
    for o in objstms:
        n, k, r = blank_uris(o[2])
        o[4] += n
        out, left, refs = out + n, left + k, refs + r
    # An address held in an object of its own, a string or a dictionary with /Base in it: that object is emptied,
    # and the reference to it becomes an empty one of the same kind, () or <<>>, padded to its old length.
    kinds = {}
    for num in sorted({r[0] for r in refs}):
        rx = re.compile(rb'(?<![0-9])' + str(num).encode() + rb'\s+\d+\s+obj\s*')
        for lo, hi in raw:
            for m in rx.finditer(buf, lo, hi):
                kinds[num] = buf[m.end():m.end() + 2]
                out += blank_string(buf, m.end())[1]
                if kinds[num] == b'<<':
                    end = buf.find(b'>>', m.end())
                    n, k, r = blank_uris(buf, m.end(), end if end > 0 else hi)
                    out, left = out + n, left + k
        for o in objstms:
            if o[3] is None:
                continue
            nums = [int(x) for x in re.findall(rb'\d+', bytes(o[2][:o[3]]))]
            for n_, off in zip(nums[0::2], nums[1::2]):
                if n_ == num:
                    i = o[3] + off
                    while i < len(o[2]) and o[2][i] in b' \r\n\t\f\0':
                        i += 1
                    kinds[num] = o[2][i:i + 2]
                    done = blank_string(o[2], i)[1]
                    if kinds[num] == b'<<':
                        end = o[2].find(b'>>', i)
                        done += blank_uris(o[2], i, end if end > 0 else None)[0]
                    o[4] += done
                    out += done
    for num, where, a, b in refs:
        kind = kinds.get(num, b'')
        empty = b'<<>>' if kind == b'<<' else b'()' if kind[:1] in (b'(', b'<') else None
        if empty is None or b - a < len(empty):
            left += 1
            continue
        where[a:b] = empty + b' ' * (b - a - len(empty))
        for o in objstms:
            if o[2] is where:
                o[4] += 1
    for start, size, plain, first, changed in objstms:
        if not changed:
            continue
        new = deflate_exactly(bytes(plain), size)
        if new is None:
            left += changed
            out -= changed
            continue
        buf[start:start + size] = new
    new = bytes(buf)
    assert len(new) == len(data)
    return new, out, left


def load_saved_pages():
    return load_module('saved_pages', PROJECT / 'engine' / 'saved_pages.py')


# ---------------------------------------------------------------- files ----
def allowed_ext(rel):
    top = rel.split('/')[0]
    ext = os.path.splitext(rel)[1].lower()
    lists = SPEC['extension_allow_list']
    if top in ('pages', 'assets', 'courses'):
        return ext in lists[top]
    return top in ('documents', 'videos', 'videos-external', 'transcripts', 'text', 'metadata', 'collections')


def resolve_ref(ref, page):
    """The library path a page's reference names, or None."""
    ref = urllib.parse.unquote(no_ws(ref).split('#')[0].split('?')[0]).replace('\\', '/').strip()
    if not ref:
        return None
    rel = os.path.normpath(ref.lstrip('/') if ref.startswith('/') else os.path.join(os.path.dirname(page), ref)).replace('\\', '/')
    return None if rel.startswith('..') else true_name(rel)


_folders = {}


def true_name(rel):
    """A page may name a file in the wrong case, which this disk and Windows both forgive. The pack holds it once, under its real name."""
    folder, name = os.path.split(rel)
    if folder not in _folders:
        try:
            _folders[folder] = {n.lower(): n for n in os.listdir(LIB / folder)}
        except OSError:
            _folders[folder] = {}
    return f'{folder}/{_folders[folder].get(name.lower(), name)}'


IMAGE = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'}


def wanted_ref(kind, rel):
    """What a page brings along. A link to another page does not, or one guide page would pull in a whole course: such a link just does nothing in a smaller pack."""
    if refused_name(os.path.basename(rel)):
        return False
    ext = os.path.splitext(rel)[1].lower()
    if kind == 'a' and ext not in IMAGE:
        return False
    if rel.split('/')[0] == 'documents':
        return ext in IMAGE | {'.css', '.html', '.htm'}
    return rel == 'reader.css' or (rel.split('/')[0] in ('pages', 'courses', 'assets') and allowed_ext(rel))


class Build:
    def __init__(self, name, pack_dir, build_dir, fetched):
        self.pack, self.build_dir, self.fetched = pack_dir, build_dir, fetched
        self.name = name
        self.placed = {}    # pack path -> (how, changes)
        self.problems = []
        self.saved_pages = load_saved_pages()
        self.web = {}
        cache = build_dir / 'hash-cache.json'
        self.hashes = json.loads(cache.read_text(encoding='utf-8')) if cache.exists() else {}

    def lib_dest(self, rel):
        return self.pack / 'library-data' / rel

    def clone_in(self, src, dest, how='clone'):
        if not (dest.exists() and dest.stat().st_size == os.path.getsize(src) and dest.stat().st_mtime_ns == os.stat(src).st_mtime_ns):
            clone(src, dest)
        self.placed[dest.relative_to(self.pack).as_posix()] = (how, '')

    def write(self, dest, data, how, changes=''):
        os.makedirs(dest.parent, exist_ok=True)
        if not dest.exists() or dest.read_bytes() != data:
            need_space(len(data))
            tmp = dest.with_name(dest.name + f'.tmp{os.getpid()}')
            tmp.write_bytes(data)
            os.replace(tmp, dest)
        self.placed[dest.relative_to(self.pack).as_posix()] = (how, changes)

    def page(self, rel, dest_rel, dead=None):
        """Strips one page into the pack; returns the local files it names."""
        data = (LIB / rel).read_bytes()
        if data[:2] in (b'\xff\xfe', b'\xfe\xff') or data[:512].count(b'\0') > 64:   # UTF-16: no parser here reads it, and nor does the engine's repair
            self.problems.append((rel, 'UTF-16 page left out'))
            return None
        new, changes, refs = strip_page(data, self.saved_pages, dead, self.web)
        dest = self.lib_dest(dest_rel)
        if new == data:
            self.clone_in(LIB / rel, dest)
        else:
            self.write(dest, new, 'stripped', ','.join(f'{k}={v}' for k, v in sorted(changes.items())))
        return refs

    def office(self, rel, dest_rel):
        """An Office file with its web links taken out, or a clone when it has none."""
        data = (LIB / rel).read_bytes()
        try:
            new, links, left = strip_office(data)
        except (zipfile.BadZipFile, KeyError, AttributeError) as e:
            self.problems.append((rel, f'Office file not read: {e}'))
            return
        if left:
            self.problems.append((rel, f'{left} references to a removed web link remain'))
        if new == data:
            self.clone_in(LIB / rel, self.lib_dest(dest_rel))
        else:
            self.write(self.lib_dest(dest_rel), new, 'stripped', f'office_link_out={links}')

    def pdf(self, rel, dest_rel):
        """A PDF with the addresses of its web links emptied, or a clone when it has none. Where an object stream will not
        pack back into its old length, pdfium writes the file out afresh, with no object streams, and that copy is emptied.
        So too for an encrypted file with object streams, whose links cannot be read in place: that copy is kept if it had any."""
        data = (LIB / rel).read_bytes()
        new, links, left = strip_pdf(data)
        how = f'pdf_link_out={links}'
        encrypted = b'/Encrypt' in data
        if (left and not encrypted) or (encrypted and b'/ObjStm' in data):
            import pypdfium2
            doc = pypdfium2.PdfDocument(data)
            pages = len(doc)
            out = io.BytesIO()
            doc.save(out)
            doc.close()
            again, links2, left2 = strip_pdf(out.getvalue())
            check = pypdfium2.PdfDocument(again)
            if not left2 and len(check) == pages and (links2 or not encrypted):
                new, links, left, how = again, links2, 0, f'pdf_link_out={links2},pdf_rewritten=1'
            check.close()
        if left:
            self.problems.append((rel, f'{left} PDF web links could not be taken out'))
        if new == data:
            self.clone_in(LIB / rel, self.lib_dest(dest_rel))
        else:
            self.write(self.lib_dest(dest_rel), new, 'stripped', how)

    def tree(self, top, only=None):
        for root, dirs, files in os.walk(LIB / top):
            rel_root = os.path.relpath(root, LIB).replace(os.sep, '/')
            dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
            for f in sorted(files):
                rel = f'{rel_root}/{f}'
                if (only is None or rel.split('/')[1] in only) and not refused_name(f) and allowed_ext(rel):
                    yield rel


def named_owner(r):
    """'See source' names no one. Where a record says only that, the web host it came from is the owner it can name."""
    if r.get('publisher') == 'See source' and r.get('source_url'):
        return urllib.parse.urlsplit(r['source_url']).hostname or r['publisher']
    return r.get('publisher')


def rename(rel, renames):
    for a, b in renames.items():
        if rel.startswith(a):
            return b + rel[len(a):]
    return rel


def pack_record(r, fields):
    """A record as the pack carries it: allowed fields only, the owner's renames and note edits, an owner named."""
    out = {k: r[k] for k in fields if k in r}
    out['id'] = pack_id(r['id'])
    for k in ('path', 'text_path', 'vtt_path', 'video_path', 'thumbnail_path'):
        if out.get(k):
            out[k] = pack_path(out[k])
    if out.get('captions'):
        out['captions'] = [pack_path(c) for c in out['captions']]
    if r.get('publisher'):
        out['publisher'] = named_owner(r)
    if out.get('collection') in SPEC['collection_renames']:
        out['collection'] = SPEC['collection_renames'][out['collection']]
    for a, b in SPEC['note_edits'].items():
        if a in (out.get('notes') or ''):
            out['notes'] = out['notes'].replace(a, b)
    if r['kind'] == 'transcript' and not r.get('video_path'):     # text with no video to play: the app points a player at anything it calls a transcript
        out['kind'] = 'html'
    return out


def build_pack(args):
    pack_name = args.pack + ('-sample' if args.sample else '')
    pack_dir, build_dir = OUT / pack_name, OUT / f'{pack_name}-build'
    os.makedirs(pack_dir, exist_ok=True)
    os.makedirs(build_dir, exist_ok=True)
    snap = Path(args.snapshot) if args.snapshot else latest_snapshot()
    catalog = json.loads((snap / 'catalog.json').read_text(encoding='utf-8'))
    web = {r[k]: r['path'] for r in catalog for k in ('source_url', 'final_url') if r.get(k)}   # as engine/library.py maps them when it serves a page
    catalog += course_records(catalog)
    chosen, left_out, remote = select(args.pack, catalog, snap, args.sample)
    gone = gone_paths(catalog)
    b = Build(pack_name, pack_dir, build_dir, Path(args.fetched) if args.fetched else None)
    b.web = web
    now = time.time()
    missing = []
    records = []

    def missing_rec(r, why):
        missing.append((r['id'], r['path'], why))

    # Videos first: a video that cannot be placed takes its transcript and thumbnail with it.
    dropped_videos = set()
    for r in chosen.values():
        if r['kind'] != 'video':
            continue
        src = LIB / r['path']
        if r['path'] in remote:
            src = (b.fetched / r['path']) if b.fetched else None
            if not src or not src.exists():
                missing_rec(r, f"on a Studio and not fetched; {r['bytes']} bytes")
                dropped_videos.add(r['path'])
                continue
        elif not src.exists():
            missing_rec(r, 'file not on this computer')
            dropped_videos.add(r['path'])
            continue
        if os.path.getsize(src) != r['bytes']:
            missing_rec(r, f'size {os.path.getsize(src)} differs from catalog {r["bytes"]}')
            dropped_videos.add(r['path'])
        elif now - os.stat(src).st_mtime < SETTLING:
            missing_rec(r, 'changed in the last 10 minutes')
            dropped_videos.add(r['path'])
        else:
            b.clone_in(src, b.lib_dest(r['path']))
    for r in list(chosen.values()):
        if r.get('video_path') in dropped_videos or r['path'] in dropped_videos:
            del chosen[r['id']]

    # Everything a record names, and the pages' own files. Pages are stripped; the rest is cloned.
    todo, done, pages = [], set(), {}
    if args.pack in ('medium', 'all'):
        for top in ('pages', 'courses'):
            only = {r['path'].split('/')[1] for r in chosen.values() if r['path'].startswith(top + '/')} if args.sample else None
            todo += [(rel, 'tree') for rel in b.tree(top, only) if rel not in gone]
    for r in list(chosen.values()):
        if r['kind'] == 'metadata':
            names = [r['thumbnail_path']]
        elif r['kind'] in ('video', 'zim'):   # their own files are already placed, or are placed below
            names = [r.get('text_path'), *(r.get('captions') or [])]
        else:
            names = [r['path'], r.get('vtt_path'), r.get('text_path')]
        todo += [(p, 'record') for p in names if p and p not in gone]
    todo.append(('reader.css', 'record'))
    while todo:
        rel, why = todo.pop()
        if rel in done:
            continue
        done.add(rel)
        name = os.path.basename(rel)
        if refused_name(name) or not (LIB / rel).is_file():
            if why == 'record' and not refused_name(name):
                b.problems.append((rel, 'named by a record, file not found'))
            continue
        dest_rel = pack_path(rel)
        low = rel.lower()
        if low.endswith(('.html', '.htm')):
            refs = b.page(rel, dest_rel)
            if refs is None:
                continue
            pages[rel] = (dest_rel, refs)
            for kind, ref in refs:
                target = resolve_ref(ref, rel)
                if target and target not in gone and wanted_ref(kind, target):
                    todo.append((target, 'page'))
        elif low.endswith(OFFICE_FILES):
            b.office(rel, dest_rel)
        elif low.endswith('.pdf'):
            b.pdf(rel, dest_rel)
        else:
            if why == 'page' and low.endswith('.css'):
                for ref in css_refs((LIB / rel).read_text(encoding='utf-8', errors='replace')):
                    target = resolve_ref(ref, rel)
                    if target and wanted_ref('css', target):
                        todo.append((target, 'page'))
            b.clone_in(LIB / rel, b.lib_dest(dest_rel))

    # Only now is it known what the pack holds, so only now can a page be made to stop pointing at what it does not: a link becomes plain text, a picture source or image goes.
    def holds(target):
        dest = 'library-data/' + pack_path(target.lstrip('/'))
        return dest in b.placed or f'{dest}/index.html' in b.placed
    for rel, (dest_rel, refs) in pages.items():
        def dead(ref, page=rel):
            target = None if REMOTE.match(no_ws(ref)) else resolve_ref(ref, page)
            return bool(target) and not holds(target)
        if any(kind != 'css' and dead(ref) for kind, ref in refs):
            b.page(rel, dest_rel, dead)

    # ZIMs and the map set.
    for r in chosen.values():
        if r['kind'] == 'zim':
            b.clone_in(LIB / r['path'], b.lib_dest(r['path']))
    map_set = args.map or SIZES.get(args.pack, {}).get('map')
    if args.pack in ADDONS or map_set == 'none':
        map_set = None
    elif map_set == 'state':
        raise SystemExit('this size takes a state: say --map XX')
    if map_set:
        built = OUT / 'maps' / map_set
        if not (built / 'map-set.json').exists():
            build_map(map_set)
        for f in sorted(built.rglob('*')):
            if f.is_file() and f.name != 'map-set.json':
                b.clone_in(f, pack_dir / f.relative_to(built))

    # The catalog: allowed fields only, and each record's true size and hash.
    fields, meta_fields = SPEC['catalog_fields_allowed'], SPEC['metadata_records']['fields']
    missing_files = []
    placed = b.placed
    for r in catalog:
        if r['id'] not in chosen:
            continue
        if r['kind'] == 'metadata':
            out = {k: r[k] for k in meta_fields if k in r}
            out['path'] = out['thumbnail_path']
            dest = pack_dir / 'library-data' / out['thumbnail_path']
            if not dest.exists():
                missing_files.append((r['id'], r['thumbnail_path'], 'thumbnail not placed'))
                continue
            out['bytes'] = dest.stat().st_size
        else:
            out = pack_record(r, fields)
            dest = pack_dir / 'library-data' / out['path']
            if not dest.exists():
                missing_files.append((r['id'], r['path'], 'file not placed'))
                continue
            if out.get('text_path') and not (pack_dir / 'library-data' / out['text_path']).exists():
                del out['text_path']
            if out.get('vtt_path') and not (pack_dir / 'library-data' / out['vtt_path']).exists():
                del out['vtt_path']
            out['bytes'] = dest.stat().st_size
            if placed.get(dest.relative_to(pack_dir).as_posix(), ('', ''))[0] == 'stripped':
                out['sha256'] = sha256_file(dest)
            elif r.get('sha256'):
                out['sha256'] = b_hash(b, dest)
                if out['sha256'] != r['sha256']:
                    if r['kind'] in ('video', 'zim'):
                        missing_files.append((r['id'], r['path'], 'sha256 differs from catalog'))
                        placed.pop(dest.relative_to(pack_dir).as_posix(), None)
                        continue
                    b.problems.append((r['path'], 'file differs from catalog; the pack records what is really there'))
        records.append(out)
    missing += missing_files

    # The Sources page and its text.
    page, text = sources_page(records)
    page_dest = pack_dir / 'library-data' / SOURCES_REL
    b.write(page_dest, page.encode('utf-8'), 'generated')
    text_rel = f"text/{SOURCES['record']['id']}.txt"
    b.write(pack_dir / 'library-data' / text_rel, text.encode('utf-8'), 'generated')
    records.append({**SOURCES['record'], 'path': SOURCES_REL, 'bytes': len(page.encode('utf-8')), 'sha256': sha256_file(page_dest), 'text_path': text_rel, 'source_url': '', 'notes': 'Generated from this pack.'})

    b.write(pack_dir / 'library-data' / 'catalog.json', json.dumps(records, indent=1).encode('utf-8'), 'generated')
    build_search(snap, pack_dir / 'library-data' / 'search.sqlite', records, text, b)
    summary = {'resources': len(records), 'pdfs': sum(r['kind'] == 'pdf' for r in records), 'pdf_pages': sum(r.get('pages') or 0 for r in records),
               'web_pages': sum(r['kind'] in ('html', 'htm') for r in records), 'videos': sum(r['kind'] == 'video' for r in records),
               'collections': sum(r['kind'] == 'zim' for r in records), 'document_bytes': sum(r['bytes'] for r in records)}
    b.write(pack_dir / 'library-data' / 'summary.json', json.dumps(summary, indent=1).encode('utf-8'), 'generated')

    # Contents, what was left out, what goes.
    lines = ['id\tkind\ttitle\tpublisher\tsource_url\tpath\tbytes']
    for r in records:
        if r['kind'] != 'metadata':
            lines.append('\t'.join(str(r.get(k, '')).replace('\t', ' ').replace('\n', ' ') for k in ('id', 'kind', 'title', 'publisher', 'source_url', 'path', 'bytes')))
    b.write(pack_dir / 'pack-contents.tsv', ('\n'.join(lines) + '\n').encode('utf-8'), 'generated')
    info = {'name': pack_name, 'size': args.pack, 'map': map_set, 'built': datetime.date.today().isoformat(), 'snapshot': snap.name, 'sample': bool(args.sample), 'add_ons': [],
            'counts': {k: sum(r['kind'] == k for r in records) for k in sorted({r['kind'] for r in records})}, 'known_gaps': SPEC['known_gap_documents'], 'incomplete': len(missing)}
    revision = SIZES.get(args.pack, {}).get('content_revision')
    if revision:
        info['content_revision'] = revision
    b.write(pack_dir / 'pack.json', json.dumps(info, indent=1).encode('utf-8'), 'generated')
    removed = 0
    for root, dirs, files in os.walk(pack_dir, topdown=False):
        for f in files:
            p = Path(root) / f
            rel = p.relative_to(pack_dir).as_posix()
            if rel not in b.placed and rel != 'pack-manifest.tsv':
                p.unlink()
                removed += 1
        if root != str(pack_dir) and not os.listdir(root):
            os.rmdir(root)
    rows = ['path\tbytes\tsha256\thow\tchanges']
    total = 0
    for rel in sorted(b.placed):
        p = pack_dir / rel
        size = p.stat().st_size
        total += size
        rows.append(f'{rel}\t{size}\t{b_hash(b, p)}\t{b.placed[rel][0]}\t{b.placed[rel][1]}')
    (pack_dir / 'pack-manifest.tsv').write_text('\n'.join(rows) + '\n', encoding='utf-8')
    (build_dir / 'hash-cache.json').write_text(json.dumps(b.hashes), encoding='utf-8')
    with open(build_dir / 'left-out.tsv', 'w', encoding='utf-8') as f:
        f.write('id\tpath\twhy\n')
        for row in left_out + missing:
            f.write('\t'.join(row) + '\n')
    with open(build_dir / 'problems.tsv', 'w', encoding='utf-8') as f:
        for row in b.problems:
            f.write('\t'.join(row) + '\n')
    how = Counter(v[0] for v in b.placed.values())
    changes = Counter()
    for v in b.placed.values():
        for part in filter(None, v[1].split(',')):
            k, n = part.split('=')
            changes[k] += int(n)
    result = {'pack': str(pack_dir), 'files': len(b.placed), 'bytes': total, 'how': how, 'records': info['counts'], 'removed_from_previous_build': removed,
              'left_out_records': len(left_out), 'incomplete': len(missing), 'problems': len(b.problems), 'taken_out_of_pages': changes}
    print(json.dumps(result, indent=1))
    if missing:
        print(f'INCOMPLETE: {len(missing)} records could not be placed; see {build_dir}/left-out.tsv', file=sys.stderr)
        return 1
    return 0


def b_hash(b, path):
    st = path.stat()
    key = str(path.relative_to(b.pack))
    known = b.hashes.get(key)
    if known and known[0] == st.st_size and known[1] == st.st_mtime_ns:
        return known[2]
    h = sha256_file(path)
    b.hashes[key] = [st.st_size, st.st_mtime_ns, h]
    return h


def build_search(snap, dest, records, sources_text, b):
    """Rows come from the snapshot, renamed as the catalog is, and nothing is extracted again. A course record's one row is its title. An identical result leaves the old file in place."""
    os.makedirs(dest.parent, exist_ok=True)
    tmp = dest.with_name('search.building')
    if tmp.exists():
        tmp.unlink()
    db = sqlite3.connect(tmp)
    db.execute(f"CREATE VIRTUAL TABLE pages USING {SPEC['search_index']['schema']}")
    src = sqlite3.connect(f'file:{snap}/search.sqlite?immutable=1', uri=True)
    keep = {r['id'] for r in records if r['kind'] != 'metadata'}
    coll = SPEC['collection_renames']
    rows = ((pack_id(d), t, coll.get(c, c), cat, pack_path(p), pg, body) for d, t, c, cat, p, pg, body in src.execute('SELECT doc_id,title,collection,category,path,page,body FROM pages') if pack_id(d) in keep)
    db.executemany('INSERT INTO pages VALUES(?,?,?,?,?,?,?)', rows)
    for r in records:
        if r['id'].startswith('course-') and r['path'].endswith('/index.html'):
            db.execute('INSERT INTO pages VALUES(?,?,?,?,?,?,?)', (r['id'], r['title'], r.get('collection', ''), r.get('category', ''), r['path'], 1, r['title']))
    rec = SOURCES['record']
    db.execute('INSERT INTO pages VALUES(?,?,?,?,?,?,?)', (rec['id'], rec['title'], rec['collection'], rec['category'], SOURCES_REL, 1, sources_text))
    db.execute("INSERT INTO pages(pages) VALUES('optimize')")
    db.commit()
    db.execute('VACUUM')
    db.execute('PRAGMA journal_mode=DELETE')
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise SystemExit('the new search file fails its integrity check')
    db.close()
    src.close()
    need_space(tmp.stat().st_size)
    data_same = dest.exists() and sha256_file(dest) == sha256_file(tmp)
    if data_same:
        tmp.unlink()
    else:
        os.replace(tmp, dest)
    b.placed[dest.relative_to(b.pack).as_posix()] = ('generated', '')


def sources_page(records):
    """Each owner in this pack, what of theirs is in it, and where it came from. Generated from the pack's own catalog; signed permissions never come near it."""
    credits = {o['name']: o for o in json.loads((PROJECT / SPEC['sources']['credits']).read_text(encoding='utf-8'))['owners']}
    owners = defaultdict(list)
    for r in records:
        if r['kind'] in DOC_KINDS | {'video', 'zim'} or (r['kind'] == 'transcript' and not r.get('video_path')):
            owners[r.get('publisher') or 'Unnamed source'].append(r)
    e = html.escape
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Sources and permissions</title>',
             '<style>body{font:17px/1.6 system-ui,sans-serif;max-width:900px;margin:32px auto;padding:0 20px}h2{margin-top:1.6em;font-size:1.15em}li{margin:.15em 0}</style></head><body>',
             '<h1>Sources and permissions</h1>',
             '<p>Everything here was made by the people and organizations listed below. We love to give credit: please visit them. To ask for anything to be removed, write to hello@lastbook.org.</p>',
             '<p>Not clinically reviewed. Check medical guidance against a clinician when you can.</p>',
             f'<h2>Maps</h2><p>{e(MAP_CREDIT)}</p>']
    text = ['Sources and permissions', 'Everything here was made by the people and organizations listed below. To ask for anything to be removed, write to hello@lastbook.org.', 'Maps: ' + MAP_CREDIT]
    gaps = [os.path.basename(p) for p in SPEC['known_gap_documents']]
    parts.append('<h2>Left out of this first release</h2><p>' + e(', '.join(gaps)) + ': too large to open well. They will come in a later release.</p>')
    for name in sorted(owners, key=str.lower):
        rs = sorted(owners[name], key=lambda r: r['title'].lower())
        kinds = Counter('videos' if r['kind'] == 'video' else 'collections' if r['kind'] == 'zim' else 'documents' for r in rs)
        what = ', '.join(f'{n} {k}' for k, n in sorted(kinds.items()))
        owner = credits.get(name) or {}
        site = owner.get('source') or ''
        parts.append(f'<h2>{e(name)}</h2><p>{e(what)}' + (f' · source: {e(site)}' if site else '') + (f" · terms: {e(owner['terms'])}" if owner.get('terms') else '') + '</p><details><summary>What is included</summary><ul>')
        text.append(f'{name}: {what}' + (f', source {site}' if site else ''))
        for r in rs:
            url = r.get('source_url') or ''
            parts.append(f'<li>{e(r["title"])}' + (f' · {e(url)}' if url.startswith(('http://', 'https://')) else '') + '</li>')    # the address as words: a link here could only do nothing
        parts.append('</ul></details>')
    parts.append('</body></html>')
    return ''.join(parts), '\n'.join(text)


# ---------------------------------------------------------------- maps ----
GEO_FILES = ['national', 'map', 'water', 'terrain', 'buildings', 'crops', 'places']


def geo_source():
    """Clones of the map files with their write-ahead logs merged in, so that nothing is lost and the originals are never opened."""
    src = OUT / 'maps' / 'source'
    os.makedirs(src, exist_ok=True)
    for name in GEO_FILES + ['atlas', 'profiles']:
        path = LIB / 'geography/resources/atlas.sqlite' if name == 'atlas' else GEO / f'{name}.sqlite'
        dest = src / f'{name}.sqlite'
        if dest.exists():
            continue
        wal = Path(str(path) + '-wal')
        tmp = src / f'{name}.cloning'
        clone(path, tmp)
        if wal.exists() and wal.stat().st_size:
            clone(wal, Path(str(tmp) + '-wal'))
        db = sqlite3.connect(tmp)
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        db.execute('PRAGMA journal_mode=DELETE')
        db.close()
        os.replace(tmp, dest)
    return src


def state_box(src, code):
    db = sqlite3.connect(f'file:{src}/profiles.sqlite?immutable=1', uri=True)
    blank = {'CT': '09'}.get(code, '-')     # Connecticut's ZIPs carry its 2022 planning-region county codes and no state
    row = db.execute("SELECT min(lon),min(lat),max(lon),max(lat),count(*) FROM zcta WHERE state=? OR (coalesce(state,'')='' AND substr(county,1,2)=?)", (code, blank)).fetchone()
    if not row[4]:
        raise SystemExit(f'no ZIP centres for state {code}')
    return row[0] - .15, row[1] - .15, row[2] + .15, row[3] + .15


def tile_range(box, z):
    n = 2 ** z

    def merc(lat):
        return (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    x0, x1 = int((box[0] + 180) / 360 * n), int((box[2] + 180) / 360 * n)
    return x0, x1, int(merc(box[3])), int(merc(box[1]))


def empty_like(src_db, dest, tables):
    """A new file with these tables' schema and their indexes, journal off the disk."""
    if dest.exists():
        dest.unlink()
    os.makedirs(dest.parent, exist_ok=True)
    db = sqlite3.connect(dest)
    db.execute('PRAGMA journal_mode=DELETE')
    db.execute('PRAGMA synchronous=OFF')
    s = sqlite3.connect(f'file:{src_db}?immutable=1', uri=True)
    for name, sql in s.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL").fetchall():
        if name in tables:
            db.execute(sql)
    for tbl, sql in s.execute("SELECT tbl_name,sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL").fetchall():
        if tbl in tables:
            db.execute(sql)
    s.close()
    db.execute('ATTACH DATABASE ? AS src', (str(src_db),))
    return db


def finish_db(db):
    db.commit()
    db.execute('DETACH DATABASE src')
    db.execute('PRAGMA journal_mode=DELETE')
    db.close()


def base_set(src, dest):
    """No pins, buildings or crops. Roads and water at low zoom, relief to zoom 7, national places."""
    geo = dest / 'derived/geo'
    db = empty_like(src / 'national.sqlite', geo / 'national.sqlite', ['feat', 'feat_idx', 'poi', 'meta'])
    db.execute('INSERT INTO feat SELECT * FROM src.feat WHERE minz<=8')
    db.execute('INSERT INTO feat_idx SELECT i.* FROM feat f CROSS JOIN src.feat_idx i ON i.id=f.id')
    features = db.execute('SELECT count(*) FROM feat').fetchone()[0]
    db.execute("INSERT INTO meta VALUES('status',?)", (json.dumps({'state': 'complete', 'stage': 'ready', 'features': features}),))
    finish_db(db)
    db = empty_like(src / 'water.sqlite', geo / 'water.sqlite', ['resource', 'spatial', 'feat', 'feat_idx', 'meta', 'water_fts'])
    db.execute('INSERT INTO feat SELECT * FROM src.feat WHERE minz<=9')
    db.execute('INSERT INTO feat_idx SELECT i.* FROM feat f CROSS JOIN src.feat_idx i ON i.id=f.id')
    db.execute('INSERT INTO meta SELECT * FROM src.meta')
    finish_db(db)
    db = empty_like(src / 'terrain.sqlite', geo / 'terrain.sqlite', ['tile', 'meta'])
    db.execute('INSERT INTO tile SELECT * FROM src.tile WHERE z<=7')
    db.execute('INSERT INTO meta SELECT key,value FROM src.meta')
    scrub_terrain(db)
    finish_db(db)
    db = empty_like(src / 'places.sqlite', geo / 'places.sqlite', ['place', 'place_fts'])
    db.execute("INSERT INTO place SELECT * FROM src.place WHERE source<>'OpenStreetMap'")
    db.execute("INSERT INTO place_fts(place_fts) VALUES('rebuild')")
    finish_db(db)
    db = empty_like(src / 'map.sqlite', geo / 'map.sqlite', ['feat', 'feat_idx', 'poi', 'meta'])
    finish_db(db)
    (geo / 'national-status.json').write_text(json.dumps({'state': 'complete', 'stage': 'ready', 'features': features}), encoding='utf-8')
    (geo / 'socal-cells.json').write_text('[]', encoding='utf-8')


def scrub_terrain(db):
    db.execute('PRAGMA secure_delete=ON')      # or the old value stays in the page's free space
    db.execute("UPDATE meta SET value=trim(substr(value,1,instr(value||'(','(')-1)) WHERE key='source'")


def state_set(src, dest, code):
    """Base, then every row inside the state's box."""
    base = OUT / 'maps' / 'base'
    if not (base / 'map-set.json').exists():
        build_map('base')
    for f in base.rglob('*'):
        if f.is_file() and f.name != 'map-set.json':
            clone(f, dest / f.relative_to(base))
    box = state_box(src, code)
    w, s, e, n = box
    inbox = 'maxx>=? AND minx<=? AND maxy>=? AND miny<=?'
    args = (w, e, s, n)
    geo = dest / 'derived/geo'

    def attach(name):
        db = sqlite3.connect(geo / f'{name}.sqlite')
        db.execute('PRAGMA synchronous=OFF')
        db.execute('ATTACH DATABASE ? AS src', (str(src / f'{name}.sqlite'),))
        return db
    db = attach('national')
    db.execute(f'INSERT OR IGNORE INTO feat SELECT f.* FROM src.feat_idx i CROSS JOIN src.feat f ON f.id=i.id WHERE {inbox}', args)
    db.execute('DELETE FROM feat_idx')
    db.execute('INSERT INTO feat_idx SELECT i.* FROM feat f CROSS JOIN src.feat_idx i ON i.id=f.id')
    db.execute('INSERT INTO poi SELECT * FROM src.poi WHERE lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?', (w, e, s, n))
    features = db.execute('SELECT count(*) FROM feat').fetchone()[0]
    db.execute("UPDATE meta SET value=? WHERE key='status'", (json.dumps({'state': 'complete', 'stage': 'ready', 'features': features}),))
    finish_db(db)
    (geo / 'national-status.json').write_text(json.dumps({'state': 'complete', 'stage': 'ready', 'features': features}), encoding='utf-8')
    db = attach('water')
    db.execute(f'INSERT OR IGNORE INTO feat SELECT f.* FROM src.feat_idx i CROSS JOIN src.feat f ON f.id=i.id WHERE {inbox}', args)
    db.execute('DELETE FROM feat_idx')
    db.execute('INSERT INTO feat_idx SELECT i.* FROM feat f CROSS JOIN src.feat_idx i ON i.id=f.id')
    db.execute(f'INSERT INTO resource SELECT r.* FROM src.spatial i CROSS JOIN src.resource r ON r.id=i.id WHERE {inbox}', args)
    db.execute(f'INSERT INTO spatial SELECT * FROM src.spatial WHERE {inbox}', args)
    db.execute('INSERT INTO water_fts(rowid,name,other) SELECT f.rowid,f.name,f.other FROM resource r CROSS JOIN src.water_fts f ON f.rowid=r.id')
    finish_db(db)
    db = attach('terrain')
    for z in range(8, 13):
        x0, x1, y0, y1 = tile_range(box, z)
        db.execute('INSERT OR IGNORE INTO tile SELECT * FROM src.tile WHERE z=? AND x BETWEEN ? AND ? AND y BETWEEN ? AND ?', (z, x0, x1, y0, y1))
    finish_db(db)
    db = attach('places')
    db.execute("INSERT INTO place SELECT * FROM src.place WHERE source='OpenStreetMap' AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?", (w, e, s, n))
    db.execute("INSERT INTO place_fts(place_fts) VALUES('rebuild')")
    finish_db(db)
    db = empty_like(src / 'buildings.sqlite', geo / 'buildings.sqlite', ['bldg', 'bldg_socal', 'bldg_osm', 'meta'])
    x0, x1, y0, y1 = tile_range(box, 14)
    for t in ('bldg', 'bldg_socal', 'bldg_osm'):     # the spec names two; the map also reads bldg_socal, and without it Southern California has no footprints
        db.execute(f'INSERT INTO {t} SELECT * FROM src.{t} WHERE x BETWEEN ? AND ? AND y BETWEEN ? AND ?', (x0, x1, y0, y1))
    db.execute('INSERT INTO meta SELECT * FROM src.meta')
    finish_db(db)
    db = empty_like(src / 'crops.sqlite', geo / 'crops.sqlite', ['meta', 'resource', 'spatial', 'tile'])
    db.execute(f'INSERT INTO resource SELECT r.* FROM src.spatial i CROSS JOIN src.resource r ON r.id=i.id WHERE {inbox}', args)
    db.execute(f'INSERT INTO spatial SELECT * FROM src.spatial WHERE {inbox}', args)
    for (z,) in db.execute('SELECT DISTINCT z FROM src.tile').fetchall():
        x0, x1, y0, y1 = tile_range(box, z)
        db.execute('INSERT INTO tile SELECT * FROM src.tile WHERE z=? AND x BETWEEN ? AND ? AND y BETWEEN ? AND ?', (z, x0, x1, y0, y1))
    db.execute('INSERT INTO meta SELECT * FROM src.meta')
    finish_db(db)
    db = empty_like(src / 'atlas.sqlite', dest / 'library-data/geography/resources/atlas.sqlite', ['resource', 'spatial', 'metadata'])
    db.execute(f'INSERT INTO resource SELECT r.* FROM src.spatial i CROSS JOIN src.resource r ON r.id=i.id WHERE {inbox}', args)
    db.execute(f'INSERT INTO spatial SELECT * FROM src.spatial WHERE {inbox}', args)
    db.execute('INSERT INTO metadata SELECT * FROM src.metadata')
    finish_db(db)


def us_set(src, dest):
    """Clones of the whole files, with the private notes in them cleaned out. Changing a few pages of a clone costs only those pages."""
    geo = dest / 'derived/geo'
    for name in GEO_FILES:
        clone(src / f'{name}.sqlite', geo / f'{name}.sqlite')
    clone(src / 'atlas.sqlite', dest / 'library-data/geography/resources/atlas.sqlite')
    status = json.loads((GEO / 'national-status.json').read_text(encoding='utf-8'))
    status = {k: status[k] for k in ('state', 'stage', 'features')}
    db = sqlite3.connect(geo / 'national.sqlite')
    db.execute('PRAGMA secure_delete=ON')      # the old status holds a full local path, which would stay in the page's free space
    db.execute("UPDATE meta SET value=? WHERE key='status'", (json.dumps(status),))
    db.commit()
    db.close()
    db = sqlite3.connect(geo / 'buildings.sqlite')
    db.execute('PRAGMA secure_delete=ON')
    db.execute('DROP TABLE done')
    db.execute('DROP TABLE done_socal')
    db.commit()
    db.close()
    db = sqlite3.connect(geo / 'terrain.sqlite')
    scrub_terrain(db)
    db.commit()
    db.close()
    (geo / 'national-status.json').write_text(json.dumps(status), encoding='utf-8')
    clone(GEO / 'socal-cells.json', geo / 'socal-cells.json')


def build_map(name):
    src = geo_source()
    dest = OUT / 'maps' / name
    start = time.time()
    if dest.exists():
        shutil.rmtree(dest)
    os.makedirs(dest)
    if name == 'base':
        base_set(src, dest)
    elif name == 'us':
        us_set(src, dest)
    elif re.fullmatch(r'[A-Z]{2}', name):
        state_set(src, dest, name)
    else:
        raise SystemExit(f'unknown map set {name}')
    size = {f.relative_to(dest).as_posix(): f.stat().st_size for f in dest.rglob('*') if f.is_file()}
    (dest / 'map-set.json').write_text(json.dumps({'name': name, 'files': size, 'bytes': sum(size.values()), 'seconds': round(time.time() - start)}, indent=1), encoding='utf-8')
    print(json.dumps({'map': name, 'bytes': sum(size.values()), 'files': size, 'seconds': round(time.time() - start)}, indent=1))


def main():
    if 'iframe' not in HTMLParser.CDATA_CONTENT_ELEMENTS:
        raise SystemExit('this Python reads HTML differently from a browser; use derived/app-build/mac-venv/bin/python3')
    os.nice(10)
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('snapshot')
    m = sub.add_parser('map')
    m.add_argument('name')
    p = sub.add_parser('build')
    p.add_argument('pack')
    p.add_argument('--map')
    p.add_argument('--sample', type=int, default=0)
    p.add_argument('--snapshot')
    p.add_argument('--fetched')
    args = ap.parse_args()
    if args.cmd == 'snapshot':
        take_snapshot()
    elif args.cmd == 'map':
        build_map(args.name)
    else:
        return build_pack(args)
    return 0


if __name__ == '__main__':
    sys.exit(main())
