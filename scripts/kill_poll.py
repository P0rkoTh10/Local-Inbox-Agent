import psutil

for p in psutil.process_iter(['pid','name','cmdline']):
    try:
        name = (p.info['name'] or '').lower()
        cmd = ' '.join(p.info['cmdline'] or [])
        if 'python' in name and 'poll_gmail' in cmd:
            print(f"killing {p.pid} {cmd[:80]}")
            p.terminate()
    except Exception as e:
        print(e)
