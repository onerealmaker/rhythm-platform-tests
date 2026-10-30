"""Rhythm platform test (version 2).

Checks, on real Windows and Mac machines, whether a Chrome-type browser keeps
running an extension that has the "background" permission after its last
window closes, whether that extension sees keyboard or mouse use in another
app, and (Windows) whether the browser registers itself to start at sign-in
and really starts, with no window, the way Windows starts it at sign-in.

Test only: the test extension has no network access; nothing is signed in.
Results go to results/<name>.md and to GitHub annotations on the run.

Usage:
  python scripts/run_test.py --browser chrome-for-testing|edge|chromium
  python scripts/run_test.py --browser chrome-for-testing|edge --mode signin   (Windows)
"""
import argparse
import glob
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT = os.path.join(ROOT, 'test-extension')
SYSTEM = {'Windows': 'windows', 'Darwin': 'mac', 'Linux': 'linux'}[platform.system()]

IDLE_PHASE_S = 100       # nobody touches the computer
ACTIVE_PHASE_S = 120     # keyboard or mouse use in another app
MAX_TICK_GAP_S = 45      # ticks come every 30 s; more than 45 s apart = a gap
RUN_KEY = r'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'


def log(msg):
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)


def annotate(title, message):
    # Annotations are readable through the GitHub API, unlike artifacts.
    clean = message.replace('%', '%25').replace('\r', '').replace('\n', '%0A')
    print(f'::notice title={title}::{clean}', flush=True)


def ps(command):
    return subprocess.run(['powershell', '-NoProfile', '-Command', command],
                          capture_output=True, text=True)


def find_browser(name):
    if name == 'chrome-for-testing':
        dest = os.path.join(tempfile.gettempdir(), 'cft')
        out = subprocess.run(
            'npx --yes @puppeteer/browsers install chrome@stable --path "' + dest + '"',
            shell=True, capture_output=True, text=True, check=True).stdout
        return out.strip().splitlines()[-1].split(' ', 1)[1].strip()
    candidates = {
        ('windows', 'edge'): [r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
                              r'C:\Program Files\Microsoft\Edge\Application\msedge.exe'],
        ('mac', 'edge'): ['/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'],
        ('linux', 'chromium'): [os.environ.get('CHROMIUM_PATH', '')],
    }.get((SYSTEM, name), [])
    for c in candidates:
        if c and os.path.exists(c):
            return c
    sys.exit(f'Browser {name} not found on {SYSTEM}')


def default_user_data_dir(name):
    local = os.environ.get('LOCALAPPDATA', '')
    return {
        'chrome-for-testing': os.path.join(local, 'Google', 'Chrome for Testing', 'User Data'),
        'edge': os.path.join(local, 'Microsoft', 'Edge', 'User Data'),
    }[name]


def ext_flags():
    return [f'--disable-extensions-except={EXT}', f'--load-extension={EXT}',
            '--disable-features=DisableLoadExtensionCommandLineSwitch']


def launch(path, profile=None):
    args = [path, '--no-first-run', '--no-default-browser-check'] + ext_flags() + ['about:blank']
    if profile:
        args.insert(1, f'--user-data-dir={profile}')
    if SYSTEM == 'linux':
        args.insert(1, '--no-sandbox')
    log('Launching: ' + ' '.join(args))
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def process_count(marker):
    """Running processes whose command line contains `marker`."""
    if SYSTEM == 'windows':
        r = ps("@(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*" +
               marker.replace("'", "''") + "*' }).Count")
        try:
            return int(r.stdout.strip() or 0)
        except ValueError:
            return 0
    out = subprocess.run(['pgrep', '-f', marker], capture_output=True, text=True).stdout
    return len([l for l in out.splitlines() if l.strip()])


