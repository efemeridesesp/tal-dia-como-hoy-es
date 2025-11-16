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

# Cliente de OpenAI (usa OPENAI_API_KEY del entorno)
client = OpenAI()

# ID numérico de tu cuenta
TWITTER_USER_ID = "1988838626760032256"


# ----------------- NUEVO: evitar repetir efemérides ya publicadas ----------------- #

def fetch_previous_events_same_day(month, day):
    """
    Obtiene los tuits TITULARES publicados en años anteriores en este mismo día
    para detectar efemérides ya usadas. Si hay rate limit (429), devolvemos []
    para no romper el workflow.
    """
    if not TW_BEARER_TOKEN:
        return []

    cli = tweepy.Client(bearer_token=TW_BEARER_TOKEN)
    old_texts = []
    pagination_token = None

    search_prefix = f"🇪🇸 {day} de "

    for _ in range(6):  # 6 páginas máx.
        try:
            resp = cli.get_users_tweets(
                id=TWITTER_USER_ID,
                max_results=100,
                pagination_token=pagination_token,
                tweet_fields=["created_at", "text"]
            )
        except tweepy.errors.TooManyRequests:
            print("⚠️ Rate limit X (429) en get_users_tweets. Se desactiva anti-repetición hoy.")
            return []
        except Exception as e:
            print("⚠️ Error consultando tuits anteriores:", e)
            return []

        if not resp.data:
            break

        for t in resp.data:
            txt = t.text
            if search_prefix in txt:
                old_texts.append(txt.lower())

        pagination_token = resp.meta.get("next_token")
        if not pagination_token:
            break

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


# ----------------- NUEVO: detector de contradicciones ----------------- #

def detect_and_fix_contradictions(headline, followups, event_text):
    """
    Detecta contradicciones internas usando modelo y reescribe los tuits conflictivos.
    """
    all_tweets = [headline] + followups

    prompt = f"""
Analiza estos tuits y detecta contradicciones internas en fechas, cifras, nombres, lugares o hechos:

EFEMÉRIDE ORIGINAL:
\"\"\"{event_text}\"\"\"

TUITS DEL HILO:
{json.dumps(all_tweets, ensure_ascii=False, indent=2)}

Devuelve EXCLUSIVAMENTE un JSON con la siguiente forma:
{{
  "fixed": ["tuit1", "tuit2", "..."]
}}
No añadas nada más.
"""

    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "Corrige contradicciones internas respetando el estilo original."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.2,
        max_tokens=800
    )

    raw = resp.choices[0].message.content.strip()

    try:
        data = json.loads(raw)
        fixed = data.get("fixed", [])
        if isinstance(fixed, list) and len(fixed) == len(all_tweets):
            return fixed[0], fixed[1:]
    except Exception:
        pass

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


# ----------------- Scraper de hoyenlahistoria.com ----------------- #

def fetch_hoyenlahistoria_events():
    """
    Lee https://www.hoyenlahistoria.com/efemerides.php y devuelve
    una lista de eventos con campos: year, text, raw.
    """
    url = "https://www.hoyenlahistoria.com/efemerides.php"
    headers = {"User-Agent": USER_AGENT}

    resp = requests.get(url, headers=headers, timeout=25)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    events = []

    # Miramos todos los list items que empiezan con un año
    for li in soup.find_all("li"):
        text = " ".join(li.stripped_strings)
        if not text:
            continue

        # Formato típico: "1501 el príncipe de Gales..."
        m = re.match(r"^(\d+)\s*(a\.C\.)?\s*(.*)", text)
        if not m:
            continue

        year_str, era, rest = m.groups()
        try:
            year = int(year_str)
        except ValueError:
            continue

        if era:
            year = -year  # años a.C. negativos, por si algún día interesa

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


# ----------------- NUEVO: scraper de nuestrahistoria.es ----------------- #

