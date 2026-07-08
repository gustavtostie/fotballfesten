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

already_alerted: set = set()


def notify(key: str, title: str, message: str):
    if key in already_alerted:
        return
    already_alerted.add(key)
    print(f"\n🚨 {title}\n{message}\n", flush=True)
    headers = {
        "Title": title.encode("utf-8"),
        "Priority": "urgent",
        "Tags": "rotating_light,soccer",
        "Click": PAGE_URL,
    }
    if ALERT_EMAIL:
        headers["Email"] = ALERT_EMAIL
    try:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}",
                      data=message.encode("utf-8"), headers=headers, timeout=10)
    except Exception as e:
        print(f"ntfy-feil: {e}", flush=True)


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


def main():
    deadline = time.time() + RUN_MINUTES * 60
    errors = 0
    print(f"Vakt kjører i {RUN_MINUTES} min, sjekker hvert ~{POLL_SECONDS}s",
          flush=True)
    while time.time() < deadline:
        try:
            check_once()
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
        time.sleep(POLL_SECONDS + random.uniform(0, 5))
    print("Ferdig med denne runden - neste cron tar over.", flush=True)


if __name__ == "__main__":
    main()
