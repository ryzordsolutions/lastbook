# Regional coverage in the guides and library: a proposal

This is a proposal for discussion, not a decision. Lastbook is meant to help anyone, anywhere. Most of its guides are
already written for any reader. The regional material it does have, though, comes almost entirely from coastal Southern
California. A reader somewhere else who asks about their own area gets the nearest regional pages the library has, and
those are Los Angeles pages.

The proposal is to treat regional material as a set of slots that every region fills the same way, rather than adding
one more region by hand. It uses northeastern Pennsylvania (the Lehigh Valley and the Pocono Mountains) as a first
worked example, because a reader there noticed the gap: asked about that area, Chat answered with Los Angeles County
material.

The design below was first proposed in this file on its own. The section "Prototype" describes a small working version of
it, added for review. Builds package `web/` and `content/` only, so this file does not ship. Where something below could
not be checked, it says so.

## What a reader sees today

Counted from `content/topics/*.json` on 17 November 2026. A reading counts as regional when its label, note or source
names California, a California place, or University of California extension.

| Guide | Readings | Regional (all California) |
|---|---|---|
| `grow` | 59 | 20 |
| `disaster` | 67 | 4 |
| `land` | 53 | 3 |
| `preserve` | 50 | 1 |
| `shelter` | 57 | 1 |
| The other nine guides | 507 | 0 |

- **Growing food is where it shows most.** The `grow` intro says its "readings lean on University of California
  extension guides for coastal Southern California". Step 2 of its route is "Learn local timing for coastal Southern
  California", and one of its choices is "What can I plant now in coastal Southern California?". Its keywords include
  `Los Angeles` and `Southern California`. Planting dates are where location matters most. In a cold-winter climate,
  the coastal Southern California calendar is wrong by months.
- **Earthquakes.** The regional readings in `disaster`, `land` and `shelter` are California earthquake and Red Cross
  Northern California material. That is right for California. Elsewhere, the local hazards (floods, winter storms,
  ice, heat, wildfire, hurricanes) have no regional counterpart.
- **The map.** `docs/REGIONS.md` already notes that the fish, boating and well layers are labelled California and stay
  empty elsewhere.
- **Chat follows the library.** Chat answers only from library pages and cites them. When the library holds regional
  pages for one place only, a question about any other place finds those pages. So the fix belongs in the library and
  the guides, not in Chat.

## The proposal: general first, then the same slots for every region

1. **Keep the general guides general.** They already work for any reader. Nothing in them should assume a place.
2. **Name the place when a reading is regional.** A label or note should say which region a page is about (as `grow`
   already does). That way a reader elsewhere knows to look for their own.
3. **Fill the same slots for each region.** Each slot is a kind of source that exists, in some form, for every US state,
   and for many other places:

| Slot | What it answers | Where it usually comes from |
|---|---|---|
| Growing season | When to plant here; first and last frost; hardiness zone; what grows well | The state's land-grant extension service and its county Master Gardeners; USDA plant hardiness zones; NOAA frost and freeze normals |
| Keeping the harvest | Canning, freezing and drying methods tested for home use | The state extension service; the USDA and the National Center for Home Food Preservation, already in the library |
| Local hazards | What actually happens here: floods, storms, ice, heat, quakes, fire | The state emergency management agency's preparedness guide; the county hazard mitigation plan |
| Warnings | Who issues warnings here, and which NOAA Weather Radio station covers it | The local National Weather Service office |
| Wildlife and plants | Dangerous animals and plants here; what is safe to forage | The state wildlife agency; the state extension service |
| Water and floods | Which rivers flood, and how | USGS; river basin commissions; the county plan |

4. **Prefer federal sources where they fit.** Works of the US federal government are generally not under copyright
   in the US, so NWS, NOAA, USDA, USGS and FEMA material can go into the library for every state the same way. State and
   university sources need their reuse terms checked one by one (see "Reuse terms" below).
5. **Consider tying regional documents to the state chosen in setup.** Setup already asks the person to pick a state for
   the detailed map, and Lastbook never detects location. A region's documents could arrive with that state's map, so
   every library carries its reader's own region without carrying all fifty. This is a design question for the
   maintainers.
6. **Record what is missing.** Once a region's sources are checked against a real library, what could not be found goes
   in `content/gaps.json`, as the guides already do.

## Design

This section answers the review on the pull request: how regional coverage would work in the app, before any of it is
built. It is written against public `main` at `256d52f` (17 November 2026). Nothing in it is implemented. Where it
leans on something not in the public repository, it says so under "Assumptions".

### 1. Regions and what they hold

**Codes.** A region has an ISO 3166 style code, the same scheme `docs/REGIONS.md` proposes for maps outside the US:

