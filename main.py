import os
import requests
import datetime
import pytz
import re
import json
from bs4 import BeautifulSoup
from openai import OpenAI
import tweepy

# Zona horaria de referencia
TZ = "Europe/Madrid"

# Hashtags fijos SOLO para el tuit titular
DEFAULT_HASHTAGS = ["#TalDiaComoHoy", "#España", "#HistoriaDeEspaña", "#Efemérides"]

# España / Imperio como ACTOR claro (muy valorado)
SPANISH_ACTOR_TOKENS = [
    "reyes católicos",
    "imperio español",
    "monarquía hispánica",
    "monarquía española",
    "armada española",
    "ejército español",
    "tercios",
    "tercios españoles",
    "tercios de flandes",
    "virreinato de",
    "virreinato del",
    "virreinato de nueva españa",
    "virreinato del perú",
    "virreinato del río de la plata",
    "virrey",
    "virreina",
    "corona de castilla",
    "corona de aragón",
]

# “Marca España” amplia (aquí queremos que entren muchas cosas)
SPANISH_WIDE_TOKENS = [
    "españa", "español", "española", "españoles",
    "hispania", "hispano", "hispánica",
    "reino de castilla", "reino de aragón",
    "castilla", "aragón",
    "granada", "sevilla", "toledo", "madrid",
    "cartagena", "cartagena de indias",
    "virreinato",
    "borbón", "borbones",
    "habsburgo",
    "felipe ii", "felipe iii", "felipe iv",
    "carlos v", "carlos i de españa",
    "alfonso xii", "alfonso xiii", "isabel ii",
    "partido comunista de españa",
    "radio barcelona",
]

# Teatro en suelo español (puede ser guiris dándose de hostias en nuestra costa)
SPANISH_THEATRE_TOKENS = [
    "málaga", "cádiz", "cartagena", "cartagena de indias",
    "barcelona", "valencia", "bilbao", "santander", "la coruña",
    "ceuta", "melilla", "baleares", "canarias",
]

# Palabras claramente militares
MILITARY_KEYWORDS = [
    "batalla", "guerra", "combate", "frente",
    "asedio", "sitio", "conquista", "derrota", "victoria", "alzamiento",
    "revolución", "levantamiento", "sublevación", "bombardeo", "invasión",
    "ejército", "toma", "capitulación", "ofensiva", "defensiva",
]

# Diplomacia / acuerdos / alianzas
DIPLO_KEYWORDS = [
    "tratado", "acuerdo", "paz", "alianza",
    "capitulaciones", "concordia",
]

# Nacionalidades extranjeras típicas
FOREIGN_TOKENS = [
    "alemán", "alemana", "alemania", "nazi",
    "británico", "británica", "inglés", "inglesa", "inglaterra",
    "estadounidense", "americano", "americana", "ee.uu", "eeuu",
    "francés", "francesa", "francia",
    "italiano", "italiana", "italia",
    "ruso", "rusa", "rusia",
    "soviético", "soviética", "urss",
    "japonés", "japonesa", "japón",
]

# Cosas que penalizamos (cultura/pop blanda)
CULTURE_LOW_PRIORITY = [
    "premio", "premios", "concurso", "festival", "certamen",
    "programa de radio", "programa de televisión", "radio", "televisión",
    "serie", "película", "cine", "novela", "poeta", "cantante", "músico",
    "discográfica", "disco", "álbum", "single"
]

# Claves de X (Twitter) desde los secrets del repositorio
TW_API_KEY = os.getenv("TWITTER_API_KEY", "")
TW_API_SECRET = os.getenv("TWITTER_API_SECRET", "")
TW_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN", "")
TW_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")
TW_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN", "")

USER_AGENT = "Efemerides_Imp_Bot/1.0 (https://github.com/efemeridesesp/tal-dia-como-hoy-es)"
WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"

# Cliente de OpenAI (usa OPENAI_API_KEY del entorno)
client = OpenAI()

# ID numérico de tu cuenta
TWITTER_USER_ID = "1988838626760032256"

# Fichero para almacenar hilos pendientes por 429
PENDING_FILE = "pending_tweet.json"


# ----------------- Helper para limpiar JSON con ```json ... ``` ----------------- #

