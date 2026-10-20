import datetime
import html as html_lib
import io
import json
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path
from typing import Dict, Any, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))

from scoring_engine import load_buyer_profile, score_listing

DB_PATH = BASE_DIR / "data" / "zaragoza_homes.db"
REPORTS_DIR = BASE_DIR / "data" / "reports"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}


def fetch_html(url: str, timeout: int = 14) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS listings (
            id TEXT PRIMARY KEY,
            portal TEXT,
            url TEXT,
            title TEXT,
            neighborhood TEXT,
            address TEXT,
            price_eur REAL,
            previous_price_eur REAL,
            sqm REAL,
            bedrooms INTEGER,
            bathrooms INTEGER,
            floor_num INTEGER,
            has_elevator INTEGER,
            has_terrace INTEGER,
            has_garage INTEGER,
            has_storage INTEGER,
            is_penthouse INTEGER,
            is_agency INTEGER DEFAULT 1,
            seller_name TEXT,
            orientation TEXT,
            heating_type TEXT,
            community_eur REAL,
            year_built INTEGER,
            description TEXT,
            image_url TEXT,
            lat REAL,
            lon REAL,
            raw_json TEXT,
            score_json TEXT,
            final_ranking_score REAL,
            base_score_100 REAL,
            zone_tier TEXT,
            hard_discard INTEGER,
            user_status TEXT DEFAULT 'nuevo',
            user_notes TEXT DEFAULT '',
            first_seen_date TEXT,
            last_seen_date TEXT
        )
        """
    )
    # Asegurar columnas nuevas si la tabla ya existía
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(listings)").fetchall()}
    if "is_agency" not in existing_cols:
        conn.execute("ALTER TABLE listings ADD COLUMN is_agency INTEGER DEFAULT 1")
    if "seller_name" not in existing_cols:
        conn.execute("ALTER TABLE listings ADD COLUMN seller_name TEXT DEFAULT ''")

    ph_cols = {row[1] for row in conn.execute("PRAGMA table_info(price_history)").fetchall()}
    if ph_cols and "listing_id" not in ph_cols:
        conn.execute("DROP TABLE price_history")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id TEXT,
            price_eur REAL,
            recorded_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scan_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT,
            total_processed INTEGER,
            new_listings INTEGER,
            price_drops INTEGER,
            notes TEXT
        )
        """
    )
    conn.execute("DROP TABLE IF EXISTS properties")
    conn.execute("DELETE FROM listings WHERE id LIKE 'ZGZ-%' OR url LIKE '%/con-ascensor/' OR url LIKE '%todas-las-zonas/l'")
    conn.commit()


