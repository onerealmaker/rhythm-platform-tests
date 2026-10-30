"""Rhythm platform test: does a Chrome-type browser keep running an extension
with the "background" permission after its last window closes, and does the
extension see keyboard use in another app?

Test only. Uses a fresh, empty browser profile; nothing is signed in and the
test extension has no network access.

Usage: python scripts/run_test.py --browser chrome-for-testing|chrome|edge|chromium
Writes results/<os>-<browser>.md and prints the same summary.
"""
import argparse
import glob
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT = os.path.join(ROOT, 'test-extension')
SYSTEM = {'Windows': 'windows', 'Darwin': 'mac', 'Linux': 'linux'}[platform.system()]

IDLE_PHASE_S = 100       # nobody touches the computer
TYPING_PHASE_S = 120     # keyboard use in another app (Notepad / TextEdit)
MAX_TICK_GAP_S = 45      # ticks come every 30 s; more than 45 s apart = a gap


def log(msg):
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)


def find_browser(name):
    if name == 'chrome-for-testing':
        dest = os.path.join(tempfile.gettempdir(), 'cft')
        out = subprocess.run(
            'npx --yes @puppeteer/browsers install chrome@stable --path "' + dest + '"',
            shell=True, capture_output=True, text=True, check=True).stdout
        path = out.strip().splitlines()[-1].split(' ', 1)[1].strip()
        return path
    candidates = {
        ('windows', 'chrome'): [r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                                r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe'],
        ('windows', 'edge'): [r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
                              r'C:\Program Files\Microsoft\Edge\Application\msedge.exe'],
        ('mac', 'chrome'): ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'],
        ('mac', 'edge'): ['/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'],
        ('linux', 'chromium'): [os.environ.get('CHROMIUM_PATH', '')],
    }.get((SYSTEM, name), [])
    for c in candidates:
        if c and os.path.exists(c):
            return c
    sys.exit(f'Browser {name} not found on {SYSTEM}')


def launch(path, profile):
    args = [path, f'--user-data-dir={profile}', '--no-first-run', '--no-default-browser-check',
            f'--disable-extensions-except={EXT}', f'--load-extension={EXT}',
            # Branded Chrome 137+ ignores --load-extension unless this is set.
            '--disable-features=DisableLoadExtensionCommandLineSwitch',
            'about:blank']
    if SYSTEM == 'linux':
        args.insert(1, '--no-sandbox')
    log('Launching: ' + ' '.join(args))
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def browser_process_count(profile):
    """Browser processes still running with our test profile."""
    if SYSTEM == 'windows':
        ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*" +
              profile.replace("'", "''") + "*' } | Measure-Object | Select -Expand Count")
        out = subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, text=True).stdout
        return int(out.strip() or 0)
    out = subprocess.run(['pgrep', '-f', profile], capture_output=True, text=True).stdout
    return len([l for l in out.splitlines() if l.strip()])


def type_in_other_app(seconds):
    """Keyboard use in a non-browser app. Returns a note on what was done."""
    if SYSTEM == 'windows':
        ps = (
            "Start-Process notepad; Start-Sleep 3; $w = New-Object -ComObject WScript.Shell; "
            "$null = $w.AppActivate('Notepad'); $end = (Get-Date).AddSeconds(" + str(seconds) + "); "
            "while ((Get-Date) -lt $end) { $w.SendKeys('rhythm test '); Start-Sleep -Milliseconds 700 }; "
            "Stop-Process -Name notepad -Force"
        )
        r = subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, text=True)
        return 'Typed in Notepad' + ('' if r.returncode == 0 else f' (error: {r.stderr.strip()[:200]})')
    if SYSTEM == 'mac':
        subprocess.run(['open', '-a', 'TextEdit'])
        time.sleep(4)
        script = ('tell application "TextEdit" to activate\n'
                  'tell application "TextEdit" to make new document\n'
                  'delay 1\n'
                  f'set endTime to (current date) + {seconds}\n'
                  'repeat while (current date) < endTime\n'
                  '  tell application "System Events" to keystroke "rhythm test "\n'
                  '  delay 0.7\n'
                  'end repeat')
        r = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
        subprocess.run(['osascript', '-e', 'tell application "TextEdit" to quit saving no'],
                       capture_output=True)
        return 'Typed in TextEdit' + ('' if r.returncode == 0 else f' (error: {r.stderr.strip()[:200]})')
    time.sleep(seconds)
    return 'No keyboard simulation on Linux (idle expected)'


