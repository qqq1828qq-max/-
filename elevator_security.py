# -*- coding: utf-8 -*-
import os, sys, json, hmac, hashlib, platform, secrets
import tkinter as tk
from tkinter import messagebox, simpledialog

_DEFAULT_USERS = ["161808", "162872", "149710", "152356", "162858",
                  "157901", "162846", "162264", "162646"]
_KEY = b"\x5a\xc3\x11\x9e\x70\x2d\xb4\x68\xf1\x07\xa9\x3c\xd2\x84\x4e\x95"
_FILE = ".admin_settings.json"


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


def _sign(users, pw):
    msg = json.dumps([sorted(users), pw], ensure_ascii=False).encode("utf-8")
    return hmac.new(_KEY, msg, hashlib.sha256).hexdigest()


def _hash_pw(salt, pw):
    return hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), 120000).hex()


def _load():
    """(users, pw_record) — 파일이 없거나 변조되면 기본값."""
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        users = [str(u).strip() for u in d.get("users", []) if str(u).strip()]
        pw = d.get("pw") or None
        if users and hmac.compare_digest(str(d.get("sig", "")), _sign(users, pw)):
            return users, pw
    except Exception:
        pass
    return list(_DEFAULT_USERS), None


def _save(users, pw):
    d = {"users": users, "pw": pw, "sig": _sign(users, pw)}
    with open(_path(), "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def _allowed(users):
    s = set()
    for u in users:
        b = f"P{u}N".upper()
        s.add(b)
        s.update(f"{b}{k:02d}" for k in range(1, 100))
    return s


def check_access():
    name = platform.node().upper()
    users, _ = _load()
    return name in _allowed(users), name


def _verify_admin(parent):
    users, pw = _load()
    if not pw:
        a = simpledialog.askstring("관리자", "관리자 비밀번호를 새로 설정하세요.", show="*", parent=parent)
        if not a:
            return False
        b = simpledialog.askstring("관리자", "비밀번호 확인", show="*", parent=parent)
        if a != b:
            messagebox.showerror("관리자", "비밀번호가 일치하지 않습니다.", parent=parent)
            return False
        salt = secrets.token_hex(16)
        _save(users, {"s": salt, "h": _hash_pw(salt, a)})
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
    users, pw = _load()
    dlg = tk.Toplevel(parent)
    dlg.title("사용자 관리")
    dlg.geometry("340x420")
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
        _save(items, pw)
        messagebox.showinfo("사용자 관리", "저장했습니다.", parent=dlg)
        dlg.destroy()

    def chpw():
        a = simpledialog.askstring("관리자", "새 비밀번호", show="*", parent=dlg)
        if a:
            salt = secrets.token_hex(16)
            _save(list(lb.get(0, "end")) or users, {"s": salt, "h": _hash_pw(salt, a)})
            messagebox.showinfo("관리자", "변경했습니다.", parent=dlg)

    tk.Button(row, text="추가", command=add).pack(side="left")
    tk.Button(row, text="삭제", command=rem).pack(side="left", padx=4)
    bot = tk.Frame(dlg)
    bot.pack(pady=8)
    tk.Button(bot, text="저장", width=8, command=save).pack(side="left", padx=4)
    tk.Button(bot, text="비밀번호 변경", command=chpw).pack(side="left", padx=4)
    tk.Button(bot, text="닫기", width=8, command=dlg.destroy).pack(side="left", padx=4)
