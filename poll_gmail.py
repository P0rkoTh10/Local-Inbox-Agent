"""
Polls Gmail every 60s - detects new INBOX emails (max 20).
GENERAL RULE: never send emails - this script must not use gmail.send / compose / drafts.create.

Note: this is a public, sanitized version. The original system prompt contained a
personal identity that has been replaced with a generic placeholder. Paths such as
CLIENT_SECRET, TOKEN_PATH, INBOX_DIR and GCAL_JS are absolute on the original author's
machine - adapt them to your environment (ideally via a .env or central config).
"""
import os, json, time, base64, re, pathlib, sys, shutil, requests
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except: pass
try: sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except: pass
from datetime import datetime

# --- Identity + Categorization via Gemma ---
CATEGORIES = ["spam","social","bills","official communications","purchases","subscriptions","payments","friends and family","work","job offers","events/appointments","housing","important","other"]
# Replace this placeholder identity with your own (or move it to a config file).
IDENTITY = "The user is Yourself (you@example.com). When you see emails from/to this user, treat them as 'me' (human sender or recipient)."
SYSTEM_PROMPT = f"""{IDENTITY}
You are an assistant that sorts emails into categories.
Valid categories (choose EXACTLY one, lowercase, copy it exactly):
spam, social, bills, official communications, purchases, subscriptions, payments, friends and family, work, job offers, events/appointments, housing, important, other
Priority rules (in order):
- PRIORITY 0 - NEVER "work" for job boards: IF sender contains indeed.com, glassdoor.com, linkedin.com (any subdomain: match.indeed.com, donotreply@indeed.com, noreply@glassdoor.com, messages-noreply@linkedin.com, etc.) -> NEVER category "work" nor "job offers" (they are automatic, not addressed personally to you). At most "social" (for LinkedIn invitations) or "other"/"spam". Never "work".
- PRIORITY 1 - spam: IF sender contains noreply@glassdoor.com, donotreply@indeed.com, donotreply@match.indeed.com, indeed.com, glassdoor.com, messages-noreply@linkedin.com, noreply@linkedin.com, noreply@hashlist, or promotional ("you might like"/"product week") -> ALWAYS spam, NEVER work/job offers/purchases. All automatic job announcements go here (or in "other" if Indeed - see deterministic).
- PRIORITY 1b - Indeed -> other: emails from Indeed (any indeed.com subdomain) go to "other" (not "work"), unless already classified spam above.
- housing: ALWAYS if sender contains casa.it, immobiliare.it, idealista.it
- job offers: ONLY if personal email from a human (e.g. recruiter@company.com writes "Hello You..."). If automatic -> spam/other (see above) - NEVER if sender is indeed/glassdoor/linkedin
- purchases: ONLY real purchases (delivery/order confirmations) - NOT recommendations
- important: ALWAYS if subject contains account security, deadline, new device access
- social: LinkedIn invitations, Instagram, Facebook. LinkedIn job alert -> social or spam, NEVER work.
- payments: fintech/payment services, PayPal, receipts
- bills: utilities (electricity/gas/water/telecom)
- subscriptions: streaming, recurring memberships
- official communications: government, public administration
- events/appointments: calendar invites (if you see meeting date/time)
- work: real operational work communications - NEVER if sender is indeed/linkedin/glassdoor
- friends and family: ONLY personal between private individuals, NOT to a work domain and NOT LinkedIn
- other: if in doubt (and by default for non-spam Indeed automatic emails)
Reply with ONE single category from the list, nothing else.
"""

# --- Deterministic: job boards never "work" ---
def deterministic_category(headers):
    """Return a forced category if it matches a job board, otherwise None. Prevents Gemma from putting Indeed/LinkedIn/Glassdoor into 'work'."""
    from_l = (headers.get("From") or "").lower()
    if "indeed" in from_l:
        return "other"
    if "glassdoor" in from_l:
        return "spam"
    if "linkedin.com" in from_l or "linkedin" in from_l:
        return "social"
    return None

