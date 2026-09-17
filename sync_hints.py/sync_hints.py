#!/usr/bin/env python3
"""Weekly dinner-hint sync: reads family calendars, writes /hints to the RTDB."""
import subprocess, json, re, sys, urllib.request
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo('America/New_York')
DB = 'https://family-recipe-book-f493c-default-rtdb.firebaseio.com'
DINNER_START = (17, 30)   # 5:30pm
DINNER_END = (19, 30)     # 7:30pm

CALENDARS = [
    # (calendar_id, account, owner_attr)
    ('pouchman27@gmail.com', None, 'Nate'),
    ('weir.nathan@gmail.com', 'weir.nathan@gmail.com', 'Nate'),
    ('briana.weir@gmail.com', 'briana.weir@gmail.com', 'Briana'),
    ('42iuof97ibdlp9m0bg739inl8u3bg8li@import.calendar.google.com', None, None),  # Cozi family import
    ('family14912820059316396580@group.calendar.google.com', None, None),         # Google Family
    ('sgvo1oqfhaqr6a580kpejmd75k@group.calendar.google.com', None, None),         # FHE
]
KIDS = [('emmy', 'Emmy'), ('rex', 'Rex'), ('june', 'June'), ('rosie', 'Rosie')]
ORDER = ['Nate', 'Briana', 'Rex', 'Emmy', 'June', 'Rosie', 'Family']

def person_for(title, owner):
    t = title.strip()
    tl = t.lower()
    if re.match(r'^e\s', tl) or tl.startswith('e xc'):  # Briana's shorthand: "E XC practice"
        return 'Emmy', re.sub(r'^e\s+', '', t, flags=re.I).strip()
    for key, name in KIDS + [('nate', 'Nate'), ('nathan', 'Nate'), ('briana', 'Briana')]:
        m = re.match(r'^' + key + r':\s*', tl)
        if m:
            return name, t[m.end():].strip()
        if re.match(r'^' + key + r'\b', tl):
            return name, t
    return (owner or 'Family'), t

def overlaps_window(ev):
    if ev.get('all_day') or not ev.get('start_time'):
        return False
    s = datetime.fromisoformat(ev['start_time']).astimezone(TZ)
    e = datetime.fromisoformat(ev['end_time']).astimezone(TZ) if ev.get('end_time') else s
    ws = s.replace(hour=DINNER_START[0], minute=DINNER_START[1], second=0)
    we = s.replace(hour=DINNER_END[0], minute=DINNER_END[1], second=0)
    return (s < we and e > ws), s, e

def fmt_time(dt):
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d}" if dt.minute else f"{h}"

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
    monday = (now.date() - timedelta(days=now.weekday()))
    days = [(monday + timedelta(days=i)) for i in range(7)]
    dows = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
    hints = {}
    for i, day in enumerate(days):
        found = {}  # normalized title -> {'people': set, 's':, 'e':, 'allday':, 'display':}
        for cal, account, owner in CALENDARS:
            for ev in search(cal, account, day.isoformat(), day.isoformat()):
                title = (ev.get('summary') or '').strip()
                if not title or ev.get('status') == 'cancelled':
                    continue
                person, clean = person_for(title, owner)
                if ev.get('all_day') or not ev.get('start_time'):
                    # all-day: only when a person is named
                    if person in ('Family',) or (owner is not None and person == owner and not re.match(r'^(e|emmy|rex|june|rosie|nate|nathan|briana)\b', title.lower())):
                        continue
                    key = re.sub(r'\W+', '', clean.lower())
                    f = found.setdefault(key, {'people': set(), 'allday': True, 'display': clean})
                    f['people'].add(person)
                    continue
                ov = overlaps_window(ev)
                if not ov[0]:
                    continue
                _, s, e = ov
                key = re.sub(r'\W+', '', clean.lower())
                f = found.setdefault(key, {'people': set(), 's': s, 'e': e, 'allday': False, 'display': clean})
                f['people'].add(person)
                f['s'] = min(f['s'], s); f['e'] = max(f['e'], e)
        day_hints = []
        for f in found.values():
            people = sorted(f['people'], key=lambda p: ORDER.index(p) if p in ORDER else 99)
            who = ' & '.join(people)
            if f['allday']:
                day_hints.append(f"{who} may be out all day ({f['display']})")
            else:
                s, e = f['s'], f['e']
                if (s.hour, s.minute) <= DINNER_START:
                    day_hints.append(f"{who} may be out ({f['display']} until {fmt_time(e)})")
                else:
                    day_hints.append(f"{who} may be out ({f['display']} from {fmt_time(s)})")
        if day_hints:
            hints[dows[i]] = day_hints
    body = json.dumps(hints).encode()
    req = urllib.request.Request(DB + '/hints.json', data=body, method='PUT',
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()
    print(json.dumps(hints, indent=1))

if __name__ == '__main__':
    main()
