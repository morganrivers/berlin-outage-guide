#!/usr/bin/env python3
"""Berlin power outage guide: one source table drives every page and the downloader.

  python3 outage_guide.py build                             -> index.html, README.md and .gitignore; links go
                                                               to the original sources, or to files in this repo
  python3 outage_guide.py local DIR                         -> DIR/START_HERE.html, links go to files in DIR
  python3 outage_guide.py download [DIR]                    -> download the library into DIR (default: this
                                                               folder), then build DIR/START_HERE.html
  python3 outage_guide.py pack OUT [--full]                 -> OUT/berlin-outage-guide-mini.zip: the shareable files
                                                               and START_HERE.html; --full also writes ...-full.zip,
                                                               which adds medical Wikipedia (CC BY-SA)

The repo ships only files that may be shared: US government works (public domain), Wikipedia and
Appropedia articles (CC BY-SA) and OpenStreetMap data (ODbL). Everything else comes from its publisher.
"""
import argparse
import csv
import hashlib
import html
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent
REPO_URL = "https://github.com/morganrivers/berlin-outage-guide"
PACK_TAG = "offline-pack"   # GitHub release that the workflow keeps up to date with the zips below
PACKS = {"mini": "berlin-outage-guide-mini.zip", "full": "berlin-outage-guide-full.zip"}
FULL_ZIMS = [("zim/wikipedia/", "wikipedia_en_medicine_nopic")]   # Wikipedia is CC BY-SA, so it may be shared
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
KIWIX = "https://download.kiwix.org/"
HESP = "https://hesperian.org/wp-content/uploads/pdf/"
HESP_INDEX = "https://languages.hesperian.org/pages/en/pdf.html"
CHAPTERS = "hesperian_chapters/"


def hesperian_chapters(book, parts):
    return tuple(f"{HESP}{book}/{book}_{p}.pdf" for p in parts)


def chapter_path(url):
    return CHAPTERS + url.rsplit("/", 1)[1]


WTND_CHAPTERS = hesperian_chapters(
    "en_wtnd_2025", ["fm", "toc", "int", *[f"{i:02d}" for i in range(1, 24)], "gp", "info", "gloss", "resc", "indx", "bm"])
DENT_CHAPTERS = hesperian_chapters("en_dent_2024", ["fm", *[f"{i:02d}" for i in range(1, 13)], "bm"])


@dataclass(frozen=True)
class Source:
    path: str        # path inside the library folder; for get="hesperian", the chapter that opens the book
    desc: str
    url: str         # original source: shown in the references, linked online, downloaded
    get: str         # repo (shipped in this repo) | file | print (save web page as PDF) | hesperian (chapter PDFs)
    web: str = ""    # online link when it differs from url
    parts: tuple = ()      # chapter URLs for get="hesperian"
    page_map: tuple = ()   # (topic, chapter URL, page in that chapter PDF) for get="hesperian"

    def chapter(self, page):
        hits = [(url, p) for topic, url, p in self.page_map if topic == page]
        assert len(hits) <= 1 and (hits or not isinstance(page, str)), f"no chapter for {page!r} in {self.path}"
        return hits[0] if hits else None

    def local_href(self, page=None):
        ch = self.chapter(page)
        if ch:
            return f"{chapter_path(ch[0])}#page={ch[1]}"
        return self.path + (f"#page={page}" if page else "")

    def web_href(self, page=None):
        if self.get == "repo":
            return self.local_href(page)  # relative link into this repo
        ch = self.chapter(page)
        if ch:
            return f"{ch[0]}#page={ch[1]}"
        base = self.web or self.url
        return base + (f"#page={page}" if page and base.endswith(".pdf") else "")

    def local_files(self):
        return [chapter_path(u) for u in self.parts] if self.get == "hesperian" else [self.path]


def article(title, site="Wikipedia"):
    base = "https://en.wikipedia.org/wiki/" if site == "Wikipedia" else "https://www.appropedia.org/"
    return Source(f"library_extracts/{site}_{title}.pdf",
                  f"{site} article {title.replace('_', ' ')}, CC BY-SA licence, saved as PDF", base + title, "repo")