SKIP_APPOINTMENT_CATEGORIES = {"spam", "social", "other", "housing"}

def get_appointment_prompt():
    oggi = datetime.now().strftime("%Y-%m-%d %H:%M")
    year = datetime.now().year
    return f"""{IDENTITY}
Today is {oggi} (Europe/Rome). Use this as the year reference: {year}, do not invent another year.
Analyze this email and determine whether it contains an appointment/event with a concrete date and time (e.g. first day, meeting, onboarding, call).
EXCLUDE strictly: promotions/discounts/commercial offers, generic subscription deadlines, marketing notifications. Only personal events where YOU MUST physically attend or connect (meeting, onboarding, call, appointment).
If YES, reply EXACTLY in JSON on ONE line:
{{"date": "YYYY-MM-DD HH:MM", "title": "Short title", "location": "full physical address (e.g. 230 Example Street, City)", "description": "1 descriptive sentence", "todo": "what to do beforehand (e.g. bring badge, documents)", "link": "call url if online meeting, otherwise null"}}
Field 'location' rules: if the email has no physical address use "null". If it is an online meeting (Zoom/Meet/Teams/call) use "Online".
Field 'link' rules: if online meeting (Zoom/Meet/Teams) always extract the call URL (e.g. https://meet.google.com/...). If no call or link, use null.
If the date has no year, use {year}. If the time is missing, use 09:00.
If NO (no appointment), reply EXACTLY: NO APPOINTMENT
Do not add anything else, only JSON or NO APPOINTMENT.
Example YES (physical): {{"date": "{year}-09-01 09:00", "title": "Onboarding - first day", "location": "230 Example Street, City", "description": "First day at the company", "todo": "Bring badge and documents", "link": null}}
Example YES (online): {{"date": "{year}-09-03 14:00", "title": "Technical interview", "location": "Online", "description": "Technical interview team", "todo": "Connect 5 minutes early", "link": "https://meet.google.com/abc-defg-hij"}}
"""

OLLAMA_URL = "http://localhost:11434/api/generate"
GEMMA_MODEL = "gemma3:4b"
CAT_TO_LABEL = {
    "spam":"SPAM","social":"social","bills":"bills",
    "official communications":"official communications","purchases":"purchases",
    "subscriptions":"subscriptions","payments":"payments",
    "friends and family":"friends and family","work":"work","job offers":"Job offers",
    "events/appointments":"events/appointments","housing":"housing",
    "important":"Important","other":"other",
}

GCAL_JS = r"C:\path\to\gcal\scripts\gcal.js"

# --- Paths (adjust to your environment) ---
CLIENT_SECRET = r"C:\path\to\gcal\scripts\client_secret.json"
TOKEN_PATH = r"C:\path\to\watcher\token_gmail.json"
STATE_PATH = r"C:\path\to\watcher\state.json"
INBOX_DIR = r"C:\path\to\watcher\Inbox"
TO_SORT = os.path.join(INBOX_DIR, "to_sort")
SORTED = os.path.join(INBOX_DIR, "sorted")
SCHEDULE_PATH = os.path.join(INBOX_DIR, "schedule.txt")

# --- Maps / travel (Routes API) ---
MAPS_CONFIG_PATH = pathlib.Path(__file__).parent / "maps_config.json"