def clean_json_from_markdown(raw: str) -> str:
    """
    Limpia posibles fences de Markdown tipo ```json ... ``` o ``` ... ``` y
    recorta todo lo que haya antes del primer '{' o '[' y después del último '}' o ']'.
    Deja solo el bloque JSON parseable.
    """
    if not isinstance(raw, str):
        raw = str(raw)

    s = raw.strip()

    # Si empieza con ``` algo, quitamos la primera línea y la última si también es ```
    if s.startswith("```"):
        lines = s.splitlines()
        # quitar la primera línea (``` o ```json)
        if lines:
            lines = lines[1:]
        # quitar la última si es ``` o ```algo
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()

    # Buscar el primer '{' o '['
    first_brace = s.find("{")
    first_bracket = s.find("[")
    candidates = [i for i in (first_brace, first_bracket) if i != -1]
    if candidates:
        start = min(candidates)
        s = s[start:]
    else:
        # No hay ni { ni [, devolvemos tal cual (dejará fallar a json.loads)
        return s

    # Buscar el último '}' o ']'
    last_brace = s.rfind("}")
    last_bracket = s.rfind("]")
    candidates_end = [i for i in (last_brace, last_bracket) if i != -1]
    if candidates_end:
        end = max(candidates_end) + 1
        s = s[:end]

    return s.strip()


# ----------------- Wikidata (validación determinista de fechas) ----------------- #

def search_entity_id(label: str):
    """
    Busca un QID en Wikidata a partir de un label en español.
    """
    if not label:
        return None

    params = {
        "action": "wbsearchentities",
        "search": label,
        "language": "es",
        "format": "json",
        "limit": 1,
    }

    try:
        resp = requests.get(WIKIDATA_API_URL, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"⚠️ Error buscando entidad Wikidata para '{label}': {exc}")
        return None

    results = data.get("search", [])
    if not results:
        return None

    return results[0].get("id")


def _extract_time_values(claims, prop):
    times = []
    for claim in claims.get(prop, []):
        mainsnak = claim.get("mainsnak", {})
        datavalue = mainsnak.get("datavalue")
        if not datavalue:
            continue
        value = datavalue.get("value", {})
        time_str = value.get("time")
        if time_str:
            times.append(time_str)
    return times


def fetch_dates_for_qid(qid: str):
    """
    Devuelve un dict con posibles fechas a partir de claims de Wikidata.
    """
    if not qid:
        return {}

    params = {
        "action": "wbgetentities",
        "ids": qid,
        "props": "claims",
        "format": "json",
    }

    try:
        resp = requests.get(WIKIDATA_API_URL, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"⚠️ Error consultando Wikidata para {qid}: {exc}")
        return {}

    entity = data.get("entities", {}).get(qid, {})
    claims = entity.get("claims", {})

    return {
        "P585": _extract_time_values(claims, "P585"),
        "P580": _extract_time_values(claims, "P580"),
        "P582": _extract_time_values(claims, "P582"),
        "P569": _extract_time_values(claims, "P569"),
        "P570": _extract_time_values(claims, "P570"),
    }


def normalize_ddmm(wikidata_time_str):
    """
    Convierte un time string de Wikidata a DD/MM o None si no es válido.
    """
    if not wikidata_time_str:
        return None

    match = re.match(r"^[+-]?\d{4,}-(\d{2})-(\d{2})", wikidata_time_str)
    if not match:
        return None

    month, day = match.groups()
    if month == "00" or day == "00":
        return None

    return f"{day}/{month}"


def _pick_unique_ddmm(time_values):
    ddmms = []
    for time_value in time_values:
        ddmm = normalize_ddmm(time_value)
        if ddmm:
            ddmms.append(ddmm)

    unique = sorted(set(ddmms))
    if not unique:
        return None, "sin fecha exacta en Wikidata"
    if len(unique) > 1:
        return None, "ambigüedad de fechas en Wikidata"
    return unique[0], None


def validate_candidate_with_wikidata(candidate, today_ddmm):
    """
    Valida la fecha con Wikidata. Devuelve True si coincide con today_ddmm.
    """
    entity = candidate.get("entity")
    cand_type = candidate.get("type")
    print(f"🔍 Wikidata: validando '{entity}' ({cand_type})")

    qid = search_entity_id(entity)
    if not qid:
        print("   -> Sin QID encontrado. Descartado.")
        return False

    dates = fetch_dates_for_qid(qid)

    if cand_type == "event":
        for prop in ("P585", "P580", "P582"):
            ddmm, reason = _pick_unique_ddmm(dates.get(prop, []))
            print(f"   -> {prop} ddmm: {ddmm}")
            if ddmm is None:
                if reason == "ambigüedad de fechas en Wikidata":
                    print(f"   -> Descartado: {reason}.")
                    return False
                continue
            if ddmm == today_ddmm:
                print("   -> Fecha coincide. Válido.")
                return True
            print("   -> Fecha no coincide. Descartado.")
            return False

        print("   -> Sin fecha exacta. Descartado.")
        return False

    if cand_type == "birth":
        ddmm, reason = _pick_unique_ddmm(dates.get("P569", []))
        print(f"   -> P569 ddmm: {ddmm}")
        if ddmm == today_ddmm:
            print("   -> Fecha coincide. Válido.")
            return True
        print(f"   -> Descartado: {reason or 'fecha no coincide'}.")
        return False

    if cand_type == "death":
        ddmm, reason = _pick_unique_ddmm(dates.get("P570", []))
        print(f"   -> P570 ddmm: {ddmm}")
        if ddmm == today_ddmm:
            print("   -> Fecha coincide. Válido.")
            return True
        print(f"   -> Descartado: {reason or 'fecha no coincide'}.")
        return False

    print("   -> Tipo desconocido. Descartado.")
    return False


