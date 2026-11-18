"""Checks on finished packs that the pack scan does not do. Not in any pack. Reads packs, the frozen snapshot and, read-only, the source files of the forbidden items.

  python -I -B verify_packs.py absent  <snapshot dir> <out.json> <pack> ...   the two skipped PDFs, the 28 flagged videos, the files the owner had left out (the NIST form PDFs, the
                                                                             land-data pages), sign-in and cookie files: by name and by content hash
  python -I -B verify_packs.py guides  <snapshot dir> <pack> ...              every card of the guides as an app build ships them has its record in the pack, by the ID the card uses
  python -I -B verify_packs.py binary  <out.tsv> <pack> ...                   every byte of every unique file, private words by line number of the terms file
  python -I -B verify_packs.py sizes   <out.json> <pack> ...                  size by kind, clones, what stripping took out

Private words are never printed. Sign-in and cookie files are never opened: only their names are looked for, and the cookie file's standard first line in the packs.
"""
import ctypes
import fnmatch
import hashlib
import importlib.util
import json
import multiprocessing
import os
import sqlite3
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
LIB = PROJECT / 'library-data'
OUT = PROJECT / 'derived/packs'
SPEC = json.loads((OUT / 'pack-spec.json').read_text(encoding='utf-8'))
NEVER = ('video-auth*', '*cookie*')
EXTRA = [b'# netscape http cookie file', b'.youtube.com\ttrue\t/']     # what any exported cookie file holds


def walk(pack):
    for root, dirs, files in os.walk(pack):
        for f in files:
            yield Path(root) / f


