"""Start the table and its browser player together."""
import argparse
import asyncio
import threading
from .server import make_server
from .browser_bot import run


def main():
    parser = argparse.ArgumentParser(description='Unser Pokerbot – lokales Spielgeld')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--headless', action='store_true', help='Browser im Hintergrund; Tisch separat öffnen')
    args = parser.parse_args()
    server = make_server(args.port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f'Tisch: http://127.0.0.1:{args.port}', flush=True)
    try:
        asyncio.run(run(f'http://127.0.0.1:{args.port}', headless=args.headless))
    except KeyboardInterrupt:
        print('Bot und Übungstisch beendet.')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == '__main__':
    main()