def load_maps_config():
    """Reads api_key and home_address from maps_config.json (gitignored). Returns dict or {}."""
    try:
        if MAPS_CONFIG_PATH.exists():
            return json.loads(MAPS_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  Maps config error: {e}")
    return {}

def get_travel_info(destination, departure_dt):
    """Computes driving time from home to destination via Google Routes API.
    destination: address string. departure_dt: desired arrival/departure datetime.
    Returns dict with duration_min, distance_km, leave_by (str) or None."""
    cfg = load_maps_config()
    key = cfg.get("api_key", "")
    origin = cfg.get("home_address", "")
    if not key or not origin or not destination:
        return None
    url = "https://routes.googleapis.com/directions/v2:computeRoutes"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": "routes.duration,routes.distanceMeters",
    }
    departure_iso = departure_dt.strftime("%Y-%m-%dT%H:%M:%S+02:00")
    body = {
        "origin": {"address": origin},
        "destination": {"address": destination},
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
        "departureTime": departure_iso,
    }
    try:
        r = requests.post(url, json=body, headers=headers, timeout=20)
        if r.status_code != 200:
            print(f"  Maps HTTP error {r.status_code}: {r.text[:150]}")
            return None
        data = r.json()
        route = (data.get("routes") or [None])[0]
        if not route or not route.get("duration"):
            print(f"  Maps: no route ({data.get('status','')})")
            return None
        dur_s = int(route["duration"].rstrip("s")) if route["duration"].endswith("s") else int(float(route["duration"]))
        dist_m = int(route.get("distanceMeters", 0))
        dur_min = round(dur_s / 60)
        dist_km = round(dist_m / 1000, 1)
        leave_by = departure_dt.timestamp() - dur_s
        from datetime import datetime as _dt
        leave_by_str = _dt.fromtimestamp(leave_by).strftime("%H:%M")
        return {"duration_min": dur_min, "distance_km": dist_km, "leave_by": leave_by_str}
    except Exception as e:
        print(f"  Maps error: {e}")
        return None

TRAVEL_BUFFER_MIN = 15  # margin: arrive X minutes before the appointment

def _create_travel_event(appt_titolo, appt_dt, travel, destination):
    """Creates a calendar event 'Travel to X' starting travel.duration_min before the appointment.
    Arrives TRAVEL_BUFFER_MIN minutes early (location = destination)."""
    import subprocess as _sp
    from datetime import datetime, timedelta
    cfg = load_maps_config()
    home = cfg.get("home_address", "")
    arrival_dt = appt_dt - timedelta(minutes=TRAVEL_BUFFER_MIN)
    start_dt = arrival_dt - timedelta(minutes=travel["duration_min"])
    start_iso = start_dt.strftime("%Y-%m-%dT%H:%M:%S+02:00")
    end_iso = arrival_dt.strftime("%Y-%m-%dT%H:%M:%S+02:00")
    titolo_viaggio = f"Travel to: {appt_titolo}"
    descr_viaggio = (f"Depart from {home} at {start_dt.strftime('%H:%M')}. "
                     f"Arrive at {destination} at {arrival_dt.strftime('%H:%M')} "
                     f"({TRAVEL_BUFFER_MIN} min before the appointment at {appt_dt.strftime('%H:%M')}). "
                     f"By car: {travel['distance_km']} km, ~{travel['duration_min']} min.")
    cmd = ["node", GCAL_JS, "create", titolo_viaggio, start_iso, end_iso, destination, descr_viaggio]
    res = _sp.run(cmd, capture_output=True, text=True, timeout=30, cwd=os.path.dirname(GCAL_JS))
    if res.returncode == 0:
        print(f"  Calendar travel -> {titolo_viaggio} @ {start_dt.strftime('%Y-%m-%d %H:%M')} -> {arrival_dt.strftime('%H:%M')} [{destination[:40]}]")
        return res.stdout.strip()
    else:
        print(f"  Calendar travel error: {res.stderr.strip()[:300]}")
        return None

