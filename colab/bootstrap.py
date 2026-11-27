"""Executed inside the Colab Jupyter kernel. Complete ComfyUI, loopback only."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

COMFY = Path('/content/DrawingStudio/ComfyUI')
STATE = COMFY.parent / 'process.json'
WORKER_PORTS = (8188, 8189)


def _run(argv, cwd=None):
    print('Drawing Studio: ' + ' '.join(argv), flush=True)
    subprocess.run(argv, cwd=cwd, check=True)


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _gpu_vram_gb():
    try:
        out = subprocess.check_output(
            ['nvidia-smi', '--query-gpu=memory.total', '--format=csv,noheader,nounits'],
            text=True, timeout=5,
        )
        return int(float(out.strip().splitlines()[0].strip())) / 1024.0
    except Exception:
        return 0.0


def _port_healthy(port, timeout=2):
    import requests
    try:
        r = requests.get(f'http://127.0.0.1:{int(port)}/system_stats', timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def _kill_pid(pid):
    if not pid or not _alive(pid):
        return
    # Validate recorded process identity before signalling a reused PID.
    cmd = Path(f'/proc/{pid}/cmdline').read_bytes()
    if str(COMFY / 'main.py').encode() not in cmd:
        raise RuntimeError('ComfyUI PID身份不符，拒绝终止未知进程')
    os.killpg(pid, signal.SIGTERM)
    for _ in range(40):
        if not _alive(pid):
            break
        time.sleep(.25)
    if _alive(pid):
        os.killpg(pid, signal.SIGKILL)


def _stop():
    if not STATE.exists():
        return
    state = json.loads(STATE.read_text())
    seen = set()
    for pid in [state.get('pid')] + list((state.get('workers') or {}).values()):
        if isinstance(pid, int) and pid not in seen:
            seen.add(pid)
            _kill_pid(pid)


def _start_one(port, cpu=False):
    port = int(port)
    COMFY.parent.mkdir(parents=True, exist_ok=True)
    log_name = 'comfy.log' if port == 8188 else f'comfy-{port}.log'
    log_path = COMFY.parent / log_name
    log = open(log_path, 'ab', buffering=0)
    argv = [sys.executable, str(COMFY / 'main.py'), '--listen', '127.0.0.1', '--port', str(port), '--disable-auto-launch']
    if cpu:
        argv.append('--cpu')
    proc = subprocess.Popen(argv, cwd=COMFY, stdout=log, stderr=log, start_new_session=True)
    log.close()
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            print(log_path.read_text(errors='replace')[-12000:])
            raise RuntimeError(f'ComfyUI({port})进程已退出')
        if _port_healthy(port, timeout=2):
            print(f'Drawing Studio: 完整ComfyUI已就绪，{port}仅监听loopback', flush=True)
            return proc.pid
        time.sleep(1)
    raise RuntimeError(f'ComfyUI({port})启动健康检查超时；日志 {log_path}')


def _resolve_ports(cpu=False, workers=None):
    if cpu:
        return [8188]
    if workers is None:
        count = 1
    else:
        count = max(1, min(len(WORKER_PORTS), int(workers)))
        if count > 1 and _gpu_vram_gb() < 48:
            count = 1
    return list(WORKER_PORTS[:count])


def _start(cpu=False, workers=None):
    ports = _resolve_ports(cpu=cpu, workers=workers)
    pids = {}
    for port in ports:
        pids[port] = _start_one(port, cpu=cpu)
    STATE.write_text(json.dumps({
        'pid': pids[8188],
        'cpu': cpu,
        'workers': {str(p): pid for p, pid in pids.items()},
    }))
    STATE.chmod(0o600)


def ensure_extra_workers(count=2, cpu=False):
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    workers_map = dict(state.get('workers') or {})
    if state.get('pid') and '8188' not in workers_map:
        workers_map['8188'] = state['pid']
    ports = _resolve_ports(cpu=cpu, workers=count)
    for port in ports:
        if port == 8188 or _port_healthy(port, timeout=2):
            continue
        workers_map[str(port)] = _start_one(port, cpu=cpu)
    if workers_map:
        STATE.write_text(json.dumps({
            'pid': workers_map.get('8188', state.get('pid')),
            'cpu': bool(state.get('cpu', cpu)),
            'workers': workers_map,
        }))
        STATE.chmod(0o600)
    return [p for p in ports if _port_healthy(p, timeout=2)]


def bootstrap(config=None):
    config = config or {}
    COMFY.parent.mkdir(parents=True, exist_ok=True)
    if not (COMFY / '.git').exists():
        _run(['git', 'clone', 'https://github.com/comfyanonymous/ComfyUI.git', str(COMFY)])
    _run(['git', 'fetch', '--depth', '1', 'origin', str(config.get('comfy_ref', 'master'))], COMFY)
    _run(['git', 'checkout', '--detach', 'FETCH_HEAD'], COMFY)
    _run([sys.executable, '-m', 'pip', 'install', '-r', str(COMFY / 'requirements.txt'), 'websocket-client'], COMFY)
    # Only repositories explicitly configured by the owner are installed/executed.
    for node in config.get('custom_nodes') or []:
        import re
        if isinstance(node, str):
            node = {'url': node}
        url = node.get('url', '')
        if not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?', url):
            raise ValueError('自定义节点必须为明确配置的GitHub HTTPS仓库')
        name = url.rstrip('/').split('/')[-1].removesuffix('.git')
        dest = COMFY / 'custom_nodes' / name
        if not dest.exists():
            _run(['git', 'clone', '--depth', '1', url, str(dest)])
        if node.get('ref'):
            _run(['git', 'fetch', '--depth', '1', 'origin', str(node['ref'])], dest)
            _run(['git', 'checkout', '--detach', 'FETCH_HEAD'], dest)
        req = dest / 'requirements.txt'
        if not req.exists() and (dest / 'requirements-no-cupy.txt').exists():
            req = dest / 'requirements-no-cupy.txt'
        if req.exists():
            _run([sys.executable, '-m', 'pip', 'install', '-r', str(req)], dest)
        if name == 'ComfyUI-Frame-Interpolation':
            # RIFE looks in this node's ckpts/rife, not ComfyUI/models. Link Drive-synced weights.
            vfi_ckpts = dest / 'ckpts' / 'rife'
            vfi_ckpts.mkdir(parents=True, exist_ok=True)
            synced = COMFY / 'models' / 'vfi_models'
            if synced.exists():
                for weight in synced.iterdir():
                    if weight.is_file() and not weight.name.startswith('.'):
                        link = vfi_ckpts / weight.name
                        if not link.exists():
                            link.symlink_to(weight)
    for folder in ('checkpoints', 'loras', 'vae', 'embeddings', 'controlnet', 'upscale_models', 'diffusion_models', 'text_encoders', 'clip', 'clip_vision', 'latent_upscale_models', 'vfi_models'):
        (COMFY / 'models' / folder).mkdir(parents=True, exist_ok=True)
    _stop()
    _start(bool(config.get('cpu')), workers=config.get('comfy_workers'))


def restart_comfy(workers=None):
    config = json.loads(STATE.read_text()) if STATE.exists() else {}
    _stop()
    if workers is None:
        workers = len(config.get('workers') or {}) or 1
    _start(bool(config.get('cpu')), workers=workers)