# ----------------- Gestión de hilos pendientes ----------------- #

def load_pending_tweet():
    """Carga un hilo pendiente del fichero JSON, si existe y es válido."""
    if not os.path.exists(PENDING_FILE):
        return None
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        headline = data.get("headline")
        followups = data.get("followups", [])
        target_ddmm = data.get("target_ddmm")
        if not isinstance(headline, str) or not headline.strip():
            return None
        if not isinstance(followups, list):
            followups = []
        followups = [str(t) for t in followups]
        if not isinstance(target_ddmm, str):
            target_ddmm = None
        return {"headline": headline, "followups": followups, "target_ddmm": target_ddmm}
    except Exception as e:
        print("⚠️ Error leyendo pending_tweet.json:", e)
        return None


def save_pending_tweet(headline, followups, target_ddmm):
    """Guarda un hilo pendiente en el fichero JSON."""
    try:
        data = {
            "headline": headline,
            "followups": list(followups or []),
            "target_ddmm": target_ddmm,
            "saved_at": datetime.datetime.utcnow().isoformat() + "Z",
        }
        with open(PENDING_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("💾 Hilo guardado en pending_tweet.json para publicar más adelante.")
    except Exception as e:
        print("⚠️ No se pudo guardar el hilo pendiente:", e)


def clear_pending_tweet():
    """Elimina el fichero de hilo pendiente si existe."""
    try:
        if os.path.exists(PENDING_FILE):
            os.remove(PENDING_FILE)
            print("🧹 pending_tweet.json eliminado tras publicar el hilo pendiente.")
    except Exception as e:
        print("⚠️ No se pudo eliminar pending_tweet.json:", e)


# ----------------- Anti-repetición (timeline X) ----------------- #

def fetch_previous_events_same_day(month, day):
    """
    Lee solo los últimos tuits del usuario y detecta titulares del mismo día
    (para no repetir efemérides). Usa UNA sola llamada para evitar 429.
    Si hay rate limit (429) u otro error, devolvemos [] y no rompemos nada.
    """
    if not TW_BEARER_TOKEN:
        return []

    cli = tweepy.Client(bearer_token=TW_BEARER_TOKEN)
    search_prefix = f"🇪🇸 {day} de "
    old_texts = []

    try:
        resp = cli.get_users_tweets(
            id=TWITTER_USER_ID,
            max_results=50,
            tweet_fields=["created_at", "text"],
        )
    except tweepy.errors.TooManyRequests:
        print("⚠️ Rate limit X (429) en get_users_tweets. Se desactiva anti-repetición hoy.")
        return []
    except Exception as e:
        print("⚠️ Error consultando tuits anteriores:", e)
        return []

    if not resp.data:
        return []

    for t in resp.data:
        txt = t.text
        if search_prefix in txt:
            old_texts.append(txt.lower())

    return old_texts


def event_is_repeated(event_text, old_texts):
    """
    Comprueba si un evento ya fue tratado comparando tokens clave.
    """
    t = event_text.lower()

    key_fragments = (
        SPANISH_ACTOR_TOKENS +
        SPANISH_WIDE_TOKENS +
        MILITARY_KEYWORDS +
        DIPLO_KEYWORDS
    )

    for prev in old_texts:
        matches = 0
        for k in key_fragments:
            if k in t and k in prev:
                matches += 1
        if matches >= 2:
            return True

    return False


# ----------------- Anti-contradicciones (hilo) ----------------- #

def detect_and_fix_contradictions(headline, followups, event_text):
    """
    Detecta contradicciones internas usando modelo y reescribe los tuits conflictivos.
    """
    all_tweets = [headline] + followups

    prompt = f"""
Analiza estos tuits y detecta contradicciones internas en fechas, cifras, nombres, lugares o hechos.

EFEMÉRIDE ORIGINAL:
\"\"\"{event_text}\"\"\"

TUITS DEL HILO:
{json.dumps(all_tweets, ensure_ascii=False, indent=2)}

Tu tarea:
- Si hay contradicciones, corrige los tuits mínimos necesarios para que todo sea coherente con la efeméride original.
- Respeta el estilo, tono y longitud aproximada.

Devuelve EXCLUSIVAMENTE un JSON con la siguiente forma:
{{
  "fixed": ["tuit1", "tuit2", "..."]
}}
No añadas nada más.
"""

    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "Corrige contradicciones internas respetando el estilo original y la efeméride proporcionada."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.2,
        max_tokens=800
    )

    raw = resp.choices[0].message.content.strip()
    raw_clean = clean_json_from_markdown(raw)

    try:
        data = json.loads(raw_clean)
        fixed = data.get("fixed", [])
        if isinstance(fixed, list) and len(fixed) == len(all_tweets):
            return fixed[0], fixed[1:]
    except Exception:
        print("⚠️ No se ha podido parsear el JSON de corrección de contradicciones.")
        print("Contenido bruto devuelto por OpenAI:")
        print(raw)

    return headline, followups


