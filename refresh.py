"""Checks every part number at the Toyota dealer and saves the results in data.json.
Runs from GitHub Actions every 6 hours. Uses Python's standard library only."""
import json, os, sys, time, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

SOURCE = 'https://api.c0a7xy-abdullati1-p1-public.model-t.cc.commerce.ondemand.com/occ/v2/aljsparepartsToyota/products/'
FIELDS = '?fields=DEFAULT,averageRating,numberOfReviews,categories(FULL)'
BATCH = 8

def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

def parse(t):
    return datetime.fromisoformat(t.replace('Z', '+00:00')).timestamp() if t else 0

def fetch_json(url):
    req = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': 'corolla-parts-checker'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def classify(product, part):
    if not product or product.get('code') != part:
        return 'unknown'
    value = (product.get('stock') or {}).get('stockLevelStatus')
    if value in ('inStock', 'lowStock') and product.get('purchasable') is not False:
        return 'available'
    if value == 'outOfStock':
        return 'unavailable'
    return 'unknown'

def check(part, old, fetcher=fetch_json, clock=now):
    t = clock()
    result = {'part': part, 'state': 'unknown', 'lastAttemptAt': t, 'checkedAt': None,
              'source': 'https://parts.toyota.com.sa/en/product/' + part}
    try:
        product = fetcher(SOURCE + urllib.parse.quote(part) + FIELDS)
        state = classify(product, part)
        price = product.get('price') or {}
        try:
            regular, sale = float(price.get('value')), float(price.get('valueWithDiscount'))
        except (TypeError, ValueError):
            regular = sale = None
        offer = None
        if product.get('code') == part and price.get('currencyIso') == 'SAR' and regular and sale and 0 < sale < regular:
            offer = {'regular': regular, 'sale': sale, 'percent': round((1 - sale / regular) * 10000) / 100}
        result.update(state=state, checkedAt=None if state == 'unknown' else t, offer=offer,
                      offerCheckedAt=t if product.get('code') == part else None)
    except Exception:
        result['reason'] = 'source_unavailable'
    if result['state'] == 'unknown' and old:
        result['lastKnownState'] = old.get('lastKnownState') if old.get('state') == 'unknown' else old.get('state')
        result['lastKnownAt'] = old.get('checkedAt') or old.get('lastKnownAt')
    return result

def codes_of(p):
    return [p['part']] + ([p['alt']] if p.get('alt') else [])

def check_all(parts, old, fetcher=fetch_json):
    codes = list(dict.fromkeys(c for p in parts for c in codes_of(p)))
    items, done = {}, set()
    with ThreadPoolExecutor(BATCH) as pool:
        for i in range(0, len(codes), BATCH):
            batch = codes[i:i + BATCH]
            for item in pool.map(lambda c: check(c, old.get(c), fetcher), batch):
                items[item['part']] = item
                done.add(item['part'])
            print(f"Checked {sum(all(c in done for c in codes_of(p)) for p in parts)} / {len(parts)} entries", flush=True)
    return items, codes

def main():
    parts = json.load(open('catalog.json', encoding='utf-8'))['parts']
    before = json.load(open('data.json', encoding='utf-8'))['items']
    started = time.time()
    items, codes = check_all(parts, before)
    with open('data.json.tmp', 'w', encoding='utf-8') as f:
        json.dump({'items': items, 'refreshAfterHours': 6}, f, ensure_ascii=False, indent=1)
    os.replace('data.json.tmp', 'data.json')
    saved = json.load(open('data.json', encoding='utf-8'))['items']
    for c in codes:  # never lose the last confirmed time when the source fails
        prev = before.get(c) or {}
        if saved[c]['state'] == 'unknown' and (prev.get('checkedAt') or prev.get('lastKnownAt')):
            assert saved[c].get('lastKnownAt') == (prev.get('checkedAt') or prev.get('lastKnownAt')), 'Lost successful time: ' + c
    not_renewed = [c for c in codes if not saved[c].get('checkedAt') or parse(saved[c]['checkedAt']) < started
                   or parse(saved[c]['checkedAt']) <= parse((before.get(c) or {}).get('checkedAt'))]
    changed = [c for c in codes if (before.get(c) or {}).get('state') != saved[c]['state']
               or (before.get(c) or {}).get('offer') != saved[c].get('offer')]
    report = {'total': len(parts), 'uniqueCodes': len(codes),
              'available': sum(saved.get(p['part'], {}).get('state') == 'available' for p in parts),
              'notRenewed': not_renewed, 'changed': changed}
    print(json.dumps(report, indent=2))
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a') as f:
            f.write(f"Available: {report['available']} / {report['total']}\n\nNot renewed: {', '.join(not_renewed) or 'none'}\n\nChanged: {', '.join(changed) or 'none'}\n")
    with open('refresh-report.json', 'w') as f:
        json.dump(report, f)

if __name__ == '__main__':
    main()
