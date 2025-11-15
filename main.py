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

# “Marca España” amplia
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

# Teatro español
SPANISH_THEATRE_TOKENS = [
    "málaga", "cádiz", "cartagena", "cartagena de indias",
    "barcelona", "valencia", "bilbao", "santander", "la coruña",
    "ceuta", "melilla", "baleares", "canarias",
]

MILITARY_KEYWORDS = [
    "batalla", "guerra", "combate", "frente",
    "asedio", "sitio", "conquista", "derrota", "victoria", "alzamiento",
    "revolución", "levantamiento", "sublevación", "bombardeo", "invasión",
    "ejército", "toma", "capitulación", "ofensiva", "defensiva",
]

DIPLO_KEYWORDS = [
    "tratado", "acuerdo", "paz", "alianza",
    "capitulaciones", "concordia",
]

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

CULTURE_LOW_PRIORITY = [
    "premio", "premios", "concurso", "festival", "certamen",
    "programa de radio", "programa de televisión", "radio", "televisión",
    "serie", "película", "cine", "novela", "poeta", "cantante", "músico",
    "discográfica", "disco", "álbum", "single"
]

# Claves X
TW_API_KEY = os.getenv("TWITTER_API_KEY", "")
TW_API_SECRET = os.getenv("TWITTER_API_SECRET", "")
TW_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN", "")
TW_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")
TW_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN", "")

USER_AGENT = "Efemerides_Imp_Bot/1.0 (https://github.com/efemeridesesp/tal-dia-como-hoy-es)"

client = OpenAI()

# ID NUMÉRICO REAL DE TU CUENTA
TWITTER_USER_ID = "1988838626760032256"


# -------------------------------------------------------------
# NUEVO: Obtener tuits antiguos de este mismo día (anti-repe)
# -------------------------------------------------------------

def fetch_previous_events_same_day(month, day):
    if not TW_BEARER_TOKEN:
        return []

    cli = tweepy.Client(bearer_token=TW_BEARER_TOKEN)
    old_texts = []
    pagination_token = None

    search_prefix = f"🇪🇸 {day} de "

    for _ in range(5):
        resp = cli.get_users_tweets(
            id=TWITTER_USER_ID,
            max_results=100,
            pagination_token=pagination_token,
            tweet_fields=["created_at", "text"],
        )
        if not resp.data:
            break

        for t in resp.data:
            if search_prefix in t.text:
                old_texts.append(t.text.lower())

        pagination_token = resp.meta.get("next_token")
        if not pagination_token:
            break

    return old_texts


def event_is_repeated(event_text, old_texts):
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


# -------------------------------------------------------------
# NUEVO: detector de contradicciones internas
# -------------------------------------------------------------

def detect_and_fix_contradictions(headline, followups, event_text):
    all_tweets = [headline] + followups

    prompt = f"""
Analiza estos tuits y elimina contradicciones internas.

EFEMÉRIDE:
\"\"\"{event_text}\"\"\"

TUITS:
{json.dumps(all_tweets, ensure_ascii=False, indent=2)}

Devuelve SOLO:

{{
  "fixed": ["tuit1", "tuit2", ...]
}}
"""

    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "Corrige contradicciones internas."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=800,
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


# -------------------------------------------------------------
# UTILIDADES
# -------------------------------------------------------------

def today_info():
    tz = pytz.timezone(TZ)
    now = datetime.datetime.now(tz)
    year, month, day = now.year, now.month, now.day
    meses = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio",
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    return year, month, day, meses[month]


def fetch_hoyenlahistoria_events():
    url = "https://www.hoyenlahistoria.com/efemerides.php"
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=25)
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
        except:
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
            "source": "hoyenlahistoria",
        })

    return events


def compute_score(ev):
    text = ev["text"].lower()
    year = ev["year"]

    score = 0

    if any(tok in text for tok in SPANISH_ACTOR_TOKENS): score += 35
    if any(tok in text for tok in SPANISH_WIDE_TOKENS): score += 18
    if any(tok in text for tok in SPANISH_THEATRE_TOKENS): score += 5
    if any(tok in text for tok in MILITARY_KEYWORDS): score += 12
    if any(tok in text for tok in DIPLO_KEYWORDS): score += 8

    for k in CULTURE_LOW_PRIORITY:
        if k in text:
            score -= 12

    if 1400 <= year <= 1899:
        score += 5

    if (any(k in text for k in MILITARY_KEYWORDS)
            and any(k in text for k in FOREIGN_TOKENS)
            and not any(k in text for k in SPANISH_ACTOR_TOKENS)
            and not any(k in text for k in DIPLO_KEYWORDS)):
        score -= 40

    ev["score"] = score