# ----------------- Utilidades de fecha ----------------- #

def today_info():
    """Devuelve (año, mes, día, nombre_mes) en Europa/Madrid."""
    tz = pytz.timezone(TZ)
    now = datetime.datetime.now(tz)
    year = now.year
    month = now.month
    day = now.day

    meses = [
        "", "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
    ]
    month_name = meses[month]
    return year, month, day, month_name


# ----------------- Scrapers web (ya no usados en main, se dejan por si acaso) ----------------- #

def fetch_hoyenlahistoria_events():
    url = "https://www.hoyenlahistoria.com/efemerides.php"
    headers = {"User-Agent": USER_AGENT}

    resp = requests.get(url, headers=headers, timeout=25)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    events = []

    for li in soup.find_all("li"):
        text = " ".join(li.stripped_strings)
        if not text:
            continue

        m = re.match(r"^(\d+)\s*(a\.C\.)?\s*(.*)", text)
        if not m:
            continue

        year_str, era, rest = m.groups()
        try:
            year = int(year_str)
        except ValueError:
            continue

        if era:
            year = -year

        body = rest.strip()
        if not body:
            continue

        events.append({
            "year": year,
            "text": body,
            "raw": text,
            "source": "hoyenlahistoria"
        })

    return events


def fetch_nuestrahistoria_events_for_today(today_day, today_month_name):
    headers = {"User-Agent": USER_AGENT}
    events = []
    month = today_month_name.lower()
    day = today_day

    urls = [
        "https://nuestrahistoria.es/efemerides/",
        "https://nuestrahistoria.es/efemerides/2/",
        "https://nuestrahistoria.es/efemerides/3/",
    ]

    pattern = re.compile(
        rf"Tal día como hoy,\s*el\s+{day}\s+de\s+{month}[^\d]*(\d{{3,4}})(.*?)(?=Tal día como hoy, el|\Z)",
        re.IGNORECASE | re.DOTALL,
    )

    for url in urls:
        try:
            resp = requests.get(url, headers=headers, timeout=25)
            resp.raise_for_status()
        except Exception as e:
            print(f"⚠️ Error accediendo a {url}:", e)
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        full_text = soup.get_text(" ", strip=True)

        for m in pattern.finditer(full_text):
            year_str = m.group(1)
            try:
                year = int(year_str)
            except ValueError:
                continue
            snippet = m.group(0).strip()
            events.append({
                "year": year,
                "text": snippet,
                "raw": snippet,
                "source": "nuestrahistoria",
            })

    return events


def fetch_espanaenlahistoria_events_for_today(today_day, today_month_name):
    headers = {"User-Agent": USER_AGENT}
    events = []
    month = today_month_name.lower()
    day = today_day

    base = "https://espanaenlahistoria.org/efemerides/"
    urls = [
        base,
        base + "page/2/",
        base + "page/3/",
    ]

    pattern = re.compile(
        rf"\({day}\s+{month}\s+(\d{{3,4}})\)",
        re.IGNORECASE,
    )

    for url in urls:
        try:
            resp = requests.get(url, headers=headers, timeout=25)
            resp.raise_for_status()
        except Exception as e:
            print(f"⚠️ Error accediendo a {url}:", e)
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        full_text = soup.get_text(" ", strip=True)

        for m in pattern.finditer(full_text):
            year_str = m.group(1)
            try:
                year = int(year_str)
            except ValueError:
                continue

            start = max(0, m.start() - 200)
            end = min(len(full_text), m.end() + 200)
            snippet = full_text[start:end].strip()

            events.append({
                "year": year,
                "text": snippet,
                "raw": snippet,
                "source": "espanaenlahistoria",
            })

    return events


