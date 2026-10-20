import html as html_lib
import json
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

BASE_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = BASE_DIR / "scripts"
STATIC_DIR = BASE_DIR / "static"
CONFIG_PATH = BASE_DIR / "config" / "buyer_profile.json"
GUIDE_PATH = BASE_DIR / "docs" / "GUIA_COMPRA_ZARAGOZA.md"
DB_PATH = BASE_DIR / "data" / "zaragoza_homes.db"

sys.path.insert(0, str(SCRIPTS_DIR))

from run_daily_scan import (  # noqa: E402
    HEADERS,
    init_db,
    parse_text_attributes,
    run_scan,
)
from scoring_engine import (  # noqa: E402
    ZARAGOZA_AVG_SQM_PRICES,
    load_buyer_profile,
    score_listing,
)


def get_db_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def build_neighborhoods_ref(profile: dict) -> dict:
    ref = {}
    zm = profile.get("zone_multipliers", {})
    for tier_key, tdata in zm.items():
        for nb in tdata.get("neighborhoods", []):
            avg_sqm = ZARAGOZA_AVG_SQM_PRICES.get(nb, 2100)
            ref[nb] = {
                "tier": tier_key,
                "multiplier": tdata.get("multiplier", 1.0),
                "base_score_10": tdata.get("base_score_10", 5.0),
                "avg_sqm_eur": int(round(avg_sqm * 0.86)),
                "reformed_sqm_eur": avg_sqm,
            }
    return ref


