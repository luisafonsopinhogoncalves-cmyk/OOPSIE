"""Price Error Radar: checks deal sites, finds likely pricing mistakes,
saves them to docs/deals.json and sends phone alerts for the best ones."""

import html
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).parent
DATA_FILE = ROOT / "docs" / "deals.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PriceErrorRadar/1.0; personal use)",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

TEMP_RE = re.compile(r"^\s*(-?\d+)\s*°\s*-?\s*")
PCT_RE = re.compile(r"(?:-\s?(\d{2,3})\s?%|(\d{2,3})\s?%\s*(?:off|rabatt|de réduction|descuento|sconto|korting))", re.I)
TAG_RE = re.compile(r"<[^>]+>")


def load_config():
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def local(tag):
    """Tag name without its namespace."""
    return tag.rsplit("}", 1)[-1]


def clean_text(raw):
    return re.sub(r"\s+", " ", html.unescape(TAG_RE.sub(" ", raw or ""))).strip()


def parse_feed(xml_bytes):
    root = ET.fromstring(xml_bytes)
    items = []
    for item in root.iter():
        if local(item.tag) not in ("item", "entry"):
            continue
        d = {"merchant": "", "price": "", "image": "", "categories": []}
        for child in item:
            name = local(child.tag)
            text = (child.text or "").strip()
            if name in ("thumbnail", "content") and child.get("url"):
                d["image"] = d["image"] or child.get("url")
            elif name == "title":
                d["title"] = text
            elif name == "link":
                d["link"] = text or child.get("href", "")
            elif name in ("description", "summary", "content"):
                d.setdefault("description", text)
            elif name in ("pubDate", "published", "updated"):
                d.setdefault("date_raw", text)
            elif name == "guid":
                d["guid"] = text
            elif name == "category":
                d["categories"].append(text or child.get("term", ""))
            elif name == "merchant":
                d["merchant"] = child.get("name", "") or text
                d["price"] = child.get("price", "")
        if d.get("title") and d.get("link"):
            items.append(d)
    return items


def to_iso(raw):
    if not raw:
        return datetime.now(timezone.utc).isoformat()
    try:
        return parsedate_to_datetime(raw).astimezone(timezone.utc).isoformat()
    except Exception:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
        except Exception:
            return datetime.now(timezone.utc).isoformat()


def score_deal(title, desc, temperature, cfg):
    """Returns (score 0-100, reasons, discount%)."""
    text = f"{title} {desc}".lower()
    score, reasons = 0, []

    hits = [k for k in cfg.get("error_keywords", []) if k.lower() in text]
    if hits:
        in_title = any(k.lower() in title.lower() for k in hits)
        score += 70 if in_title else 45
        reasons.append(f'mentions "{hits[0]}"')

    discount = None
    pcts = [int(a or b) for a, b in PCT_RE.findall(f"{title} {desc}")]
    pcts = [p for p in pcts if p <= 99]
    if pcts:
        discount = max(pcts)
        if discount >= cfg.get("min_discount_percent", 70):
            score += 30
            reasons.append(f"{discount}% off")

    if temperature is not None and temperature >= cfg.get("hot_temperature", 300):
        score += 10
        reasons.append(f"{temperature}° hot")

    return min(score, 100), reasons, discount


def price_number(price):
    m = re.search(r"\d[\d.,\s]*", price or "")
    if not m:
        return None
    n = re.sub(r"\s", "", m.group()).rstrip(".,")
    if "," in n and "." in n:  # 1,299.00 or 1.299,00
        n = n.replace(",", "") if n.rfind(".") > n.rfind(",") else n.replace(".", "").replace(",", ".")
    elif "," in n:
        n = n.replace(",", "") if re.search(r",\d{3}$", n) else n.replace(",", ".")
    elif re.search(r"\.\d{3}$", n):
        n = n.replace(".", "")
    try:
        return float(n)
    except ValueError:
        return None