# ----------------- NUEVO: fuente principal → OpenAI (lista de efemérides) ----------------- #

def fetch_openai_events_for_today(today_year, today_month, today_day, today_month_name):
    """
    Pide a OpenAI una lista de efemérides del día centradas en España / Imperio,
    devuelve lista de dicts con: year, text, raw, source="openai".
    """
    today_str = f"{today_day} de {today_month_name} de {today_year}"

    prompt = f"""
Fecha de hoy: {today_str}.

Genera una lista de entre 20 y 40 efemérides históricas relevantes para la historia de España y del Imperio español
que ocurrieran un {today_day} de {today_month_name}, en cualquier año.

Condiciones:
- Deben ser hechos de tipo militar, político, diplomático, exploraciones, conquistas, tratados, cambios de régimen,
  grandes decisiones de Estado, fundaciones importantes, etc.
- España (o sus reinos históricos: Castilla, Aragón, Navarra, la Monarquía Hispánica, el Imperio español, etc.)
  debe ser actor principal o claramente protagonista.
- Redacta todo en español.

FORMATO DE RESPUESTA (OBLIGATORIO):
    Devuelve EXCLUSIVAMENTE un JSON con esta estructura:

{{
  "events": [
    {{
      "year": 1580,
      "type": "event",
      "entity": "Tratado de Lisboa",
      "text": "texto breve describiendo la efeméride..."
    }},
    {{
      "year": 1643,
      "type": "birth",
      "entity": "Carlos II de España",
      "text": "..."
    }}
  ]
}}

No añadas comentarios fuera del JSON.
"""

    completion = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "Eres un historiador especializado en España y en el Imperio español. "
                    "Generas efemérides precisas y relevantes siguiendo estrictamente el formato pedido."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.5,
        max_tokens=1200,
    )

    raw = completion.choices[0].message.content.strip()
    raw_clean = clean_json_from_markdown(raw)

    events = []
    try:
        data = json.loads(raw_clean)

        # Puede venir como {"events":[...]} o como lista directa [...]
        if isinstance(data, dict):
            items = data.get("events", [])
        else:
            items = data

        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                year = item.get("year")
                cand_type = item.get("type")
                entity = item.get("entity")
                desc = (
                    item.get("text")
                    or item.get("description")
                    or item.get("texto")
                )
                try:
                    year_int = int(year)
                except (TypeError, ValueError):
                    continue
                if not isinstance(desc, str):
                    continue
                if cand_type not in {"event", "birth", "death"}:
                    continue
                if not isinstance(entity, str) or not entity.strip():
                    continue
                desc = desc.strip()
                if not desc:
                    continue
                events.append({
                    "year": year_int,
                    "type": cand_type,
                    "entity": entity.strip(),
                    "text": desc,
                    "raw": desc,
                    "source": "openai",
                })
    except Exception as e:
        print("⚠️ No se ha podido parsear el JSON de efemérides desde OpenAI:", e)
        print("Contenido bruto devuelto por OpenAI:")
        print(raw)

    return events


# ----------------- Scoring “imperial” ----------------- #

def compute_score(ev):
    text = ev["text"]
    t_low = text.lower()
    year = ev["year"]

    score = 0.0

    has_spanish_actor = any(tok in t_low for tok in SPANISH_ACTOR_TOKENS)
    has_spanish_wide = any(tok in t_low for tok in SPANISH_WIDE_TOKENS)
    has_spanish_theatre = any(tok in t_low for tok in SPANISH_THEATRE_TOKENS)

    has_military = any(kw in t_low for kw in MILITARY_KEYWORDS)
    has_diplomatic = any(kw in t_low for kw in DIPLO_KEYWORDS)
    has_foreign = any(tok in t_low for tok in FOREIGN_TOKENS)

    if has_spanish_actor:
        score += 35

    if has_spanish_wide:
        score += 18

    if has_spanish_theatre:
        score += 5

    if has_military:
        score += 12

    if has_diplomatic:
        score += 8

    for kw in CULTURE_LOW_PRIORITY:
        if kw in t_low:
            score -= 12

    if 1400 <= year <= 1899:
        score += 5

    if has_military and has_foreign and not has_spanish_actor and not has_diplomatic:
        score -= 40

    ev["score"] = score
    ev["has_spanish_actor"] = has_spanish_actor
    ev["has_spanish_wide"] = has_spanish_wide
    ev["has_spanish_theatre"] = has_spanish_theatre
    ev["has_military"] = has_military
    ev["has_diplomatic"] = has_diplomatic
    ev["has_foreign"] = has_foreign