def create_calendar_event(mail_data, appt_data):
    """Creates a Google Calendar event via gcal.js. appt_data = dict with date,title,location,description,todo,link."""
    import subprocess, shlex
    from datetime import datetime, timedelta
    try:
        date_str = appt_data.get("date","").strip()
        titolo = appt_data.get("title","").strip()
        descr = appt_data.get("description","").strip()
        todo = appt_data.get("todo","").strip()
        luogo = (appt_data.get("location") or "").strip()
        link = (appt_data.get("link") or "").strip()
        if not date_str or not titolo:
            return None
        dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
        event_start = dt
        end_dt = event_start + timedelta(hours=1)
        start_iso = event_start.strftime("%Y-%m-%dT%H:%M:%S+02:00")
        end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%S+02:00")
        headers = mail_data.get("headers", {})
        location = luogo.lower() if luogo else ""
        if "online" in location:
            location = "Online"
        description = f"{descr}"
        if link and location == "Online":
            description += f"\n\nCall link: {link}"
        travel = None
        if location and location != "Online":
            try:
                travel = get_travel_info(location, event_start.replace(tzinfo=None))
                if travel:
                    description += f"\n\nTravel (by car from home): {travel['distance_km']} km, ~{travel['duration_min']} min. Leave by {travel['leave_by']}."
            except Exception as e:
                print(f"  Maps travel error: {e}")
        mail_id = mail_data.get("id","")
        mail_date = headers.get("Date","")
        gmail_link = f"https://mail.google.com/mail/u/0/#inbox/{mail_id}" if mail_id else ""
        description += f"\n\nTODO: {todo}\n\nEmail: {headers.get('Subject','')} | From: {headers.get('From','')} | Date: {mail_date} | id:{mail_id}\nOpen email: {gmail_link}"
        if travel:
            try:
                _create_travel_event(titolo, dt, travel, location)
            except Exception as e:
                print(f"  Calendar travel error: {e}")
        import subprocess as _sp
        cmd = ["node", GCAL_JS, "create", titolo, start_iso, end_iso, location, description]
        res = _sp.run(cmd, capture_output=True, text=True, timeout=30, cwd=os.path.dirname(GCAL_JS))
        if res.returncode == 0:
            print(f"  Calendar -> {titolo} @ {date_str} [{location}]")
            return res.stdout.strip()
        else:
            print(f"  Calendar error: {res.stderr.strip()[:300]}")
            return None
    except Exception as e:
        print(f"  Calendar error: {e}")
        return None

