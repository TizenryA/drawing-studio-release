"""One-off: install Fannovel16 ComfyUI-Frame-Interpolation on the live kernel.

Uses Draw's Runtime.execute and the same custom-node loop as colab/bootstrap.py.
Does not call bootstrap(), start(), stop(), reset(), or restart_comfy().
"""
import asyncio
import json
import os
import sys

from backend.database import Database
from backend.runtime import Runtime

EXPECTED_ENDPOINT = os.environ.get('EXPECTED_ENDPOINT', '')
NODE_URL = 'https://github.com/Fannovel16/ComfyUI-Frame-Interpolation'
NODE_REF = '26545cc2dd95bc3d27f056016300673bdeee78f5'
INSTALL = r'''
import json
import subprocess
import sys
from pathlib import Path

url = 'https://github.com/Fannovel16/ComfyUI-Frame-Interpolation'
ref = '26545cc2dd95bc3d27f056016300673bdeee78f5'
name = url.rstrip('/').split('/')[-1].removesuffix('.git')
dest = COMFY / 'custom_nodes' / name
if not dest.exists():
    _run(['git', 'clone', '--depth', '1', url, str(dest)])
_run(['git', 'fetch', '--depth', '1', 'origin', str(ref)], dest)
_run(['git', 'checkout', '--detach', 'FETCH_HEAD'], dest)
req = dest / 'requirements.txt'
if not req.exists() and (dest / 'requirements-no-cupy.txt').exists():
    req = dest / 'requirements-no-cupy.txt'
req_used = str(req) if req.exists() else None
if req.exists():
    _run([sys.executable, '-m', 'pip', 'install', '-r', str(req)], dest)
vfi_ckpts = dest / 'ckpts' / 'rife'
vfi_ckpts.mkdir(parents=True, exist_ok=True)
synced = COMFY / 'models' / 'vfi_models'
linked = []
if synced.exists():
    for weight in synced.iterdir():
        if weight.is_file() and not weight.name.startswith('.'):
            link = vfi_ckpts / weight.name
            if not link.exists():
                link.symlink_to(weight)
            linked.append(weight.name)
sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=dest, text=True).strip()
weights = sorted(p.name for p in vfi_ckpts.iterdir() if p.name.endswith('.pth') or p.name.endswith('.pt'))
print('DRAWING_INSTALL_OK:' + json.dumps({
    'sha': sha,
    'path': str(dest),
    'requirements': req_used,
    'linked': linked,
    'ckpts': weights,
}, ensure_ascii=False), flush=True)
'''


async def main():
    db = Database('/data/studio.sqlite3')

    def settings():
        return db.get('settings', 'main') or {}

    runtime = Runtime(settings)
    handle = runtime.handle
    endpoint = handle.get('endpoint')
    state = handle.get('state')
    print('handle', {'state': state, 'gpu': handle.get('gpu'), 'endpoint': endpoint, 'mode': handle.get('mode')}, flush=True)
    if not endpoint:
        raise SystemExit('no endpoint in runtime.json; refusing to start a GPU')
    if EXPECTED_ENDPOINT and endpoint != EXPECTED_ENDPOINT:
        raise SystemExit(f'refusing to run on unexpected endpoint {endpoint!r}')
    bootstrap = Path_read()
    result = await runtime.execute(bootstrap + '\n' + INSTALL, timeout=900, interrupt_on_timeout=True)
    stdout = result.get('stdout') or ''
    stderr = result.get('stderr') or ''
    print('STDOUT\n' + stdout)
    if stderr:
        print('STDERR\n' + stderr)
    if result.get('executionError'):
        print('EXEC_ERROR', json.dumps(result['executionError'], ensure_ascii=False))
        raise SystemExit(2)
    line = next((ln[len('DRAWING_INSTALL_OK:'):] for ln in stdout.splitlines() if ln.startswith('DRAWING_INSTALL_OK:')), None)
    if not line:
        raise SystemExit('install finished without DRAWING_INSTALL_OK')
    info = json.loads(line)
    print('INSTALL', json.dumps(info, ensure_ascii=False))
    if not str(info.get('sha') or '').startswith('26545cc2'):
        raise SystemExit('checkout sha mismatch: ' + str(info.get('sha')))


def Path_read():
    from pathlib import Path
    return Path('/app/colab/bootstrap.py').read_text()


if __name__ == '__main__':
    asyncio.run(main())