def choose_best_event(events, old_texts):
    """
    Elige el evento con mayor score según compute_score, evitando repetidos.
    """
    candidates = []

    for ev in events:
        if event_is_repeated(ev["text"], old_texts):
            continue
        compute_score(ev)
        candidates.append(ev)

    if not candidates:
        return None

    best = max(candidates, key=lambda e: e["score"])
    return best


def choose_best_verified_event(events, old_texts, today_ddmm):
    """
    Elige el mejor evento por score y lo valida con Wikidata.
    Si no pasa validación, prueba el siguiente.
    """
    candidates = []

    for ev in events:
        if event_is_repeated(ev["text"], old_texts):
            continue
        compute_score(ev)
        candidates.append(ev)

    if not candidates:
        return None

    candidates.sort(key=lambda e: e["score"], reverse=True)

    for ev in candidates:
        if validate_candidate_with_wikidata(ev, today_ddmm):
            return ev
        print(f"⚠️ Evento descartado por Wikidata: {ev['text']}")

    return None


# ----------------- Generación de TEXTO con OpenAI ----------------- #

def generate_headline_tweet(today_year, today_month_name, today_day, event):
    """
    Genera el tuit TITULAR (con banderita, fecha, año del suceso y hashtags).
    """
    today_str = f"{today_day} de {today_month_name} de {today_year}"
    event_year = event["year"]
    event_text = event["text"]
    hashtags = " ".join(DEFAULT_HASHTAGS)

    prompt_user = f"""
Fecha de hoy: {today_str}.
Efeméride seleccionada (año {event_year}) procedente de un listado de efemérides históricas:

\"\"\"{event_text}\"\"\"

Escribe UN SOLO tuit en español siguiendo EXACTAMENTE este formato general:

"🇪🇸 {today_str}: En tal día como hoy del año {event_year}, ... {hashtags}"

Reglas importantes:
- Máximo 260 caracteres en total (incluyendo los hashtags y la banderita).
- Debe empezar EXACTAMENTE por: "🇪🇸 {today_str}: En tal día como hoy del año {event_year},"
  y a continuación una frase breve que resuma el hecho histórico.
- Tono divulgativo, con cierto orgullo por la historia de España y su Imperio, sin más emojis, sin URLs y sin mencionar la fuente.
- No añadas más hashtags que estos cuatro ni cambies su texto: {hashtags}.
- No uses saltos de línea, todo debe ir en una sola frase.
"""

    completion = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "Eres un divulgador de historia de España y del Imperio español. "
                    "Escribes tuits breves, claros y con ligero tono épico, respetando estrictamente el formato pedido."
                ),
            },
            {"role": "user", "content": prompt_user},
        ],
        temperature=0.4,
        max_tokens=200,
    )

    text = completion.choices[0].message.content.strip()

    if len(text) > 275:
        text = text[:272].rstrip() + "..."

    prefix = f"🇪🇸 {today_str}: En tal día como hoy del año {event_year},"
    if not text.startswith(prefix):
        core_desc = event_text
        if len(core_desc) > 150:
            core_desc = core_desc[:147].rstrip() + "..."
        text = f"{prefix} {core_desc} {hashtags}"
        if len(text) > 275:
            text = text[:272].rstrip() + "..."

    return text


