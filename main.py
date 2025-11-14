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

# Teatro en territorio español
SPANISH_THEATRE_TOKENS = [
    "málaga", "cádiz", "cartagena", "cartagena de indias",
    "barcelona", "valencia", "bilbao", "santander", "la coruña",
    "ceuta", "melilla", "baleares", "canarias",
]

# Palabras militares
MILITARY_KEYWORDS = [
    "batalla", "guerra", "combate", "frente",
    "asedio", "sitio", "conquista", "derrota", "victoria", "alzamiento",
    "revolución", "levantamiento", "sublevación", "bombardeo", "invasión",
    "ejército", "toma", "capitulación", "ofensiva", "defensiva",
]

# Diplomacia / acuerdos
DIPLO_KEYWORDS = [
    "tratado", "acuerdo", "paz", "alianza",
    "capitulaciones", "concordia",
]

# Nacionalidades extranjeras
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

# Cosas de cultura/pop que penalizamos
CULTURE_LOW_PRIORITY = [
    "premio", "premios", "concurso", "festival", "certamen",
    "programa de radio", "programa de televisión", "radio", "televisión",
    "serie", "película", "cine", "novela", "poeta", "cantante", "músico",
    "discográfica", "disco", "álbum", "single"
]

# Claves de X desde el entorno
TW_API_KEY = os.getenv("TWITTER_API_KEY", "")
TW_API_SECRET = os.getenv("TWITTER_API_SECRET", "")
TW_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN", "")
TW_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")
TW_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN", "")

USER_AGENT = "Efemerides_Imp_Bot/1.0 (https://github.com/efemeridesesp/tal-dia-como-hoy-es)"

# Cliente OpenAI
client = OpenAI()


# ----------------- Fecha ----------------- #

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


# ----------------- Scoring imperial ----------------- #

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


# ----------------- IMÁGENES: Wikipedia (imagen principal de la página) ----------------- #

def extract_name_queries(text):
    """
    Saca posibles nombres propios compuestos del texto:
    - "Catalina de Aragón"
    - "Arturo Tudor"
    - "Reyes Católicos"
    etc.
    Devuelve una lista de nombres.
    """
    pattern = re.compile(
        r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+"
        r"(?:\s+de\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)*"
        r"(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)*)"
    )

    raw_names = pattern.findall(text)
    names = []

    generic_single_words = {
        "El", "La", "Los", "Las",
        "Rey", "Reina", "Reyes", "Príncipe", "Princesa",
        "Guerra", "Batalla", "Revolución", "Constitución",
        "Partido", "Imperio", "Monarquía",
    }

    for name in raw_names:
        name = name.strip().strip(",.;:()")
        if not name:
            continue
        parts = name.split()
        if len(parts) == 1 and parts[0] in generic_single_words:
            continue
        if name not in names:
            names.append(name)

    return names


def fetch_wikipedia_image_url(event):
    """
    Intenta obtener la imagen principal de Wikipedia en español
    para alguno de los nombres propios detectados en el evento.
    - Usa la API de es.wikipedia.org para buscar la página.
    - Luego pide 'pageimages' para obtener la imagen principal.
    Si no encuentra nada para ningún nombre, devuelve None.
    """
    headers = {"User-Agent": USER_AGENT}
    base_api = "https://es.wikipedia.org/w/api.php"

    names = extract_name_queries(event["text"])
    print("Nombres propios detectados en el evento:", names)

    if not names:
        print("ℹ️ No se han detectado nombres propios claros; no se intentará imagen Wikipedia.")
        return None

    for name in names:
        try:
            # 1) Buscar página en Wikipedia para ese nombre
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

            print(f"Intentando obtener imagen principal de Wikipedia para página: {page_title!r}")

            # 2) Pedir la imagen principal de esa página
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
                if img_url:
                    print(f"✅ Imagen principal encontrada en Wikipedia para {page_title!r}: {img_url}")
                    return img_url
        except Exception as e:
            print(f"⚠️ Error consultando Wikipedia para nombre {name!r}:", e)

    print("ℹ️ No se ha encontrado imagen principal adecuada en Wikipedia.")
    return None


def download_image(url, filename="tweet_image.jpg"):
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(url, headers=headers, timeout=25)
    r.raise_for_status()
    with open(filename, "wb") as f:
        f.write(r.content)
    return filename


# ----------------- Texto con OpenAI ----------------- #

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
                    t = item.strip()
                    if not t:
                        continue
                    if len(t) > 275:
                        t = t[:272].rstrip() + "..."
                    tweets.append(t)
    except Exception as e:
        print("⚠️ No se ha podido parsear el JSON de followups:", e)
        print("Contenido bruto devuelto por OpenAI:")
        print(raw)
        tweets = []

    if len(tweets) > 5:
        tweets = tweets[:5]

    return tweets


# ----------------- Twitter/X ----------------- #

def get_twitter_client_and_api():
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
            print(f"✅ Imagen subida a X con media_id={media.media_id_string}")
        else:
            print("ℹ️ No se adjuntará imagen en el tuit titular.")
    except Exception as e:
        print("⚠️ Error subiendo imagen a X, se publicará sin imagen:", e)
        media_ids = None

    if media_ids:
        resp = client_tw.create_tweet(text=headline, media_ids=media_ids)
    else:
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


# ----------------- Main ----------------- #

def main():
    today_year, today_month, today_day, today_month_name = today_info()
    print(f"Hoy es {today_day}/{today_month}/{today_year} ({today_month_name}).")

    try:
        events = fetch_hoyenlahistoria_events()
        print(f"Se han encontrado {len(events)} eventos en hoyenlahistoria.com")
    except Exception as e:
        print("❌ Error obteniendo eventos de hoyenlahistoria.com:", e)
        print("No se publicará ningún tuit hoy.")
        return

    if not events:
        print("No hay eventos disponibles para hoy. No se publicará tuit.")
        return

    best = choose_best_event(events)
    if not best:
        print("No se ha podido seleccionar una efeméride adecuada. No se publicará tuit.")
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

    try:
        headline = generate_headline_tweet(today_year, today_month_name, today_day, best)
    except Exception as e:
        print("❌ Error al generar el tuit titular con OpenAI:", e)
        return

    print("Tuit titular generado:")
    print(headline)
    print(f"Largo: {len(headline)} caracteres")

    try:
        followups = generate_followup_tweets(today_year, today_month_name, today_day, best)
    except Exception as e:
        print("⚠️ Error generando los tuits de hilo con OpenAI:", e)
        followups = []

    print(f"Se han generado {len(followups)} tuits adicionales para el hilo.")
    for i, t in enumerate(followups, start=2):
        print(f"[Tuit {i}] {t} (len={len(t)})")

    try:
        post_thread(headline, followups, best)
        print("✅ Hilo publicado correctamente.")
    except Exception as e:
        print("❌ Error publicando el hilo en Twitter/X:", e)
        raise


if __name__ == "__main__":
    main()
