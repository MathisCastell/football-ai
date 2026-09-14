"""
╔══════════════════════════════════════════════════════════════╗
║  FOOTBALL.AI — Point d'entrée du service web                 ║
║                                                                ║
║  Local :  python main.py            → http://localhost:5000  ║
║  Prod  :  gunicorn main:app          (voir render.yaml)        ║
╚══════════════════════════════════════════════════════════════╝
"""

import sys
import threading
import time

# Evite les UnicodeEncodeError sur les consoles Windows (cp1252) quand on
# affiche des emojis dans les logs.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from app import config, pipeline
from app.server import create_app

app = create_app()


def _scheduler_loop():
    pipeline.run()
    while True:
        time.sleep(config.PIPELINE_INTERVAL_HOURS * 3600)
        pipeline.run()


threading.Thread(target=_scheduler_loop, daemon=True).start()

if __name__ == "__main__":
    print("\n" + "=" * 55)
    print("  ⚽  FOOTBALL.AI — Serveur")
    print("=" * 55)
    print(f"  → http://localhost:{config.PORT}")
    print("  Ctrl+C pour arrêter\n")
    app.run(host="0.0.0.0", port=config.PORT, debug=False, use_reloader=False)
