#!/usr/bin/env python3
"""
North West England fuel prices - fetcher and page builder.

Run:  python3 fetch_prices.py
Needs only Python 3.8+ (no extra packages).

What it does
1. Downloads the UK government Fuel Finder CSV (every registered forecourt,
   including independents). If the official URL is blocked (it only answers
   UK connections), it falls back to a public hourly mirror of the same file.
2. Downloads the old CMA retailer JSON feeds that are still online
   (the CMA interim scheme closed on 1 May 2026; some retailers still publish).
   A retailer price is only used if that feed was updated in the last 48 hours
   and is newer than the Fuel Finder price for the same station.
3. Removes duplicates, keeps North West England only, checks prices for
   obviously wrong values, then writes:
      fuel-data-northwest.json
      fuel-prices-northwest.html   (single file, data built in)
"""
import csv, io, json, math, os, re, socket, statistics, sys, time
import urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

try:
    from zoneinfo import ZoneInfo
    UK = ZoneInfo("Europe/London")
except Exception:  # very old Python: fall back to UTC labels
    UK = timezone.utc

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_JSON = os.path.join(HERE, "fuel-data-northwest.json")
OUT_HTML = os.path.join(HERE, "fuel-prices-northwest.html")
TEMPLATE = os.path.join(HERE, "template.html")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
TIMEOUT = 30

FUEL_FINDER = [
    ("Fuel Finder (gov.uk, official CSV)",
     "https://www.fuel-finder.service.gov.uk/internal/v1.0.2/csv/get-latest-fuel-prices-csv"),
    ("Fuel Finder (hourly public mirror, github.com/matthewgall/fuelfinder-archive)",
     "https://raw.githubusercontent.com/matthewgall/fuelfinder-archive/main/data.csv"),
]

LEGACY_FEEDS = [  # (retailer, url) - CMA interim scheme feeds
    ("Asda", "https://storelocator.asda.com/fuel_prices_data.json"),
    ("BP", "https://www.bp.com/en_gb/united-kingdom/home/fuelprices/fuel_prices_data.json"),
    ("Esso / Tesco Alliance", "https://fuelprices.esso.co.uk/latestdata.json"),
    ("Jet", "https://jetlocal.co.uk/fuel_prices_data.json"),
    ("Morrisons", "https://www.morrisons.com/fuel-prices/fuel.json"),
    ("Moto", "https://moto-way.com/fuel-price/fuel_prices.json"),
    ("Motor Fuel Group", "https://fuel.motorfuelgroup.com/fuel_prices_data.json"),
    ("Rontec", "https://www.rontec-servicestations.co.uk/fuel-prices/data/fuel_prices_data.json"),
    ("Sainsbury's", "https://api.sainsburys.co.uk/v1/exports/latest/fuel_prices_data.json"),
    ("SGN", "https://www.sgnretail.uk/files/data/SGN_daily_fuel_prices.json"),
    ("Shell", "https://www.shell.co.uk/fuel-prices-data.html"),
    ("Tesco", "https://www.tesco.com/fuel_prices/fuel_prices_data.json"),
    ("Applegreen", "https://applegreenstores.com/fuel-prices/data.json"),
    ("Karan Retail", "https://devapi.krlpos.com/integration/live_price/krl"),
]
LEGACY_MAX_AGE_H = 48

FUELS = ["E10", "E5", "B7", "SDV"]  # unleaded, super, diesel, premium diesel
FF_FUEL_COLS = {"E10": "E10", "E5": "E5", "B7": "B7S", "SDV": "B7P"}

NW_AREAS = {"CA", "LA", "FY", "PR", "BB", "BL", "OL", "M", "SK", "WN", "WA", "L", "CH", "CW"}
WELSH_CH_DISTRICTS = {"CH5", "CH6", "CH7", "CH8"}
NOT_ENGLAND = {"WALES", "W", "SCOTLAND", "S", "NORTHERN IRELAND", "N", "NI"}

HARD_MIN, HARD_MAX = 100.0, 250.0   # p/litre outside this = rejected
REL_LOW, REL_HIGH = 0.12, 0.25      # >12% below / >25% above NW median = rejected

