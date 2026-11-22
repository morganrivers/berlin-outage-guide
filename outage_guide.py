#!/usr/bin/env python3
"""Berlin power outage guide: one source table drives the web page, the offline page and the downloader.

  python3 outage_guide.py web                               -> index.html, links go to the original sources
  python3 outage_guide.py local DIR                         -> DIR/START_HERE.html, links go to files in DIR
  python3 outage_guide.py download DIR [--with-wikipedia]   -> download the library into DIR, then build local
"""
import argparse
import csv
import hashlib
import html
import io
import re
import shutil
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(__file__).resolve().parent
DATA = REPO / "data"
REPO_URL = "https://github.com/morganrivers/berlin-outage-guide"
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
KIWIX = "https://download.kiwix.org/"
HESP = "https://hesperian.org/wp-content/uploads/pdf/"
HESP_INDEX = "https://languages.hesperian.org/pages/en/pdf.html"


def hesperian_chapters(book, parts):
    return tuple(f"{HESP}{book}/{book}_{p}.pdf" for p in parts)


WTND_CHAPTERS = hesperian_chapters(
    "en_wtnd_2025", ["fm", "toc", "int", *[f"{i:02d}" for i in range(1, 24)], "gp", "info", "gloss", "resc", "indx", "bm"])
DENT_CHAPTERS = hesperian_chapters("en_dent_2024", ["fm", *[f"{i:02d}" for i in range(1, 13)], "bm"])


@dataclass(frozen=True)
class Source:
    path: str        # path inside the offline library folder
    desc: str
    url: str         # original source: shown in the references, linked on the web page, downloaded
    get: str         # file | print | text | hesperian | repo
    web: str = ""    # web page link when it differs from url
    parts: tuple = ()          # chapter URLs for get="hesperian"
    page_map: tuple = ()       # (merged page, chapter URL, chapter page) for web links into merged books

    def web_href(self, page=None):
        for merged, url, chapter_page in self.page_map:
            if merged == page:
                return f"{url}#page={chapter_page}"
        base = self.web or self.url
        return base + (f"#page={page}" if page and base.endswith(".pdf") else "")


def wiki(title, path_name, site="Wikipedia"):
    base = "https://en.wikipedia.org/wiki/" if site == "Wikipedia" else "https://www.appropedia.org/"
    return Source(f"library_extracts/{site}_{path_name}.pdf",
                  f"{site} article {title.replace('_', ' ')} (CC BY-SA), saved as PDF", base + title, "print")