def manifest(pack):
    out = {}
    with open(Path(pack) / 'pack-manifest.tsv', encoding='utf-8', errors='surrogateescape') as f:
        next(f)
        for line in f:
            a = line.rstrip('\n').split('\t')
            out[a[0]] = a
    return out


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(1 << 22):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- absent ----
def absent(snap, out_path, packs):
    catalog = json.loads((Path(snap) / 'catalog.json').read_text(encoding='utf-8'))
    remote = json.loads((Path(snap) / 'remote-videos.json').read_text(encoding='utf-8'))
    by_id = {r['id']: r for r in catalog}
    flagged = SPEC['video_filters']['flagged_28']['ids']
    items = []       # (group, label, path)
    for p in SPEC['known_gap_documents']:
        rec = next(r for r in catalog if r['path'] == p)
        items.append({'group': 'skipped pdf', 'id': rec['id'], 'path': p, 'catalog_sha256': rec.get('sha256')})
    by_path = {r['path']: r for r in catalog}
    for p in SPEC['pdf_one_offs']['leave_out'] + SPEC['leave_out_one_file'] + [r['path'] for r in catalog if r['id'].startswith(tuple(SPEC['leave_out_id_prefixes']))]:
        rec = by_path.get(p) or {}
        items.append({'group': 'left out by the owner', 'id': rec.get('id', ''), 'path': p, 'catalog_sha256': rec.get('sha256')})
    for i in flagged:
        r = by_id[i]
        items.append({'group': 'flagged video', 'id': i, 'path': r['path'], 'catalog_sha256': r.get('sha256')})
        for o in catalog:     # what comes with a video
            if o.get('video_path') == r['path'] and o['id'] != i:
                for k in ('path', 'vtt_path', 'text_path', 'thumbnail_path'):
                    if o.get(k) and not any(x['path'] == o[k] for x in items):
                        items.append({'group': 'flagged video, its other files', 'id': i, 'path': o[k], 'catalog_sha256': o.get('sha256') if k == 'path' else None})
        for k in ('text_path', 'vtt_path', 'thumbnail_path'):
            if r.get(k) and not any(x['path'] == r[k] for x in items):
                items.append({'group': 'flagged video, its other files', 'id': i, 'path': r[k], 'catalog_sha256': None})
        for c in r.get('captions') or []:
            items.append({'group': 'flagged video, its other files', 'id': i, 'path': c, 'catalog_sha256': None})
    for it in items:
        src = LIB / it['path']
        it['where'] = 'on a Studio' if it['path'] in remote else 'here' if src.is_file() else 'not found'
        it['source_sha256'] = sha(src) if it['where'] == 'here' and not any(fnmatch.fnmatchcase(src.name.lower(), p) for p in NEVER) else None
        it['catalog_matches_source'] = None if not (it['source_sha256'] and it['catalog_sha256']) else it['source_sha256'] == it['catalog_sha256']
    hashes = {h: it for it in items for h in (it['source_sha256'], it['catalog_sha256']) if h}
    paths = {it['path'] for it in items}
    ids = {i.removeprefix('video-') for i in flagged}
    result = {'items': len(items), 'hashes_compared': len(hashes), 'by_group': Counter(i['group'] for i in items), 'by_where': Counter(f"{i['group']}: {i['where']}" for i in items),
              'catalog_differs_from_source': [i['path'] for i in items if i['catalog_matches_source'] is False],
              'no_hash_at_all': [i['path'] for i in items if not i['source_sha256'] and not i['catalog_sha256']], 'packs': {}}
    for pack in packs:
        pack = Path(pack)
        m = manifest(pack)
        disk = {p.relative_to(pack).as_posix() for p in walk(pack)}
        found = defaultdict(list)
        for rel in disk:
            base = rel.split('/')[-1].lower()
            if any(fnmatch.fnmatchcase(base, p) for p in NEVER):
                found['sign-in or cookie name'].append(rel)
            if rel.removeprefix('library-data/') in paths:
                found['forbidden path'].append(rel)
            if any(i in rel for i in ids):
                found['flagged id in a file name'].append(rel)
        for rel, a in m.items():
            if a[2] in hashes:
                found['forbidden content hash (manifest)'].append(rel)
        # the manifest is the builder's; hash what is on disk for anything that could be one of these by size
        sizes = {(LIB / it['path']).stat().st_size for it in items if it['where'] == 'here'} | {by_id[i]['bytes'] for i in flagged} | {r['bytes'] for r in catalog if r['path'] in SPEC['known_gap_documents']}
        left_ids = {it['id'] for it in items if it['group'] == 'left out by the owner' and it['id']}
        rehashed = 0
        for rel in disk:
            if (pack / rel).stat().st_size in sizes:
                rehashed += 1
                if sha(pack / rel) in hashes:
                    found['forbidden content hash (on disk)'].append(rel)
        cat = json.loads((pack / 'library-data/catalog.json').read_text(encoding='utf-8'))
        for r in cat:
            if r['id'] in flagged or r.get('video_id') in flagged or r.get('path') in paths or r.get('video_path') in paths or r['id'] in left_ids:
                found['record in catalog.json'].append(r['id'])
        db = sqlite3.connect(f"file:{pack}/library-data/search.sqlite?immutable=1", uri=True)
        for (d, p) in db.execute('SELECT DISTINCT doc_id,path FROM pages'):
            if d in flagged or p in paths or d in left_ids:
                found['row in search.sqlite'].append(d)
        db.close()
        contents = (pack / 'pack-contents.tsv').read_text(encoding='utf-8', errors='replace')
        for i in flagged:
            if i in contents:
                found['in pack-contents.tsv'].append(i)
        result['packs'][pack.name] = {'files_on_disk': len(disk), 'rehashed_by_size': rehashed, 'found': dict(found), 'clean': not found}
    result['detail'] = items
    Path(out_path).write_text(json.dumps(result, indent=1, default=dict), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'detail'}, indent=1, default=dict))
    return 0 if all(p['clean'] for p in result['packs'].values()) else 1