BRAND_NAMES = {
    "ESSO": "Esso", "BP": "BP", "SHELL": "Shell", "TESCO": "Tesco", "MORRISONS": "Morrisons",
    "SAINSBURYS": "Sainsbury's", "SAINSBURY'S": "Sainsbury's", "TEXACO": "Texaco", "JET": "Jet",
    "GULF": "Gulf", "VALERO": "Valero", "EG ON THE MOVE": "EG On The Move", "MURCO": "Murco",
    "ASDA": "Asda", "ASDA EXPRESS": "Asda Express", "MAXOL": "Maxol", "WELCOME BREAK": "Welcome Break",
    "PACE": "Pace", "GO": "Go", "SOLO": "Solo", "CIRCLE K": "Circle K", "SPAR": "Spar",
    "THE CO-OPERATIVE": "Co-op", "CO-OP": "Co-op", "COOP": "Co-op", "COSTCO WHOLESALE": "Costco",
    "COSTCO": "Costco", "ESSAR": "Essar", "TOTAL": "Total", "TOTALENERGIES": "TotalEnergies",
    "MOTO": "Moto", "APPLEGREEN": "Applegreen", "HARVEST ENERGY": "Harvest Energy",
    "CERTAS ENERGY": "Certas Energy", "RONTEC": "Rontec", "SGN": "SGN", "MFG": "MFG",
}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def http_get(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json,text/csv,text/plain,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
        "Referer": "https://www.gov.uk/guidance/access-fuel-price-data",
        "Cache-Control": "no-cache",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def why(e):
    if isinstance(e, urllib.error.HTTPError):
        return "HTTP %s (blocked or removed)" % e.code
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "timed out"
    if isinstance(e, urllib.error.URLError):
        return "could not connect (%s)" % getattr(e, "reason", e)
    if isinstance(e, json.JSONDecodeError):
        return "not valid JSON (feed page, not data)"
    return str(e)[:120]


# ---------- helpers ----------
def norm_pc(pc):
    pc = re.sub(r"[^A-Z0-9]", "", (pc or "").upper())
    if len(pc) >= 5:
        pc = pc[:-3] + " " + pc[-3:]
    return pc


def pc_area(pc):
    m = re.match(r"^([A-Z]{1,2})\d", pc or "")
    return m.group(1) if m else ""


def pc_district(pc):
    return (pc or "").split(" ")[0]


def haversine_m(a, b, c, d):
    r = 6371000.0
    p1, p2 = math.radians(a), math.radians(c)
    dp, dl = math.radians(c - a), math.radians(d - b)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def fnum(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def to_pence(v):
    """Return (pence, note). Converts pounds and tenths-of-a-penny."""
    x = fnum(v)
    if x is None or x <= 0:
        return None, None
    if 0.5 <= x < 5:          # given in pounds, e.g. 1.459
        return round(x * 100, 1), "converted from £"
    if 1000 <= x < 5000:      # tenths of a penny, e.g. 1459
        return round(x / 10, 1), "converted from tenths of a penny"
    return round(x, 1), None


def tidy(s):
    s = re.sub(r"\s+", " ", (s or "").strip())
    if not s:
        return ""
    if s.isupper() or s.islower():
        s = s.title()
        s = re.sub(r"\b(Bp|Pfs|Uk|Mfg|Sgn|Eg|Ii|Iii|Kfc|Mot)\b", lambda m: m.group(0).upper(), s)
        s = re.sub(r"'S\b", "'s", s)
    return s


KNOWN_PREFIXES = [  # first word(s) of a brand field -> brand
    ("ASDA EXPRESS", "Asda"), ("ASDA", "Asda"), ("BP", "BP"), ("ESSO", "Esso"), ("SHELL", "Shell"),
    ("TEXACO", "Texaco"), ("ESSAR", "Essar"), ("MURCO", "Murco"), ("GULF", "Gulf"), ("JET", "Jet"),
    ("VALERO", "Valero"), ("TESCO", "Tesco"), ("MORRISONS", "Morrisons"), ("SAINSBURY", "Sainsbury's"),
    ("COSTCO", "Costco"), ("TOTAL", "Total"), ("NISA", "Nisa"), ("GO ", "Go"), ("PACE", "Pace"),
    ("APPLEGREEN", "Applegreen"), ("EG ", "EG On The Move"), ("MAXOL", "Maxol"), ("CIRCLE K", "Circle K"),
    ("SPAR", "Spar"), ("CO-OP", "Co-op"), ("THE CO-OPERATIVE", "Co-op"), ("WELCOME BREAK", "Welcome Break"),
    ("MOTO", "Moto"), ("HARVEST", "Harvest Energy"), ("CERTAS", "Certas Energy"), ("SCOTTISH FUELS", "Scottish Fuels"),
    ("UK FUELS", "UK Fuels"),
]
NO_BRAND = {"", "NONE", "NO BRAND", "UNBRANDED", "N/A", "NA", "INDEPENDENT", "OTHER", "-"}
UK_BRAND_COUNTS = {}


def brand_name(b):
    key = re.sub(r"\s+", " ", (b or "").strip()).upper()
    if key in NO_BRAND:
        return "Independent"
    if key in BRAND_NAMES:
        return BRAND_NAMES[key]
    for pre, name in KNOWN_PREFIXES:
        if key == pre.strip() or key.startswith(pre if pre.endswith(" ") else pre + " ") \
                or key.startswith(pre + "-") or key.startswith(pre + "/"):
            return name
    if UK_BRAND_COUNTS.get(key, 0) >= 5:  # a real chain name used across the UK
        return tidy(b)
    return "Independent"


def parse_ff_ts(s):
    # e.g. "Fri Oct 02 2026 15:38:14 GMT+0000 (Coordinated Universal Time)"
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%a %b %d %Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:24] if fmt.startswith("%a") else s[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def parse_legacy_ts(s):
    s = (s or "").strip()
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(s[:19], fmt).replace(tzinfo=UK).astimezone(timezone.utc)
        except ValueError:
            pass
    return None


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def uk_label(dt):
    if not dt:
        return "unknown"
    d = dt.astimezone(UK)
    return d.strftime("%a %d %b %Y, %H:%M ") + (d.tzname() or "UK")


# ---------- sources ----------
def load_fuel_finder(sources):
    for label, url in FUEL_FINDER:
        t0 = time.time()
        try:
            raw = http_get(url)
            text = raw.decode("utf-8-sig", errors="replace")
            rows = list(csv.DictReader(io.StringIO(text)))
            if len(rows) < 1000 or "forecourts.node_id" not in rows[0]:
                raise ValueError("unexpected file contents (%d rows)" % len(rows))
            latest = None
            for r in rows:
                for f in FF_FUEL_COLS.values():
                    t = parse_ff_ts(r.get("forecourts.price_submission_timestamp." + f))
                    if t and (latest is None or t > latest):
                        latest = t
            sources.append({"name": label, "url": url, "ok": True, "rows": len(rows),
                            "last_updated": iso(latest), "last_updated_uk": uk_label(latest),
                            "note": "newest price in file", "secs": round(time.time() - t0, 1)})
            log("OK   %s: %d forecourts, newest price %s" % (label, len(rows), uk_label(latest)))
            return rows, label
        except Exception as e:
            sources.append({"name": label, "url": url, "ok": False, "error": why(e)})
            log("FAIL %s: %s" % (label, why(e)))
    return [], None


def load_legacy(sources, now):
    feeds = []
    for retailer, url in LEGACY_FEEDS:
        name = "%s (old CMA retailer feed)" % retailer
        try:
            d = json.loads(http_get(url))
            st = d.get("stations") or []
            lu = parse_legacy_ts(d.get("last_updated"))
            age_h = (now - lu).total_seconds() / 3600 if lu else None
            fresh = age_h is not None and age_h <= LEGACY_MAX_AGE_H
            src = {"name": name, "url": url, "ok": True, "rows": len(st), "last_updated": iso(lu),
                   "last_updated_uk": uk_label(lu), "used": fresh,
                   "note": "used" if fresh else "stale - not used (last updated %s)" % uk_label(lu)}
            sources.append(src)
            log("%s %s: %d stations, updated %s" % ("OK  " if fresh else "OLD ", name, len(st), uk_label(lu)))
            if fresh:
                feeds.append((retailer, lu, st, src))
        except Exception as e:
            sources.append({"name": name, "url": url, "ok": False, "error": why(e)})
            log("FAIL %s: %s" % (name, why(e)))
    return feeds


# ---------- filtering ----------
def is_north_west(pc, lat, lon, country):
    c = (country or "").strip().upper()
    if c in NOT_ENGLAND:
        return False
    area = pc_area(pc)
    if area:
        if area not in NW_AREAS:
            return False
        if area == "CH":
            if pc_district(pc) in WELSH_CH_DISTRICTS:
                return False
            # CH4 straddles the border (Saltney/Broughton side is Wales)
            if pc_district(pc) == "CH4" and lon is not None and lon < -2.925:
                return False
        return True
    # no usable postcode: rough North West England box
    if lat is None or lon is None:
        return False
    return 53.0 <= lat <= 55.2 and -3.65 <= lon <= -1.9 and not (lat < 53.45 and lon < -2.95)


def main():
    now = datetime.now(timezone.utc)
    sources = []
    ff_rows, ff_label = load_fuel_finder(sources)
    legacy = load_legacy(sources, now)

    for r in ff_rows:
        k = re.sub(r"\s+", " ", (r.get("forecourts.brand_name") or "").strip()).upper()
        UK_BRAND_COUNTS[k] = UK_BRAND_COUNTS.get(k, 0) + 1
    stations = []
    closed = 0
    for r in ff_rows:
        g = lambda k: (r.get("forecourts." + k) or "").strip()
        pc = norm_pc(g("location.postcode"))
        lat, lon = fnum(g("location.latitude")), fnum(g("location.longitude"))
        if lat is not None and not (49 < lat < 61 and -9 < lon < 3):
            lat = lon = None
        if not is_north_west(pc, lat, lon, g("location.country")):
            continue
        if g("temporary_closure").lower() == "true" or g("permanent_closure").lower() == "true":
            closed += 1
            continue
        prices = {}
        for fuel, col in FF_FUEL_COLS.items():
            p, note = to_pence(g("fuel_price." + col))
            if p is None:
                continue
            t = parse_ff_ts(g("price_submission_timestamp." + col))
            prices[fuel] = {"p": p, "t": iso(t), "src": "Fuel Finder", "note": note}
        addr = ", ".join(x for x in [tidy(g("location.address_line_1")), tidy(g("location.address_line_2"))] if x)
        stations.append({
            "id": g("node_id")[:16], "brand": brand_name(g("brand_name")), "name": tidy(g("trading_name")),
            "addr": addr, "pc": pc, "town": tidy(g("location.city")) or tidy(g("location.county")),
            "lat": lat, "lon": lon, "prices": prices, "src": ["Fuel Finder"],
            "motorway": g("is_motorway_service_station").lower() == "true",
            "supermarket": g("is_supermarket_service_station").lower() == "true",
        })

    # ---- merge retailer feeds, de-duplicating by postcode / distance ----
    by_pc = {}
    for s in stations:
        by_pc.setdefault(s["pc"], []).append(s)
    merged = added = updated = agree = compared = 0
    for retailer, lu, st_list, src in legacy:
        nw_count = 0
        for x in st_list:
            loc = x.get("location") or {}
            lat, lon = fnum(loc.get("latitude")), fnum(loc.get("longitude"))
            pc = norm_pc(x.get("postcode"))
            if not is_north_west(pc, lat, lon, ""):
                continue
            nw_count += 1
            match = None
            for s in by_pc.get(pc, []):
                if lat is None or s["lat"] is None or haversine_m(lat, lon, s["lat"], s["lon"]) < 1500:
                    match = s
                    break
            if match is None and lat is not None:
                best = None
                for s in stations:
                    if s["lat"] is None or abs(s["lat"] - lat) > 0.003:
                        continue
                    d = haversine_m(lat, lon, s["lat"], s["lon"])
                    if d < 120 and (best is None or d < best[0]):
                        best = (d, s)
                match = best[1] if best else None
            lprices = {}
            for fuel, v in (x.get("prices") or {}).items():
                fuel = {"B7S": "B7", "B7P": "SDV", "SUPER": "E5"}.get(fuel.upper(), fuel.upper())
                if fuel in FUELS:
                    p, note = to_pence(v)
                    if p is not None:
                        lprices[fuel] = {"p": p, "t": iso(lu), "src": retailer + " feed", "note": note}
            if match:
                merged += 1
                if retailer + " feed" not in match["src"]:
                    match["src"].append(retailer + " feed")
                for fuel, lp in lprices.items():
                    cur = match["prices"].get(fuel)
                    if cur is None:
                        match["prices"][fuel] = lp
                        updated += 1
                        continue
                    if cur["src"] == "Fuel Finder":
                        compared += 1
                        if abs(cur["p"] - lp["p"]) < 0.05:
                            agree += 1
                    if (cur["t"] or "") < lp["t"] and abs(cur["p"] - lp["p"]) >= 0.05:
                        lp = dict(lp, alt="%s said %.1fp" % (cur["src"], cur["p"]))
                        match["prices"][fuel] = lp
                        updated += 1
            elif lprices:
                added += 1
                addr = tidy(x.get("address"))
                town = addr.split(",")[-1].strip() if "," in addr else ""
                s = {"id": "%s-%s" % (retailer[:3].lower(), x.get("site_id", "")), "brand": brand_name(x.get("brand")),
                     "name": "%s %s" % (brand_name(x.get("brand")), town or pc), "addr": addr, "pc": pc,
                     "town": town, "lat": lat, "lon": lon, "prices": lprices, "src": [retailer + " feed"],
                     "motorway": False, "supermarket": retailer in ("Asda", "Tesco", "Sainsbury's", "Morrisons")}
                stations.append(s)
                by_pc.setdefault(pc, []).append(s)
        src["nw_rows"] = nw_count

    # ---- sanity-check prices against the NW median for each fuel ----
    rejected = []
    medians = {}
    for fuel in FUELS:
        vals = [s["prices"][fuel]["p"] for s in stations if fuel in s["prices"]
                and HARD_MIN <= s["prices"][fuel]["p"] <= HARD_MAX]
        medians[fuel] = statistics.median(vals) if vals else None
    def reject(s, fuel, why_):
        rejected.append({"station": s["name"], "brand": s["brand"], "pc": s["pc"], "fuel": fuel,
                         "value": s["prices"][fuel]["p"], "src": s["prices"][fuel]["src"], "why": why_})
        del s["prices"][fuel]

    for s in stations:
        for fuel in list(s["prices"]):
            p = s["prices"][fuel]["p"]
            med = medians[fuel]
            if not (HARD_MIN <= p <= HARD_MAX):
                reject(s, fuel, "outside %d-%dp" % (HARD_MIN, HARD_MAX))
            elif med and p < med * (1 - REL_LOW):
                reject(s, fuel, "more than %d%% below the NW median (%.1fp)" % (REL_LOW * 100, med))
            elif med and p > med * (1 + REL_HIGH):
                reject(s, fuel, "more than %d%% above the NW median (%.1fp)" % (REL_HIGH * 100, med))
        # a premium grade cheaper than the standard grade is almost always a typing error
        pr = s["prices"]
        for prem, std in (("E5", "E10"), ("SDV", "B7")):
            if prem in pr and std in pr and pr[prem]["p"] < pr[std]["p"] - 0.05:
                reject(s, prem, "premium cheaper than standard (%.1fp)" % pr[std]["p"])
    stations = [s for s in stations if s["prices"]]

    stats = {}
    for fuel in FUELS:
        vals = [s["prices"][fuel]["p"] for s in stations if fuel in s["prices"]]
        if vals:
            stats[fuel] = {"n": len(vals), "avg": round(sum(vals) / len(vals), 1),
                           "min": min(vals), "max": max(vals), "median": round(statistics.median(vals), 1)}

    newest = max((s["prices"][f]["t"] for s in stations for f in s["prices"] if s["prices"][f]["t"]), default=None)
    week_ago = iso(now - timedelta(days=7))
    fresh7 = sum(1 for s in stations if any((v["t"] or "") >= week_ago for v in s["prices"].values()))
    brands = sorted({s["brand"] for s in stations})
    for s in stations:
        s["src"] = " + ".join(s["src"])
    data = {
        "generated": iso(now), "generated_uk": uk_label(now),
        "newest_price": newest,
        "newest_price_uk": uk_label(datetime.strptime(newest, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)) if newest else "unknown",
        "fuel_finder_source": ff_label, "sources": sources, "stats": stats,
        "counts": {"stations": len(stations), "brands": len(brands), "closed_excluded": closed,
                   "retailer_matches": merged, "retailer_added": added, "retailer_price_updates": updated,
                   "cross_checked": compared, "cross_check_agree": agree, "rejected_prices": len(rejected),
                   "stations_updated_last_7_days": fresh7},
        "rejected": rejected[:200], "brands": brands, "stations": stations,
    }
    with open(OUT_JSON, "w") as f:
        json.dump(data, f, indent=1)
    log("Wrote %s (%d NW stations, %d brands, %d prices rejected)" % (OUT_JSON, len(stations), len(brands), len(rejected)))

    if not os.path.exists(TEMPLATE):
        log("template.html missing - JSON written, HTML not rebuilt")
        return 1
    tpl = open(TEMPLATE, encoding="utf-8").read()
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    html = tpl.replace("/*__FUEL_DATA__*/null", blob)
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    log("Wrote %s (%.0f KB)" % (OUT_HTML, os.path.getsize(OUT_HTML) / 1024))
    if not stations:
        log("WARNING: no stations - every source failed")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