def kill_processes(marker):
    if SYSTEM == 'windows':
        ps("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*" +
           marker.replace("'", "''") + "*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    else:
        subprocess.run(['pkill', '-f', marker])
    time.sleep(3)


def mac_mouse(seconds):
    """Move the mouse with system events (Quartz), no app in front needed."""
    import ctypes
    q = ctypes.cdll.LoadLibrary(
        '/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
    cf = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')

    class CGPoint(ctypes.Structure):
        _fields_ = [('x', ctypes.c_double), ('y', ctypes.c_double)]
    q.CGEventCreateMouseEvent.restype = ctypes.c_void_p
    q.CGEventCreateMouseEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, CGPoint, ctypes.c_uint32]
    q.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    end, i = time.time() + seconds, 0
    while time.time() < end:
        e = q.CGEventCreateMouseEvent(None, 5, CGPoint(200 + (i % 300), 200 + (i % 7)), 0)
        q.CGEventPost(0, e)
        cf.CFRelease(e)
        i += 1
        time.sleep(0.5)


def activity_in_other_app(seconds, method):
    """Keyboard or mouse use outside the browser. Returns a note."""
    if SYSTEM == 'windows':
        r = ps("Start-Process notepad; Start-Sleep 3; $w = New-Object -ComObject WScript.Shell; "
               "$null = $w.AppActivate('Notepad'); $end = (Get-Date).AddSeconds(" + str(seconds) + "); "
               "while ((Get-Date) -lt $end) { $w.SendKeys('rhythm test '); Start-Sleep -Milliseconds 700 }; "
               "Stop-Process -Name notepad -Force")
        return 'Typed in Notepad' + ('' if r.returncode == 0 else f' (error: {r.stderr.strip()[:200]})')
    if SYSTEM == 'mac':
        if method == 'mouse':
            try:
                mac_mouse(seconds)
                return 'Moved the mouse with system events'
            except Exception as e:  # noqa: BLE001
                return f'Mouse simulation (error: {e})'
        r = subprocess.run(['caffeinate', '-u', '-t', str(seconds)], capture_output=True, text=True)
        return 'Declared user activity (caffeinate -u)' + ('' if r.returncode == 0 else ' (error)')
    time.sleep(seconds)
    return 'No activity simulation on Linux (not in use expected)'


def run_key_values():
    r = ps("$p = Get-ItemProperty '" + RUN_KEY + "' -ErrorAction SilentlyContinue; "
           "if ($p) { $p | Select-Object * -ExcludeProperty PS* | ConvertTo-Json -Compress } else { '{}' }")
    try:
        data = json.loads(r.stdout.strip() or '{}')
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def read_ticks(profile_dir):
    rows = {}
    pattern = re.compile(r'\{"s":"(\w+)","src":"([^"]*)","t":([0-9.e+]+),"w":(\d+)\}')
    for f in glob.glob(os.path.join(profile_dir, 'Default', 'Local Extension Settings', '*', '*')):
        try:
            text = open(f, 'rb').read().decode('latin-1')
        except OSError:
            continue
        for s, src, t, w in pattern.findall(text):
            rows[float(t)] = {'t': float(t) / 1000, 's': s, 'src': src, 'w': int(w)}
    return [rows[k] for k in sorted(rows)]


def gaps_in(ticks):
    return [round(b['t'] - a['t']) for a, b in zip(ticks, ticks[1:]) if b['t'] - a['t'] > MAX_TICK_GAP_S]


def verdict(ok, inconclusive=False):
    return 'PASS' if ok else ('INCONCLUSIVE' if inconclusive else 'FAIL')


def write_report(name, path, checks, notes, ticks):
    lines = [f'# Rhythm platform test: {name}', '', f'Browser: `{path}`', f'OS: {platform.platform()}',
             '', '| Check | Result |', '|---|---|']
    lines += [f'| {c} | **{r}** |' for c, r in checks]
    lines += [''] + notes + ['', '## Records', '', '| Time (UTC) | Event | Computer | Windows open |',
                             '|---|---|---|---|']
    lines += [f'| {time.strftime("%H:%M:%S", time.gmtime(k["t"]))} | {k["src"]} | {k["s"]} | {k["w"]} |'
              for k in ticks]
    os.makedirs(os.path.join(ROOT, 'results'), exist_ok=True)
    with open(os.path.join(ROOT, 'results', name + '.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print('\n'.join(lines))
    for c, r in checks:
        annotate(f'{name}: {r}', c)
    for n in notes:
        if n:
            annotate(f'{name}: note', n)


def standard(browser, path):
    """Closed windows, idle, then activity in another app (one or two methods)."""
    profile = tempfile.mkdtemp(prefix='rhythm-test-profile-')
    t_launch = time.time()
    proc = launch(path, profile)
    time.sleep(45)                     # the extension closes every window at about 20 s
    alive_after_close = process_count(profile)

    t_idle0 = time.time()
    time.sleep(IDLE_PHASE_S)
    t_idle1 = time.time()

    methods = ['mouse', 'caffeinate'] if SYSTEM == 'mac' else ['default']
    phases = []
    for m in methods:
        t0 = time.time()
        note = activity_in_other_app(ACTIVE_PHASE_S, m)
        t1 = time.time()
        log(note)
        phases.append((m, note, t0, t1))
        time.sleep(70)                 # back to "not in use" before the next method
    alive_at_end = process_count(profile)
    try:
        proc.terminate()
        proc.wait(timeout=15)
    except Exception:  # noqa: BLE001
        proc.kill()
    kill_processes(profile)
    ticks = read_ticks(profile)

    after = [k for k in ticks if k['t'] > t_launch + 25]
    no_window = [k for k in after if k['w'] == 0]
    idle_ok = [k for k in no_window if t_idle0 + 65 <= k['t'] <= t_idle1 and k['s'] == 'idle']
    checks = [
        ('Extension loaded and recorded', verdict(len(ticks) > 0)),
        ('Browser still running after its last window closed', verdict(alive_after_close > 0 and alive_at_end > 0)),
        ('Kept recording with no window open, no gaps over 45 s', verdict(len(no_window) >= 8 and not gaps_in(after))),
        ('Computer seen as "not in use" while nobody touched it', verdict(len(idle_ok) >= 1)),
    ]
    notes = []
    for m, note, t0, t1 in phases:
        seen = [k for k in no_window if t0 + 5 <= k['t'] <= t1 + 30 and k['s'] == 'active']
        label = {'mouse': 'Mouse use', 'caffeinate': 'Declared user activity',
                 'default': 'Keyboard use in another app'}[m]
        checks.append((f'{label} seen as "in use", with no browser window open',
                       verdict(len(seen) >= 2, inconclusive=(SYSTEM == 'linux' or 'error' in note))))
        notes.append(f'{label}: {note}')
    notes.append(f'Gaps over 45 s after windows closed: {gaps_in(after) or "none"}')
    shutil.rmtree(profile, ignore_errors=True)
    return checks, notes, ticks


def signin(browser, path):
    """Windows only, normal (default) profile, as a real person has it.

    1. Start the browser with the test extension; it closes its window.
    2. Check that the browser added itself to the Windows sign-in list.
    3. Stop the browser, then start it exactly the way Windows does at
       sign-in (the command in that list). The command-line test extension
       is added to it, because a test extension loaded this way is not
       remembered between starts (a store install is).
    4. Check that it runs with no window and keeps recording.
    """
    if SYSTEM != 'windows':
        sys.exit('Sign-in mode is for Windows only')
    profile_dir = default_user_data_dir(browser)
    before = run_key_values()
    proc = launch(path)                 # no --user-data-dir: the normal profile
    exe_marker = os.path.basename(path)
    time.sleep(120)                     # Chrome writes its sign-in entry within about a minute
    after = run_key_values()
    new_entries = {k: v for k, v in after.items() if before.get(k) != v}
    log(f'New sign-in entries: {new_entries}')

    started_ok, alive, entry_cmd = False, 0, ''
    if new_entries:
        entry_cmd = next(iter(new_entries.values()))
        kill_processes(exe_marker)
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(5)
        t_signin = time.time()
        # The way Windows runs a sign-in entry: the stored command, as is.
        parts = shlex.split(entry_cmd, posix=False)
        cmd = [p.strip('"') for p in parts] + ext_flags()
        log('Starting as at sign-in: ' + ' '.join(cmd))
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(150)
        alive = process_count(exe_marker)
        kill_processes(exe_marker)
        ticks = read_ticks(profile_dir)
        after_signin = [k for k in ticks if k['t'] >= t_signin]
        started_ok = any(k['src'] == 'browser started' for k in after_signin)
        no_window = [k for k in after_signin if k['w'] == 0]
    else:
        kill_processes(exe_marker)
        ticks = read_ticks(profile_dir)
        after_signin, no_window = [], []

    checks = [
        ('Extension loaded and recorded (normal profile)', verdict(len(ticks) > 0)),
        ('Browser added itself to the Windows sign-in list', verdict(bool(new_entries))),
        ('Started the way Windows starts it at sign-in, and still running', verdict(alive > 0)),
        ('Extension running after that start ("browser started" recorded)', verdict(started_ok)),
        ('After that start: no window opened, kept recording, no gaps over 45 s',
         verdict(len(no_window) >= 4 and len(no_window) == len(after_signin) and not gaps_in(after_signin))),
    ]
    notes = [f'Sign-in entry: {json.dumps(new_entries) if new_entries else "none"}']
    return checks, notes, ticks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--browser', required=True)
    ap.add_argument('--mode', default='standard', choices=['standard', 'signin'])
    a = ap.parse_args()
    path = find_browser(a.browser)
    name = f'{SYSTEM}-{a.browser}' + ('-signin' if a.mode == 'signin' else '')
    checks, notes, ticks = (signin if a.mode == 'signin' else standard)(a.browser, path)
    write_report(name, path, checks, notes, ticks)


if __name__ == '__main__':
    main()
