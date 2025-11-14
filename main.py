import os
import requests
import datetime
import pytz
import re
from bs4 import BeautifulSoup
from openai import OpenAI

# Zona horaria de referencia
TZ = "Europe/Madrid"

# Hashtags fijos
DEFAULT_HASHTAGS = ["#TalDiaComoHoy", "#España", "#HistoriaDeEspaña", "#Efemérides"]

# Palabras clave para priorizar eventos "imperiales/españoles"
KEYWORDS_PRIORITY = [
    "Imperio español", "Reyes Católicos", "Armada", "Flota", "Galeón",
    "América", "Virreinato", "Nueva España", "Filipinas", "Pacífico",
    "batalla", "victoria", "derrota", "guerra", "naval",
    "Carlos V", "Felipe II", "Felipe III", "Felipe IV",
    "Granada", "Castilla", "Aragón", "Toledo", "Sevilla", "Madrid",
    "España", "español", "española"
]

# Claves de X (Twitter) desde los secrets del repositorio
TW_API_KEY = os.getenv("TWITTER_API_KEY", "")
TW_API_SECRET = os.getenv("TWITTER_API_SECRET", "")
TW_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN", "")
TW_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")

USER_AGENT = "Efemerides_Imp_Bot/1.0 (https://github.com/efemeridesesp/tal-dia-como-hoy-es)"

# Cliente de OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
client = OpenAI(api_key=OPENAI_API_KEY)


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

    # Recorremos todos los <li> y nos quedamos con los que empiezan por un año
    for li in soup.find_all("li"):
        text = " ".join(li.stripped_strings)
        if not text:
            continue

        # Busca "AAAA ..." o "AAAA a.C. ..."
        m = re.match(r"^(\d+)\s*(a\.C\.)?\s*(.*)", text)
        if not m:
            continue

        year_str, era, rest = m.groups()
        try:
            year = int(year_str)
        except ValueError:
            continue

        # Si es a.C., lo dejamos como año negativo por si algún día lo quisieras usar
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


def score_event(ev):
    """
    Da una puntuación a cada evento según palabras clave y época histórica.
    Cuanto más "España / Imperio", más puntos.
    """
    text = ev["text"]
    year = ev["year"]
    t_low = text.lower()

    score = 0

    # Puntos por menciones explícitas a España
    if "españa" in t_low or "español" in t_low or "española" in t_low:
        score += 5

    # Palabras clave de prioridad
    for i, kw in enumerate(KEYWORDS_PRIORITY[::-1], start=1):
        if kw.lower() in t_low:
            score += i

    # Bonus por siglos "interesantes" (aprox. XV–XIX)
    if 1400 <= year <= 1899:
        score += 3

    return score


def choose_best_event(events):
    """
    Elige el mejor evento, priorizando los que tengan score > 0.
    Si todos son 0, elige el que más score tenga igualmente.
    """
    if not events:
        return None

    # Calculamos score de todos
    for ev in events:
        ev["score"] = score_event(ev)

    spanish_like = [e for e in events if e["score"] > 0]

    if spanish_like:
        candidates = spanish_like
    else:
        candidates = events  # si un día raro no hay nada español, usamos algo general

    best = max(candidates, key=lambda e: e["score"])
    return best


# ----------------- Generación de texto con OpenAI ----------------- #

def generate_openai_tweet(today_year, today_month_name, today_day, event):
    """
    Pide a OpenAI que redacte el tuit con el formato:

    '14 de noviembre de 2025: En tal día como hoy del año XXXX, ... #TalDiaComoHoy #España #HistoriaDeEspaña #Efemérides'
    """
    if not OPENAI_API_KEY:
        raise RuntimeError("Falta OPENAI_API_KEY en las variables de entorno.")

    today_str = f"{today_day} de {today_month_name} de {today_year}"
    event_year = event["year"]
    event_text = event["text"]

    hashtags = " ".join(DEFAULT_HASHTAGS)

    prompt_user = f"""
Fecha de hoy: {today_str}.
Efeméride seleccionada (año {event_year}) procedente de un listado de efemérides históricas:

\"\"\"{event_text}\"\"\"


Escribe UN SOLO tuit en español siguiendo EXACTAMENTE este formato:

\"{today_str}: En tal día como hoy del año {event_year}, ... {hashtags}\"

Reglas importantes:
- Máximo 260 caracteres en total (incluyendo los hashtags).
- Respeta el comienzo fijo: "{today_str}: En tal día como hoy del año {event_year},".
- Tono divulgativo y positivo, sin emojis, sin URLs y sin mencionar la fuente.
- No añadas más hashtags que estos cuatro ni cambies su texto: {hashtags}.
- No uses saltos de línea, todo debe ir en una sola frase.
"""

    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "Eres un divulgador de historia de España y del Imperio español. "
                    "Escribes tuits breves y claros respetando estrictamente el formato pedido."
                ),
            },
            {"role": "user", "content": prompt_user},
        ],
        temperature=0.4,
        max_tokens=180,
    )

    text = completion.choices[0].message.content.strip()

    # Por seguridad recortamos a 275 caracteres máximo
    if len(text) > 275:
        text = text[:272].rstrip() + "..."

    return text


# ----------------- Publicación en X (Twitter) ----------------- #

def post_to_twitter(text):
    """Publica el tuit usando Tweepy y OAuth1.0a (v1.1)."""
    import tweepy

    if not (TW_API_KEY and TW_API_SECRET and TW_ACCESS_TOKEN and TW_ACCESS_SECRET):
        raise RuntimeError("Faltan claves de Twitter/X en las variables de entorno.")

    auth = tweepy.OAuth1UserHandler(
        TW_API_KEY, TW_API_SECRET, TW_ACCESS_TOKEN, TW_ACCESS_SECRET
    )
    api = tweepy.API(auth)

    # Esto falla con 401 si algo está mal, lo cual nos viene bien para depurar
    api.verify_credentials()
    api.update_status(status=text)


# ----------------- Main ----------------- #

def main():
    today_year, today_month, today_day, today_month_name = today_info()

    print(f"Hoy es {today_day}/{today_month}/{today_year} ({today_month_name}).")

    # 1) Obtener eventos de hoy en la web
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

    # 2) Elegir el mejor evento
    best = choose_best_event(events)
    if not best:
        print("No se ha podido seleccionar una efeméride adecuada. No se publicará tuit.")
        return

    print("Evento elegido:")
    print(f"- Año: {best['year']}")
    print(f"- Texto: {best['text']}")
    print(f"- Score: {best.get('score', 'N/A')}")

    # 3) Generar el texto del tuit con OpenAI
    try:
        tweet_text = generate_openai_tweet(today_year, today_month_name, today_day, best)
    except Exception as e:
        print("❌ Error al generar el tuit con OpenAI:", e)
        raise

    print("Tuit generado:")
    print(tweet_text)
    print(f"Largo: {len(tweet_text)} caracteres")

    # 4) Publicar en X
    try:
        post_to_twitter(tweet_text)
        print("✅ Tuit publicado correctamente.")
    except Exception as e:
        print("❌ Error publicando en Twitter/X:", e)
        raise


if __name__ == "__main__":
    main()