def windows_startup_entries():
    """Entries in the current user's Windows startup list (Run key)."""
    ps = ("Get-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run' | "
          "Select-Object * -ExcludeProperty PS* | ConvertTo-Json -Compress")
    out = subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, text=True).stdout
    try:
        data = json.loads(out) if out.strip() else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def read_ticks(profile):
    rows = {}
    pattern = re.compile(r'\{"s":"(\w+)","src":"([^"]*)","t":([0-9.e+]+),"w":(\d+)\}')
    for f in glob.glob(os.path.join(profile, 'Default', 'Local Extension Settings', '*', '*')):
        try:
            text = open(f, 'rb').read().decode('latin-1')
        except OSError:
            continue
        for s, src, t, w in pattern.findall(text):
            rows[float(t)] = {'t': float(t) / 1000, 's': s, 'src': src, 'w': int(w)}
    return [rows[k] for k in sorted(rows)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--browser', required=True)
    a = ap.parse_args()
    path = find_browser(a.browser)
    profile = tempfile.mkdtemp(prefix='rhythm-test-profile-')
    startup_before = windows_startup_entries() if SYSTEM == 'windows' else {}

    t_launch = time.time()
    proc = launch(path, profile)
    time.sleep(45)  # the extension closes every window at about 20 s
    t_closed = t_launch + 20
    alive_after_close = browser_process_count(profile)
    log(f'Browser processes 25 s after the last window closed: {alive_after_close}')

    startup_after = windows_startup_entries() if SYSTEM == 'windows' else {}
    new_startup = {k: v for k, v in startup_after.items() if k not in startup_before}

    log(f'Idle phase: {IDLE_PHASE_S} s without input')
    t_idle0 = time.time()
    time.sleep(IDLE_PHASE_S)
    t_idle1 = time.time()

    log(f'Typing phase: {TYPING_PHASE_S} s in another app')
    t_type0 = time.time()
    typing_note = type_in_other_app(TYPING_PHASE_S)
    t_type1 = time.time()
    log(typing_note)
    time.sleep(35)  # let the last tick of the typing phase be recorded
    alive_at_end = browser_process_count(profile)

    # Stop the browser, then read what the extension recorded.
    try:
        proc.terminate()
        proc.wait(timeout=15)
    except Exception:
        proc.kill()
    if SYSTEM == 'windows':
        subprocess.run(['powershell', '-NoProfile', '-Command',
                        "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*" +
                        profile + "*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                       capture_output=True)
    time.sleep(3)
    ticks = read_ticks(profile)

    after_close = [k for k in ticks if k['t'] > t_closed + 5]
    gaps = [round(b['t'] - a2['t']) for a2, b in zip(after_close, after_close[1:])
            if b['t'] - a2['t'] > MAX_TICK_GAP_S]
    no_window = [k for k in after_close if k['w'] == 0]
    idle_ok = [k for k in no_window if t_idle0 + 65 <= k['t'] <= t_idle1 and k['s'] == 'idle']
    typing_active = [k for k in no_window if t_type0 + 5 <= k['t'] <= t_type1 + 30 and k['s'] == 'active']
    typing_ticks = [k for k in no_window if t_type0 + 5 <= k['t'] <= t_type1 + 30]

    def verdict(ok, inconclusive=False):
        return 'PASS' if ok else ('INCONCLUSIVE' if inconclusive else 'FAIL')

    loaded = len(ticks) > 0
    checks = [
        ('Extension loaded and recorded', verdict(loaded)),
        ('Browser still running after its last window closed',
         verdict(alive_after_close > 0 and alive_at_end > 0)),
        ('Kept recording with no window open, no gaps over 45 s',
         verdict(len(no_window) >= 8 and not gaps)),
        ('Computer correctly seen as "not in use" while nobody typed', verdict(len(idle_ok) >= 1)),
        ('Typing in another app seen as "in use", with no browser window open',
         verdict(len(typing_active) >= 2, inconclusive=(SYSTEM == 'linux' or 'error' in typing_note))),
    ]
    if SYSTEM == 'windows':
        checks.append(('Browser registered itself to start at Windows sign-in',
                       verdict(bool(new_startup))))

    name = f'{SYSTEM}-{a.browser}'
    lines = [f'# Rhythm platform test: {name}', '',
             f'Browser: `{path}`', f'OS: {platform.platform()}', '',
             '| Check | Result |', '|---|---|']
    lines += [f'| {c} | **{r}** |' for c, r in checks]
    lines += ['', f'Typing step: {typing_note}',
              f'New Windows startup entries: {json.dumps(new_startup) if new_startup else "none"}'
              if SYSTEM == 'windows' else '',
              f'Gaps over 45 s after windows closed: {gaps or "none"}', '',
              '## Records', '', '| Time | Event | Computer | Windows open |', '|---|---|---|---|']
    lines += [f'| {time.strftime("%H:%M:%S", time.gmtime(k["t"]))} UTC | {k["src"]} | {k["s"]} | {k["w"]} |'
              for k in ticks]
    report = '\n'.join(lines) + '\n'
    os.makedirs(os.path.join(ROOT, 'results'), exist_ok=True)
    with open(os.path.join(ROOT, 'results', name + '.md'), 'w', encoding='utf-8') as f:
        f.write(report)
    print(report)
    shutil.rmtree(profile, ignore_errors=True)


if __name__ == '__main__':
    main()
