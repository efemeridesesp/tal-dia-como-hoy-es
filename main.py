import os
import requests
import datetime
import pytz
import time

from openai import OpenAI
import tweepy

TZ = "Europe/Madrid"
DEFAULT_HASHTAGS = ["#TalDiaComoHoy", "#España", "#HistoriaDeEspaña", "#Efemérides"]
KEYWORDS_PRIORITY = [
    "Armada", "Descubrimiento", "Reyes Católicos", "Imperio", "Monarquía Hispánica",
    "Magallanes", "Elcano", "Lepanto", "América", "Pacífico", "Galeón", "Naval",
    "Ciencia", "Cultural", "Constitución", "Exploración", "Cartagena de Indias",
    "Sevilla", "Madrid", "Toledo", "Granada", "Castilla", "Aragón", "España"
]

TW_API_KEY = os.getenv("TWITTER_API_KEY", "")
TW_API_SECRET = os.getenv("TWITTER_API_SECRET", "")
TW_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN", "")
TW_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")
TW_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

USER_AGENT = "Efemerides_Imp_Bot/1.0 (https://github.com/efemeridesesp/tal-dia-como-hoy-es)"

client = OpenAI()


def today_parts():
    tz = pytz.timezone(TZ)
    now = datetime.datetime.now(tz)
    return now.year, now.month, now.day


def safe_request(url, params=None, headers=None, tries=5, wait=3):
    if headers is None:
        headers = {}
    if "User-Agent" not in headers:
        headers["User-Agent"] = USER_AGENT

    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=15)
            r.raise_for_status()
            return r
        except Exception as e:
            if i < tries - 1:
                print(f"⚠️ Error al conectar ({e}). Reintentando en {wait} segundos...")
                time.sleep(wait)
            else:
                raise


def fetch_wikidata_events(month: int, day: int):
    endpoint = "https://query.wikidata.org/sparql"
    query = f"""
    SELECT ?item ?itemLabel ?eventDate ?wpES WHERE {{
      ?item wdt:P31/wdt:P279* wd:Q1190554.
      ?item wdt:P585 ?eventDate.
      FILTER(MONTH(?eventDate) = {month} && DAY(?eventDate) = {day})
      OPTIONAL {{ ?item wdt:P17 ?country . }}
      OPTIONAL {{ ?item wdt:P495 ?origin . }}
      OPTIONAL {{ ?item wdt:P276 ?place . }}
      BIND(
        IF( (?country = wd:Q29) || (?origin = wd:Q29) || EXISTS {{
            ?place wdt:P17 wd:Q29
        }}, 1, 0) as ?isSpanish
      )
      FILTER(?isSpanish = 1)
      OPTIONAL {{
        ?wpES schema:about ?item ;
              schema:isPartOf <https://es.wikipedia.org/> .
      }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "es,en". }}
    }}
    ORDER BY DESC(?eventDate)
    LIMIT 20
    """
    headers = {
        "Accept": "application/sparql-results+json",
        "User-Agent": USER_AGENT,
    }

    r = safe_request(endpoint, params={"query": query}, headers=headers)
    data = r.json()["results"]["bindings"]

    events = []
    for b in data:
        events.append({
            "label": b.get("itemLabel", {}).get("value", ""),
            "date": b.get("eventDate", {}).get("value", ""),
            "wp_es": b.get("wpES", {}).get("value", ""),
            "qid": b.get("item", {}).get("value", "").split("/")[-1],
        })
    return events


def score_event(ev):
    score = 0
    label = ev["label"]
    for i, kw in enumerate(KEYWORDS_PRIORITY[::-1], start=1):
        if kw.lower() in label.lower():
            score += i
    try:
        year = int(ev["date"][:4])
        score += max(0, (year - 1500) / 200.0)
    except Exception:
        pass
    return score


def choose_best(events):
    if not events:
        return None
    return sorted(events, key=score_event, reverse=True)[0]


def fetch_wikipedia_summary(title_or_url: str):
    title = title_or_url
    if "wikipedia.org" in title_or_url:
        title = title_or_url.rstrip("/").split("/")[-1]

    url = f"https://es.wikipedia.org/api/rest_v1/page/summary/{title}"

    try:
        r = safe_request(url, headers={"User-Agent": USER_AGENT})
    except Exception:
        return None

    j = r.json()
    return {
        "title": j.get("title"),
        "extract": j.get("extract"),
        "url": j.get("content_urls", {}).get("desktop", {}).get("page"),
    }


