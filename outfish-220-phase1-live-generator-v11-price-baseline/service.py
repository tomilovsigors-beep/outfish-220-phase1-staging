#!/usr/bin/env python3
import json
import hashlib, os, shutil, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from production_compare import compare as compare_production
from production_price_baseline import apply_production_before_discount

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get('DATA_DIR', str(BASE / 'runtime-data')))
CURRENT = DATA / 'current'
RUNS = DATA / 'runs'
PILOT = Path(os.environ.get('PILOT_CSV', str(BASE / 'pilot_input_674.csv')))
EXCLUSIONS = Path(os.environ.get('APPROVED_EXCLUSIONS', str(BASE / 'approved_exclusions.json')))
MODE = os.environ.get('GENERATOR_MODE', 'live').strip().lower()
FIXTURE = Path(os.environ.get('FIXTURE_JSON', str(BASE / 'fixtures' / 'shopify_snapshot_smoke.json')))
REFRESH_SECONDS = max(3600, int(os.environ.get('REFRESH_SECONDS', '21600')))
PORT = int(os.environ.get('PORT', '10000'))
PRODUCTION_FEED_URL = os.environ.get('PRODUCTION_FEED_URL', 'https://outfish-220-stock-feed.onrender.com/220-stock.xml')
EXPECTED_EXPORT_ROWS = int(os.environ.get('EXPECTED_EXPORT_ROWS', '672'))

lock = threading.Lock()
state = {
    'last_attempt_at': None,
    'last_success_at': None,
    'last_error': None,
    'last_returncode': None,
}


def iso_now():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def generator_gate():
    return load_json(CURRENT / 'publish-gate.json', {}) or {}

def current_gate():
    return load_json(CURRENT / 'cutover-gate.json', {}) or {}


def current_manifest():
    return load_json(CURRENT / 'run-manifest.json', {}) or {}


