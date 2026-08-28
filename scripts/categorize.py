"""
Categorizes emails in Inbox/to_sort/ by calling gemma3:4b via Ollama.
Moves them to Inbox/sorted/<category>/
"""
import os, json, re, shutil, pathlib, requests, time, sys
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except: pass
try: sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except: pass

# Adjust these paths to your environment.
INBOX_BASE = r"C:\path\to\watcher\Inbox"
TO_SORT = os.path.join(INBOX_BASE, "to_sort")
SORTED = os.path.join(INBOX_BASE, "sorted")

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "gemma3:4b"

CATEGORIES = [
    "spam",
    "social",
    "bills",
    "official communications",
    "purchases",
    "subscriptions",
    "payments",
    "friends and family",
    "work",
    "job offers",
    "events/appointments",
    "housing",
    "important",
    "other",
]

SYSTEM_PROMPT = f"""You are an assistant that sorts emails into categories.
Valid categories (choose EXACTLY one, lowercase, copy it exactly):
{', '.join(CATEGORIES)}

Priority rules (in order):
- housing: ALWAYS if sender contains a real-estate site
- job offers: ONLY if personal email from a human (e.g. a recruiter writes you directly). NOT for AUTOMATIC LinkedIn/Glassdoor/Indeed/Hashlist noreply emails -> those go to spam
- spam: ALL automatic job/offer emails (Glassdoor noreply, LinkedIn noreply, Indeed donotreply, Hashlist noreply) + generic unsolicited promotions
- important: ALWAYS if subject contains account security, deadline, new device access
- social: LinkedIn (social only, not jobs), Instagram, Facebook
- payments: fintech/payment services, PayPal, receipts (EUR amount)
- purchases: ONLY real purchases (delivery/order confirmations) - NOT recommendations
- bills: utilities (electricity/gas/water/telecom)
- subscriptions: streaming, recurring memberships
- official communications: government, public administration
- events/appointments: calendar invites
- work: only real operational work communications from colleagues (not offers, not automatic announcements)
- friends and family: personal
- other: if in doubt

Reply with ONE single category from the list, nothing else.
"""

def call_gemma(mail_json):
    headers = mail_json.get("headers", {})
    body = mail_json.get("body_text", "")[:3000]
    prompt = f"""Subject: {headers.get('Subject','')}
From: {headers.get('From','')}
Snippet: {mail_json.get('snippet','')[:200]}

Body:
{body[:2000]}

Category?"""
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "system": SYSTEM_PROMPT,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 20}
    }
    r = requests.post(OLLAMA_URL, json=payload, timeout=120)
    r.raise_for_status()
    txt = r.json().get("response", "").strip().lower()
    for cat in CATEGORIES:
        if cat.lower() in txt:
            return cat
    clean = re.sub(r"[^a-z/ ]", "", txt).strip()
    for cat in CATEGORIES:
        if clean == cat.lower():
            return cat
    return "other"

def process_one(mail_dir):
    mail_path = os.path.join(mail_dir, "mail.json")
    data = json.loads(open(mail_path, encoding="utf-8").read())
    if "categoria" in data:
        return data["categoria"], True

    cat = call_gemma(data)
    if cat not in CATEGORIES:
        cat = "other"
    data["categoria"] = cat
    with open(mail_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    with open(os.path.join(mail_dir, "categoria.txt"), "w", encoding="utf-8") as f:
        f.write(cat)
    return cat, False

if __name__ == "__main__":
    os.makedirs(SORTED, exist_ok=True)
    if not os.path.exists(TO_SORT):
        print(f"Missing {TO_SORT}")
        exit(1)
    dirs = [d for d in os.listdir(TO_SORT) if os.path.isdir(os.path.join(TO_SORT, d))]
    dirs.sort()
    print(f"Found {len(dirs)} emails in to_sort - model {MODEL}")
    for d in dirs:
        src = os.path.join(TO_SORT, d)
        try:
            cat, already = process_one(src)
            dest_root = os.path.join(SORTED, cat)
            os.makedirs(dest_root, exist_ok=True)
            dest = os.path.join(dest_root, d)
            if os.path.exists(dest):
                print(f"  [skip] {d} -> {cat} (already in sorted)")
                if os.path.exists(src) and src != dest:
                    try: shutil.rmtree(src)
                    except: pass
                continue
            if not already:
                print(f"  {d} -> {cat}")
            else:
                print(f"  {d} (already categorized) -> {cat}")
            shutil.move(src, dest)
            time.sleep(0.5)
        except Exception as e:
            print(f"  ERROR {d}: {e}")
    print("Done.")
    for cat in CATEGORIES:
        p = os.path.join(SORTED, cat)
        n = len(os.listdir(p)) if os.path.exists(p) else 0
        if n: print(f"  {cat}: {n}")
