import os
import requests
import datetime
import pytz
import re
import json
from bs4 import BeautifulSoup
from openai import OpenAI
import tweepy

TZ = "Europe/Madrid"

DEFAULT_HASHTAGS = ["#TalDiaComoHoy", "#España", "#HistoriaDeEspaña", "#Efemérides"]

SPANISH_ACTOR_TOKENS = [
    "reyes católicos","imperio español","monarquía hispánica","monarquía española",
    "armada española","ejército español","tercios","tercios españoles","tercios de flandes",
    "virreinato de","virreinato del","virreinato de nueva españa","virreinato del perú",
    "virreinato del río de la plata","virrey","virreina","corona de castilla","corona de aragón",
]

SPANISH_WIDE_TOKENS = [
    "españa","español","española","españoles","hispania","hispano","hispánica",
    "reino de castilla","reino de aragón","castilla","aragón","granada","sevilla",
    "toledo","madrid","cartagena","cartagena de indias","virreinato","borbón","borbones",
    "habsburgo","felipe ii","felipe iii","felipe iv","carlos v","carlos i de españa",
    "alfonso xii","alfonso xiii","isabel ii","partido comunista de españa","radio barcelona",
]

SPANISH_THEATRE_TOKENS = [
    "málaga","cádiz","cartagena","cartagena de indias","barcelona","valencia",
    "bilbao","santander","la coruña","ceuta","melilla","baleares","canarias",
]

MILITARY_KEYWORDS = [
    "batalla","guerra","combate","frente","asedio","sitio","conquista","derrota",
    "victoria","alzamiento","revolución","levantamiento","sublevación","bombardeo",
    "invasión","ejército","toma","capitulación","ofensiva","defensiva",
]

DIPLO_KEYWORDS = ["tratado","acuerdo","paz","alianza","capitulaciones","concordia"]

FOREIGN_TOKENS = [
    "alemán","alemana","alemania","nazi","británico","británica","inglés","inglesa",
    "inglaterra","estadounidense","americano","americana","ee.uu","eeuu","francés",
    "francesa","francia","italiano","italiana","italia","ruso","rusa","rusia",
    "soviético","soviética","urss","japonés","japonesa","japón",
]

CULTURE_LOW_PRIORITY = [
    "premio","premios","concurso","festival","certamen","programa de radio",
    "programa de televisión","radio","televisión","serie","película","cine","novela",
    "poeta","cantante","músico","discográfica","disco","álbum","single",
]

TW_API_KEY = os.getenv("TWITTER_API_KEY")
TW_API_SECRET = os.getenv("TWITTER_API_SECRET")
TW_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN")
TW_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_TOKEN_SECRET")
TW_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")

USER_AGENT = "Efemerides_Imp_Bot/1.0"
client = OpenAI()

# ---------------------
# FECHA
# ---------------------

def today_info():
    tz = pytz.timezone(TZ)
    now = datetime.datetime.now(tz)
    meses = ["","enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"]
    return now.year, now.month, now.day, meses[now.month]

# ---------------------
# SCRAPER EFEMÉRIDES
# ---------------------

def fetch_events():
    url = "https://www.hoyenlahistoria.com/efemerides.php"
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    events = []

    for li in soup.find_all("li"):
        text = " ".join(li.stripped_strings)
        m = re.match(r"^(\d+)\s*(a\.C\.)?\s*(.*)", text)
        if not m:
            continue
        year_str, era, body = m.groups()
        try:
            year = int(year_str)
        except:
            continue
        if era:
            year = -year
        body = body.strip()
        if body:
            events.append({"year": year, "text": body})
    return events

# ---------------------
# SCORING
# ---------------------

def compute_score(ev):
    t = ev["text"].lower()
    y = ev["year"]
    s = 0

    if any(x in t for x in SPANISH_ACTOR_TOKENS): s += 35
    if any(x in t for x in SPANISH_WIDE_TOKENS): s += 18
    if any(x in t for x in SPANISH_THEATRE_TOKENS): s += 5
    if any(x in t for x in MILITARY_KEYWORDS): s += 12
    if any(x in t for x in DIPLO_KEYWORDS): s += 8

    if any(x in t for x in CULTURE_LOW_PRIORITY): s -= 12

    if 1400 <= y <= 1899: s += 5

    if any(x in t for x in MILITARY_KEYWORDS) and any(x in t for x in FOREIGN_TOKENS) and not any(x in t for x in SPANISH_ACTOR_TOKENS) and not any(x in t for x in DIPLO_KEYWORDS):
        s -= 40

    ev["score"] = s

def choose_best(events):
    if not events:
        return None
    for ev in events:
        compute_score(ev)
    return max(events, key=lambda e: e["score"])

# ---------------------
# IMÁGENES
# ---------------------

