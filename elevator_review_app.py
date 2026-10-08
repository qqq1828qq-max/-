#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
조규수님의 승강기 검토 (Elevator Shaft Review)
────────────────────────────────────────────────────────
독립 실행 프로그램입니다. electrical_design_hub.py의 "탭"이 아니라
그 자체로 완결된 프로그램이며, UI 셸(좌측 사이드바 + 상단바 + 카드
스타일)은 기존 Design Hub와 동일한 스타일을 유지하되, 이 프로그램에
필요 없는 다른 설계 탭들(AI 자동화, SLD, 접지, 부하계산, 분전반,
저압반/고압반, 전선관, 케이블트레이, DXF 비교/병합, PDF변환, 성과물
출력, AI 어시스턴트 등)은 전부 제거했습니다.

남은 탭은 단 2개입니다:
    00. API 설정          — NVIDIA NIM / Google Gemini 키·모델 관리
    01. 승강로 CAD 도면 분석   — 평면도/입면도 실측 치수 vs 제조사별(미쓰비시/OTIS/TK/현대) 기준 자동 판정
                           (법적 규격/인승산정 등은 이후 02단계~ 탭에서 다룰 예정)

실행:
    python elevator_review_app.py

같은 폴더에 다음 파일들이 있어야 합니다:
    designhub_ui_theme.py, ai_client.py, api_settings_module.py,
    elevator_standards.py, elevator_shaft_table.json,
    elevator_shaft_review_module.py