def extract_appointment_and_update_schedule(mail_data):
    """Calls Gemma to extract an appointment (with description and todo), updates schedule.txt and creates a Calendar event."""
    cat = (mail_data.get("categoria") or "").strip().lower()
    if cat in SKIP_APPOINTMENT_CATEGORIES:
        print(f"  Schedule skip: category '{cat}' excluded (no appointment)")
        return None
    headers = mail_data.get("headers", {})
    body = mail_data.get("body_text","")[:4000]
    prompt = f"Subject: {headers.get('Subject','')}\nFrom: {headers.get('From','')}\nTo: {headers.get('To','')}\nDate header: {headers.get('Date','')}\nBody:\n{body[:3000]}"
    try:
        system = get_appointment_prompt()
        r = requests.post(OLLAMA_URL, json={"model": GEMMA_MODEL, "prompt": prompt, "system": system, "stream": False, "options": {"temperature":0.1, "num_predict":120}}, timeout=120)
        r.raise_for_status()
        txt = r.json().get("response","").strip()
        if "NO APPOINTMENT" in txt.upper():
            return None
        import re as _re, json as _json
        m = _re.search(r"\{.*\}", txt, re.DOTALL)
        if not m:
            return None
        try:
            obj = _json.loads(m.group(0))
        except:
            return None
        data = obj.get("date","").strip()
        titolo = obj.get("title","").strip()
        luogo = (obj.get("location") or "").strip()
        descr = obj.get("description","").strip().replace("\n"," ")
        todo = obj.get("todo","").strip().replace("\n"," ")
        if not data or not titolo:
            return None
        if not _re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", data):
            return None
        line = f"{data} | {titolo} | {luogo} | {descr} | TODO: {todo} | id:{mail_data.get('id','')}"
        os.makedirs(os.path.dirname(SCHEDULE_PATH), exist_ok=True)
        existing = ""
        if os.path.exists(SCHEDULE_PATH):
            existing = open(SCHEDULE_PATH, encoding="utf-8").read()
            if mail_data.get("id") in existing:
                return line
        with open(SCHEDULE_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        print(f"  Schedule -> {line}")
        try:
            create_calendar_event(mail_data, obj)
        except Exception as e:
            print(f"  Calendar error (non-blocking): {e}")
        return line
    except Exception as e:
        print(f"  Schedule error: {e}")
        return None

def categorize_and_move(service, mail_dir, mail_data):
    """Calls Gemma, saves the category, moves to sorted/<cat>, applies the Gmail label and updates schedule.txt."""
    if mail_data.get("categoria"):
        try:
            if os.path.exists(SCHEDULE_PATH):
                if mail_data.get("id") not in open(SCHEDULE_PATH, encoding="utf-8").read():
                    extract_appointment_and_update_schedule(mail_data)
            else:
                extract_appointment_and_update_schedule(mail_data)
        except: pass
        return mail_data["categoria"]
    headers = mail_data.get("headers", {})
    forced = deterministic_category(headers)
    if forced:
        cat = forced
        print(f"  Deterministic -> {cat} (From: {headers.get('From','')})")
        mail_data["categoria"] = cat
        jpath = os.path.join(mail_dir, "mail.json")
        try:
            with open(jpath, "w", encoding="utf-8") as f: json.dump(mail_data, f, indent=2, ensure_ascii=False)
            with open(os.path.join(mail_dir, "categoria.txt"), "w", encoding="utf-8") as f: f.write(cat)
        except Exception as e:
            print(f"  Error saving category: {e}")
        dest_root = os.path.join(SORTED, cat)
        os.makedirs(dest_root, exist_ok=True)
        dest = os.path.join(dest_root, os.path.basename(mail_dir))
        moved_dir = mail_dir
        if os.path.abspath(mail_dir) != os.path.abspath(dest):
            if not os.path.exists(dest):
                try:
                    shutil.move(mail_dir, dest)
                    moved_dir = dest
                    print(f"  Sorted -> {cat} : {os.path.basename(dest)}")
                except Exception as e:
                    print(f"  Move error: {e}")
            else:
                print(f"  Already in sorted/{cat}")
                moved_dir = dest
        try:
            apply_gmail_label(service, mail_data.get("id"), cat)
        except Exception as e:
            print(f"  Gmail label error: {e}")
        try:
            moved_data = mail_data
            if os.path.exists(os.path.join(moved_dir, "mail.json")):
                moved_data = json.loads(open(os.path.join(moved_dir, "mail.json"), encoding="utf-8-sig").read())
            extract_appointment_and_update_schedule(moved_data)
        except Exception as e:
            print(f"  Schedule error: {e}")
        return cat
    body = mail_data.get("body_text","")[:3000]
    prompt = f"Subject: {headers.get('Subject','')}\nFrom: {headers.get('From','')}\nBody:\n{body[:2000]}\nCategory?"
    try:
        r = requests.post(OLLAMA_URL, json={"model": GEMMA_MODEL, "prompt": prompt, "system": SYSTEM_PROMPT, "stream": False, "options": {"temperature":0.1, "num_predict":20}}, timeout=120)
        r.raise_for_status()
        txt = r.json().get("response","").strip().lower()
        cat = "other"
        for c in CATEGORIES:
            if c.lower() in txt:
                cat = c; break
        if cat not in CATEGORIES: cat="other"
    except Exception as e:
        print(f"  Gemma error: {e} -> other")
        cat="other"
    _forced_check = deterministic_category(headers)
    if _forced_check and cat == "work":
        print(f"  Safety override: Gemma -> work blocked for {headers.get('From','')} -> {_forced_check}")
        cat = _forced_check
    if cat == "work" and any(k in headers.get("From","").lower() for k in ["indeed", "glassdoor", "linkedin"]):
        print(f"  Safety override: work blocked for job board -> other")
        cat = "other"
    mail_data["categoria"]=cat
    jpath=os.path.join(mail_dir,"mail.json")
    try:
        with open(jpath,"w",encoding="utf-8") as f: json.dump(mail_data,f,indent=2,ensure_ascii=False)
        with open(os.path.join(mail_dir,"categoria.txt"),"w",encoding="utf-8") as f: f.write(cat)
    except Exception as e:
        print(f"  Error saving category: {e}")
    dest_root=os.path.join(SORTED, cat)
    os.makedirs(dest_root, exist_ok=True)
    dest=os.path.join(dest_root, os.path.basename(mail_dir))
    moved_dir=mail_dir
    if os.path.abspath(mail_dir) != os.path.abspath(dest):
        if not os.path.exists(dest):
            try:
                shutil.move(mail_dir, dest)
                moved_dir=dest
                print(f"  Sorted -> {cat} : {os.path.basename(dest)}")
            except Exception as e:
                print(f"  Move error: {e}")
        else:
            print(f"  Already in sorted/{cat}")
            moved_dir=dest
    try:
        apply_gmail_label(service, mail_data.get("id"), cat)
    except Exception as e:
        print(f"  Gmail label error: {e}")
    try:
        moved_data = mail_data
        if os.path.exists(os.path.join(moved_dir, "mail.json")):
            moved_data = json.loads(open(os.path.join(moved_dir, "mail.json"), encoding="utf-8-sig").read())
        extract_appointment_and_update_schedule(moved_data)
    except Exception as e:
        print(f"  Schedule error: {e}")
    return cat

def apply_gmail_label(service, msg_id, categoria):
    if not hasattr(apply_gmail_label,"label_map"):
        labels=service.users().labels().list(userId="me").execute().get("labels",[])
        name_to_id={l['name']:l['id'] for l in labels}
        lower={l['name'].lower():l['id'] for l in labels}
        m={}
        for cat, lname in CAT_TO_LABEL.items():
            lid=name_to_id.get(lname) or lower.get(lname.lower())
            if lid: m[cat]=lid
        apply_gmail_label.label_map=m
    label_map=apply_gmail_label.label_map
    lid=label_map.get(categoria)
    if not lid: return
    remove_ids=[v for k,v in label_map.items() if k!=categoria]
    try:
        service.users().messages().modify(userId="me", id=msg_id, body={"addLabelIds":[lid], "removeLabelIds":remove_ids}).execute()
        print(f"  Gmail label -> {categoria} [{lid}]")
    except Exception as e:
        print(f"  Label error {categoria}: {e}")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.labels",
    # GENERAL RULE: never send emails - no gmail.send / gmail.compose
    "https://www.googleapis.com/auth/calendar",
]

