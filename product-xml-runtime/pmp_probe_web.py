from flask import Flask, Response
import json
from pmp_api_probe import discover

app = Flask(__name__)

@app.get('/health')
def health():
    return Response(json.dumps({'ok': True}), mimetype='application/json')

@app.get('/discovery.json')
def discovery():
    try:
        data = discover()
        return Response(json.dumps(data, indent=2, sort_keys=True), status=200, mimetype='application/json')
    except Exception as e:
        return Response(json.dumps({'status':'ERROR','error':f'{type(e).__name__}: {e}'}), status=503, mimetype='application/json')
