import json
import os
import runpy
from http.server import BaseHTTPRequestHandler, HTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/health':
            self.send_error(404)
            return
        body = json.dumps({'service':'outfish-ocun-sync','status':'scaffold-only','import_enabled':False}).encode()
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)

if __name__ == '__main__':
    runpy.run_path(os.path.join(os.path.dirname(__file__), 'probe.py'), run_name='__main__')
    HTTPServer(('0.0.0.0',int(os.environ.get('PORT','10000'))),Handler).serve_forever()
