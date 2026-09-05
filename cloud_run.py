"""One-shot Actions entry point. Demo by default; durable state in private repo."""
import base64
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request
import tracker

STATE_FILE = 'cloud-alert-state.json'

def github_state(method='GET', body=None):
    repo = os.environ['GITHUB_REPOSITORY']
    url = 'https://api.github.com/repos/' + repo + '/contents/' + STATE_FILE
    headers = {'Authorization': 'Bearer ' + os.environ['GH_TOKEN'], 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28', 'Content-Type': 'application/json'}
    if method == 'GET':
        url += '?ref=' + urllib.parse.quote(os.environ.get('GITHUB_REF_NAME', 'main'), safe='')
    req = urllib.request.Request(url, headers=headers, method=method, data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        if e.code == 404 and method == 'GET':
            # Distinguish absent state from inaccessible repo (no silent dedup reset).
            repo_req = urllib.request.Request('https://api.github.com/repos/' + repo, headers=headers)
            with urllib.request.urlopen(repo_req, timeout=30):
                return None
        raise RuntimeError(f'GitHub state request failed (HTTP {e.code}); stopping to protect alert history.') from None

def summary(results):
    mode = 'DEMO — invented examples' if results['demo'] else 'LIVE'
    rows = results['items']
    good = [r for r in rows if r['good']]
    text = f'# Gear Scout\n\n**{mode}**\n\nChecked: {results["checked_at"]}\n\n{len(rows)} candidates; {len(good)} pass the deal rules.\n\n'
    if results['demo']:
        text += 'eBay is not being contacted. These examples are not actual listings.\n\n'
    for row in good:
        # Escaping titles prevents API content becoming injected summary markup.
        title = tracker.html.escape(row['title']).replace('\n', ' ')
        text += f'- {title}: **${row["total"]:.2f}**, {row["discount"]}% below estimate.\n'
    if results['errors']:
        text += '\n## Check issues\n\n' + '\n'.join('- ' + tracker.html.escape(e) for e in results['errors']) + '\n'
    text += '\nOpen the Gear Scout website for the full dashboard. A downloadable copy is also available under **gear-scout-report** in Artifacts.\n\nPhone/email deal delivery is not configured; results and alert history are available in this run.\n'
    return text

def main():
    mode = os.getenv('GEAR_SCOUT_MODE', '').strip().lower() or 'demo'
    if mode not in ('demo', 'live'):
        raise ValueError('GEAR_SCOUT_MODE must be demo or live')
    demo = mode == 'demo'
    out = tracker.ROOT / 'cloud-output'
    out.mkdir(exist_ok=True)
    state_path = out / 'alert-state.json'
    original, blob = {}, None
    if not demo:
        if not os.getenv('EBAY_CLIENT_ID') or not os.getenv('EBAY_CLIENT_SECRET'):
            raise ValueError('Live mode needs both eBay secrets. Switch GEAR_SCOUT_MODE back to demo while waiting for approval.')
        blob = github_state()
        if blob:
            original = json.loads(base64.b64decode(blob['content']))
            if not isinstance(original, dict):
                raise ValueError('Stored alert history is invalid')
        tracker.atomic_json(state_path, original)
    c = tracker.load_config(tracker.ROOT / 'config.json')
    _, issues = tracker.run(c, tracker.Ebay(), demo, out, False)
    results = json.loads((out / 'results.json').read_text())
    report = summary(results)
    (out / 'summary.md').write_text(report)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write(report)
    if not demo:
        updated = json.loads(state_path.read_text())
        if updated != original:
            body = {'message': 'Save Gear Scout alert history [skip ci]', 'content': base64.b64encode(state_path.read_bytes()).decode(), 'branch': os.environ.get('GITHUB_REF_NAME', 'main')}
            if blob:
                body['sha'] = blob['sha']
            github_state('PUT', body)
    return 1 if issues else 0

if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        # Avoid printing request objects, headers, or credentials.
        print(f'Cloud check stopped: {type(e).__name__}: {e}', file=sys.stderr)
        sys.exit(1)
