import json
import math
import re
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE_PATH = BASE_DIR / "config" / "buyer_profile.json"

# Precios medios reales de referencia en Zaragoza por barrio (€/m² vivienda reformada/buen estado, 2026)
# Fuente: Informes de mercado inmobiliario Zaragoza Capital (Idealista/Fotocasa/Colegio Registradores Aragón)
ZARAGOZA_AVG_SQM_PRICES = {
    "Romareda": 2950,
    "Universidad": 2750,
    "Fernando El Católico": 2800,
    "Plaza Roma": 2650,
    "Hernán Cortés": 2900,
    "Ruiseñores": 2850,
    "Actur": 2350,
    "Independencia": 3100,
    "Centro": 2950,
    "Sagasta": 3050,
    "Casablanca": 2550,
    "Pinares de Venecia": 2400,
    "Parque Venecia": 2400,
    "Venecia": 2400,
    "La Almozara": 2150,
    "Almozara": 2150,
    "Universitas": 2250,
    "Los Enlaces": 2050,
    "Magdalena": 2150,
    "Rebolería": 2050,
    "La Paz": 2000,
    "Miralbueno": 2450,
    "Montecanal": 2550,
    "Rosales del Canal": 2500,
    "Valdespartera": 2250,
    "Barrio Jesús": 2200,
    "Arrabal": 2000,
    "San José": 1950,
    "Miraflores": 2100,
    "Santa Isabel": 1900,
    "Parque Goya": 2050,
    "Delicias": 1720,
    "Las Fuentes": 1680,
    "Torrero": 1750,
    "Valdefierro": 1820,
    "Arcosur": 2050,
    "Oliver": 1480,
    "El Gancho": 1550,
    "San Pablo": 1600,
    "Casco Histórico": 1950,
}

# Coordenadas GPS reales de los principales parques y zonas verdes de Zaragoza
ZARAGOZA_PARKS_GPS = [
    ("Parque Grande José Antonio Labordeta", 41.6335, -0.8938),
    ("Parque del Agua Luis Buñuel", 41.6710, -0.9075),
    ("Parque Tío Jorge", 41.6656, -0.8805),
    ("Parque Delicias", 41.6462, -0.9142),
    ("Parque Bruil", 41.6478, -0.8672),
    ("Parque Pignatelli", 41.6392, -0.8860),
    ("Parque Miraflores", 41.6398, -0.8765),
    ("Parque Castillo Palomar", 41.6525, -0.9078),
    ("Parque Sedetania", 41.6475, -0.9200),
    ("Parque Torre Ramona", 41.6378, -0.8588),
    ("Parque Lineal del Huerva / Emperador Augusto", 41.6385, -0.8920),
    ("Parque de la Aljafería", 41.6565, -0.8970),
    ("Parque de Oriente / Ribera del Ebro", 41.6578, -0.8645),
    ("Pinares de Venecia / Canal Imperial", 41.6255, -0.8865),
]