BBK_EN = "emergency_guides/BBK_Planning_ahead_for_crises_and_disasters_2025_EN.pdf"
WTND = chapter_path(WTND_CHAPTERS[1])
DENT = chapter_path(DENT_CHAPTERS[0])
FEMA_READY = "emergency_guides/FEMA_Are_You_Ready_2020.pdf"
MAP = "berlin/berlin_emergency_map.html"
KATL = "berlin/berlin_kat_leuchttuerme.html"
KATL_FK = "berlin/friedrichshain_kreuzberg_kat_leuchttuerme.html"
BWB_PUMPS = "berlin/bwb_notbrunnen.html"
CDC_WATER = "emergency_guides/CDC_Make_Water_Safe_During_Emergency.pdf"
RBB_JAN = "berlin/rbb24_blackout_january_2026.html"
BBK_DE = "emergency_guides/BBK_Stromausfall_Vorsorge_und_Selbsthilfe_DE.pdf"
ERC = "emergency_guides/ERC_Guidelines_for_Everyone_2025.pdf"
MSF = "emergency_guides/MSF_Essential_Drugs_2026.pdf"
BBK_MEDS = "emergency_guides/BBK_Medicine_cabinet_EN.pdf"
FDA_INSULIN = "emergency_guides/FDA_Insulin_Storage_and_Switching_in_Emergency.pdf"
CDC_CO = "emergency_guides/CDC_Carbon_Monoxide_Danger_flyer.pdf"
FOOD_CHARTS = "emergency_guides/FoodSafetyGov_Food_Safety_During_Power_Outage.pdf"
TWIN_BUCKET = "emergency_guides/RDPO_Twin_Bucket_Emergency_Toilet_flyer.pdf"
WHO_DWM = "emergency_guides/WHO_Doing_What_Matters_in_Times_of_Stress_2020.pdf"
CDC_DISAB = "emergency_guides/CDC_Emergency_Preparedness_People_with_Disabilities.pdf"
SOURCES = {s.path: s for s in [
    Source(BBK_EN, "BBK (German federal civil protection office): Planning ahead for crises and disasters, English, Nov 2025",
           "https://www.ettlingen.de/site/Ettlingen-2021/get/params_E1441149346_Dattachment/3856928/BBK-Planning-ahead-for-crises-and-disasters_English%20November%202025.pdf", "file"),
    Source(BBK_DE, "BBK: Stromausfall, Vorsorge und Selbsthilfe (German, October 2026)",
           "https://www.bbk.bund.de/SharedDocs/Downloads/DE/Mediathek/Publikationen/Buergerinformationen/stromausfall-vorsorge-selbsthilfe.pdf?__blob=publicationFile&v=17", "file"),
    Source("emergency_guides/FEMA_Power_Outage_sheet.pdf", "FEMA: Be prepared for a power outage, V-1008, Dec 2023 (US government work, public domain)",
           "https://www.cpp.edu/em/documents/power-outage-safety-tips-readygov-pdf.pdf", "repo"),
    Source(FEMA_READY, "FEMA: Are You Ready? US citizen preparedness guide (US government work, public domain)",
           "https://www.ready.gov/sites/default/files/2021-03/are-you-ready-guide.pdf", "repo"),
    Source("emergency_guides/CDC_Keep_Food_Safe_After_Disaster.pdf", "CDC: Keep food safe after a disaster or emergency (US government work, public domain; web page saved as PDF)",
           "https://www.cdc.gov/food-safety/foods/keep-food-safe-after-emergency.html", "repo"),
    Source("emergency_guides/Emergency_Toilets_WREMO.pdf", "WREMO (Wellington, NZ): Emergency toilets, two bucket method (web page saved as PDF; its folded question and answer sections do not print)",
           "https://www.wremo.nz/get-ready/home-ready/emergency-toilets", "print"),
    Source("emergency_guides/WHO_Psychological_First_Aid_2011.pdf", "WHO: Psychological first aid, guide for field workers, 2011",
           "https://mhpsshub.org/wp-content/uploads/2018/10/9789241548205_eng.pdf", "file"),
    Source(WTND, f"Hesperian: Where There Is No Doctor, 2025 ({len(WTND_CHAPTERS)} chapter PDFs in {CHAPTERS}; this one is the contents)",
           HESP + "en_wtnd_2025/", "hesperian", web=HESP_INDEX, parts=WTND_CHAPTERS,
           page_map=(("rehydration", WTND_CHAPTERS[15], 2), ("cold", WTND_CHAPTERS[27], 10))),
    Source(DENT, f"Hesperian: Where There Is No Dentist, 2024 ({len(DENT_CHAPTERS)} chapter PDFs in {CHAPTERS}; this one opens the book)",
           HESP + "en_dent_2024/", "hesperian", web=HESP_INDEX, parts=DENT_CHAPTERS),
    Source("berlin/Berlin_Flyer_Gefahrenlagen.pdf", "Berlin Senate: flyer, information for the public in dangerous situations (German)",
           "https://www.berlin.de/bevoelkerungsschutz/warnung-und-information/druckdatei-flyer---informationen-und-hinweise-fuer-die-bevoelkerung-bei-gefahrenlagen.pdf", "file"),
    Source("berlin/berlin_stromausfall.html", "berlin.de: Vorsorge und Verhalten bei Stromausfall (saved web page)",
           "https://www.berlin.de/katastrophenschutz/notfallvorsorge/stromausfall/artikel.1653256.php", "file"),
    Source("berlin/berlin_rufnummern.html", "berlin.de: Wichtige Rufnummern (saved web page)",
           "https://www.berlin.de/katastrophenschutz/service/wichtige-rufnummern/wichtige-rufnummern-1279430.php", "file"),
    Source("berlin/rbb_frequenzen.html", "rbb: radio frequencies (saved web page)", "https://www.rbb-online.de/radio/frequenzen/", "file"),
    Source("berlin/giftnotruf.html", "Giftnotruf Berlin, Charité (poison line, saved web page)", "https://giftnotruf.charite.de/", "file"),
    Source("berlin/116117.html", "116117: medical on-call service (saved web page)", "https://www.116117.de/de/index.php", "file"),
    Source("berlin/kat_leuchttuerme.csv", "berlin.de: Katastrophenschutz-Leuchttürme, all 73 locations (list of addresses, Nov 2026)",
           "https://www.berlin.de/bevoelkerungsschutz/warnung-und-information/anlaufstellen-fuer-die-bevoelkerung/", "repo"),
    Source("berlin/notbrunnen_osm.csv", "Manual water pumps in Berlin, © OpenStreetMap contributors, ODbL licence (Overpass export, Nov 2026)",
           "https://www.openstreetmap.org/copyright", "repo"),
    Source("berlin/notbrunnen_hand_pumps.gpx", "Same pumps as GPX for offline map apps, made from the CSV above (© OpenStreetMap contributors, ODbL)",
           "https://www.openstreetmap.org/copyright", "repo"),
    Source(MAP, "Offline map of Berlin, one HTML file that opens in any browser without internet or an app: Kat-Leuchttürme and info points "
           "(from the list above), hand pumps, hospitals, fire and police stations, pharmacies, supermarkets, hardware stores, "
           "underground stations, plus main roads and towns in Brandenburg. Press and hold a spot to list the nearest of each. "
           "© OpenStreetMap contributors, ODbL", "https://www.openstreetmap.org/copyright", "repo"),
    Source(KATL, "berlin.de: Katastrophenschutz-Leuchttürme, how they open (saved web page)",
           "https://www.berlin.de/bevoelkerungsschutz/warnung-und-information/katastrophenschutz-leuchttuerme/", "file"),
    Source(KATL_FK, "Friedrichshain-Kreuzberg district: its Kat-Leuchttürme are not yet ready (saved web page)",
           "https://www.berlin.de/ba-friedrichshain-kreuzberg/politik-und-verwaltung/service-und-organisationseinheiten/katastrophenschutz/artikel.1663279.php", "file"),
    Source(BWB_PUMPS, "Berliner Wasserbetriebe: Notbrunnen, how many work and whether the water is drinkable (saved web page)",
           "https://www.bwb.de/de/notbrunnen.php", "file"),
    Source(CDC_WATER, "CDC: Make water safe during an emergency, 2022 (US government work, public domain)",
           "https://www.cdc.gov/water-emergency/media/pdfs/make-water-safe-during-emergency-p.pdf", "repo"),
    Source(RBB_JAN, "rbb24: live blog of the January 2026 blackout in south-west Berlin; water and sewage kept running (saved web page)",
           "https://www.rbb24.de/panorama/beitrag/2026/01/berlin-sueden-massiver-stromausfall-nikolassee-zehlendorf-wannsee-lichterfelde-verteilerstation-brand.html", "file"),
    Source(ERC, "European Resuscitation Council: Guidelines 2025 for Everyone, CPR and first aid for lay people",
           "https://www.erc.edu/media/p5ymaeej/gl2025_layperson_book_ipdf-v11-e.pdf", "file"),
    Source(MSF, "MSF (Doctors Without Borders): Essential Drugs, practical guidelines, January 2026. A reference for health workers",
           "https://medicalguidelines.msf.org/sites/default/files/pdf/guideline-339-en.pdf", "file"),
    Source(BBK_MEDS, "BBK: Medicine cabinet, what to keep at home (English web page saved as PDF)",
           "https://www.bbk.bund.de/EN/Prepare-for-disasters/Personal-Preparedness/Medicine-cabinet/medicine-cabinet_node.html", "print"),
    Source(FDA_INSULIN, "US FDA: Insulin storage and switching between products in an emergency, Sept 2026 (US government work, public domain; web page saved as PDF)",
           "https://www.fda.gov/drugs/emergency-preparedness-drugs/information-regarding-insulin-storage-and-switching-between-products-emergency", "repo"),
    Source(CDC_CO, "CDC: Carbon monoxide, one-page danger flyer (US government work, public domain)",
           "https://www.cdc.gov/carbon-monoxide/media/pdfs/Flyer_Danger.pdf", "repo"),
    Source(FOOD_CHARTS, "FoodSafety.gov: Food safety during a power outage, fridge and freezer charts, 2024 (US government work, public domain; web page saved as PDF)",
           "https://www.foodsafety.gov/food-safety-charts/food-safety-during-power-outage", "repo"),
    Source(TWIN_BUCKET, "Portland-area RDPO (PublicAlerts): Emergency toilet, twin bucket system, one-page flyer",
           "https://drive.google.com/uc?export=download&id=1d2CTMeX4VzRDpvr06gUK_7ace_cWR97Z", "file", web="https://www.publicalerts.org/toilet"),
    Source(WHO_DWM, "WHO: Doing What Matters in Times of Stress, an illustrated guide, 2020",
           "https://iris.who.int/server/api/core/bitstreams/65bb7c04-5180-4773-a0d8-0d5be2cc35a0/content", "file",
           web="https://www.who.int/publications/i/item/9789240003927"),
    Source(CDC_DISAB, "CDC: Emergency kit and emergency plan for people with disabilities, 2024 (US government work, public domain; two web pages saved as one PDF)",
           "https://www.cdc.gov/disability-emergency-preparedness/people-with-disabilities/build-a-kit.html", "repo"),
    article("Bucket_toilet"),
    article("Emergency_sanitation"),
    article("Carbon_monoxide_poisoning"),
    article("Hypothermia"),
    article("Water_supply_and_purification_for_emergencies", site="Appropedia"),
]}

