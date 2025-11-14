import os
import datetime
import time
import json

import requests
import pytz
import tweepy
from openai import OpenAI

# =========================
# CONFIGURACIÓN GENERAL
# =========================

TZ = "Europe/Madrid"

DEFAULT_HASHTAGS = ["#TalDiaComoHoy", "#España", "#HistoriaDeEspaña", "#Efemérides"]

KEYWORDS_PRIORITY = [
    "Armada", "Descubrimiento", "Reyes Católicos", "Imperio", "Monarquía Hispánica",
    "Magallanes", "Elcano", "Lepanto", "América", "Pacífico", "Galeón", "Naval",
    "Ciencia", "Cultural", "Constitución", "Exploración", "Cartagena de Indias",
    "Sevilla", "Madrid", "Toledo", "Granada", "Castilla", "Aragón", "España"
]

MESES_ES = {
    1: "enero",
    2: "febrero",
    3: "marzo",
    4: "abril",
    5: "mayo",
    6: "junio",
    7: "julio",
    8: "agosto",
    9: "septiembre",
    10: "octubre",
    11: "noviembre",
    12: "diciembre",
}

USER_AGENT = "Efemerides_Imp_Bot/1.0 (https://github.com/efemeridesesp/tal-dia-como-hoy-es)"

# Claves de X (Twitter)
TWITTER_API_KEY = os.getenv("TWITTER_API_KEY", "")
TWITTER_API_SECRET = os.getenv("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN", "")

# Cliente OpenAI (usa OPENAI_API_KEY del entorno)
client = OpenAI()


# =========================
# FUNCIONES DE FECHA
# =========================

def today_parts():
    tz = pytz.timezone(TZ)
    now = datetime.datetime.now(tz)
    return now.year, now.month, now.day


def fecha_larga_hoy():
    year, month, day = today_parts()
    return f"{day} de {MESES_ES[month]} de {year}", year, month, day


# =========================
# RED CON REINTENTOS
# =========================

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


# =========================
# WIKIDATA + WIKIPEDIA
# =========================

def fetch_wikidata_events(month: int, day: int):
    """
    Devuelve eventos de Wikidata (lista de dicts) filtrados por España.
    """
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
    """
    Usa la API de Wikipedia para obtener un resumen en español.
    """
    if not title_or_url:
        return {"title": "", "extract": "", "url": ""}

    title = title_or_url
    if "wikipedia.org" in title_or_url:
        title = title_or_url.rstrip("/").split("/")[-1]

    url = f"https://es.wikipedia.org/api/rest_v1/page/summary/{title}"

    try:
        r = safe_request(url, headers={"User-Agent": USER_AGENT})
    except Exception:
        return {"title": "", "extract": "", "url": ""}

    j = r.json()
    return {
        "title": j.get("title", ""),
        "extract": j.get("extract", ""),
        "url": j.get("content_urls", {}).get("desktop", {}).get("page", ""),
    }


# =========================
# OPENAI → TWEET FORMATEADO
# =========================