BBK_EN = "emergency_guides/BBK_Planning_ahead_for_crises_and_disasters_2025_EN.pdf"
WTND = "Where_There_Is_No_Doctor_2025.pdf"
SOURCES = {s.path: s for s in [
    Source(BBK_EN, "BBK (German federal civil protection office): Planning ahead for crises and disasters, English, Nov 2025",
           "https://www.ettlingen.de/site/Ettlingen-2021/get/params_E1441149346_Dattachment/3856928/BBK-Planning-ahead-for-crises-and-disasters_English%20November%202025.pdf", "file"),
    Source("emergency_guides/BBK_Stromausfall_Vorsorge_und_Selbsthilfe_DE.pdf", "BBK: Stromausfall, Vorsorge und Selbsthilfe (German, older)",
           "https://epflicht.ulb.uni-bonn.de/download/pdf/786332", "file"),
    Source("emergency_guides/FEMA_Power_Outage_sheet.pdf", "FEMA: Be prepared for a power outage, V-1008, Dec 2023",
           "https://www.cpp.edu/em/documents/power-outage-safety-tips-readygov-pdf.pdf", "file"),
    Source("emergency_guides/FEMA_Are_You_Ready_2020.pdf", "FEMA: Are You Ready? (US citizen preparedness guide; US government work, public domain, copy in this repo because ready.gov blocks scripts)",
           "https://www.ready.gov/sites/default/files/2021-03/are-you-ready-guide.pdf", "repo"),
    Source("emergency_guides/CDC_Keep_Food_Safe_After_Disaster.pdf", "CDC: Keep food safe after a disaster or emergency (web page saved as PDF)",
           "https://www.cdc.gov/food-safety/foods/keep-food-safe-after-emergency.html", "print"),
    Source("emergency_guides/Emergency_Toilets_WREMO.pdf", "WREMO (Wellington, NZ): Emergency toilets, two bucket method (web page saved as PDF; its folded question and answer sections do not print)",
           "https://www.wremo.nz/get-ready/home-ready/emergency-toilets", "print"),
    Source("emergency_guides/WHO_Psychological_First_Aid_2011.pdf", "WHO: Psychological first aid, guide for field workers, 2011",
           "https://mhpsshub.org/wp-content/uploads/2018/10/9789241548205_eng.pdf", "file"),
    Source(WTND, "Hesperian: Where There Is No Doctor, 2025 (chapter PDFs merged into one)", HESP + "en_wtnd_2025/", "hesperian",
           web=HESP_INDEX, parts=WTND_CHAPTERS,
           page_map=((199, WTND_CHAPTERS[15], 2), (457, WTND_CHAPTERS[27], 10))),
    Source("Where_There_Is_No_Dentist_2024.pdf", "Hesperian: Where There Is No Dentist, 2024 (chapter PDFs merged into one)",
           HESP + "en_dent_2024/", "hesperian", web=HESP_INDEX, parts=DENT_CHAPTERS),
    Source("berlin/Berlin_Flyer_Gefahrenlagen.pdf", "Berlin Senate: flyer, information for the public in dangerous situations (German)",
           "https://www.berlin.de/bevoelkerungsschutz/warnung-und-information/druckdatei-flyer---informationen-und-hinweise-fuer-die-bevoelkerung-bei-gefahrenlagen.pdf", "file"),
    Source("berlin/berlin_stromausfall.txt", "berlin.de: Vorsorge und Verhalten bei Stromausfall (text copy; .html copy next to it)",
           "https://www.berlin.de/katastrophenschutz/notfallvorsorge/stromausfall/artikel.1653256.php", "text"),
    Source("berlin/berlin_rufnummern.txt", "berlin.de: Wichtige Rufnummern (text copy; .html copy next to it)",
           "https://www.berlin.de/katastrophenschutz/service/wichtige-rufnummern/wichtige-rufnummern-1279430.php", "text"),
    Source("berlin/rbb_frequenzen.html", "rbb: radio frequencies (saved web page)", "https://www.rbb-online.de/radio/frequenzen/", "file"),
    Source("berlin/giftnotruf.txt", "Giftnotruf Berlin, Charité (poison line, text copy)", "https://giftnotruf.charite.de/", "text"),
    Source("berlin/116117.txt", "116117: medical on-call service (text copy)", "https://www.116117.de/de/index.php", "text"),
    Source("berlin/kat_leuchttuerme.csv", "berlin.de: Katastrophenschutz-Leuchttürme, all 73 locations (CSV export, Nov 2026)",
           "https://www.berlin.de/bevoelkerungsschutz/warnung-und-information/anlaufstellen-fuer-die-bevoelkerung/", "repo",
           web="data/kat_leuchttuerme.csv"),
    Source("berlin/notbrunnen_osm.csv", "Manual water pumps in Berlin, © OpenStreetMap contributors, ODbL licence (Overpass export, CSV, Nov 2026)",
           "https://www.openstreetmap.org/copyright", "repo", web="data/notbrunnen_osm.csv"),
    Source("berlin/notbrunnen_hand_pumps.gpx", "Same pumps as GPX for offline map apps, made from the CSV above (© OpenStreetMap contributors, ODbL)",
           "https://www.openstreetmap.org/copyright", "repo", web="data/notbrunnen_hand_pumps.gpx"),
    wiki("Bucket_toilet", "Bucket_toilet"),
    wiki("Emergency_sanitation", "Emergency_sanitation"),
    wiki("Carbon_monoxide_poisoning", "Carbon_monoxide_poisoning"),
    wiki("Hypothermia", "Hypothermia"),
    wiki("Water_supply_and_purification_for_emergencies", "Water_supply_and_purification_for_emergencies", site="Appropedia"),
]}

# (Kiwix folder, file name prefix, size note, needs --with-wikipedia)
ZIMS = [
    ("zim/wikipedia/", "wikipedia_en_medicine_nopic", "1.0 GB, medical Wikipedia articles", False),
    ("zim/other/", "appropedia_en_all_maxi", "0.6 GB, appropriate technology wiki", False),
    ("zim/other/", "zimgit-post-disaster_en", "0.6 GB, post-disaster guides", False),
    ("zim/other/", "zimgit-food-preparation_en", "0.1 GB, food preparation", False),
    ("zim/other/", "zimgit-medicine_en", "0.1 GB, medicine guides", False),
    ("zim/other/", "zimgit-water_en", "20 MB, water guides", False),
    ("zim/other/", "zimgit-knots_en", "30 MB, knots", False),
    ("zim/wikipedia/", "wikipedia_en_top_nopic", "2.6 GB, the most read Wikipedia articles, no pictures", True),
]


