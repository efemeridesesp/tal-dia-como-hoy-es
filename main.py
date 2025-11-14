import os
import requests
import datetime
import pytz
import re
from bs4 import BeautifulSoup
from openai import OpenAI
import tweepy

# Zona horaria de referencia
TZ = "Europe/Madrid"

# Hashtags fijos
DEFAULT_HASHTAGS = ["#TalDiaComoHoy", "#España", "#HistoriaDeEspaña", "#Efemérides"]

# Palabras clave para priorizar eventos "imperiales/españoles"
BASE_KEYWORDS = [
    "Imperio español", "Reyes Católicos", "Armada", "Flota", "Galeón",
    "América", "Virreinato", "Nueva España", "Filipinas", "Pacífico",
    "Granada", "Castilla", "Aragón", "Toledo", "Sevilla", "Madrid",
    "Carlos V", "Felipe II", "Felipe III", "Felipe IV", "Monarquía Hispánica",
    "Tercios", "Virreinato", "Virrey", "Corte de Madrid"
]

MILITARY_KEYWORDS = [
    "batalla", "Batalla", "guerra", "Guerra", "combate", "frente",
    "asedio", "sitio", "conquista", "derrota", "victoria", "alzamiento",
    "revolución", "levantamiento", "sublevación", "bombardeo", "invasión",
    "ejército", "Armada", "flota", "toma", "capitulación"
]

# Palabras que queremos penalizar (cosas poco épicas)
CULTURE_LOW_PRIORITY = [
    "premio", "premios", "concurso", "festival", "certamen",
    "programa de radio", "programa de televisión", "radio", "televisión",
    "serie", "película", "cine", "novela", "poeta", "cantante", "músico",
    "discográfica", "disco", "álbum", "single"
]

# Núcleo de “marca España / Imperio” que EXIGIMOS para publicar
CORE_SPANISH_TOKENS = [
    "españa",
    "español",
    "española",
    "reyes católicos",
    "monarquía hispánica",
    "imperio español",
    "monarquía española",
    "reino de castilla",
    "reino de aragón",
    "corona de castilla",
    "corona de aragón",
    "rey de españa",
    "reina de españa",
    "tercios",
]

# Claves de X (Twitter) desde los secrets del repositorio
TW_API_KEY = os.getenv("TWITTER_API_KEY", "")
TW_API_SECRET = os.getenv("TWITTER_API_SECRET", "")
TW_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN", "")
TW_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")
TW_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN", "")

USER_AGENT = "Efemerides_Imp_Bot/1.0 (https://github.com/efemeridesesp/tal-dia-como-hoy-es)"

# Cliente de OpenAI (toma OPENAI_API_KEY del entorno)
client = OpenAI()


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


# ----------------- Scoring “imperial” ----------------- #

def compute_scores(events):
    """
    Calcula varias métricas y devuelve la lista de eventos enriquecida con:
      - score
      - has_military
      - has_imperial
      - has_spanish
    """
    for ev in events:
        text = ev["text"]
        t_low = text.lower()
        year = ev["year"]

        score = 0.0

        # ¿Tiene algo claramente español/imperial?
        has_spanish = any(tok in t_low for tok in CORE_SPANISH_TOKENS)
        if has_spanish:
            score += 8

        # ¿Tiene keywords imperiales/políticas gordas (más suaves)?
        has_imperial = False
        for kw in BASE_KEYWORDS:
            if kw.lower() in t_low:
                score += 3
                has_imperial = True

        # ¿Tiene keywords directamente militares?
        has_military = False
        for kw in MILITARY_KEYWORDS:
            if kw.lower() in t_low:
                score += 8
                has_military = True

        # Penalizar fuertemente eventos “de premios/concurso/programa/etc.”
        for kw in CULTURE_LOW_PRIORITY:
            if kw.lower() in t_low:
                score -= 8

        # Bonus por siglos “interesantes” (aprox. XV–XIX)
        if 1400 <= year <= 1899:
            score += 2

        ev["score"] = score
        ev["has_military"] = has_military
        ev["has_imperial"] = has_imperial
        ev["has_spanish"] = has_spanish

    return events


def choose_best_event(events):
    """
    Nueva lógica:
      - Solo consideramos eventos cuyo texto incluya ALGUNA de las CORE_SPANISH_TOKENS.
      - Dentro de esos, priorizamos los militares.
      - Si aun así no hay NINGUNO → devolvemos None y NO se publica.
    """
    if not events:
        return None

    compute_scores(events)

    # Filtramos SOLO eventos con núcleo español explícito
    core_spanish_events = [e for e in events if e["has_spanish"]]

    if not core_spanish_events:
        print("⚠️ No hay eventos con núcleo español/imperial explícito hoy. No se publicará nada.")
        return None

    # Dentro de esos, priorizamos militares si los hay
    military_core = [e for e in core_spanish_events if e["has_military"]]

    if military_core:
        candidates = military_core
        tier_name = "Eventos núcleo español/imperial con componente militar"
    else:
        candidates = core_spanish_events
        tier_name = "Eventos núcleo español/imperial (sin requisito militar)"

    best = max(candidates, key=lambda e: e["score"])
    print(f"➡️ Seleccionando de {tier_name}, total candidatos: {len(candidates)}")
    return best


# ----------------- Generación de texto con OpenAI ----------------- #

def generate_openai_tweet(today_year, today_month_name, today_day, event):
    """
    Pide a OpenAI que redacte el tuit con el formato:

    '14 de noviembre de 2025: En tal día como hoy del año XXXX, ... #TalDiaComoHoy #España #HistoriaDeEspaña #Efemérides'
    """
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
        model="gpt-4.1-mini",
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


# ----------------- Publicación en X (API v2) ----------------- #

def post_to_twitter(text):
    """Publica el tuit usando la X API v2 (create_tweet)."""
    if not text:
        raise RuntimeError("Texto vacío, no se puede publicar.")

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

    resp = client_tw.create_tweet(text=text)
    print("DEBUG create_tweet response:", resp)


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

    # 2) Elegir el mejor evento según lógica “núcleo español obligatorio”
    best = choose_best_event(events)
    if not best:
        print("No se ha encontrado ningún evento suficientemente español/imperial. No se publicará tuit.")
        return

    print("Evento elegido:")
    print(f"- Año: {best['year']}")
    print(f"- Texto: {best['text']}")
    print(f"- Score: {best.get('score', 'N/A')}")
    print(f"- Militar: {best.get('has_military')}, Imperial: {best.get('has_imperial')}, Español(core): {best.get('has_spanish')}")

    # 3) Generar el texto del tuit con OpenAI
    try:
        tweet_text = generate_openai_tweet(today_year, today_month_name, today_day, best)
    except Exception as e:
        print("❌ Error al generar el tuit con OpenAI:", e)
        raise

    print("Tuit generado:")
    print(tweet_text)
    print(f"Largo: {len(tweet_text)} caracteres")

    # 4) Publicar en X (API v2)
    try:
        post_to_twitter(tweet_text)
        print("✅ Tuit publicado correctamente.")
    except Exception as e:
        print("❌ Error publicando en Twitter/X:", e)
        raise


if __name__ == "__main__":
    main()
