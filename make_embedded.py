# -*- coding: utf-8 -*-
"""빌드 전에 admin_config.txt(배포하지 않음)를 읽어 elevator_embedded.py를 만든다 — exe 안에 설정이 들어가 모든 PC에 적용된다."""
import sys, os, hashlib, secrets, datetime

CFG = "admin_config.txt"
OUT = "elevator_embedded.py"

if not os.path.exists(CFG):
    print("admin_config.txt not found - building with code defaults")
    if os.path.exists(OUT):
        os.remove(OUT)
    sys.exit(0)

cfg = {}
with open(CFG, "r", encoding="utf-8-sig") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        cfg[k.strip().lower()] = v.strip()

users = []
for u in cfg.get("users", "").replace(";", ",").replace("\n", ",").split(","):
    u = u.strip().upper()
    if u.startswith("P"):
        u = u[1:]
    if u.endswith("N"):
        u = u[:-1]
    if u:
        users.append(u)
if not users:
    print("ERROR: users is empty in admin_config.txt")
    sys.exit(1)

try:
    days = int(cfg.get("days", "0") or 0)
except ValueError:
    print("ERROR: days must be a number")
    sys.exit(1)
start = cfg.get("start", "")
if days > 0:
    try:
        datetime.date.fromisoformat(start)
    except Exception:
        print("ERROR: start must be YYYY-MM-DD when days > 0")
        sys.exit(1)
else:
    days, start = 0, ""

pw = cfg.get("password", "")
if not pw:
    print("ERROR: password is empty in admin_config.txt")
    sys.exit(1)
salt = secrets.token_hex(16)
h = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), 120000).hex()
build = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("# generated - do not edit\n")
    f.write("USERS = %r\n" % (users,))
    f.write("START = %r\n" % (start,))
    f.write("DAYS = %r\n" % (days,))
    f.write("PW = %r\n" % ({"s": salt, "h": h},))
    f.write("BUILD = %r\n" % (build,))
print("embedded: users=%d start=%s days=%d (password hashed)" % (len(users), start or "-", days))