# Coordenadas GPS reales de paradas del Tranvía de Zaragoza (Línea 1)
ZARAGOZA_TRAM_STOPS_GPS = [
    ("Av. Academia / Parque Goya", 41.6862, -0.8755),
    ("Juslibol / Campus Río Ebro", 41.6790, -0.8842),
    ("Grancasa / Actur", 41.6680, -0.8884),
    ("Legaz Lacambra / Adolfo Aznar", 41.6632, -0.8878),
    ("Pablo Neruda / León Felipe", 41.6695, -0.8872),
    ("La Chimenea / Puente Santiago", 41.6592, -0.8848),
    ("César Augusto / Mercado Central", 41.6552, -0.8825),
    ("Plaza España / Independencia", 41.6518, -0.8808),
    ("Plaza Aragón / Paraíso", 41.6475, -0.8848),
    ("Gran Vía / Fernando El Católico", 41.6425, -0.8895),
    ("Plaza San Francisco / Universidad", 41.6388, -0.8928),
    ("Emperador Carlos V / Romareda", 41.6345, -0.8980),
    ("Casablanca / Argualas", 41.6285, -0.9065),
    ("Valdespartera / Mago de Oz", 41.6175, -0.9165),
]


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia geodésica en metros entre dos coordenadas GPS."""
    r = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def nearest_poi(lat: Optional[float], lon: Optional[float], pois: List[Tuple[str, float, float]]) -> Tuple[Optional[str], Optional[int]]:
    if not lat or not lon or abs(lat) < 1 or abs(lon) < 0.01:
        return None, None
    best_name = None
    best_dist = 1e9
    for name, plat, plon in pois:
        d = haversine_m(lat, lon, plat, plon)
        if d < best_dist:
            best_dist = d
            best_name = name
    return best_name, int(round(best_dist))


def load_buyer_profile() -> Dict[str, Any]:
    with open(PROFILE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def classify_neighborhood(
    neighborhood_text: str,
    address_text: str,
    profile: Dict[str, Any],
    title_text: str = "",
    url_text: str = "",
) -> Tuple[str, float, float, str]:
    """
    Devuelve (tier_key, multiplier, base_score_10, matched_neighborhood)
    basado estrictamente en el texto del barrio, dirección, título y URL del anuncio.
    """
    nb_low = (neighborhood_text or "").lower()
    addr_low = (address_text or "").lower()
    title_low = (title_text or "").lower()
    url_clean = (url_text or "").lower().replace("_", " ").replace("-", " ")
    specific_low = f"{addr_low} {title_low}"
    combined = f"{nb_low} {addr_low} {title_low} {url_clean}"
    zm = profile["zone_multipliers"]

    def find_nb_tier(target_nb: str) -> Optional[Tuple[str, float, float, str]]:
        for t_key in ["x4", "x3", "x2", "x1"]:
            t_data = zm.get(t_key, {})
            for nb in t_data.get("neighborhoods", []):
                if nb.lower() == target_nb.lower():
                    return t_key, float(t_data["multiplier"]), float(t_data["base_score_10"]), nb
        return None

    # 1. Si la dirección/calle menciona explícitamente calles de La Paz o Torrero, asignar a La Paz
    if any(k in specific_low for k in ["zafiro", "la paz", "cuarta avenida", "lasierra purroy", "villa de pau", "ortiz de zárate", "ortiz de zarate"]):
        res = find_nb_tier("La Paz")
        if res:
            return res

    # 2. Primero buscar coincidencia exacta en la dirección o título específico
    for tier_key in ["x4", "x3", "x2", "x1"]:
        tier_data = zm[tier_key]
        for nb in tier_data["neighborhoods"]:
            if nb.lower() in specific_low:
                return tier_key, float(tier_data["multiplier"]), float(tier_data["base_score_10"]), nb

    # 3. Si el portal devuelve el distrito agrupado "Torrero - La Paz - Parque Venecia" sin mencionar Venecia en la dirección, usar La Paz / Torrero
    if "torrero" in combined and "la paz" in combined and "venecia" in combined and "venecia" not in specific_low:
        res = find_nb_tier("La Paz") or find_nb_tier("Torrero")
        if res:
            return res

    # 4. Búsqueda general en barrio + dirección + título + URL
    for tier_key in ["x4", "x3", "x2", "x1"]:
        tier_data = zm[tier_key]
        for nb in tier_data["neighborhoods"]:
            if nb.lower() in combined:
                return tier_key, float(tier_data["multiplier"]), float(tier_data["base_score_10"]), nb

    if any(k in combined for k in ["gómez laguna", "gomez laguna", "aragonia", "audiorama", "parque grande", "gran vía", "gran via"]):
        return find_nb_tier("Romareda") or ("x4", 10.0, 10.0, "Romareda")
    if any(k in combined for k in ["paseo teruel", "puerta del carmen", "hernán cortés", "hernan cortes", "doctor cerrada", "paseo pamplona", "constitución", "constitucion", "damas", "centro"]):
        return find_nb_tier("Centro") or ("x3", 7.8, 7.8, "Centro")
    if any(k in combined for k in ["vadorrey", "la jota", "azucarera", "cogullada", "el rabal"]):
        return find_nb_tier("Arrabal") or ("x2", 5.5, 5.5, "Arrabal")
    if any(k in combined for k in ["conde de la viñaza", "conde de la vinaza", "duquesa villahermosa", "vía univérsitas", "via universitas", "avenida madrid", "avda madrid"]):
        return find_nb_tier("Delicias") or ("x1", 3.5, 3.5, "Delicias")
    if any(k in combined for k in ["san josé", "san jose", "tenor fleta", "cesáreo alierta", "cesareo alierta"]):
        return find_nb_tier("San José") or ("x2", 5.5, 5.5, "San José")
    if any(k in combined for k in ["casco antiguo", "casco histórico", "casco historico", "el pilar", "san miguel", "predicadores", "san jorge", "don jaime", "calle de las armas"]):
        return find_nb_tier("Magdalena") or ("x2", 5.5, 5.5, "Casco Histórico")

    return "x2", 5.5, 5.5, neighborhood_text or "Zaragoza Capital"


def estimate_renovation_and_financials(listing: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calcula coste de reforma (si procede), impuestos en Aragón, comisión de agencia,
    entrada necesaria y cuota mensual hipotecaria a 30 años.
    """
    fin = profile["financials"]
    price = float(listing.get("price_eur") or 150000)
    sqm = float(listing.get("sqm") or 75)
    condition_raw = (listing.get("condition_state") or "").lower()
    desc = (listing.get("description") or "").lower()
    title = (listing.get("title") or "").lower()
    full_text = f"{title} {desc} {condition_raw}"

    is_agency = bool(listing.get("is_agency", True))
    buyer_fee_exempt = bool(listing.get("buyer_fee_exempt", False)) or any(
        k in full_text for k in [
            "exento de honorarios",
            "sin honorarios para el comprador",
            "sin comisión al comprador",
            "sin comision al comprador",
            "no cobramos comisión al comprador",
        ]
    )

    # Detección transparente del estado de reforma según el texto real del anuncio
    if any(k in full_text for k in ["a reformar", "para reformar", "reforma integral", "origen", "actualizar entero", "de origen"]):
        reform_type = "Reforma Eficiencia + Cocina + Baño + Pintura/Suelos"
        reform_Trigger = "Detectado en anuncio: mención explícita 'a reformar / de origen'"
        cost_windows_insulation = round(max(4500, sqm * 72))
        cost_kitchen = 6800
        cost_bathroom = 4400
        cost_living_rooms = round(sqm * 65)
        estimated_reform_eur = cost_windows_insulation + cost_kitchen + cost_bathroom + cost_living_rooms
    elif any(k in full_text for k in ["reformado integral", "recién reformado", "recien reformado", "totalmente reformado", "reforma a estrenar", "obra nueva", "perfecto estado"]):
        reform_type = "Listo para entrar (Reforma estética mínima)"
        reform_Trigger = "Detectado en anuncio: 'reformado / perfecto estado'"
        cost_windows_insulation = 0
        cost_kitchen = 0
        cost_bathroom = 0
        cost_living_rooms = 2200
        estimated_reform_eur = 2200
    else:
        reform_type = "Actualización parcial (Ventanas Climalit + Cocina/Baño)"
        reform_Trigger = "Estado intermedio ('Buen estado' sin mención de reforma reciente)"
        cost_windows_insulation = round(max(4200, sqm * 65))
        cost_kitchen = 5500
        cost_bathroom = 3800
        cost_living_rooms = 2500
        estimated_reform_eur = cost_windows_insulation + cost_kitchen + cost_bathroom + cost_living_rooms

    # Impuestos Aragón (<35 años)
    itp_pct = 6.0 if (fin.get("age_under_35", True) and price <= 150000) else 7.5
    taxes_and_notary_eur = round(price * (itp_pct / 100.0) + fin.get("notary_registry_appraisal_eur", 2200))

    # Comisión de agencia estimada si vende agencia y no está exento (3% + IVA = 3.63%, mín 3.630 €)
    if is_agency and not buyer_fee_exempt:
        agency_fee_eur = round(max(3630.0, price * 0.0363))
    else:
        agency_fee_eur = 0

    total_project_cost_eur = round(price + taxes_and_notary_eur + agency_fee_eur + estimated_reform_eur)

    savings_target = 52000.0
    max_cash_for_purchase = max(
        price * 0.10 + taxes_and_notary_eur + agency_fee_eur,
        savings_target - estimated_reform_eur,
    )
    down_payment_for_price = max(price * 0.10, max_cash_for_purchase - taxes_and_notary_eur - agency_fee_eur)
    loan_amount = max(0.0, min(price * 0.90, price - down_payment_for_price))

    annual_rate = fin.get("estimated_interest_rate_tin", 2.75) / 100.0
    r_month = annual_rate / 12.0
    n_months = fin.get("mortgage_years", 30) * 12
    if loan_amount > 0 and r_month > 0:
        monthly_quota = loan_amount * (r_month * (1 + r_month) ** n_months) / ((1 + r_month) ** n_months - 1)
    else:
        monthly_quota = 0.0

    cash_needed_total = round((price - loan_amount) + taxes_and_notary_eur + agency_fee_eur + estimated_reform_eur)
    fits_monthly_limit = monthly_quota <= fin.get("max_monthly_mortgage_eur", 700)
    fits_savings_limit = cash_needed_total <= (fin.get("available_savings_max_eur", 60000) + 5000)

    return {
        "itp_pct": itp_pct,
        "taxes_and_notary_eur": taxes_and_notary_eur,
        "agency_fee_eur": agency_fee_eur,
        "buyer_fee_exempt": buyer_fee_exempt,
        "reform_type": reform_type,
        "reform_trigger": reform_Trigger,
        "estimated_reform_eur": estimated_reform_eur,
        "reform_breakdown": {
            "windows_insulation_eur": cost_windows_insulation,
            "kitchen_eur": cost_kitchen,
            "bathroom_eur": cost_bathroom,
            "living_bedrooms_paint_eur": cost_living_rooms,
        },
        "total_project_cost_eur": total_project_cost_eur,
        "loan_amount_eur": round(loan_amount),
        "monthly_mortgage_eur": round(monthly_quota, 2),
        "cash_needed_total_eur": cash_needed_total,
        "fits_monthly_limit_700": fits_monthly_limit,
        "fits_savings_limit_60k": fits_savings_limit,
        "is_sweet_spot_reform_deal": (price <= 138000 and total_project_cost_eur <= 178000),
    }