def compose_post(ev, summary):
    anio = None
    if ev and ev.get("date"):
        try:
            anio = int(ev["date"][:4])
        except Exception:
            pass

    titulo = summary["title"] if summary and summary.get("title") else ev["label"]
    if anio:
        base = f"🇪🇸 Tal día como hoy, en {anio}, {titulo}."
    else:
        base = f"🇪🇸 Tal día como hoy: {titulo}."

    extra = ""
    if summary and summary.get("extract"):
        extract = summary["extract"]
        if len(extract) > 220:
            extract = extract[:220] + "…"
        extra = f"\n{extract}"

    hashtags = " ".join(DEFAULT_HASHTAGS)
    url = summary.get("url") if summary and summary.get("url") else ""
    tail = f"\n\n{hashtags}\nFuente: {url}" if url else f"\n\n{hashtags}"

    return (base + extra + tail)[:275]


def compose_fallback_post(month: int, day: int):
    fecha = f"{day:02d}/{month:02d}"
    text = (
        f"🇪🇸 Tal día como hoy ({fecha}) seguimos completando nuestro archivo de hazañas del "
        f"Imperio Español. Hoy no hemos podido recuperar una efeméride concreta por problemas "
        f"técnicos con las fuentes, pero la historia de España no descansa.\n\n"
        + " ".join(DEFAULT_HASHTAGS)
    )
    return text[:275]


def generate_openai_tweet(ev, summary, month: int, day: int) -> str:
    anio = None
    if ev and ev.get("date"):
        try:
            anio = int(ev["date"][:4])
        except Exception:
            pass

    titulo = summary["title"] if summary and summary.get("title") else ev["label"]
    extract = summary.get("extract") if summary else ""
    url = summary.get("url") if summary else ""
    hashtags = " ".join(DEFAULT_HASHTAGS)

    fecha_str = f"{day:02d}/{month:02d}"
    anio_str = str(anio) if anio else "año no determinado"

    prompt = f"""
Eres community manager experto en historia de España.
Escribe UN ÚNICO tweet en español para X sobre una efeméride.

Datos:
- Fecha: {fecha_str}
- Año del evento: {anio_str}
- Título: {titulo}
- Descripción breve tomada de Wikipedia: {extract}
- Enlace de referencia (si lo ves útil): {url}

Instrucciones para el tweet:
- Empieza con "🇪🇸 Tal día como hoy".
- Tono: divulgativo, interesante y ligeramente épico, pero sin parecer panfleto.
- Incluye el enlace de referencia solo si cabe de forma natural.
- Añade exactamente estos hashtags al final y en este orden: {hashtags}
- Máximo 275 caracteres en TOTAL (contando espacios, enlace y hashtags).
- No añadas explicaciones, ni comillas, ni notas externas. Devuelve SOLO el texto del tweet listo para publicar.
"""

    completion = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "Eres un experto en redes sociales de historia de España."},
            {"role": "user", "content": prompt},
        ],
    )

    tweet = completion.choices[0].message.content.strip()

    if len(tweet) > 275:
        tweet = tweet[:272] + "…"

    return tweet


def post_to_twitter(text):
    print(
        "DEBUG Twitter keys present:",
        bool(TW_API_KEY),
        bool(TW_API_SECRET),
        bool(TW_ACCESS_TOKEN),
        bool(TW_ACCESS_SECRET),
        bool(TW_BEARER_TOKEN),
    )

    # Cliente v2: create_tweet (POST /2/tweets)
    client_tw = tweepy.Client(
        consumer_key=TW_API_KEY,
        consumer_secret=TW_API_SECRET,
        access_token=TW_ACCESS_TOKEN,
        access_token_secret=TW_ACCESS_SECRET,
        bearer_token=TW_BEARER_TOKEN,
    )

    resp = client_tw.create_tweet(text=text)
    print("DEBUG create_tweet response:", resp)


def notify_telegram(msg):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg})


def main():
    _, month, day = today_parts()

    use_fallback = False
    events = []
    try:
        events = fetch_wikidata_events(month, day)
    except Exception as e:
        print("⚠️ Error serio con Wikidata:", e)
        use_fallback = True

    if not events:
        print("No se han encontrado efemérides en Wikidata para hoy.")
        use_fallback = True

    if use_fallback:
        text = compose_fallback_post(month, day)
    else:
        best = choose_best(events)
        summary = fetch_wikipedia_summary(best["wp_es"]) if best.get("wp_es") else None
        if not summary:
            summary = {"title": best["label"], "extract": "", "url": ""}

        try:
            text = generate_openai_tweet(best, summary, month, day)
            print("✅ Tweet generado con OpenAI.")
        except Exception as e:
            print("⚠️ Error usando OpenAI, usando versión clásica:", e)
            text = compose_post(best, summary)

    try:
        post_to_twitter(text)
        print("Tweet posted.")
        notify_telegram(f"✅ Tweet publicado:\n\n{text}")
    except Exception as e:
        print("❌ Error posting to Twitter:", e)
        notify_telegram(f"❌ Error al publicar tweet: {e}")
        raise


if __name__ == "__main__":
    main()