def refresh_once():
    with lock:
        state['last_attempt_at'] = iso_now()
        run_id = str(int(time.time()))
        tmp = RUNS / f'.tmp-{run_id}'
        final = RUNS / run_id
        tmp.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable, str(BASE / 'generator.py'),
            '--pilot-csv', str(PILOT),
            '--approved-exclusions', str(EXCLUSIONS),
            '--output-dir', str(tmp),
        ]
        if MODE == 'fixture':
            cmd += ['--fixture-json', str(FIXTURE)]
        else:
            cmd += ['--live']
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            state['last_returncode'] = p.returncode
            (tmp / 'service-run.stdout.txt').write_text(p.stdout or '', encoding='utf-8')
            (tmp / 'service-run.stderr.txt').write_text(p.stderr or '', encoding='utf-8')
            gate = load_json(tmp / 'publish-gate.json', {}) or {}
            manifest = load_json(tmp / 'run-manifest.json', {}) or {}
            if p.returncode != 0:
                raise RuntimeError(f'generator exit {p.returncode}')
            if not (tmp / 'stock-price-candidate.xml').exists():
                raise RuntimeError('candidate XML missing')

            # Preserve the current production 'price-before-discount' anchor.
            # The generated selling price remains in price-after-discount.
            baseline_report = apply_production_before_discount(
                tmp / 'stock-price-candidate.xml',
                PRODUCTION_FEED_URL,
            )
            (tmp / 'production-price-baseline.json').write_text(
                json.dumps(baseline_report, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )

            # Compare against the current production feed before promoting this run.
            prod_diff = compare_production(
                tmp / 'stock-price-candidate.xml',
                PRODUCTION_FEED_URL,
                expected_export_rows=EXPECTED_EXPORT_ROWS,
            )
            (tmp / 'production-diff.json').write_text(json.dumps(prod_diff, ensure_ascii=False, indent=2), encoding='utf-8')
            cutover_gate = {
                'mode': 'PHASE1_CUTOVER_REHEARSAL_READ_ONLY',
                'cutover_allowed': bool(gate.get('publish_allowed')) and bool(prod_diff.get('pass')),
                'generator_publish_allowed': bool(gate.get('publish_allowed')),
                'production_compare_pass': bool(prod_diff.get('pass')),
                'expected_export_rows': EXPECTED_EXPORT_ROWS,
                'candidate_rows': prod_diff.get('candidate_rows'),
                'production_rows': prod_diff.get('production_rows'),
                'blocking_checks': [x for x in prod_diff.get('checks', []) if not x.get('pass')],
                'safety': {
                    'publishes_to_220': False,
                    'replaces_production_feed': False,
                    'writes_shopify': False,
                    'writes_google_sheet': False,
                    'writes_pmp': False,
                    'fhm_in_scope': False,
                },
            }
            (tmp / 'cutover-gate.json').write_text(json.dumps(cutover_gate, ensure_ascii=False, indent=2), encoding='utf-8')

            tmp.rename(final)
            candidate = DATA / '.current-next'
            if candidate.exists() or candidate.is_symlink():
                candidate.unlink()
            candidate.symlink_to(final, target_is_directory=True)
            os.replace(candidate, CURRENT)
            state['last_success_at'] = iso_now()
            state['last_error'] = None
            return {'gate': gate, 'manifest': manifest}
        except Exception as e:
            state['last_error'] = f'{type(e).__name__}: {e}'
            try:
                (tmp / 'service-error.txt').write_text(state['last_error'], encoding='utf-8')
            except Exception:
                pass
            return None


def scheduler():
    while True:
        refresh_once()
        time.sleep(REFRESH_SECONDS)


def status_payload():
    gate = current_gate()
    manifest = current_manifest()
    return {
        'service': 'outfish-220-phase1-staging-v11-price-baseline',
        'mode': MODE,
        'candidate_available': (CURRENT / 'stock-price-candidate.xml').exists(),
        'cutover_allowed': bool(gate.get('cutover_allowed')),
        'generator_publish_allowed': bool((load_json(CURRENT / 'publish-gate.json', {}) or {}).get('publish_allowed')),
        'production_compare_pass': bool(gate.get('production_compare_pass')),
        'gate': gate,
        'manifest_generated_at': manifest.get('generated_at'),
        'last_attempt_at': state['last_attempt_at'],
        'last_success_at': state['last_success_at'],
        'last_error': state['last_error'],
        'safety': {
            'publishes_to_220': False,
            'writes_shopify': False,
            'writes_pmp': False,
            'writes_google_sheet': False,
            'fhm_in_scope': False,
        },
    }


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class Handler(BaseHTTPRequestHandler):
    server_version = 'Outfish220Staging/11'

    def _send_json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/health':
            payload = status_payload()
            code = 200 if payload['last_error'] is None else 503
            return self._send_json(code, payload)
        if self.path == '/audit':
            return self._send_json(200, status_payload())
        if self.path == '/generator-publish-gate.json':
            gate = load_json(CURRENT / 'publish-gate.json', {}) or {}
            return self._send_json(200 if gate else 404, gate if gate else {'error':'no run yet'})
        if self.path == '/cutover-gate.json':
            gate = current_gate()
            return self._send_json(200 if gate else 404, gate if gate else {'error':'no run yet'})
        if self.path == '/production-diff.json':
            diff = load_json(CURRENT / 'production-diff.json', {}) or {}
            return self._send_json(200 if diff else 404, diff if diff else {'error':'no production comparison yet'})
        if self.path == '/ready':
            payload = status_payload()
            return self._send_json(200 if payload.get('cutover_allowed') else 503, payload)
        if self.path == '/220-stock-candidate.xml':
            gate = current_gate()
            path = CURRENT / 'stock-price-candidate.xml'
            if not path.exists():
                return self._send_json(404, {'error':'candidate not generated yet'})
            if not gate.get('cutover_allowed'):
                return self._send_json(503, {'error':'candidate blocked by cutover gate', 'gate':gate})
            body = path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'application/xml; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
            return
        return self._send_json(404, {'error':'not found'})

    def log_message(self, fmt, *args):
        sys.stderr.write('[http] ' + (fmt % args) + '\n')


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    RUNS.mkdir(parents=True, exist_ok=True)
    t = threading.Thread(target=scheduler, daemon=True)
    t.start()
    ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()


if __name__ == '__main__':
    main()
