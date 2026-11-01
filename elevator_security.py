# -*- coding: utf-8 -*-
import os, sys, json, hmac, hashlib, platform, secrets, datetime
import tkinter as tk
from tkinter import messagebox, simpledialog

_DEFAULT_USERS = ["161808", "162872", "149710", "152356", "162858",
                  "157901", "162846", "162264", "162646"]
_KEY = b"\x5a\xc3\x11\x9e\x70\x2d\xb4\x68\xf1\x07\xa9\x3c\xd2\x84\x4e\x95"
_FILE = ".admin_settings.json"
# 설정 파일이 없거나 변조됐을 때 쓰는 내장 기간(빌드 전에 지정: 예 "2026-11-01", 90). 비워두면 무제한.
_DEFAULT_START = ""
_DEFAULT_DAYS = 0


def _app_dir():
    try:
        d = __compiled__.containing_dir  # Nuitka
        return d
    except NameError:
        pass
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(sys.argv[0] or __file__))


def _path():
    return os.path.join(_app_dir(), _FILE)


def _sign(users, pw, ex):
    msg = json.dumps([sorted(users), pw, ex], ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hmac.new(_KEY, msg, hashlib.sha256).hexdigest()


def _hash_pw(salt, pw):
    return hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), 120000).hex()


def _today():
    return datetime.date.today()


def _load():
    """(users, pw_record, ex) — ex={'days','start','last'}. 파일이 없거나 변조되면 기본값."""
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        users = [str(u).strip() for u in d.get("users", []) if str(u).strip()]
        pw = d.get("pw") or None
        ex = d.get("ex") or {"days": _DEFAULT_DAYS, "start": _DEFAULT_START, "last": ""}
        if users and hmac.compare_digest(str(d.get("sig", "")), _sign(users, pw, ex)):
            return users, pw, ex
    except Exception:
        pass
    return list(_DEFAULT_USERS), None, {"days": _DEFAULT_DAYS, "start": _DEFAULT_START, "last": ""}


