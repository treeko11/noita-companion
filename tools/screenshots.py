"""Capture screenshots of the running companion with headless Edge, driven over the DevTools protocol.

Needs:  pip install websocket-client pillow   (keep it out of the build venv; any Python works)
Usage:  python companion.py --no-browser --port 8799          # in another terminal
        python tools/screenshots.py http://127.0.0.1:8799/ <out dir> tools/scenes.json

scenes.json is a list of {"name": "...", "js": "..."}: each js snippet runs in the page (it can use the page's
globals: items, S, M, C, goTo, flyTo, fit, render, showTip, toggleItem, toggleMat, $ ...), then the page is
captured to <out dir>/<name>.png at 1440x900.
"""
import json, os, subprocess, sys, time, base64, tempfile, urllib.request, io, shutil
import websocket
from PIL import Image

URL, OUT, SCENES = sys.argv[1], sys.argv[2], sys.argv[3]
# Edge on Windows; Chrome/Chromium elsewhere (cloud sessions run Linux). SHOT_BROWSER overrides.
EDGE = os.environ.get('SHOT_BROWSER') or next((p for p in [
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    shutil.which('chromium'), shutil.which('chromium-browser'), shutil.which('google-chrome'), shutil.which('google-chrome-stable'),
    shutil.which('microsoft-edge')] if p and os.path.exists(p)), None)
if not EDGE: sys.exit('No Chromium-based browser found. Install chromium or set SHOT_BROWSER to its path.')
W, H = 1440, 900
os.makedirs(OUT, exist_ok=True)
prof = tempfile.mkdtemp(prefix='edgeshot')     # fresh profile: tracking starts at the defaults
proc = subprocess.Popen([EDGE, '--headless=new', '--remote-debugging-port=9333', f'--user-data-dir={prof}',
                         f'--window-size={W},{H}', '--hide-scrollbars', '--force-device-scale-factor=1', 'about:blank'])
try:
    for _ in range(60):
        try:
            tabs = json.load(urllib.request.urlopen('http://127.0.0.1:9333/json'))
            page = next(t for t in tabs if t['type'] == 'page'); break
        except Exception:
            time.sleep(0.5)
    ws = websocket.create_connection(page['webSocketDebuggerUrl'], timeout=120)
    n = [0]

    def cmd(method, **params):
        n[0] += 1
        ws.send(json.dumps(dict(id=n[0], method=method, params=params)))
        while True:
            m = json.loads(ws.recv())
            if m.get('id') == n[0]:
                if 'error' in m: raise RuntimeError(m['error'])
                return m.get('result', {})

    def js(expr):
        r = cmd('Runtime.evaluate', expression=expr, awaitPromise=True, returnByValue=True)
        if 'exceptionDetails' in r: raise RuntimeError(r['exceptionDetails'])
        return r.get('result', {}).get('value')

    cmd('Emulation.setDeviceMetricsOverride', width=W, height=H, deviceScaleFactor=1, mobile=False)
    cmd('Page.enable'); cmd('Page.navigate', url=URL)
    for _ in range(240):            # wait for the first scan and data load
        time.sleep(0.5)
        try:
            if js('typeof D !== "undefined" && !!D && items.length > 0'): break
        except Exception:
            pass

    def settle():
        time.sleep(2.5)
        js('new Promise(r => { const imgs = [...document.images]; let t = 0; const c = () => (imgs.every(i => i.complete) && [...tiles.values()].every(t => t.ready)) || t++ > 40 ? r(1) : setTimeout(c, 250); c(); })')

    for sc in json.load(open(SCENES, encoding='utf-8')):
        js(sc['js'])
        settle()
        data = base64.b64decode(cmd('Page.captureScreenshot', format='png')['data'])
        Image.open(io.BytesIO(data)).convert('RGB').save(os.path.join(OUT, sc['name'] + '.png'), optimize=True)
        print('saved', sc['name'])
finally:
    proc.terminate()