# (Kiwix folder, file name prefix, size note). The zimgit bundles of mixed-quality books were dropped for the
# one-per-topic documents in SOURCES; the knots bundle stays, because its knot guide exists nowhere else.
ZIMS = [
    ("zim/wikipedia/", "wikipedia_en_medicine_nopic", "1.0 GB, medical Wikipedia articles"),
    ("zim/wikipedia/", "wikipedia_en_top_nopic", "2.6 GB, the most read Wikipedia articles, no pictures"),
    ("zim/other/", "zimgit-knots_en", "30 MB, knots"),
]


class Guide:
    """Renders links for one mode: web (original sources and this repo) or local (files in lib)."""

    def __init__(self, mode, lib=None):
        assert mode in ("web", "local")
        assert (lib is None) == (mode == "web")
        self.mode, self.lib, self.missing = mode, lib, []

    def href(self, path, page=None):
        src = SOURCES[path]
        if src.get == "repo":
            assert (REPO / path).is_file(), f"missing repo file: {path}"
        if self.mode == "web":
            return src.web_href(page)
        if all((self.lib / f).is_file() for f in src.local_files()):
            return src.local_href(page)
        self.missing.append(path)
        print(f"WARNING: {path} is missing in {self.lib}; its links go online instead", file=sys.stderr)
        return src.web_href(page)

    def a(self, path, text, page=None):
        href = self.href(path, page)
        numbered = page and not isinstance(page, str) and "#page=" in href
        label = text + (f", PDF page {page}" if numbered else "")
        return f'<a href="{html.escape(href)}">{html.escape(label)}</a>'


def build_gpx():
    rows = list(csv.DictReader(open(REPO / "berlin/notbrunnen_osm.csv", encoding="utf-8"), delimiter=";"))
    assert len(rows) > 1000, f"expected about 2000 pumps, got {len(rows)}"
    pts = [f'<wpt lat="{float(r["@lat"]):.7f}" lon="{float(r["@lon"]):.7f}"><name>{html.escape(r.get("name") or "Notbrunnen (hand pump)")}</name></wpt>'
           for r in rows]
    gpx = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<gpx version="1.1" creator="berlin-outage-guide" xmlns="http://www.topografix.com/GPX/1/1">\n'
           '<metadata><copyright author="OpenStreetMap contributors"><license>https://opendatacommons.org/licenses/odbl/</license></copyright></metadata>\n'
           + "\n".join(pts) + "\n</gpx>\n")
    (REPO / "berlin/notbrunnen_hand_pumps.gpx").write_text(gpx, encoding="utf-8")
    return len(rows)