def fetch_nuestrahistoria_events_for_today(today_day, today_month_name):
    """
    Busca en nuestrahistoria.es/efemerides/ textos tipo
    'Tal día como hoy, el X de mes de AAAA...' y devuelve eventos.
    """
    headers = {"User-Agent": USER_AGENT}
    events = []
    month = today_month_name.lower()
    day = today_day

    # Revisamos primera página de efemérides (suele tener las más recientes)
    urls = [
        "https://nuestrahistoria.es/efemerides/",
        "https://nuestrahistoria.es/efemerides/2/",
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


# ----------------- NUEVO: scraper de espanaenlahistoria.org ----------------- #

def fetch_espanaenlahistoria_events_for_today(today_day, today_month_name):
    """
    Busca en espanaenlahistoria.org/efemerides/ textos con fechas tipo
    '(X mes AAAA)' y extrae los que coincidan con el día/mes actual.
    """
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

            # Cogemos una ventana de contexto alrededor de la fecha
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


# ----------------- Scoring “imperial” con penalización a batallas guiris ----------------- #

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

    # Núcleo: España/Imperio como actor → MUY arriba
    if has_spanish_actor:
        score += 35

    # Marca España amplia (España, hispania, ciudades históricas, etc.)
    if has_spanish_wide:
        score += 18

    # Teatro en España suma, pero menos
    if has_spanish_theatre:
        score += 5

    # Militar suma bastante (prioriza batallas)
    if has_military:
        score += 12

    # Diplomático (tratados, acuerdos, etc.) también suma
    if has_diplomatic:
        score += 8

    # Penalizar fuerte cosas de premios/cultura pop
    for kw in CULTURE_LOW_PRIORITY:
        if kw in t_low:
            score -= 12

    # Bonus por siglos interesantes (1500–1899 aprox.)
    if 1400 <= year <= 1899:
        score += 5

    # Penalización clave:
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


# ----------------- Generación de TEXTO con OpenAI ----------------- #

def generate_headline_tweet(today_year, today_month_name, today_day, event):
    """
    Genera el tuit TITULAR (con banderita, fecha, año del suceso y hashtags).
    Formato:
    '🇪🇸 14 de noviembre de 2025: En tal día como hoy del año XXXX, ... #TalDiaComoHoy #España #HistoriaDeEspaña #Efemérides'
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

    # Recorte de seguridad
    if len(text) > 275:
        text = text[:272].rstrip() + "..."

    # Seguridad extra: si por lo que sea no empieza como debe, lo forzamos mínimamente
    prefix = f"🇪🇸 {today_str}: En tal día como hoy del año {event_year},"
    if not text.startswith(prefix):
        # Extraemos solo la parte descriptiva
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
    - Sin fecha ni fórmula 'En tal día como hoy...'
    - Sin hashtags.
    - Sin emojis.
    - Explican por qué ese hecho fue importante para España/Imperio, consecuencias, etc.
    Devuelve una lista de strings.
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

    tweets = []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    text = item.strip()
                    if not text:
                        continue
                    # Recorte de seguridad
                    if len(text) > 275:
                        text = text[:272].rstrip() + "..."
                    tweets.append(text)
    except Exception as e:
        print("⚠️ No se ha podido parsear el JSON de followups:", e)
        print("Contenido bruto devuelto por OpenAI:")
        print(raw)
        tweets = []

    # Garantizar entre 1 y 5 si hay algo; si no hay nada, devolvemos lista vacía
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

    # Publicar titular
    resp = client_tw.create_tweet(text=headline)
    print("DEBUG create_tweet (headline) response:", resp)
    tweet_id = resp.data.get("id")
    if not tweet_id:
        print("⚠️ No se obtuvo ID del tuit titular, no se puede continuar el hilo.")
        return

    # Publicar respuestas encadenadas
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


# ----------------- Main ----------------- #

def main():
    today_year, today_month, today_day, today_month_name = today_info()

    print(f"Hoy es {today_day}/{today_month}/{today_year} ({today_month_name}).")

    # 1) Obtener eventos de hoy en la web (HOYENLAHISTORIA)
    try:
        events = fetch_hoyenlahistoria_events()
        print(f"Se han encontrado {len(events)} eventos en hoyenlahistoria.com")
    except Exception as e:
        print("❌ Error obteniendo eventos de hoyenlahistoria.com:", e)
        events = []

    # NUEVO: sumar eventos de otras fuentes
    try:
        nh_events = fetch_nuestrahistoria_events_for_today(today_day, today_month_name)
        print(f"Se han encontrado {len(nh_events)} eventos en nuestrahistoria.es")
        events.extend(nh_events)
    except Exception as e:
        print("⚠️ Error obteniendo eventos de nuestrahistoria.es:", e)

    try:
        ee_events = fetch_espanaenlahistoria_events_for_today(today_day, today_month_name)
        print(f"Se han encontrado {len(ee_events)} eventos en espanaenlahistoria.org")
        events.extend(ee_events)
    except Exception as e:
        print("⚠️ Error obteniendo eventos de espanaenlahistoria.org:", e)

    print(f"Total de eventos recopilados de todas las fuentes: {len(events)}")

    if not events:
        print("No hay eventos disponibles para hoy en ninguna fuente. No se publicará tuit.")
        return

    # NUEVO: cargar tuits antiguos de este día (para anti-repetición)
    old_texts = fetch_previous_events_same_day(today_month, today_day)

    # 2) Elegir el mejor evento según scoring y evitando repetidos
    best = choose_best_event(events, old_texts)
    if not best:
        print("No se ha podido seleccionar una efeméride adecuada (o todas repetidas). No se publicará tuit.")
        return

    print("Evento elegido:")
    print(f"- Año: {best['year']}")
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

    # 3) Generar el tuit titular
    try:
        headline = generate_headline_tweet(today_year, today_month_name, today_day, best)
    except Exception as e:
        print("❌ Error al generar el tuit titular con OpenAI:", e)
        return

    # NUEVO: evitar publicar un tuit vacío
    if not headline or not isinstance(headline, str) or len(headline.strip()) == 0:
        print("❌ OpenAI devolvió un titular vacío o inválido. Abortando para evitar publicar un tuit en blanco.")
        return

    print("Tuit titular generado:")
    print(headline)
    print(f"Largo: {len(headline)} caracteres")

    # 4) Generar los tuits de hilo (2º a 6º)
    try:
        followups = generate_followup_tweets(today_year, today_month_name, today_day, best)
    except Exception as e:
        print("⚠️ Error generando los tuits de hilo con OpenAI:", e)
        followups = []

    print(f"Se han generado {len(followups)} tuits adicionales para el hilo.")
    for i, t in enumerate(followups, start=2):
        print(f"[Tuit {i}] {t} (len={len(t)})")

    # NUEVO: detector de contradicciones interno (titular + hilo)
    headline, followups = detect_and_fix_contradictions(headline, followups, best["text"])

    # 5) Publicar hilo en X
    try:
        post_thread(headline, followups)
        print("✅ Hilo publicado correctamente.")
    except Exception as e:
        print("❌ Error publicando el hilo en Twitter/X:", e)
        raise


if __name__ == "__main__":
    main()
