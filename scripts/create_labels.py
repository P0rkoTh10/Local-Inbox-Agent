from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# Adjust this path to your environment.
TOKEN = r'C:\path\to\watcher\token_gmail.json'
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly','https://www.googleapis.com/auth/gmail.modify','https://www.googleapis.com/auth/gmail.labels','https://www.googleapis.com/auth/calendar']

creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
svc = build('gmail','v1',credentials=creds)

need = ['spam','social','bills','official communications','purchases','subscriptions','payments','friends and family','work','events/appointments','important','other']
existing = [l['name'].lower() for l in svc.users().labels().list(userId='me').execute().get('labels',[])]
for n in need:
    if n.lower() in existing:
        print(f'skip {n} already exists')
    else:
        r = svc.users().labels().create(userId='me', body={'name': n, 'labelListVisibility':'labelShow', 'messageListVisibility':'show'}).execute()
        print(f'created {n} -> {r["id"]}')
print('--- final ---')
for l in svc.users().labels().list(userId='me').execute().get('labels',[]):
    if l['type']=='user':
        print(l['name'], l['id'])