def format_row_for_frontend(r: sqlite3.Row) -> dict:
    d = dict(r)
    raw = json.loads(d.get("raw_json") or "{}")
    sc = json.loads(d.get("score_json") or "{}")
    fin = sc.get("financial_analysis", {})
    zone_info = sc.get("zone_info", {})
    geo = sc.get("geo_distances", {})
    bonuses = sc.get("bonuses", {})
    seller = sc.get("seller_info", {})

    gallery = raw.get("gallery_urls") or ([d["image_url"]] if d.get("image_url") else [])
    price = float(d.get("price_eur") or 0)
    sqm = max(1.0, float(d.get("sqm") or 75))
    avg_zone_sqm = float(zone_info.get("avg_zone_eur_sqm") or 2100)
    reformed_value = int(round(avg_zone_sqm * sqm))
    total_inv = int(round(fin.get("total_project_cost_eur") or price))
    net_equity = reformed_value - total_inv

    is_agency = bool(d.get("is_agency", 1))
    seller_name = d.get("seller_name") or seller.get("seller_name") or ("Agencia" if is_agency else "Particular")
    buyer_fee_exempt = bool(seller.get("buyer_fee_exempt", False))

    badges = []
    if not is_agency:
        badges.append("Particular (Trato directo · 0 € comisión)")
    else:
        if buyer_fee_exempt:
            badges.append(f"Agencia: {seller_name} (Sin comisión comprador · -5 pts)")
        else:
            badges.append(f"Agencia: {seller_name} ({bonuses.get('agency_penalty_points', -15)} pts)")

    if price <= 135000:
        badges.append("Estrategia A (<135k € para reformar)")
    if d.get("has_garage"):
        badges.append(f"Garaje (+{int(bonuses.get('garage_points', 20))} pts)")
    if d.get("has_storage"):
        badges.append(f"Trastero (+{int(bonuses.get('storage_room_points', 8))} pts)")
    if d.get("is_penthouse"):
        badges.append(f"Ático (+{int(bonuses.get('penthouse_solarium_points', 10))} pts)")
    if d.get("has_terrace"):
        badges.append("Con terraza")
    if fin.get("fits_monthly_limit_700"):
        badges.append(f"Cuota {fin.get('monthly_mortgage_eur', 0):.0f} €/mes (≤700 €)")

    return {
        "id": d["id"],
        "portal": d.get("portal") or "Portal",
        "url": d.get("url") or "",
        "title": d.get("title") or "Vivienda en Zaragoza",
        "neighborhood": zone_info.get("matched_neighborhood") or d.get("neighborhood") or "Zaragoza",
        "raw_neighborhood": d.get("neighborhood") or "",
        "street": d.get("address") or d.get("neighborhood") or "Zaragoza",
        "price": int(round(price)),
        "previous_price": int(round(d["previous_price_eur"])) if d.get("previous_price_eur") else None,
        "sqm": int(round(sqm)),
        "bedrooms": int(d.get("bedrooms") or 3),
        "bathrooms": int(d.get("bathrooms") or 1),
        "floor": d.get("floor_num"),
        "has_elevator": bool(d.get("has_elevator", 1)),
        "has_terrace": bool(d.get("has_terrace", 0)),
        "has_garage": bool(d.get("has_garage", 0)),
        "has_storage": bool(d.get("has_storage", 0)),
        "has_penthouse": bool(d.get("is_penthouse", 0)),
        "is_agency": is_agency,
        "seller_name": seller_name,
        "buyer_fee_exempt": buyer_fee_exempt,
        "orientation": d.get("orientation") or "No especificada en anuncio",
        "heating_type": d.get("heating_type") or "No especificada en anuncio",
        "community_eur": int(d["community_eur"]) if d.get("community_eur") is not None else None,
        "year_built": d.get("year_built"),
        "description": d.get("description") or "",
        "image_url": d.get("image_url") or "",
        "photos": gallery,
        "lat": d.get("lat"),
        "lon": d.get("lon"),
        "zone_tier": d.get("zone_tier") or zone_info.get("tier") or "x2",
        "total_points": float(d.get("final_ranking_score") or 0.0),
        "score_100": float(d.get("base_score_100") or 0.0),
        "hard_discard": bool(d.get("hard_discard", 0)),
        "user_status": d.get("user_status") or "nuevo",
        "user_notes": d.get("user_notes") or "",
        "first_seen_date": d.get("first_seen_date") or "",
        "last_seen_date": d.get("last_seen_date") or "",
        "evaluation": {
            "price_per_sqm": int(round(price / sqm)),
            "scores_1_to_10": sc.get("scores_1_to_10", {}),
            "audit_evidence": sc.get("audit_evidence", {}),
            "bonuses": bonuses,
            "bonus_points": bonuses.get("total_bonuses", 0.0),
            "alerts": sc.get("alerts", []),
            "badges": badges,
            "nearest_park_name": geo.get("nearest_park_name"),
            "nearest_park_m": geo.get("nearest_park_m"),
            "nearest_tram_name": geo.get("nearest_tram_name"),
            "nearest_tram_m": geo.get("nearest_tram_m"),
            "financials": {
                "itp_pct": fin.get("itp_pct", 6.0),
                "itp_and_notary_eur": fin.get("taxes_and_notary_eur", 0),
                "agency_fee_eur": fin.get("agency_fee_eur", 0),
                "reform_type": fin.get("reform_type", ""),
                "reform_trigger": fin.get("reform_trigger", ""),
                "estimated_reform_eur": fin.get("estimated_reform_eur", 0),
                "reform_breakdown": fin.get("reform_breakdown", {}),
                "total_investment_eur": total_inv,
                "reformed_market_value_eur": reformed_value,
                "net_equity_gain_eur": net_equity,
                "monthly_quota_90_eur": round(float(fin.get("monthly_mortgage_eur") or 0)),
                "cash_needed_90_eur": fin.get("cash_needed_total_eur", 0),
                "fits_700_rule": bool(fin.get("fits_monthly_limit_700", True)),
            },
        },
    }


