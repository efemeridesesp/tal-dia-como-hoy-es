import os
import datetime
import pytz

from openai import OpenAI
import tweepy

# Zona horaria para calcular la fecha de hoy
TZ = "Europe/Madrid"

# Hashtags fijos
DEFAULT_HASHTAGS = ["#TalDiaComoHoy", "#España", "#HistoriaDeEspaña", "#Efemérides"]

# Claves de Twitter (vienen de los secrets del workflow)
TW_API_KEY = os.getenv("TWITTER_API_KEY", "")
TW_API_SECRET = os.getenv("TWITTER_API_SECRET", "")
TW_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN", "")
TW_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")
TW_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN", "")

# Cliente de OpenAI (coge OPENAI_API_KEY de la variable de entorno)
client = OpenAI()


def today_parts():
    """Devuelve año, mes y día actuales en la TZ indicada."""
    tz = pytz.timezone(TZ)
    now = datetime.datetime.now(tz)
    return now.year, now.month, now.day


def generate_openai_tweet(month: int, day: int) -> str:
    """
    Pide a OpenAI que genere un único tweet de efeméride de historia de España
    para el día y mes indicados.
    Debe empezar con '🇪🇸 En tal día como hoy del año XXXX,' y respetar el límite
    de caracteres de X.
    """
    # Construimos una fecha legible para el prompt (solo día y mes)
    fecha_str = f"{day:02d}/{month:02d}"
    hashtags = " ".join(DEFAULT_HASHTAGS)

    prompt = f"""
Quiero que escribas UN ÚNICO tweet de efeméride sobre la historia de España.

Condiciones muy estrictas:

- Hoy es el día {fecha_str} (ignora el año actual).
- Elige tú un acontecimiento histórico real y relevante de la historia de España que ocurriera en esta fecha (no inventes).
- El tweet DEBE empezar EXACTAMENTE así (incluyendo bandera y coma):
"🇪🇸 En tal día como hoy del año XXXX,"
  donde XXXX es el año del suceso que hayas elegido.

- Después de esa frase, explica en una sola frase breve qué ocurrió.
- Al final del tweet, añade EXACTAMENTE estos hashtags y en este orden:
{hashtags}

- No añadas otros hashtags ni más emojis.
- No pongas comillas alrededor del texto.
- No añadas explicaciones adicionales, ni notas, ni contexto fuera del propio tweet.
- El resultado final (todo el texto del tweet, incluyendo hashtags y espacios) debe tener como máximo 260 caracteres.
  Si hace falta, resume al máximo para que quepa.

Devuélveme SOLO el texto del tweet listo para publicar.
"""

    completion = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "Eres un experto en historia de España y community manager. "
                    "Siempre respetas estrictamente el límite de caracteres y el formato pedido."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    )

    tweet = completion.choices[0].message.content.strip()

    # Por si acaso el modelo se pasa un poco, recortamos a margen de seguridad.
    # 280 es el máximo de X; dejamos 275.
    if len(tweet) > 275:
        tweet = tweet[:272] + "…"

    return tweet


def post_to_twitter(text: str):
    """Publica el tweet usando la X API v2 (create_tweet)."""
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

    resp = client_tw.create_tweet(text=text)
    print("DEBUG create_tweet response:", resp)


def main():
    _, month, day = today_parts()

    try:
        text = generate_openai_tweet(month, day)
        print("✅ Tweet generado con OpenAI:")
        print(text)
    except Exception as e:
        print("❌ Error generando el tweet con OpenAI:", e)
        raise

    try:
        post_to_twitter(text)
        print("✅ Tweet publicado correctamente.")
    except Exception as e:
        print("❌ Error publicando el tweet en X:", e)
        raise


if __name__ == "__main__":
    main()
