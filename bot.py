"""Daily Cameroon job digest. Secrets are read only from environment variables."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import time
import unicodedata
import urllib.request
import urllib.error
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

TODAY = dt.datetime.now(dt.timezone.utc).date()
STATE = Path('state.json')
PROFILES = {
    'FERDINAND': r'mecatron|automat|electromecan|instrumentation|maintenance|electrotechn|electricien|electrique|mechanical|mechanic|mecanicien',
    'YANN': r'reseaux?|telecom|informat|fibre|fiber|\bnoc\b|\bit\b|network|system[es]?|cyber|helpdesk|support technique',
}
SOURCES = {
    'jobinfo': ('https://www.jobinfocamer.com', '/job/', ['/jobs/?p=' + str(i) for i in range(1, 7)]),
    'jobincamer': ('https://jobincamer.com', '/job/', ['/adverts/jobs?page=' + str(i) for i in range(4)] + ['/adverts/internships']),
    'minajobs': ('https://cm2024.minajobs.net', '/emplois-stage-recrutement/', ['/', '/offres-emplois-stages/informatique-internet-telecommun', '/offres-emplois-stages/electricite-electronique', '/offres-emplois-stages/mecanique-electromecanique']),
}

def norm(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', value.lower()) if not unicodedata.combining(c))

def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'CameroonJobDigest/1.0 (+https://github.com/Redington09/schema-work-)'})
    with urllib.request.urlopen(req, timeout=25) as response:
        return BeautifulSoup(response.read(), 'html.parser')

def dates(text):
    text = norm(text)
    found = []
    months = 'janvier fevrier mars avril mai juin juillet aout septembre octobre novembre decembre'.split()
    for match in re.finditer(r'\b(\d{1,2})[-/](\d{1,2})[-/](20\d{2})\b', text):
        try: found.append(dt.date(int(match[3]), int(match[2]), int(match[1])))
        except ValueError: pass
    for match in re.finditer(r'\b(\d{1,2})\s+(' + '|'.join(months) + r')\s+(20\d{2})\b', text):
        try: found.append(dt.date(int(match[3]), months.index(match[2]) + 1, int(match[1])))
        except ValueError: pass
    return found

def match_profile(title, profile):
    title = norm(title)
    if not re.search(PROFILES[profile], title): return False
    if re.search(r'commercia|marketing|vente|business|socia|plomb|genie civil|appel d.offre|profils divers|reseau.*bank|reseau.*banqu', title): return False
    if profile == 'FERDINAND' and re.search(r'informat|reseau|telecom|\bit\b|helpdesk', title): return False
    if profile == 'YANN' and re.search(r'electrique|mecanique|hydraul|solaire', title): return False
    return True

def parse_job(url, title, soup, listing=''):
    for node in soup.select('script, style, nav, footer, header'): node.decompose()
    text = soup.get_text(' ', strip=True)
    # Isolate the actual vacancy, excluding navigation and related advertisements.
    start = norm(text).find(norm(title))
    if start >= 0: text = text[start:]
    for marker in ['Offres d\'emploi récentes', 'Offres similaires', 'Vus récemment', 'Envoyez moi des offres']:
        text = text.split(marker)[0]
    normalized = norm(text)
    published = None
    date_text = norm(listing) + ' ' + normalized
    for pattern in [r'poste\s*:', r'publie le', r'date de publication\s*:', r'date de publication sur minajobs emplois\s*date de publication\s*:']:
        marker = re.search(pattern, date_text)
        if marker:
            values = dates(date_text[marker.end():marker.end() + 50])
            if values: published = values[0]; break
    if published is None: return None  # Do not advertise undated or stale listings as new.
    if not 0 <= (TODAY - published).days <= 35: return None
    for marker in re.finditer(r'date limite[^:]{0,35}:|date expiration\s*:|postuler avant(?: le|\s*:)|deadline\s*:', normalized):
        values = dates(normalized[marker.end():marker.end() + 55])
        if values and values[0] < TODAY: return None
    # Prefer the vacancy's location metadata rather than mentions in footer/address.
    location = 'Cameroun — ville à vérifier'
    marker = re.search(r'(?:lieu|localisation)\s*:', normalized)
    location_text = normalized[marker.end():marker.end() + 90] if marker else norm(title)
    for city in ['douala', 'yaounde', 'bafoussam', 'kribi', 'garoua', 'buea', 'limbe', 'bertoua', 'edea']:
        if city in location_text: location = city.title(); break
    if any(country in location_text for country in ['kinshasa', 'abidjan', 'senegal', 'france', 'gabon']): return None
    return {'title': title, 'url': url, 'date': str(published), 'location': location}

def collect():
    jobs, failed = {}, []
    for name, (base, pattern, pages) in SOURCES.items():
        candidates, successful = {}, 0
        for page in pages:
            try:
                soup = fetch(urljoin(base, page))
                links = soup.select('a[href]')
                if not any(pattern in a['href'] for a in links): raise ValueError('No vacancy links')
                successful += 1
                for a in links:
                    url, title = urljoin(base, a['href']).split('#')[0], a.get_text(' ', strip=True)
                    if urlparse(url).netloc != urlparse(base).netloc or pattern not in url: continue
                    if any(match_profile(title, p) for p in PROFILES):
                        card = a.find_parent(class_='media-body')
                        listing = card.get_text(' ', strip=True) if card else ''
                        candidates[url] = (title, listing)
            except Exception as exc:
                print(f'{name}: listing unavailable ({type(exc).__name__})')
            time.sleep(0.3)
        if not successful: failed.append(name)
        for url, (title, listing) in list(candidates.items())[:60]:
            try:
                job = parse_job(url, title, fetch(url), listing)
                if job: jobs[url] = job
            except Exception as exc: print(f'{name}: vacancy unavailable ({type(exc).__name__})')
            time.sleep(0.3)
        print(f'{name}: {successful} pages, {len(candidates)} candidate vacancies')
    return list(jobs.values()), failed

def send(hook, content):
    if not re.fullmatch(r'https://discord\.com/api/webhooks/\d+/[\w-]+', hook):
        raise ValueError('Invalid Discord webhook configuration')
    body = json.dumps({'content': content, 'allowed_mentions': {'parse': []}}).encode()
    for attempt in range(4):
        req = urllib.request.Request(hook + '?wait=true', data=body, headers={'Content-Type': 'application/json', 'User-Agent': 'CameroonJobDigest/1.0'})
        try:
            with urllib.request.urlopen(req, timeout=25) as response: response.read()
            return
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 3:
                time.sleep(min(float(json.loads(exc.read()).get('retry_after', 5)), 60)); continue
            raise RuntimeError(f'Discord HTTP {exc.code}') from None
        except Exception:
            raise RuntimeError('Discord delivery unconfirmed; inspect the channel before retrying') from None

def main():
    dry = '--dry-run' in __import__('sys').argv
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    jobs, failed = collect()
    print(f'{len(jobs)} recent vacancies, unavailable sources: {failed}')
    errors = []
    for profile in PROFILES:
        history = state.setdefault(profile, {})
        selected = []
        for job in sorted(jobs, key=lambda j: (j['location'] == 'Douala', j['date']), reverse=True):
            key = hashlib.sha256(norm(job['title']).encode()).hexdigest()
            if match_profile(job['title'], profile) and key not in history:
                selected.append((key, job))
                if len(selected) == 8: break
        messages = []
        for key, job in selected:
            title = re.sub(r'[*_`<>@]', '', job['title'])[:300]
            messages.append((key, f"**{profile.title()} — {title}**\n{job['location']} · Publiée le {job['date']}\n<{job['url']}>\nVérifie les conditions et la date limite dans l’annonce."))
        if not messages:
            note = 'Recherche incomplète : sources indisponibles.' if failed else 'Aucune nouvelle offre correspondant aux critères aujourd’hui.'
            messages = [(None, f'**Veille emploi — {profile.title()} — {TODAY}**\n{note}')]
        elif failed:
            messages.append((None, 'Recherche partielle : certaines sources sont indisponibles aujourd’hui.'))
        for key, message in messages:
            if dry: print(message); continue
            try:
                send(os.environ['DISCORD_' + profile], message)
                if key: history[key] = str(TODAY)
                STATE.write_text(json.dumps(state, indent=2) + '\n')
                time.sleep(1)
            except Exception as exc:
                errors.append(f'{profile}: {type(exc).__name__}')
                break
    if not dry:
        state['last_run'] = str(TODAY)
        STATE.write_text(json.dumps(state, indent=2) + '\n')
    if errors or len(failed) == len(SOURCES): raise RuntimeError('; '.join(errors) or 'All sources unavailable')

if __name__ == '__main__': main()
