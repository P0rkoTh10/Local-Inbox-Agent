"""
Applies Gmail labels based on categoria.txt / mail.json
Mapping: housing->housing, job offers->Job offers, important->Important, spam->SPAM (system), rest 1:1
"""
import os, json, sys
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except: pass
try: sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except: pass

# Adjust these paths to your environment.
INBOX_BASE = r"C:\path\to\watcher\inbox"
SMISTATE = os.path.join(INBOX_BASE, "smistate")
DA_SMISTARE = os.path.join(INBOX_BASE, "da_smistare")
TOKEN = r"C:\path\to\watcher\token_gmail.json"
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly','https://www.googleapis.com/auth/gmail.modify','https://www.googleapis.com/auth/gmail.labels','https://www.googleapis.com/auth/calendar']

# Local category -> Gmail label name
CAT_TO_LABEL = {
    "spam": "SPAM",
    "social": "social",
    "bills": "bills",
    "official communications": "official communications",
    "purchases": "purchases",
    "subscriptions": "subscriptions",
    "payments": "payments",
    "friends and family": "friends and family",
    "work": "work",
    "job offers": "Job offers",
    "events/appointments": "events/appointments",
    "housing": "housing",
    "important": "Important",
    "other": "other",
}

def get_service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    from google.auth.transport.requests import Request
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build('gmail','v1',credentials=creds)

def build_label_map(svc):
    labels = svc.users().labels().list(userId='me').execute().get('labels',[])
    name_to_id = {l['name']: l['id'] for l in labels}
    lower = {l['name'].lower(): l['id'] for l in labels}
    mapping = {}
    for cat, lname in CAT_TO_LABEL.items():
        lid = name_to_id.get(lname) or lower.get(lname.lower())
        if not lid:
            print(f"WARN missing label for category {cat} -> {lname}")
        mapping[cat] = lid
    return mapping

def collect_mails():
    roots = []
    for base in [SMISTATE, DA_SMISTARE]:
        if not os.path.exists(base):
            continue
        for root, dirs, files in os.walk(base):
            if "mail.json" in files:
                roots.append(root)
    return roots

if __name__ == "__main__":
    svc = get_service()
    label_map = build_label_map(svc)
    print("Category->labelId map:")
    for k,v in label_map.items():
        print(f"  {k} -> {v} ({CAT_TO_LABEL[k]})")

    mails = collect_mails()
    print(f"\nFound {len(mails)} emails with categoria.txt/mail.json")
    for mdir in sorted(mails):
        cat = None
        cpath = os.path.join(mdir, "categoria.txt")
        jpath = os.path.join(mdir, "mail.json")
        if os.path.exists(cpath):
            cat = open(cpath, encoding="utf-8-sig").read().strip()
        elif os.path.exists(jpath):
            try:
                cat = json.loads(open(jpath, encoding="utf-8-sig").read()).get("categoria")
            except: pass
        if not cat:
            print(f"  SKIP {mdir} - no category")
            continue
        cat = cat.strip().lower().lstrip("\ufeff")
        matched = None
        for k in CAT_TO_LABEL:
            if k.lower() == cat.lower():
                matched = k
                break
        if not matched:
            print(f"  SKIP {mdir} - unknown category '{cat}'")
            continue
        lid = label_map.get(matched)
        if not lid:
            print(f"  SKIP {mdir} - missing labelId for {matched}")
            continue
        try:
            data = json.loads(open(jpath, encoding="utf-8-sig").read())
            mid = data.get("id")
            if not mid:
                print(f"  SKIP {mdir} - no id")
                continue
            all_ids = [v for v in label_map.values() if v]
            remove_ids = [v for k,v in label_map.items() if k != matched and v != lid]
            body = {"addLabelIds": [lid], "removeLabelIds": remove_ids}
            svc.users().messages().modify(userId="me", id=mid, body=body).execute()
            print(f"  OK {mid} ({data['headers'].get('Subject','')[:40]}) -> {matched} [{lid}]")
        except Exception as e:
            print(f"  ERR {mdir}: {e}")
    print("Done.")
