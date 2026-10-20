"""
CLI de gestión rápida y Cero-Tokens para ZgzHouseFinder.
Permite operar la base de datos, recalcular el ranking, ejecutar rastreos incrementales
o instalar una tarea programada nativa de Windows (Task Scheduler) sin consumir tokens de IA.

Uso:
  python scripts/manage.py stats                  -> Resumen compacto de la BD (<60 tokens)
  python scripts/manage.py recalc                 -> Recalcula las 258 viviendas sin hacer llamadas web (0.2s)
  python scripts/manage.py scan                   -> Rastreo incremental rápido (usa caché SQLite)
  python scripts/manage.py scan-full              -> Rastreo profundo completo de los 4 portales
  python scripts/manage.py install-windows-cron   -> Crea tarea diaria 08:30 en Windows Task Scheduler (0 tokens IA)
"""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))

from run_daily_scan import DB_PATH, run_scan  # noqa: E402


def cmd_stats() -> None:
    if not DB_PATH.exists():
        print(json.dumps({"error": "DB not found"}))
        return
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    total = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    candidates = conn.execute("SELECT COUNT(*) FROM listings WHERE hard_discard = 0").fetchone()[0]
    particulares = conn.execute("SELECT COUNT(*) FROM listings WHERE is_agency = 0").fetchone()[0]
    by_portal = dict(conn.execute("SELECT portal, COUNT(*) FROM listings GROUP BY portal").fetchall())
    top5 = [
        dict(r)
        for r in conn.execute(
            """
            SELECT id, portal, title, price_eur, is_agency, seller_name, final_ranking_score
            FROM listings WHERE hard_discard = 0
            ORDER BY final_ranking_score DESC LIMIT 5
            """
        ).fetchall()
    ]
    conn.close()
    print(
        json.dumps(
            {
                "total_listings": total,
                "valid_candidates": candidates,
                "particulares": particulares,
                "by_portal": by_portal,
                "top_5": top5,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_install_windows_cron() -> None:
    bat_path = BASE_DIR / "rastreo_silencioso_sin_tokens.bat"
    task_name = "ZgzHouseFinder_DailyScan_ZeroTokens"
    cmd = [
        "schtasks",
        "/Create",
        "/SC",
        "DAILY",
        "/TN",
        task_name,
        "/TR",
        f'"{bat_path}"',
        "/ST",
        "08:30",
        "/F",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        print(f"[OK] Tarea nativa de Windows '{task_name}' programada a las 08:30 AM (0 tokens de IA).")
    else:
        print(f"[WARN] No se pudo registrar automáticamente en schtasks: {res.stderr.strip()}")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if action == "stats":
        cmd_stats()
    elif action == "recalc":
        print(json.dumps(run_scan(incremental=True, recalc_only=True, quiet=True), ensure_ascii=False))
    elif action == "scan":
        print(json.dumps(run_scan(incremental=True, recalc_only=False, quiet=True), ensure_ascii=False))
    elif action == "scan-full":
        print(json.dumps(run_scan(incremental=False, recalc_only=False, quiet=False), ensure_ascii=False, indent=2))
    elif action == "install-windows-cron":
        cmd_install_windows_cron()
    else:
        print(f"Acción desconocida: {action}")