def generate_followup_tweets(today_year, today_month_name, today_day, event):
    """
    Genera entre 1 y 5 tuits adicionales que irán como respuestas (hilo).
    """
    today_str = f"{today_day} de {today_month_name} de {today_year}"
    event_year = event["year"]
    event_text = event["text"]

    prompt_user = f"""
Fecha de hoy: {today_str}.
Efeméride seleccionada (año {event_year}):

\"\"\"{event_text}\"\"\"

Vas a escribir un HILO que continúa el tuit titular (que ya dice:
"🇪🇸 {today_str}: En tal día como hoy del año {event_year}, ...").

Tu tarea:
- Redacta entre 1 y 5 tuits adicionales (no el titular) que expliquen:
  - qué supuso este hecho para España o para el Imperio español,
  - o por qué la figura implicada fue importante para España/Imperio,
  - consecuencias a corto y largo plazo,
  - contexto histórico relevante (sin irte del tema).
- Cada tuit debe:
  - estar en español,
  - tener como máximo 260 caracteres,
  - NO empezar por la fecha ni por "En tal día como hoy...",
  - NO incluir hashtags,
  - NO incluir emojis,
  - ser autosuficiente pero encajar como parte de una pequeña historia enlazada.

FORMATO DE RESPUESTA:
- Devuélveme EXCLUSIVAMENTE un JSON con una lista de strings, por ejemplo:
  ["texto del tuit 2", "texto del tuit 3", "..."]
- No añadas nada fuera del JSON.
"""

    completion = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "Eres un divulgador de historia de España y del Imperio español. "
                    "Escribes hilos de X breves, claros y ordenados, respetando estrictamente el formato pedido."
                ),
            },
            {"role": "user", "content": prompt_user},
        ],
        temperature=0.6,
        max_tokens=400,
    )

    raw = completion.choices[0].message.content.strip()
    raw_clean = clean_json_from_markdown(raw)

    tweets = []
    try:
        data = json.loads(raw_clean)

        # Puede venir como lista directa ["...", "..."]
        # o como {"tweets":[...]} o similar
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            # buscamos una clave razonable
            if "tweets" in data and isinstance(data["tweets"], list):
                items = data["tweets"]
            elif "hilo" in data and isinstance(data["hilo"], list):
                items = data["hilo"]
            else:
                # Intentar coger el primer valor que sea lista
                listas = [v for v in data.values() if isinstance(v, list)]
                items = listas[0] if listas else []
        else:
            items = []

        if isinstance(items, list):
            for item in items:
                if isinstance(item, str):
                    text = item.strip()
                elif isinstance(item, dict):
                    text = str(
                        item.get("text")
                        or item.get("contenido")
                        or item.get("description")
                        or ""
                    ).strip()
                else:
                    continue

                if not text:
                    continue

                if len(text) > 275:
                    text = text[:272].rstrip() + "..."
                tweets.append(text)
    except Exception as e:
        print("⚠️ No se ha podido parsear el JSON de followups:", e)
        print("Contenido bruto devuelto por OpenAI:")
        print(raw)
        tweets = []

    if len(tweets) > 5:
        tweets = tweets[:5]

    return tweets


# ----------------- Publicación en X (API v2) ----------------- #

def get_twitter_client():
    if not (TW_API_KEY and TW_API_SECRET and TW_ACCESS_TOKEN and TW_ACCESS_SECRET and TW_BEARER_TOKEN):
        raise RuntimeError("Faltan claves de Twitter/X en las variables de entorno.")

    print(
        "DEBUG Twitter keys present:",
        bool(TW_API_KEY),
        bool(TW_API_SECRET),
        bool(TW_ACCESS_TOKEN),
        bool(TW_ACCESS_SECRET),
        bool(TW_BEARER_TOKEN),
    )

    client_tw = tweepy.Client(
        consumer_key=TW_API_KEY,
        consumer_secret=TW_API_SECRET,
        access_token=TW_ACCESS_TOKEN,
        access_token_secret=TW_ACCESS_SECRET,
        bearer_token=TW_BEARER_TOKEN,
    )
    return client_tw


def post_thread(headline, followups):
    """
    Publica el tuit titular y, si hay followups, va respondiendo en hilo.
    """
    client_tw = get_twitter_client()

    resp = client_tw.create_tweet(text=headline)
    print("DEBUG create_tweet (headline) response:", resp)
    tweet_id = resp.data.get("id")
    if not tweet_id:
        print("⚠️ No se obtuvo ID del tuit titular, no se puede continuar el hilo.")
        return

    parent_id = tweet_id
    for t in followups:
        try:
            resp = client_tw.create_tweet(text=t, in_reply_to_tweet_id=parent_id)
            print("DEBUG create_tweet (reply) response:", resp)
            new_id = resp.data.get("id")
            if new_id:
                parent_id = new_id
        except Exception as e:
            print("❌ Error publicando un tuit de hilo:", e)
            break


def try_publish_pending_thread(pending):
    """Intenta publicar un hilo pendiente. Devuelve True si se publicó o False si se mantiene."""
    print("📨 Hay un hilo pendiente en pending_tweet.json. Intentando publicarlo primero...")
    try:
        post_thread(pending["headline"], pending.get("followups", []))
        print("✅ Hilo pendiente publicado correctamente.")
        clear_pending_tweet()
        return True
    except tweepy.errors.TooManyRequests:
        print("❌ Rate limit 429 al publicar el hilo pendiente. Se mantiene en cola y se aborta hoy.")
        return False
    except Exception as e:
        print("❌ Error publicando el hilo pendiente:", e)
        print("Se mantiene en cola y se aborta hoy para no perderlo.")
        return False