| Level | Code | Example |
|---|---|---|
| Country | ISO 3166-1 | `US`, `CA`, `GY` |
| State or province | ISO 3166-2 | `US-PA`, `US-CA`, `CA-ON` |
| Local area | the state code, then a short lowercase name | `US-PA-lehigh-pocono`, `US-CA-socal-coast` |

A US state map is named by its bare state code (`PA` in `map-set.json`). The region for it is `US-PA`. That one
mapping lives in one place, so setup can offer the map and the region from the same choice.

Each region names its parent. A local area's parent is its state, and a state's parent is its country. When the app
looks for regional readings, it walks that chain from the most local level up to the country, then falls back to the
general guides.

**One file per region:** `content/regions/<code>.json`. It holds:
- the region's name, level and parent;
- for a local area, the counties it covers (by FIPS code in the US), so a contributor can say exactly where it applies;
- its readings, grouped by slot (growing season, keeping the harvest, local hazards, warnings, wildlife and plants,
  water and floods), plus what is missing for each slot, in plain words;
- its sources: publisher, where it came from, reuse terms and how those terms were settled;
- a `revision`, so a library knows when its copy is out of date.

**Documents stay ordinary catalog records.** A regional document is a record in `catalog.json` like any other, with
one new field, `region`, holding its code. `edition`, `publication_date`, `retrieved_at`, `license` and `license_url`
already exist, so revisions and terms need nothing new on the record. `catalog_fields_allowed` in
`derived/packs/pack-spec.json` would gain `region`. The search index (`search.sqlite`) keeps its schema, because search
reads the region from the catalog.

**General and regional material.** Topic files stay general and name no place. Where a guide needs regional readings,
a choice or a route step names a slot instead of listing readings:

```json
{"id": "when-to-plant", "label": "What can I plant now where I live?", "hint": "Planting dates and frost for your area",
 "slot": "growing-season",
 "read": [{"doc": "med-2ca695c9db63d020", "page": 33, "label": "...", "note": "General readings, shown to everyone"}]}
```

The readings already listed under `read` stay general. The server adds the slot's regional readings for the reader's
region (section 3). The 20 California readings in `grow`, and the few in `disaster`, `land`, `preserve` and `shelter`,
move into `content/regions/US-CA-socal-coast.json` and `US-CA.json`. Nothing about them changes except where they are
listed. After that move, the California choice in `grow` ("What can I plant now in coastal Southern California?")
becomes the place-neutral choice above, and step 2 of its route becomes "Learn the timing where you live".

**A data example.** Doc ids starting `psu-` are placeholders: those documents are not in any catalog yet.

```json
{
  "id": "US-PA-lehigh-pocono",
  "name": "Lehigh Valley and the Poconos",
  "level": "local",
  "parent": "US-PA",
  "counties": ["42077", "42095", "42089", "42025", "42103", "42127"],
  "revision": "2026-11-17",
  "slots": {
    "growing-season": {
      "read": [
        {"doc": "psu-vegetable-seed-planting-guide", "page": 1, "label": "When to sow each vegetable",
         "note": "Penn State chart set for a 1 May last frost. It was made for York County, which is warmer: later here."}
      ],
      "missing": ["No planting chart written for Lehigh, Northampton or Monroe County."]
    },
    "warnings": {
      "read": [{"doc": "nws-zones-phi-bgm", "label": "Which weather office warns your county",
                "note": "Lehigh, Northampton, Monroe and Carbon: Mount Holly. Pike and Wayne: Binghamton."}]
    }
  },
  "sources": [
    {"doc": "psu-vegetable-seed-planting-guide", "publisher": "Penn State Extension",
     "url": "https://extension.psu.edu/...", "terms": "permission", "terms_status": "asked",
     "terms_note": "Asked Penn State Extension on <date>; no answer yet"}
  ]
}
```

`US-PA.json` holds what applies to the whole state (Penn State's statewide vegetable page, cool- and warm-season crops,
Let's Preserve, black bears, the Ready PA guide). `US.json` holds federal material that applies to every state (USDA
hardiness zones, NOAA frost and freeze normals, National Center for Home Food Preservation).

`terms` is one of `federal` (a US federal government work), `open` (an open licence, named in `license`) or
`permission` (the publisher agreed). `terms_status` is `settled` or `asked`. Only a `settled` source can go into a
build (section 4).

### 2. Choosing, downloading and updating

**Choosing.** Setup already asks for one US state for the detailed map (`docs/SETUP-API.md`, "State maps"). The same
choice would set the library's region: "Where will this library be used?". It lists states, and then the local areas
that state's region files define, if any. Lastbook still never detects location. A person can skip the question, and
the guides then stay general.

