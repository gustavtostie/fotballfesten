"""
Fotballfesten-vakt for GitHub Actions.

Looper i RUN_MINUTES og sjekker hvert ~15. sekund. Varsler via ntfy
(push + valgfri e-post) hvis:
  1. Det finnes produkter i /store (baseline: tom butikk)
  2. "Billetter kommer snart" er borte fra /frognerstadion
  3. Sitemap inneholder URL-er som ikke er i baseline_sitemap.txt

Miljøvariabler:
  NTFY_TOPIC   (påkrevd)  - hemmelig topic-navn i ntfy-appen
  ALERT_EMAIL  (valgfri)  - e-post som også skal varsles
  RUN_MINUTES  (valgfri)  - hvor lenge jobben looper (default 25)
"""

import json
import os
import random
import re
import time
from pathlib import Path

import requests

NTFY_TOPIC = os.environ["NTFY_TOPIC"]
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "")
RUN_MINUTES = int(os.environ.get("RUN_MINUTES", "25"))
POLL_SECONDS = 15

STORE_URL = "https://www.fotballfesten.no/store"
PAGE_URL = "https://www.fotballfesten.no/frognerstadion"
SITEMAP_URL = "https://www.fotballfesten.no/sitemap.xml"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

BASELINE_SITEMAP = set(
    Path(__file__).with_name("baseline_sitemap.txt").read_text().split()
)

# Mykt signal: nye lenker på nøkkelsider = "sjekk ut", ikke full alarm.
SOFT_PAGES = [
    "https://www.fotballfesten.no/",
    PAGE_URL,
    "https://www.fotballfesten.no/infofrogner",
    STORE_URL,
]
_links_file = Path(__file__).with_name("baseline_links.json")
SOFT_BASELINE = json.loads(_links_file.read_text()) if _links_file.exists() else {}

already_alerted: set = set()


def extract_links(html: str) -> set:
    """Interne lenker på en side, normalisert (uten query/fragment/assets)."""
    out = set()
    for link in re.findall(r'href="([^"#]+)"', html):
        if link.startswith("http") and "fotballfesten.no" not in link:
            continue
        link = link.split("?")[0]
        if link.lower().endswith((".css", ".js", ".ico", ".png", ".jpg",
                                  ".jpeg", ".gif", ".svg", ".webp", ".pdf",
                                  ".woff", ".woff2", ".xml")):
            continue
        if link:
            out.add(link)
    return out


def notify(key: str, title: str, message: str,
           priority: str = "urgent", tags: str = "rotating_light,soccer",
           github_issue: bool = True):
    if key in already_alerted:
        return
    already_alerted.add(key)
    print(f"\n🚨 {title}\n{message}\n", flush=True)
    headers = {
        "Title": title.encode("utf-8"),
        "Priority": priority,
        "Tags": tags,
        "Click": PAGE_URL,
    }
    # 1) Push - viktigste kanal. Sendes ALLTID uten Email-header, fordi
    #    ntfy.sh avviser hele meldingen (HTTP 400) hvis Email er med
    #    uten betalt ntfy-konto - da hadde heller ikke pushen gått ut.
    try:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}",
                      data=message.encode("utf-8"), headers=headers, timeout=10)
    except Exception as e:
        print(f"ntfy-feil: {e}", flush=True)
    # 2) E-post via ntfy - best effort, krever betalt ntfy-konto.
    if ALERT_EMAIL:
        try:
            r = requests.post(f"https://ntfy.sh/{NTFY_TOPIC}",
                              data=message.encode("utf-8"),
                              headers={**headers, "Email": ALERT_EMAIL},
                              timeout=10)
            if r.status_code >= 400:
                print(f"ntfy e-post avvist ({r.status_code}): {r.text}",
                      flush=True)
        except Exception as e:
            print(f"ntfy e-postfeil: {e}", flush=True)
    # 3) GitHub-issue - GitHub e-poster alle som watcher repoet.
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if github_issue and token and repo:
        try:
            requests.post(
                f"https://api.github.com/repos/{repo}/issues",
                json={"title": title, "body": message},
                headers={"Authorization": f"Bearer {token}",
                         "Accept": "application/vnd.github+json"},
                timeout=10)
        except Exception as e:
            print(f"github-issue-feil: {e}", flush=True)