def get_service():
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
            creds = flow.run_local_server(port=3000, prompt="consent", access_type="offline")
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)

def load_state():
    if os.path.exists(STATE_PATH):
        try:
            return json.loads(open(STATE_PATH, encoding="utf-8").read())
        except: pass
    return {"seen_ids": [], "last_check": None}

def save_state(s):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)

def _decode(data):
    if not data:
        return ""
    data = data.replace("-", "+").replace("_", "/")
    pad = len(data) % 4
    if pad:
        data += "=" * (4 - pad)
    return base64.b64decode(data).decode("utf-8", errors="ignore")

def _html_to_text(html):
    text = re.sub(r"<style.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</div\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def extract_body_and_attachments(payload):
    """Returns (body_text, [(filename, mime, attachmentId or data)])"""
    body_text = ""
    attachments = []

    def walk(parts):
        nonlocal body_text
        for p in parts:
            mime = p.get("mimeType", "")
            filename = p.get("filename", "")
            body = p.get("body", {})
            data = body.get("data")
            att_id = body.get("attachmentId")
            if filename:
                attachments.append((filename, mime, att_id, data))
            elif mime == "text/plain" and data and not body_text:
                body_text = _decode(data)
            elif mime == "text/html" and data and not body_text:
                body_text = _html_to_text(_decode(data))
            if "parts" in p:
                walk(p["parts"])
                if mime.startswith("multipart/") and not body_text:
                    for sub in p["parts"]:
                        if sub.get("mimeType") == "text/plain" and sub.get("body", {}).get("data"):
                            body_text = _decode(sub["body"]["data"])
                            break
                    if not body_text:
                        for sub in p["parts"]:
                            if sub.get("mimeType") == "text/html" and sub.get("body", {}).get("data"):
                                body_text = _html_to_text(_decode(sub["body"]["data"]))
                                break

    mime = payload.get("mimeType", "")
    if "parts" in payload:
        walk(payload["parts"])
        if not body_text and payload.get("body", {}).get("data"):
            raw = _decode(payload["body"]["data"])
            body_text = raw if mime == "text/plain" else _html_to_text(raw)
    else:
        raw = _decode(payload.get("body", {}).get("data", ""))
        if raw:
            body_text = raw if mime == "text/plain" else _html_to_text(raw)

    return body_text.strip(), attachments

def poll_once(service, state, first_run=False):
    res = service.users().messages().list(userId="me", maxResults=20, q="in:inbox").execute()
    msgs = res.get("messages", [])
    ids = [m["id"] for m in msgs]
    seen = set(state.get("seen_ids", []))

    if first_run and not seen:
        state["seen_ids"] = ids
        state["last_check"] = datetime.now().isoformat()
        save_state(state)
        print(f"[{state['last_check']}] Initialized with {len(ids)} emails. No triggers.")
        return

    existing_ids = set()
    for base in [TO_SORT, SORTED]:
        if os.path.exists(base):
            for root, dirs, files in os.walk(base):
                if "mail.json" in files:
                    try:
                        j = json.loads(open(os.path.join(root, "mail.json"), encoding="utf-8-sig").read())
                        if j.get("id"): existing_ids.add(j["id"])
                    except: pass
                for d in dirs:
                    if "_" in d and len(d.split("_"))>=2:
                        try:
                            existing_ids.add(d.split("_")[1])
                        except: pass
    new_ids = [i for i in ids if i not in seen and i not in existing_ids]
    if new_ids:
        for nid in reversed(new_ids):
            try:
                m = service.users().messages().get(userId="me", id=nid, format="full").execute()
                hdrs = {h["name"]: h["value"] for h in m.get("payload", {}).get("headers", [])}
                body_text, atts = extract_body_and_attachments(m.get("payload", {}))
                date_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                raw_subj = hdrs.get("Subject", "no_subject") or "no_subject"
                tmp = re.sub(r'[\\/:*?"<>|]', "_", raw_subj)
                tmp = re.sub(r'[^\x00-\x7F]+', '_', tmp)
                safe_subj = tmp.strip().strip("._ ")[:40].strip()
                if not safe_subj:
                    safe_subj = "no_subject"
                os.makedirs(TO_SORT, exist_ok=True)
                mail_dir = os.path.join(TO_SORT, f"{date_str}_{nid}_{safe_subj}")
                os.makedirs(mail_dir, exist_ok=True)
                attach_dir = os.path.join(mail_dir, "attachments")
                saved_attachments = []
                for filename, mime, att_id, inline_data in atts:
                    safe_name = re.sub(r'[\\/:*?"<>|]', "_", filename) or f"attach_{len(saved_attachments)}"
                    dest = os.path.join(attach_dir, safe_name)
                    os.makedirs(attach_dir, exist_ok=True)
                    try:
                        if att_id:
                            att = service.users().messages().attachments().get(userId="me", messageId=nid, id=att_id).execute()
                            raw = base64.urlsafe_b64decode(att["data"] + "===" )
                            with open(dest, "wb") as f:
                                f.write(raw)
                        elif inline_data:
                            with open(dest, "wb") as f:
                                f.write(base64.urlsafe_b64decode(inline_data + "==="))
                        saved_attachments.append({"filename": filename, "saved_as": dest, "mime": mime})
                    except Exception as e:
                        print(f"  Attachment {filename} error: {e}")
                        saved_attachments.append({"filename": filename, "error": str(e), "mime": mime})

                out = {
                    "id": nid,
                    "threadId": m.get("threadId"),
                    "headers": {
                        "From": hdrs.get("From", ""),
                        "To": hdrs.get("To", ""),
                        "Cc": hdrs.get("Cc", ""),
                        "Subject": hdrs.get("Subject", ""),
                        "Date": hdrs.get("Date", ""),
                    },
                    "body_text": body_text,
                    "attachments": [{"filename": a["filename"], "saved_as": a.get("saved_as",""), "mime": a["mime"]} for a in saved_attachments],
                    "labels": m.get("labelIds", []),
                    "internalDate": m.get("internalDate"),
                }
                json_path = os.path.join(mail_dir, "mail.json")
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(out, f, indent=2, ensure_ascii=False)

                print(f"--- NEW EMAIL {nid} ---")
                print(f"  From: {hdrs.get('From','')}")
                print(f"  Subject: {hdrs.get('Subject','')}")
                print(f"  Saved: {json_path}")
                print(f"  Body chars: {len(body_text)} | Attachments: {len(saved_attachments)}")
                for a in saved_attachments:
                    print(f"    - {a['filename']} -> {a.get('saved_as','')}")
                try:
                    cat = categorize_and_move(service, mail_dir, out)
                    print(f"  -> Autonomous done: {cat}")
                except Exception as e:
                    print(f"  Autonomous error {nid}: {e}")
            except Exception as e:
                print(f"Error reading {nid}: {e}")
        state["seen_ids"] = (new_ids + state.get("seen_ids", []))[:200]
        state["last_check"] = datetime.now().isoformat()
        save_state(state)
    else:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] No new emails (checked {len(ids)}).")

