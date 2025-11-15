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

# Teatro en suelo español
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

# Diplomacia
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

# Cosas que penalizamos
CULTURE_LOW_PRIORITY = [
    "premio", "premios", "concurso", "festival", "certamen",
    "programa de radio", "programa de televisión", "radio", "televisión",
    "serie", "película", "cine", "novela", "poeta", "cantante", "músico",
    "discográfica", "disco", "álbum", "single"
]

# Claves de X
TW_API_KEY = os.getenv("TWITTER_API_KEY", "")
TW_API_SECRET = os.getenv("TWITTER_API_SECRET", "")
TW_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN", "")
TW_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")
TW_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN", "")

USER_AGENT = "Efemerides_Imp_Bot/1.0 (https://github.com/efemeridesesp/tal-dia-como-hoy-es)"

# Cliente OpenAI
client = OpenAI()

# ID numérico de tu cuenta:
TWITTER_USER_ID = "1988838626760032256"


# ----------------- NUEVO: obtener tuits antiguos para evitar repetidos ----------------- #

def fetch_previous_events_same_day(month, day):
    """
    Obtiene los tuits TITULARES publicados en años anteriores en este mismo día
    para detectar efemérides ya usadas.
    """
    if not TW_BEARER_TOKEN:
        return []

    cli = tweepy.Client(bearer_token=TW_BEARER_TOKEN)

    old_texts = []
    pagination_token = None

    # buscamos texto que empiece por "🇪🇸 {día} de {mes}"
    search_prefix = f"🇪🇸 {day} de "

    for _ in range(5):  # límite defensivo
        resp = cli.get_users_tweets(
            id=TWITTER_USER_ID,
            max_results=100,
            pagination_token=pagination_token,
            tweet_fields=["created_at", "text"]
        )
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

    key_fragments = [
        *SPANISH_ACTOR_TOKENS,
        *SPANISH_WIDE_TOKENS,
        *MILITARY_KEYWORDS,
        *DIPLO_KEYWORDS
    ]

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
  "fixed": ["tuit1", "tuit2", ...]   ← MISMO NÚMERO DE TUITES, corregidos
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
    except:
        pass

    return headline, followups


# ----------------- Utilidades de fecha ----------------- #

def today_info():
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


# ----------------- Scraper hoyenlahistoria ----------------- #

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


# ----------------- Scoring ----------------- #

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
    Filtra eventos ya usados y elige el mejor.
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


# ----------------- Generación texto OpenAI ----------------- #

def generate_headline_tweet(today_year, today_month_name, today_day, event):
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

Reglas:
- Máx 260 caracteres.
- Sin emojis adicionales.
- Sin URLs.
- Solo esos hashtags.
"""

    completion = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "Eres divulgador..."},
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
    today_str = f"{today_day} de {today_month_name} de {today_year}"
    event_year = event["year"]
    event_text = event["text"]

    prompt_user = f"""
Fecha de hoy: {today_str}.
Efeméride seleccionada (año {event_year}):

\"\"\"{event_text}\"\"\"

Escribe entre 1 y 5 tuits adicionales. Sin fechas, sin "En tal día...", sin hashtags, sin emojis.
Devuelve únicamente JSON con una lista de strings.
"""

    completion = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "Eres divulgador..."},
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
                    if len(text) > 275:
                        text = text[:272].rstrip() + "..."
                    tweets.append(text)
    except:
        tweets = []

    if len(tweets) > 5:
        tweets = tweets[:5]

    return tweets


# ----------------- Publicación X ----------------- #

def get_twitter_client():
    if not (TW_API_KEY and TW_API_SECRET and TW_ACCESS_TOKEN and TW_ACCESS_SECRET and TW_BEARER_TOKEN):
        raise RuntimeError("Faltan claves de Twitter/X en las variables de entorno.")

    client_tw = tweepy.Client(
        consumer_key=TW_API_KEY,
        consumer_secret=TW_API_SECRET,
        access_token=TW_ACCESS_TOKEN,
        access_token_secret=TW_ACCESS_SECRET,
        bearer_token=TW_BEARER_TOKEN,
    )
    return client_tw


def post_thread(headline, followups):
    client_tw = get_twitter_client()

    resp = client_tw.create_tweet(text=headline)
    tweet_id = resp.data.get("id")
    if not tweet_id:
        return

    parent_id = tweet_id
    for t in followups:
        try:
            resp = client_tw.create_tweet(text=t, in_reply_to_tweet_id=parent_id)
            new_id = resp.data.get("id")
            if new_id:
                parent_id = new_id
        except:
            break


# ----------------- Main ----------------- #

def main():
    today_year, today_month, today_day, today_month_name = today_info()

    print(f"Hoy es {today_day}/{today_month}/{today_year} ({today_month_name}).")

    try:
        events = fetch_hoyenlahistoria_events()
        print(f"Se han encontrado {len(events)} eventos.")
    except Exception as e:
        print("❌ Error obteniendo eventos:", e)
        return

    if not events:
        print("No hay eventos.")
        return

    # NUEVO: obtener tuits antiguos de este día
    old_texts = fetch_previous_events_same_day(today_month, today_day)

    # NUEVO: elegir evento evitando repetidos
    best = choose_best_event(events, old_texts)
    if not best:
        print("No hay efemérides nuevas para este día.")
        return

    print("Evento elegido:")
    print(best)

    try:
        headline = generate_headline_tweet(today_year, today_month_name, today_day, best)
    except Exception as e:
        print("❌ Error titular:", e)
        return

    try:
        followups = generate_followup_tweets(today_year, today_month_name, today_day, best)
    except:
        followups = []

    # NUEVO: detector de contradicciones
    headline, followups = detect_and_fix_contradictions(headline, followups, best["text"])

    try:
        post_thread(headline, followups)
        print("✅ Publicado.")
    except Exception as e:
        print("❌ Error publicando:", e)
        raise


if __name__ == "__main__":
    main()
