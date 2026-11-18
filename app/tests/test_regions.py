"""Regional coverage prototype (docs/REGIONAL-COVERAGE.md, Prototype): one guide slot, growing season, across coastal Southern
California (the app's own region file) and the Lehigh Valley and the Poconos (a test region pack whose documents are
stand-ins, app/tests/fixtures/region-US-PA).

It checks that installing a region pack adds exactly its documents and removing it takes exactly them out; that a pack
holding anything it should not is refused before the library is touched; that the Grow guide shows the chosen region's
readings first, keeps other places' readings apart, and falls back to general readings when the region has none here;
and that Chat's search prefers the reader's region (or the place a question names) and labels every regional source.

Runs on a tiny library made in a temporary folder (tools/region_pack.py test-library). Never packaged."""
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
from engine import regions  # noqa: E402
import region_pack  # noqa: E402

FIXTURE = ROOT / 'app' / 'tests' / 'fixtures' / 'region-US-PA'
PA_IDS = {'region-us-pa-planting-guide', 'region-us-pa-cool-warm'}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.data = self.tmp / 'LastbookData'
        region_pack.test_library(self.data)
        self.lib = self.data / 'library-data'
        self.pack = regions.build(FIXTURE, self.tmp / 'Lastbook-Region-US-PA.zip')

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def catalog_ids(self):
        return {r['id'] for r in json.loads((self.lib / 'catalog.json').read_text(encoding='utf-8'))}

    def search_ids(self):
        con = sqlite3.connect(self.lib / 'search.sqlite')
        try:
            return {r[0] for r in con.execute('SELECT doc_id FROM pages')}
        finally:
            con.close()


class Codes(unittest.TestCase):
    def test_chain_runs_from_local_to_country(self):
        self.assertEqual(regions.chain('US-PA-lehigh-pocono'), ['US-PA-lehigh-pocono', 'US-PA', 'US'])
        self.assertEqual(regions.chain('US-PA'), ['US-PA', 'US'])
        self.assertEqual(regions.chain('CA-ON'), ['CA-ON', 'CA'])
        self.assertEqual(regions.chain('US'), ['US'])
        for bad in ('', 'us-pa', 'US_PA', '../US', 'US-PA-Lehigh', None, 7):
            self.assertEqual(regions.chain(bad), [], bad)

    def test_names(self):
        defs = regions.load_definitions(ROOT / 'content' / 'regions', FIXTURE / 'regions')
        self.assertEqual(regions.name('US-PA-lehigh-pocono', defs), 'Lehigh Valley and the Poconos')
        self.assertEqual(regions.name('US-OH', {}), 'Ohio')
        self.assertEqual(regions.name('US-PA-lehigh-pocono', {}), 'Pennsylvania (lehigh pocono area)')

    def test_places_named_in_a_question(self):
        defs = regions.load_definitions(ROOT / 'content' / 'regions', FIXTURE / 'regions')
        for q in ('When can I plant tomatoes in Easton?', 'frost dates for stroudsburg', 'Jim Thorpe garden', 'the Poconos', 'East Stroudsburg PA'):
            self.assertIn('US-PA-lehigh-pocono', regions.named_in(q, defs), q)
        self.assertEqual(regions.named_in('planting in Long Beach', defs), ['US-CA-socal-coast'])
        for q in ('eastern Europe', 'eastonia', 'When can I plant tomatoes?'):
            self.assertEqual(regions.named_in(q, defs), [], q)


class RegionFiles(unittest.TestCase):
    def test_every_region_file_and_slot_is_well_formed(self):
        files = sorted((ROOT / 'content' / 'regions').glob('*.json')) + sorted((FIXTURE / 'regions').glob('*.json'))
        self.assertTrue(files)
        for f in files:
            d = json.loads(f.read_text(encoding='utf-8'))
            self.assertEqual(d['id'], f.stem, f)
            self.assertTrue(regions.valid(d['id']), f)
            self.assertTrue(d.get('name'), f)
            for slot, part in d.get('slots', {}).items():
                self.assertIn(slot, regions.SLOTS, f)
                for i in part.get('read', []):
                    self.assertTrue(i.get('doc') and i.get('label'), (f, i))
                self.assertIsInstance(part.get('missing', []), list, f)

    def test_guides_name_only_known_slots(self):
        for f in sorted((ROOT / 'content' / 'topics').glob('*.json')):
            t = json.loads(f.read_text(encoding='utf-8'))
            for c in t.get('choices', []) + t.get('route', []):
                if 'slot' in c:
                    self.assertIn(c['slot'], regions.SLOTS, f)

    def test_grow_no_longer_names_a_place_for_planting_times(self):
        g = json.loads((ROOT / 'content' / 'topics' / 'grow.json').read_text(encoding='utf-8'))
        c = next(c for c in g['choices'] if c['id'] == 'when-to-plant')
        self.assertEqual(c['slot'], 'growing-season')
        self.assertNotIn('California', c['label'] + c['hint'])
        self.assertFalse([i for i in c['read'] if i['doc'].startswith('coastal-growing-')])
        ca = json.loads((ROOT / 'content' / 'regions' / 'US-CA-socal-coast.json').read_text(encoding='utf-8'))
        self.assertEqual({i['doc'] for i in ca['slots']['growing-season']['read']},
                         {'coastal-growing-6c1e6263ed3907b61d56', 'coastal-growing-2bf5bc7fde86306a742c', 'coastal-growing-7e742f7e7ec7d1b6ee65'})