def meeting_points_table():
    rows = list(csv.DictReader(open(REPO / "berlin/kat_leuchttuerme.csv", encoding="utf-8"), delimiter=";"))
    assert len(rows) > 50, f"expected about 73 meeting points, got {len(rows)}"
    rows.sort(key=lambda r: (r["bezirk"], r["adresse"]))
    out = ["<table><tr><th>District</th><th>Address</th><th>Building</th><th>Type</th><th>Status</th></tr>"]
    for r in rows:
        kind = "Leuchtturm (staffed)" if "Leuchtturm" in r["art"] else "Info point"
        status = "ready" if "betriebsbereit" in r["status"] else "planned only"
        out.append("<tr>" + "".join(f"<td>{html.escape(v)}</td>" for v in
                   (r["bezirk"], r["adresse"], r["liegenschaft"], kind, status)) + "</tr>")
    out.append("</table>")
    return "\n".join(out), len(rows)


def references(g):
    out = ["<table><tr><th>File</th><th>What it is</th><th>Original source</th></tr>"]
    for path, src in SOURCES.items():
        out.append(f"<tr><td>{g.a(path, path)}</td><td>{html.escape(src.desc)}</td><td><code>{html.escape(src.url)}</code></td></tr>")
    out.append("</table>")
    return "\n".join(out)


HOW = {"file": "Download it (right click, Save link as).",
       "print": "Open it in a browser, choose Print, then Save as PDF."}


def manual_steps():
    """Walkthrough without the script. Built from SOURCES and ZIMS, so it always matches what the script gets."""
    rows = []
    for path, src in SOURCES.items():
        if src.get == "repo":
            continue
        if src.get == "hesperian":
            links = "<br>\n".join(f'<a href="{html.escape(u)}">{html.escape(u.rsplit("/", 1)[1])}</a>' for u in src.parts)
            rows.append(f"<tr><td><code>{CHAPTERS}</code></td><td>Download all {len(src.parts)} chapter PDFs "
                        f"(right click, Save link as):<details><summary>chapter list</summary>{links}</details></td></tr>")
        else:
            site = src.url.split("/")[2]
            rows.append(f'<tr><td><code>{html.escape(path)}</code></td><td><a href="{html.escape(src.url)}">{html.escape(site)}</a>. {HOW[src.get]}</td></tr>')
    zims = "\n".join(f'<li><code>{p}_YYYY-MM.zim</code> ({html.escape(n)}): the newest one in '
                     f'<a href="{KIWIX}{f}">{KIWIX}{f}</a></li>' for f, p, n in ZIMS)
    return f"""<ol>
<li>Get this repo: on <a href="{REPO_URL}">its GitHub page</a> click Code, then Download ZIP. Unzip it.
The files that may be shared are already in it.</li>
<li>Save each file below into the unzipped folder, with exactly the folder and name shown:
<table><tr><th>Save as</th><th>Where and how</th></tr>
{chr(10).join(rows)}
</table></li>
<li>Install the Kiwix reader for your system from <a href="https://kiwix.org/en/applications/">kiwix.org/en/applications</a>.</li>
<li>Download the offline wikis (ZIM files) into the folder. Open them with Kiwix.
<ul>
{zims}
</ul></li>
<li>Open the guide pages in the folder: this guide links to the original websites; the files sit next to it.
(With Python installed, <code>python3 outage_guide.py local .</code> in the folder writes <code>START_HERE.html</code>, which links to the files.)</li>
</ol>"""


def by_topic(g):
    """One best document per need, so nobody has to wade through the library in a hurry."""
    a = g.a
    rows = [("Illness and injury", a(WTND, "Where There Is No Doctor")),
            ("CPR and first aid", a(ERC, "ERC Guidelines for Everyone")),
            ("Teeth", a(DENT, "Where There Is No Dentist")),
            ("Medicines and doses (for health workers)", a(MSF, "MSF Essential Drugs")),
            ("What medicines to keep at home", a(BBK_MEDS, "BBK Medicine cabinet")),
            ("Insulin", a(FDA_INSULIN, "FDA insulin storage and switching")),
            ("Stocking up before an outage", f'{a(BBK_EN, "BBK checklist", 34)}; German: {a(BBK_DE, "BBK Stromausfall")}'),
            ("Carbon monoxide", a(CDC_CO, "CDC carbon monoxide flyer")),
            ("Making water safe", a(CDC_WATER, "CDC Make water safe")),
            ("Food after a power cut", a(FOOD_CHARTS, "FoodSafety.gov fridge and freezer charts")),
            ("Toilet when you cannot flush", a(TWIN_BUCKET, "Twin bucket toilet flyer")),
            ("Stress and anxiety", a(WHO_DWM, "WHO Doing What Matters in Times of Stress")),
            ("People with disabilities", a(CDC_DISAB, "CDC kit and plan for people with disabilities")),
            ("Knots", "the knots ZIM file, in Kiwix")]
    return ("<h3>One document per topic</h3>\n<table><tr><th>Need</th><th>Document</th></tr>\n"
            + "\n".join(f"<tr><td>{html.escape(n)}</td><td>{d}</td></tr>" for n, d in rows) + "\n</table>")


def library_section(g):
    if g.mode == "web":
        return f"""<p>This guide links to its sources online. Files that may be shared are in <a href="{REPO_URL}">the repo</a>.
To have every source offline, plus offline wikis with a reader, use the script or the steps by hand.</p>
<h3>With the script (Linux)</h3>
<ol>
<li>Install <code>git</code>, <code>curl</code> and <code>python3</code>. Chrome or Chromium saves one web page as PDF.</li>
<li>Run <code>git clone {REPO_URL}</code></li>
<li>Run <code>berlin-outage-guide/download.sh</code>. It downloads about 3.8 GB into the cloned folder.
To use another folder, give it as the first argument.</li>
<li>Open <code>berlin-outage-guide/START_HERE.html</code>. It is this guide, with every link going to a file on your disk.</li>
</ol>
<h3>By hand (Windows, Mac, or without the script)</h3>
{manual_steps()}
{by_topic(g)}"""
    appimages = sorted(p.name for p in g.lib.glob("kiwix-desktop*.appimage"))
    reader = (f"run <code>{html.escape(appimages[-1])}</code> in this folder and open the <code>.zim</code> files with it"
              if appimages else 'install the free <a href="https://kiwix.org/en/applications/">Kiwix app</a> and open the <code>.zim</code> files with it')
    zims = ", ".join(sorted(p.name for p in g.lib.glob("*.zim"))) or "none downloaded"
    serve = ('<li>Search across all ZIM files: <code>kiwix-tools/kiwix-serve --port 8181 *.zim</code>, then open '
             '<code>http://127.0.0.1:8181</code> in a browser. This works offline.</li>\n'
             if (g.lib / "kiwix-tools/kiwix-serve").is_file() else "")
    return f"""<ul>
<li>Offline wikis (ZIM files): {html.escape(zims)}.</li>
<li>Open the reader: {reader}.</li>
{serve}<li>Useful searches: carbon monoxide poisoning, hypothermia, water purification, oral rehydration therapy, first aid, food preservation, solar cooker, rainwater harvesting.</li>
<li>Where There Is No Doctor and Where There Is No Dentist are chapter PDFs in <code>{CHAPTERS}</code>.</li>
</ul>
{by_topic(g)}"""


