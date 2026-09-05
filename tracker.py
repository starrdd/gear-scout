#!/usr/bin/env python3
"""Local eBay gear tracker. Python 3.9+, standard library only."""
import argparse
import base64
import datetime as dt
import html
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent

def atomic_json(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2), encoding='utf-8')
    tmp.replace(path)

def load_config(path):
    c = json.loads(path.read_text())
    assert re.fullmatch(r'\d{5}', c['zip_code']), 'zip_code must be five digits'
    for key, low, high in [('radius_miles', 1, 500), ('interval_minutes', 15, 1440), ('alert_discount_percent', 0, 90), ('min_seller_feedback_percent', 0, 100), ('min_seller_feedback_count', 0, 1000000)]:
        assert isinstance(c[key], (int, float)) and math.isfinite(c[key]) and low <= c[key] <= high, f'Invalid {key}'
    assert c['mode'] in ('shipped', 'pickup', 'both'), 'mode must be shipped, pickup, or both'
    assert c['condition_ids'] and all(str(x) in ('1000','1500','2000','2010','2020','2030','2500','2750','3000','4000','5000','6000') for x in c['condition_ids']), 'Invalid condition IDs (parts condition is intentionally excluded)'
    assert c['searches'], 'Add at least one search'
    names = set()
    for s in c['searches']:
        assert s['name'] not in names and s['name'].strip(), 'Search names must be unique'
        names.add(s['name'])
        assert s['query'].strip(), 'Search query cannot be empty'
        re.compile(s['title_pattern'], re.I)
        for key in ('max_price', 'typical_used_price'):
            assert isinstance(s[key], (int, float)) and math.isfinite(s[key]) and s[key] > 0, f'Invalid {key}'
    return c

class Ebay:
    def __init__(self):
        self.token = None
        self.expires = 0

    def request(self, url, data=None, headers=None):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers or {}), timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            hints = {401: 'Check your production credentials/token.', 403: 'Your eBay keyset may lack production Browse API approval.', 429: 'eBay rate limit reached. Wait before retrying.'}
            raise RuntimeError(f'eBay HTTP {e.code}. {hints.get(e.code, "Try again later.")}') from None
        except (urllib.error.URLError, TimeoutError):
            raise RuntimeError('Could not reach eBay. Check your internet connection.') from None

    def access_token(self):
        if self.token and time.time() < self.expires:
            return self.token
        client, secret = os.getenv('EBAY_CLIENT_ID'), os.getenv('EBAY_CLIENT_SECRET')
        if not client or not secret:
            raise RuntimeError('Live checks need EBAY_CLIENT_ID and EBAY_CLIENT_SECRET. See README.md; use --demo to try the tracker without credentials.')
        auth = base64.b64encode(f'{client}:{secret}'.encode()).decode()
        result = self.request('https://api.ebay.com/identity/v1/oauth2/token', urllib.parse.urlencode({'grant_type': 'client_credentials', 'scope': 'https://api.ebay.com/oauth/api_scope'}).encode(), {'Authorization': 'Basic ' + auth, 'Content-Type': 'application/x-www-form-urlencoded'})
        self.token = result['access_token']
        self.expires = time.time() + result['expires_in'] - 60
        return self.token

    def search(self, s, c, mode):
        filters = [f'price:[..{s["max_price"]}]', 'priceCurrency:USD', 'conditionIds:{' + '|'.join(map(str,c['condition_ids'])) + '}', 'buyingOptions:{FIXED_PRICE}', 'itemLocationCountry:US']
        if mode == 'pickup':
            filters += ['deliveryOptions:{SELLER_ARRANGED_LOCAL_PICKUP}', 'pickupCountry:US', f'pickupPostalCode:{c["zip_code"]}', f'pickupRadius:{c["radius_miles"]}', 'pickupRadiusUnit:mi']
        else:
            filters += ['deliveryCountry:US', f'deliveryPostalCode:{c["zip_code"]}']
        items = []
        # Bounded pagination: at most 600 newest matches per search and delivery mode.
        for offset in range(0, 600, 200):
            params = {'q': s['query'], 'filter': ','.join(filters), 'sort': 'newlyListed', 'limit': 200, 'offset': offset}
            result = self.request('https://api.ebay.com/buy/browse/v1/item_summary/search?' + urllib.parse.urlencode(params), headers={'Authorization': 'Bearer ' + self.access_token(), 'X-EBAY-C-MARKETPLACE-ID': 'EBAY_US', 'X-EBAY-C-ENDUSERCTX': 'contextualLocation=' + urllib.parse.quote('country=US,zip=' + c['zip_code'], safe='')})
            items.extend(result.get('itemSummaries', []))
            if not result.get('next'):
                return items, False
        return items, True