# ----------------- Main ----------------- #

def main():
    today_year, today_month, today_day, today_month_name = today_info()
    today_ddmm = f"{today_day:02d}/{today_month:02d}"

    print(f"Hoy es {today_day}/{today_month}/{today_year} ({today_month_name}).")

    # 0) Si hay un hilo pendiente de días anteriores, intentamos publicarlo primero
    pending = load_pending_tweet()
    if pending:
        pending_ddmm = pending.get("target_ddmm")
        if pending_ddmm != today_ddmm:
            print(
                "⚠️ Hay un hilo pendiente, pero su fecha objetivo no coincide con hoy. "
                "No se publicará para evitar errores de dd/mm."
            )
        else:
            if not try_publish_pending_thread(pending):
                return

    # 1) Fuente principal: OpenAI genera efemérides del día
    try:
        events = fetch_openai_events_for_today(today_year, today_month, today_day, today_month_name)
        print(f"Se han generado {len(events)} efemérides desde OpenAI para {today_day}/{today_month}/{today_year}.")
    except Exception as e:
        print("❌ Error generando efemérides desde OpenAI:", e)
        events = []

    if not events:
        print("No hay eventos generados para hoy. No se publicará tuit.")
        return

    # 2) Anti-repetición basándose en tu timeline reciente
    old_texts = fetch_previous_events_same_day(today_month, today_day)

    # 3) Elegir el mejor evento según scoring y evitando repetidos
    best = choose_best_verified_event(events, old_texts, today_ddmm)
    if not best:
        print("No se ha podido seleccionar una efeméride válida tras verificación. No se publicará tuit.")
        return

    print("Evento elegido:")
    print(f"- Año: {best['year']}")
    print(f"- Tipo: {best['type']}")
    print(f"- Entidad: {best['entity']}")
    print(f"- Texto: {best['text']}")
    print(f"- Score: {best.get('score', 'N/A')}")
    print(
        f"- ActorEsp: {best.get('has_spanish_actor')}, "
        f"EspAmplio: {best.get('has_spanish_wide')}, "
        f"TeatroEsp: {best.get('has_spanish_theatre')}, "
        f"Militar: {best.get('has_military')}, "
        f"Diplomático: {best.get('has_diplomatic')}, "
        f"Extranjeros: {best.get('has_foreign')}"
    )

    # 4) Generar el tuit titular
    try:
        headline = generate_headline_tweet(today_year, today_month_name, today_day, best)
    except Exception as e:
        print("❌ Error al generar el tuit titular con OpenAI:", e)
        return

    if not headline or not isinstance(headline, str) or len(headline.strip()) == 0:
        print("❌ OpenAI devolvió un titular vacío o inválido. Abortando para evitar publicar un tuit en blanco.")
        return

    print("Tuit titular generado:")
    print(headline)
    print(f"Largo: {len(headline)} caracteres")

    # 5) Generar los tuits de hilo (2º a 6º)
    try:
        followups = generate_followup_tweets(today_year, today_month_name, today_day, best)
    except Exception as e:
        print("⚠️ Error generando los tuits de hilo con OpenAI:", e)
        followups = []

    print(f"Se han generado {len(followups)} tuits adicionales para el hilo.")
    for i, t in enumerate(followups, start=2):
        print(f"[Tuit {i}] {t} (len={len(t)})")

    # 6) Anti-contradicciones
    headline, followups = detect_and_fix_contradictions(headline, followups, best["text"])

    # 7) Publicar hilo en X
    try:
        post_thread(headline, followups)
        print("✅ Hilo publicado correctamente.")
    except tweepy.errors.TooManyRequests:
        print("⚠️ 429 Too Many Requests al publicar el hilo de hoy. Se guarda como pendiente.")
        save_pending_tweet(headline, followups, today_ddmm)
        return
    except Exception as e:
        print("❌ Error publicando el hilo en Twitter/X:", e)
        raise


def run_wikidata_validation_smoke_test():
    """
    Smoke test manual: Felipe III de España NO coincide con 07/01.
    """
    candidate = {
        "type": "death",
        "entity": "Felipe III de España",
        "year": 1621,
        "text": "Fallecimiento de Felipe III de España.",
    }
    today_ddmm = "07/01"
    is_valid = validate_candidate_with_wikidata(candidate, today_ddmm)
    print(f"Resultado test Felipe III (death) vs {today_ddmm}: {is_valid}")


if __name__ == "__main__":
    if os.getenv("RUN_WIKIDATA_TEST") == "1":
        run_wikidata_validation_smoke_test()
    else:
        main()
