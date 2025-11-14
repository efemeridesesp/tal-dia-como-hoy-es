import os
import requests
import datetime
import pytz
import re
import json
from bs4 import BeautifulSoup
from openai import OpenAI
import tweepy

TZ = "Europe/Madrid"

DEFAULT_HASHTAGS = ["#TalDiaComoHoy", "#España", "#HistoriaDeEspaña", "#Efemérides"]

SPANISH_ACTOR_TOKENS = [
    "reyes católicos", "imperio español", "monarquía hispánica", "monarquía española",
    "armada española", "ejército español", "tercios", "tercios españoles",
    "tercios de flandes", "virreinato de", "virreinato del", "virreinato de nueva españa",
    "virreinato del perú", "virreinato del río de la plata", "virrey", "virreina",
    "corona de castilla", "corona de aragón",
]

SPANISH_WIDE_TOKENS = [
    "españa", "español", "española", "españoles", "hispania", "hispano", "hispánica",
    "reino de castilla", "reino de aragón", "castilla", "aragón", "granada", "sevilla",
    "toledo", "madrid", "cartagena", "cartagena de indias", "virreinato", "borbón",
    "borbones", "habsburgo", "felipe ii", "felipe iii", "felipe iv",
    "carlos v", "carlos i de españa", "alfonso xii", "alfonso xiii", "isabel ii",
    "partido comunista de españa", "radio barcelona",
]

SPANISH_THEATRE_TOKENS = [
    "málaga", "cádiz", "cartagena", "cartagena de indias", "barcelona",
    "valencia", "bilbao", "santander", "la coruña", "ceuta", "melilla",
    "baleares", "canarias",
]

MILITARY_KEYWORDS = [
    "batalla", "guerra", "combate", "frente", "asedio", "sitio", "conquista",
    "derrota", "victoria", "alzamiento", "revolución", "levantamiento",
    "sublevación", "bombardeo", "invasión", "ejército", "toma", "capitulación",
    "ofensiva", "defensiva",
]

DIPLO_KEYWORDS = ["tratado", "acuerdo", "paz", "alianza", "capitulaciones", "concordia"]

FOREIGN_TOKENS = [
    "alemán", "alemana", "alemania", "nazi", "británico", "británica", "inglés",
    "inglesa", "inglaterra", "estadounidense", "americano", "americana", "ee.uu",
    "eeuu", "francés", "francesa", "francia", "italiano", "italiana", "italia",
    "ruso", "rusa", "rusia", "soviético", "soviética", "urss", "japonés", "japonesa",
    "japón",
]

CULTURE_LOW_PRIORITY = [
    "premio", "premios", "concurso", "festival", "certamen", "programa de radio",
    "programa de televisión", "radio", "televisión", "serie", "película", "cine",
    "novela", "poeta", "cantante", "músico", "discográfica", "disco", "álbum",
    "single",
]

TW_API_KEY = os.getenv("TWITTER_API_KEY", "")
TW_API_SECRET = os.getenv("TWITTER_API_SECRET", "")
TW_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN", "")
TW_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")
TW_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN", "")

USER_AGENT = "Efemerides_Imp_Bot/1.0"

client = OpenAI()

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


def choose_best_event(events):
    if not events:
        return None
    for ev in events:
        compute_score(ev)
    return max(events, key=lambda e: e["score"])


# -------------------------
# IMÁGENES: versión pulida
# -------------------------

def extract_name_queries(text):
    names = []

    battle_patterns = [
        r"(Batalla de [A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ\s\-]+)",
        r"(Guerra de [A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ\s\-]+)",
        r"(Sitio de [A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ\s\-]+)",
        r"(Tratado de [A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ\s\-]+)",
        r"(Paz de [A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ\s\-]+)",
        r"(Capitulación de [A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ\s\-]+)"
    ]

    for pat in battle_patterns:
        for m in re.finditer(pat, text):
            candidate = m.group(1).strip()
            if candidate not in names:
                names.append(candidate)

    pattern = re.compile(
        r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+"
        r"(?:\s+de\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)*"
        r"(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)*)"
    )

    raw_names = pattern.findall(text)

    for name in raw_names:
        name = name.strip()
        parts = name.split()
        if len(parts) <= 1:
            continue
        if name not in names:
            names.append(name)

    return names


def fetch_wikipedia_image_url(event):
    headers = {"User-Agent": USER_AGENT}
    base_api = "https://es.wikipedia.org/w/api.php"

    names = extract_name_queries(event["text"])
    print("Nombres detectados:", names)

    if not names:
        print("No imagen lógica")
        return None

    for name in names:
        try:
            params_search = {
                "action": "query",
                "format": "json",
                "list": "search",
                "srsearch": name,
                "srlimit": 1,
            }
            r = requests.get(base_api, params=params_search, headers=headers, timeout=15)
            r.raise_for_status()
            data = r.json()
            results = data.get("query", {}).get("search", [])
            if not results:
                continue

            page_title = results[0].get("title")
            if not page_title:
                continue

            params_pageimg = {
                "action": "query",
                "format": "json",
                "prop": "pageimages",
                "piprop": "original|thumbnail",
                "pithumbsize": 1200,
                "titles": page_title,
            }
            r2 = requests.get(base_api, params=params_pageimg, headers=headers, timeout=15)
            r2.raise_for_status()
            data2 = r2.json()
            pages = data2.get("query", {}).get("pages", {})
            for _, page in pages.items():
                original = page.get("original", {})
                thumbnail = page.get("thumbnail", {})
                img_url = original.get("source") or thumbnail.get("source")
                if img_url and "upload.wikimedia.org" in img_url:
                    print("Imagen buena:", img_url)
                    return img_url

        except Exception:
            pass

    print("Sin imagen adecuada")
    return None