"""

import tkinter as tk
from tkinter import messagebox
import os
import platform

from designhub_ui_theme import (
    SIDEBAR_BG, SIDEBAR_HOVER, SIDEBAR_ACTIVE,
    CANVAS_BG, SURFACE, SURFACE_HEADER,
    HAIRLINE, BORDER_SOFT,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_PRIMARY, ACCENT_INFO, ACCENT_WARNING, ACCENT_DANGER,
    BTN_METAL_BG, BTN_METAL_HOVER,
    BTN_METAL_TEXT_PRIMARY, BTN_METAL_TEXT_NORMAL,
    _F_KOR, _F_LATIN,
    FT_SUB, FH, FH_LG, FB, FB_M, FS_LATIN, FBTN, FNUM,
)

# ══════════════════════════════════════════════════════════════
#  접근 제어 — 기존 Mr.Q 프로그램군과 동일한 컴퓨터 이름 기반 방식.
#  필요 없으면 __init__()의 _check_access() 호출부만 지우면 됩니다.
# ══════════════════════════════════════════════════════════════
ALLOWED_BASE_NAMES = [
    "P161808N",
    "P162872N",
    "P149710N",
    "P152356N",
    "P162858N",
    "P157901N",
    "P162846N",
    "P162264N",
    "P162646N",
]


def _generate_allowed_names():
    allowed_names = set()
    for base_name in ALLOWED_BASE_NAMES:
        allowed_names.add(base_name.upper())
        for k in range(1, 100):
            allowed_names.add(f"{base_name}{k:02d}".upper())
    return allowed_names


def _check_access():
    current_computer_name = platform.node().upper()
    allowed_names = _generate_allowed_names()
    return current_computer_name in allowed_names


# ══════════════════════════════════════════════════════════════
#  메인 애플리케이션
# ══════════════════════════════════════════════════════════════
class ElevatorReviewApp(tk.Tk):
    def __init__(self):
        super().__init__()

        if not _check_access():
            messagebox.showerror(
                "접근 거부",
                f"🚫 접근이 허용되지 않은 컴퓨터입니다.\n\n현재 컴퓨터 이름: {platform.node().upper()}"
            )
            self.destroy()
            return

        # 윈도우 설정
        self.title("조규수님의 승강기 검토")
        self.geometry("1480x940")
        self.minsize(1200, 760)
        self.configure(bg=CANVAS_BG)
        try:
            self.option_add('*Font', FB)
        except Exception:
            pass

        # ★ API 설정 공유 딕셔너리 (승강기 검토 탭이 여기서 키를 읽음)
        import ai_client as _ai_client
        from ai_client import DEFAULT_MODEL as _DEFAULT_MODEL, DEFAULT_PROVIDER as _DEFAULT_PROVIDER
        self.shared_api = {'api_key': '', 'model': _DEFAULT_MODEL, 'provider': _DEFAULT_PROVIDER}
        try:
            from api_settings_module import load_settings as _ls
            _saved = _ls()
            _provider = _saved['provider']
            _entry = _saved['providers'].get(_provider, {'api_key': '', 'model': _DEFAULT_MODEL})
            self.shared_api['provider'] = _provider
            self.shared_api['api_key'] = _entry['api_key']
            self.shared_api['model'] = _entry['model']
            _ai_client.set_provider(_provider)
        except Exception:
            pass

        self._build_ui()

        messagebox.showinfo(
            "조규수님의 승강기 검토",
            "환영합니다.\n\n"
            "【시작 순서】\n"
            "① 사이드바 00 API 설정 탭에서 프로바이더 선택 후 API 키 입력·저장\n"
            "② 01 승강로 CAD 도면 분석 탭에서 평면도/입면도 DXF를 불러와 검토를 진행하세요\n\n"
            "API 키는 한 번 저장하면 다음 실행 시 자동 로드됩니다."
        )

    # ──────────────────────────────────────────────────────────
    #  UI 헬퍼 (Design Hub와 동일한 스타일 유지)
    # ──────────────────────────────────────────────────────────
    def _make_pill_button(self, parent, text, command,
                           bg=BTN_METAL_BG, fg=BTN_METAL_TEXT_NORMAL,
                           hover_bg=BTN_METAL_HOVER,
                           padx=14, pady=6, font=None):
        btn = tk.Button(
            parent, text=text, command=command,
            font=font or FBTN, bg=bg, fg=fg,
            activebackground=hover_bg, activeforeground=fg,
            relief='flat', bd=0, cursor='hand2',
            padx=padx, pady=pady, highlightthickness=0,
        )
        btn.bind('<Enter>', lambda _e: btn.config(bg=hover_bg))
        btn.bind('<Leave>', lambda _e: btn.config(bg=bg))
        return btn

    def _make_card(self, parent, padx=24, pady=20, bg=SURFACE):
        outer = tk.Frame(parent, bg=BORDER_SOFT)
        inner = tk.Frame(outer, bg=bg, padx=padx, pady=pady)
        inner.pack(fill='both', expand=True, padx=1, pady=1)
        return outer, inner

    def _make_status_chip(self, parent, text, color, bg=SURFACE_HEADER):
        frame = tk.Frame(parent, bg=bg)
        dot = tk.Label(frame, text='●', font=(_F_LATIN, 11), bg=bg, fg=color)
        dot.pack(side='left', padx=(0, 8))
        lbl = tk.Label(frame, text=text, font=FB, bg=bg, fg=TEXT_SECONDARY)
        lbl.pack(side='left')
        return frame, dot, lbl

    def _hairline(self, parent, side='top', color=HAIRLINE):
        line = tk.Frame(parent, bg=color, height=1)
        if side in ('top', 'bottom'):
            line.pack(side=side, fill='x')
        else:
            line.pack(side=side, fill='y')
        return line

    # ──────────────────────────────────────────────────────────
    #  메인 레이아웃 (사이드바 2개 항목만 — 전기상수/프로젝트관리 버튼 제거)
    # ──────────────────────────────────────────────────────────
    def _build_ui(self):
        sidebar = tk.Frame(self, bg=SIDEBAR_BG, width=240)
        sidebar.pack(side='left', fill='y')
        sidebar.pack_propagate(False)
        tk.Frame(self, bg=HAIRLINE, width=1).pack(side='left', fill='y')

        brand = tk.Frame(sidebar, bg=SIDEBAR_BG, height=104)
        brand.pack(side='top', fill='x')
        brand.pack_propagate(False)
        tk.Label(brand, text='PROJECTS SUNJA', font=(_F_LATIN, 10, 'bold'),
                 bg=SIDEBAR_BG, fg=ACCENT_PRIMARY).place(x=24, y=28)
        tk.Label(brand, text='승강기 검토', font=(_F_LATIN, 14, 'bold'),
                 bg=SIDEBAR_BG, fg=TEXT_PRIMARY).place(x=24, y=44)
        tk.Label(brand, text='Mr.Q   v1.0', font=FT_SUB,
                 bg=SIDEBAR_BG, fg=TEXT_MUTED).place(x=24, y=72)

        tk.Frame(sidebar, bg=HAIRLINE, height=1).pack(fill='x')

        self.nav_container = tk.Frame(sidebar, bg=SIDEBAR_BG)
        self.nav_container.pack(side='top', fill='both', expand=True, pady=(8, 8))

        tk.Frame(sidebar, bg=HAIRLINE, height=1).pack(side='bottom', fill='x')
        footer = tk.Frame(sidebar, bg=SIDEBAR_BG, height=56)
        footer.pack(side='bottom', fill='x')
        footer.pack_propagate(False)
        tk.Label(footer, text='All rights reserved by 조규수', font=FS_LATIN,
                 bg=SIDEBAR_BG, fg=TEXT_MUTED).place(x=24, y=20)

        # ── 우측 메인 영역 ────────────────────────────────────
        main = tk.Frame(self, bg=CANVAS_BG)
        main.pack(side='left', fill='both', expand=True)

        topbar = tk.Frame(main, bg=SURFACE_HEADER, height=64)
        topbar.pack(side='top', fill='x')
        topbar.pack_propagate(False)
        tk.Frame(main, bg=HAIRLINE, height=1).pack(side='top', fill='x')

        ctx_wrap = tk.Frame(topbar, bg=SURFACE_HEADER)
        ctx_wrap.place(x=28, y=0, relheight=1)
        self.topbar_section_var = tk.StringVar(value='SYSTEM')
        self.topbar_title_var = tk.StringVar(value='API 설정')
        tk.Label(ctx_wrap, textvariable=self.topbar_section_var,
                 font=(_F_LATIN, 8, 'bold'), bg=SURFACE_HEADER, fg=ACCENT_PRIMARY).pack(anchor='w', pady=(13, 0))
        tk.Label(ctx_wrap, textvariable=self.topbar_title_var,
                 font=(_F_KOR, 13, 'bold'), bg=SURFACE_HEADER, fg=TEXT_PRIMARY).pack(anchor='w')

        self.content = tk.Frame(main, bg=CANVAS_BG)
        self.content.pack(side='top', fill='both', expand=True)

        self._create_tabs()

    # ──────────────────────────────────────────────────────────
    #  탭 로더 (Design Hub와 동일 규약 — elevator_shaft_review_module.py를
    #  그대로 재사용할 수 있도록 유지)
    # ──────────────────────────────────────────────────────────
    def _load_tab_module(self, parent, module_name, class_name, attr_name,
                          ui_name, call_build_ui=True, post_init=None):
        try:
            module = __import__(module_name, fromlist=[class_name])
            klass = getattr(module, class_name)
            instance = klass(parent, self)
            setattr(self, attr_name, instance)
            if call_build_ui and hasattr(instance, 'build_ui'):
                instance.build_ui()
            if callable(post_init):
                post_init(instance)
            print(f"✅ {ui_name} 탭 빌드 완료")
        except Exception as e:
            import traceback; traceback.print_exc()
            messagebox.showerror("모듈 로드 오류", f"{ui_name} 모듈 로드 중 오류:\n{str(e)}")
            self._show_error_ui(parent, ui_name, str(e))

    def _create_tabs(self):
        """사이드바 네비게이션 — API 설정 + 승강로 CAD 도면 분석, 딱 2개만."""
        tab_defs = [
            ('api', '00', 'API 설정', 'API Settings', 'SYSTEM', self._build_api_tab),
            ('elevator', '01', '승강로 CAD 도면 분석', 'Elevator Shaft Size Review', 'DESIGN', self._build_elevator_tab),
        ]

        self._tab_frames = {}
        self._tab_builders = {}
        self._nav_items = {}
        self._tab_meta = {}
        self._tab_built = set()

        last_section = None
        for key, number, label, caption, section, builder in tab_defs:
            if section != last_section:
                if last_section is not None:
                    tk.Frame(self.nav_container, bg=SIDEBAR_BG, height=8).pack(fill='x')
                hdr = tk.Label(self.nav_container, text=section, font=(_F_LATIN, 8, 'bold'),
                               bg=SIDEBAR_BG, fg=TEXT_MUTED)
                hdr.pack(anchor='w', padx=24, pady=(10, 4))
                last_section = section

            frame = tk.Frame(self.content, bg=CANVAS_BG)
            self._tab_frames[key] = frame
            self._tab_builders[key] = builder
            self._tab_meta[key] = {'label': label, 'caption': caption, 'section': section, 'number': number}

            row = tk.Frame(self.nav_container, bg=SIDEBAR_BG, height=44, cursor='hand2')
            row.pack(fill='x')
            row.pack_propagate(False)
            accent_bar = tk.Frame(row, bg=SIDEBAR_BG, width=3)
            accent_bar.pack(side='left', fill='y')
            inner = tk.Frame(row, bg=SIDEBAR_BG)
            inner.pack(side='left', fill='both', expand=True, padx=(20, 16))
            num_lbl = tk.Label(inner, text=number, font=FNUM, bg=SIDEBAR_BG, fg=TEXT_MUTED, width=3, anchor='w')
            num_lbl.pack(side='left', pady=12)
            name_lbl = tk.Label(inner, text=label, font=FB, bg=SIDEBAR_BG, fg=TEXT_SECONDARY, anchor='w')
            name_lbl.pack(side='left', fill='x', expand=True, pady=12, padx=(8, 0))

            self._nav_items[key] = {
                'row': row, 'accent_bar': accent_bar, 'inner': inner,
                'num_lbl': num_lbl, 'name_lbl': name_lbl, 'active': False,
            }
            for w in (row, inner, num_lbl, name_lbl, accent_bar):
                w.bind('<Button-1>', lambda _e, k=key: self._show_tab(k))
                w.bind('<Enter>', lambda _e, k=key: self._on_nav_hover(k, True))
                w.bind('<Leave>', lambda _e, k=key: self._on_nav_hover(k, False))

        _first = 'elevator' if self.shared_api.get('api_key') else 'api'
        self._show_tab(_first)

    def _on_nav_hover(self, key, entering):
        item = self._nav_items.get(key)
        if not item or item.get('active'):
            return
        bg = SIDEBAR_HOVER if entering else SIDEBAR_BG
        item['row'].config(bg=bg)
        item['inner'].config(bg=bg)
        item['num_lbl'].config(bg=bg)
        item['name_lbl'].config(bg=bg)

    def _show_tab(self, key):
        for k, f in self._tab_frames.items():
            f.pack_forget()
        self._tab_frames[key].pack(fill='both', expand=True)

        for k, item in self._nav_items.items():
            is_active = (k == key)
            item['active'] = is_active
            bg = SIDEBAR_ACTIVE if is_active else SIDEBAR_BG
            item['row'].config(bg=bg)
            item['inner'].config(bg=bg)
            item['accent_bar'].config(bg=ACCENT_PRIMARY if is_active else bg)
            item['num_lbl'].config(bg=bg, fg=TEXT_PRIMARY if is_active else TEXT_MUTED)
            item['name_lbl'].config(bg=bg, fg=TEXT_PRIMARY if is_active else TEXT_SECONDARY,
                                     font=FB_M if is_active else FB)

        meta = self._tab_meta.get(key, {})
        self.topbar_section_var.set(meta.get('section', '').upper() + '   ·   ' + meta.get('caption', ''))
        self.topbar_title_var.set(meta.get('label', ''))

        if key not in self._tab_built:
            builder = self._tab_builders.get(key)
            if builder:
                try:
                    builder(self._tab_frames[key])
                    self._tab_built.add(key)
                except Exception as e:
                    messagebox.showerror("탭 로드 오류", f"탭을 로드하는 중 오류가 발생했습니다:\n{str(e)}")

    # ──────────────────────────────────────────────────────────
    #  탭 빌더 — API 설정 / 승강로 CAD 도면 분석
    # ──────────────────────────────────────────────────────────
    def _build_api_tab(self, parent):
        """API 설정 탭 — NVIDIA/Gemini 키 중앙 관리"""
        try:
            from api_settings_module import APISettingsModule
            self.api_settings_module = APISettingsModule(parent, self)
            self.api_settings_module.build_ui()
        except Exception as e:
            import traceback; traceback.print_exc()
            self._show_error_ui(parent, 'API 설정', str(e))

    def _build_elevator_tab(self, parent):
        """01 승강로 CAD 도면 분석 탭 — 평면도/입면도 실측 치수 vs 제조사별 기준 자동 판정"""
        self._load_tab_module(
            parent, 'elevator_shaft_review_module', 'ElevatorShaftReviewModule',
            'elevator_module', '승강로 CAD 도면 분석'
        )

    # ──────────────────────────────────────────────────────────
    #  오류 표시 카드 (탭 로드 실패 시)
    # ──────────────────────────────────────────────────────────
    def _show_state_card(self, parent, *, kind, title, subtitle, body,
                          action_text=None, action_command=None):
        color_map = {'warning': ACCENT_WARNING, 'error': ACCENT_DANGER, 'info': ACCENT_INFO}
        accent = color_map.get(kind, ACCENT_INFO)

        wrap = tk.Frame(parent, bg=CANVAS_BG)
        wrap.pack(fill='both', expand=True, padx=40, pady=40)
        outer, card = self._make_card(wrap, padx=44, pady=44)
        outer.pack(anchor='center', expand=True)

        kind_text = {'warning': 'NOTICE', 'error': 'ERROR', 'info': 'INFO'}.get(kind, 'INFO')
        tk.Label(card, text=kind_text, font=(_F_LATIN, 8, 'bold'), bg=SURFACE, fg=accent).pack(anchor='w')
        tk.Label(card, text=title, font=FH_LG, bg=SURFACE, fg=TEXT_PRIMARY).pack(anchor='w', pady=(8, 4))
        if subtitle:
            tk.Label(card, text=subtitle, font=FB, bg=SURFACE, fg=TEXT_SECONDARY).pack(anchor='w')
        tk.Frame(card, bg=HAIRLINE, height=1).pack(fill='x', pady=(20, 16))
        tk.Label(card, text=body, font=FB, bg=SURFACE, fg=TEXT_SECONDARY,
                 justify='left', wraplength=520).pack(anchor='w')
        if action_text and action_command:
            btn_wrap = tk.Frame(card, bg=SURFACE)
            btn_wrap.pack(anchor='w', pady=(24, 0))
            self._make_pill_button(btn_wrap, text=action_text, command=action_command,
                                    bg=BTN_METAL_BG, fg=BTN_METAL_TEXT_PRIMARY,
                                    hover_bg=BTN_METAL_HOVER).pack()

    def _show_error_ui(self, parent, module_name, error_msg):
        self._show_state_card(
            parent, kind='error',
            title=f'{module_name} · 모듈 로드 실패',
            subtitle='모듈 초기화 중 예외가 발생했습니다.',
            body=str(error_msg),
        )


if __name__ == '__main__':
    app = ElevatorReviewApp()
    app.mainloop()