class Packs(Base):
    def test_install_adds_exactly_the_pack_and_remove_takes_it_out(self):
        before_cat = (self.lib / 'catalog.json').read_text(encoding='utf-8')
        before_ids, before_rows = self.catalog_ids(), self.search_ids()
        s = regions.install(self.data, self.pack)
        self.assertEqual(s['region'], 'US-PA')
        self.assertEqual(self.catalog_ids(), before_ids | PA_IDS)
        self.assertEqual(self.search_ids(), before_rows | PA_IDS)
        for rel in s['pack']['files'] + s['pack']['regions']:
            self.assertTrue((self.lib / rel).is_file(), rel)
        self.assertEqual(regions.remove(self.data), 'US-PA')
        self.assertEqual(json.loads((self.lib / 'catalog.json').read_text(encoding='utf-8')), json.loads(before_cat))
        self.assertEqual(self.search_ids(), before_rows)
        self.assertFalse((self.lib / 'documents' / 'regions').exists())
        self.assertFalse(list((self.lib / 'regions').glob('*.json')))
        self.assertIsNone(regions.remove(self.data))

    def test_choosing_an_area_keeps_it_and_another_pack_replaces_the_first(self):
        regions.select(self.data, 'US-PA-lehigh-pocono')
        regions.install(self.data, self.pack)
        self.assertEqual(regions.chosen(self.data), 'US-PA-lehigh-pocono')   # the pack's state already: the area stays
        s = regions.install(self.data, self.ny_pack())
        self.assertEqual(s['region'], 'US-NY')
        self.assertEqual(s['pack']['region'], 'US-NY')
        self.assertEqual(self.catalog_ids() & (PA_IDS | {'region-us-ny-a'}), {'region-us-ny-a'})

    def ny_pack(self):
        """A one-document pack for another state."""
        ny = self.tmp / 'ny'
        (ny / 'documents' / 'regions' / 'us-ny').mkdir(parents=True)
        (ny / 'documents' / 'regions' / 'us-ny' / 'a.html').write_text('<p>test</p>', encoding='utf-8')
        (ny / 'region-pack.json').write_text(json.dumps({'region': 'US-NY'}), encoding='utf-8')
        (ny / 'catalog.json').write_text(json.dumps([{'id': 'region-us-ny-a', 'kind': 'html', 'title': 'NY test', 'path': 'documents/regions/us-ny/a.html', 'region': 'US-NY'}]), encoding='utf-8')
        return regions.build(ny, self.tmp / 'ny.zip')

    def snapshot(self, sqlite=True):
        """Everything an install changes: the catalog, the search rows, region-set.json and every file in the library."""
        files = {str(f.relative_to(self.data)): f.read_bytes() for f in sorted(self.data.rglob('*'))
                 if f.is_file() and (sqlite or f.suffix != '.sqlite')}
        return (self.catalog_ids(), self.search_ids(), files)

    def test_a_failed_write_leaves_the_pack_that_was_there(self):
        regions.install(self.data, self.ny_pack())
        before = self.snapshot()
        real, calls = Path.write_bytes, []

        def second_fails(path, blob):
            calls.append(path)
            if len(calls) == 2:
                raise OSError(28, 'No space left on device')
            return real(path, blob)

        with mock.patch.object(Path, 'write_bytes', second_fails):
            with self.assertRaises(OSError):
                regions.install(self.data, self.pack)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.snapshot(), before)   # no stray files, and no empty us-pa folder
        self.assertEqual(regions.read_set(self.data)['pack']['region'], 'US-NY')
        self.assertEqual(regions.remove(self.data), 'US-NY')   # still tracked, so it can still be taken out

    def test_a_failure_after_the_files_are_in_place_puts_everything_back(self):
        regions.install(self.data, self.pack)
        regions.select(self.data, 'US-PA-lehigh-pocono')
        before = self.snapshot()
        real = regions._write_json

        def set_file_fails(path, obj):
            if Path(path).name == regions.SET_FILE:
                raise OSError(5, 'Input/output error')
            return real(path, obj)

        with mock.patch.object(regions, '_write_json', set_file_fails):
            with self.assertRaises(OSError):
                regions.install(self.data, self.ny_pack())
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(regions.chosen(self.data), 'US-PA-lehigh-pocono')

    def test_installing_the_same_pack_again_replaces_it(self):
        regions.install(self.data, self.pack)
        before = self.snapshot(sqlite=False), self.row_count()
        regions.install(self.data, self.pack)
        self.assertEqual((self.snapshot(sqlite=False), self.row_count()), before)   # the search file's bytes may move; its rows don't

    def row_count(self):
        con = sqlite3.connect(self.lib / 'search.sqlite')
        try:
            return con.execute('SELECT COUNT(*) FROM pages').fetchone()[0]
        finally:
            con.close()

    def bad_pack(self, change):
        """The test pack, with one change made to its zip."""
        with zipfile.ZipFile(self.pack) as z:
            members = {i.filename: z.read(i.filename) for i in z.infolist()}
        change(members)
        out = self.tmp / 'bad.zip'
        with zipfile.ZipFile(out, 'w') as z:
            for k, v in members.items():
                z.writestr(k, v)
        return out

    def test_a_bad_pack_is_refused_before_the_library_is_touched(self):
        cases = {
            'a path out of the pack': lambda m: m.__setitem__('../evil.txt', b'x'),
            'a file outside its own folders': lambda m: m.__setitem__('library-data/catalog.json', b'[]'),
            'another state’s documents': lambda m: m.__setitem__('documents/regions/us-ny/x.html', b'x'),
            'a changed document': lambda m: m.__setitem__('documents/regions/us-pa/planting-guide-stand-in.html', b'changed'),
            'an id from outside the pack': lambda m: m.__setitem__('catalog.json', m['catalog.json'].replace(b'"region-us-pa-cool-warm"', b'"med-2ca695c9db63d020"')),
            'a search row for nothing': lambda m: m.__setitem__('search.json', json.dumps([{'doc_id': 'other', 'body': 'x'}]).encode()),
            'a pack for a whole country': lambda m: m.__setitem__('region-pack.json', b'{"region": "US"}'),
        }
        before = ((self.lib / 'catalog.json').read_bytes(), self.search_ids())
        for why, change in cases.items():
            with self.subTest(why):
                with self.assertRaises(regions.Refused):
                    regions.install(self.data, self.bad_pack(change))
                self.assertEqual(((self.lib / 'catalog.json').read_bytes(), self.search_ids()), before)
                self.assertFalse((self.data / regions.SET_FILE).exists())

    def test_a_region_code_is_checked_when_chosen(self):
        for bad in ('pennsylvania', '../US-PA', 'US-PA-'):
            with self.assertRaises(regions.Refused):
                regions.select(self.data, bad)
        self.assertEqual(regions.select(self.data, None)['region'], None)