def download_box(g):
    if g.mode != "web":
        return ""
    base = f"{REPO_URL}/releases/download/{PACK_TAG}/"
    return f"""<div class="box"><b>Download it all for offline use (no account or tools needed)</b>
<ul>
<li><a href="{base}{PACKS['mini']}">Mini pack</a> (about 15 MB zip): this guide, the offline map, the pump list and every
document that may be shared. Unzip it and open <code>START_HERE.html</code>. Fits on any phone.</li>
<li><a href="{base}{PACKS['full']}">Full pack</a> (about 1.1 GB zip): the mini pack plus medical Wikipedia, which you read with
the free <a href="https://kiwix.org/en/applications/">Kiwix app</a>. For the most-read Wikipedia articles as well (2.6 GB),
use the Kiwix app's own library: search "wikipedia top".</li>
</ul>
<p class="src">Some books (Where There Is No Doctor, MSF, ERC, WHO, BBK) may not be republished, so the packs link to them instead.
Download those from the publishers while you are online.</p></div>"""


def body(g, n_pumps, table, n_points):
    a = g.a
    b = lambda p, t="BBK guide": a(BBK_EN, t, p)
    fema = "emergency_guides/FEMA_Power_Outage_sheet.pdf"
    cdc = "emergency_guides/CDC_Keep_Food_Safe_After_Disaster.pdf"
    strom = "berlin/berlin_stromausfall.html"
    rufn = "berlin/berlin_rufnummern.html"
    where = ("Links open the original sources online, or the copy in this repo. The last section shows how to get all of them offline."
             if g.mode == "web" else "Links open the files in this folder. A document that is not in the folder links to its website instead.")
    return f"""<h1>Power outage in Berlin: first minutes to first week</h1>
<p class="warn"><b>Claude Opus 5.5, an AI model, wrote this entire guide. I am not responsible for your safety.
Treat it as an unreviewed guide that may be incorrect. Still, it is better than nothing.
I also put together a large offline library: the last section shows how to download it, with a script on Linux or by hand.</b></p>
{download_box(g)}
<p><b>Offline map:</b> {a(MAP, "berlin_emergency_map.html")} shows every Kat-Leuchtturm, hand pump, hospital, fire and police station in Berlin.
It is one file that opens in any browser without internet or an app. Save it to your phone now. Press and hold a spot to list the nearest of each.</p>
<p class="src">Written 22 Nov 2026 for a 4th floor flat, two or three adults, radiator heating. {where}
Grey "Source" lines name the document that backs each step.</p>

<div class="box"><b>Supplies to keep (28 person-days)</b>
<p>A person-day is what one person needs for one day. 28 person-days is <b>2 adults for 14 days</b>, or <b>3 adults for about 9 days</b>.
BBK advises at least 10 days, and at least 3 days as a start. <span class="src">Source: {b(8)}</span></p>
<ul>
<li>Drinking water: 2 litres per person per day, so <b>56 litres</b> in total. <span class="src">Source: {b(9)}</span></li>
<li>Durable food that needs no fridge, for 28 person-days. <span class="src">Source: {b(34, "BBK checklist")}</span></li>
<li>Radio (battery, solar or crank), charged power bank, torch and spare batteries, numbers on paper, warm clothes, sleeping bags, cash. <span class="src">Source: {b(34, "BBK checklist")}</span></li>
<li>Small solar panel (USB) to recharge the power bank and phone. <span class="src">No source: common practice. BBK lists only a charged power bank.</span></li>
<li>Medicine cabinet: personal medication, painkillers, thermometer, diarrhoea remedies, electrolytes, plasters. <span class="src">Source: {b(35, "BBK checklist")}</span></li>
</ul></div>

<div class="box"><b>Numbers and radio (write these on paper too)</b>
<ul>
<li>Fire brigade and ambulance: <b>112</b>. Police: <b>110</b>. <span class="src">Source: {a(rufn, "berlin.de Rufnummern")}</span></li>
<li>If 112 and 110 do not work: go to the nearest police station, fire station or open Kat-Leuchtturm (list below). <span class="src">Source: {b(15)}; {a(strom, "berlin.de Stromausfall")}</span></li>
<li>Medical on-call service (not an emergency): <b>116117</b>. <span class="src">Source: {a("berlin/116117.html", "116117.de")}</span></li>
<li>Poison line Berlin: <b>030 19240</b>. <span class="src">Source: {a("berlin/giftnotruf.html", "Giftnotruf Charité")}</span></li>
<li>Police non-emergency line: <b>030 4664 4664</b>. <span class="src">Source: {a(rufn, "berlin.de Rufnummern")}</span></li>
<li>Stromnetz Berlin power fault line: <b>0800 211 2525</b> (free). <span class="src">Source: stromnetz.berlin/kontakt, read on 21 Nov 2026. The site was offline on 22 Nov, so there is no saved copy.</span></li>
<li>Radio (FM): <b>rbb 88.8 on 88.8 MHz</b>, <b>rbb24 Inforadio on 93.1 MHz</b>, radioeins 95.8 MHz. DAB+: channel 7D. A battery, solar or crank radio works when phone networks fail. <span class="src">Source: {a("berlin/rbb_frequenzen.html", "rbb frequencies")}; {b(15)}; radio types: {b(34, "BBK checklist")}</span></li>
</ul></div>

<h2>First minutes</h2>
<ol>
<li>Check if only your flat is dark. Look at the stairwell light and the windows across the street. If only your flat is dark, check your fuse box (Sicherungskasten). <span class="src">No source: common practice.</span></li>
<li>Use a torch or battery light. Do not heat with candles or a gas stove. <span class="src">Source: {a(fema, "FEMA power outage sheet", 2)}</span></li>
<li>Unplug appliances and electronics. A power surge can damage them when the power comes back. <span class="src">Source: {a(fema, "FEMA power outage sheet", 1)}</span></li>
<li>Leave one lamp switched on, so you see when power returns. <span class="src">No source: common practice.</span></li>
<li>Keep the fridge and freezer closed. <span class="src">Source: {a(cdc, "CDC food safety", 1)}</span></li>
<li>Fill water now, while the tap still runs: bathtub, pots, bottles. Waterworks need electricity; without it the water supply fails. <span class="src">Source: {b(6)}; amounts on {b(9)}</span></li>
<li>Switch the phone to power saving mode. Send one short message to the people who need to know you are fine. Networks can fail soon after the power. <span class="src">Source: {b(14)}</span></li>
<li>Turn on the radio for official information. <span class="src">Source: {b(15)}</span></li>
<li>Do not use the lift. If you are stuck in a lift, press its alarm button. <span class="src">Source: lift use, {b(32)}; alarm button: no source, common practice.</span></li>
</ol>

<h2>First hour</h2>
<ol>
<li>Expect the radiators to go cold. Central heating needs electricity for its pumps and controls. <span class="src">Source: {b(25)}</span></li>
<li>Dress warm. Pick one room, close its doors, stay there. Air it briefly (a few minutes, window wide open) a few times a day. <span class="src">Source: {b(25)}; {a(strom, "berlin.de Stromausfall")}</span></li>
<li><b>Never use a camping stove, barbecue, generator or gas heater indoors unless it is approved for indoor use.</b> Carbon monoxide kills without warning. Its signs are flu-like symptoms: headache, dizziness, weakness, nausea. If a CO alarm sounds or someone gets these signs near a stove or heater, get everyone out into fresh air and call 112. A battery CO alarm is a good buy. <span class="src">Source: {b(24)}; {a(fema, "FEMA power outage sheet", 2)}; {a("library_extracts/Wikipedia_Carbon_monoxide_poisoning.pdf", "Wikipedia: Carbon monoxide poisoning")}</span></li>
<li>Cooking indoors without power: tea light warmer or fondue set for small amounts. Camping gas stoves: outdoors only. <span class="src">Source: {b(24)}</span></li>
<li>Get cash if a shop or bank still works. ATMs and card payment fail without power. <span class="src">Source: {b(7)}; {a(strom, "berlin.de Stromausfall")}</span></li>
<li>Tell your neighbours what you know. Not everyone gets the warnings. <span class="src">Source: {b(13)}</span></li>
<li>Find your nearest Kat-Leuchtturm in the list below or on the {a(MAP, "offline map")}. Note its address on paper. <span class="src">Source: {a("berlin/Berlin_Flyer_Gefahrenlagen.pdf", "Berlin flyer")}</span></li>
</ol>

<h2>First day</h2>
<ol>
<li>Kat-Leuchttürme: they are not open all the time. In a long outage each district decides whether, where and for how long to open them, and the opening is announced separately: listen to the radio. Some listed sites are not ready yet (both in Friedrichshain-Kreuzberg, for example). An open one takes emergency calls and gives news, official advice and information about supplies and help. Charging, medical care, food and beds are not on that list, so do not count on them. <span class="src">Source: {a(KATL, "berlin.de Kat-Leuchttürme")}; {a(KATL_FK, "Friedrichshain-Kreuzberg Kat-Leuchttürme")}; services: {a("berlin/kat_leuchttuerme.csv", "kat_leuchttuerme.csv")}</span></li>
<li>Food: a closed fridge keeps food safe for 4 hours. A full closed freezer: 48 hours; half full: 24 hours. Eat the fridge food first. If in doubt, throw it out. <span class="src">Source: {a(cdc, "CDC food safety", 1)}</span></li>
<li>Toilet: if the drains still work, flush with a bucket of water you do not need for drinking when the cistern stays empty, and flush sparingly. In the January 2026 blackout the sewers kept working, partly on emergency generators. If sewage backs up, the drains stop working, or you are told not to flush, stop using the toilet. Seal it shut. Use a bucket with a plastic bag and a lid. Put dry material in the bottom (sawdust, newspaper, leaves). Cover each use with sawdust, ash or similar. Use a second bucket for urine if you can. <span class="src">Source: flushing with a bucket: no source, common practice; January 2026: {a(RBB_JAN, "rbb24, 16:53 entry")}; buckets: {a("library_extracts/Wikipedia_Bucket_toilet.pdf", "Wikipedia: Bucket toilet")}; {a("emergency_guides/Emergency_Toilets_WREMO.pdf", "WREMO emergency toilets")}; {a("library_extracts/Wikipedia_Emergency_sanitation.pdf", "Wikipedia: Emergency sanitation")}</span></li>
<li>Water: drink your stored water first. If the taps are dry, Berlin has street hand pumps (Notbrunnen), but about 30% of them do not work (1,394 of 1,976 working), and Berliner Wasserbetriebe says they do not deliver drinking water. Follow the authorities' instructions on which pumps to use and how to treat the water. Boiling clear water for 1 minute kills germs; disinfecting tablets used as directed kill most of them. Neither removes chemicals: if the water may be chemically contaminated, do not drink it. The pumps are on the {a(MAP, "offline map")}, and {n_pumps} are in {a("berlin/notbrunnen_hand_pumps.gpx", "notbrunnen_hand_pumps.gpx")} for Organic Maps or OsmAnd. <span class="src">Source: pumps working and water quality: {a(BWB_PUMPS, "Berliner Wasserbetriebe Notbrunnen")}; boiling, tablets and chemicals: {a(CDC_WATER, "CDC Make water safe", 1)}; pump locations from OpenStreetMap, © OpenStreetMap contributors, ODbL ({a("berlin/notbrunnen_osm.csv", "CSV")})</span></li>
<li>Night: light and warmth. Sleep in clothes and a sleeping bag in your warm room. Know the signs of hypothermia (shivering, then confusion). <span class="src">Source: {a(WTND, "Where There Is No Doctor, emergencies caused by cold", "cold")}; {a("library_extracts/Wikipedia_Hypothermia.pdf", "Wikipedia: Hypothermia")}</span></li>
<li>Fire: leave the room, close the door, leave the building, call 112. <span class="src">Source: {b(26)}</span></li>
</ol>

<h2>First week</h2>
<ol>
<li>Drink at least 2 litres per person per day (0.5 litres of that for cooking). For 2 adults the 56 litre stock lasts 14 days; for 3 adults it lasts about 9 days. Count what is left on day 3 and day 7. <span class="src">Source: {b(9)}</span></li>
<li>Diarrhoea: drink rehydration drink. 1 litre clean water, half a level teaspoon of salt, 8 level teaspoons of sugar. It must taste less salty than tears. <span class="src">Source: {a(WTND, "Where There Is No Doctor, rehydration drink", "rehydration")}</span></li>
<li>Stress and fear in you or others: the WHO guide shows how to help people calmly. <span class="src">Source: {a("emergency_guides/WHO_Psychological_First_Aid_2011.pdf", "WHO Psychological first aid")}</span></li>
<li>Chemical or gas release outside: go inside, close windows and doors, go to upper floors. Radiation or explosions: go low, into inner rooms or the cellar, away from windows. <span class="src">Source: {b(30)}; {b(32)}</span></li>
<li>For anything else, use the offline library (last section). After one week you are in a different situation; this guide stops here.</li>
</ol>

<h2>Kat-Leuchttürme and info points ({n_points})</h2>
<p class="src">"Planned only" locations may not open. Source: {a("berlin/kat_leuchttuerme.csv", "kat_leuchttuerme.csv")}, from berlin.de, Nov 2026.</p>
<div class="scroll">{table}</div>

<h2>References</h2>
<div class="scroll">{references(g)}</div>

<h2>The offline library</h2>
{library_section(g)}
"""


