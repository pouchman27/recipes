#!/usr/bin/env python3
"""Weekly dinner-hint sync: reads family calendars, writes /hints to the RTDB.
Hint format: "Person - reason" (one or two lowercase words, generic).
Window: events overlapping 5:00-6:30pm ET. XC practice does NOT count (back by
6:15) unless it's a pasta dinner. All-day events only when they name someone."""
import subprocess, json, re, sys, urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo('America/New_York')
DB = 'https://family-recipe-book-f493c-default-rtdb.firebaseio.com'
DINNER_START = (17, 0)    # 5:00pm
DINNER_END = (18, 30)     # 6:30pm

CALENDARS = [
    ('pouchman27@gmail.com', None, 'Nate'),
    ('weir.nathan@gmail.com', 'weir.nathan@gmail.com', 'Nate'),
    ('briana.weir@gmail.com', 'briana.weir@gmail.com', 'Briana'),
    ('42iuof97ibdlp9m0bg739inl8u3bg8li@import.calendar.google.com', None, None),  # Cozi family import
    ('family14912820059316396580@group.calendar.google.com', None, None),         # Google Family
    ('sgvo1oqfhaqr6a580kpejmd75k@group.calendar.google.com', None, None),         # FHE
]
PEOPLE = [('emmy', 'Emmy'), ('rex', 'Rex'), ('june', 'June'), ('rosie', 'Rosie'),
          ('nathan', 'Nate'), ('nate', 'Nate'), ('briana', 'Briana')]
ORDER = ['Nate', 'Briana', 'Rex', 'Emmy', 'June', 'Rosie', 'Family']

REASON_MAP = [
    (r'pasta dinner', 'pasta dinner'),
    (r'gymnastics', 'gymnastics'),
    (r'piano|\U0001f3b9', 'piano'),
    (r'dance', 'dance'),
    (r'basketball', 'basketball'),
    (r'ym/yw|yw/ym|youth activ', 'youth activity'),
    (r'fhe', 'fhe'),
    (r'book club', 'book club'),
    (r'james taylor|concert|tickets', 'concert'),
    (r'flight|airport', 'travel'),
    (r'fapa', 'fapa'),
    (r'checkup|doctor|dentist|appointment|orthodont', 'appointment'),
    (r'meeting', 'meeting'),
    (r'practice', 'practice'),
    (r'recital', 'recital'),
    (r'game\b|meet\b', 'game'),
    (r'work', 'work'),
    (r'party', 'party'),
    (r'dinner', 'dinner'),
    (r'school', 'school'),
    (r'\bto [a-z]', 'out of town'),
]

def is_xc(title):
    tl = title.lower()
    return bool(re.search(r'\bxc\b|cross country', tl))

def reason_for(title):
    tl = title.lower()
    if is_xc(title) and 'pasta' not in tl:
        return None  # XC practice doesn't count - back by 6:15
    for pat, word in REASON_MAP:
        if re.search(pat, tl):
            return word
    return 'out'

def person_for(title, owner):
    tl = title.strip().lower()
    if re.match(r'^e\s', tl):  # Briana's shorthand: "E XC ..." = Emmy
        return 'Emmy'
    for key, name in PEOPLE:
        if re.match(r'^' + key + r'\b', tl):
            return name
    if (is_xc(title) or 'pasta' in tl) and owner == 'Briana':
        return 'Emmy'  # XC/pasta events on Briana's calendar are Emmy's
    return owner or 'Family'

def overlaps_window(ev):
    s = datetime.fromisoformat(ev['start_time']).astimezone(TZ)
    e = datetime.fromisoformat(ev['end_time']).astimezone(TZ) if ev.get('end_time') else s
    ws = s.replace(hour=DINNER_START[0], minute=DINNER_START[1], second=0)
    we = s.replace(hour=DINNER_END[0], minute=DINNER_END[1], second=0)
    return (s < we and e > ws), s, e

def search(cal, account, start, end):
    cmd = ['tools', 'google-calendar', 'search', '--calendar-id', cal,
           '--start-date', start, '--end-date', end, '--limit', '50', '--json']
    if account:
        cmd += ['--account-id', account]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        print(f'  search failed for {cal}: {r.stderr.strip()[:120]}', file=sys.stderr)
        return []
    return json.loads(r.stdout).get('events', [])

def main():
    now = datetime.now(TZ)
    monday = now.date() - timedelta(days=now.weekday())
    dows = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
    hints = {}
    for i in range(7):
        day = monday + timedelta(days=i)
        found = {}  # (person, reason) -> allday bool
        for cal, account, owner in CALENDARS:
            for ev in search(cal, account, day.isoformat(), day.isoformat()):
                title = (ev.get('summary') or '').strip()
                if not title or ev.get('status') == 'cancelled':
                    continue
                reason = reason_for(title)
                if reason is None:
                    continue
                person = person_for(title, owner)
                if ev.get('all_day') or not ev.get('start_time'):
                    named = re.match(r'^(e|emmy|rex|june|rosie|nate|nathan|briana)\b', title.strip().lower())
                    if not named:
                        continue
                    found[(person, reason)] = found.get((person, reason), False) or True
                    continue
                if not overlaps_window(ev)[0]:
                    continue
                found.setdefault((person, reason), False)
        day_hints = []
        for (person, reason), allday in sorted(found.items(),
                key=lambda kv: (ORDER.index(kv[0][0]) if kv[0][0] in ORDER else 99, kv[0][1])):
            day_hints.append(f"{person} - {reason}" + (" (all day)" if allday else ""))
        if day_hints:
            hints[dows[i]] = day_hints
    # Friday baseline: seed Family Pizza Night for the current week unless removed
    week_key = monday.isoformat()
    def _get(path):
        try:
            with urllib.request.urlopen(DB + path, timeout=30) as r:
                return json.loads(r.read() or b'null')
        except Exception:
            return None
    skip = _get('/pizza_skip.json')
    fri = _get('/week/fri.json') or []
    has_pizza = any(isinstance(e, dict) and str(e.get('k', '')).startswith('pizza-') for e in fri)
    if skip == week_key:
        print('pizza: removed by family this week, not reseeding')
    elif has_pizza:
        print('pizza: already on Friday')
    else:
        fri.append({'k': 'pizza-' + week_key, 't': 'n', 'label': 'Family Pizza Night'})
        body2 = json.dumps(fri).encode()
        req2 = urllib.request.Request(DB + '/week/fri.json', data=body2, method='PUT',
                                      headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req2, timeout=30) as resp2:
            resp2.read()
        print('pizza: seeded Family Pizza Night on Friday')
    body = json.dumps(hints).encode()
    req = urllib.request.Request(DB + '/hints.json', data=body, method='PUT',
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()
    print(json.dumps(hints, indent=1))

if __name__ == '__main__':
    main()
