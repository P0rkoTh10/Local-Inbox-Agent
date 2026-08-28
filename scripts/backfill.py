"""
Backfills the last N emails into Inbox/to_sort/
Does NOT modify poll_gmail.py - replicates the save logic.
"""
import os, json, base64, re
from datetime import datetime

# Adjust these paths to your environment.
CLIENT_SECRET = r"C:\path\to\gcal\scripts\client_secret.json"
TOKEN_PATH = r"C:\path\to\watcher\token_gmail.json"
INBOX_BASE = r"C:\path\to\watcher\Inbox"
TO_SORT = os.path.join(INBOX_BASE, "to_sort")
SORTED = os.path.join(INBOX_BASE, "sorted")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.labels",
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

def _decode(data):
    if not data: return ""
    data = data.replace("-", "+").replace("_", "/")
    pad = len(data) % 4
    if pad: data += "=" * (4 - pad)
    return base64.b64decode(data).decode("utf-8", errors="ignore")

def _html_to_text(html):
    import re
    text = re.sub(r"<style.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</div\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;", " ", text); text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text); text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def extract_body_and_attachments(payload):
    import re
    body_text = ""; attachments = []
    def walk(parts):
        nonlocal body_text
        for p in parts:
            mime = p.get("mimeType",""); filename = p.get("filename","")
            body = p.get("body",{}); data = body.get("data"); att_id = body.get("attachmentId")
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
                        if sub.get("mimeType")=="text/plain" and sub.get("body",{}).get("data"):
                            body_text = _decode(sub["body"]["data"]); break
                    if not body_text:
                        for sub in p["parts"]:
                            if sub.get("mimeType")=="text/html" and sub.get("body",{}).get("data"):
                                body_text = _html_to_text(_decode(sub["body"]["data"])); break
    import re
    mime = payload.get("mimeType","")
    if "parts" in payload:
        walk(payload["parts"])
        if not body_text and payload.get("body",{}).get("data"):
            raw = _decode(payload["body"]["data"])
            body_text = raw if mime=="text/plain" else _html_to_text(raw)
    else:
        raw = _decode(payload.get("body",{}).get("data",""))
        if raw: body_text = raw if mime=="text/plain" else _html_to_text(raw)
    return body_text.strip(), attachments

def save_mail(service, nid):
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    m = service.users().messages().get(userId="me", id=nid, format="full").execute()
    hdrs = {h["name"]: h["value"] for h in m.get("payload",{}).get("headers",[])}
    body_text, atts = extract_body_and_attachments(m.get("payload",{}))
    try:
        dt = datetime.fromtimestamp(int(m.get("internalDate","0"))/1000)
        date_str = dt.strftime("%Y-%m-%d_%H-%M-%S")
    except:
        date_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    safe_subj = re.sub(r'[\\/:*?"<>|]', "_", hdrs.get("Subject","no_subject"))[:40].strip() or "no_subject"
    mail_dir = os.path.join(TO_SORT, f"{date_str}_{nid}_{safe_subj}")
    if os.path.exists(mail_dir):
        print(f"  Skip {nid} already present")
        return mail_dir
    os.makedirs(mail_dir, exist_ok=True)
    attach_dir = os.path.join(mail_dir, "attachments")
    saved_attachments = []
    import base64 as _b64
    for filename, mime, att_id, inline_data in atts:
        safe_name = re.sub(r'[\\/:*?"<>|]', "_", filename) or f"attach_{len(saved_attachments)}"
        dest = os.path.join(attach_dir, safe_name)
        os.makedirs(attach_dir, exist_ok=True)
        try:
            if att_id:
                att = service.users().messages().attachments().get(userId="me", messageId=nid, id=att_id).execute()
                raw = _b64.urlsafe_b64decode(att["data"] + "===")
                with open(dest, "wb") as f: f.write(raw)
            elif inline_data:
                with open(dest, "wb") as f: f.write(_b64.urlsafe_b64decode(inline_data + "==="))
            saved_attachments.append({"filename": filename, "saved_as": dest, "mime": mime})
        except Exception as e:
            print(f"  Attachment {filename} error: {e}")
            saved_attachments.append({"filename": filename, "error": str(e), "mime": mime})
    out = {
        "id": nid,
        "threadId": m.get("threadId"),
        "headers": {
            "From": hdrs.get("From",""),
            "To": hdrs.get("To",""),
            "Cc": hdrs.get("Cc",""),
            "Subject": hdrs.get("Subject",""),
            "Date": hdrs.get("Date",""),
        },
        "body_text": body_text,
        "attachments": [{"filename": a["filename"], "saved_as": a.get("saved_as",""), "mime": a["mime"]} for a in saved_attachments],
        "labels": m.get("labelIds", []),
        "internalDate": m.get("internalDate"),
        "snippet": m.get("snippet",""),
    }
    with open(os.path.join(mail_dir, "mail.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"  Saved {nid}: {hdrs.get('Subject','')[:50]} -> {mail_dir} ({len(body_text)} chars, {len(saved_attachments)} att)")
    return mail_dir

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    os.makedirs(TO_SORT, exist_ok=True)
    os.makedirs(SORTED, exist_ok=True)
    service = get_service()
    N = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    print(f"Backfilling the last {N} emails...")
    res = service.users().messages().list(userId="me", maxResults=N, q="").execute()
    msgs = res.get("messages", [])
    print(f"Found {len(msgs)} emails, saving to {TO_SORT}")
    for m in msgs:
        save_mail(service, m["id"])
    print("Backfill complete.")
    count = len([d for d in os.listdir(TO_SORT) if os.path.isdir(os.path.join(TO_SORT, d))])
    print(f"Total folders in to_sort: {count}")