def fetch_all_properties_and_profile() -> dict:
    profile = load_buyer_profile()
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT * FROM listings ORDER BY hard_discard ASC, final_ranking_score DESC"
    ).fetchall()
    scan_row = conn.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()

    props = [format_row_for_frontend(r) for r in rows]
    guide_md = GUIDE_PATH.read_text(encoding="utf-8") if GUIDE_PATH.exists() else ""

    return {
        "profile": profile,
        "properties": props,
        "neighborhoods_ref": build_neighborhoods_ref(profile),
        "guide_markdown": guide_md,
        "last_scan": dict(scan_row) if scan_row else None,
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def recalculate_all_scores(new_profile: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(new_profile, indent=2, ensure_ascii=False), encoding="utf-8")
    conn = get_db_connection()
    rows = conn.execute("SELECT id, raw_json FROM listings").fetchall()
    for r in rows:
        item = json.loads(r["raw_json"] or "{}")
        if not item:
            continue
        score_res = score_listing(item, new_profile)
        conn.execute(
            """
            UPDATE listings
            SET score_json = ?, final_ranking_score = ?, base_score_100 = ?, zone_tier = ?, hard_discard = ?
            WHERE id = ?
            """,
            (
                json.dumps(score_res, ensure_ascii=False),
                score_res["final_ranking_score"],
                score_res["base_score_100"],
                score_res["zone_info"]["tier"],
                1 if score_res["hard_discard"] else 0,
                r["id"],
            ),
        )
    conn.commit()
    conn.close()


class ZgzHouseFinderHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        pass

    def _send_json(self, data: dict, status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            index_file = STATIC_DIR / "index.html"
            content = index_file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        if parsed.path == "/api/state":
            self._send_json(fetch_all_properties_and_profile())
            return

        if parsed.path == "/api/image-proxy":
            qs = parse_qs(parsed.query)
            img_url = (qs.get("url") or [""])[0]
            if not img_url.startswith("http"):
                self.send_error(400, "Invalid image URL")
                return
            try:
                req = urllib.request.Request(img_url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    img_bytes = resp.read()
                    ctype = resp.headers.get("Content-Type", "image/jpeg")
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("Content-Length", str(len(img_bytes)))
                self.end_headers()
                self.wfile.write(img_bytes)
            except Exception as e:
                self.send_error(502, f"Proxy error: {e}")
            return

        self.send_error(404, "Not Found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        body = json.loads(raw_body) if raw_body else {}

        if parsed.path == "/api/property/status":
            prop_id = body.get("id")
            status = body.get("user_status", "nuevo")
            notes = body.get("user_notes", "")

            conn = get_db_connection()
            conn.execute(
                "UPDATE listings SET user_status = ?, user_notes = ? WHERE id = ?",
                (status, notes, prop_id),
            )
            conn.commit()
            conn.close()
            self._send_json({"ok": True, "state": fetch_all_properties_and_profile()})
            return

        if parsed.path == "/api/weights":
            profile = load_buyer_profile()
            if "weights" in body:
                for k, val in body["weights"].items():
                    if k in profile["scoring_factors_1_to_10"]:
                        profile["scoring_factors_1_to_10"][k]["weight"] = float(val)
            if "bonuses" in body:
                for bk, bval in body["bonuses"].items():
                    profile["fixed_bonuses"][bk] = float(bval)
            if "zone_assignments" in body and isinstance(body["zone_assignments"], dict):
                zm = profile.get("zone_multipliers", {})
                # Remove reassigned neighborhoods from all tiers first, then place into target tier
                for nb_name, target_tier in body["zone_assignments"].items():
                    if target_tier not in ("x4", "x3", "x2", "x1"):
                        continue
                    for t_key in ("x4", "x3", "x2", "x1"):
                        if t_key in zm and "neighborhoods" in zm[t_key]:
                            zm[t_key]["neighborhoods"] = [
                                n for n in zm[t_key]["neighborhoods"] if n.lower() != nb_name.lower()
                            ]
                    if target_tier in zm and "neighborhoods" in zm[target_tier]:
                        zm[target_tier]["neighborhoods"].append(nb_name)
                        # If user moves Pinares de Venecia or Parque Venecia, keep Venecia aliases synced
                        if nb_name.lower() in ("pinares de venecia", "parque venecia", "venecia"):
                            for alias in ("Pinares de Venecia", "Parque Venecia", "Venecia"):
                                for t_key in ("x4", "x3", "x2", "x1"):
                                    zm[t_key]["neighborhoods"] = [
                                        n for n in zm[t_key]["neighborhoods"] if n.lower() != alias.lower()
                                    ]
                                zm[target_tier]["neighborhoods"].append(alias)
            recalculate_all_scores(profile)
            self._send_json({"ok": True, "state": fetch_all_properties_and_profile()})
            return

        if parsed.path == "/api/property/add":
            url = (body.get("url") or "").strip()
            photo_url = (body.get("photo_url") or "").strip()
            title = (body.get("title") or "").strip()
            desc = (body.get("description") or "").strip()

            # Si el usuario pega una URL real y no pone foto, intentamos extraer og:image real de la URL
            if url.startswith("http") and not photo_url:
                try:
                    req = urllib.request.Request(url, headers=HEADERS)
                    with urllib.request.urlopen(req, timeout=8) as r:
                        html_page = r.read().decode("utf-8", errors="ignore")
                    og_m = re.search(r'property="og:image"\s+content="([^"]+)"', html_page)
                    if og_m:
                        photo_url = og_m.group(1)
                    if not title:
                        t_m = re.search(r'property="og:title"\s+content="([^"]+)"', html_page)
                        if t_m:
                            title = html_lib.unescape(t_m.group(1).strip())
                except Exception:
                    pass

            text_attrs = parse_text_attributes(f"{title} {desc}")
            is_agency = bool(body.get("is_agency", False))
            seller_name = body.get("seller_name") or ("Agencia Inmobiliaria" if is_agency else "Particular (Añadido manual)")

            item = {
                "id": f"MANUAL-{int(datetime.now().timestamp())}",
                "portal": body.get("portal") or "Añadido Manual",
                "url": url,
                "title": title or "Piso añadido manualmente en Zaragoza",
                "neighborhood": body.get("neighborhood") or "Actur",
                "address": body.get("street") or body.get("neighborhood") or "Zaragoza",
                "price_eur": float(body.get("price") or 132000),
                "previous_price_eur": None,
                "sqm": float(body.get("sqm") or 75),
                "bedrooms": int(body.get("bedrooms") or 3),
                "bathrooms": int(body.get("bathrooms") or 1),
                "floor_num": int(body["floor"]) if body.get("floor") is not None else text_attrs["floor_num"],
                "has_elevator": bool(body.get("has_elevator", True)),
                "has_terrace": bool(body.get("has_terrace", True)),
                "has_garage": bool(body.get("has_garage", False)),
                "has_storage": bool(body.get("has_storage", False)),
                "is_penthouse": bool(body.get("has_penthouse", False)),
                "is_agency": is_agency,
                "seller_name": seller_name,
                "buyer_fee_exempt": text_attrs["buyer_fee_exempt"],
                "orientation": body.get("orientation") or text_attrs["orientation"],
                "heating_type": body.get("heating_type") or text_attrs["heating_type"],
                "community_eur": float(body["community_eur"]) if body.get("community_eur") else text_attrs["community_eur"],
                "year_built": text_attrs["year_built"],
                "description": desc,
                "image_url": photo_url,
                "gallery_urls": [photo_url] if photo_url else [],
                "lat": None,
                "lon": None,
            }
            profile = load_buyer_profile()
            score_res = score_listing(item, profile)
            today_str = datetime.now().strftime("%Y-%m-%d")

            conn = get_db_connection()
            conn.execute(
                """
                INSERT OR REPLACE INTO listings (
                    id, portal, url, title, neighborhood, address,
                    price_eur, previous_price_eur, sqm, bedrooms, bathrooms,
                    floor_num, has_elevator, has_terrace, has_garage, has_storage, is_penthouse,
                    is_agency, seller_name,
                    orientation, heating_type, community_eur, year_built,
                    description, image_url, lat, lon,
                    raw_json, score_json, final_ranking_score, base_score_100,
                    zone_tier, hard_discard, user_status, user_notes,
                    first_seen_date, last_seen_date
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    item["id"], item["portal"], item["url"], item["title"], item["neighborhood"], item["address"],
                    item["price_eur"], None, item["sqm"], item["bedrooms"], item["bathrooms"],
                    item["floor_num"], 1 if item["has_elevator"] else 0, 1 if item["has_terrace"] else 0,
                    1 if item["has_garage"] else 0, 1 if item["has_storage"] else 0, 1 if item["is_penthouse"] else 0,
                    1 if item["is_agency"] else 0, item["seller_name"],
                    item["orientation"] or "", item["heating_type"] or "", item["community_eur"], item["year_built"],
                    item["description"], item["image_url"], None, None,
                    json.dumps(item, ensure_ascii=False), json.dumps(score_res, ensure_ascii=False),
                    score_res["final_ranking_score"], score_res["base_score_100"],
                    score_res["zone_info"]["tier"], 1 if score_res["hard_discard"] else 0,
                    "nuevo", "", today_str, today_str,
                ),
            )
            conn.commit()
            conn.close()
            self._send_json({"ok": True, "state": fetch_all_properties_and_profile()})
            return

        if parsed.path == "/api/scan":
            stats = run_scan()
            self._send_json({"ok": True, "scan_stats": stats, "state": fetch_all_properties_and_profile()})
            return

        self.send_error(404, "Endpoint not found")


def run_server(port: int = 8765) -> None:
    get_db_connection().close()
    server = ThreadingHTTPServer(("127.0.0.1", port), ZgzHouseFinderHandler)
    print(f"ZgzHouseFinder Web Local activa en: http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    port_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    run_server(port_arg)