STYLE = """:root { --fg:#111; --bg:#fff; --muted:#555; --line:#ccc; --box:#f3f3f3; --warn:#fff3cd; }
@media (prefers-color-scheme: dark) { :root { --fg:#eee; --bg:#151515; --muted:#aaa; --line:#444; --box:#222; --warn:#3a3000; } a { color:#8cf; } }
body { font: 16px/1.45 system-ui, sans-serif; color:var(--fg); background:var(--bg); max-width:52rem; margin:0 auto; padding:1rem; }
h1 { margin-bottom:.2rem; } h2 { border-top:2px solid var(--fg); padding-top:.6rem; margin-top:2rem; }
.src { color:var(--muted); font-size:.9em; }
.box { background:var(--box); border:1px solid var(--line); padding:.6rem 1rem; margin:.8rem 0; }
.warn { background:var(--warn); border:2px solid var(--fg); padding:.6rem 1rem; }
.scroll { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:.9em; } td,th { border:1px solid var(--line); padding:.25rem .4rem; text-align:left; vertical-align:top; }
code { word-break:break-all; }
@media print { a { color:inherit; } }"""


def html_page(content):
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Berlin Outage Guide</title>
<style>
{STYLE}
</style></head><body>
{content}</body></html>
"""


def render(mode, lib=None):
    n_pumps = build_gpx()
    table, n_points = meeting_points_table()
    g = Guide(mode, lib)
    return body(g, n_pumps, table, n_points), g.missing


def gitignore():
    """Files download.sh puts into this folder, so a clone stays clean after a download."""
    downloaded = sorted({s.path for s in SOURCES.values() if s.get not in ("repo", "hesperian")})
    return "\n".join(["# Generated by outage_guide.py build", "__pycache__/", "*.part", "*.part.*", "_chrome_profile/",
                      "START_HERE.html", "*.zim", "*.appimage", "kiwix-tools/", CHAPTERS, *downloaded]) + "\n"


def build_web():
    content, missing = render("web")
    assert not missing
    (REPO / "index.html").write_text(html_page(content), encoding="utf-8")
    # GitHub shows README.md as the repo front page; it drops the <style>, the text stays the same.
    (REPO / "README.md").write_text("<!-- Generated by outage_guide.py build. Edit that file, not this one. -->\n" + content,
                                    encoding="utf-8")
    (REPO / ".gitignore").write_text(gitignore(), encoding="utf-8")
    print("wrote index.html, README.md, .gitignore")


def build_local(lib):
    if lib != REPO:
        for path, src in SOURCES.items():
            if src.get == "repo":
                (lib / path).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(REPO / path, lib / path)
    content, missing = render("local", lib)
    out = lib / "START_HERE.html"
    out.write_text(html_page(content), encoding="utf-8")
    print(f"wrote {out}")
    if missing:
        print(f"WARNING: {len(set(missing))} sources missing, linked online instead: {sorted(set(missing))}", file=sys.stderr)
    return missing


# ---------- download ----------

def check(path):
    """Fails loudly when a downloaded file is empty or of the wrong type."""
    assert path.is_file() and path.stat().st_size > 0, f"empty download: {path}"
    head = path.open("rb").read(5)
    if path.suffix == ".pdf":
        assert head == b"%PDF-", f"not a PDF: {path}"
    elif path.suffix == ".zim":
        assert head[:4] == b"ZIM\x04", f"not a ZIM file: {path}"
    elif path.suffix == ".html":
        assert path.stat().st_size > 500, f"page too small, probably an error page: {path}"


def curl(url, dest, resume=False):
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    cmd = ["curl", "-fL", "--retry", "3", "--connect-timeout", "30", "-A", UA, "-o", str(part), url]
    if resume:
        cmd[1:1] = ["-C", "-"]
    subprocess.run(cmd, check=True)
    return part


def fetch(url, dest):
    tmp = dest.with_name(dest.stem + ".part" + dest.suffix)  # keeps the suffix, so check() knows the type
    curl(url, dest).rename(tmp)
    try:
        check(tmp)
    except AssertionError:
        tmp.unlink()
        raise
    tmp.rename(dest)


def listing(url):
    return subprocess.run(["curl", "-fsL", "-A", UA, url], check=True, capture_output=True, text=True).stdout


def find_chrome():
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if shutil.which(name):
            return name
    return None


def print_page(url, dest, lib):
    chrome = find_chrome()
    assert chrome, "Chrome or Chromium is needed to save web pages as PDF; install one and run again"
    profile = lib / "_chrome_profile"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.stem + ".part.pdf")
    try:
        subprocess.run([chrome, "--headless", "--disable-gpu", "--no-pdf-header-footer", f"--user-data-dir={profile}",
                        f"--print-to-pdf={tmp}", url], check=True, timeout=180, capture_output=True)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    check(tmp)
    tmp.rename(dest)


def get_source(src, lib):
    if src.get == "file":
        fetch(src.url, lib / src.path)
    elif src.get == "print":
        print_page(src.url, lib / src.path, lib)
    elif src.get == "hesperian":
        for url in src.parts:
            if not (lib / chapter_path(url)).is_file():
                fetch(url, lib / chapter_path(url))
    else:
        raise AssertionError(f"unknown download method {src.get}")


def latest(folder_url, pattern):
    names = set(re.findall(pattern, listing(folder_url)))
    assert names, f"nothing matches {pattern} at {folder_url}"
    return max(names, key=lambda n: [int(x) for x in re.findall(r"\d+", n)])


def sha256_ok(path, url):
    try:
        want = listing(url + ".sha256").split()[0]
    except subprocess.CalledProcessError:
        print(f"WARNING: no checksum published for {url}; checked the file type only", file=sys.stderr)
        return True
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest() == want


def get_zim(folder, prefix, lib):
    if any(p.is_file() for p in lib.glob(f"{prefix}_*.zim")):
        print(f"have {prefix}, skipped")
        return
    name = latest(KIWIX + folder, rf'"({re.escape(prefix)}_\d{{4}}-\d{{2}}\.zim)"')
    url, dest = KIWIX + folder + name, lib / name
    part = curl(url, dest, resume=True)
    assert part.open("rb").read(4) == b"ZIM\x04", f"not a ZIM file: {url}"
    assert sha256_ok(part, url), f"checksum mismatch, delete {part} and run again: {url}"
    part.rename(dest)


def get_kiwix(lib):
    if not list(lib.glob("kiwix-desktop*.appimage")):
        name = latest(KIWIX + "release/kiwix-desktop/", r'"(kiwix-desktop_x86_64_\d+\.\d+\.\d+\.appimage)"')
        dest = lib / name
        curl(KIWIX + "release/kiwix-desktop/" + name, dest).rename(dest)
        dest.chmod(0o755)
    tools = lib / "kiwix-tools"
    if not (tools / "kiwix-serve").is_file():
        name = latest(KIWIX + "release/kiwix-tools/", r'"(kiwix-tools_linux-x86_64-musl-\d+\.\d+\.\d+\.tar\.gz)"')
        tgz = tools / name
        curl(KIWIX + "release/kiwix-tools/" + name, tgz).rename(tgz)
        with tarfile.open(tgz) as tar:
            for m in tar.getmembers():
                base = Path(m.name).name
                if m.isfile() and base in ("kiwix-serve", "kiwix-manage", "kiwix-search"):
                    (tools / base).write_bytes(tar.extractfile(m).read())
                    (tools / base).chmod(0o755)
        assert (tools / "kiwix-serve").is_file(), f"kiwix-serve not found in {name}"


def download(lib):
    lib.mkdir(parents=True, exist_ok=True)
    failed = []

    def attempt(label, fn, *args):
        try:
            fn(*args)
            print(f"ok   {label}")
        except (AssertionError, subprocess.SubprocessError, OSError) as e:
            failed.append(label)
            print(f"FAIL {label}: {e}", file=sys.stderr)

    for path, src in SOURCES.items():
        if src.get == "repo" or all((lib / f).is_file() for f in src.local_files()):
            continue
        attempt(path, get_source, src, lib)
    attempt("Kiwix reader and kiwix-serve", get_kiwix, lib)
    for folder, prefix, note in ZIMS:
        attempt(f"{prefix} ({note})", get_zim, folder, prefix, lib)
    missing = build_local(lib)
    if failed or missing:
        print(f"\n{len(failed)} downloads FAILED: {failed}\nRun the script again to retry them.", file=sys.stderr)
        sys.exit(1)
    print(f"\nDone. Open {lib / 'START_HERE.html'}")


def zip_folder(folder, out):
    """Zips folder as one top-level folder. PDFs and ZIMs are already compressed, so they are stored as they are."""
    tmp = out.with_name(out.name + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(folder.rglob("*")):
            if f.is_file():
                kind = zipfile.ZIP_STORED if f.suffix in (".pdf", ".zim") else zipfile.ZIP_DEFLATED
                z.write(f, Path(folder.name) / f.relative_to(folder), compress_type=kind)
    tmp.rename(out)
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")


def pack(out, full):
    """The release zips: no download step and no tools needed, for people who just want the files."""
    out.mkdir(parents=True, exist_ok=True)
    folder = out / "berlin-outage-guide"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir()
    build_local(folder)  # copies every file this repo may share, links the rest online
    zip_folder(folder, out / PACKS["mini"])
    assert (out / PACKS["mini"]).stat().st_size < 30e6, "the mini pack should stay under 30 MB"
    if full:
        for f, prefix in FULL_ZIMS:
            get_zim(f, prefix, folder)
        build_local(folder)  # lists the ZIM files now present
        zip_folder(folder, out / PACKS["full"])
    shutil.rmtree(folder)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    sub.add_parser("local").add_argument("dir", type=Path)
    d = sub.add_parser("download")
    d.add_argument("dir", type=Path, nargs="?", default=REPO)
    pk = sub.add_parser("pack")
    pk.add_argument("out", type=Path)
    pk.add_argument("--full", action="store_true", help="also build the full pack with medical Wikipedia (downloads 1 GB)")
    args = ap.parse_args()
    if args.cmd == "build":
        build_web()
    elif args.cmd == "local":
        build_local(args.dir.expanduser().resolve())
    elif args.cmd == "pack":
        pack(args.out.expanduser().resolve(), args.full)
    else:
        download(args.dir.expanduser().resolve())


if __name__ == "__main__":
    main()