def _save(users, pw, ex):
    d = {"users": users, "pw": pw, "ex": ex, "sig": _sign(users, pw, ex)}
    with open(_path(), "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def _d(s):
    try:
        return datetime.date.fromisoformat(s)
    except Exception:
        return None


def expiry_status():
    """(만료 여부, 남은 일수 또는 None=무제한). 시작일은 관리자가 입력한 날짜 고정이며,
    PC 시계를 되돌려도 마지막으로 확인된 날짜 이전으로는 못 간다."""
    users, pw, ex = _load()
    days = int(ex.get("days") or 0)
    if days <= 0:
        return False, None
    start = _d(ex.get("start", ""))
    if start is None:
        return True, 0          # 기간만 있고 시작일이 없으면 잠금
    now = _today()
    last = _d(ex.get("last", ""))
    if last and now < last:
        now = last
    if last is None or now > last:
        ex["last"] = now.isoformat()
        try:
            _save(users, pw, ex)
        except Exception:
            pass
    left = days - max(0, (now - start).days)
    return left <= 0, max(left, 0)


def end_date_text(ex):
    st = _d(ex.get("start", ""))
    days = int(ex.get("days") or 0)
    return (st + datetime.timedelta(days=days)).isoformat() if st and days > 0 else ""


def _allowed(users):
    s = set()
    for u in users:
        b = f"P{u}N".upper()
        s.add(b)
        s.update(f"{b}{k:02d}" for k in range(1, 100))
    return s


def check_access():
    name = platform.node().upper()
    users = _load()[0]
    return name in _allowed(users), name


def _verify_admin(parent):
    users, pw, ex = _load()
    if not pw:
        a = simpledialog.askstring("관리자", "관리자 비밀번호를 새로 설정하세요.", show="*", parent=parent)
        if not a:
            return False
        b = simpledialog.askstring("관리자", "비밀번호 확인", show="*", parent=parent)
        if a != b:
            messagebox.showerror("관리자", "비밀번호가 일치하지 않습니다.", parent=parent)
            return False
        salt = secrets.token_hex(16)
        _save(users, {"s": salt, "h": _hash_pw(salt, a)}, ex)
        return True
    a = simpledialog.askstring("관리자", "관리자 비밀번호", show="*", parent=parent)
    if a is None:
        return False
    ok = hmac.compare_digest(_hash_pw(pw["s"], a), pw["h"])
    if not ok:
        messagebox.showerror("관리자", "비밀번호가 틀렸습니다.", parent=parent)
    return ok


def open_user_manager(parent):
    if not _verify_admin(parent):
        return
    users, pw, ex = _load()
    dlg = tk.Toplevel(parent)
    dlg.title("사용자 관리")
    dlg.geometry("360x540")
    dlg.transient(parent)
    dlg.grab_set()
    tk.Label(dlg, text=f"현재 PC: {platform.node().upper()}").pack(pady=(10, 2))
    tk.Label(dlg, text="허용 사번 (PC명 P{사번}N + 01~99)").pack()
    lb = tk.Listbox(dlg, height=12)
    lb.pack(fill="both", expand=True, padx=12, pady=8)
    for u in users:
        lb.insert("end", u)
    row = tk.Frame(dlg)
    row.pack(pady=4)
    ent = tk.Entry(row, width=14)
    ent.pack(side="left", padx=4)

    def add():
        v = ent.get().strip().upper().lstrip("P").rstrip("N")
        if v and v not in lb.get(0, "end"):
            lb.insert("end", v)
        ent.delete(0, "end")

    def rem():
        for i in reversed(lb.curselection()):
            lb.delete(i)

    def save():
        items = list(lb.get(0, "end"))
        if not items:
            messagebox.showwarning("사용자 관리", "최소 1명은 등록해야 합니다.", parent=dlg)
            return
        try:
            nd = max(0, int(dent.get().strip() or 0))
        except ValueError:
            messagebox.showwarning("사용자 관리", "기간은 숫자로 입력하세요.", parent=dlg)
            return
        sd = sent.get().strip()
        if nd > 0 and _d(sd) is None:
            messagebox.showwarning("사용자 관리", "시작 날짜를 YYYY-MM-DD 형식으로 입력하세요.", parent=dlg)
            return
        nex = {"days": nd, "start": sd if nd else "", "last": ""}
        if nd and nex["start"] == ex.get("start") and nd == int(ex.get("days") or 0):
            nex["last"] = ex.get("last", "")
        _save(items, pw, nex)
        messagebox.showinfo("사용자 관리", "저장했습니다.", parent=dlg)
        dlg.destroy()

    def chpw():
        a = simpledialog.askstring("관리자", "새 비밀번호", show="*", parent=dlg)
        if a:
            salt = secrets.token_hex(16)
            _save(list(lb.get(0, "end")) or users, {"s": salt, "h": _hash_pw(salt, a)}, ex)
            messagebox.showinfo("관리자", "변경했습니다.", parent=dlg)

    tk.Button(row, text="추가", command=add).pack(side="left")
    tk.Button(row, text="삭제", command=rem).pack(side="left", padx=4)
    srow = tk.Frame(dlg)
    srow.pack(pady=2)
    tk.Label(srow, text="시작 날짜(YYYY-MM-DD)").pack(side="left")
    sent = tk.Entry(srow, width=12)
    sent.pack(side="left", padx=4)
    sent.insert(0, ex.get("start") or _today().isoformat())
    drow = tk.Frame(dlg)
    drow.pack(pady=2)
    tk.Label(drow, text="사용 기간(일, 0=무제한)").pack(side="left")
    dent = tk.Entry(drow, width=6)
    dent.pack(side="left", padx=4)
    dent.insert(0, str(int(ex.get("days") or 0)))
    _exp, _left = expiry_status()
    tk.Label(dlg, text=("무제한" if _left is None else f"종료일 {end_date_text(ex)} · 남은 기간 {_left}일")).pack()
    bot = tk.Frame(dlg)
    bot.pack(pady=8)
    tk.Button(bot, text="저장", width=8, command=save).pack(side="left", padx=4)
    tk.Button(bot, text="비밀번호 변경", command=chpw).pack(side="left", padx=4)
    tk.Button(bot, text="닫기", width=8, command=dlg.destroy).pack(side="left", padx=4)


def bind_admin_hotkey(win):
    """Ctrl+Shift+Q+P 동시 입력 시 관리 창."""
    down = set()

    def kd(e):
        down.add(e.keysym.lower())
        if {"q", "p"} <= down and (e.state & 0x4) and (e.state & 0x1):
            down.clear()
            open_user_manager(win)

    win.bind_all("<KeyPress>", kd, add="+")
    win.bind_all("<KeyRelease>", lambda e: down.discard(e.keysym.lower()), add="+")
