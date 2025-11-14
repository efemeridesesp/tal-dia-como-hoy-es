import os
import datetime
import pytz
import json

import tweepy
from openai import OpenAI

# Zona horaria
TZ = "Europe/Madrid"

# Hashtags fijos
DEFAULT_HASHTAGS = ["#TalDiaComoHoy", "#España", "#HistoriaDeEspaña", "#Efemérides"]

# Claves de Twitter desde secrets
TWITTER_API_KEY = os.getenv("TWITTER_API_KEY", "")
TWITTER_API_SECRET = os.getenv("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN", "")

# Cliente OpenAI (lee OPENAI_API_KEY de entorno)
client = OpenAI()


def today_parts():
    """Devuelve año, mes y día actuales en la TZ indicada."""
    tz = pytz.timezone(TZ)
    now = datetime.datetime.now(tz)
    return now.year, now.month, now.day


def generate_openai_payload(month: int, day: int) -> dict:
    """
    Pide a OpenAI que devuelva un JSON con:
    {
      "status": "OK" | "NO_EVENT",
      "tweet": "...",
      "event_day": 14,
      "event_month": 11
    }
    """
    fecha_str = f"{day:02d}/{month:02d}"
    hashtags = " ".join(DEFAULT_HASHTAGS)

    prompt = f"""
Vas a devolver SIEMPRE un JSON válido de una sola línea, sin texto adicional.

Formato del JSON (campos obligatorios):
{{
  "status": "OK" o "NO_EVENT",
  "tweet": "texto del tweet",
  "event_day": número entero del día del suceso,
  "event_month": número entero del mes del suceso
}}

Instrucciones:

1) Tu tarea es buscar una efeméride REAL de la historia de España que ocurriera EXACTAMENTE el día {fecha_str} (mismo día y mes).
2) Si NO encuentras ninguna efeméride real, relevante y verificable que ocurriera EXACTAMENTE en ese día y mes:
   - Devuelve:
     {{
       "status": "NO_EVENT",
       "tweet": "",
       "event_day": 0,
       "event_month": 0
     }}
   - NO inventes nada.

3) Si SÍ existe una efeméride:
   - "status" debe ser "OK".
   - "event_day" y "event_month" deben reflejar el día y mes EXACTOS del suceso.
   - "tweet" debe ser un único tweet en español que:
     - Empiece EXACTAMENTE así (incluyendo bandera, espacio y coma):
       "🇪🇸 En tal día como hoy del año XXXX,"
       donde XXXX es el año real del suceso.
     - Después de esa frase, en una sola oración breve, explique qué sucedió.
     - Termina el tweet con EXACTAMENTE estos hashtags y en este orden:
       {hashtags}
     - No añadas otros hashtags ni más emojis.
     - No añadas comillas, ni notas, ni texto fuera del tweet.
     - El tweet completo (incluyendo hashtags y espacios) debe tener como máximo 260 caracteres.

Devuelve ÚNICAMENTE el JSON, sin texto antes ni después.
"""

    completion = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "Eres un historiador de España muy estricto con las fechas y un community manager. "
                    "Nunca inventas efemérides. Si no hay efeméride exacta, indicas NO_EVENT."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    )

    raw = completion.choices[0].message.content.strip()
    print("RAW FROM OPENAI:", raw)

    try:
        data = json.loads(raw)
    except Exception as e:
        print("❌ Error parseando JSON de OpenAI:", e)
        return {"status": "NO_EVENT", "tweet": "", "event_day": 0, "event_month": 0}

    # Normalizamos por si falta algún campo
    data.setdefault("status", "NO_EVENT")
    data.setdefault("tweet", "")
    data.setdefault("event_day", 0)
    data.setdefault("event_month", 0)

    return data


def post_to_twitter(text: str):
    """Publica el tweet usando la X API v2 (create_tweet)."""
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


def main():
    _, month, day = today_parts()
    fecha_str = f"{day:02d}/{month:02d}"

    try:
        data = generate_openai_payload(month, day)
    except Exception as e:
        print("❌ Error llamando a OpenAI:", e)
        return

    status = str(data.get("status", "NO_EVENT"))
    tweet = str(data.get("tweet", "")).strip()
    event_day = int(data.get("event_day", 0))
    event_month = int(data.get("event_month", 0))

    print("PARSED FROM OPENAI:", data)

    # 1) Si no hay evento exacto → no publicamos
    if status != "OK":
        print(f"❌ OpenAI indica NO_EVENT para el {fecha_str}. No se publica nada.")
        return

    # 2) Comprobamos que la fecha del suceso coincide EXACTAMENTE
    if event_day != day or event_month != month:
        print(
            f"❌ Fecha devuelta por OpenAI ({event_day:02d}/{event_month:02d}) "
            f"no coincide con la fecha de hoy ({fecha_str}). No se publica."
        )
        return

    # 3) Comprobamos formato del tweet
    prefix = "🇪🇸 En tal día como hoy del año"
    if not tweet.startswith(prefix):
        print(f"❌ El tweet no empieza por '{prefix}'. No se publica.")
        return

    # 4) Comprobamos longitud
    if len(tweet) > 280:
        print(f"❌ Tweet demasiado largo ({len(tweet)} caracteres). No se publica.")
        return

    # Si hemos llegado hasta aquí, publicamos
    try:
        post_to_twitter(tweet)
        print("✅ Tweet publicado correctamente.")
    except Exception as e:
        print("❌ Error publicando el tweet en X:", e)


if __name__ == "__main__":
    main()