def extract_queries(text):
    names = []

    patterns = [
        r"(Batalla de [A-ZÁÉÍÓÚÑ][^,.;]*)",
        r"(Guerra de [A-ZÁÉÍÓÚÑ][^,.;]*)",
        r"(Sitio de [A-ZÁÉÍÓÚÑ][^,.;]*)",
        r"(Tratado de [A-ZÁÉÍÓÚÑ][^,.;]*)",
        r"(Paz de [A-ZÁÉÍÓÚÑ][^,.;]*)"
    ]

    for p in patterns:
        for m in re.finditer(p, text):
            names.append(m.group(1).strip())

    pattern2 = re.compile(r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+de\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)*(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)+)")
    for m in pattern2.findall(text):
        names.append(m.strip())

    clean = []
    for n in names:
        if len(n.split()) >= 2:
            if n not in clean:
                clean.append(n)
    return clean

def fetch_image(event):
    names = extract_queries(event["text"])
    print("Nombres detectados:", names)

    if not names:
        print("No imagen lógica")
        return None

    base = "https://es.wikipedia.org/w/api.php"

    for name in names:
        try:
            r = requests.get(base, params={
                "action": "query","format": "json","list": "search","srsearch": name,"srlimit": 1
            }, timeout=10)
            data = r.json()
            results = data.get("query", {}).get("search", [])
            if not results:
                continue

            title = results[0]["title"]
            r2 = requests.get(base, params={
                "action":"query","format":"json","prop":"pageimages",
                "piprop":"original|thumbnail","pithumbsize":1200,"titles":title
            }, timeout=10)
            pages = r2.json()["query"]["pages"]

            for _,p in pages.items():
                url = p.get("original",{}).get("source") or p.get("thumbnail",{}).get("source")
                if url and "upload.wikimedia.org" in url:
                    print("Imagen buena:", url)
                    return url
        except:
            pass

    print("Sin imagen adecuada")
    return None

def download_image(url):
    r = requests.get(url, timeout=15)
    with open("img.jpg","wb") as f:
        f.write(r.content)
    return "img.jpg"

# ---------------------
# OPENAI TEXTO
# ---------------------

def generate_headline(y, mname, d, ev):
    today = f"{d} de {mname} de {y}"
    yev = ev["year"]
    hashtags = " ".join(DEFAULT_HASHTAGS)

    prompt = f"""
Escribe un único tuit:

"🇪🇸 {today}: En tal día como hoy del año {yev}, ... {hashtags}"

Hecho histórico:
{ev["text"]}
"""

    out = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role":"user","content":prompt}],
        max_tokens=180
    ).choices[0].message.content.strip()

    if len(out) > 275:
        out = out[:272] + "..."

    return out

def generate_followups(ev):
    prompt = f"""
Escribe entre 1 y 5 tuits cortos explicando el contexto español del siguiente hecho:

{ev["text"]}

Devuélvelos SOLO en JSON así:
["texto1","texto2"]
"""

    out = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role":"user","content":prompt}],
        max_tokens=350
    ).choices[0].message.content.strip()

    try:
        arr = json.loads(out)
        return arr[:5]
    except:
        return []

# ---------------------
# PUBLICACIÓN TWITTER
# ---------------------

def twitter_clients():
    c = tweepy.Client(
        consumer_key=TW_API_KEY,
        consumer_secret=TW_API_SECRET,
        access_token=TW_ACCESS_TOKEN,
        access_token_secret=TW_ACCESS_SECRET,
        bearer_token=TW_BEARER_TOKEN
    )
    auth = tweepy.OAuth1UserHandler(
        TW_API_KEY, TW_API_SECRET, TW_ACCESS_TOKEN, TW_ACCESS_SECRET
    )
    api_v1 = tweepy.API(auth)
    return c, api_v1

def post_thread(head, follow, ev):
    client_tw, api_v1 = twitter_clients()

    media_ids = None
    img_url = fetch_image(ev)
    if img_url:
        img_path = download_image(img_url)
        media = api_v1.media_upload(img_path)
        media_ids = [media.media_id_string]

    if media_ids:
        resp = client_tw.create_tweet(text=head, media_ids=media_ids)
    else:
        resp = client_tw.create_tweet(text=head)

    parent = resp.data["id"]

    for t in follow:
        r = client_tw.create_tweet(text=t, in_reply_to_tweet_id=parent)
        parent = r.data["id"]

# ---------------------
# MAIN
# ---------------------

def main():
    y, m, d, mname = today_info()

    print("Obteniendo eventos…")
    try:
        events = fetch_events()
    except Exception as e:
        print("ERROR obteniendo eventos:", e)
        return

    if not events:
        print("No hay eventos")
        return

    print("Escogiendo mejor evento…")
    best = choose_best(events)

    if not best:
        print("No evento adecuado")
        return

    print("Evento elegido:", best)

    headline = generate_headline(y, mname, d, best)
    print("Titular:", headline)

    followups = generate_followups(best)
    print("Followups:", followups)

    print("Publicando hilo…")
    post_thread(headline, followups, best)

    print("Hilo publicado.")

if __name__=="__main__":
    main()
