import os
import datetime
import pytz
import tweepy
from openai import OpenAI

TZ = "Europe/Madrid"

DEFAULT_HASHTAGS = ["#TalDiaComoHoy", "#España", "#HistoriaDeEspaña", "#Efemérides"]

TWITTER_API_KEY = os.getenv("TWITTER_API_KEY", "")
TWITTER_API_SECRET = os.getenv("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN", "")

client = OpenAI()


def today_parts():
    tz = pytz.timezone(TZ)
    now = datetime.datetime.now(tz)
    return now.year, now.month, now.day


def generate_openai_tweet(month: int, day: int) -> str:
    fecha_str = f"{day:02d}/{month:02d}"
    hashtags = " ".join(DEFAULT_HASHTAGS)

    prompt = f"""
Eres un historiador experto. Necesito UN ÚNICO tweet de efeméride de la historia de España.

CONDICIONES ESTRICTAS:
- Hoy es el día {fecha_str}. SOLO puedes usar acontecimientos históricos verificables que ocurrieran EXACTAMENTE en este día y mes.
- Si NO existe NINGÚN evento histórico famoso, relevante y verificable QUE OCURRIERA EXACTAMENTE EN ESTA FECHA:
    → Responde SOLO con la palabra: NO_EVENT
- El tweet debe comenzar EXACTAMENTE así:
"🇪🇸 En tal día como hoy del año XXXX,"
y XXXX debe ser el año REAL del suceso.
- El suceso DEBE haber ocurrido precisamente en el día {fecha_str}. No aproximaciones, no sucesos solo "cercanos".
- Si pones una fecha incorrecta → NO debes generar el tweet.
- Al final del tweet añade solo estos hashtags:
{hashtags}
- Máximo 260 caracteres.

Devuélveme solo el tweet, o NO_EVENT si no existe una efeméride exacta.
"""

    completion = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system",
             "content": "Verificas fechas con precisión. Si no existe un evento EXACTO para ese día, devuelves 'NO_EVENT'."},
            {"role": "user", "content": prompt},
        ],
    )

    return completion.choices[0].message.content.strip()


def post_to_twitter(text: str):
    client_tw = tweepy.Client(
        consumer_key=TWITTER_API_KEY,
        consumer_secret=TWITTER_API_SECRET,
        access_token=TWITTER_ACCESS_TOKEN,
        access_token_secret=TWITTER_ACCESS_SECRET,
        bearer_token=TWITTER_BEARER_TOKEN,
    )
    client_tw.create_tweet(text=text)


def main():
    _, month, day = today_parts()
    fecha_str = f"{day:02d}/{month:02d}"

    text = generate_openai_tweet(month, day)
    print("GENERATED:", text)

    # 🚨 BLOQUEO total si no hay efeméride exacta
    if text == "NO_EVENT":
        print(f"❌ No existe efeméride exacta el {fecha_str}. No se publica nada.")
        return

    # 🚨 Bloqueo si no empieza por la frase obligatoria
    prefix = "🇪🇸 En tal día como hoy del año"
    if not text.startswith(prefix):
        print("❌ Formato inválido. No se publica.")
        return

    # 🚨 Última seguridad: intentar detectar la fecha
    if str(day) not in text:
        print("❌ Parece que la fecha del suceso NO coincide con el día actual. No se publica.")
        return

    try:
        post_to_twitter(text)
        print("✅ Publicado correctamente.")
    except Exception as e:
        print("❌ Error publicando:", e)


if __name__ == "__main__":
    main()