class Guide:
    """Renders links for one mode: web (original sources) or local (files in lib)."""

    def __init__(self, mode, lib=None):
        assert mode in ("web", "local")
        assert (lib is None) == (mode == "web")
        self.mode, self.lib, self.missing = mode, lib, []

    def href(self, path, page=None):
        src = SOURCES[path]
        if self.mode == "web":
            if src.get == "repo":
                assert (DATA / Path(path).name).is_file(), f"missing repo data file: {path}"
            return src.web_href(page), bool(page and "#page=" in src.web_href(page))
        if (self.lib / path).is_file():
            return path + (f"#page={page}" if page else ""), bool(page)
        self.missing.append(path)
        print(f"WARNING: {path} is missing in {self.lib}; its links go online instead", file=sys.stderr)
        return src.web_href(page), bool(page and "#page=" in src.web_href(page))

    def a(self, path, text, page=None):
        href, has_page = self.href(path, page)
        label = text + (f", PDF page {page}" if page and has_page else "")
        return f'<a href="{html.escape(href)}">{html.escape(label)}</a>'


def build_gpx():
    rows = list(csv.DictReader(open(DATA / "notbrunnen_osm.csv", encoding="utf-8"), delimiter=";"))
    assert len(rows) > 1000, f"expected about 2000 pumps, got {len(rows)}"
    pts = [f'<wpt lat="{float(r["@lat"]):.7f}" lon="{float(r["@lon"]):.7f}"><name>{html.escape(r.get("name") or "Notbrunnen (hand pump)")}</name></wpt>'
           for r in rows]
    gpx = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<gpx version="1.1" creator="berlin-outage-guide" xmlns="http://www.topografix.com/GPX/1/1">\n'
           '<metadata><copyright author="OpenStreetMap contributors"><license>https://opendatacommons.org/licenses/odbl/</license></copyright></metadata>\n'
           + "\n".join(pts) + "\n</gpx>\n")
    (DATA / "notbrunnen_hand_pumps.gpx").write_text(gpx, encoding="utf-8")
    return len(rows)


def meeting_points_table():
    rows = list(csv.DictReader(open(DATA / "kat_leuchttuerme.csv", encoding="utf-8"), delimiter=";"))
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


def library_section(g):
    if g.mode == "web":
        return f"""<p>This page links to the sources online. On Linux, get them all offline, plus an offline Wikipedia reader:</p>
<ol>
<li>Install <code>git</code>, <code>curl</code>, <code>python3</code>, <code>poppler-utils</code> (for <code>pdfunite</code>) and Chrome or Chromium.</li>
<li>Run <code>git clone {REPO_URL}</code></li>
<li>Run <code>berlin-outage-guide/download.sh ~/outage_library</code>. This gets about 2.6 GB. Add <code>--with-wikipedia</code> for the 2.6 GB general Wikipedia as well.</li>
<li>Open <code>~/outage_library/START_HERE.html</code>. It is this guide, with every link going to a file on your disk.</li>
</ol>
<p>Medicine books: {g.a(WTND, "Where There Is No Doctor")}, {g.a("Where_There_Is_No_Dentist_2024.pdf", "Where There Is No Dentist")}.
Checklists to stock up: {g.a(BBK_EN, "BBK checklist", 34)}; {g.a("emergency_guides/FEMA_Are_You_Ready_2020.pdf", "FEMA Are You Ready")}.</p>"""
    appimages = sorted(p.name for p in g.lib.glob("kiwix-desktop*.appimage"))
    reader = (f"run <code>{html.escape(appimages[-1])}</code> in this folder and open the <code>.zim</code> files with it"
              if appimages else "the Kiwix reader is missing; run download.sh again")
    zims = ", ".join(sorted(p.name for p in g.lib.glob("*.zim"))) or "none downloaded"
    return f"""<ul>
<li>Offline wikis (ZIM files): {html.escape(zims)}.</li>
<li>Open the reader: {reader}.</li>
<li>Search across all ZIM files: <code>kiwix-tools/kiwix-serve --port 8181 *.zim</code>, then open <code>http://127.0.0.1:8181</code> in a browser. This works offline.</li>
<li>Useful searches: carbon monoxide poisoning, hypothermia, water purification, oral rehydration therapy, first aid, food preservation, solar cooker, rainwater harvesting.</li>
<li>Medicine: {g.a(WTND, "Where There Is No Doctor")}, {g.a("Where_There_Is_No_Dentist_2024.pdf", "Where There Is No Dentist")}.</li>
<li>Checklists to stock up before an outage: {g.a(BBK_EN, "BBK checklist", 34)}; {g.a("emergency_guides/FEMA_Are_You_Ready_2020.pdf", "FEMA Are You Ready")}.</li>
</ul>"""