def evaluate(item, s, c, mode):
    title = item.get('title', '')
    if not re.search(s['title_pattern'], title, re.I):
        return None
    if any(re.search(pattern, title, re.I) for pattern in c['exclude_title_patterns']):
        return None
    if str(item.get('conditionId')) not in set(map(str,c['condition_ids'])) or 'FIXED_PRICE' not in item.get('buyingOptions', []):
        return None
    price = item.get('price', {})
    if price.get('currency') != 'USD':
        return None
    amount = float(price['value'])
    if not math.isfinite(amount) or amount <= 0:
        return None
    shipping = 0.0 if mode == 'pickup' else None
    if mode != 'pickup':
        costs = [float(o['shippingCost']['value']) for o in item.get('shippingOptions', []) if o.get('shippingCost', {}).get('currency') == 'USD']
        costs = [v for v in costs if math.isfinite(v) and v >= 0]
        if costs:
            shipping = min(costs)
    total = round(amount + shipping, 2) if shipping is not None else None
    if amount > s['max_price'] or (total is not None and total > s['max_price']):
        return None
    discount = round(100 * (1-total/s['typical_used_price']), 1) if total is not None else None
    seller = item.get('seller', {})
    trusted = float(seller.get('feedbackPercentage', 0)) >= c['min_seller_feedback_percent'] and int(seller.get('feedbackScore', 0)) >= c['min_seller_feedback_count']
    warnings = []
    if shipping is None:
        warnings.append('Shipping unknown — verify total')
    if not trusted:
        warnings.append('Limited or low seller feedback — inspect carefully')
    if discount is not None and discount > 55:
        warnings.append('Unusually cheap — inspect completeness and condition')
    good = discount is not None and discount >= c['alert_discount_percent'] and not warnings
    url = item.get('itemWebUrl', '')
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != 'https' or not (parsed.hostname == 'ebay.com' or (parsed.hostname or '').endswith('.ebay.com')):
        url = 'https://www.ebay.com/'
    return {'id': item['itemId'], 'title': title, 'gear': s['name'], 'price': amount, 'shipping': shipping, 'total': total, 'discount': discount, 'typical': s['typical_used_price'], 'good': good, 'warnings': warnings, 'mode': mode, 'url': url, 'condition': item.get('condition', 'Unknown'), 'location': item.get('itemLocation', {}).get('city', ''), 'seller': seller.get('username', 'Unknown')}

def should_alert(row, state):
    previous = state.get(row['id'])
    return row['good'] and (previous is None or row['total'] <= previous - 5)

def demo_items():
    def item(i, title, price, shipping=0, feedback=100):
        return {'itemId': 'demo-' + str(i), 'title': title, 'price': {'value': str(price), 'currency': 'USD'}, 'shippingOptions': [] if shipping is None else [{'shippingCost': {'value': str(shipping), 'currency': 'USD'}}], 'conditionId': '3000', 'condition': 'Used', 'buyingOptions': ['FIXED_PRICE'], 'seller': {'username': 'sample-seller', 'feedbackPercentage': str(feedback), 'feedbackScore': 120}, 'itemWebUrl': 'https://www.ebay.com/', 'itemLocation': {'city': 'Los Angeles'}}
    return [item(1, 'Yamaha P-125 digital piano with stand', 285, 35), item(2, 'Roland FP-30X digital piano', 375, 45), item(3, 'Kawai ES120 digital piano', 390, None), item(4, 'Yamaha P-45 keyboard', 150, 30, 90), item(5, 'Yamaha P-125 dust cover', 20), item(6, 'AKG C214 condenser microphone', 175, 15)]

def marketplace_links(search, config):
    """Human-facing searches for sources without a suitable listings API."""
    query, maximum = search['query'], search['max_price']
    encoded = urllib.parse.quote_plus(query)
    return [
        ('Facebook', f'https://www.facebook.com/marketplace/losangeles/search?query={encoded}&maxPrice={maximum}'),
        ('OfferUp', f'https://offerup.com/search?q={encoded}&price_max={maximum}'),
        ('Craigslist LA', 'https://losangeles.craigslist.org/search/msa?' + urllib.parse.urlencode({'query': query, 'max_price': maximum, 'sort': 'date'})),
        ('Craigslist OC', 'https://orangecounty.craigslist.org/search/msa?' + urllib.parse.urlencode({'query': query, 'max_price': maximum, 'sort': 'date'})),
        ('Reverb', 'https://reverb.com/marketplace?' + urllib.parse.urlencode({'query': query, 'price_max': maximum})),
        ('Guitar Center', 'https://www.guitarcenter.com/search?Ntt=' + encoded),
        ('Music Go Round', 'https://musicgoround.com/search?q=' + encoded),
        ('Sweetwater', 'https://www.sweetwater.com/used/listings?query=' + encoded),
        ('Mercari', 'https://www.mercari.com/search/?' + urllib.parse.urlencode({'keyword': query, 'maxPrice': maximum})),
        ('ShopGoodwill', 'https://shopgoodwill.com/categories/listing?' + urllib.parse.urlencode({'st': query, 'hp': maximum})),
    ]