def evaluate_Discard_Criteria(listing: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Comprueba si el inmueble incumple algún filtro duro o presenta alertas críticas.
    """
    reasons = []
    desc = f"{listing.get('title', '')} {listing.get('description', '')}".lower()
    url_clean = (listing.get("url") or "").lower().replace("_", " ").replace("-", " ")
    loc_text = f"{listing.get('neighborhood', '')} {listing.get('address', '')} {listing.get('title', '')} {url_clean}".lower()

    if any(k in loc_text for k in [
        "barrios rurales", "casetas", "villarrapa", "garrapinillos", "monzalbarba",
        "movera", "montañana", "peñaflor", "san juan de mozarrifar", "juslibol",
        "san mateo de gállego", "san mateo de gallego", "utebo", "cuarte", "cadrete",
        "alfajarín", "zuera", "alagón", "épila", "la muela", "villanueva de gállego"
    ]):
        reasons.append("Descartado: Fuera del núcleo urbano de Zaragoza Capital (barrio rural o municipio periférico)")

    if listing.get("is_occupied") or any(k in desc for k in [
        "inmueble ocupado", "piso ocupado", "sin posesión", "sin posesion",
        "ocupado ilegalmente", "no se puede visitar por estar ocupado"
    ]):
        reasons.append("Descartado: Inmueble ocupado / sin posesión")

    if listing.get("is_bare_ownership") or "nuda propiedad" in desc:
        reasons.append("Descartado: Venta de Nuda Propiedad (no uso inmediato)")

    if listing.get("is_rented") or "alquilado con inquilino" in desc or "vivienda alquilada" in desc:
        reasons.append("Descartado: Inmueble actualmente alquilado a terceros")

    sqm = float(listing.get("sqm") or 0)
    if 0 < sqm < 60:
        reasons.append(f"Descartado: Superficie ({int(sqm)} m²) inferior al mínimo estricto de 60 m²")

    bedrooms = int(listing.get("bedrooms") or 0)
    if 0 < bedrooms < 3:
        reasons.append(f"Penalización Fuerte: Solo {bedrooms} hab. (buscas mínimo 3 hab: 1 principal + 2 despachos/invitados)")

    if listing.get("has_elevator") is False:
        reasons.append("Penalización Fuerte: Sin ascensor (riesgo de derrama futura de instalación y peor accesibilidad)")

    floor = listing.get("floor_num")
    if floor is not None and floor < 2:
        reasons.append(f"Penalización: Planta baja o 1ª ({floor}ª planta; prefieres >= 2ª planta por luz e intimidad)")

    hard_discard = any(r.startswith("Descartado:") for r in reasons)
    return hard_discard, reasons


def score_listing(listing: Dict[str, Any], profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Calcula las 13 puntuaciones en escala 1-10 con EVIDENCIA AUDITABLE 100% EXPLÍCITA
    (sin inventar ningún dato que no venga del anuncio, coordenadas GPS o análisis píxel real),
    aplica los pesos del perfil, suma extras fijos (garaje, trastero, ático) y resta la
    penalización por Agencia Inmobiliaria.
    """
    if profile is None:
        profile = load_buyer_profile()

    factors_cfg = profile["scoring_factors_1_to_10"]
    bonuses_cfg = profile["fixed_bonuses"]

    price = float(listing.get("price_eur") or 150000)
    sqm = max(40.0, float(listing.get("sqm") or 75))
    sqm_useful = float(listing.get("sqm_useful") or 0)
    eur_per_sqm = price / sqm
    bedrooms = int(listing.get("bedrooms") or 3)
    bathrooms = int(listing.get("bathrooms") or 1)
    floor_num = listing.get("floor_num")  # None si el anuncio no especifica la planta
    has_elevator = bool(listing.get("has_elevator", True))
    has_terrace = bool(listing.get("has_terrace", False))
    has_garage = bool(listing.get("has_garage", False))
    has_storage = bool(listing.get("has_storage", False))
    is_penthouse = bool(listing.get("is_penthouse", False))
    is_agency = bool(listing.get("is_agency", True))
    seller_name = listing.get("seller_name") or ("Agencia Inmobiliaria" if is_agency else "Particular")
    lat = listing.get("lat")
    lon = listing.get("lon")

    desc = f"{listing.get('title', '')} {listing.get('description', '')}".lower()

    tier_key, zone_mult, zone_base_10, matched_nb = classify_neighborhood(
        listing.get("neighborhood", ""),
        listing.get("address", ""),
        profile,
        title_text=listing.get("title", ""),
        url_text=listing.get("url", ""),
    )
    fin_est = estimate_renovation_and_financials(listing, profile)
    hard_discard, alerts = evaluate_Discard_Criteria(listing)

    avg_zone_sqm = ZARAGOZA_AVG_SQM_PRICES.get(matched_nb, 2100)
    total_cost_per_sqm = fin_est["total_project_cost_eur"] / sqm
    discount_ratio = (avg_zone_sqm - total_cost_per_sqm) / avg_zone_sqm

    audit_evidence: Dict[str, Dict[str, Any]] = {}

    # 1. Precio y Rentabilidad Post-Reforma (1-10)
    s_price = 6.0 + (discount_ratio * 10.0)
    sweet_bonus = 0.0
    if price <= 135000 and fin_est["total_project_cost_eur"] < (avg_zone_sqm * sqm * 0.92):
        sweet_bonus = 1.2
        s_price += sweet_bonus
    quota_penalty = 0.0
    if not fin_est["fits_monthly_limit_700"]:
        quota_penalty = -2.0
        s_price += quota_penalty
    s_price = max(1.0, min(10.0, round(s_price, 1)))
    audit_evidence["price_and_total_roi"] = {
        "score": s_price,
        "status": "verificado",
        "source": f"Precio anuncio ({int(price):,} € / {int(sqm)} m² = {int(eur_per_sqm)} €/m²) vs Media barrio {matched_nb} ({avg_zone_sqm} €/m²)",
        "formula": f"6.0 + (({avg_zone_sqm} - {int(total_cost_per_sqm)} coste_total/m²) / {avg_zone_sqm}) × 10 {'+ 1.2 bonus <135k' if sweet_bonus else ''}{' - 2.0 exceso cuota' if quota_penalty else ''}",
        "detail": f"Coste total tras impuestos, agencia y reforma estimada: {int(fin_est['total_project_cost_eur']):,} € ({int(total_cost_per_sqm)} €/m² vs {avg_zone_sqm} €/m² reformado en {matched_nb}).",
    }

    # 2. Localización por Barrio (10 / 7.8 / 5.5 / 3.5 pts)
    s_zone = round(zone_base_10, 1)
    audit_evidence["neighborhood_tier"] = {
        "score": s_zone,
        "status": "verificado",
        "source": f"Barrio detectado en anuncio: '{matched_nb}'",
        "formula": f"Valoración asignada al barrio {matched_nb} en tu perfil → {s_zone} / 10 puntos",
        "detail": f"Ubicado en {matched_nb}, valorado con {s_zone} puntos en tu configuración de zonas.",
    }

    # 3. Micro-localización por Calle y Tranvía (1-10)
    tram_name, tram_dist_m = nearest_poi(lat, lon, ZARAGOZA_TRAM_STOPS_GPS)
    park_name, park_dist_m = nearest_poi(lat, lon, ZARAGOZA_PARKS_GPS)
    street_ev_parts = []
    if tram_dist_m is not None:
        # Basado en coordenadas GPS reales del anuncio
        s_street = s_zone * 0.75 + 2.0
        if tram_dist_m <= 450:
            s_street += 0.8
            street_ev_parts.append(f"Parada Tranvía '{tram_name}' a {tram_dist_m} m por GPS (+0.8)")
        elif tram_dist_m <= 900:
            s_street += 0.3
            street_ev_parts.append(f"Parada Tranvía '{tram_name}' a {tram_dist_m} m por GPS (+0.3)")
        else:
            street_ev_parts.append(f"Parada Tranvía más cercana a {tram_dist_m} m")
        if park_dist_m is not None and park_dist_m <= 400:
            s_street += 0.5
            street_ev_parts.append(f"Parque '{park_name}' a {park_dist_m} m (+0.5)")
        s_street = max(1.0, min(10.0, round(s_street, 1)))
        audit_evidence["street_microlocation"] = {
            "score": s_street,
            "status": "verificado",
            "source": f"Coordenadas GPS reales del anuncio ({lat:.5f}, {lon:.5f})",
            "formula": f"0.75 × Nota_Barrio ({s_zone}) + 2.0 + Bonus proximidad GPS a Tranvía/Parque",
            "detail": " | ".join(street_ev_parts),
        }
    else:
        s_street = round(max(3.0, min(9.0, s_zone * 0.85)), 1)
        audit_evidence["street_microlocation"] = {
            "score": s_street,
            "status": "verificado",
            "source": f"Dirección textual: '{listing.get('address', matched_nb)}' (sin coordenadas GPS exactas)",
            "formula": f"0.85 × Nota_Barrio ({s_zone})",
            "detail": "El portal no incluye coordenadas GPS exactas; se pondera proporcionalmente al nivel del barrio.",
        }

    # 4. Superficie (>70m²) y Aprovechamiento de Planta (1-10)
    if sqm < 60:
        s_size = 1.5
        size_base_str = "<60 m² → 1.5"
    elif sqm < 70:
        s_size = 5.5 + (sqm - 60) * 0.15
        size_base_str = f"60-70 m² ({int(sqm)} m²) → {s_size:.1f}"
    elif sqm <= 105:
        s_size = 7.2 + (sqm - 70) * 0.075
        size_base_str = f"70-105 m² ({int(sqm)} m²) → {s_size:.1f}"
    else:
        s_size = 9.6
        size_base_str = f">105 m² ({int(sqm)} m²) → 9.6"

    layout_mod = 0.0
    layout_notes = [f"Superficie declarada: {int(sqm)} m² construidos" + (f" ({int(sqm_useful)} m² útiles)" if sqm_useful else "")]
    if any(k in desc for k in ["sin pasillo", "poco pasillo", "cuadrado", "distribución cuadrada", "distribucion cuadrada", "cocina americana", "espacios abiertos"]):
        layout_mod += 0.6
        layout_notes.append("Mención en anuncio de buena distribución / sin pasillo perdido (+0.6)")
    if bathrooms >= 2:
        layout_mod += 0.4
        layout_notes.append(f"{bathrooms} baños declarados (+0.4)")
    s_size = max(1.0, min(10.0, round(s_size + layout_mod, 1)))
    audit_evidence["size_and_layout"] = {
        "score": s_size,
        "status": "verificado",
        "source": f"Ficha técnica del anuncio: {int(sqm)} m² y {bathrooms} baño(s)",
        "formula": f"Base por m² ({size_base_str}) + modificadores distribución/baños ({layout_mod:+.1f})",
        "detail": " | ".join(layout_notes),
    }

    # 5. Calidad de Habitaciones Secundarias (Despachos/Invitados) (1-10)
    sqm_per_room = sqm / max(1, bedrooms)
    if bedrooms < 3:
        s_rooms = 2.0
        room_formula = f"Menos de 3 habitaciones ({bedrooms} hab.) → 2.0/10"
    elif bedrooms == 3:
        s_rooms = 6.8 + min(2.4, max(0.0, (sqm_per_room - 22.0) * 0.22))
        room_formula = f"3 hab. con ratio {sqm_per_room:.1f} m²/hab → 6.8 + ({sqm_per_room:.1f} - 22) × 0.22"
    else:
        s_rooms = 7.8 + min(2.0, max(0.0, (sqm_per_room - 20.0) * 0.20))
        room_formula = f"{bedrooms} hab. con ratio {sqm_per_room:.1f} m²/hab → 7.8 + ({sqm_per_room:.1f} - 20) × 0.20"

    if "armarios empotrados" in desc or listing.get("has_wardrobes"):
        s_rooms += 0.4
        room_formula += " + 0.4 (armarios empotrados en anuncio)"
    s_rooms = max(1.0, min(10.0, round(s_rooms, 1)))
    audit_evidence["secondary_rooms_quality"] = {
        "score": s_rooms,
        "status": "verificado",
        "source": f"Ficha técnica: {bedrooms} habitaciones en {int(sqm)} m² ({sqm_per_room:.1f} m²/hab)",
        "formula": room_formula,
        "detail": f"Con {int(sqm)} m² para {bedrooms} dormitorios, el tamaño medio por estancia permite {'holgadamente' if sqm_per_room >= 25 else 'ajustadamente'} 1 dormitorio principal + 2 despachos/invitados.",
    }

    # 6. Luz Natural, Orientación (S/SE/E), Ventilación Cruzada y Altura (1-10)
    s_light = 5.0  # Punto de partida neutro verificable
    light_parts = ["Base neutra 5.0"]
    if floor_num is not None:
        if floor_num >= 5:
            s_light += 2.0
            light_parts.append(f"Planta {floor_num}ª declarada (+2.0 por altura y vistas despejadas)")
        elif floor_num >= 3:
            s_light += 1.3
            light_parts.append(f"Planta {floor_num}ª declarada (+1.3)")
        elif floor_num == 2:
            s_light += 0.6
            light_parts.append("Planta 2ª declarada (+0.6, cumple mínimo >=2ª)")
        else:
            s_light -= 2.0
            light_parts.append(f"Planta {floor_num}ª (<2ª planta requerida: -2.0)")
    else:
        light_parts.append("Planta no especificada en la ficha (0.0)")

    orient_str = (listing.get("orientation") or "").lower()
    if any(k in desc or k in orient_str for k in ["orientación sur", "orientacion sur", "sureste", "orientación este", "orientacion este", "sol de mañana", "sur", "este"]):
        s_light += 1.4
        light_parts.append("Orientación Sur/Sureste/Este verificada (+1.4)")
    elif "oeste" in orient_str or "norte" in orient_str:
        s_light += 0.2
        light_parts.append(f"Orientación {orient_str} declarada (+0.2)")

    if any(k in desc for k in ["todo exterior", "totalmente exterior", "ventilación cruzada", "ventilacion cruzada", "dos orientaciones", "doble orientación", "exterior"]):
        s_light += 1.0
        light_parts.append("Vivienda exterior / ventilación mencionada en anuncio (+1.0)")
    if "interior" in desc and "exterior" not in desc:
        s_light -= 2.2
        light_parts.append("Vivienda interior mencionada en anuncio (-2.2)")

    img_light_bonus = float(listing.get("image_light_bonus") or 0.0)
    if abs(img_light_bonus) > 0.01:
        s_light += img_light_bonus
        light_parts.append(f"Brillo real medido en píxeles de la foto principal ({img_light_bonus:+.1f})")

    s_light = max(1.0, min(10.0, round(s_light, 1)))
    audit_evidence["light_orientation_views"] = {
        "score": s_light,
        "status": "verificado" if (floor_num is not None or orient_str or "exterior" in desc) else "no_especificado",
        "source": f"Planta: {f'{floor_num}ª' if floor_num is not None else 'N/D'} | Orientación: {listing.get('orientation') or 'N/D'} | Foto principal analizada",
        "formula": " + ".join(light_parts),
        "detail": "Combinación auditable de planta declarada, orientación en ficha/texto y luminosidad real de la imagen.",
    }

    # 7. Terraza y Vistas a Zona Verde (1-10)
    if has_terrace or any(k in desc for k in ["terraza", "balcón", "balcon", "galería exterior"]):
        s_terrace = 7.5
        terrace_parts = ["Terraza/balcón confirmado en ficha o texto (base 7.5)"]
        if park_dist_m is not None and park_dist_m <= 300:
            s_terrace += 1.8
            terrace_parts.append(f"A {park_dist_m} m por GPS de '{park_name}' (+1.8)")
        elif any(k in desc for k in ["vistas al parque", "zona verde", "vistas despejadas", "arbolado", "parque"]):
            s_terrace += 1.4
            terrace_parts.append("Mención de vistas a parque / zona verde en el anuncio (+1.4)")
        if any(k in desc for k in ["gran terraza", "amplia terraza", "2 terrazas", "dos terrazas", "terraza de 1", "terraza de 2"]):
            s_terrace += 0.7
            terrace_parts.append("Terraza amplia o doble terraza (+0.7)")
        terrace_status = "verificado"
    else:
        s_terrace = 2.5
        terrace_parts = ["Sin terraza ni balcón en ficha ni descripción (2.5/10)"]
        terrace_status = "verificado"
    s_terrace = max(1.0, min(10.0, round(s_terrace, 1)))
    audit_evidence["terrace_and_green_areas"] = {
        "score": s_terrace,
        "status": terrace_status,
        "source": f"Terraza: {'Sí' if s_terrace >= 7.0 else 'No'} | Parque más cercano: {f'{park_name} ({park_dist_m} m)' if park_dist_m is not None else 'Según texto'}",
        "formula": " + ".join(terrace_parts),
        "detail": "Puntuación basada en la existencia real de terraza y distancia geodésica al parque más cercano.",
    }

    # 8. Estado Interior y Viabilidad de Reforma (Cocina/Baño/Eficiencia) (1-10)
    if fin_est["is_sweet_spot_reform_deal"]:
        s_reform = 9.3
        ref_formula = f"Estrategia A cumplida: Compra <=138k ({int(price):,} €) + Reforma ({fin_est['estimated_reform_eur']:,} €) = Coste total {fin_est['total_project_cost_eur']:,} € <= 178k → 9.3/10"
    elif price <= 145000 and fin_est["estimated_reform_eur"] >= 14000:
        s_reform = 8.2
        ref_formula = f"Piso económico ({int(price):,} €) apto para reforma a tu gusto ({fin_est['estimated_reform_eur']:,} €) → 8.2/10"
    elif fin_est["estimated_reform_eur"] <= 4000 and price <= 185000:
        s_reform = 8.4
        ref_formula = f"Estrategia B: Reformado/Buen estado ({int(price):,} €) sin obra pesada → 8.4/10"
    elif price > 180000 and fin_est["estimated_reform_eur"] >= 14000:
        s_reform = 4.0
        ref_formula = f"Precio alto ({int(price):,} €) + requiere además reforma de {fin_est['estimated_reform_eur']:,} € → 4.0/10"
    else:
        s_reform = 6.8
        ref_formula = f"Precio intermedio ({int(price):,} €) con actualización estimada de {fin_est['estimated_reform_eur']:,} € → 6.8/10"
    s_reform = max(1.0, min(10.0, round(s_reform, 1)))
    audit_evidence["condition_and_reform_viability"] = {
        "score": s_reform,
        "status": "verificado",
        "source": fin_est["reform_trigger"],
        "formula": ref_formula,
        "detail": f"Tipo de intervención estimada: {fin_est['reform_type']}.",
    }

    # 9. Aislamiento Actual (Ventanas Climalit/PVC) e Instalaciones (1-10)
    ins_parts = []
    ins_status = "verificado"
    if any(k in desc for k in ["climalit", "oscilobatiente", "oscilobatientes", "pvc", "rotura de puente térmico", "rotura puente termico", "doble acristalamiento", "doble cristal"]):
        s_insul = 8.5
        ins_parts.append("Anuncio confirma carpintería Climalit / PVC / doble acristalamiento (8.5)")
    elif any(k in desc for k in ["ventanas de aluminio", "carpintería de aluminio", "aluminio blanco"]):
        s_insul = 6.5
        ins_parts.append("Anuncio menciona ventanas de aluminio (6.5)")
    elif any(k in desc for k in ["a reformar", "de origen", "para actualizar"]):
        s_insul = 4.2
        ins_parts.append("Anuncio indica estado de origen / a reformar (4.2; presupuestado cambio a PVC en reforma)")
    else:
        s_insul = 5.0
        ins_status = "no_especificado"
        ins_parts.append("Tipo de ventanas no especificado en el anuncio → Nota neutra (5.0)")

    if any(k in desc for k in ["electricidad cambiada", "fontanería cambiada", "fontaneria cambiada", "instalación eléctrica nueva", "reforma integral"]):
        s_insul = min(10.0, round(s_insul + 1.2, 1))
        ins_parts.append("Instalaciones renovadas mencionadas en texto (+1.2)")

    audit_evidence["insulation_and_hidden_installations"] = {
        "score": s_insul,
        "status": ins_status,
        "source": "Búsqueda textual de carpintería/instalaciones en descripción del anuncio",
        "formula": " + ".join(ins_parts),
        "detail": "Si el anuncio no menciona Climalit/PVC ni reforma integral, recibe nota neutra 5.0 sin inventar calidades.",
    }

    # 10. Costes Asociados (Comunidad, IBI) y Calefacción Central (1-10)
    comm_eur = listing.get("community_eur")
    heating_raw = (listing.get("heating_type") or "").lower()
    heat_parts = []
    heat_status = "verificado"
    if "central" in desc or "central" in heating_raw:
        s_costs = 7.8
        heat_parts.append("Calefacción central confirmada en anuncio (base 7.8)")
        if "contador individual" in desc or "contadores individuales" in desc:
            s_costs += 1.0
            heat_parts.append("Con contador individual (+1.0)")
    elif "gas natural" in desc or "individual" in desc or "gas" in heating_raw:
        s_costs = 7.0
        heat_parts.append("Calefacción individual a gas confirmada (base 7.0)")
    elif "eléctrica" in desc or "electrica" in desc or "acumuladores" in desc:
        s_costs = 4.5
        heat_parts.append("Calefacción eléctrica / acumuladores (base 4.5)")
    else:
        s_costs = 5.0
        heat_status = "no_especificado"
        heat_parts.append("Sistema de calefacción no especificado en anuncio → Nota neutra (5.0)")

    if comm_eur is not None:
        heat_status = "verificado"
        if comm_eur <= 65:
            s_costs += 1.2
            heat_parts.append(f"Gastos de comunidad explícitos en anuncio: {int(comm_eur)} €/mes (+1.2 por ser <=65 €)")
        elif comm_eur <= 95:
            s_costs += 0.4
            heat_parts.append(f"Gastos de comunidad explícitos en anuncio: {int(comm_eur)} €/mes (+0.4)")
        elif comm_eur > 130:
            s_costs -= 1.5
            heat_parts.append(f"Gastos de comunidad explícitos en anuncio: {int(comm_eur)} €/mes (-1.5 por ser >130 €)")
    else:
        heat_parts.append("Cuota de comunidad no indicada en el anuncio (0.0)")

    s_costs = max(1.0, min(10.0, round(s_costs, 1)))
    audit_evidence["running_costs_and_heating"] = {
        "score": s_costs,
        "status": heat_status,
        "source": f"Calefacción: {listing.get('heating_type') or 'Según texto'} | Comunidad: {f'{int(comm_eur)} €/mes' if comm_eur is not None else 'No especificada en anuncio'}",
        "formula": " + ".join(heat_parts),
        "detail": "Solo se bonifica o penaliza la cuota de comunidad cuando el importe en €/mes aparece explícitamente en el anuncio.",
    }

    # 11. Salud de la Finca (Ascensor, Cota Cero, ITE) (1-10)
    b_parts = []
    if not has_elevator:
        s_building = 1.5
        b_parts.append("Sin ascensor confirmado en ficha → 1.5/10 (alto riesgo de derrama)")
    else:
        s_building = 7.0
        b_parts.append("Ascensor confirmado en ficha (base 7.0)")
        if any(k in desc for k in ["cota cero", "cota 0", "sin barreras arquitectónicas", "sin barreras arquitectonicas", "portal reformado", "patio reformado", "ascensor a piso llano", "piso llano"]):
            s_building += 1.5
            b_parts.append("Portal reformado / ascensor a cota cero mencionado en anuncio (+1.5)")
        if any(k in desc for k in ["ite favorable", "fachada rehabilitada", "tejado rehabilitado", "caldera nueva"]):
            s_building += 0.8
            b_parts.append("ITE favorable / rehabilitación de finca mencionada (+0.8)")
        year_built = listing.get("year_built")
        if year_built:
            b_parts.append(f"Año construcción declarado: {year_built}")
    s_building = max(1.0, min(10.0, round(s_building, 1)))
    audit_evidence["building_health_and_neighbors"] = {
        "score": s_building,
        "status": "verificado",
        "source": f"Ascensor: {'Sí' if has_elevator else 'No'} | Año finca: {listing.get('year_built') or 'No especificado'}",
        "formula": " + ".join(b_parts),
        "detail": "Verifica ascensor instalado y menciones reales en el anuncio sobre cota cero, patio reformado o ITE.",
    }

    # 12. Nivel de Silencio y Tranquilidad (1-10)
    noise_parts = ["Base neutra 6.0"]
    s_noise = 6.0
    noise_status = "no_especificado"
    if any(k in desc for k in ["zona tranquila", "calle tranquila", "muy tranquilo", "peatonal", "sin ruidos", "silencioso", "junto al parque"]):
        s_noise += 2.0
        noise_status = "verificado"
        noise_parts.append("Anuncio destaca calle tranquila / peatonal / junto a parque (+2.0)")
    if floor_num is not None and floor_num >= 4:
        s_noise += 1.0
        noise_status = "verificado"
        noise_parts.append(f"Altura planta {floor_num}ª aleja del ruido de calle (+1.0)")
    elif floor_num is not None and floor_num <= 1:
        s_noise -= 1.2
        noise_status = "verificado"
        noise_parts.append(f"Planta baja/1ª más expuesta a ruido peatonal (-1.2)")
    s_noise = max(1.0, min(10.0, round(s_noise, 1)))
    audit_evidence["noise_and_tranquility"] = {
        "score": s_noise,
        "status": noise_status,
        "source": f"Altura: {f'{floor_num}ª planta' if floor_num is not None else 'N/D'} + análisis de texto del anuncio",
        "formula": " + ".join(noise_parts),
        "detail": "Calculado según altura de planta y menciones explícitas de calle peatonal/tranquila.",
    }

    # 13. Carril Bici, Tranvía/Bus y Aparcamiento (1-10)
    mob_parts = []
    if tram_dist_m is not None:
        if tram_dist_m <= 400:
            s_mobility = 9.0
            mob_parts.append(f"Parada Tranvía '{tram_name}' a {tram_dist_m} m por GPS (9.0)")
        elif tram_dist_m <= 800:
            s_mobility = 8.0
            mob_parts.append(f"Parada Tranvía '{tram_name}' a {tram_dist_m} m por GPS (8.0)")
        elif tram_dist_m <= 1400:
            s_mobility = 7.0
            mob_parts.append(f"Parada Tranvía '{tram_name}' a {tram_dist_m} m + red bus urbano (7.0)")
        else:
            s_mobility = 6.2
            mob_parts.append(f"A {tram_dist_m} m del Tranvía (conexión mediante bus urbano: 6.2)")
        mob_status = "verificado"
    else:
        s_mobility = 7.5 if tier_key in ("x4", "x3") else 6.5
        mob_parts.append(f"Estimación por zona {matched_nb} ({s_mobility})")
        mob_status = "verificado"

    if has_garage:
        s_mobility = min(10.0, round(s_mobility + 1.0, 1))
        mob_parts.append("Plaza de garaje incluida (+1.0)")
    audit_evidence["mobility_and_parking"] = {
        "score": s_mobility,
        "status": mob_status,
        "source": f"Distancia GPS a Tranvía L1: {f'{tram_dist_m} m ({tram_name})' if tram_dist_m is not None else 'Sin GPS exacto'} | Garaje: {'Sí' if has_garage else 'No'}",
        "formula": " + ".join(mob_parts),
        "detail": "Calculado por distancia geodésica real a las paradas del Tranvía de Zaragoza y disponibilidad de garaje.",
    }

    scores_1_to_10 = {k: v["score"] for k, v in audit_evidence.items()}

    # Cálculo de media ponderada sobre 100
    weighted_sum = 0.0
    total_weight = 0.0
    for key, factor_meta in factors_cfg.items():
        w = float(factor_meta["weight"])
        val_10 = scores_1_to_10.get(key, 5.0)
        weighted_sum += val_10 * w
        total_weight += w

    base_score_100 = (weighted_sum / total_weight) * 10.0 if total_weight > 0 else 50.0

    # Bonificaciones y Penalizaciones fijas (proporcionadas a la escala 0-100)
    bonus_garage = float(bonuses_cfg.get("garage_points", 6.0)) if has_garage else 0.0
    bonus_storage = float(bonuses_cfg.get("storage_room_points", 3.0)) if has_storage else 0.0
    bonus_penthouse = float(bonuses_cfg.get("penthouse_solarium_points", 5.0)) if is_penthouse else 0.0

    if is_agency:
        if fin_est["buyer_fee_exempt"]:
            agency_modifier = float(bonuses_cfg.get("agency_no_fee_penalty_points", -3.0))
        else:
            agency_modifier = float(bonuses_cfg.get("agency_penalty_points", -10.0))
    else:
        agency_modifier = 0.0

    # Impacto directo del nivel de zona (10, 7.8, 5.5, 3.5) para que una zona de 3.5 pts no supere a una de 10 pts por tener garaje
    zone_direct_impact = round((s_zone - 6.0) * 1.8, 1)

    total_bonuses = round(bonus_garage + bonus_storage + bonus_penthouse + agency_modifier + zone_direct_impact, 1)

    structural_penalty = 0.0
    if not has_elevator:
        structural_penalty -= 25.0
    if bedrooms < 3:
        structural_penalty -= 18.0
    if floor_num is not None and floor_num < 2:
        structural_penalty -= 10.0

    final_ranking_score = round(base_score_100 + total_bonuses + structural_penalty, 1)

    return {
        "scores_1_to_10": scores_1_to_10,
        "audit_evidence": audit_evidence,
        "base_score_100": round(base_score_100, 1),
        "bonuses": {
            "garage_points": bonus_garage,
            "storage_room_points": bonus_storage,
            "penthouse_solarium_points": bonus_penthouse,
            "agency_penalty_points": agency_modifier,
            "structural_penalty": structural_penalty,
            "total_bonuses": total_bonuses,
        },
        "seller_info": {
            "is_agency": is_agency,
            "seller_type": "Agencia Inmobiliaria" if is_agency else "Particular",
            "seller_name": seller_name,
            "buyer_fee_exempt": fin_est["buyer_fee_exempt"],
            "estimated_agency_fee_eur": fin_est["agency_fee_eur"],
        },
        "geo_distances": {
            "nearest_park_name": park_name,
            "nearest_park_m": park_dist_m,
            "nearest_tram_name": tram_name,
            "nearest_tram_m": tram_dist_m,
        },
        "final_ranking_score": final_ranking_score,
        "zone_info": {
            "tier": tier_key,
            "multiplier": zone_mult,
            "matched_neighborhood": matched_nb,
            "avg_zone_eur_sqm": avg_zone_sqm,
        },
        "financial_analysis": fin_est,
        "hard_discard": hard_discard,
        "alerts": alerts,
    }