def generate_openai_tweet(fecha_hoy_str: str, event_year: int, event_label: str,
                          summary_text: str, wikipedia_url: str) -> str:
    """
    Usa OpenAI solo para redactar el texto, NO para decidir la efeméride.
    El hecho histórico viene de Wikidata/Wikipedia.
    Formato obligatorio:
    '{fecha_hoy}: En tal día como hoy del año XXXX, ...'
    """
    hashtags = " ".join(DEFAULT_HASHTAGS)

    resumen_corto = summary_text
    if resumen_corto and len(resumen_corto) > 400:
        resumen_corto = resumen_corto[:400] + "…"

    prompt = f"""
Vas a redactar UN ÚNICO tweet de efeméride sobre historia de España.

Los datos históricos SON FIJOS y NO puedes cambiarlos:
- Fecha de hoy: {fecha_hoy_str}
- Año del suceso: {event_year}
- Nombre del evento: {event_label}
- Descripción/resumen (puedes condensarla): {resumen_corto}
- Enlace de referencia (puedes omitirlo si no cabe): {wikipedia_url}

Formato OBLIGATORIO DEL TWEET (respétalo al 100%):
- Debe comenzar EXACTAMENTE así (incluyendo dos puntos y espacio):
  "{fecha_hoy_str}: En tal día como hoy del año {event_year},"
- Después de esa frase, en una sola oración breve, explica qué ocurrió.
- Termina el tweet con EXACTAMENTE estos hashtags y en este orden:
  {hashtags}
- No añadas otros hashtags.
- No añadas más emojis (puedes mantener solo la bandera inicial si la añades tú, pero en este caso NO la usamos porque ya empieza con la fecha).
- No añadas comillas ni texto fuera del propio tweet.
- Todo el tweet debe tener como máximo 260 caracteres.

Tu tarea:
- Condensa el hecho histórico en una frase breve, sin cambiar el año ni el sentido del evento.
- Usa un tono divulgativo y sobrio (sin panfleto).

Devuélveme SOLO el texto del tweet, listo para publicar.
"""

    completion = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "Eres un historiador de España y community manager. "
                    "Nunca alteras los datos históricos proporcionados, solo los redactas."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    )

    tweet = completion.choices[0].message.content.strip()

    # Seguridad extra: recortar a 275 por si acaso
    if len(tweet) > 275:
        tweet = tweet[:272] + "…"

    # Comprobamos que respeta el prefijo
    prefix = f"{fecha_hoy_str}: En tal día como hoy del año {event_year},"
    if not tweet.startswith(prefix):
        print("❌ OpenAI no respetó el formato, no se publicará.")
        print("TWEET GENERADO:", tweet)
        return ""

    return tweet


# =========================
# PUBLICAR EN X (TWITTER)
# =========================

def post_to_twitter(text: str):
    if not text:
        print("⚠️ Texto vacío, no se publica.")
        return

    print(
        "DEBUG Twitter keys present:",
        bool(TWITTER_API_KEY),
        bool(TWITTER_API_SECRET),
        bool(TWITTER_ACCESS_TOKEN),
        bool(TWITTER_ACCESS_SECRET),
        bool(TWITTER_BEARER_TOKEN),
    )

    client_tw = tweepy.Client(
        consumer_key=TWITTER_API_KEY,
        consumer_secret=TWITTER_API_SECRET,
        access_token=TWITTER_ACCESS_TOKEN,
        access_token_secret=TWITTER_ACCESS_SECRET,
        bearer_token=TWITTER_BEARER_TOKEN,
    )

    resp = client_tw.create_tweet(text=text)
    print("DEBUG create_tweet response:", resp)


# =========================
# MAIN
# =========================

def main():
    fecha_hoy_str, year_today, month, day = fecha_larga_hoy()
    fecha_corta_str = f"{day:02d}/{month:02d}"

    print(f"📅 Hoy es {fecha_hoy_str} (día/mes: {fecha_corta_str})")

    # 1) Obtener eventos de Wikidata
    try:
        events = fetch_wikidata_events(month, day)
    except Exception as e:
        print("❌ Error serio con Wikidata:", e)
        return

    if not events:
        print("ℹ️ No se han encontrado efemérides en Wikidata para hoy. No se publica nada.")
        return

    best = choose_best(events)
    print("✅ Evento elegido de Wikidata:", json.dumps(best, ensure_ascii=False))

    # 2) Año del suceso
    try:
        event_year = int(best["date"][:4])
    except Exception:
        print("❌ No se ha podido extraer el año del evento. No se publica.")
        return

    # 3) Resumen de Wikipedia (si hay URL)
    summary = {"title": "", "extract": "", "url": ""}
    if best.get("wp_es"):
        summary = fetch_wikipedia_summary(best["wp_es"])

    event_label = summary["title"] or best["label"]
    summary_text = summary["extract"]
    wikipedia_url = summary["url"]

    # 4) Generar tweet con OpenAI
    try:
        tweet = generate_openai_tweet(
            fecha_hoy_str=fecha_hoy_str,
            event_year=event_year,
            event_label=event_label,
            summary_text=summary_text,
            wikipedia_url=wikipedia_url,
        )
    except Exception as e:
        print("❌ Error generando tweet con OpenAI:", e)
        return

    if not tweet:
        print("⚠️ No se generó un tweet válido. No se publica.")
        return

    print("✅ Tweet generado:")
    print(tweet)

    # 5) Publicar
    try:
        post_to_twitter(tweet)
        print("✅ Tweet publicado correctamente.")
    except Exception as e:
        print("❌ Error publicando el tweet en X:", e)


if __name__ == "__main__":
    main()
