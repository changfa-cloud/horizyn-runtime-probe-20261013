import hashlib
import http.server
import json
import os
import pathlib
import platform
import plistlib
import shutil
import shlex
import subprocess
import time
import threading
import traceback
import urllib.request

out = pathlib.Path('evidence').resolve()
out.mkdir(exist_ok=True)
work = pathlib.Path(os.environ['RUNNER_TEMP']) / 'horizyn-0135-probe'
work.mkdir(exist_ok=True)
report = {'release': '0.1.35', 'platform': platform.platform(), 'machine': platform.machine(), 'commands': []}
gui = None
gui_log = None
mount = work / 'mount'
web_server = None
web_log = None

def run(args, name, timeout=45, env=None):
    try:
        proc = subprocess.run(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        content = proc.stdout.decode('utf-8', errors='replace')
        (out / (name + '.txt')).write_text(content, encoding='utf-8')
        report['commands'].append({'name': name, 'returncode': proc.returncode})
        print(name, 'exit', proc.returncode, content[:700], flush=True)
        return proc.returncode, content
    except subprocess.TimeoutExpired as exc:
        content = (exc.stdout or b'').decode('utf-8', errors='replace')
        (out / (name + '.txt')).write_text(content + '\nTIMEOUT\n', encoding='utf-8')
        report['commands'].append({'name': name, 'timeout': True})
        return 124, content

try:
    run(['sw_vers'], 'os')
    run(['system_profiler', 'SPHardwareDataType', 'SPDisplaysDataType'], 'hardware', 30)
    assert platform.machine() == 'arm64', 'This release requires arm64'
    code, _ = run(['swiftc', 'desktop.swift', '-o', str(work / 'desktop')], 'compile-desktop', 90)
    if code == 0:
        run([str(work / 'desktop')], 'desktop-before')
    archive = work / 'Horizyn-0.1.35-arm64.dmg'
    url = 'https://github.com/visual-z/horizyn-releases/releases/download/v0.1.35/Horizyn-0.1.35-arm64.dmg'
    urllib.request.urlretrieve(url, archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    report['archive_sha256'] = digest
    assert digest == '3537e9b653f0ea64d34e5c4b8e7add1edaad6aaccb928e8454018307a85e5af2'
    mount.mkdir(exist_ok=True)
    code, _ = run(['hdiutil', 'attach', '-readonly', '-nobrowse', '-mountpoint', str(mount), str(archive)], 'mount')
    assert code == 0, 'Could not mount DMG'
    app = work / 'Horizyn.app'
    run(['ditto', str(mount / 'Horizyn.app'), str(app)], 'copy-app')
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    report['version'] = info.get('CFBundleShortVersionString')
    report['build'] = info.get('CFBundleVersion')
    assert report['version'] == '0.1.35'
    code, _ = run(['codesign', '--verify', '--deep', '--strict', '--verbose=2', str(app)], 'signature')
    assert code == 0, 'Code signature validation failed'
    executable = app / 'Contents/MacOS/horizyn'
    code, help_text = run([str(executable), '--help'], 'actual-help', 20)
    report['help_returncode'] = code
    args = [str(executable)]
    if '--fresh-start' in help_text:
        args.append('--fresh-start')
    fixture = work / 'sample-project'
    fixture.mkdir(exist_ok=True)
    (fixture / 'README.md').write_text('# Runtime observation fixture\n\nOriginal application, temporary sample project.\n', encoding='utf-8')
    (fixture / 'main.js').write_text('export const greeting = "Hello";\n', encoding='utf-8')
    (fixture / 'index.html').write_text('<!doctype html><html><head><title>Horizyn browser fixture</title></head><body><h1>Horizyn 0.1.35 browser fixture</h1><p id="context">A local page for observing the original browser.</p><button onclick="this.textContent=\'Clicked\'">Test button</button></body></html>', encoding='utf-8')
    run(['git', 'init', '-q', str(fixture)], 'fixture-git-init')
    run(['git', '-C', str(fixture), 'add', '.'], 'fixture-git-add')
    run(['git', '-C', str(fixture), '-c', 'user.name=Runtime Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'Initial sample'], 'fixture-git-commit')
    (fixture / 'main.js').write_text('export const greeting = "Hello from the modified fixture";\nexport const ready = true;\n', encoding='utf-8')
    config = work / 'config.toml'
    config.write_text('[session]\ncwd = ' + json.dumps(str(fixture)) + '\n[ui]\nlanguage = "en"\n', encoding='utf-8')
    args += ['--config', str(config), '--layout', str(work / 'layout.json')]
    web_log = (out / 'fixture-http.log').open('wb')
    class FixtureHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(fixture), **kwargs)
        def log_message(self, fmt, *args):
            web_log.write((fmt % args + '\n').encode('utf-8', errors='replace'))
            web_log.flush()
    web_server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
    threading.Thread(target=web_server.serve_forever, daemon=True).start()
    test_url = f'http://127.0.0.1:{web_server.server_port}/'
    direct_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with direct_http.open(test_url, timeout=5) as response:
        report['local_http_preflight'] = {'status': response.status, 'fixture_title_found': b'Horizyn 0.1.35 browser fixture' in response.read()}
    app_env = dict(os.environ, HORIZYND_SOCKET=str(work / 'daemon.sock'), HORIZYND_TRANSCRIPTS=str(work / 'transcripts'))
    gui_log = (out / 'gui-process.log').open('wb')
    gui = subprocess.Popen(args, cwd=fixture, env=app_env, stdout=gui_log, stderr=subprocess.STDOUT)
    report['gui_pid'] = gui.pid
    report['gui_arguments'] = args[1:]
    print('Started original GUI PID', gui.pid, flush=True)
    time.sleep(12)
    report['gui_exit_after_12s'] = gui.poll()
    if (work / 'desktop').exists():
        _, desktop = run([str(work / 'desktop')], 'desktop-after')
        try:
            state = json.loads(desktop)
            report['metalDevices'] = state['metalDevices']
            app_windows = [w for w in state['windows'] if w.get('kCGWindowOwnerPID') == gui.pid]
            report['app_window_count'] = len(app_windows)
            for index, window in enumerate(app_windows[:3]):
                run(['screencapture', '-x', '-l', str(window['kCGWindowNumber']), str(out / f'horizyn-window-{index}.png')], f'capture-window-{index}', 15)
        except Exception as exc:
            report['desktop_parse_error'] = str(exc)
    run(['screencapture', '-x', str(out / 'desktop.png')], 'capture-desktop', 15)
    code, _ = run(['swiftc', 'ui.swift', '-o', str(work / 'ui')], 'compile-ui', 90)
    if code == 0 and gui.poll() is None:
        _, initial_ax = run([str(work / 'ui'), 'dump', str(gui.pid)], 'ax-initial')
        def nodes(value):
            if isinstance(value, dict):
                yield value
                for child in value.get('children', []):
                    yield from nodes(child)
        titles = [n.get('AXTitle', '') for n in nodes(json.loads(initial_ax).get('tree', {})) if n.get('AXRole') == 'AXMenuItem']
        report['menu_titles'] = titles
        print('Observed menu titles:', json.dumps(titles), flush=True)
        def capture_state(label):
            time.sleep(1)
            _, raw = run([str(work / 'desktop')], 'state-' + label)
            state = json.loads(raw)
            windows = [w for w in state['windows'] if w.get('kCGWindowOwnerPID') == gui.pid and w.get('kCGWindowLayer') == 0]
            report.setdefault('window_states', []).append({'label': label, 'count': len(windows), 'windows': windows})
            for index, window in enumerate(windows[:4]):
                run(['screencapture', '-x', '-l', str(window['kCGWindowNumber']), str(out / f'{label}-{index}.png')], f'capture-{label}-{index}', 15)
            run([str(work / 'ui'), 'dump', str(gui.pid)], 'ax-' + label)
        ui = [str(work / 'ui')]
        command = 'cd ' + shlex.quote(str(fixture)) + '; printf ' + shlex.quote('\\033]7;file://localhost' + str(fixture) + '\\007')
        run(ui + ['type', str(gui.pid), command], 'fixture-terminal-type')
        run(ui + ['key', str(gui.pid), '36', '0'], 'fixture-terminal-return')
        capture_state('fixture-terminal')
        if titles.count('Toggle Palette') == 1:
            for label, query in [('browser', 'Open Browser Sidebar'), ('files', 'Open Files Sidebar'), ('changes', 'Open Changes Sidebar'), ('agent', 'Open Agent Sidebar')]:
                _, observed = run([str(work / 'desktop')], 'focus-state-' + label)
                candidates = [w for w in json.loads(observed)['windows'] if w.get('kCGWindowOwnerPID') == gui.pid and w.get('kCGWindowLayer') == 0]
                if candidates:
                    bounds = candidates[0]['kCGWindowBounds']
                    run(ui + ['click', str(gui.pid), str(bounds['X'] + 330), str(bounds['Y'] + 220)], 'focus-terminal-' + label)
                run(ui + ['menu', str(gui.pid), 'Toggle Palette'], 'palette-open-' + label)
                run(ui + ['key', str(gui.pid), '0', '1048576'], 'palette-select-all-' + label)
                run(ui + ['type', str(gui.pid), query], 'palette-query-' + label)
                capture_state('palette-' + label)
                run(ui + ['key', str(gui.pid), '36', '0'], 'palette-select-' + label)
                capture_state('panel-' + label)
                if label == 'browser':
                    run(ui + ['key', str(gui.pid), '37', '1048576'], 'browser-focus-address')
                    run(ui + ['type', str(gui.pid), test_url], 'browser-type-address')
                    run(ui + ['key', str(gui.pid), '36', '0'], 'browser-navigate')
                    time.sleep(20)
                    capture_state('browser-loaded')
                report.setdefault('palette_actions', []).append({'label': label, 'query': query, 'evidence': 'panel-' + label + '-0.png'})
        for label, choices in [
            ('browser', ['Browser', 'Open Browser Sidebar', 'Browser Sidebar']),
            ('files', ['Files', 'Open Files Sidebar', 'Files and Git']),
            ('changes', ['Changes', 'Open Changes Sidebar']),
            ('agent', ['Agent', 'Open Agent Sidebar', 'Agent Overview']),
            ('split', ['Split Right']),
            ('second-window', ['New Window']),
            ('settings', ['Settings…', 'Settings...', 'Settings']),
        ]:
            _, raw = run([str(work / 'ui'), 'dump', str(gui.pid)], 'before-' + label)
            current = [n.get('AXTitle', '') for n in nodes(json.loads(raw).get('tree', {})) if n.get('AXRole') == 'AXMenuItem']
            found = next((choice for choice in choices if current.count(choice) == 1 or (label == 'second-window' and current.count(choice) > 0)), None)
            if found:
                scope = ['File'] if label == 'second-window' else []
                code, message = run([str(work / 'ui'), 'menu', str(gui.pid), found] + scope, 'action-' + label)
                report.setdefault('ui_actions', []).append({'label': label, 'menu': found, 'returncode': code})
                capture_state(label)
            else:
                report.setdefault('ui_actions', []).append({'label': label, 'skipped': 'No unique matching menu item'})
    recovery_env = dict(os.environ, HORIZYND_SOCKET=str(work / 'recovery-test.sock'), HORIZYND_TRANSCRIPTS=str(work / 'recovery-transcripts'))
    report['crash_recovery_exit'], _ = run([str(executable), '--crash-recover-test'], 'original-crash-recover-test', 45, env=recovery_env)
    layout = work / 'layout.json'
    if layout.exists():
        shutil.copy2(layout, out / 'observed-layout.json')
    run(['ps', '-axo', 'pid,ppid,comm'], 'processes')
    run(['log', 'show', '--last', '10m', '--style', 'compact', '--predicate', 'process == "horizyn" OR process == "horizynd" OR process CONTAINS "WebKit"'], 'unified-log', 30)
    crashes = pathlib.Path.home() / 'Library/Logs/DiagnosticReports'
    if crashes.exists():
        for path in crashes.glob('horizyn*'):
            if path.is_file() and path.stat().st_size < 2_000_000:
                shutil.copy2(path, out / path.name)
except Exception as exc:
    report['error'] = str(exc)
    (out / 'exception.txt').write_text(traceback.format_exc(), encoding='utf-8')
    print(traceback.format_exc(), flush=True)
finally:
    if web_server:
        web_server.shutdown()
        web_server.server_close()
    if web_log:
        web_log.close()
    if gui and gui.poll() is None:
        gui.terminate()
        try:
            gui.wait(timeout=5)
        except subprocess.TimeoutExpired:
            gui.kill()
            gui.wait(timeout=5)
    if gui_log:
        gui_log.close()
    (out / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)