def page(g, n_pumps, table, n_points):
    a = g.a
    b = lambda p, t="BBK guide": a(BBK_EN, t, p)
    fema = "emergency_guides/FEMA_Power_Outage_sheet.pdf"
    cdc = "emergency_guides/CDC_Keep_Food_Safe_After_Disaster.pdf"
    strom = "berlin/berlin_stromausfall.txt"
    rufn = "berlin/berlin_rufnummern.txt"
    where = ("Links open the original sources online. The last section shows how to get all of them offline."
             if g.mode == "web" else "Every link opens a file in this folder; nothing needs the internet.")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Berlin Outage Guide</title>
<style>
:root {{ --fg:#111; --bg:#fff; --muted:#555; --line:#ccc; --box:#f3f3f3; --warn:#fff3cd; }}
@media (prefers-color-scheme: dark) {{ :root {{ --fg:#eee; --bg:#151515; --muted:#aaa; --line:#444; --box:#222; --warn:#3a3000; }} a {{ color:#8cf; }} }}
body {{ font: 16px/1.45 system-ui, sans-serif; color:var(--fg); background:var(--bg); max-width:52rem; margin:0 auto; padding:1rem; }}
h1 {{ margin-bottom:.2rem; }} h2 {{ border-top:2px solid var(--fg); padding-top:.6rem; margin-top:2rem; }}
.src {{ color:var(--muted); font-size:.9em; }}
.box {{ background:var(--box); border:1px solid var(--line); padding:.6rem 1rem; margin:.8rem 0; }}
.warn {{ background:var(--warn); border:2px solid var(--fg); padding:.6rem 1rem; }}
.scroll {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; font-size:.9em; }} td,th {{ border:1px solid var(--line); padding:.25rem .4rem; text-align:left; vertical-align:top; }}
code {{ word-break:break-all; }}
@media print {{ a {{ color:inherit; }} h2 {{ break-before:auto; }} }}
</style></head><body>
<h1>Power outage in Berlin: first minutes to first week</h1>
<p class="warn"><b>Claude Opus 5.5, an AI model, wrote this entire guide. I am not responsible for your safety.
Treat it as an unreviewed guide that may be incorrect. Still, it is better than nothing.
If you run Linux, the download script also gets a large offline library (see the end of this page).</b></p>
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
<li>If 112 and 110 do not work: go to the nearest police station, fire station or Kat-Leuchtturm (list below). <span class="src">Source: {b(15)}; {a(strom, "berlin.de Stromausfall")}</span></li>
<li>Medical on-call service (not an emergency): <b>116117</b>. <span class="src">Source: {a("berlin/116117.txt", "116117.de")}</span></li>
<li>Poison line Berlin: <b>030 19240</b>. <span class="src">Source: {a("berlin/giftnotruf.txt", "Giftnotruf Charité")}</span></li>
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
<li><b>Never use a camping stove, barbecue, generator or gas heater indoors unless it is approved for indoor use.</b> Carbon monoxide kills without warning. Its signs are flu-like symptoms. <span class="src">Source: {b(24)}; {a(fema, "FEMA power outage sheet", 2)}; {a("library_extracts/Wikipedia_Carbon_monoxide_poisoning.pdf", "Wikipedia: Carbon monoxide poisoning")}</span></li>
<li>Cooking indoors without power: tea light warmer or fondue set for small amounts. Camping gas stoves: outdoors only. <span class="src">Source: {b(24)}</span></li>
<li>Get cash if a shop or bank still works. ATMs and card payment fail without power. <span class="src">Source: {b(7)}; {a(strom, "berlin.de Stromausfall")}</span></li>
<li>Tell your neighbours what you know. Not everyone gets the warnings. <span class="src">Source: {b(13)}</span></li>
<li>Find your nearest Kat-Leuchtturm in the list below. Note its address on paper. <span class="src">Source: {a("berlin/Berlin_Flyer_Gefahrenlagen.pdf", "Berlin flyer")}</span></li>
</ol>

<h2>First day</h2>
<ol>
<li>Go to a Kat-Leuchtturm for news, emergency calls and help. The districts open them in long outages. <span class="src">Source: {a(strom, "berlin.de Stromausfall")}</span></li>
<li>Food: a closed fridge keeps food safe for 4 hours. A full closed freezer: 48 hours; half full: 24 hours. Eat the fridge food first. If in doubt, throw it out. <span class="src">Source: {a(cdc, "CDC food safety", 1)}</span></li>
<li>Toilet: if the tap water stops, do not flush. Seal the toilet shut. Use a bucket with a plastic bag and a lid. Put dry material in the bottom (sawdust, newspaper, leaves). Cover each use with sawdust, ash or similar. Use a second bucket for urine if you can. <span class="src">Source: {a("library_extracts/Wikipedia_Bucket_toilet.pdf", "Wikipedia: Bucket toilet")}; {a("emergency_guides/Emergency_Toilets_WREMO.pdf", "WREMO emergency toilets")}; {a("library_extracts/Wikipedia_Emergency_sanitation.pdf", "Wikipedia: Emergency sanitation")}</span></li>
<li>Water: if the taps are dry, use Berlin's street hand pumps (Notbrunnen). {n_pumps} are in the map file {a("berlin/notbrunnen_hand_pumps.gpx", "notbrunnen_hand_pumps.gpx")}. Load it into Organic Maps or OsmAnd now, before an outage. Boil pump water for several minutes if you can. <span class="src">Source: pumps from OpenStreetMap, © OpenStreetMap contributors, ODbL ({a("berlin/notbrunnen_osm.csv", "CSV")}); boiling: {a("library_extracts/Appropedia_Water_supply_and_purification_for_emergencies.pdf", "Appropedia: Water supply and purification for emergencies")}</span></li>
<li>Night: light and warmth. Sleep in clothes and a sleeping bag in your warm room. Know the signs of hypothermia (shivering, then confusion). <span class="src">Source: {a(WTND, "Where There Is No Doctor, cold emergencies", 457)}; {a("library_extracts/Wikipedia_Hypothermia.pdf", "Wikipedia: Hypothermia")}</span></li>
<li>Fire: leave the room, close the door, leave the building, call 112. <span class="src">Source: {b(26)}</span></li>
</ol>

<h2>First week</h2>
<ol>
<li>Drink at least 2 litres per person per day (0.5 litres of that for cooking). For 2 adults the 56 litre stock lasts 14 days; for 3 adults it lasts about 9 days. Count what is left on day 3 and day 7. <span class="src">Source: {b(9)}</span></li>
<li>Diarrhoea: drink rehydration drink. 1 litre clean water, half a level teaspoon of salt, 8 level teaspoons of sugar. It must taste less salty than tears. <span class="src">Source: {a(WTND, "Where There Is No Doctor", 199)}</span></li>
<li>Stress and fear in you or others: the WHO guide shows how to help people calmly. <span class="src">Source: {a("emergency_guides/WHO_Psychological_First_Aid_2011.pdf", "WHO Psychological first aid")}</span></li>
<li>Chemical or gas release outside: go inside, close windows and doors, go to upper floors. Radiation or explosions: go low, into inner rooms or the cellar, away from windows. <span class="src">Source: {b(30)}; {b(32)}</span></li>
<li>For anything else, use the full library (last section). After one week you are in a different situation; this guide stops here.</li>
</ol>

<h2>Kat-Leuchttürme and info points ({n_points})</h2>
<p class="src">"Planned only" locations may not open. Source: {a("berlin/kat_leuchttuerme.csv", "kat_leuchttuerme.csv")}, from berlin.de, Nov 2026.</p>
<div class="scroll">{table}</div>

<h2>References</h2>
<div class="scroll">{references(g)}</div>

<h2>The offline library</h2>
{library_section(g)}
</body></html>
"""


def build(mode, lib=None):
    n_pumps = build_gpx()
    table, n_points = meeting_points_table()
    g = Guide(mode, lib)
    if mode == "local":
        for path, src in SOURCES.items():
            if src.get == "repo":
                (lib / path).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(DATA / Path(path).name, lib / path)
    out = (REPO / "index.html") if mode == "web" else (lib / "START_HERE.html")
    out.write_text(page(g, n_pumps, table, n_points), encoding="utf-8")
    print(f"wrote {out} ({n_points} meeting points, {n_pumps} pumps)")
    if g.missing:
        print(f"WARNING: {len(set(g.missing))} files missing, linked online instead: {sorted(set(g.missing))}", file=sys.stderr)
    return g.missing


# ---------- download ----------

def is_pdf(p):
    return p.read_bytes()[:5] == b"%PDF-"


def check(path):
    """Fails loudly when a downloaded file is empty or of the wrong type."""
    assert path.is_file() and path.stat().st_size > 0, f"empty download: {path}"
    if path.suffix == ".pdf":
        assert is_pdf(path), f"not a PDF: {path}"
    elif path.suffix == ".zim":
        assert path.open("rb").read(4) == b"ZIM\x04", f"not a ZIM file: {path}"
    elif path.suffix in (".html", ".txt"):
        assert path.stat().st_size > 500, f"page too small, probably an error page: {path}"


def curl(url, dest, resume=False):
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    cmd = ["curl", "-fL", "--retry", "3", "--connect-timeout", "30", "-A", UA, "-o", str(part), url]
    if resume:
        cmd[1:1] = ["-C", "-"]
    subprocess.run(cmd, check=True)
    return part


def fetch(url, dest, resume=False):
    tmp_named = dest.with_name(dest.stem + ".part" + dest.suffix)  # keeps the suffix, so check() knows the type
    curl(url, dest, resume).rename(tmp_named)
    try:
        check(tmp_named)
    except AssertionError:
        tmp_named.unlink()
        raise
    tmp_named.rename(dest)


def listing(url):
    return subprocess.run(["curl", "-fsL", "-A", UA, url], check=True, capture_output=True, text=True).stdout


class TextOnly(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        self.skip += tag in ("script", "style", "noscript")

    def handle_endtag(self, tag):
        self.skip -= tag in ("script", "style", "noscript") and self.skip > 0

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(data.strip())


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
    dest = lib / src.path
    if src.get == "file":
        fetch(src.url, dest)
    elif src.get == "print":
        print_page(src.url, dest, lib)
    elif src.get == "text":
        page_html = dest.with_suffix(".html")
        fetch(src.url, page_html)
        parser = TextOnly()
        parser.feed(page_html.read_text(encoding="utf-8", errors="replace"))
        dest.write_text("\n".join(parser.parts) + "\n", encoding="utf-8")
        check(dest)
    elif src.get == "hesperian":
        chapters = lib / "hesperian_chapters"
        files = []
        for url in src.parts:
            f = chapters / url.rsplit("/", 1)[1]
            if not (f.is_file() and is_pdf(f)):
                fetch(url, f)
            files.append(str(f))
        assert shutil.which("pdfunite"), "pdfunite is needed to merge the Hesperian chapters; install poppler-utils"
        tmp = dest.with_name(dest.stem + ".part.pdf")
        subprocess.run(["pdfunite", *files, str(tmp)], check=True)
        check(tmp)
        tmp.rename(dest)
    elif src.get == "repo":
        pass  # build() copies repo data files
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


def download(lib, with_wikipedia):
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
        if src.get == "repo" or (lib / path).is_file():
            continue
        attempt(path, get_source, src, lib)
    attempt("Kiwix reader and kiwix-serve", get_kiwix, lib)
    for folder, prefix, note, optional in ZIMS:
        if optional and not with_wikipedia:
            print(f"skip {prefix} ({note}); add --with-wikipedia to get it")
            continue
        attempt(f"{prefix} ({note})", get_zim, folder, prefix, lib)
    missing = build("local", lib)
    if failed or missing:
        print(f"\n{len(failed)} downloads FAILED: {failed}\nRun the script again to retry them.", file=sys.stderr)
        sys.exit(1)
    print(f"\nDone. Open {lib / 'START_HERE.html'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("web")
    sub.add_parser("local").add_argument("dir", type=Path)
    d = sub.add_parser("download")
    d.add_argument("dir", type=Path)
    d.add_argument("--with-wikipedia", action="store_true", help="also get the 2.6 GB general Wikipedia")
    args = ap.parse_args()
    if args.cmd == "web":
        build("web")
    elif args.cmd == "local":
        build("local", args.dir.expanduser().resolve())
    else:
        download(args.dir.expanduser().resolve(), args.with_wikipedia)


if __name__ == "__main__":
    main()