def choose_best_event(events, old_texts):
    filtered = []
    for ev in events:
        if event_is_repeated(ev["text"], old_texts):
            continue
        compute_score(ev)
        filtered.append(ev)

    if not filtered:
        return None

    return max(filtered, key=lambda e: e["score"])


# -------------------------------------------------------------
# GENERACIÓN OPENAI
# -------------------------------------------------------------

def generate_headline_tweet(today_year, today_month_name, today_day, event):
    today_str = f"{today_day} de {today_month_name} de {today_year}"
    event_year = event["year"]
    event_text = event["text"]
    hashtags = " ".join(DEFAULT_HASHTAGS)

    prompt = f"""
Escribe exactamente un tuit así:

"🇪🇸 {today_str}: En tal día como hoy del año {event_year}, ... {hashtags}"

Máx 260 caracteres.
Sin emojis extra.
Sin URLs.
"""

    out = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "Eres divulgador épico español."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
        max_tokens=200,
    )

    text = out.choices[0].message.content.strip()

    if len(text) > 275:
        text = text[:272].rstrip() + "..."

    return text


def generate_followup_tweets(today_year, today_month_name, today_day, event):
    today_str = f"{today_day} de {today_month_name} de {today_year}"

    prompt = f"""
Redacta 1–5 tuits de hilo, sin emojis, sin hashtags, sin fecha.
Devuelve SOLO un JSON: ["tuit2", "tuit3", ...]
"""

    out = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "Eres divulgador."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.6,
        max_tokens=400,
    )

    raw = out.choices[0].message.content.strip()

    tweets = []
    try:
        data = json.loads(raw)
        for t in data:
            if len(t) > 275:
                t = t[:272] + "..."
            tweets.append(t)
    except:
        pass

    return tweets[:5]


# -------------------------------------------------------------
# PUBLICAR
# -------------------------------------------------------------

def get_twitter_client():
    return tweepy.Client(
        consumer_key=TW_API_KEY,
        consumer_secret=TW_API_SECRET,
        access_token=TW_ACCESS_TOKEN,
        access_token_secret=TW_ACCESS_SECRET,
        bearer_token=TW_BEARER_TOKEN,
    )


def post_thread(headline, followups):
    client_tw = get_twitter_client()

    resp = client_tw.create_tweet(text=headline)
    tid = resp.data.get("id")
    if not tid:
        return

    parent = tid
    for t in followups:
        r = client_tw.create_tweet(text=t, in_reply_to_tweet_id=parent)
        if r.data.get("id"):
            parent = r.data["id"]


# -------------------------------------------------------------
# MAIN
# -------------------------------------------------------------

def main():
    today_year, today_month, today_day, today_month_name = today_info()

    print(f"Hoy es {today_day}/{today_month}/{today_year} ({today_month_name}).")

    try:
        events = fetch_hoyenlahistoria_events()
    except Exception as e:
        print("❌ Error eventos:", e)
        return

    if not events:
        print("No hay eventos")
        return

    old_texts = fetch_previous_events_same_day(today_month, today_day)

    best = choose_best_event(events, old_texts)
    if not best:
        print("No hay efemérides nuevas")
        return

    # -----------------------------------------------
    # GENERAR TITULAR
    # -----------------------------------------------
    try:
        headline = generate_headline_tweet(today_year, today_month_name, today_day, best)
    except Exception as e:
        print("❌ Error titular:", e)
        return

    # NUEVO: evitar tuit vacío o None
    if not headline or not isinstance(headline, str) or len(headline.strip()) == 0:
        print("❌ OpenAI devolvió titular vacío. Abortando.")
        return

    # -----------------------------------------------
    # FOLLOWUPS
    # -----------------------------------------------
    try:
        followups = generate_followup_tweets(today_year, today_month_name, today_day, best)
    except:
        followups = []

    # -----------------------------------------------
    # DETECTAR Y CORREGIR CONTRADICCIONES
    # -----------------------------------------------
    headline, followups = detect_and_fix_contradictions(headline, followups, best["text"])

    # -----------------------------------------------
    # PUBLICAR
    # -----------------------------------------------
    try:
        post_thread(headline, followups)
        print("✅ Publicado.")
    except Exception as e:
        print("❌ Error publicando:", e)


if __name__ == "__main__":
    main()