**What arrives.**
- Country-level material (`US.json`) is federal and small. It ships in every pack, like any document a guide names.
- State and local material comes as a region pack per state, `Lastbook-Region-US-PA.zip`. It holds that state's
  documents and those of all its local areas: a few dozen documents at most, so one zip per state is simpler than one
  per area.
- The manifest gains a `regions` list beside `packs`, `maps` and `address_indexes`, with the same entry shape (`id`,
  `title`, `installed_bytes`, `available`, `files`) plus `revision`.
- A region pack is small, so setup would include it by default once a state is chosen. The detailed map stays optional:
  a Small library on a small disk can have Pennsylvania's guides without Pennsylvania's map.

**Installing.** A region pack is installed like an add-on (`addons.merge` in `pack-spec.json`), in the staged copy:
- its catalog records join `catalog.json`;
- its search rows go into `search.sqlite`;
- its files are copied in.

The library then switches in one step, as packs and maps do, so a working library is never broken. A
`region-set.json` at the library's top, like `map-set.json`, records:
- the region chosen, which may be a local area;
- the installed revision;
- the ids and files it added, so a later change can remove exactly those.

`state.current.region` reports it.

**Changing region.** One region per library, as with state maps. Choosing another state removes the old region's
records, rows and files and adds the new one's, in the same staged way. Moving between local areas of the same state
needs no download: it only rewrites `region-set.json`.

**Offline.** Everything a region needs is inside the library, so guides, search and Chat work with no network. If a
person changes region while offline and the new region's zip is not on the computer, setup says so, and the current
region stays. A region zip already on the computer is found the way pack and map zips are (`found[]`).

**Updates.** When the manifest's `revision` for the installed region differs from `region-set.json`, the manage screen
offers the update, as it does for a pack's `content_revision`.

### 3. Guides, search and Chat

**Guides.** `topics_resolved()` in `server.py` already drops readings whose document is not in the library. For a
choice or step with a `slot`, it would walk the reader's region chain and add the readings from each level that fills
the slot, most local first, each labelled with its region's name. Then:
- **Covered.** The reader sees their area's readings first, then the general ones.
- **Not covered.** If no level of the chain fills the slot, the reader sees the general readings and one plain line from
  that region's `missing` list, for example "No planting dates for Pennsylvania in this library yet". Readings from
  other regions are not offered as the answer.
- **No region chosen.** The reader sees the general readings and a link to choose where they are.

Readings written for other regions that this library holds (in All, for example) stay findable under a closed
"Written for other places" list, each with its place in the label. They are never mixed in with "yours".

**Search.** Records keep their `region` field into results. `hybrid()` in `assistant.py` already reweights scores, and
it would add one rule:
- a record for a region in the reader's chain is raised a little;
- a record for any other region is lowered, but not removed, because it may be the only thing on the subject;
- general records are unchanged.

Search results show the region beside the publisher.

**Chat.** Two changes in `assistant.py`:
1. **Sources say where they apply.** `_src_block()` adds the region to each source's header: "written for coastal
   Southern California", plus "not the reader's area" when it falls outside the chain.
2. **The instructions know the region.** The system prompt gains the reader's chosen region, if any, and one rule:
   "When a source is written for another region, say which region; never give its dates, hazards or contacts as the
   reader's own. When the answer depends on place and no source is written for the reader's region, say so under
   'Not found in the library:'."

The chosen region stays on the computer, like everything else Chat sees. The fix stays in the library, as the proposal
says: Chat still answers only from library pages, but it is told which pages are for which place.

### 4. Adding the next region

A contributor supplies, in one pull request:
1. `content/regions/<code>.json`, and its parent's file if that is new, with readings by slot and what is missing.
2. A source entry for every document: publisher, URL, the date it was retrieved, and its terms. A `permission` source
   gives `terms_status: "asked"` until the publisher agrees. Permission letters stay with the maintainers, not in the
   repository.

The contributor does not add the documents themselves. A maintainer fetches each one from its URL, checks it, adds its
catalog record with `region` set, and builds the region pack.

**Checks**, in a `tools/validate_regions.py` beside `tools/validate_topics.py`, run against a library that holds the
region pack:
- the code is well formed, its parent exists, and the chain ends at a country;
- every slot name is a known slot;
- every `doc` is in the catalog, carries this region's code (or a code in its chain), and every page is in range;
- every source has terms, and the build refuses a region with any source not `settled`;
- every label and note names no place outside the region, so nothing reads as local when it is not.

Plus a test in `app/tests`, like `test_validate_topics.py`, that runs these checks on the region files in the
repository.