# ---------------------------------------------------------------- guides ----
def guides(snap, packs):
    """The guides an app build ships (ship_app_files.py) against each pack's catalog. A card whose record is not there is shown by the app as nothing: fine for a video the
    rules leave out or a known gap, a fault for anything else. Encyclopedia items are listed: the app hides those whose file a pack lacks."""
    spec = importlib.util.spec_from_file_location('ship_app_files', PROJECT / 'packaging/ship_app_files.py')
    ship = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ship)
    docs, zims = set(), set()

    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get('doc'), str):
                docs.add(o['doc'])
            if isinstance(o.get('zim'), str):
                zims.add(o['zim'].split('/')[1])
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    for f in sorted((PROJECT / 'content/topics').glob('*.json')) + sorted((PROJECT / 'content/regions').glob('*.json')):
        walk(ship.clean(json.loads(f.read_text(encoding='utf-8'))))   # a region file's readings are guide readings too
    snapshot = {ship.new_id(r['id']): r for r in json.loads((Path(snap) / 'catalog.json').read_text(encoding='utf-8'))}
    gone = set(SPEC['known_gap_documents'])
    bad = 0
    for pack in packs:
        pack = Path(pack)
        ids = {r['id'] for r in json.loads((pack / 'library-data/catalog.json').read_text(encoding='utf-8'))}
        held_zims = {p.name for p in (pack / 'library-data/collections').glob('*.zim')}
        why = defaultdict(list)
        for d in sorted(docs - ids):
            r = snapshot.get(d)
            why['not in the snapshot' if r is None else 'known gap' if r['path'] in gone else 'video the rules leave out' if r['kind'] == 'video' else 'MISSING'].append(d)
        bad += len(why['MISSING'])
        print(json.dumps({'pack': pack.name, 'cards': len(docs), 'in_pack': len(docs & ids), 'absent': {k: (len(v), v[:8]) for k, v in why.items()},
                          'encyclopedia_files_the_app_hides': sorted(zims - held_zims)}, indent=1))
    return 1 if bad else 0


# ---------------------------------------------------------------- binary ----
def terms():
    lines = (OUT / 'private-terms.txt').read_text(encoding='utf-8').splitlines()
    return [(n + 1, t.strip().lower().encode()) for n, t in enumerate(lines) if t.strip() and not t.startswith('#')]


def scan_file(job):
    path, size = job
    os.nice(15)
    words = terms() + [(f'cookie{n + 1}', w) for n, w in enumerate(EXTRA)]
    over = max(len(w) for _, w in words) - 1
    hits = {}
    with open(path, 'rb') as f:
        tail, pos = b'', 0
        while chunk := f.read(1 << 24):
            data = tail + chunk.lower()
            start = pos - len(tail)
            for line, w in words:
                i = data.find(w)
                while i >= 0:
                    # a match wholly inside the overlap was counted last time
                    if i + len(w) > len(tail):
                        n, first = hits.get(line, (0, start + i))
                        hits[line] = (n + 1, first)
                    i = data.find(w, i + 1)
            pos += len(chunk)
            tail = data[-over:] if over else b''
    return path, size, hits


