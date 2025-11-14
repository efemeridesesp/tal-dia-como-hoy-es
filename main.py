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
Quiero que escribas