**Pennsylvania, as the first worked example.** The research below fills the growing-season and keeping-the-harvest slots
from Penn State Extension, and starts on hazards, warnings and wildlife. Its order would be:
1. **`US.json` first, from federal sources.** It needs no permission, so it can go in while the rest waits.
2. **`US-PA.json` and `US-PA-lehigh-pocono.json` growing season**, once Penn State answers on reuse.
3. **The other slots**, which use the same file and the same checks:
   - local hazards (Ready PA, if the Commonwealth agrees; the county hazard plans);
   - warnings, where the NWS offices already differ within the Poconos, which is why local areas exist;
   - water and floods (USGS, the Delaware River Basin Commission).

### 5. End to end

1. A contributor opens a pull request adding `US-PA.json` and `US-PA-lehigh-pocono.json`. The checks pass, and the
   maintainers settle the terms.
2. A maintainer adds the documents to the catalog, builds `Lastbook-Region-US-PA.zip`, and lists it under `regions` in
   the manifest.
3. A person in Bethlehem runs setup, picks Small, then Pennsylvania, then "Lehigh Valley and the Poconos", and skips
   the detailed map. Setup downloads Small and the region pack, installs them in the staged library, and switches.
4. In Grow, "What can I plant now where I live?" shows the Penn State planting chart labelled Pennsylvania first, then
   the general readings. Southern California's chart sits under "Written for other places", if this library holds it.
5. In Chat, "When can I plant tomatoes outside?" finds the Penn State pages first. The UC chart, if found, is marked
   "written for coastal Southern California, not the reader's area", so the answer cites Penn State and does not give
   the California dates.
6. The person moves to Ontario and changes region in the manage screen. If no Ontario region exists yet, the guides go
   back to general readings and say there is nothing for Ontario yet, rather than offering Pennsylvania.

### 6. Assumptions and where this meets the map work

- **Add-on merging.** `pack-spec.json` describes merging add-ons into a library (`addons.merge`, naming
  `packaging/pack_merge.py`), but that script is not in the public repository. Region install would live in
  `engine/setup/install.py` beside the state map code, and use the same staged switch.
- **One picker, two downloads.** The region and the state map come from the same choice in setup but are separate
  downloads with separate files (`region-set.json` and `map-set.json`). A person can have either without the other.
- **Codes.** If maps outside the US adopt the codes in `docs/REGIONS.md` (`CA-ON`, `CA-BC`, `GY`), regions use the
  same ones, and only US state maps need the `PA` to `US-PA` mapping.
- **No geometry.** Regions need no box. Map sets are cut by a box, while a local area is a list of counties. Lastbook
  does not use the map to guess where someone is.
- **California-only map layers.** The fish, boating and well layers (`web/map.js`) are a map question. They could
  carry the same region code later, so the map could say "California only" in the same words as the guides.
- **The All pack.** All would carry every region's documents, so a region choice there only sets which ones count as
  the reader's own.

## Prototype: one slot, two regions

Asked for in review: one guide slot (growing season) across northeastern Pennsylvania and coastal Southern California, on
a test library, showing that choosing a region installs or switches on its material, that switching region changes both
the Grow guide and the sources Chat is given, and that the guide falls back to general readings when a region has nothing
in the library. It is a prototype for review, not a final implementation: there are no setup screens, and the
Pennsylvania documents are stand-ins written for testing, because Penn State has not yet been asked about reuse.

### What it adds