def binary(out_path, packs):
    unique = {}
    where = defaultdict(list)
    for pack in packs:
        pack = Path(pack)
        for rel, a in manifest(pack).items():
            where[a[2]].append(f'{pack.name}:{rel}')
            unique.setdefault(a[2], str(pack / rel))
        for name in ('pack-manifest.tsv',):
            p = pack / name
            h = sha(p)
            where[h].append(f'{pack.name}:{name}')
            unique.setdefault(h, str(p))
    by_path = {p: h for h, p in unique.items()}
    jobs = sorted(((p, os.path.getsize(p)) for p in by_path), key=lambda x: -x[1])
    total = sum(s for _, s in jobs)
    print(f'{len(jobs)} unique files, {total} bytes', flush=True)
    done = 0
    rows = []
    with multiprocessing.get_context('spawn').Pool(6) as pool:
        for path, size, hits in pool.imap_unordered(scan_file, jobs, chunksize=1):
            done += size
            for line, (n, first) in hits.items():
                rows.append((path, size, line, n, first))
            if len(rows) % 50 == 0:
                print(f'{done}/{total} bytes, {len(rows)} file-word hits', flush=True)
    with open(out_path, 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('kind\tterm_line\tmatches\tfirst_offset\tbytes\tall_places\n')
        for path, size, line, n, first in sorted(rows, key=lambda r: (str(r[2]), r[0])):
            f.write(f"{kind_of(where[by_path[path]][0].split(':', 1)[1])}\t{line}\t{n}\t{first}\t{size}\t{' '.join(where[by_path[path]])}\n")
    print(f'done: {len(jobs)} files, {total} bytes read, {len(rows)} file-word hits')
    by = Counter()
    for path, size, line, n, first in rows:
        by[(kind_of(where[by_path[path]][0].split(':', 1)[1]), str(line))] += n
    for k, v in sorted(by.items()):
        print(k, v)
    return 0


# ---------------------------------------------------------------- sizes ----
_libc = ctypes.CDLL(None, use_errno=True)


def private_size(path):
    """APFS: the bytes of this file not shared with any other file (ATTR_CMNEXT_PRIVATESIZE)."""
    attrs = struct.pack('=HHIIIII', 5, 0, 0, 0, 0, 0, 0x8)
    buf = ctypes.create_string_buffer(64)
    r = _libc.getattrlist(os.fsencode(path), attrs, buf, 64, 0x20)
    if r != 0:
        raise OSError(ctypes.get_errno(), 'getattrlist')
    length, value = struct.unpack('=Iq', buf.raw[:12])
    return value


def kind_of(rel):
    if rel in ('pack.json', 'pack-manifest.tsv', 'pack-contents.tsv'):
        return 'pack files'
    if rel.startswith('derived/geo/') or rel.endswith('geography/resources/atlas.sqlite'):
        return 'maps'
    parts = rel.split('/')
    if parts[0] != 'library-data':
        return 'other'
    top = parts[1]
    if len(parts) == 2:
        return 'search' if top == 'search.sqlite' else 'catalog and summary'
    if top in ('videos', 'videos-external'):
        return 'video'
    return {'documents': 'documents', 'pages': 'saved pages', 'courses': 'courses', 'assets': 'page images (assets)', 'collections': 'ZIM', 'transcripts': 'transcripts',
            'text': 'text', 'metadata': 'thumbnails'}.get(top, 'other')


def sizes(out_path, packs):
    result = {}
    for pack in packs:
        pack = Path(pack)
        m = manifest(pack)
        kinds = defaultdict(lambda: [0, 0])
        how = defaultdict(lambda: [0, 0])
        taken = Counter()
        stripped_files = changed_before = 0
        priv = 0
        vectors = []
        for p in walk(pack):
            rel = p.relative_to(pack).as_posix()
            st = p.stat()
            k = kinds[kind_of(rel)]
            k[0] += 1
            k[1] += st.st_size
            priv += private_size(p)
            if 'semantic' in rel or rel.endswith(('.onnx', '.npy', '.gguf', '.safetensors')) or '/derived/index' in rel:
                vectors.append(rel)
            a = m.get(rel)
            h = how['pack-manifest' if a is None else a[3]]
            h[0] += 1
            h[1] += st.st_size
            if a and a[3] == 'stripped':
                stripped_files += 1
                for part in filter(None, a[4].split(',')):
                    n, v = part.split('=')
                    taken[n] += int(v)
        scripts = sum(v for k, v in taken.items() if k in ('element_script',))
        remote = sum(v for k, v in taken.items() if k.startswith(('remote_', 'link_out', 'css_remote', 'css_import', 'element_link', 'element_iframe', 'element_frame', 'element_source', 'data_attribute', 'base_href', 'attr_', 'animated_url')))
        result[pack.name] = {'files': sum(v[0] for v in kinds.values()), 'bytes': sum(v[1] for v in kinds.values()), 'by_kind': {k: {'files': v[0], 'bytes': v[1]} for k, v in sorted(kinds.items())},
                             'by_how': {k: {'files': v[0], 'bytes': v[1]} for k, v in sorted(how.items())}, 'private_bytes_not_shared_with_any_file': priv, 'files_stripped': stripped_files,
                             'scripts_removed': scripts, 'remote_references_removed': remote, 'taken_out': dict(sorted(taken.items())), 'vector_or_model_files': vectors}
        print(pack.name, result[pack.name]['files'], result[pack.name]['bytes'], flush=True)
    Path(out_path).write_text(json.dumps(result, indent=1), encoding='utf-8')
    return 0


if __name__ == '__main__':
    os.nice(10)
    cmd = sys.argv[1]
    if cmd == 'absent':
        sys.exit(absent(sys.argv[2], sys.argv[3], sys.argv[4:]))
    if cmd == 'guides':
        sys.exit(guides(sys.argv[2], sys.argv[3:]))
    if cmd == 'binary':
        sys.exit(binary(sys.argv[2], sys.argv[3:]))
    if cmd == 'sizes':
        sys.exit(sizes(sys.argv[2], sys.argv[3:]))
