import os, requests, datetime, pytz, sys

TZ = "Europe/Madrid"
DEFAULT_HASHTAGS = ["#TalDiaComoHoy", "#España", "#HistoriaDeEspaña", "#Efemérides"]
KEYWORDS_PRIORITY = [
    "Armada","Descubrimiento","Reyes Católicos","Imperio","Monarquía Hispánica",
    "Magallanes","Elcano","Lepanto","América","Pacífico","Galeón","Naval",
    "Ciencia","Cultural","Constitución","Exploración","Cartagena de Indias",
    "Sevilla","Madrid","Toledo","Granada","Castilla","Aragón","España"
]

TW_API_KEY = os.getenv("TWITTER_API_KEY", "")
TW_API_SECRET = os.getenv("TWITTER_API_SECRET", "")
TW_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN", "")
TW_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

def today_parts():
    tz = pytz.timezone(TZ)
    now = datetime.datetime.now(tz)
    return now.year, now.month, now.day

def fetch_wikidata_events(month: int, day: int):
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
    headers = {"Accept": "application/sparql-results+json"}
    r = requests.get(endpoint, params={"query": query}, headers=headers, timeout=30)
    r.raise_for_status()
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
    except:
        pass
    return score

def choose_best(events):
    if not events:
        return None
    return sorted(events, key=score_event, reverse=True)[0]

def fetch_wikipedia_summary(title_or_url: str):
    title = title_or_url
    if "wikipedia.org" in title_or_url:
        title = title_or_url.rstrip("/").split("/")[-1]
    url = f"https://es.wikipedia.org/api/rest_v1/page/summary/{title}"
    r = requests.get(url, timeout=20)
    if r.status_code != 200:
        return None
    j = r.json()
    return {
        "title": j.get("title"),
        "extract": j.get("extract"),
        "url": j.get("content_urls", {}).get("desktop", {}).get("page"),
    }

def compose_post(ev, summary):
    anio = None
    if ev and ev.get("date"):
        try:
            anio = int(ev["date"][:4])
        except:
            pass
    titulo = summary["title"] if summary and summary.get("title") else ev["label"]
    base = f"🇪🇸 Tal día como hoy, en {anio}, {titulo}." if anio else f"🇪🇸 Tal día como hoy: {titulo}."
    extra = ""
    if summary and summary.get("extract"):
        extract = summary["extract"]
        extract = (extract[:220] + "…") if len(extract) > 220 else extract
        extra = f"\n{extract}"
    hashtags = " ".join(DEFAULT_HASHTAGS)
    url = summary.get("url") if summary and summary.get("url") else ""
    tail = f"\n\n{hashtags}\nFuente: {url}" if url else f"\n\n{hashtags}"
    text = base + extra + tail
    return text[:275]

def post_to_twitter(text):
    import tweepy
    auth = tweepy.OAuth1UserHandler(
        TW_API_KEY, TW_API_SECRET, TW_ACCESS_TOKEN, TW_ACCESS_SECRET
    )
    api = tweepy.API(auth)
    api.verify_credentials()
    api.update_status(status=text)

def notify_telegram(msg):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg})

def main():
    _, month, day = today_parts()
    events = fetch_wikidata_events(month, day)
    if not events:
        notify_telegram(f"No se encontraron efemérides para {day}/{month}.")
        print("No events found.")
        sys.exit(0)
    best = choose_best(events)
    summary = fetch_wikipedia_summary(best["wp_es"]) if best.get("wp_es") else None
    if not summary:
        summary = {"title": best["label"], "extract": "", "url": ""}
    text = compose_post(best, summary)
    try:
        post_to_twitter(text)
        notify_telegram(f"Publicado en X: {text[:120]}…")
        print("Tweet posted.")
    except Exception as e:
        notify_telegram(f"Error publicando en X: {e}")
        raise

if __name__ == "__main__":
    main()