def fetch(url: str) -> str:
    sep = "&" if "?" in url else "?"
    r = requests.get(f"{url}{sep}cb={int(time.time())}",
                     headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.text


def check_once():
    store = fetch(STORE_URL)
    prods = set(re.findall(r'href="(/store/[^"?#]+)"', store)) | set(
        re.findall(r'"(/store/p/[^"?#]+)"', store))
    if prods:
        notify("store",
               "🎫 BILLETTER I BUTIKKEN - LØP!",
               "\n".join(f"https://www.fotballfesten.no{p}" for p in sorted(prods)))

    page = fetch(PAGE_URL)
    if "Billetter kommer snart" not in page:
        hot = sorted(set(re.findall(
            r'href="([^"]*(?:billett|store|checkout|ticket)[^"]*)"', page, re.I)))
        notify("page",
               "⚡ FROGNER-SIDEN ER ENDRET!",
               "'Billetter kommer snart' er borte.\n"
               + ("\n".join(hot) + "\n" if hot else "") + PAGE_URL)

    smap = set(re.findall(r"<loc>([^<]+)</loc>", fetch(SITEMAP_URL)))
    new = smap - BASELINE_SITEMAP
    if new:
        notify("sitemap:" + ",".join(sorted(new)),
               "🗺️ NY SIDE PÅ FOTBALLFESTEN.NO!",
               "\n".join(sorted(new)))


def check_soft():
    """Belte og bukseseler: nye lenker på nøkkelsider gir en mildere
    'sjekk ut'-push (uten GitHub-issue, uten urgent-prioritet)."""
    if not SOFT_BASELINE:
        return
    for url in SOFT_PAGES:
        base = set(SOFT_BASELINE.get(url, []))
        if not base:
            continue
        try:
            links = extract_links(fetch(url))
        except Exception as e:
            print(f"soft-feil {url}: {e}", flush=True)
            continue
        new = links - base
        if new:
            notify("soft:" + url + ":" + ",".join(sorted(new)),
                   "👀 Mindre endring på fotballfesten.no - sjekk ut",
                   f"Nye lenker på {url}:\n" + "\n".join(sorted(new)),
                   priority="default", tags="eyes", github_issue=False)


def heartbeat():
    """Stille status-push ved rundestart, så man ser at vakten lever."""
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=(f"Vakten kjører - ny runde på {RUN_MINUTES} min startet. "
                  "Ingen varsel = ingen endring på siden.").encode("utf-8"),
            headers={"Title": "💚 Vakt-status: alt ok".encode("utf-8"),
                     "Priority": "low",
                     "Tags": "green_heart"},
            timeout=10)
    except Exception as e:
        print(f"heartbeat-feil: {e}", flush=True)


def main():
    deadline = time.time() + RUN_MINUTES * 60
    errors = 0
    print(f"Vakt kjører i {RUN_MINUTES} min, sjekker hvert ~{POLL_SECONDS}s",
          flush=True)
    heartbeat()
    n = 0
    while time.time() < deadline:
        try:
            check_once()
            if n % 4 == 0:
                check_soft()
            errors = 0
            print(f"[{time.strftime('%H:%M:%S')}] ok", flush=True)
        except Exception as e:
            errors += 1
            print(f"[{time.strftime('%H:%M:%S')}] feil ({errors}): {e}",
                  flush=True)
            if errors >= 10:
                notify("errors", "⚠️ Vakten sliter",
                       f"10 feil på rad, siste: {e}")
                errors = 0
        n += 1
        time.sleep(POLL_SECONDS + random.uniform(0, 5))
    print("Ferdig med denne runden - neste cron tar over.", flush=True)


if __name__ == "__main__":
    main()
