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

# “Marca España” amplia (aquí queremos que entren muchas cosas)
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

# Teatro en suelo español (puede ser guiris dándose de hostias en nuestra costa)
SPANISH_THEATRE_TOKENS = [
    "málaga", "cádiz", "cartagena", "cartagena de indias",
    "barcelona", "valencia", "bilbao", "santander", "la coruña",
    "ceuta", "melilla", "baleares", "canarias",
]

# Palabras claramente militares
MILITARY_KEYWORDS = [
    "batalla", "guerra", "combate", "frente",
    "asedio", "sitio", "conquista", "derrota", "victoria", "alzamiento",
    "revolución", "levantamiento", "sublevación", "bombardeo", "invasión",
    "ejército", "toma", "capitulación", "ofensiva", "defensiva",
]

# Diplomacia / acuerdos / alianzas
DIPLO_KEYWORDS = [
    "tratado", "acuerdo", "paz", "alianza",
    "capitulaciones", "concordia",
]

# Nacionalidades extranjeras típicas (si solo salen estos y no España como actor, penalizamos)
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

# Cosas que penalizamos (cultura/pop blanda)
CULTURE_LOW_PRIORITY = [
    "premio", "premios", "concurso", "festival", "certamen",
    "programa de radio", "programa de televisión", "radio", "televisión",
    "serie", "película", "cine", "novela", "poeta", "cantante", "músico",
    "discográfica", "disco", "álbum", "single"
]

# Claves de X (Twitter) desde los secrets del repositorio
TW_API_KEY = os.getenv("TWITTER_API_KEY", "")
TW_API_SECRET = os.getenv("TWITTER_API_SECRET", "")
TW_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN", "")
TW_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")
TW_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN", "")

USER_AGENT = "Efemerides_Imp_Bot/1.0 (https://github.com/efemeridesesp/tal-dia-como-hoy-es)"

# Cliente de OpenAI (usa OPENAI_API_KEY del entorno)
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

    # Miramos todos los list items que empiezan con un año
    for li in soup.find_all("li"):
        text = " ".join(li.stripped_strings)
        if not text:
            continue

        # Formato típico: "1501 el príncipe de Gales..."
        m = re.match(r"^(\d+)\s*(a\.C\.)?\s*(.*)", text)
        if not m:
            continue

        year_str, era, rest = m.groups()
        try:
            year = int(year_str)
        except ValueError:
            continue

        if era:
            year = -year  # años a.C. negativos, por si algún día interesa

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


# ----------------- Scoring “imperial” con penalización a batallas guiris ----------------- #

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

    # Núcleo: España/Imperio como actor → MUY arriba
    if has_spanish_actor:
        score += 35

    # Marca España amplia (España, hispania, ciudades históricas, etc.)
    if has_spanish_wide:
        score += 18

    # Teatro en España suma, pero menos
    if has_spanish_theatre:
        score += 5

    # Militar suma bastante (prioriza batallas)
    if has_military:
        score += 12

    # Diplomático (tratados, acuerdos, etc.) también suma
    if has_diplomatic:
        score += 8

    # Penalizar fuerte cosas de premios/cultura pop
    for kw in CULTURE_LOW_PRIORITY:
        if kw in t_low:
            score -= 12

    # Bonus por siglos interesantes (1500–1899 aprox.)
    if 1400 <= year <= 1899:
        score += 5

    # Penalización clave:
    # Si es evento MILITAR, con actores claramente extranjeros,
    # y España solo aparece de fondo (sin ser actor),
    # lo hundimos en puntuación para que no gane a una efeméride española normal.
    if has_military and has_foreign and not has_spanish_actor and not has_diplomatic:
        # Este caso es EXACTAMENTE Ark Royal y similares
        score -= 40

    ev["score"] = score
    ev["has_spanish_actor"] = has_spanish_actor
    ev["has_spanish_wide"] = has_spanish_wide
    ev["has_spanish_theatre"] = has_spanish_theatre
    ev["has_military"] = has_military
    ev["has_diplomatic"] = has_diplomatic
    ev["has_foreign"] = has_foreign


def choose_best_event(events):
    """
    Elige el evento con mayor score según compute_score.
    Siempre devuelve algo si hay eventos.
    """
    if not events:
        return None

    for ev in events:
        compute_score(ev)

    best = max(events, key=lambda e: e["score"])
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
- Tono divulgativo, con cierto orgullo por la historia de España y su Imperio, sin emojis, sin URLs y sin mencionar la fuente.
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
        temperature=0.5,
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

    # 2) Elegir el mejor evento según scoring
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
