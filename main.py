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

# Núcleo duro: España como ACTOR
SPANISH_ACTOR_TOKENS = [
    "españa",
    "español",
    "española",
    "españoles",
    "reyes católicos",
    "imperio español",
    "monarquía hispánica",
    "monarquía española",
    "reino de castilla",
    "reino de aragón",
    "corona de castilla",
    "corona de aragón",
    "rey de españa",
    "reina de españa",
    "tercios",
    "armada española",
    "ejército español",
]

# “Español amplio”: cosas que suelen implicar España/Imperio
SPANISH_WIDE_TOKENS = [
    "castilla",
    "aragón",
    "granada",
    "sevilla",
    "toledo",
    "madrid",
    "virreinato",
    "virrey",
    "borbón",
    "borbones",
    "habsburgo",
    "felipe ii",
    "carlos v",
    "carlos i de españa",
    "carlos i de castilla",
]

# Ciudades / teatro en territorio español (actor puede no ser España)
SPANISH_THEATRE_TOKENS = [
    "málaga", "cádiz", "cartagena", "cartagena de indias",
    "barcelona", "valencia", "bilbao", "santander", "la coruña",
    "ceuta", "melilla", "baleares", "canarias",
]

# Palabras claramente militares
MILITARY_KEYWORDS = [
    "batalla", "Batalla", "guerra", "Guerra", "combate", "frente",
    "asedio", "sitio", "conquista", "derrota", "victoria", "alzamiento",
    "revolución", "levantamiento", "sublevación", "bombardeo", "invasión",
    "ejército", "Ejército", "toma", "capitulación", "ofensiva", "defensiva",
]

# Diplomacia / acuerdos / alianzas
DIPLO_KEYWORDS = [
    "tratado", "Tratado", "acuerdo", "Acuerdo",
    "paz", "Paz", "alianza", "Alianza",
    "capitulaciones", "Capitulaciones", "concordia", "Concordia",
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


# ----------------- Scoring “imperial” con Tiers ----------------- #

def compute_flags_and_score(ev):
    text = ev["text"]
    t_low = text.lower()
    year = ev["year"]

    score = 0.0

    has_spanish_actor = any(tok in t_low for tok in SPANISH_ACTOR_TOKENS)
    has_spanish_wide = any(tok in t_low for tok in SPANISH_WIDE_TOKENS)
    has_spanish_theatre = any(tok in t_low for tok in SPANISH_THEATRE_TOKENS)

    has_military = any(kw.lower() in t_low for kw in MILITARY_KEYWORDS)
    has_diplomatic = any(kw.lower() in t_low for kw in DIPLO_KEYWORDS)

    # Núcleo: España como actor
    if has_spanish_actor:
        score += 40

    # Español amplio (ciudades, reinos, virreinatos…)
    if has_spanish_wide:
        score += 15

    # Teatro en España suma un poco (pero no decide solo)
    if has_spanish_theatre:
        score += 4

    # Militar suma bastante, pero por debajo de “España actor”
    if has_military:
        score += 10

    # Diplomático suma, pero menos que militar
    if has_diplomatic:
        score += 6

    # Penalizar fuertemente cultura/pop
    for kw in CULTURE_LOW_PRIORITY:
        if kw.lower() in t_low:
            score -= 10

    # Bonus por siglos “interesantes” (aprox. XV–XIX)
    if 1400 <= year <= 1899:
        score += 5

    ev["score"] = score
    ev["has_spanish_actor"] = has_spanish_actor
    ev["has_spanish_wide"] = has_spanish_wide
    ev["has_spanish_theatre"] = has_spanish_theatre
    ev["has_military"] = has_military
    ev["has_diplomatic"] = has_diplomatic


def choose_best_event(events):
    """
    Lógica de elección SIEMPRE devuelve algo si hay eventos.

    Tiers por prioridad:
      A) has_military AND has_spanish_actor
      B) has_diplomatic AND has_spanish_actor
      C) has_spanish_actor (aunque no sea militar/diplomático)
      D) has_military AND has_spanish_theatre
      E) resto (último recurso)
    """
    if not events:
        return None

    for ev in events:
        compute_flags_and_score(ev)

    tierA = [e for e in events if e["has_military"] and e["has_spanish_actor"]]
    tierB = [e for e in events if not e in tierA and e["has_diplomatic"] and e["has_spanish_actor"]]
    tierC = [e for e in events if not e in tierA + tierB and e["has_spanish_actor"]]
    tierD = [e for e in events if not e in tierA + tierB + tierC and e["has_military"] and e["has_spanish_theatre"]]
    tierE = [e for e in events if e not in (tierA + tierB + tierC + tierD)]

    if tierA:
        candidates, tier_name = tierA, "Tier A (batalla/acción militar con España como actor)"
    elif tierB:
        candidates, tier_name = tierB, "Tier B (acuerdo/alianza con España como actor)"
    elif tierC:
        candidates, tier_name = tierC, "Tier C (evento claramente español/imperial)"
    elif tierD:
        candidates, tier_name = tierD, "Tier D (batalla en territorio español pero sin España como actor claro)"
    else:
        candidates, tier_name = tierE, "Tier E (evento general, último recurso)"

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
- Tono divulgativo, con cierto orgullo por la historia de España/Imperio, sin emojis, sin URLs y sin mencionar la fuente.
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

    # 2) Elegir el mejor evento según Tiers
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
        f"Diplomático: {best.get('has_diplomatic')}"
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