WAS_RE = re.compile(
    r"(?:was|rrp|normally|usually|statt|vorher|uvp|au lieu de|prix barr[ée]|antes|en vez de|invece di|prima|van|zamiast|regular)"
    r"\s*:?\s*([£€$]|chf|zł|kr)?\s*(\d[\d.,\s]*\d|\d)\s*([£€$]|chf|zł|kr|eur|gbp)?",
    re.I,
)
PAGE_PRICE_RES = [
    re.compile(r'"nextBestPrice"\s*:\s*"?(\d+(?:\.\d+)?)'),
    re.compile(r'"originalPrice"\s*:\s*"?(\d+(?:\.\d+)?)'),
    re.compile(r'"oldPrice"\s*:\s*"?(\d+(?:\.\d+)?)'),
]
CURRENCY_CODES = {"£": "GBP", "€": "EUR", "CHF": "CHF", "zł": "PLN", "kr": "SEK"}
FALLBACK_RATES = {"EUR": 1.0, "GBP": 1.17, "CHF": 1.06, "PLN": 0.23, "SEK": 0.087}


def get_rates():
    """How many EUR one unit of each currency is worth (ECB rates, free)."""
    rates = dict(FALLBACK_RATES)
    try:
        r = requests.get("https://api.frankfurter.app/latest?from=EUR", timeout=10)
        for code, per_eur in r.json()["rates"].items():
            rates[code] = 1 / per_eur
    except Exception as e:
        print("using fallback exchange rates:", e)
    return rates


def find_was_price(text, price):
    """Original price written in the post, if any (must be higher than the deal price)."""
    best = None
    for _, num, _ in WAS_RE.findall(text):
        v = price_number(num)
        if v and price and v > price and (best is None or v > best):
            best = v
    return best


def was_price_from_page(url):
    """Last resort: look for the original price inside the deal page."""
    try:
        page = requests.get(url, headers=HEADERS, timeout=15).text
    except Exception:
        return None
    for rx in PAGE_PRICE_RES:
        m = rx.search(page)
        if m:
            return float(m.group(1))
    return None


def watch_match(title, desc, price, cfg):
    text = f"{title} {desc}".lower()
    for w in cfg.get("watchlist") or []:
        kw = str(w.get("keyword", "")).lower()
        if kw and kw in text:
            limit = w.get("max_price")
            p = price_number(price)
            if limit in (None, "") or (p is not None and p <= float(limit)):
                return kw
    return None


def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.content


def collect(cfg):
    found, errors = [], []
    blocks = [b.lower() for b in cfg.get("block_keywords") or []]
    for src in cfg["sources"]:
        if not src.get("enabled", True):
            continue
        for url in src["feeds"]:
            try:
                items = parse_feed(fetch(url))
            except Exception as e:
                errors.append(f"{src['name']}: {url} -> {e}")
                continue
            for it in items:
                raw_title = clean_text(it["title"])
                temp = None
                m = TEMP_RE.match(raw_title)
                if m:
                    temp = int(m.group(1))
                    raw_title = raw_title[m.end():]
                desc = clean_text(it.get("description", ""))[:400]
                low = f"{raw_title} {desc}".lower()
                if any(b in low for b in blocks):
                    continue
                score, reasons, discount = score_deal(raw_title, desc, temp, cfg)
                watched = watch_match(raw_title, desc, it.get("price"), cfg)
                if watched:
                    reasons.append(f'watchlist: "{watched}"')
                if score < cfg.get("min_score", 30) and not watched:
                    continue
                price = (it.get("price") or "").strip()
                if price and re.fullmatch(r"[\d.,\s]+", price):
                    price = f"{src.get('currency', '')}{price}"
                now_price = price_number(price)
                was = find_was_price(f"{raw_title} {desc}", now_price)
                if was is None and now_price and discount:
                    was = round(now_price / (1 - discount / 100), 2)
                found.append({
                    "id": it.get("guid") or it["link"],
                    "title": raw_title,
                    "link": it["link"],
                    "price": price,
                    "currency": src.get("currency", ""),
                    "merchant": it.get("merchant", ""),
                    "image": it.get("image", ""),
                    "category": (it["categories"] or [""])[0],
                    "source": src["name"],
                    "country": src["country"],
                    "temperature": temp,
                    "discount": discount,
                    "price_value": now_price,
                    "was_price": was,
                    "score": score,
                    "watchlist": bool(watched),
                    "reasons": reasons,
                    "posted": to_iso(it.get("date_raw")),
                    "snippet": desc[:220],
                    "currency_code": CURRENCY_CODES.get(src.get("currency", ""), "EUR"),
                })
            time.sleep(1)  # be polite to the sites
    return found, errors