def analyze_image_url_pixels(image_url: str) -> Dict[str, Any]:
    """
    Descarga la imagen principal real del anuncio y analiza su brillo medio (luminancia)
    con Pillow/estándar para aportar un modificador objetivo y auditable de luminosidad.
    """
    if not image_url or not image_url.startswith("http"):
        return {"analyzed": False, "light_bonus": 0.0}
    try:
        from PIL import Image, ImageStat

        req = urllib.request.Request(image_url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read()
        img = Image.open(io.BytesIO(data)).convert("RGB")
        stat = ImageStat.Stat(img)
        r, g, b = stat.mean
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        if luminance >= 165:
            bonus = 0.5
        elif luminance >= 135:
            bonus = 0.2
        elif luminance < 85:
            bonus = -0.5
        else:
            bonus = 0.0
        return {
            "analyzed": True,
            "avg_brightness_0_255": round(luminance, 1),
            "light_bonus": bonus,
        }
    except Exception:
        return {"analyzed": False, "light_bonus": 0.0}


def parse_text_attributes(text: str) -> Dict[str, Any]:
    """Extrae planta, comunidad, año, calefacción, orientación y extras desde la descripción real del anuncio."""
    t_low = text.lower()

    # Planta
    floor_num: Optional[int] = None
    m_fl = re.search(r'(\d+)[ªºa]\s*planta|planta\s*(\d+)', t_low)
    if m_fl:
        floor_num = int(m_fl.group(1) or m_fl.group(2))
    elif any(w in t_low for w in ["quinta planta", "5ª planta", "quinto piso"]):
        floor_num = 5
    elif any(w in t_low for w in ["cuarta planta", "4ª planta", "cuarto piso"]):
        floor_num = 4
    elif any(w in t_low for w in ["tercera planta", "3ª planta", "tercer piso"]):
        floor_num = 3
    elif any(w in t_low for w in ["segunda planta", "2ª planta", "segundo piso"]):
        floor_num = 2
    elif any(w in t_low for w in ["primera planta", "1ª planta", "primer piso"]):
        floor_num = 1
    elif any(w in t_low for w in ["planta baja", "bajo exterior", "bajo vivienda"]):
        floor_num = 0

    # Gastos de comunidad explícitos (ej. "50€ de comunidad", "gastos de comunidad 45 €")
    comm_eur: Optional[float] = None
    m_comm = re.search(
        r'(?:comunidad|gastos de comunidad)[^0-9\n]{0,25}?(\d{2,3})\s*(?:€|eur)|(\d{2,3})\s*(?:€|eur)[^.\n]{0,25}?de\s+(?:gastos\s+de\s+)?comunidad',
        t_low,
    )
    if m_comm:
        val = int(m_comm.group(1) or m_comm.group(2))
        if 15 <= val <= 300:
            comm_eur = float(val)

    # Año de construcción explícito (ej. "construido en 1978", "año 1985", "finca de 1974")
    year_built: Optional[int] = None
    m_year = re.search(r'(?:construid[oa]\s+en|finca\s+de|edificio\s+de|año\s+de\s+construcci[óo]n|año)\s*(19\d{2}|20[0-2]\d)', t_low)
    if m_year:
        year_built = int(m_year.group(1))

    # Orientación explícita
    orientation: Optional[str] = None
    m_or = re.search(r'orientaci[óo]n\s+(sur\s*este|sureste|sur\s*oeste|suroeste|sur|este|oeste|norte)', t_low)
    if m_or:
        orientation = m_or.group(1).capitalize()

    # Calefacción explícita
    heating_type: Optional[str] = None
    if "calefacción central" in t_low or "calefaccion central" in t_low:
        if "contador individual" in t_low or "contadores individuales" in t_low:
            heating_type = "Central con contador individual"
        else:
            heating_type = "Central"
    elif "gas natural" in t_low or "calefacción individual a gas" in t_low or "calefaccion individual" in t_low:
        heating_type = "Individual a gas"
    elif "bomba de calor" in t_low or "aerotermia" in t_low:
        heating_type = "Bomba de calor / Conductos"
    elif "calefacción eléctrica" in t_low or "calefaccion electrica" in t_low or "acumuladores" in t_low:
        heating_type = "Eléctrica"

    buyer_fee_exempt = any(
        k in t_low for k in [
            "exento de honorarios",
            "sin honorarios para el comprador",
            "sin comisión al comprador",
            "sin comision al comprador",
            "no cobramos comisión al comprador",
        ]
    )

    return {
        "floor_num": floor_num,
        "community_eur": comm_eur,
        "year_built": year_built,
        "orientation": orientation,
        "heating_type": heating_type,
        "buyer_fee_exempt": buyer_fee_exempt,
    }


def scrape_fotocasa_zaragoza(max_pages: int = 6) -> List[Dict[str, Any]]:
    """
    Extrae anuncios 100% reales de Fotocasa Zaragoza (páginas 1 a max_pages),
    incluyendo tanto Particulares (clientTypeId == 1) como Agencias (clientTypeId == 3),
    con sus fotos reales de alta resolución, link directo al inmueble, coordenadas GPS y características.
    """
    results: List[Dict[str, Any]] = []
    seen_ids = set()

    # Mapeo de orientación Fotocasa
    fc_orient_map = {
        1: "Noreste",
        2: "Oeste",
        3: "Norte",
        4: "Suroeste",
        5: "Este",
        6: "Sureste",
        7: "Noroeste",
        8: "Sur",
    }

    for page in range(1, max_pages + 1):
        suffix = f"/{page}" if page > 1 else ""
        url = f"https://www.fotocasa.es/es/comprar/viviendas/zaragoza-capital/todas-las-zonas/l{suffix}?maxPrice=205000&minRooms=3"
        try:
            html = fetch_html(url)
            scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.S)
            re_list = []
            for s in scripts:
                if '"realEstates"' in s:
                    idx = s.find('{"__IMAGES_TO_PRELOAD__"')
                    if idx != -1:
                        fc_data = json.loads(s[idx:])
                        re_list = fc_data.get("initialSearch", {}).get("result", {}).get("realEstates", [])
                    break

            for item in re_list:
                fc_id = str(item.get("id") or "")
                if not fc_id or fc_id in seen_ids:
                    continue
                seen_ids.add(fc_id)

                detail_path = (item.get("detail") or {}).get("es-ES")
                if not detail_path:
                    continue
                full_url = f"https://www.fotocasa.es{detail_path}"

                price_eur = float(item.get("rawPrice") or 0)
                if price_eur < 50000 or price_eur > 215000:
                    continue

                # Fotos reales del inmueble
                media = item.get("multimedia") or []
                photos = [
                    m.get("src")
                    for m in media
                    if isinstance(m, dict) and m.get("type") == "image" and str(m.get("src", "")).startswith("http")
                ]
                if not photos:
                    continue

                # Agencia vs Particular
                client_type_id = item.get("clientTypeId")
                client_alias = (item.get("clientAlias") or "").strip()
                is_agency = (client_type_id == 3) or bool(client_alias)
                seller_name = client_alias if is_agency else "Particular (Trato directo)"

                # Dirección y coordenadas GPS reales
                addr_obj = item.get("address") or {}
                district = addr_obj.get("district") or "Zaragoza"
                neighborhood = addr_obj.get("neighborhood") or district
                upper_level = addr_obj.get("upperLevel") or neighborhood
                zip_code = addr_obj.get("zipCode") or ""

                coords = item.get("coordinates") or {}
                lat = coords.get("latitude")
                lon = coords.get("longitude")

                # Features
                f_map = {f.get("key"): f.get("value") for f in (item.get("features") or []) if isinstance(f, dict)}
                sqm = float(f_map.get("surface") or 0)
                bedrooms = int(f_map.get("rooms") or 0)
                bathrooms = int(f_map.get("bathrooms") or 1)
                if sqm < 50 or bedrooms < 2:
                    continue

                desc = str(item.get("description") or "").strip()
                text_attrs = parse_text_attributes(desc)

                # Planta en Fotocasa: floor value >= 6 equivale a (value - 5)ª planta (6->1ª, 7->2ª, 8->3ª, 9->4ª...)
                fc_floor_val = f_map.get("floor")
                if fc_floor_val is not None and isinstance(fc_floor_val, int):
                    if fc_floor_val >= 6:
                        floor_num = min(15, fc_floor_val - 5)
                    elif fc_floor_val in (3, 4, 5):
                        floor_num = 0
                    else:
                        floor_num = text_attrs["floor_num"]
                else:
                    floor_num = text_attrs["floor_num"]

                path_slug = detail_path.lower()
                has_elevator = (
                    ("elevator" in f_map)
                    or ("ascensor" in path_slug)
                    or ("con ascensor" in desc.lower() and "sin ascensor" not in desc.lower())
                    or ("ascensor" in desc.lower() and "sin ascensor" not in desc.lower())
                )
                if "sin ascensor" in desc.lower():
                    has_elevator = False

                has_terrace = (
                    ("terrace" in f_map)
                    or ("balcony" in f_map)
                    or ("terraza" in path_slug)
                    or any(k in desc.lower() for k in ["terraza", "balcón", "balcon"])
                )
                has_garage = (
                    ("parking" in f_map)
                    or ("parking" in path_slug)
                    or ("garaje incluido" in desc.lower())
                    or ("plaza de garaje" in desc.lower())
                )
                has_storage = ("storage_room" in f_map) or ("trastero" in path_slug) or ("trastero" in desc.lower())
                is_penthouse = ("ático" in desc.lower() or "atico" in desc.lower() or "/atico/" in path_slug)

                orient_code = f_map.get("orientation")
                orientation = fc_orient_map.get(orient_code) or text_attrs["orientation"]

                heating_type = text_attrs["heating_type"]
                if not heating_type and ("heating" in f_map or "calefaccion" in path_slug):
                    heating_type = "Calefacción (declarada en ficha)"

                reduced_raw = str(item.get("reducedPrice") or "")
                m_red = re.search(r'([\d\.]+)', reduced_raw)
                prev_price = None
                if m_red:
                    drop = float(m_red.group(1).replace(".", ""))
                    if drop > 0:
                        prev_price = price_eur + drop

                title = f"Piso en {neighborhood} ({district}) · {int(sqm)} m² · {bedrooms} hab."

                results.append(
                    {
                        "id": f"FC-{fc_id}",
                        "portal": "Fotocasa",
                        "url": full_url,
                        "title": title,
                        "neighborhood": f"{district} - {neighborhood}" if district != neighborhood else district,
                        "address": f"{upper_level} ({zip_code})" if zip_code else upper_level,
                        "price_eur": price_eur,
                        "previous_price_eur": prev_price,
                        "sqm": sqm,
                        "bedrooms": bedrooms,
                        "bathrooms": bathrooms,
                        "floor_num": floor_num,
                        "has_elevator": has_elevator,
                        "has_terrace": has_terrace,
                        "has_garage": has_garage,
                        "has_storage": has_storage,
                        "is_penthouse": is_penthouse,
                        "is_agency": is_agency,
                        "seller_name": seller_name,
                        "buyer_fee_exempt": text_attrs["buyer_fee_exempt"],
                        "orientation": orientation,
                        "heating_type": heating_type,
                        "community_eur": text_attrs["community_eur"],
                        "year_built": text_attrs["year_built"],
                        "is_occupied": bool(item.get("isOccupied", False)),
                        "is_bare_ownership": bool(item.get("isBareOwnership", False)),
                        "is_rented": bool(item.get("isRentedWithTenants", False)),
                        "is_auction": bool(item.get("isAuctioned", False)),
                        "description": desc,
                        "image_url": photos[0],
                        "gallery_urls": photos[:8],
                        "lat": float(lat) if lat else None,
                        "lon": float(lon) if lon else None,
                    }
                )
        except Exception as e:
            print(f"[WARN] Fotocasa page {page} error: {e}")

    return results