def process_pending_to_sort(service):
    """Sorts any emails left in to_sort (backlog)."""
    if not os.path.exists(TO_SORT):
        return
    for d in os.listdir(TO_SORT):
        dpath = os.path.join(TO_SORT, d)
        if not os.path.isdir(dpath):
            continue
        jpath = os.path.join(dpath, "mail.json")
        if not os.path.exists(jpath):
            continue
        try:
            data = json.loads(open(jpath, encoding="utf-8-sig").read())
        except:
            continue
        if data.get("categoria"):
            try:
                categorize_and_move(service, dpath, data)
            except: pass
            continue
        print(f"[pending] Sorting backlog: {d}")
        try:
            categorize_and_move(service, dpath, data)
        except Exception as e:
            print(f"  pending error {d}: {e}")

if __name__ == "__main__":
    print("Gmail Watcher - polling every 60s + autonomous sorting via Gemma (Ctrl+C to stop)")
    print(f"Token: {TOKEN_PATH}")
    print(f"State: {STATE_PATH}")
    print(f"Inbox: {INBOX_DIR} -> new in {TO_SORT}")
    os.makedirs(TO_SORT, exist_ok=True)
    os.makedirs(SORTED, exist_ok=True)
    service = get_service()
    state = load_state()
    first = len(state.get("seen_ids", [])) == 0
    poll_once(service, state, first_run=first)
    try:
        process_pending_to_sort(service)
    except Exception as e:
        print(f"Backlog error: {e}")
    while True:
        time.sleep(60)
        state = load_state()
        poll_once(service, state, first_run=False)
        try:
            process_pending_to_sort(service)
        except Exception as e:
            print(f"Pending error: {e}")