def send_alert(deal, cfg):
    a = cfg.get("alerts") or {}
    topic = a.get("ntfy_topic", "")
    if not a.get("enabled") or not topic or topic.startswith("CHANGE-ME"):
        return
    body = f"{deal['price']} {deal['merchant']} ({deal['country']})\n{', '.join(deal['reasons'])}".strip()
    try:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=body.encode("utf-8"),
            headers={
                "Title": deal["title"][:120].encode("utf-8"),
                "Click": deal["link"],
                "Tags": "rotating_light" if deal["score"] >= 70 else "moneybag",
                "Priority": "high" if deal["score"] >= 70 else "default",
            },
            timeout=10,
        )
    except Exception as e:
        print("alert failed:", e)


def main():
    cfg = load_config()
    old = {"deals": []}
    if DATA_FILE.exists():
        old = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    known = {d["id"]: d for d in old.get("deals", [])}

    found, errors = collect(cfg)
    now = datetime.now(timezone.utc)
    rates = get_rates()
    page_checks = 0
    for d in found:
        prev = known.get(d["id"])
        if d["was_price"] is None and prev and prev.get("was_price"):
            d["was_price"] = prev["was_price"]
        elif d["was_price"] is None and not prev and d["price_value"] and page_checks < cfg.get("max_page_checks", 25):
            page_checks += 1
            w = was_price_from_page(d["link"])
            if w and w > d["price_value"]:
                d["was_price"] = w
            time.sleep(1)
        if d["was_price"] and d["price_value"]:
            saving = d["was_price"] - d["price_value"]
            d["saving"] = round(saving, 2)
            d["saving_eur"] = round(saving * rates.get(d["currency_code"], 1.0), 2)
            if d["discount"] is None:
                d["discount"] = round(100 * saving / d["was_price"])
        else:
            d["saving"] = d["saving_eur"] = None

    min_save = cfg.get("min_saving_eur", 0) or 0
    show_unknown = cfg.get("show_unknown_saving", True)
    found = [
        d for d in found
        if (d["saving_eur"] is not None and d["saving_eur"] >= min_save)
        or (d["saving_eur"] is None and show_unknown)
    ]
    new_count = 0
    for d in found:
        if d["id"] in known:
            d["first_seen"] = known[d["id"]].get("first_seen", d["posted"])
            d["alerted"] = known[d["id"]].get("alerted", False)
        else:
            d["first_seen"] = now.isoformat()
            d["alerted"] = False
            new_count += 1
        if not d["alerted"] and d["score"] >= (cfg.get("alerts") or {}).get("min_score", 60):
            send_alert(d, cfg)
            d["alerted"] = True
        known[d["id"]] = d

    cutoff = now - timedelta(days=cfg.get("keep_days", 3))
    deals = [
        d for d in known.values()
        if datetime.fromisoformat(d["first_seen"]) >= cutoff
        and not (d.get("saving_eur") is not None and d["saving_eur"] < min_save)
        and not (d.get("saving_eur") is None and not show_unknown)
    ]
    deals.sort(key=lambda d: (d.get("saving_eur") or 0, d["first_seen"]), reverse=True)

    DATA_FILE.parent.mkdir(exist_ok=True)
    DATA_FILE.write_text(json.dumps({
        "updated": now.isoformat(),
        "settings": {
            "app_name": cfg.get("app_name", "Oopsie"),
            "language": cfg.get("dashboard_language", "en"),
            "min_saving_eur": cfg.get("min_saving_eur", 0),
        },
        "errors": errors,
        "deals": deals,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(deals)} deals on file, {new_count} new, {len(errors)} feed errors")
    for e in errors:
        print("  !", e)


if __name__ == "__main__":
    sys.exit(main())
