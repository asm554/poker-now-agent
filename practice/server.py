"""Loopback-only practice table with versioned actions and a stop control."""
import argparse
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .game import Game


def make_server(port=8765, seed=None):
    game = Game(seed)
    lock = threading.Lock()
    token = secrets.token_urlsafe(24)
    origin = f'http://127.0.0.1:{port}'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, value):
            body = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/api/state':
                with lock:
                    self.reply(200, game.public_state())
            elif self.path == '/':
                body = Path(__file__).with_name('table.html').read_text().replace('__TOKEN__', token).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.reply(404, {'error': 'Nicht gefunden'})

        def do_POST(self):
            nonlocal game
            if self.headers.get('X-Practice-Token') != token or self.headers.get('Origin') != origin:
                self.reply(403, {'error': 'Ungültige lokale Sitzung'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    raise ValueError('Ungültige Nachricht')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError('Ungültige Nachricht')
                with lock:
                    if self.path == '/api/control':
                        if type(data.get('paused')) is not bool:
                            raise ValueError('Ungültiger Steuerbefehl')
                        game.paused = data['paused']
                        game.version += 1
                        game.bot['status'] = 'Gestoppt' if game.paused else 'Browserbot wartet auf deinen Zug'
                    elif self.path == '/api/reset':
                        if not game.paused or not game.done:
                            raise ValueError('Erst Bot stoppen und laufende Hand beenden')
                        game = Game(seed)
                    elif self.path == '/api/new':
                        if data.get('decision_id') != game.public_state()['decision_id']:
                            raise ValueError('Veralteter Tischzustand')
                        if data.get('source') == 'bot' and game.paused:
                            raise ValueError('Bot ist gestoppt')
                        game.new_hand()
                        game.last_ack = {'decision_id': data['decision_id'], 'kind': 'new'}
                        game.advance_opponent()
                    elif self.path == '/api/action':
                        if data.get('decision_id') != game.public_state()['decision_id']:
                            raise ValueError('Veralteter Zug – Aktion verworfen')
                        if data.get('source') == 'bot' and game.paused:
                            raise ValueError('Bot ist gestoppt')
                        game.act(0, data.get('action'), data.get('amount'))
                        game.last_ack = {'decision_id': data['decision_id'], 'kind': 'action', 'action': data['action']}
                        game.advance_opponent()
                    elif self.path == '/api/bot-status':
                        if data.get('decision_id') != game.public_state()['decision_id'] or game.paused:
                            raise ValueError('Botstatus veraltet oder gestoppt')
                        game.bot = {key: str(data.get(key, ''))[:300] for key in ('status', 'action', 'reason')}
                    else:
                        self.reply(404, {'error': 'Nicht gefunden'})
                        return
                    self.reply(200, game.public_state())
            except (ValueError, TypeError, KeyError) as exc:
                self.reply(409, {'error': str(exc)})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    origin = f'http://127.0.0.1:{server.server_address[1]}'
    server.game = lambda: game
    return server


def main():
    parser = argparse.ArgumentParser(description='Lokaler Spielgeldtisch')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    server = make_server(args.port)
    print(f'Übungstisch: http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