def scrape_pisos_com_zaragoza(max_pages: int = 2) -> List[Dict[str, Any]]:
    """
    Extrae anuncios 100% reales de Pisos.com Zaragoza (con ascensor, >=3 hab, <=200.000 €),
    incluyendo sus fotos reales de alta resolución, link directo al anuncio, coordenadas GPS de JSON-LD y agencia/particular.
    """
    results: List[Dict[str, Any]] = []
    seen_ids = set()

    for page in range(1, max_pages + 1):
        suffix = f"{page}/" if page > 1 else ""
        url = f"https://www.pisos.com/venta/pisos-zaragoza_capital/con-ascensor/desde-3-habitaciones/hasta-200000-euros/{suffix}"
        try:
            html = fetch_html(url)
            ad_blocks = [b for b in html.split('<div id="')[1:] if 'class="ad-preview ' in b[:120]]
            for b in ad_blocks:
                ad_id = b.split('"')[0].strip()
                if not ad_id or ad_id in seen_ids:
                    continue
                seen_ids.add(ad_id)

                href_m = re.search(r'href="(/comprar/[^"]+)"', b)
                if not href_m:
                    continue
                full_url = "https://www.pisos.com" + href_m.group(1)

                # Fotos reales del anuncio (excluyendo logos de agencia)
                imgs = [
                    u
                    for u in dict.fromkeys(re.findall(r'https://fotos\.imghs\.net/[^\s"\']+\.jpg', b))
                    if "/prof/logos/" not in u
                ]
                if not imgs:
                    continue

                price_m = re.search(r'data-ad-price="(\d+)"', b) or re.search(r'([\d\.]+)\s*€', b)
                if not price_m:
                    continue
                price_eur = float(price_m.group(1).replace(".", ""))
                if price_eur < 50000 or price_eur > 215000:
                    continue

                title_m = re.search(r'class="ad-preview__title"[^>]*>([^<]+)<', b)
                sub_m = re.search(r'class="ad-preview__subtitle"[^>]*>([^<]+)<', b)
                title_raw = html_lib.unescape(title_m.group(1).strip()) if title_m else "Piso en Zaragoza"
                zone_raw = html_lib.unescape(sub_m.group(1).strip()) if sub_m else "Zaragoza Capital"
                zone_clean = zone_raw.replace("(Zaragoza Capital)", "").strip()

                chars = [html_lib.unescape(c.strip()) for c in re.findall(r'class="ad-preview__char[^"]*"[^>]*>([^<]+)<', b)]
                bedrooms = 3
                bathrooms = 1
                sqm = 75.0
                floor_from_char = None
                for c in chars:
                    c_low = c.lower()
                    if "hab" in c_low:
                        m_h = re.search(r'(\d+)', c_low)
                        if m_h:
                            bedrooms = int(m_h.group(1))
                    elif "bañ" in c_low:
                        m_b = re.search(r'(\d+)', c_low)
                        if m_b:
                            bathrooms = int(m_b.group(1))
                    elif "m²" in c_low or "m2" in c_low:
                        m_s = re.search(r'(\d+)', c_low)
                        if m_s:
                            sqm = float(m_s.group(1))
                    elif "planta" in c_low or "ª" in c_low:
                        m_f = re.search(r'(\d+)', c_low)
                        if m_f:
                            floor_from_char = int(m_f.group(1))

                desc_m = re.search(r'class="ad-preview__description"[^>]*>(.*?)</p>', b, re.S)
                desc = html_lib.unescape(re.sub(r'<[^>]+>', ' ', desc_m.group(1)).strip()) if desc_m else ""
                text_attrs = parse_text_attributes(f"{title_raw} {desc}")

                floor_num = floor_from_char if floor_from_char is not None else text_attrs["floor_num"]
                has_elevator = "sin ascensor" not in desc.lower()

                # Agencia vs Particular
                is_particular = "venta-particular" in b
                is_agency = not is_particular
                agency_slug_m = re.search(r'https://www\.pisos\.com/inmobiliaria-([^/"]+)/', b)
                if is_particular:
                    seller_name = "Particular (Trato directo)"
                elif agency_slug_m:
                    seller_name = agency_slug_m.group(1).replace("_", " ").replace("-", " ").upper()
                else:
                    seller_name = "Agencia en Pisos.com"

                # Coordenadas GPS desde el bloque JSON-LD del propio anuncio
                lat_m = re.search(r'"latitude"\s*:\s*"([^"]+)"', b)
                lon_m = re.search(r'"longitude"\s*:\s*"([^"]+)"', b)
                lat = float(lat_m.group(1).replace(",", ".")) if lat_m else None
                lon = float(lon_m.group(1).replace(",", ".")) if lon_m else None

                results.append(
                    {
                        "id": f"PISOS-{ad_id}",
                        "portal": "Pisos.com",
                        "url": full_url,
                        "title": f"{title_raw} · {int(sqm)} m² · {bedrooms} hab.",
                        "neighborhood": zone_clean or title_raw.replace("Piso en ", ""),
                        "address": title_raw,
                        "price_eur": price_eur,
                        "previous_price_eur": None,
                        "sqm": sqm,
                        "bedrooms": bedrooms,
                        "bathrooms": bathrooms,
                        "floor_num": floor_num,
                        "has_elevator": has_elevator,
                        "has_terrace": any(k in desc.lower() for k in ["terraza", "balcón", "balcon"]),
                        "has_garage": any(k in desc.lower() for k in ["garaje", "parking", "cochera"]),
                        "has_storage": "trastero" in desc.lower(),
                        "is_penthouse": any(k in f"{title_raw} {desc}".lower() for k in ["ático", "atico"]),
                        "is_agency": is_agency,
                        "seller_name": seller_name,
                        "buyer_fee_exempt": text_attrs["buyer_fee_exempt"],
                        "orientation": text_attrs["orientation"],
                        "heating_type": text_attrs["heating_type"],
                        "community_eur": text_attrs["community_eur"],
                        "year_built": text_attrs["year_built"],
                        "description": desc,
                        "image_url": imgs[0],
                        "gallery_urls": imgs[:8],
                        "lat": lat,
                        "lon": lon,
                    }
                )
        except Exception as e:
            print(f"[WARN] Pisos.com page {page} error: {e}")

    return results