class InTheApp(Base):
    """What the Grow guide and Chat get, from server.py and assistant.py, pointed at the test library."""

    def setUp(self):
        super().setUp()
        import server
        import assistant
        self.server, self.assistant = server, assistant
        self.patches = [mock.patch.object(server, 'HERE', self.data), mock.patch.object(server, 'LIB', self.lib),
                        mock.patch.object(server.library, 'DB', self.lib / 'search.sqlite'),
                        mock.patch.object(assistant, 'S', server), mock.patch.object(assistant, 'INDEX', None)]
        for p in self.patches:
            p.start()
        server._cat['mtime'] = 0

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.server._cat['mtime'] = 0
        super().tearDown()

    def when_to_plant(self):
        self.server._cat['mtime'] = 0
        g = next(t for t in self.server.topics_resolved() if t['id'] == 'grow')
        c = next(c for c in g['choices'] if c['id'] == 'when-to-plant')
        step = next(s for s in g['route'] if s.get('slot') == 'growing-season')
        self.assertEqual([i.get('place') for i in c['read']], [i.get('place') for i in step['read']])
        return c

    def sources(self, q):
        self.server._cat['mtime'] = 0
        return [(r['doc_id'], r.get('region'), r.get('regionFit')) for r in self.assistant.hybrid(q, 8)]

    def test_no_region_chosen(self):
        c = self.when_to_plant()
        self.assertEqual([i['doc'] for i in c['read']], ['med-2ca695c9db63d020'])
        self.assertIn('choose where this library is used', c['regionNote'])
        self.assertEqual({i['place'] for i in c['elsewhere']}, {'Coastal Southern California'})

    def test_lehigh_valley_and_the_poconos(self):
        regions.install(self.data, self.pack)
        regions.select(self.data, 'US-PA-lehigh-pocono')
        c = self.when_to_plant()
        self.assertEqual([(i['doc'], i.get('place')) for i in c['read']],
                         [('region-us-pa-planting-guide', 'Lehigh Valley and the Poconos'), ('region-us-pa-cool-warm', 'Pennsylvania'),
                          ('med-2ca695c9db63d020', None)])
        self.assertEqual(c['regionNote'], 'No planting chart written for Lehigh, Northampton or Monroe County.')
        self.assertTrue(all(i['place'] == 'Coastal Southern California' for i in c['elsewhere']))
        got = self.sources('When can I plant tomatoes?')
        self.assertEqual(got[0], ('region-us-pa-planting-guide', 'Lehigh Valley and the Poconos', 'mine'))
        self.assertEqual(got[1], ('region-us-pa-cool-warm', 'Pennsylvania', 'mine'))
        self.assertTrue(all(f == 'other' for d, r, f in got if d.startswith('coastal-growing-')))
        self.assertEqual(self.assistant.reader_area(), 'The person set this library to their area: Lehigh Valley and the Poconos, Pennsylvania. Prefer sources written for it (rule 10).')

    def test_switching_to_california_changes_the_guide_and_chat(self):
        regions.install(self.data, self.pack)
        regions.select(self.data, 'US-CA-socal-coast')
        c = self.when_to_plant()
        self.assertEqual({i.get('place') for i in c['read']}, {'Coastal Southern California', None})
        self.assertEqual({i['place'] for i in c['elsewhere']}, {'Pennsylvania', 'Lehigh Valley and the Poconos'})
        got = self.sources('When can I plant tomatoes?')
        self.assertTrue(got[0][0].startswith('coastal-growing-'))
        self.assertTrue(all(f == 'other' for d, r, f in got if d.startswith('region-us-pa-')))

    def test_a_question_naming_a_town_is_about_that_town(self):
        regions.install(self.data, self.pack)
        regions.select(self.data, 'US-CA-socal-coast')
        got = self.sources('When can I plant tomatoes in Easton?')
        self.assertEqual(got[0][:2], ('region-us-pa-planting-guide', 'Lehigh Valley and the Poconos'))
        self.assertTrue(all(f == 'other' for d, r, f in got if d.startswith('coastal-growing-')))
        block = self.assistant._src_block('S1', dict(self.assistant.hybrid('When can I plant tomatoes in Easton?', 8)[-2]), 'text')
        self.assertIn('Written for: Coastal Southern California — NOT the area this question is about', block)

    def test_a_region_with_nothing_here_falls_back_to_general_readings(self):
        regions.select(self.data, 'US-OH')
        c = self.when_to_plant()
        self.assertEqual([i['doc'] for i in c['read']], ['med-2ca695c9db63d020'])
        self.assertEqual(c['regionNote'], 'Nothing written for Ohio in this library yet. These readings are general.')
        regions.install(self.data, self.pack)
        regions.remove(self.data)
        regions.select(self.data, 'US-PA-lehigh-pocono')   # chosen, but its pack is gone: offline, nothing to download
        c = self.when_to_plant()
        self.assertEqual([i['doc'] for i in c['read']], ['med-2ca695c9db63d020'])
        self.assertIn('Nothing written for Pennsylvania (lehigh pocono area)', c['regionNote'])
        self.assertFalse([d for d, r, f in self.sources('When can I plant tomatoes?') if d.startswith('region-us-pa-')])

    def test_the_region_endpoint(self):
        regions.install(self.data, self.pack)
        info = self.server.region_info()
        self.assertEqual((info['region'], info['name'], info['pack']), ('US-PA', 'Pennsylvania', 'US-PA'))
        self.assertEqual({k['code'] for k in info['known']}, {'US-CA-socal-coast', 'US-PA', 'US-PA-lehigh-pocono'})


if __name__ == '__main__':
    unittest.main()