| Part | Where | What it does |
|---|---|---|
| Regions | `engine/regions.py` | Region codes and chains (`US-PA-lehigh-pocono` → `US-PA` → `US`), region files, `region-set.json` in the data folder, and region packs: check, install, remove, choose. One pack per library, as with state maps. |
| California's region file | `content/regions/US-CA-socal-coast.json` | The three California growing-season readings, moved out of `grow.json` unchanged, and the towns the region covers. |
| Grow guide | `content/topics/grow.json` | "What can I plant now where I live?" and step 2 name the `growing-season` slot. Their own reading is the general Hesperian chapter, already in the guide's learn list. |
| Guides | `server.py` `fill_slot`, `web/app.js` `slotMore` | The reader's region's readings first, each with its place; then the general ones; a note on what is missing; readings for other places in a closed "Written for other places" list. `/api/region` reports the region (read only: the server still writes nothing). |
| Chat | `assistant.py` `region_context`, `hybrid`, `_src_block`, `reader_area`, rule 10 | Sources for the reader's region (or for a place the question names, such as Easton or Stroudsburg) are raised a little; sources for other places are lowered, never removed. Every regional source says where it is written for, and the model is told the area the person chose. |
| Pennsylvania test pack | `app/tests/fixtures/region-US-PA/` | Two stand-in documents, their search rows and text, and region files for Pennsylvania and for the Lehigh Valley and the Poconos (Allentown, Bethlehem, Easton, Stroudsburg, Jim Thorpe, Honesdale and the rest). |
| Tool | `tools/region_pack.py` | `test-library`, `build`, `install`, `select`, `remove` and `show`, for the run-through below. |
| Tests | `app/tests/test_regions.py` | 16 tests: codes and names, region files, install and remove leaving the library exactly as it was, refused packs (a path out of the pack, another state's files, a changed file, a borrowed id, a stray search row), the guide and Chat for each case below. |
| Shipping | `packaging/ship_app_files.py`, `lastbook_macos.spec`, `build_packs.py`, `verify_packs.py`, `tools/validate_topics.py` | `content/regions/` ships with the app, its readings count as guide readings when a pack is built (so Small keeps the California charts), and the guide check checks them. |

### How a region pack is checked and installed

A region pack is a zip holding `region-pack.json`, `catalog.json`, `search.json`, `regions/<code>.json` and its documents
under `documents/regions/<state>/`, with their text under `text/`. Before anything in the library changes, every file is
checked: no path leaves the pack or reaches another state's folder, every record's id starts `region-<state>-`, every
file matches its SHA-256, and every search row belongs to a document the pack holds. Installing removes any other region
pack first, copies the files, adds the search rows in one transaction and writes the catalog in one rename. Removing takes
out exactly the ids, rows and files it added. The real version would run through setup's staged switch
(`docs/SETUP-API.md`), as the state maps do; the region choice would come from setup's state picker.

### Run-through

On any computer with this branch, no library and no network needed:

```bash
python3 tools/region_pack.py test-library test-library
python3 tools/region_pack.py build app/tests/fixtures/region-US-PA Lastbook-Region-US-PA.zip
python3 tools/region_pack.py install --data test-library Lastbook-Region-US-PA.zip
python3 tools/region_pack.py select --data test-library US-PA-lehigh-pocono
python3 tools/region_pack.py show --data test-library --ask "When can I plant tomatoes?"
python3 -B -m unittest app.tests.test_regions
```

To see it in the app, run `python3 server.py --data test-library` and open Grow, then "What can I plant now where I
live?", or ask in Chat. `select` changes the region (`US-CA-socal-coast`, `US-OH`, `none`); reload the page after it.
The test library holds the documents the Grow guide cites under their real ids, with short stand-in text. On a real
library the same commands work with `--data /path/to/LastbookData` (close the app first).

The log below is the output of those commands on 18 November 2026, covering each case asked for:
1. no region chosen;
2. the Pennsylvania pack installed;
3. the Lehigh Valley and the Poconos chosen;
4. California chosen, with a question about Easton;
5. Ohio, which has no material;
6. the Pennsylvania pack removed, with the Poconos still chosen.

<details><summary>Run-through log</summary>

```text
$ python3 tools/region_pack.py test-library test-library
Made a test library in test-library/library-data

$ python3 tools/region_pack.py build app/tests/fixtures/region-US-PA Lastbook-Region-US-PA.zip
Built Lastbook-Region-US-PA.zip

$ python3 tools/region_pack.py show --data test-library --ask When\ can\ I\ plant\ tomatoes\?
Region: not chosen
Region pack installed: none

Guide “Grow staple food” → “What can I plant now where I live?”
  - [general] Growing food in any place: the general chapter
  Note: Readings for a particular place show here once you choose where this library is used.
  Written for other places: [Coastal Southern California] Month-by-month vegetable planting chart for Southern California
  Written for other places: [Coastal Southern California] October planting list for Los Angeles County (historical)
  Written for other places: [Coastal Southern California] Potato planting months by California region

Chat sources for “When can I plant tomatoes?”:
  - Sustainable farming (test stand-in for the Hesperian chapter) — general
  - October planting list (test stand-in for UC Los Angeles County) — written for Coastal Southern California, the reader has not chosen an area
  - Vegetable planting chart (test stand-in for UC Ventura County) — written for Coastal Southern California, the reader has not chosen an area
  - Potato planting months (test stand-in for UC IPM) — written for Coastal Southern California, the reader has not chosen an area

$ python3 tools/region_pack.py install --data test-library Lastbook-Region-US-PA.zip
Installed the US-PA region pack: 2 documents. Region: US-PA.

$ python3 tools/region_pack.py select --data test-library US-PA-lehigh-pocono
Region: US-PA-lehigh-pocono.

$ python3 tools/region_pack.py show --data test-library --ask When\ can\ I\ plant\ tomatoes\?
Region: Lehigh Valley and the Poconos (US-PA-lehigh-pocono)
Region pack installed: US-PA

Guide “Grow staple food” → “What can I plant now where I live?”
  - [Lehigh Valley and the Poconos] When to sow each vegetable (test stand-in)
  - [Pennsylvania] Which vegetables go in before the last frost (test stand-in)
  - [general] Growing food in any place: the general chapter
  Note: No planting chart written for Lehigh, Northampton or Monroe County.
  Written for other places: [Coastal Southern California] Month-by-month vegetable planting chart for Southern California
  Written for other places: [Coastal Southern California] October planting list for Los Angeles County (historical)
  Written for other places: [Coastal Southern California] Potato planting months by California region

Chat sources for “When can I plant tomatoes?”:
  - Vegetable seed planting guide (test stand-in) — written for Lehigh Valley and the Poconos
  - Cool-season and warm-season vegetables (test stand-in) — written for Pennsylvania
  - Sustainable farming (test stand-in for the Hesperian chapter) — general
  - October planting list (test stand-in for UC Los Angeles County) — written for Coastal Southern California, not the area asked about
  - Vegetable planting chart (test stand-in for UC Ventura County) — written for Coastal Southern California, not the area asked about
  - Potato planting months (test stand-in for UC IPM) — written for Coastal Southern California, not the area asked about
  Told to the model: The person set this library to their area: Lehigh Valley and the Poconos, Pennsylvania. Prefer sources written for it (rule 10).

$ python3 tools/region_pack.py select --data test-library US-CA-socal-coast
Region: US-CA-socal-coast.

$ python3 tools/region_pack.py show --data test-library --ask When\ can\ I\ plant\ tomatoes\ in\ Easton\?
Region: Coastal Southern California (US-CA-socal-coast)
Region pack installed: US-PA

Guide “Grow staple food” → “What can I plant now where I live?”
  - [Coastal Southern California] Month-by-month vegetable planting chart for Southern California
  - [Coastal Southern California] October planting list for Los Angeles County (historical)
  - [Coastal Southern California] Potato planting months by California region
  - [general] Growing food in any place: the general chapter
  Written for other places: [Pennsylvania] Which vegetables go in before the last frost (test stand-in)
  Written for other places: [Lehigh Valley and the Poconos] When to sow each vegetable (test stand-in)

Chat sources for “When can I plant tomatoes in Easton?”:
  - Vegetable seed planting guide (test stand-in) — written for Lehigh Valley and the Poconos
  - Cool-season and warm-season vegetables (test stand-in) — written for Pennsylvania
  - Sustainable farming (test stand-in for the Hesperian chapter) — general
  - October planting list (test stand-in for UC Los Angeles County) — written for Coastal Southern California, not the area asked about
  - Vegetable planting chart (test stand-in for UC Ventura County) — written for Coastal Southern California, not the area asked about
  - Potato planting months (test stand-in for UC IPM) — written for Coastal Southern California, not the area asked about
  Told to the model: The person set this library to their area: Coastal Southern California, California. Prefer sources written for it (rule 10).

$ python3 tools/region_pack.py select --data test-library US-OH
Region: US-OH.

$ python3 tools/region_pack.py show --data test-library
Region: Ohio (US-OH)
Region pack installed: US-PA

Guide “Grow staple food” → “What can I plant now where I live?”
  - [general] Growing food in any place: the general chapter
  Note: Nothing written for Ohio in this library yet. These readings are general.
  Written for other places: [Coastal Southern California] Month-by-month vegetable planting chart for Southern California
  Written for other places: [Coastal Southern California] October planting list for Los Angeles County (historical)
  Written for other places: [Coastal Southern California] Potato planting months by California region
  Written for other places: [Pennsylvania] Which vegetables go in before the last frost (test stand-in)
  Written for other places: [Lehigh Valley and the Poconos] When to sow each vegetable (test stand-in)

$ python3 tools/region_pack.py remove --data test-library
Removed the US-PA region pack.

$ python3 tools/region_pack.py select --data test-library US-PA-lehigh-pocono
Region: US-PA-lehigh-pocono.

$ python3 tools/region_pack.py show --data test-library --ask When\ can\ I\ plant\ tomatoes\?
Region: Pennsylvania (lehigh pocono area) (US-PA-lehigh-pocono)
Region pack installed: none

Guide “Grow staple food” → “What can I plant now where I live?”
  - [general] Growing food in any place: the general chapter
  Note: Nothing written for Pennsylvania (lehigh pocono area) in this library yet. These readings are general.
  Written for other places: [Coastal Southern California] Month-by-month vegetable planting chart for Southern California
  Written for other places: [Coastal Southern California] October planting list for Los Angeles County (historical)
  Written for other places: [Coastal Southern California] Potato planting months by California region

Chat sources for “When can I plant tomatoes?”:
  - Sustainable farming (test stand-in for the Hesperian chapter) — general
  - October planting list (test stand-in for UC Los Angeles County) — written for Coastal Southern California, not the area asked about
  - Vegetable planting chart (test stand-in for UC Ventura County) — written for Coastal Southern California, not the area asked about
  - Potato planting months (test stand-in for UC IPM) — written for Coastal Southern California, not the area asked about
  Told to the model: The person set this library to their area: Pennsylvania (lehigh pocono area), Pennsylvania. Prefer sources written for it (rule 10).
```

</details>

### What the prototype leaves open

- **Setup.** No screens: `tools/region_pack.py` stands in for setup's picker and download.
- **Ranking.** The weights (×1.25 for the reader's region, ×0.6 for another, ×0.85 when no region is chosen) are first
  guesses, chosen so a regional source moves past a general one on the same subject but never disappears.
- **Places in questions.** A town is matched by whole words from a region file's `places`. A town name shared with
  another state (Bangor, Milford) is left out of the Pennsylvania list for that reason.
- **One region per library.** Kept, as with state maps.

## Worked example: northeastern Pennsylvania

The Lehigh Valley (Lehigh and Northampton counties, including Allentown, Bethlehem and Easton) and the Pocono Mountains
(Monroe, Carbon, Pike and Wayne counties, including Stroudsburg). The area has cold winters, a growing season months
shorter and later than coastal Southern California's, river flooding on the Delaware and Lehigh and their creeks,
winter storms and black bears.

Each source below is marked **opened** (the page or file was fetched and read on 17 November 2026) or **found** (it
turned up in a search but was not opened). Every source still has to be read in full and checked before it goes into the
library.

### Growing food

This is the slot that matters most, and Penn State Extension covers it well.

| Source | Publisher | What it adds | Checked |
|---|---|---|---|
| Vegetables (extension.psu.edu/forage-and-food-crops/vegetables) | Penn State Extension | "Pennsylvania is in 5, 6, and 7 USDA plant hardiness zones". First frost ranges from 13 September (Ridgway) to 28 October (Philadelphia), last frost from 14 April (Philadelphia) to 7 June (Ridgway), with "approximately 150 days between the last and first frost". Also soil temperatures for planting sweet corn, broccoli and peppers. | Opened |
| Cool-season vs. Warm-season Vegetables (extension.psu.edu/cool-season-vs-warm-season-vegetables) | Penn State Extension | Which crops go in weeks before the last frost, which wait for warm soil, and which can be grown twice a year. This is the idea a cold-winter gardener needs first. | Opened |
| Season Extenders and Growing Fall Vegetables (extension.psu.edu/season-extenders-and-growing-fall-vegetables) | Penn State Extension | Finding local frost and freeze dates by ZIP code (its example: University Park, 12 October to 10 May, about 155 days), and growing past them with season extenders. | Opened |
| Vegetable Seed Planting Guide (PDF, 3 pages; extension.psu.edu, Master Gardeners of York County) | Penn State Extension | A chart per crop: cool or warm season, pH, sowing depth and spacing, earliest and last sowing dates, days to germinate and to maturity, and last harvest before frost. Set for a 1 May last frost and a 1 October first frost. The file was created in 2009 and last changed in 2020. York County is warmer than the Poconos, so a reader there would shift the dates. | Opened |
| Vegetable Transplanting Guide (abe.psu.edu, Master Gardeners of York County) | Penn State Extension | The companion chart for crops set out as transplants | Found |
| Philadelphia Planting Guide (extension.psu.edu, Master Gardeners of Philadelphia County) | Penn State Extension | First and last planting dates per crop. Gives the average last frost for central Philadelphia as 20 April, "a week or two later" in higher and outlying parts. | Opened |
| Vegetable Gardening From Scratch (extension.psu.edu, Master Gardeners of Pike County) | Penn State Extension | A short series written in a Pocono county: container and raised-bed gardening, choosing crops, designing the space, soil, tools and reading a seed packet. | Opened |
| Let's Preserve fact sheets (extension.psu.edu/food-safety-and-quality/home-food-preservation-and-safety/lets-preserve) | Penn State Extension | Tested methods to can, freeze and dry fruits, vegetables and meats, with troubleshooting. Also available in Spanish. | Opened (the index page; the sheets themselves were not) |
| USDA Plant Hardiness Zone Map | USDA Agricultural Research Service | Zones for every state | Found |

No planting chart was found for Lehigh, Northampton or Monroe County. Penn State's pages direct readers to their county
Master Gardeners and Garden Hotlines for local dates.

### Local hazards and warnings

| Source | Publisher | What it adds | Checked |
|---|---|---|---|
| Pennsylvania Emergency Preparedness Guide (pa.gov/en/agencies/ready/get-prepared/emergency-preparedness-guide.html) | Ready PA (Commonwealth of Pennsylvania) | A 24-page booklet. Its "Top 10 Emergencies" begin with floods, fires and winter storms, then tropical storms, tornadoes and thunderstorms, flu pandemic, hazardous materials, earthquakes and landslides, nuclear threat, dam failures and terrorism. Also family plans, home and vehicle kit checklists, and planning for older people and people with disabilities. Offered in English, large print, Spanish, German, Russian and Simplified and Traditional Chinese. The English file was made in 2017 and last changed in 2021. | Opened |
| National Weather Service | NOAA | The NWS county zone records (api.weather.gov) put Lehigh, Northampton, Monroe and Carbon counties under the Mount Holly office (PHI), and Pike and Wayne counties under the Binghamton office (BGM). So a guide card for "who warns this area" differs within the Poconos. | Opened |
| Monroe County Multi-Jurisdictional Hazard Mitigation Plan (2025 draft; 2021 plan) | Monroe County Office of Emergency Management | The hazards the county itself ranks, with their history. It is long; only the hazard sections would help a reader. | Found |
| Northampton County hazard mitigation plan | Northampton County | Not found. Only a 2024 township annex turned up. | — |
| Flood material for the Delaware River basin | Delaware River Basin Commission | Basin flooding history and mitigation | Found |

### Wildlife

| Source | Publisher | What it adds | Checked |
|---|---|---|---|
| Black Bears (extension.psu.edu/black-bears) | Penn State Extension | Keeping bears away from homes, crops and beehives: removing what attracts them, securing garbage, electric fencing. Says bears are protected under the Pennsylvania Game Commission's jurisdiction. | Opened |
| Wildlife Notes: Black Bear (pgc.pa.gov) | Pennsylvania Game Commission | The state wildlife agency's own fact sheet | Found |

### Not found

- A Pennsylvania guide to safe wild edible plants from an agency or the extension service.
- Flood material written for Easton or the Lehigh Valley itself.
- A planting chart for the Lehigh Valley or Pocono counties.

## Reuse terms

The library already carries University of California extension guides, so the maintainers have a way of deciding
whether a university or state document may be included. That same decision is needed for each source above:

- **Penn State Extension.** Its pages carry "© 2026 The Pennsylvania State University". No reuse statement for
  extension publications turned up, and Penn State's general copyright guidance says permission is generally needed to
  reuse a substantial part of a copyrighted work. Asking Penn State Extension directly may be simplest, and one answer
  would cover the growing, preserving and wildlife sources together.
- **Commonwealth of Pennsylvania.** The Ready PA page carries "Copyright © 2026 Commonwealth of Pennsylvania. All rights
  reserved." No general Commonwealth reuse policy turned up, so Ready PA (readypa@pa.gov, as printed in the guide) would
  need to be asked. The Game Commission would need to be asked separately.
- **Federal sources** (NWS, NOAA, USDA, USGS, FEMA) are generally not under copyright in the US.

## Decisions the design needs

Each of these needs the maintainers' decision before any code follows the design:

1. **Moving California's readings.** Whether the California readings in the guides move into region files and the
   guides name slots instead (section 1).
2. **Region packs by default.** Whether setup adds a region pack whenever a state is chosen, or offers it as a separate
   choice (section 2).
3. **One region per library.** Whether a library holds one region, as it holds one state map, or several, for someone
   who moves between places (section 2).
4. **Ranking.** How strongly search raises the reader's region and lowers others (section 3). This needs testing on
   real questions before a number is chosen.
5. **First regions.** Whether to start with Pennsylvania alone to set the pattern, or with `US.json` (federal only) for
   every state first, while Penn State and the Commonwealth are asked.
6. **Gaps.** Whether a region's `missing` lists replace `content/gaps.json` for regional topics, or feed it.

## What was checked

- The design was written against public `main` at `256d52f`, from `docs/SETUP-API.md`, `derived/packs/pack-spec.json`,
  `content/SCHEMA.md`, `server.py` (`topics_resolved`, `item_here`), `assistant.py` (`hybrid`, `_src_block`, `SYSTEM`)
  and `engine/setup/` as they are there. Nothing in it was built or run.
- The counts in "What a reader sees today" were computed from `content/topics/*.json` at the commit this file was added
  in.
- Sources marked "Opened" were fetched on 17 November 2026, and the quotations and facts given for them were read from
  the fetched page or file. File dates come from the PDFs' own metadata.
- Sources marked "Found" turned up in web searches on the same day and were not opened. Their contents, editions and
  reuse terms are not confirmed.