def render(rows, c, errors, demo, path, alerts):
    esc = lambda x: html.escape(str(x), quote=True)
    money = lambda x: 'Unknown' if x is None else f'${x:,.0f}'
    cards = []
    for r in sorted(rows, key=lambda x: (not x['good'], -(x['discount'] if x['discount'] is not None else -999))):
        badge = 'Good deal' if r['good'] else 'Review details'
        discount = 'Unscored' if r['discount'] is None else f'{r["discount"]:g}% below estimate'
        cards.append(f'<article><div class="row"><span class="tag {"good" if r["good"] else ""}">{badge}</span><small>{esc(r["mode"])}</small></div><h2>{esc(r["title"])}</h2><div class="price">{money(r["total"])}</div><p>{money(r["price"])} item + {money(r["shipping"])} shipping / pickup</p><strong>{discount}</strong><p>Used benchmark: {money(r["typical"])} · {esc(r["condition"])}</p><p>{esc(r["location"])} · {esc(r["seller"])}</p><p class="warning">{esc(" · ".join(r["warnings"]))}</p>' + ('<span class="sample">Sample listing — not for sale</span>' if demo else f'<a href="{esc(r["url"])}" target="_blank" rel="noopener noreferrer">View on eBay ↗</a>') + '</article>')
    searches = []
    for s in c['searches']:
        url = 'https://www.ebay.com/sch/i.html?' + urllib.parse.urlencode({'_nkw': s['query'], '_udhi': s['max_price'], 'LH_BIN': 1, 'LH_ItemCondition': '|'.join(map(str,c['condition_ids'])), '_sop': 10, '_stpos': c['zip_code']})
        local = url + '&LH_Distance=' + str(c['radius_miles'])
        links = [('eBay', url), ('eBay nearby', local)] + marketplace_links(s, c)
        searches.append(f'<tr><td>{esc(s["name"])}</td><td>{money(s["max_price"])}</td><td>{money(s["typical_used_price"])}</td><td class="links">' + ' '.join(f'<a href="{esc(href)}" target="_blank" rel="noopener noreferrer">{esc(label)} ↗</a>' for label, href in links) + '</td></tr>')
    status = 'DEMO · invented examples, no live listings' if demo else 'LIVE CHECK · ' + dt.datetime.now().astimezone().strftime('%b %d, %Y · %I:%M %p %Z')
    gear_json = json.dumps([{'name': s['name'], 'max': s['max_price'], 'typical': s['typical_used_price']} for s in c['searches']]).replace('</', '<\\/')
    content = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Gear Scout · used gear deals</title><style>
:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f5f4ed;color:#21372f;font:16px system-ui,sans-serif}main{max-width:1150px;margin:auto;padding:40px 24px}header{border-bottom:2px solid #21372f;padding-bottom:24px}.eyebrow{font-size:12px;letter-spacing:2px;font-weight:700}h1{font-size:52px;letter-spacing:-3px;margin:12px 0}h2{font-size:20px;line-height:1.35}p{color:#52655d;line-height:1.5}a{color:#215e44;font-weight:650}header p{max-width:750px}.banner{padding:15px;background:#e5eadd;border-radius:8px;margin:20px 0}.error{background:#ffe8cf}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px;margin:24px 0}article,.calculator{background:white;border:1px solid #d4ddd3;border-radius:12px;padding:24px}article p{font-size:13px}.row{display:flex;justify-content:space-between;align-items:center;gap:12px}.tag{font-size:12px;background:#f4e7c9;padding:6px 10px;border-radius:20px}.good{background:#daf4bd}.price{font-size:36px;font-weight:700;letter-spacing:-1px}.warning{color:#93501a;min-height:20px}.sample{font-size:13px;color:#7c6950}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:14px 10px;border-bottom:1px solid #d3dacf;font-size:14px;vertical-align:top}.table{overflow:auto}.links{min-width:520px}.links a{display:inline-block;margin:0 10px 8px 0;font-size:13px}.formgrid{display:grid;grid-template-columns:2fr repeat(3,1fr);gap:12px}.field label{display:block;font-size:12px;font-weight:700;margin-bottom:6px}.field input,.field select{width:100%;padding:10px;border:1px solid #aebbb3;border-radius:7px;background:white;font:inherit}.checks{display:flex;flex-wrap:wrap;gap:16px;margin:16px 0}.verdict{background:#edf2e9;border-radius:8px;padding:18px;min-height:76px}.verdict strong{font-size:20px}button{border:0;border-radius:7px;background:#215e44;color:white;padding:11px 16px;font-weight:700;cursor:pointer}footer{margin-top:30px;font-size:13px}small{color:#64766d}@media(max-width:700px){main{padding:24px 16px}h1{font-size:40px}.formgrid{grid-template-columns:1fr 1fr}.field:first-child{grid-column:1/-1}td,th{min-width:100px}}
</style></head><body><main>'''
    content += f'<header><div class="eyebrow">GEAR SCOUT / SOUTH BAY & LOS ANGELES</div><h1>Good gear. Better prices.</h1><p>Your used music gear watchlist. Prices include known shipping, before tax. Compare the details, then inspect the instrument before buying.</p></header><div class="banner">{esc(status)}<br><small>ZIP {esc(c["zip_code"])} · {c["radius_miles"]} mile pickup radius · {len(rows)} candidates · {len(alerts)} new deal alerts</small></div>'
    content += ''.join(f'<div class="banner error">{esc(e)}</div>' for e in errors)
    content += '<div class="grid">' + (''.join(cards) or '<p>No qualifying candidates in this check. Browse your saved searches below or adjust config.json.</p>') + '</div>'
    content += '''<h2>Score any listing</h2><p>For Facebook, OfferUp, Craigslist, or anything you find elsewhere. The calculation stays in this browser and is never uploaded.</p><section class="calculator"><div class="formgrid"><div class="field"><label for="gear">Gear</label><select id="gear"></select></div><div class="field"><label for="asking">Asking price</label><input id="asking" inputmode="decimal" type="number" min="0" step="0.01" placeholder="300"></div><div class="field"><label for="shipping">Shipping</label><input id="shipping" inputmode="decimal" type="number" min="0" step="0.01" value="0"></div><div class="field"><label for="extras">Travel / fees</label><input id="extras" inputmode="decimal" type="number" min="0" step="0.01" value="0"></div></div><div class="checks"><label><input id="working" type="checkbox" checked> Confirmed working</label><label><input id="complete" type="checkbox" checked> Power supply / essential parts included</label><label><input id="testable" type="checkbox" checked> Can test or has buyer protection</label></div><button id="score" type="button">Score this listing</button><div id="verdict" class="verdict" aria-live="polite"><p>Enter the price to see the landed total and deal score.</p></div></section>'''
    content += '<h2>Your cross-market watchlist</h2><p>These links open ready-made searches. On Facebook, OfferUp, Craigslist, and Reverb, use the marketplace’s Save Search or alert control after opening the link.</p><div class="table"><table><tr><th>Gear</th><th>Max total</th><th>Used estimate</th><th>Search sources</th></tr>' + ''.join(searches) + '</table></div>'
    content += f'''<script>const gear={gear_json};const select=document.getElementById('gear');for(const [i,g] of gear.entries()){{const o=document.createElement('option');o.value=i;o.textContent=`${{g.name}} — benchmark $${{g.typical}}`;select.appendChild(o)}}document.getElementById('score').addEventListener('click',()=>{{const g=gear[Number(select.value)],numbers=['asking','shipping','extras'].map(id=>Number(document.getElementById(id).value)),box=document.getElementById('verdict');if(numbers.some(n=>!Number.isFinite(n)||n<0)||numbers[0]<=0){{box.innerHTML='<p class="warning">Enter a valid asking price. Costs cannot be negative.</p>';return}}const total=numbers.reduce((a,b)=>a+b,0),discount=100*(1-total/g.typical),risks=[];if(!document.getElementById('working').checked)risks.push('working condition is unconfirmed');if(!document.getElementById('complete').checked)risks.push('essential parts may be missing');if(!document.getElementById('testable').checked)risks.push('no testing or buyer protection');let label=discount>=20?'Good price to investigate':discount>=10?'Fair-to-good price':'Not a standout deal';if(total>g.max)label='Over your current budget';if(discount>55)risks.push('price is unusually low—check for scams or hidden faults');box.replaceChildren();const strong=document.createElement('strong');strong.textContent=label;const p=document.createElement('p');p.textContent=`Total $${{total.toFixed(2)}} · ${{Math.abs(discount).toFixed(1)}}% ${{discount>=0?'below':'above'}} your $${{g.typical}} benchmark.${{risks.length?' Verify '+risks.join(', ')+'.':''}}`;box.append(strong,p)}});</script>'''
    content += '<footer><p>Benchmarks are editable starter assumptions, not verified sold-market averages. The calculator is a screening aid; it cannot inspect photos, verify sellers, or account for taxes and repairs unless you include them as fees.</p><p>Only eBay is designed for automatic retrieval after API approval. Other marketplace links use official browsing and saved-alert features; no Facebook or OfferUp account scraping is performed. Search URLs and filters can change, so verify each marketplace’s displayed location, radius, condition, and maximum price.</p></footer></main></body></html>'
    tmp = path.with_suffix('.tmp')
    tmp.write_text(content, encoding='utf-8')
    tmp.replace(path)

def run(c, api, demo, directory, desktop):
    rows, errors = {}, []
    modes = ['shipped', 'pickup'] if c['mode'] == 'both' else [c['mode']]
    for s in c['searches']:
        for mode in (['shipped'] if demo else modes):
            try:
                items, truncated = (demo_items(), False) if demo else api.search(s, c, mode)
                if truncated:
                    errors.append(f'{s["name"]} ({mode}): capped at 600 newest results; narrow the query if needed.')
                for item in items:
                    row = evaluate(item, s, c, mode)
                    if row:
                        old = rows.get(row['id'])
                        if old is None or (row['total'] is not None and (old['total'] is None or row['total'] < old['total'])):
                            rows[row['id']] = row
            except (RuntimeError, KeyError, ValueError, TypeError) as e:
                errors.append(f'{s["name"]} ({mode}): {e}')
                if 'credentials' in str(e) or 'HTTP 401' in str(e) or 'HTTP 403' in str(e) or 'HTTP 429' in str(e):
                    break
        if errors and any(x in errors[-1] for x in ('credentials', 'HTTP 401', 'HTTP 403', 'HTTP 429')):
            break
    directory.mkdir(parents=True, exist_ok=True)
    state_path = directory / 'alert-state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() and not demo else {}
    alerts = [r for r in rows.values() if should_alert(r, state)]
    if not demo:
        for r in alerts:
            event = dict(r, alerted_at=dt.datetime.now(dt.timezone.utc).isoformat())
            with (directory / 'alerts.jsonl').open('a') as f:
                f.write(json.dumps(event) + '\n')
            state[r['id']] = r['total']
        atomic_json(state_path, state)
        if alerts:
            print(f'\a{len(alerts)} new deals! Open the report for details.', flush=True)
            if desktop and sys.platform == 'darwin':
                # Fixed text avoids inserting untrusted listing titles into AppleScript.
                result = subprocess.run(['osascript', '-e', f'display notification "{len(alerts)} new music gear deals. Open your Gear Scout report." with title "Gear Scout"'], capture_output=True)
                if result.returncode:
                    errors.append('Desktop notification failed; deals are saved in alerts.jsonl.')
    atomic_json(directory / 'results.json', {'demo': demo, 'checked_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'errors': errors, 'items': list(rows.values())})
    report = directory / 'report.html'
    render(list(rows.values()), c, errors, demo, report, alerts)
    print(f'{len(rows)} candidates; {len(errors)} issues. Report: {report}')
    for error in errors:
        print(error, file=sys.stderr)
    return report, bool(errors)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=ROOT / 'config.json')
    p.add_argument('--demo', action='store_true', help='Invented examples; no network or notifications')
    p.add_argument('--watch', action='store_true', help='Keep checking at the configured interval')
    p.add_argument('--open', action='store_true', help='Open the report in your browser')
    p.add_argument('--desktop', action='store_true', help='Enable macOS desktop notifications')
    p.add_argument('--output', type=Path, help='Override result directory')
    args = p.parse_args()
    api = Ebay()
    try:
        while True:
            c = load_config(args.config)
            report, issues = run(c, api, args.demo, args.output or ROOT / ('demo' if args.demo else 'data'), args.desktop)
            if args.open:
                webbrowser.open(report.resolve().as_uri())
                args.open = False
            if not args.watch or args.demo:
                return 1 if issues else 0
            print(f'Next check in {c["interval_minutes"]} minutes. Ctrl+C to stop.', flush=True)
            time.sleep(c['interval_minutes'] * 60)
    except KeyboardInterrupt:
        print('\nTracker stopped.')
        return 0
    except (OSError, ValueError, AssertionError, KeyError, re.error) as e:
        print(f'Cannot run tracker: {e}', file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(main())