def scrape_tecnocasa_zaragoza(max_pages: int = 2, cached_raw_map: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """
    Extrae anuncios 100% reales de Tecnocasa Zaragoza (páginas 1 a max_pages)
    y consulta el JSON de detalle de los inmuebles <= 205.000 € para obtener dirección, GPS y descripción.
    En modo incremental reutiliza la ficha de detalle ya cacheada en SQLite si el ID ya existe.
    """
    results: List[Dict[str, Any]] = []
    seen_ids = set()
    cached_raw_map = cached_raw_map or {}

    for page in range(1, max_pages + 1):
        suffix = f"/pag-{page}" if page > 1 else ""
        url = f"https://www.tecnocasa.es/venta/piso/zaragoza/zaragoza.html{suffix}"
        try:
            h = fetch_html(url)
            m = re.search(r':estates="([^"]+)"', h)
            if not m:
                continue
            estates = json.loads(html_lib.unescape(m.group(1)))
            for card in estates:
                est_id = str(card.get("id") or "")
                if not est_id or est_id in seen_ids:
                    continue
                seen_ids.add(est_id)

                price_str = re.sub(r'[^\d]', '', str(card.get("price") or ""))
                if not price_str:
                    continue
                price_eur = float(price_str)
                if price_eur < 50000 or price_eur > 205000:
                    continue

                full_tc_id = f"TC-{est_id}"
                if full_tc_id in cached_raw_map:
                    cached_item = dict(cached_raw_map[full_tc_id])
                    cached_item["price_eur"] = price_eur
                    results.append(cached_item)
                    continue

                detail_url = card.get("detail_url")
                if not detail_url:
                    continue

                imgs = [
                    img.get("url", {}).get("detail") or img.get("url", {}).get("gallery")
                    for img in (card.get("images") or [])
                    if isinstance(img, dict) and isinstance(img.get("url"), dict)
                ]
                imgs = [u for u in imgs if u and u.startswith("http")]
                if not imgs:
                    continue

                # Consultar detalle individual solo para pisos nuevos no cacheados
                try:
                    det_html = fetch_html(detail_url, timeout=10)
                    det_m = re.search(r':estate="([^"]+)"', det_html)
                    det = json.loads(html_lib.unescape(det_m.group(1))) if det_m else {}
                except Exception:
                    det = {}

                sqm = float(det.get("numeric_surface") or re.sub(r'[^\d]', '', str(card.get("surface") or "75")) or 75)
                rooms_m = re.search(r'(\d+)', str(card.get("rooms") or "3"))
                bedrooms = int(rooms_m.group(1)) if rooms_m else 3
                bath_m = re.search(r'(\d+)', str(card.get("bathrooms") or "1"))
                bathrooms = int(bath_m.group(1)) if bath_m else 1

                desc_raw = str(det.get("description") or "")
                desc = html_lib.unescape(re.sub(r'<[^>]+>', ' ', desc_raw)).strip()
                text_attrs = parse_text_attributes(desc)

                subtitle = str(det.get("subtitle") or card.get("subtitle") or "Zaragoza").replace("Zaragoza,", "").strip()
                address = str(det.get("address") or subtitle)
                lat = det.get("latitude")
                lon = det.get("longitude")

                agency_obj = det.get("agency") or {}
                agency_name = agency_obj.get("company_name") or f"TECNOCASA ({subtitle.upper()})"

                results.append(
                    {
                        "id": full_tc_id,
                        "portal": "Tecnocasa",
                        "url": detail_url,
                        "title": f"Piso en {subtitle} ({address}) · {int(sqm)} m² · {bedrooms} hab.",
                        "neighborhood": subtitle,
                        "address": address,
                        "price_eur": price_eur,
                        "previous_price_eur": None,
                        "sqm": sqm,
                        "bedrooms": bedrooms,
                        "bathrooms": bathrooms,
                        "floor_num": text_attrs["floor_num"],
                        "has_elevator": "sin ascensor" not in desc.lower(),
                        "has_terrace": any(k in desc.lower() for k in ["terraza", "balcón", "balcon", "galería"]),
                        "has_garage": any(k in desc.lower() for k in ["garaje", "cochera", "parking"]),
                        "has_storage": "trastero" in desc.lower(),
                        "is_penthouse": "ático" in desc.lower() or "atico" in desc.lower(),
                        "is_agency": True,
                        "seller_name": agency_name,
                        "buyer_fee_exempt": text_attrs["buyer_fee_exempt"],
                        "orientation": text_attrs["orientation"],
                        "heating_type": text_attrs["heating_type"],
                        "community_eur": text_attrs["community_eur"],
                        "year_built": text_attrs["year_built"],
                        "description": desc,
                        "image_url": imgs[0],
                        "gallery_urls": imgs[:8],
                        "lat": float(lat) if lat else None,
                        "lon": float(lon) if lon else None,
                    }
                )
        except Exception as e:
            print(f"[WARN] Tecnocasa page {page} error: {e}")

    return results


def scrape_redpiso_zaragoza(cached_raw_map: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """
    Extrae anuncios 100% reales de Redpiso Zaragoza y analiza sus páginas de detalle individuales.
    En modo incremental reutiliza los detalles cacheados en SQLite si el ID ya existe.
    """
    results: List[Dict[str, Any]] = []
    cached_raw_map = cached_raw_map or {}
    try:
        html = fetch_html("https://www.redpiso.es/venta-viviendas/zaragoza/zaragoza")
        links = list(dict.fromkeys(re.findall(r'https://www\.redpiso\.es/inmueble/piso-en-venta-[^"\'\s>]+', html)))
        for link in links[:12]:
            try:
                rp_code = link.split("-")[-1]
                full_rp_id = f"RP-{rp_code}"
                if full_rp_id in cached_raw_map:
                    results.append(dict(cached_raw_map[full_rp_id]))
                    continue

                det = fetch_html(link, timeout=10)
                imgs = list(dict.fromkeys(re.findall(r'https://assets\.redpiso\.es/static/customers-sales/images/[^"\'\s>]+', det)))
                if not imgs:
                    continue

                no_style = re.sub(r'<(style|script)[^>]*>.*?</\1>', ' ', det, flags=re.S | re.I)
                text_clean = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', no_style)).strip()

                price_m = re.search(r'([\d\.]+)\s*€', text_clean)
                if not price_m:
                    continue
                price_eur = float(price_m.group(1).replace(".", ""))
                if price_eur < 50000 or price_eur > 210000:
                    continue

                sqm_m = re.search(r'(\d+)\s*m²\s*Construidos', text_clean, re.I) or re.search(r'(\d+)\s*m²', text_clean)
                sqm = float(sqm_m.group(1)) if sqm_m else 75.0
                hab_m = re.search(r'(\d+)\s*Dormitorios', text_clean, re.I)
                bedrooms = int(hab_m.group(1)) if hab_m else 3
                bath_m = re.search(r'(\d+)\s*Baños', text_clean, re.I)
                bathrooms = int(bath_m.group(1)) if bath_m else 1
                year_m = re.search(r'(19\d{2}|20\d{2})\s*Año', text_clean)
                year_built = int(year_m.group(1)) if year_m else None

                has_elevator = bool(re.search(r'Sí\s*Ascensor', text_clean, re.I)) or ("con ascensor" in text_clean.lower())
                if re.search(r'No\s*Ascensor', text_clean, re.I) or "sin ascensor" in text_clean.lower():
                    has_elevator = False

                office_m = re.search(r'(REDPISO ZARAGOZA [A-ZÁÉÍÓÚÑ\s\-]+?)(?:\s+Realiza|\s+Calle|\s+Av)', text_clean)
                seller_name = office_m.group(1).strip() if office_m else "REDPISO ZARAGOZA"

                title_m = re.search(r'property="og:title"\s+content="([^"]+)"', det)
                title_raw = html_lib.unescape(title_m.group(1).strip()) if title_m else "Piso en Zaragoza"

                desc_m = re.search(r'Galería de fotos\s+(.*?)\s+Explorar mapa', text_clean)
                desc = desc_m.group(1).strip() if desc_m else text_clean[:400]
                text_attrs = parse_text_attributes(desc)

                results.append(
                    {
                        "id": full_rp_id,
                        "portal": "Redpiso",
                        "url": link,
                        "title": f"{title_raw} · {int(sqm)} m² · {bedrooms} hab.",
                        "neighborhood": title_raw.replace("Piso en venta en ", ""),
                        "address": title_raw.replace("Piso en venta en ", ""),
                        "price_eur": price_eur,
                        "previous_price_eur": None,
                        "sqm": sqm,
                        "bedrooms": bedrooms,
                        "bathrooms": bathrooms,
                        "floor_num": text_attrs["floor_num"],
                        "has_elevator": has_elevator,
                        "has_terrace": any(k in desc.lower() for k in ["terraza", "balcón", "balcon"]),
                        "has_garage": any(k in desc.lower() for k in ["garaje included", "con garaje", "plaza de garaje incluida"]),
                        "has_storage": "trastero" in desc.lower(),
                        "is_penthouse": "ático" in desc.lower() or "atico" in desc.lower(),
                        "is_agency": True,
                        "seller_name": seller_name,
                        "buyer_fee_exempt": text_attrs["buyer_fee_exempt"],
                        "orientation": text_attrs["orientation"],
                        "heating_type": text_attrs["heating_type"],
                        "community_eur": text_attrs["community_eur"],
                        "year_built": year_built or text_attrs["year_built"],
                        "description": desc,
                        "image_url": imgs[0],
                        "gallery_urls": imgs[:8],
                        "lat": None,
                        "lon": None,
                    }
                )
            except Exception as e_det:
                print(f"[WARN] Redpiso detail error ({link}): {e_det}")
    except Exception as e:
        print(f"[WARN] Redpiso list error: {e}")

    return results


def deduplicate_listing_rows(rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
    """
    Fusiona anuncios duplicados del mismo inmueble publicados en distintos portales
    (mismo precio, m² con margen ±2 m², habitaciones y mismo barrio o agencia).
    Devuelve diccionarios enriquecidos con `matched_neighborhood`, `zone_points` y `also_in_portals`.
    """
    unique: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        sc = json.loads(d.get("score_json") or "{}")
        z_info = sc.get("zone_info", {})
        matched_nb = z_info.get("matched_neighborhood") or d.get("neighborhood") or "Zaragoza"
        zone_pts = float(z_info.get("zone_score_10") or sc.get("scores_1_to_10", {}).get("neighborhood_tier") or 5.5)
        d["matched_neighborhood"] = matched_nb
        d["zone_points"] = zone_pts
        d["also_in_portals"] = []

        price = int(round(float(d.get("price_eur") or 0)))
        sqm = int(round(float(d.get("sqm") or 0)))
        beds = int(d.get("bedrooms") or 0)
        seller = (d.get("seller_name") or "").strip().lower()[:10]
        nb_norm = matched_nb.strip().lower()

        matched_existing = None
        for ex in unique:
            ex_price = int(round(float(ex.get("price_eur") or 0)))
            ex_sqm = int(round(float(ex.get("sqm") or 0)))
            ex_beds = int(ex.get("bedrooms") or 0)
            ex_seller = (ex.get("seller_name") or "").strip().lower()[:10]
            ex_nb = (ex.get("matched_neighborhood") or "").strip().lower()
            if ex_price == price and abs(ex_sqm - sqm) <= 2 and ex_beds == beds and (ex_nb == nb_norm or (seller and ex_seller == seller)):
                matched_existing = ex
                break

        if matched_existing is None:
            unique.append(d)
        else:
            if d.get("portal") and d.get("portal") != matched_existing.get("portal"):
                if d["portal"] not in matched_existing["also_in_portals"]:
                    matched_existing["also_in_portals"].append(d["portal"])
    return unique


def run_scan(incremental: bool = True, recalc_only: bool = False, quiet: bool = False) -> Dict[str, Any]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    profile = load_buyer_profile()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    init_db(conn)

    now_iso = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")

    cached_rows = conn.execute("SELECT id, raw_json FROM listings").fetchall()
    cached_raw_map: Dict[str, Dict[str, Any]] = {}
    for cr in cached_rows:
        try:
            cached_raw_map[cr["id"]] = json.loads(cr["raw_json"] or "{}")
        except Exception:
            pass

    if recalc_only:
        all_listings = list(cached_raw_map.values())
        fc_listings = [x for x in all_listings if x.get("portal") == "Fotocasa"]
        pisos_listings = [x for x in all_listings if x.get("portal") == "Pisos.com"]
        tc_listings = [x for x in all_listings if x.get("portal") == "Tecnocasa"]
        rp_listings = [x for x in all_listings if x.get("portal") == "Redpiso"]
    else:
        cache_arg = cached_raw_map if incremental else {}
        if not quiet:
            print(f"[1/4] Fotocasa Zaragoza (modo={'incremental' if incremental else 'completo'})...")
        fc_listings = scrape_fotocasa_zaragoza(max_pages=6)
        if not quiet:
            print(f"      -> {len(fc_listings)} viviendas reales extraídas de Fotocasa.")
            print("[2/4] Pisos.com Zaragoza...")
        pisos_listings = scrape_pisos_com_zaragoza(max_pages=2)
        if not quiet:
            print(f"      -> {len(pisos_listings)} viviendas reales extraídas de Pisos.com.")
            print("[3/4] Tecnocasa Zaragoza...")
        tc_listings = scrape_tecnocasa_zaragoza(max_pages=2, cached_raw_map=cache_arg)
        if not quiet:
            print(f"      -> {len(tc_listings)} viviendas reales extraídas de Tecnocasa.")
            print("[4/4] Redpiso Zaragoza...")
        rp_listings = scrape_redpiso_zaragoza(cached_raw_map=cache_arg)
        if not quiet:
            print(f"      -> {len(rp_listings)} viviendas reales extraídas de Redpiso.")
        all_listings = fc_listings + pisos_listings + tc_listings + rp_listings

    new_count = 0
    price_drops = 0
    new_img_downloads = 0

    for idx, item in enumerate(all_listings):
        lid = item["id"]
        prev_cached = cached_raw_map.get(lid, {})
        if "image_pixel_luminance" in prev_cached and incremental:
            item["image_light_bonus"] = prev_cached.get("image_light_bonus", 0.0)
            item["image_pixel_luminance"] = prev_cached.get("image_pixel_luminance")
        elif new_img_downloads < 25 and item.get("image_url") and not recalc_only:
            img_stats = analyze_image_url_pixels(item["image_url"])
            new_img_downloads += 1
            if img_stats.get("analyzed"):
                item["image_light_bonus"] = img_stats["light_bonus"]
                item["image_pixel_luminance"] = img_stats["avg_brightness_0_255"]

        score_res = score_listing(item, profile)
        resolved_nb = score_res["zone_info"]["matched_neighborhood"]
        if "raw_neighborhood" not in item:
            item["raw_neighborhood"] = item.get("neighborhood", "")
        if resolved_nb and resolved_nb not in ("Zaragoza Capital", "Zaragoza (General)"):
            item["neighborhood"] = resolved_nb

        existing = conn.execute("SELECT price_eur, first_seen_date, user_status, user_notes FROM listings WHERE id = ?", (lid,)).fetchone()
        if existing is None:
            new_count += 1
            first_seen = today_str
            prev_price = item.get("previous_price_eur")
            user_status = "nuevo"
            user_notes = ""
            conn.execute(
                "INSERT INTO price_history (listing_id, price_eur, recorded_at) VALUES (?, ?, ?)",
                (lid, item["price_eur"], now_iso),
            )
        else:
            first_seen = existing["first_seen_date"]
            user_status = existing["user_status"]
            user_notes = existing["user_notes"]
            old_price = existing["price_eur"]
            if item["price_eur"] < old_price:
                price_drops += 1
                prev_price = old_price
                conn.execute(
                    "INSERT INTO price_history (listing_id, price_eur, recorded_at) VALUES (?, ?, ?)",
                    (lid, item["price_eur"], now_iso),
                )
            else:
                prev_price = item.get("previous_price_eur")

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
            ) VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?
            )
            """,
            (
                lid,
                item.get("portal", ""),
                item.get("url", ""),
                item.get("title", ""),
                resolved_nb or item.get("neighborhood", ""),
                item.get("address", ""),
                item.get("price_eur", 0),
                prev_price,
                item.get("sqm", 0),
                item.get("bedrooms", 0),
                item.get("bathrooms", 1),
                item.get("floor_num"),
                1 if item.get("has_elevator", True) else 0,
                1 if item.get("has_terrace", False) else 0,
                1 if item.get("has_garage", False) else 0,
                1 if item.get("has_storage", False) else 0,
                1 if item.get("is_penthouse", False) else 0,
                1 if item.get("is_agency", True) else 0,
                item.get("seller_name", ""),
                item.get("orientation", ""),
                item.get("heating_type", ""),
                item.get("community_eur"),
                item.get("year_built"),
                item.get("description", ""),
                item.get("image_url", ""),
                item.get("lat"),
                item.get("lon"),
                json.dumps(item, ensure_ascii=False),
                json.dumps(score_res, ensure_ascii=False),
                score_res["final_ranking_score"],
                score_res["base_score_100"],
                score_res["zone_info"]["tier"],
                1 if score_res["hard_discard"] else 0,
                user_status,
                user_notes,
                first_seen,
                today_str,
            ),
        )

    conn.execute(
        "INSERT INTO scan_runs (run_at, total_processed, new_listings, price_drops, notes) VALUES (?, ?, ?, ?, ?)",
        (
            now_iso,
            len(all_listings),
            new_count,
            price_drops,
            f"Portales reales: Fotocasa ({len(fc_listings)}), Pisos.com ({len(pisos_listings)}), Tecnocasa ({len(tc_listings)}), Redpiso ({len(rp_listings)})",
        ),
    )
    conn.commit()

    candidate_rows = conn.execute(
        """
        SELECT id, title, neighborhood, portal, is_agency, seller_name, price_eur, sqm, bedrooms, floor_num,
               final_ranking_score, base_score_100, zone_tier, score_json, url
        FROM listings
        WHERE hard_discard = 0
        ORDER BY final_ranking_score DESC
        """
    ).fetchall()

    dedup_candidates = deduplicate_listing_rows(candidate_rows)
    top_rows = dedup_candidates[:15]

    report_path = REPORTS_DIR / f"report_{today_str}.md"
    lines = [
        f"# Informe Diario de Viviendas Reales en Zaragoza ({today_str})",
        "",
        f"- **Fecha de ejecución**: `{now_iso}`",
        f"- **Repositorio activo**: `C:\\personal\\Git_Repositories\\zgz-house-finder`",
        f"- **Viviendas reales analizadas**: **{len(all_listings)}** (`Fotocasa`: {len(fc_listings)}, `Pisos.com`: {len(pisos_listings)}, `Tecnocasa`: {len(tc_listings)}, `Redpiso`: {len(rp_listings)})",
        f"- **Candidatas únicas tras deduplicación multi-portal**: `{len(dedup_candidates)}`",
        f"- **Nuevas ofertas detectadas**: `{new_count}`",
        f"- **Bajadas de precio**: `{price_drops}`",
        "",
        "## Top 15 Viviendas Únicas según tu Perfil (Puntuación de Zona: 10 / 7.8 / 5.5 / 3.5 pts)",
        "",
        "| # | Puntos | Barrio (Pts Zona) | Vendedor | Vivienda | Portal | Precio | Reforma Est. | Coste Total | Cuota Hip. | Enlace |",
        "|---|--------|-------------------|----------|----------|--------|--------|--------------|-------------|------------|--------|",
    ]
    for idx, r in enumerate(top_rows, 1):
        sc = json.loads(r["score_json"])
        fa = sc["financial_analysis"]
        z_pts = r["zone_points"]
        z_pts_str = f"{int(z_pts)}" if float(z_pts).is_integer() else f"{z_pts:.1f}"
        zone_col = f"{r['matched_neighborhood']} ({z_pts_str} pts)"
        seller_badge = "Particular" if not r["is_agency"] else f"Agencia: {r['seller_name'][:18]}"
        portal_str = r["portal"]
        if r.get("also_in_portals"):
            portal_str += f" (+{', '.join(r['also_in_portals'])})"
        lines.append(
            f"| {idx} | **{r['final_ranking_score']} pts** | {zone_col} | {seller_badge} | {r['title']} | {portal_str} | "
            f"{int(r['price_eur']):,} € | {int(fa['estimated_reform_eur']):,} € | **{int(fa['total_project_cost_eur']):,} €** | "
            f"{fa['monthly_mortgage_eur']:.0f} €/mes | [Ver piso]({r['url']}) |"
        )

    report_text = "\n".join(lines)
    report_path.write_text(report_text, encoding="utf-8")
    (BASE_DIR / "data" / "ultimo_informe_diario.md").write_text(report_text, encoding="utf-8")
    conn.close()

    top3_digest = [
        {
            "id": r["id"],
            "score": r["final_ranking_score"],
            "neighborhood": r["matched_neighborhood"],
            "zone_points": r["zone_points"],
            "seller": "Particular" if not r["is_agency"] else r["seller_name"],
            "title": r["title"],
            "price_eur": int(r["price_eur"]),
            "url": r["url"],
        }
        for r in top_rows[:3]
    ]

    return {
        "run_at": now_iso,
        "mode": "recalc_only" if recalc_only else ("incremental" if incremental else "full"),
        "total_processed": len(all_listings),
        "unique_candidates": len(dedup_candidates),
        "new_listings": new_count,
        "price_drops": price_drops,
        "report_path": str(report_path),
        "top_3": top3_digest,
    }


if __name__ == "__main__":
    args = set(sys.argv[1:])
    is_full = "--full" in args
    is_recalc = "--recalc-only" in args
    is_quiet = "--quiet" in args
    res = run_scan(incremental=not is_full, recalc_only=is_recalc, quiet=is_quiet)
    if is_quiet:
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(json.dumps(res, indent=2, ensure_ascii=False))
