"""
╔══════════════════════════════════════════════════════════════╗
║  SCRIPT 4 — RUNNER AUTOMATIQUE                              ║
║  Lance tous les scripts dans l'ordre, toutes les X heures   ║
║  Exécuter : python 4_auto_runner.py                         ║
╚══════════════════════════════════════════════════════════════╝
"""

import subprocess
import time
import sys
from datetime import datetime

INTERVAL_HOURS = 6  # Mise à jour toutes les 6 heures
SCRIPTS = [
    "1_collect_data.py",
    "2_predict.py",
    "3_export_json.py",
]


def run_pipeline():
    print(f"\n{'=' * 60}")
    print(f"  🔄 Pipeline lancé : {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"{'=' * 60}")
    
    for script in SCRIPTS:
        print(f"\n▶ Exécution de {script}...")
        result = subprocess.run(
            [sys.executable, script],
            capture_output=True, text=True
        )
        
        if result.returncode == 0:
            print(result.stdout)
        else:
            print(f"❌ Erreur dans {script}:")
            print(result.stderr)
    
    print(f"\n✅ Pipeline terminé : {datetime.now().strftime('%H:%M:%S')}")
    print(f"⏰ Prochaine mise à jour dans {INTERVAL_HOURS}h")


if __name__ == "__main__":
    print("🚀 Football Predictor — Auto Runner")
    print(f"   Mise à jour toutes les {INTERVAL_HOURS} heures")
    print("   Ctrl+C pour arrêter\n")
    
    run_pipeline()
    
    while True:
        try:
            time.sleep(INTERVAL_HOURS * 3600)
            run_pipeline()
        except KeyboardInterrupt:
            print("\n\n👋 Auto runner arrêté")
            break