def download_image(url, filename="tweet_image.jpg"):
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(url, headers=headers, timeout=25)
    r.raise_for_status()
    with open(filename, "wb") as f:
        f.write(r.content)
    return filename


# -----------------
# Texto OpenAI
# -----------------

def generate_headline_tweet(today_year, today_month_name, today_day, event):
    today_str = f"{today_day} de {today_month_name} de {today_year}"
    event_year = event["year"]
    event_text = event["text"]
    hashtags = " ".join(DEFAULT_HASHTAGS)

    prompt_user = f"""
Fecha de hoy: {today_str}.
Efeméride seleccionada (año {event_year}):

\"\"\"{event_text}\"\"\"


Escribe UN SOLO tuit:

"🇪🇸 {today_str}: En tal día como hoy del año {event_year}, ... {hashtags}"

Reglas:
- Máximo 260 caracteres.
- Debe empezar EXACTAMENTE por: "🇪🇸 {today_str}: En tal día como hoy del año {event_year},"
- Estilo divulgativo español.
- Sin emojis adicionales, sin URLs, sin saltos de línea.
"""

    completion = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "Eres un divulgador español."},
            {"role": "user", "content": prompt_user},
        ],
        temperature=0.4,
        max_tokens=200,
    )

    text = completion.choices[0].message.content.strip()

    prefix = f"🇪🇸 {today_str}: En tal día como hoy del año {event_year},"
    if not text.startswith(prefix):
        core_desc = event_text
        if len(core_desc) > 150:
            core_desc = core_desc[:147] + "..."
        text = f"{prefix} {core_desc} {hashtags}"

    if len(text) > 275:
        text = text[:272] + "..."

    return text


def generate_followup_tweets(today_year, today_month_name, today_day, event):
    today_str = f"{today_day} de {today_month_name} de {today_year}"
    event_year = event["year"]
    event_text = event["text"]

    prompt_user = f"""
Escribe entre 1 y 5 tuits de un hilo sobre:

\"\"\"{event_text}\"\"\"

Reglas:
- Máx 260 caracteres por tuit.
- NO hashtags, NO emojis.
- NO repetir la frase del tuit titular.
Devuélvelos SOLO en JSON: ["...", "..."]
"""

    completion = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "Eres un divulgador español."},
            {"role": "user", "content": prompt_user},
        ],
        temperature=0.6,
        max_tokens=400,
    )

    raw = completion.choices[0].message.content.strip()

    try:
        data = json.loads(raw)
        tweets = [t[:275] for t in data if isinstance(t, str)]
    except:
        tweets = []

    return tweets[:5]


def get_twitter_client_and_api():
    if not (TW_API_KEY and TW_API_SECRET and TW_ACCESS_TOKEN and TW_ACCESS_SECRET and TW_BEARER_TOKEN):
        raise RuntimeError("Faltan claves de Twitter/X.")

    client_tw = tweepy.Client(
        consumer_key=TW_API_KEY,
        consumer_secret=TW_API_SECRET,
        access_token=TW_ACCESS_TOKEN,
        access_token_secret=TW_ACCESS_SECRET,
        bearer_token=TW_BEARER_TOKEN,
    )

    auth = tweepy.OAuth1UserHandler(
        TW_API_KEY, TW_API_SECRET, TW_ACCESS_TOKEN, TW_ACCESS_SECRET
    )
    api_v1 = tweepy.API(auth)

    return client_tw, api_v1


def post_thread(headline, followups, event):
    client_tw, api_v1 = get_twitter_client_and_api()

    media_ids = None
    try:
        img_url = fetch_wikipedia_image_url(event)
        if img_url:
            img_path = download_image(img_url)
            media = api_v1.media_upload(img_path)
            media_ids = [media.media_id_string]
    except:
        media_ids = None

    if media_ids:
        resp = client_tw.create_tweet(text=headline, media_ids=media_ids)
    else:
        resp = client_tw.create_tweet(text=headline)

    tweet_id = resp.data.get("id")
    parent_id = tweet_id

    for t in followups:
        try:
            resp = client_tw.create_tweet(text=t, in_reply_to_tweet_id=parent_id)
            parent_id = resp.data.get("id")
        except:
            break


def main():
    today_year, today_month, today_day, today_month_name = today_info()

    try:
        events = fetch_hoyenlahistoria_events()
    except:
        return

    if not events:
        return

    best = choose_best_event(events)
    if not best:
        return

    try:
        headline = generate_headline_tweet(today_year, today_month_name, today_day, best)
    except:
        return

    try:
        followups = generate_followup_tweets(today_year, today_month_name, today_day, best)
    except:
        followups = []

    try:
        post_thread(headline, followups, best)
    except:
        pass


if __name__ == "__main__":
    main()
