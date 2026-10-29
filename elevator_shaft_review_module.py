# -*- coding: utf-8 -*-
"""승강로 CAD 도면 분석 탭."""

import os
import re
import math
import json
import time
import logging
import threading
import traceback
from collections import defaultdict, Counter
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

# ezdxf는 실제 프로젝트 DXF(Civil3D/Revit 등 다른 프로그램에서 만든 ACAD_PROXY_OBJECT를
# 포함하는 경우가 흔함)를 렌더링/스캔할 때 "copy process ignored ACAD_PROXY_OBJECT(...)"류
# 경고를 대량으로 남긴다. 실제 렌더링/치수 추출 결과에는 영향이 없는 진단성 로그이므로,
# 터미널이 도배되지 않도록 WARNING 이하는 조용히 한다(에러(ERROR)는 계속 보이게 둔다).
logging.getLogger('ezdxf').setLevel(logging.ERROR)

from designhub_ui_theme import (
    CANVAS_BG, SURFACE, SURFACE_HI, SURFACE_HEADER,
    HAIRLINE, BORDER_SOFT,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_PRIMARY, ACCENT_SUCCESS, ACCENT_WARNING, ACCENT_DANGER, ACCENT_INFO,
    BTN_METAL_BG, BTN_METAL_HOVER, BTN_METAL_BORDER,
    BTN_METAL_TEXT_PRIMARY, BTN_METAL_TEXT_NORMAL, BTN_METAL_TEXT_MUTED, BTN_METAL_TEXT_DANGER,
    _F_KOR, _F_LATIN, _F_MONO, FH, FH_LG, FB, FB_M, FS, FBTN,
    STYLE_TREEVIEW, STYLE_TREEVIEW_HEADING,
    configure_designhub_ttk_styles,
)


# 인승(정원) 콤보박스에 쓰이는 흔한 값 목록 — 예전에는 이 값이 제조사/당사 규격표
# (vendor_standards.py/elevator_standards.py)와 맞물려 있었지만, 이 탭은 이제
# 규격 비교를 다루지 않으므로(다른 탭 담당) 그 모듈들에 의존하지 않는 단순 참고용
# 목록으로 둔다. AI가 도면 문구("16인승" 등)에서 읽은 값이 이 목록에 없어도
# 그대로 반영할 수 있도록, 콤보박스는 읽기전용이 아니라 직접 입력도 허용한다.
CAPACITY_OPTIONS = [9, 11, 13, 15, 16, 17, 18, 21, 24, 26]

# [기능 추가] 거대 배치도에서 승강로 위치를 레이어명으로 자동 탐색할 때(
# _find_shaft_points_by_layer) 쓰는 키워드 목록. 설계사무소마다 레이어 명명
# 규칙이 다를 수 있어 여러 표기를 한꺼번에 반영해둔다. 다만 "EV"처럼 너무 짧은
# 토큰은 "REVISION"/"DEVELOP" 같은 무관한 레이어명에도 우연히 걸리므로
# (예: "REVISION"에도 "EV"가 들어있음) 일부러 넣지 않았다 — 그런 짧은 표기를
# 쓰는 도면을 만나면 이 목록에 그 정확한 표기를 추가해서 대응한다.
SHAFT_LAYER_KEYWORDS = ('ELEV', '승강', 'LIFT', '엘리베이터', 'ELEVATOR')

# write_shaft_layer_dxf()가 '승강로 사이즈' 레이어에 그리는 폴리선의 색/선가중치 —
# 육안으로 바로 확인되도록 주황(ACI 30), 2.00mm로 고정한다.
_SHAFT_LAYER_COLOR = 30
_SHAFT_LAYER_LINEWEIGHT = 200


# ──────────────────────────────────────────────────────────────
#  DXF 유틸 (지연 import — ezdxf는 실제 필요할 때만 로드)
# ──────────────────────────────────────────────────────────────
def _active_xclip_polygon_world(insert_entity):
    """INSERT에 "활성화된"(XCLIP으로 켜져 있는) 클리핑 경계가 있으면 월드 좌표계
    다각형(점 리스트)을 반환하고, 없으면 None을 반환한다.

    실무 도면에서는 원본 도면(예: 여러 층이 쌓인 마스터 파일이나 공유 코어 블록)의
    일부만 보이도록 XCLIP으로 화면을 잘라서 쓰는 경우가 흔하다 — XCLIP은 "보기에서만
    숨기는" 비파괴 기능이라, 블록 자체에는 잘려서 안 보이는 부분까지 실제 형상이 그대로
    남아있다. virtual_entities()로 블록 내부를 펼칠 때 이 경계를 함께 확인하지 않으면,
    화면에는 안 보이는(=사용자 의도와 무관한) 형상/치수/문자까지 후보로 섞여 들어간다.
    ezdxf.xclip.XClip은 가상(virtual) 엔티티로 복사된 INSERT에서도 클리핑 정보를
    정확히 읽어내므로(직접 실측 확인됨), 중첩 블록 재귀 중에도 그대로 활용할 수 있다."""
    try:
        from ezdxf import xclip
        clip = xclip.XClip(insert_entity)
        if not (clip.has_clipping_path and clip.is_clipping_enabled):
            return None
        path = clip.get_wcs_clipping_path()
        verts = path.inner_polygon() if path.is_inverted_clip else path.vertices
        pts = [(v.x, v.y) for v in verts]
        return pts if len(pts) >= 3 else None
    except Exception:
        return None


def _point_in_clip_stack(pt, clip_stack):
    """pt(x, y)가 clip_stack에 쌓인 모든 XCLIP 경계 안(경계 포함)에 있는지 검사한다.
    중첩된 블록이 각각 자기 클립을 가질 수 있으므로, 안쪽 블록으로 들어갈수록
    조건이 누적(AND)된다. clip_stack이 비어 있으면(클리핑 없음) 항상 True."""
    if not clip_stack:
        return True
    try:
        from ezdxf.math import Vec2, is_point_in_polygon_2d
    except Exception:
        return True  # 라이브러리 문제로 확인이 안 되면 보수적으로 통과시킨다
    p = Vec2(pt[0], pt[1])
    for poly in clip_stack:
        try:
            if is_point_in_polygon_2d(p, [Vec2(x, y) for x, y in poly]) < 0:
                return False
        except Exception:
            continue
    return True


def _hidden_layer_names(doc):
    """캐드 화면에 보이지 않는 레이어(동결 Freeze / 꺼짐 Off) 이름 집합. 이런 레이어에는 설계 정의용
    기준선(예: '0-Def-기준Wall')처럼 도면 사용자가 볼 수 없는 형상이 들어 있는 경우가 많다 — 실측: 동결된
    기준선 레이어에 스냅해 구조체(G-CON)보다 승강로 치수가 60mm 작게 나왔다. 사람이 도면에서 보는 것과
    같은 기준으로 치수를 따도록, 모든 수집 단계에서 이 레이어의 개체(블록 안의 개체 포함)를 제외한다."""
    hidden = set()
    try:
        for lay in doc.layers:
            try:
                nm = lay.dxf.name
                if lay.is_frozen() or not lay.is_on() or nm.split('$')[-1].strip().lower().startswith('defpoint'):
                    hidden.add(nm)
            except Exception:
                continue
    except Exception:
        pass
    return hidden


def _insert_hidden(e, hidden):
    try:
        return bool(hidden) and (e.dxf.layer in hidden)
    except Exception:
        return False


def _wall_segment_candidates(msp, layer_hint='골조', max_block_depth=6):
    """평면도의 벽/기둥(골조) 레이어에 그려진 개별 선분들을 모두 모은다. 이 도면들은
    승강로 개구부를 감싸는 닫힌 도형이 없는 대신, 벽이 개별 LINE(또는 열린
    LWPOLYLINE/POLYLINE의 변)으로 그려져 있는 경우가 흔하다 — _snap_shaft_to_wall_lines가
    AI의 대략적 위치 주변에서 이 선분들 중 승강로를 감싸는 안쪽 벽면을 기하학적으로
    찾아내는 데 쓴다.

    layer_hint가 포함된 레이어의 선분만 모으며(치수선/문자/해칭 등 다른 레이어는
    제외), 블록(INSERT) 내부에 있는 벽선도 virtual_entities()로 실제 배치
    위치/축척으로 변환해 함께 찾는다. XCLIP으로 화면에서 잘려 안 보이는 부분은
    제외한다(_closed_entity_candidates와 동일한 처리)."""
    segs = []

    # '골조' 레이어명에 부분일치로만 걸러내다 보니, 같은 '...골조$0$...' 접두를
    # 공유하는 중심선/그리드 서브레이어(예: 'S-22BL 지하3층 주차장-골조$0$CEN-P')까지
    # 벽 후보로 같이 잡히는 사고가 실측으로 확인됐다(건물 전체를 가로지르는
    # 40m+ 길이의 통심선이, 승강로 바로 옆 진짜 벽보다 오히려 판정 중심점에
    # 더 가까워 잘못된 경계로 뽑힐 수 있음). 벽 레이어가 아닌 것이 명백한
    # 중심선/그리드 계열 서브레이어는 '골조' 부분일치와 무관하게 항상 제외한다.
    _NON_WALL_LAYER_TOKENS = ('CEN', '중심선', 'GRID', '그리드')
    hidden = _hidden_layer_names(msp.doc)

    def _layer_matches(e):
        try:
            layer = e.dxf.layer or ''
        except Exception:
            return False
        if layer in hidden or layer_hint not in layer:
            return False
        layer_upper = layer.upper()
        if any(tok.upper() in layer_upper for tok in _NON_WALL_LAYER_TOKENS):
            return False
        return True

    def _collect(e, clip_stack):
        try:
            if not _layer_matches(e):
                return
            etype = e.dxftype()
            pt_pairs = []
            if etype == 'LINE':
                p0, p1 = e.dxf.start, e.dxf.end
                pt_pairs = [[(p0.x, p0.y), (p1.x, p1.y)]]
            elif etype in ('LWPOLYLINE', 'POLYLINE'):
                if etype == 'LWPOLYLINE':
                    verts = [(p[0], p[1]) for p in e.get_points('xy')]
                    closed = e.closed
                else:
                    verts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                    closed = e.is_closed
                n = len(verts)
                for i in range(n - 1):
                    pt_pairs.append([verts[i], verts[i + 1]])
                if closed and n >= 2:
                    pt_pairs.append([verts[-1], verts[0]])
            else:
                return
            for a, b in pt_pairs:
                cx, cy = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
                if _point_in_clip_stack((cx, cy), clip_stack):
                    segs.append((a[0], a[1], b[0], b[1]))
        except Exception:
            pass

    def _walk(entities, depth, clip_stack):
        for e in entities:
            try:
                etype = e.dxftype()
            except Exception:
                continue
            if etype == 'INSERT':
                if depth >= max_block_depth or _insert_hidden(e, hidden):
                    continue
                clip_poly = _active_xclip_polygon_world(e)
                new_stack = clip_stack + [clip_poly] if clip_poly else clip_stack
                try:
                    _walk(e.virtual_entities(), depth + 1, new_stack)
                except Exception:
                    continue
            else:
                _collect(e, clip_stack)

    _walk(msp, 0, [])
    return segs


# 벽 레이어별 벽선 후보 캐시 — 28MB급 도면은 레이어 하나를 모으는 데 약 20초가 걸리는데, ②(구성
# 판정)와 ③(스냅)이 같은 레이어를 각자 다시 모으면 시간이 두 배로 든다. (파일경로, 수정시각, 레이어)
# 기준으로 한 번만 모은다.
_WALL_SEG_CACHE = {}


def _cached_wall_segs(msp, dxf_path, layer):
    """layer가 'A|B'처럼 '|'로 이어진 복합 키면 각 레이어의 벽선을 합친다(여러 레이어를 한 벽선
    집합으로 보고 '가장 가까운 선'을 고르기 위함 — 실제 구조체 면이 기준선보다 승강로에 가깝다)."""
    if '|' in layer:
        out = []
        for part in layer.split('|'):
            out += _cached_wall_segs(msp, dxf_path, part)
        return out
    try:
        key = (dxf_path, os.path.getmtime(dxf_path), layer)
    except Exception:
        key = (dxf_path, 0, layer)
    if key not in _WALL_SEG_CACHE:
        _WALL_SEG_CACHE[key] = _wall_segment_candidates(msp, layer_hint=layer) if msp is not None else []
    return _WALL_SEG_CACHE[key]


def _pretty_layer(key):
    """긴 XREF 접두('XR-...$0$')를 떼고 'G-CON + 0-Def-기준Wall' 꼴로 줄여 표시한다."""
    return ' + '.join(part.split('$')[-1] for part in (key or '').split('|'))


_STRUCT_NAME_TOKENS = ('CON', 'WAL', 'WALL', '골조', '벽체', '구조', '기준')
_STRUCT_EXCLUDE_TOKENS = ('CEN', 'GRID', '중심선', '그리드', 'CONTINUOUS', 'DIM', '치수', 'TEXT', 'FIN', '마감',
                          'ELEV', '승강', 'LIFT')


def _layer_priority(name):
    """벽 레이어 우선순위(작을수록 먼저 시도): 0=콘크리트/골조(CON·CONC·골조), 1=벽체(WAL·WALL·벽체),
    2=그 밖. 한 도면에서도 승강로 위치에 따라 골조 레이어가 다를 수 있어(실측: 주민공동시설 쪽은 A_CON,
    세대 쪽은 0-상부 벽체) 전역 1위 하나 대신, 승강로마다 이 순서로 시도해 주변에 실제로 감싸는 선이
    있는 첫 레이어를 쓴다."""
    last = (name or '').split('$')[-1]
    up = last.upper()
    toks = [t for t in re.split(r'[^A-Z0-9가-힣]+', up) if t]
    if '골조' in last or any((t.endswith('CON') and t != 'CONTINUOUS') or t.startswith('CONC') for t in toks):
        return 0
    if '벽체' in last or any('WAL' in t for t in toks):
        return 1
    return 2


def _structural_layer_names(stats):
    """레이어명에 벽/구조체 단서가 있는 후보(AI가 놓쳤을 수 있는 G-CON 같은 이름까지 후보 풀에 넣는다).
    최종 선택은 이름이 아니라 아래 기하 점수(_rank_wall_layers)가 한다."""
    out = []
    for st in stats:
        up = st['layer'].upper()
        if st['count'] < 4:
            continue
        if any(t.upper() in up for t in _STRUCT_NAME_TOKENS) and not any(t.upper() in up for t in _STRUCT_EXCLUDE_TOKENS):
            out.append(st['layer'])
    return out


def _rank_wall_layers(msp, clusters, candidates, max_block_depth: int = 6,
                       near_out_mm: float = 600.0, min_cover: float = 0.35, debug=None, keep_hug=()):
    """후보 레이어마다 '승강로 심볼을 실제로 둘러싸는 정도'를 기하로 점수화한다 — 점수 = 심볼 4변 중
    3변 이상에, 심볼 가장자리 바깥(또는 25% 안쪽)~near_out_mm 이내로 그 변 길이의 min_cover 이상을 덮는
    선이 있는 승강로 수. 이름이나 AI 나열 순서가 아니라 도면 기하로 구조체 레이어를 고른다.
    반환: [(layer, score, 덮는 승강로 index 집합)] 점수 내림차순."""
    hidden = _hidden_layer_names(msp.doc)
    cand = set(candidates) - hidden
    if not cand or not clusters:
        return []
    info = []
    for c in clusters:
        rr = c.get('rotated_rect') or {}
        ang = rr.get('angle', 0.0) or 0.0
        hw, hh = rr.get('width_mm', 2000.0) / 2, rr.get('height_mm', 2000.0) / 2
        info.append((c['center'], ang, hw, hh, max(hw, hh) + near_out_mm + 800.0))
    hits = defaultdict(list)     # (layer, ci) -> [(orient, pos, lo, hi)]

    def _add(layer, pts):
        for k in range(len(pts) - 1):
            ax, ay = pts[k]; bx, by = pts[k + 1]
            if math.hypot(bx - ax, by - ay) < 300.0:
                continue
            mx, my = (ax + bx) / 2, (ay + by) / 2
            for ci, (ctr, ang, hw, hh, R) in enumerate(info):
                if abs(mx - ctr[0]) > R * 1.5 or abs(my - ctr[1]) > R * 1.5:
                    continue
                p0 = _rotate_point(ax, ay, -ang, ctr); p1 = _rotate_point(bx, by, -ang, ctr)
                x0, y0, x1, y1 = p0[0] - ctr[0], p0[1] - ctr[1], p1[0] - ctr[0], p1[1] - ctr[1]
                if abs((x0 + x1) / 2) > R or abs((y0 + y1) / 2) > R:
                    continue
                a = math.degrees(math.atan2(abs(y1 - y0), abs(x1 - x0)))
                if a >= 85:
                    hits[(layer, ci)].append(('v', (x0 + x1) / 2, min(y0, y1), max(y0, y1)))
                elif a <= 5:
                    hits[(layer, ci)].append(('h', (y0 + y1) / 2, min(x0, x1), max(x0, x1)))

    def _walk(entities, depth, clip_stack):
        for e in entities:
            try:
                et = e.dxftype()
            except Exception:
                continue
            if et == 'INSERT':
                if depth >= max_block_depth or _insert_hidden(e, hidden):
                    continue
                clip_poly = _active_xclip_polygon_world(e)
                new_stack = clip_stack + [clip_poly] if clip_poly else clip_stack
                try:
                    _walk(e.virtual_entities(), depth + 1, new_stack)
                except Exception:
                    continue
                continue
            try:
                layer = e.dxf.layer or '0'
                if layer not in cand:
                    continue
                if et == 'LINE':
                    pts = [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]
                elif et == 'LWPOLYLINE':
                    pts = [(q[0], q[1]) for q in e.get_points('xy')]
                    if e.closed and pts:
                        pts.append(pts[0])
                elif et == 'POLYLINE':
                    pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                else:
                    continue
                if len(pts) >= 2:
                    mid = ((pts[0][0] + pts[-1][0]) / 2, (pts[0][1] + pts[-1][1]) / 2)
                    if _point_in_clip_stack(mid, clip_stack):
                        _add(layer, pts)
            except Exception:
                continue

    try:
        _walk(msp, 0, [])
    except Exception:
        traceback.print_exc()

    def _cov(lines, lo, hi):
        if hi <= lo:
            return 0.0
        iv = sorted((max(a, lo), min(b, hi)) for _o, _p, a, b in lines if min(b, hi) > max(a, lo))
        tot, cur = 0.0, None
        for a, b in iv:
            if cur and a <= cur[1]:
                cur = (cur[0], max(cur[1], b))
            else:
                if cur:
                    tot += cur[1] - cur[0]
                cur = (a, b)
        if cur:
            tot += cur[1] - cur[0]
        return tot / (hi - lo)

    scores = Counter()
    covered = defaultdict(set)
    hug = Counter()          # 승강기 차실 윤곽에 거의 붙어 있는(≤HUG_MM) 승강로 수
    HUG_MM = 120.0
    for (layer, ci), lines in hits.items():
        ctr, ang, hw, hh, R = info[ci]
        sx, sy = max(150.0, 0.25 * 2 * hw), max(150.0, 0.25 * 2 * hh)
        sides = 0
        hug_sides = 0
        for orient, edge, lo, hi, sl, sign in (('v', -hw, -hh, hh, sx, -1), ('v', hw, -hh, hh, sx, 1),
                                                ('h', -hh, -hw, hw, sy, -1), ('h', hh, -hw, hw, sy, 1)):
            sel = [ln for ln in lines if ln[0] == orient and
                   (edge - sl <= ln[1] <= edge + near_out_mm if sign > 0 else edge - near_out_mm <= ln[1] <= edge + sl)]
            if _cov(sel, lo, hi) >= min_cover:
                sides += 1
                # 심볼 가장자리에서 가장 가까운 선까지의 바깥쪽 거리 — 벽 안쪽 면은 차실 윤곽보다 최소 수백 mm 밖에
                # 있지만, 차실 윤곽을 그린 레이어는 거의 0mm(실측: 레이어 '7' — 결과가 X사각형과 같은 1565x2030)
                gaps = [(ln[1] - edge) if sign > 0 else (edge - ln[1]) for ln in sel if _cov([ln], lo, hi) >= 0.15]
                if gaps and min(gaps) <= HUG_MM:
                    hug_sides += 1
        if sides >= 3:
            scores[layer] += 1
            covered[layer].add(ci)
            if hug_sides >= 3:
                hug[layer] += 1
    if debug is not None:
        debug.extend((l, scores[l], hug[l]) for l in scores)
    # 대부분의 승강로에서 차실 윤곽에 밀착한 레이어는 벽이 아니라 승강기 도면 자체이므로 제외
    for layer in list(scores):
        if hug[layer] >= 0.6 * scores[layer] and _layer_priority(layer) >= 2 and layer not in keep_hug:
            del scores[layer]
    return [(layer, sc, covered[layer]) for layer, sc in sorted(scores.items(), key=lambda kv: -kv[1])]


def _snap_bbox_to_wall_segs(segs, guess_bbox_world, layer_hint: str = '골조',
                             search_margin_ratio: float = 1.5, min_seg_len_mm: float = 300.0,
                             angle_tol_deg: float = 8.0, size_min_mm: float = 800.0,
                             size_max_mm: float = 8000.0, strict_overlap_ratio: float = None,
                             max_reach_mm: float = None):
    """_snap_shaft_to_wall_lines의 실제 기하 계산부. 이미 수집해둔 벽선 후보
    목록(segs — _wall_segment_candidates의 반환값)을 받아 동작하므로, 같은
    DXF/같은 레이어에서 승강로 후보 여러 개를 한꺼번에 스냅할 때(지하주차장
    전체평면도의 승강로 수십 개 등) 파일을 후보마다 다시 읽고 중첩 블록을
    다시 순회하는 중복 비용 없이, segs를 한 번만 모아 재사용할 수 있다.

    max_reach_mm: 지정하면 중심점에서 이 거리를 넘어서는 벽선은 애초에
    검색 대상에서 제외한다. 병렬/나란한 승강로가 서로 가까이 있을 때
    (실제 도면 실측: 승강로 사이 간격이 600~900mm) search_margin_ratio만으로
    창을 넓히면 바로 옆 승강로를 감싸는 벽(또는 그 승강로 너머의 외벽)까지
    검색 범위에 들어와, 깊이가 실제보다 훨씬 짧게(옆 승강로 사이 칸막이벽에
    스냅) 또는 훨씬 길게(옆 승강로를 통째로 가로질러 그 너머 외벽에 스냅)
    잘못 측정되는 사례가 확인됨 — 호출부(_snap_parking_clusters_to_walls)가
    "가장 가까운 다른 승강로 중심까지 거리의 절반"을 넘지 않도록 넘겨주면
    구조적으로 옆 승강로 영역을 침범할 수 없다."""
    if not segs or not guess_bbox_world:
        return None

    gx0, gy0, gx1, gy1 = guess_bbox_world
    gw, gh = abs(gx1 - gx0), abs(gy1 - gy0)
    cx, cy = (gx0 + gx1) / 2, (gy0 + gy1) / 2
    margin_x = max(gw * search_margin_ratio, 1000.0)
    margin_y = max(gh * search_margin_ratio, 1000.0)
    if max_reach_mm is not None:
        margin_x = min(margin_x, max(max_reach_mm - gw / 2, 0.0))
        margin_y = min(margin_y, max(max_reach_mm - gh / 2, 0.0))
    sx0, sx1 = cx - gw / 2 - margin_x, cx + gw / 2 + margin_x
    sy0, sy1 = cy - gh / 2 - margin_y, cy + gh / 2 + margin_y

    # 여러 층을 관통하는 외곽 기준선/그리드선처럼 이 승강로 개구부와 무관하게
    # 훨씬 긴 선분은, 검색창 안에 걸리기만 하면(끝점 중 하나가 창 안에 있지
    # 않아도 선분이 창을 가로지르면 걸린다) '중심에 가장 가깝다'는 이유만으로
    # 잘못 뽑힐 수 있다(실제 도면에서 30m짜리 외벽 관통선이 진짜 벽(2700mm)
    # 대신 뽑히는 사례로 확인됨) — AI 게스 크기 대비 비정상적으로 긴 선분은
    # 애초에 후보에서 제외한다.
    max_seg_len_mm = max(max(gw, gh) * 4.0, 6000.0)

    verticals, horizontals = [], []
    for x0, y0, x1, y1 in segs:
        length = math.hypot(x1 - x0, y1 - y0)
        if length < min_seg_len_mm or length > max_seg_len_mm:
            continue
        seg_x0, seg_x1 = min(x0, x1), max(x0, x1)
        seg_y0, seg_y1 = min(y0, y1), max(y0, y1)
        if seg_x1 < sx0 or seg_x0 > sx1 or seg_y1 < sy0 or seg_y0 > sy1:
            continue
        angle = math.degrees(math.atan2(abs(y1 - y0), abs(x1 - x0)))
        if angle >= 90 - angle_tol_deg:
            verticals.append({'x': (x0 + x1) / 2, 'y0': seg_y0, 'y1': seg_y1})
        elif angle <= angle_tol_deg:
            horizontals.append({'y': (y0 + y1) / 2, 'x0': seg_x0, 'x1': seg_x1})

    def _overlaps(a0, a1, b0, b1, min_ratio):
        ov = min(a1, b1) - max(a0, b0)
        span = min(a1 - a0, b1 - b0)
        return span > 0 and ov / span >= min_ratio

    def _cluster_coverage(items, key, r0, r1, t0, t1, cluster_tol=80.0):
        """items(수직선이면 x/y0/y1, 수평선이면 y/x0/x1)를 key값(같은 벽선이면
        거의 동일한 x 또는 y)이 cluster_tol 이내인 것끼리 한 벽으로 묶고, 그
        벽에 속한 (문턱/문틀 등으로 끊어진) 개별 선분들을 [t0,t1] 구간에
        클리핑해 "겹치지 않게 합집합"으로 커버 길이를 구한다(같은 위치에
        중복으로 그려진 선은 두 번 세지 않도록). 실측 도면에서 진짜 벽은
        문/개구부 때문에 여러 개의 짧은 선분으로 쪼개져 있는 경우가 흔해서
        (예: 650+1300+650mm 세 조각), 개별 선분 하나의 길이만으로는 '문턱
        같은 잡음'과 '진짜 벽의 한 조각'을 구별할 수 없다 — 같은 벽 위치의
        조각들을 모두 합친 총 커버 비율로 판단해야 한다.
        반환: [{'key': 대표좌표, 'ratio': 커버비율}, ...]"""
        lo_t, hi_t = (t0, t1) if t0 <= t1 else (t1, t0)
        target_len = hi_t - lo_t
        # 건물 전체를 관통하는 외곽 기준선/그리드선처럼 대상 범위보다 훨씬 긴
        # 개별 선분은, 클리핑하면 항상 100% 커버로 계산돼(사실상 무조건
        # 통과) 실제 이 승강로와 무관한데도 "가장 가까운 벽"으로 잘못 뽑힐 수
        # 있다(실제 도면에서 여러 층을 관통하는 30m짜리 외벽선으로 확인됨).
        # 그래서 대상 범위의 4배(또는 최소 6000mm)보다 긴 선분은 애초에
        # 이 승강로의 벽 후보에서 제외한다.
        max_len = max(target_len * 4.0, 6000.0)
        clusters = []
        for it in items:
            placed = False
            for c in clusters:
                if abs(it[key] - c['key']) <= cluster_tol:
                    c['items'].append(it)
                    placed = True
                    break
            if not placed:
                clusters.append({'key': it[key], 'items': [it]})
        result = []
        for c in clusters:
            intervals = []
            for it in c['items']:
                if (it[r1] - it[r0]) > max_len:
                    continue
                a0, a1 = it[r0], it[r1]
                lo, hi = max(a0, lo_t), min(a1, hi_t)
                if hi > lo:
                    intervals.append((lo, hi))
            intervals.sort()
            merged = []
            for lo, hi in intervals:
                if merged and lo <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
                else:
                    merged.append((lo, hi))
            covered = sum(hi - lo for lo, hi in merged)
            ratio = covered / target_len if target_len > 0 else 0.0
            result.append({'key': c['key'], 'ratio': ratio})
        return result

    def _pick_side(coverage_list, center, min_ratio, side):
        passing = [c['key'] for c in coverage_list if c['ratio'] >= min_ratio]
        if side == 'low':
            cands = [k for k in passing if k <= center]
            return max(cands) if cands else None
        cands = [k for k in passing if k >= center]
        return min(cands) if cands else None

    def _pick_strict():
        """strict_overlap_ratio 모드 전용: 2단계(상호 보정) 커버리지 판정.

        1단계는 원래의 guess_bbox_world 그대로(gx0~gx1, gy0~gy1)를 기준
        범위로 각 축을 독립적으로 판정한다. 그런데 병렬 승강로를 합친
        guess 범위는 AI의 1차 위치 두 개를 단순히 합친 것이라 실제
        승강로 폭/높이보다 부풀려져 있는 축이 있을 수 있고, 그 축에서는
        진짜 벽(문틀로 쪼개진 짧은 조각들)의 합쳐진 커버 비율이 부풀려진
        기준 범위 대비 낮게 나와 기준치를 못 넘길 수 있다(실제 도면으로
        검증됨). 그래서 2단계에서는 반대축에서 이미 정확히 찾은 결과를
        기준 범위로 다시 사용해 재판정한다 — 부풀려진 원래 guess보다
        훨씬 정확한 기준이므로, 문틀로 쪼개진 진짜 벽도 정확히 잡아낸다."""
        vcov0 = _cluster_coverage(verticals, 'x', 'y0', 'y1', gy0, gy1)
        hcov0 = _cluster_coverage(horizontals, 'y', 'x0', 'x1', gx0, gx1)
        left_x0 = _pick_side(vcov0, cx, strict_overlap_ratio, 'low')
        right_x0 = _pick_side(vcov0, cx, strict_overlap_ratio, 'high')
        bottom_y0 = _pick_side(hcov0, cy, strict_overlap_ratio, 'low')
        top_y0 = _pick_side(hcov0, cy, strict_overlap_ratio, 'high')

        if bottom_y0 is not None and top_y0 is not None:
            vcov1 = _cluster_coverage(verticals, 'x', 'y0', 'y1', bottom_y0, top_y0)
        else:
            vcov1 = vcov0
        if left_x0 is not None and right_x0 is not None:
            hcov1 = _cluster_coverage(horizontals, 'y', 'x0', 'x1', left_x0, right_x0)
        else:
            hcov1 = hcov0

        left_x = _pick_side(vcov1, cx, strict_overlap_ratio, 'low')
        if left_x is None:
            left_x = left_x0
        right_x = _pick_side(vcov1, cx, strict_overlap_ratio, 'high')
        if right_x is None:
            right_x = right_x0
        bottom_y = _pick_side(hcov1, cy, strict_overlap_ratio, 'low')
        if bottom_y is None:
            bottom_y = bottom_y0
        top_y = _pick_side(hcov1, cy, strict_overlap_ratio, 'high')
        if top_y is None:
            top_y = top_y0

        if left_x is None or right_x is None or bottom_y is None or top_y is None:
            return None
        return left_x, right_x, bottom_y, top_y

    def _pick(use_overlap):
        if use_overlap:
            lc = [v for v in verticals if v['x'] <= cx and _overlaps(v['y0'], v['y1'], gy0, gy1, 0.15)]
            rc = [v for v in verticals if v['x'] >= cx and _overlaps(v['y0'], v['y1'], gy0, gy1, 0.15)]
            hb = [h for h in horizontals if _overlaps(h['x0'], h['x1'], gx0, gx1, 0.15)]
        else:
            lc = [v for v in verticals if v['x'] <= cx and v['y0'] <= cy <= v['y1']]
            rc = [v for v in verticals if v['x'] >= cx and v['y0'] <= cy <= v['y1']]
            hb = [h for h in horizontals if h['x0'] <= cx <= h['x1']]
        bc = [h for h in hb if h['y'] <= cy]
        tc = [h for h in hb if h['y'] >= cy]
        if not (lc and rc and bc and tc):
            return None
        left_x, right_x = max(v['x'] for v in lc), min(v['x'] for v in rc)
        bottom_y, top_y = max(h['y'] for h in bc), min(h['y'] for h in tc)
        return left_x, right_x, bottom_y, top_y

    if strict_overlap_ratio is not None:
        picked = _pick_strict() or _pick(use_overlap=True) or _pick(use_overlap=False)
    else:
        picked = _pick(use_overlap=True) or _pick(use_overlap=False)
    if not picked:
        return None
    left_x, right_x, bottom_y, top_y = picked

    # strict 모드(2단계 상호보정)는 "반대축에서 먼저 찾은 값"을 기준 범위로
    # 다시 커버리지를 재는 과정에서, 벽이 문/개구부로 끊겨 두께 방향으로
    # 안쪽/바깥쪽 두 줄(실측 약 200mm 간격)이 겹쳐 있는 변은 그 중간값으로
    # 살짝(실측 약 50mm) 흔들리는 경우가 확인됨 — 어느 실제 선에도 해당하지
    # 않는 좌표가 나올 수 있다는 뜻이라, 마지막으로 가장 가까운 실제 벽선
    # 좌표(150mm 이내)에 스냅해 "존재하는 선 위"로 고정한다.
    def _snap_to_nearest_line(val, items, key, tol=150.0):
        best = None
        for it in items:
            d = abs(it[key] - val)
            if d <= tol and (best is None or d < best[0]):
                best = (d, it[key])
        return best[1] if best else val

    left_x = _snap_to_nearest_line(left_x, verticals, 'x')
    right_x = _snap_to_nearest_line(right_x, verticals, 'x')
    bottom_y = _snap_to_nearest_line(bottom_y, horizontals, 'y')
    top_y = _snap_to_nearest_line(top_y, horizontals, 'y')

    width, depth = right_x - left_x, top_y - bottom_y
    if width <= 0 or depth <= 0:
        return None
    if not (size_min_mm <= width <= size_max_mm and size_min_mm <= depth <= size_max_mm):
        return None
    # AI의 1차 추정 크기와 너무 동떨어지면(예: 엉뚱한 다른 방 벽에 스냅) 신뢰하지 않는다.
    # 주의: 이 gw/gh는 회전 보정 래퍼(_snap_bbox_to_wall_segs_any_angle)를 통해
    # 호출될 경우 "회전된 bbox"의 가로/세로라 원래 심볼 크기보다 부풀려져 있을 수
    # 있다(회전된 사각형의 축정렬 bbox는 항상 원래보다 크다) — 그래서 여기서는
    # 느슨한 범위(명백한 오탐만 제거)만 적용하고, 원래(회전 전) 심볼 크기 대비
    # 엄격한 판정은 호출부에서 한다.
    if gw > 0 and gh > 0:
        ratio_w, ratio_h = width / gw, depth / gh
        if not (0.3 <= ratio_w <= 3.0 and 0.3 <= ratio_h <= 3.0):
            return None

    pts = [(left_x, bottom_y), (right_x, bottom_y), (right_x, top_y), (left_x, top_y)]
    return {
        'points': pts, 'source': 'vector_snap', 'iou': None,
        'width_mm': round(width, 1), 'height_mm': round(depth, 1),
        'confidence': 'high',
        'evidence': (f"'{layer_hint}' 레이어 벽선 벡터 좌표(좌{left_x:.0f}/우{right_x:.0f}/"
                     f"하{bottom_y:.0f}/상{top_y:.0f})로 직접 산출 — AI 이미지 판독 없이 실제 도면 좌표값"),
    }


def _dominant_wall_angle_deg(segs, center, radius_mm, min_len_mm: float = 500.0):
    """center 주변 radius_mm 이내 벽선들의 각도(0~90도로 접어서, 서로 수직인
    두 방향을 같은 값으로 취급)의 최빈값을 구해, 이 구역 벽체가 월드 좌표축
    에서 얼마나 회전돼 있는지 추정한다. 단지 전체평면도는 부지 형태에 맞춰
    동마다(또는 구역마다) 서로 다른 각도로 돌려 배치되는 경우가 흔해서(실측
    확인: 같은 도면 안에서 어떤 승강로 주변은 0도인데 다른 승강로 주변은
    30도), _snap_bbox_to_wall_segs의 angle_tol_deg(월드 축 기준 수평/수직만
    인정)만으로는 회전된 구역의 벽을 전혀 못 찾는다. 못 찾으면 0.0(회전 없음
    가정)을 반환해 호출부가 그대로 기존 로직으로 진행하게 한다."""
    cx, cy = center
    buckets = Counter()
    samples = []
    for x0, y0, x1, y1 in segs:
        length = math.hypot(x1 - x0, y1 - y0)
        if length < min_len_mm:
            continue
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        if math.hypot(mx - cx, my - cy) > radius_mm:
            continue
        angle = math.degrees(math.atan2(y1 - y0, x1 - x0)) % 90.0
        buckets[round(angle)] += 1
        samples.append((angle, length))
    if not buckets:
        return 0.0
    peak = float(buckets.most_common(1)[0][0])
    # 정수 단위로 반올림한 각도는 최대 0.5도 틀려(긴 변 2.6m에서 20mm 가까운 오차) 치수가 어긋난다 —
    # 최빈 각도 ±2.5도 안 벽선들의 길이 가중 평균으로 소수점 각도까지 정밀화한다.
    num = den = 0.0
    for ang, ln in samples:
        d = (ang - peak + 45.0) % 90.0 - 45.0
        if abs(d) <= 2.5:
            num += d * ln
            den += ln
    return (peak + num / den) % 90.0 if den > 0 else peak


def _rotate_point(x, y, angle_deg, origin):
    ox, oy = origin
    rad = math.radians(angle_deg)
    dx, dy = x - ox, y - oy
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    return ox + dx * cos_a - dy * sin_a, oy + dx * sin_a + dy * cos_a


def _plausible_aspect_shift(snapped_w, snapped_h, guess_w, guess_h, max_divergence: float = 1.45):
    """스냅된 폭/깊이의 가로세로 비율이, 원래 심볼 윤곽(guess)의 가로세로
    비율과 비교해 너무 많이 틀어졌는지 검사한다.

    회전된 구역(건물 배치가 부지에 맞춰 돌아간 코어)에서는 심볼의 월드축
    기준 bbox(guess)가 실제 승강로 폭/깊이보다 원래부터 부풀려져 있어(회전된
    사각형의 축정렬 bbox는 항상 더 크다), "스냅 결과가 guess보다 작다"는
    것 자체는 정상이다 — 실측 확인된 정상 사례들도 대체로 0.6~0.8배 정도
    축소됐다. 다만 이 축소는 가로/세로 축에 비슷한 비율로 걸리는 게
    정상이며, 한쪽 축만 유독 더 찌그러지면(예: 가로는 거의 그대로인데 세로만
    반으로 줄어듦) 회전/벽 투영 때문이 아니라 엉뚱한(옆 승강로의 칸막이벽
    등) 벽에 스냅됐다는 신호다. 그래서 절대 크기 비율이 아니라 "가로/세로
    비율이 guess 대비 얼마나 틀어졌는지"로 판정한다(실측: 정상 사례는
    1.05~1.2배 안쪽, 실제 오류 사례(깊이 2500→1541)는 1.8배로 뚜렷이 구분됨)."""
    if guess_w <= 0 or guess_h <= 0 or snapped_w <= 0 or snapped_h <= 0:
        return True
    guess_aspect = guess_w / guess_h
    snapped_aspect = snapped_w / snapped_h
    if guess_aspect <= 0:
        return True
    divergence = snapped_aspect / guess_aspect
    if divergence < 1.0:
        divergence = 1.0 / divergence
    return divergence <= max_divergence


def _snap_bbox_to_wall_segs_any_angle(segs, guess_bbox_world, layer_hint: str = '골조', **kwargs):
    """_snap_bbox_to_wall_segs의 회전 대응 버전 — guess_bbox_world 주변의
    실제 벽 각도를 먼저 추정하고, 축이 맞지 않으면(회전된 구역) 그 각도만큼
    좌표계를 돌려 축정렬 상태로 만든 뒤 기존 스냅 로직을 그대로 적용하고,
    결과 사각형을 다시 원래 각도로 되돌린다. 회전이 거의 없으면(<1도)
    회전 변환 없이 바로 기존 함수를 호출한다(불필요한 부동소수 오차 방지).

    스냅 결과를 반환하기 전에 _plausible_aspect_shift로 가로/세로 비율이
    원래 심볼 윤곽과 비정상적으로 틀어졌는지 한 번 더 검사한다(옆 승강로의
    벽을 잘못 집어 한쪽 축만 찌그러지는 경우를 걸러냄 — 자세한 이유는 그
    함수 설명 참조). 걸러지면 이 벽 레이어로는 못 찾은 것으로 처리해
    호출부가 심볼 윤곽(symbol_bbox)으로 낮춰 표시하게 한다."""
    if not segs or not guess_bbox_world:
        return None
    gx0, gy0, gx1, gy1 = guess_bbox_world
    guess_w, guess_h = abs(gx1 - gx0), abs(gy1 - gy0)
    center = ((gx0 + gx1) / 2, (gy0 + gy1) / 2)
    search_radius = max(abs(gx1 - gx0), abs(gy1 - gy0)) * 3.0 + 3000.0
    angle = _dominant_wall_angle_deg(segs, center, search_radius)
    if abs(angle) < 1.0 or abs(angle - 90.0) < 1.0:
        snapped = _snap_bbox_to_wall_segs(segs, guess_bbox_world, layer_hint=layer_hint, **kwargs)
        if snapped and not _plausible_aspect_shift(snapped['width_mm'], snapped['height_mm'], guess_w, guess_h):
            return None
        return snapped

    rot_segs = []
    for x0, y0, x1, y1 in segs:
        rx0, ry0 = _rotate_point(x0, y0, -angle, center)
        rx1, ry1 = _rotate_point(x1, y1, -angle, center)
        rot_segs.append((rx0, ry0, rx1, ry1))

    corners = [(gx0, gy0), (gx1, gy0), (gx1, gy1), (gx0, gy1)]
    rot_corners = [_rotate_point(x, y, -angle, center) for x, y in corners]
    rxs = [p[0] for p in rot_corners]
    rys = [p[1] for p in rot_corners]
    rot_bbox = (min(rxs), min(rys), max(rxs), max(rys))

    snapped = _snap_bbox_to_wall_segs(rot_segs, rot_bbox, layer_hint=layer_hint, **kwargs)
    if not snapped:
        return None
    if not _plausible_aspect_shift(snapped['width_mm'], snapped['height_mm'], guess_w, guess_h):
        return None
    new_points = [_rotate_point(x, y, angle, center) for x, y in snapped['points']]
    snapped = dict(snapped)
    snapped['points'] = new_points
    snapped['evidence'] = (snapped.get('evidence', '') + f' (이 구역 벽체 회전각 {angle:.1f}도 보정 적용)').strip()
    return snapped


# ──────────────────────────────────────────────────────────────
#  거대 배치도(한 도면에 여러 동/전체 단지가 다 들어있는 파일)에서 승강로
#  위치를 자동으로 찾아, 그 주변만 잘라낸 "미니 도면"을 만드는 기능.
#
#  사람이 동별로 미리 잘라주지 않아도 되게 하려고: 도면 안에서 승강로
#  칸(X자 대각선 표시 등)이 보통 "ELEV" 계열 레이어(예: "A-ELEV")에 그려진다는
#  실무 관행을 이용해, 그 레이어의 선/폴리선 좌표를 전부 모아 가까운 것끼리
#  뭉치고(승강로 1칸 = 뭉치 1개), 뭉치 크기가 실제 승강로 칸다운 범위일 때만
#  후보로 인정한다. AI 이미지 판독 없이 벡터 좌표로 직접 찾으므로 빠르고
#  정확하며, 전체를 렌더링/분석할 필요 없이 그 좌표 주변만 잘라 쓸 수 있다.
# ──────────────────────────────────────────────────────────────
def _x_candidates(segs, min_len: float, min_cross_deg: float = 30.0):
    """중점에서 서로 교차하는 같은 길이의 두 선(= X자) 후보 목록. 각 항목: (길이, 중심, 선a, 선b)."""
    long_ = [(s_, math.hypot(s_[2] - s_[0], s_[3] - s_[1])) for s_ in segs]
    long_ = [(s_, ln) for s_, ln in long_ if ln >= min_len]
    out = []
    for i in range(len(long_)):
        a, la = long_[i]
        ma = ((a[0] + a[2]) / 2, (a[1] + a[3]) / 2)
        for j in range(i + 1, len(long_)):
            b, lb = long_[j]
            if abs(la - lb) > 0.05 * max(la, lb):
                continue
            mb = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
            if math.hypot(ma[0] - mb[0], ma[1] - mb[1]) > 80.0:
                continue
            d = abs(math.degrees(math.atan2(a[3] - a[1], a[2] - a[0]) - math.atan2(b[3] - b[1], b[2] - b[0]))) % 180
            if min_cross_deg <= d <= 180.0 - min_cross_deg:
                out.append((la, ma, a, b))
    return out


def _x_rect_of(cand):
    _l, ctr, a, b = cand
    corners = [(a[0], a[1]), (b[0], b[1]), (a[2], a[3]), (b[2], b[3])]
    ang = math.degrees(math.atan2(corners[1][1] - corners[0][1], corners[1][0] - corners[0][0])) % 90.0
    loc = [_rotate_point(x, y, -ang, ctr) for x, y in corners]
    w = max(p[0] for p in loc) - min(p[0] for p in loc)
    h = max(p[1] for p in loc) - min(p[1] for p in loc)
    return {'center': ctr, 'corners': corners, 'angle': ang, 'width': w, 'height': h}


def _x_symbol_rect(segs, min_len: float = 1500.0):
    """승강기 심볼의 X자 대각선(서로 중점에서 교차하는 같은 길이의 두 선)으로 차실 사각형을 정확히 구한다.
    사다리·문틀 같은 이웃 형상이 같은 클러스터에 섞여 있어도 심볼 윤곽 bbox와 각도를 흔들리지 않게 한다.
    여러 X가 있으면 가장 큰 것을 쓴다. 반환: {'center','corners'(월드 4점),'angle'(0~90),'width','height'} 또는 None."""
    cands = _x_candidates(segs, min_len)
    if not cands:
        return None
    return _x_rect_of(max(cands, key=lambda c: c[0]))


def _door_from_narrow_x(segs, main_ctr, angle_deg, main_len: float):
    """차실 X 옆에 붙은 '좁고 긴 X'(문: 폭 약 200mm, 길이 약 1400mm — 실측 세대 심볼)가 있으면 그쪽을
    문 방향으로 본다. 반환: 월드 단위벡터 또는 None."""
    best = None
    for cand in _x_candidates(segs, 400.0, min_cross_deg=8.0):
        if cand[0] >= main_len * 0.8:
            continue                      # 차실 X 자신은 제외
        r = _x_rect_of(cand)
        lo, hi = sorted((r['width'], r['height']))
        if lo <= 0 or hi / lo < 2.5:
            continue                      # 좁고 긴 것만
        ox, oy = _rotate_point(r['center'][0], r['center'][1], -angle_deg, main_ctr)
        ox, oy = ox - main_ctr[0], oy - main_ctr[1]
        dist = math.hypot(ox, oy)
        if dist < 300.0:
            continue
        if best is None or dist > best[0]:
            best = (dist, ox, oy)
    if not best:
        return None
    _d, ox, oy = best
    u = (1.0 if ox > 0 else -1.0, 0.0) if abs(ox) >= abs(oy) else (0.0, 1.0 if oy > 0 else -1.0)
    rad = math.radians(angle_deg)
    return (u[0] * math.cos(rad) - u[1] * math.sin(rad), u[0] * math.sin(rad) + u[1] * math.cos(rad))


def _door_direction(member_segs, center, angle_deg, min_asym: float = 60.0):
    """승강기 심볼의 문(승강장) 방향을 기하로 읽는다 — 월드 좌표의 단위벡터 (dx, dy), 못 읽으면 None.
    심볼의 문은 차실 사각형 바깥 한쪽에 붙은 짧은 선(실측: 중심에서 ±963mm, 길이 1400mm)이고, 차실
    윤곽선(±806~935)과 십자선(중심 통과)은 그보다 안쪽이다. 그래서 심볼 선분을 심볼 축 기준 로컬
    좌표로 돌려, 축에 평행한 선분 중 중심에서 가장 멀리 떨어진 선의 방향을 문 쪽으로 본다. 반대편
    최대 거리와의 차이가 min_asym 미만이면(좌우 대칭이라 문을 구별할 수 없음) None."""
    xr_main = _x_symbol_rect(member_segs)
    if xr_main:
        main_len = max((math.hypot(c_[2][2] - c_[2][0], c_[2][3] - c_[2][1]) for c_ in _x_candidates(member_segs, 1500.0)),
                       default=0.0)
        narrow = _door_from_narrow_x(member_segs, xr_main['center'], xr_main['angle'], main_len)
        if narrow:
            return narrow
    best = {'+x': 0.0, '-x': 0.0, '+y': 0.0, '-y': 0.0}
    for x0, y0, x1, y1 in member_segs:
        p0 = _rotate_point(x0, y0, -angle_deg, center)
        p1 = _rotate_point(x1, y1, -angle_deg, center)
        lx0, ly0, lx1, ly1 = p0[0] - center[0], p0[1] - center[1], p1[0] - center[0], p1[1] - center[1]
        dx, dy = abs(lx1 - lx0), abs(ly1 - ly0)
        if max(dx, dy) < 300.0:
            continue
        if dy <= 0.05 * max(dx, 1.0):          # 가로선 — 법선은 y
            o = (ly0 + ly1) / 2
            key = '+y' if o > 0 else '-y'
        elif dx <= 0.05 * max(dy, 1.0):        # 세로선 — 법선은 x
            o = (lx0 + lx1) / 2
            key = '+x' if o > 0 else '-x'
        else:
            continue
        best[key] = max(best[key], abs(o))
    # 한 축에서 양쪽 최대 거리의 차이가 가장 큰 축을 문 축으로 본다
    cand = []
    for ax in ('x', 'y'):
        a, b = best['+' + ax], best['-' + ax]
        if abs(a - b) >= min_asym:
            cand.append((abs(a - b), ('+' if a > b else '-') + ax))
    if not cand:
        return None
    _gap, key = max(cand)
    u = {'+x': (1.0, 0.0), '-x': (-1.0, 0.0), '+y': (0.0, 1.0), '-y': (0.0, -1.0)}[key]
    rad = math.radians(angle_deg)
    return (u[0] * math.cos(rad) - u[1] * math.sin(rad), u[0] * math.sin(rad) + u[1] * math.cos(rad))


def _same_door(c1, c2, min_dot: float = 0.9):
    """두 승강기의 문 방향이 같은지(병렬 코어 조건). 한쪽이라도 문 방향을 못 읽었으면 판단하지 않고
    True(기존 동작 유지)."""
    d1, d2 = c1.get('door'), c2.get('door')
    if not d1 or not d2:
        return True
    return d1[0] * d2[0] + d1[1] * d2[1] >= min_dot


# 줄여 쓴 승강기 레이어명('A-el', 'A-EV', 'E-ELV' 등)을 위한 토큰 규칙 — 레이어명을 '-', '_', '$', 공백 등으로
# 쪼갠 토큰 하나가 정확히 아래 값이거나 'ELEV'로 시작하면 승강기 레이어로 본다. 부분일치가 아니라
# 토큰 일치라서 'LEVEL'/'REVISION'/'DEVELOP' 같은 무관한 이름에는 걸리지 않는다.
_ELEV_TOKENS = {'EL', 'EV', 'ELV', 'LIFT', 'ELEVATOR', 'ELEVATORS', 'EVT', 'EVTR', 'E.V'}


def _has_elev_token(layer_name):
    for tok in re.split(r'[^A-Za-z0-9가-힣.]+', (layer_name or '').upper()):
        if tok in _ELEV_TOKENS or tok.startswith('ELEV') or tok.startswith('승강'):
            return True
    return False


# 병렬(나란히 붙은) 승강기로 묶는 차실 사이 최대 거리(차실 사각형 가장자리 간 최단거리, mm). 실측(두 도면):
# 같은 벽 안의 병렬 쌍은 600mm(한 쌍만 1000mm), 단독과 이웃 쌍/ㄱ자·마주 보기는 2555mm 이상.
PARALLEL_MAX_GAP_MM = 1200.0


def _rect_gap(c1, c2):
    """두 클러스터의 차실 사각형(rotated_rect) 가장자리 사이 최단거리."""
    def _pd(a, b, c):
        dx, dy = c[0] - b[0], c[1] - b[1]
        ln = dx * dx + dy * dy
        t = 0.0 if ln == 0 else max(0.0, min(1.0, ((a[0] - b[0]) * dx + (a[1] - b[1]) * dy) / ln))
        return math.hypot(a[0] - (b[0] + t * dx), a[1] - (b[1] + t * dy))
    A = c1['rotated_rect']['points']
    B = c2['rotated_rect']['points']
    best = float('inf')
    for i in range(len(A)):
        a0, a1 = A[i], A[(i + 1) % len(A)]
        for j in range(len(B)):
            b0, b1 = B[j], B[(j + 1) % len(B)]
            best = min(best, _pd(a0, b0, b1), _pd(a1, b0, b1), _pd(b0, a0, a1), _pd(b1, a0, a1))
    return best


def _linkable(c1, c2):
    """병렬 코어로 묶을 수 있는 두 승강기인지: 문 방향이 같고 차실 사이 거리가 PARALLEL_MAX_GAP_MM 이내."""
    return _same_door(c1, c2) and _rect_gap(c1, c2) <= PARALLEL_MAX_GAP_MM


def _x_rects_from_segs(segs, min_len=1500.0, size_lo=1000.0, size_hi=7000.0, cell=200.0):
    """선분 목록에서 승강기 X자(중점 교차·같은 길이) 사각형을 모두 찾는다(격자 인덱스, 중심 200mm 이내 중복 제거)."""
    segs = [sg for sg in segs if math.hypot(sg[2] - sg[0], sg[3] - sg[1]) >= min_len]
    grid = defaultdict(list)
    for i, sg in enumerate(segs):
        grid[(int(((sg[0] + sg[2]) / 2) // cell), int(((sg[1] + sg[3]) / 2) // cell))].append(i)
    rects = []
    for i, a in enumerate(segs):
        gx, gy = int(((a[0] + a[2]) / 2) // cell), int(((a[1] + a[3]) / 2) // cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((gx + dx, gy + dy), ()):
                    if j <= i:
                        continue
                    for cand in _x_candidates([a, segs[j]], min_len):
                        r = _x_rect_of(cand)
                        if size_lo <= r['width'] <= size_hi and size_lo <= r['height'] <= size_hi:
                            if not any(math.hypot(r['center'][0] - q['center'][0], r['center'][1] - q['center'][1]) < 200.0
                                       for q in rects):
                                rects.append(r)
    return rects


def _geometry_x_fallback(msp, hidden, max_block_depth=6):
    """레이어/블록 이름 단서로 승강기를 하나도 못 찾았을 때의 보완 — 이름과 무관하게 '형상'으로 찾는다.
    도면 전체에서 긴 선(1800~9000mm)을 모아, 중점에서 교차하는 같은 길이의 두 선(승강기 X자 대각선)을 찾고,
    그 사각형(1300~7000mm) 안쪽(+200mm)에 들어오는 선만 심볼 구성 요소로 반환한다(벽선은 더 길어 제외됨).
    반환: _find_shaft_points_by_layer의 found 항목 목록."""
    long_segs = []

    def _iter(entities, depth, clip_stack, cb):
        for e in entities:
            try:
                et = e.dxftype()
            except Exception:
                continue
            if et == 'INSERT':
                if depth >= max_block_depth or _insert_hidden(e, hidden):
                    continue
                clip_poly = _active_xclip_polygon_world(e)
                ns = clip_stack + [clip_poly] if clip_poly else clip_stack
                try:
                    _iter(e.virtual_entities(), depth + 1, ns, cb)
                except Exception:
                    continue
            elif et in ('LINE', 'LWPOLYLINE', 'ARC'):
                try:
                    layer = e.dxf.layer or '0'
                    if layer in hidden or layer == '승강로 사이즈':
                        continue
                    if et == 'LINE':
                        pts = [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]
                    elif et == 'LWPOLYLINE':
                        pts = [(q[0], q[1]) for q in e.get_points('xy')]
                    else:
                        c, r = e.dxf.center, e.dxf.radius
                        pts = [(c.x - r, c.y - r), (c.x + r, c.y + r)]
                    if len(pts) < 2:
                        continue
                    mid = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
                    if _point_in_clip_stack(mid, clip_stack):
                        cb(et, layer, pts)
                except Exception:
                    continue

    def _collect_long(et, layer, pts):
        if et == 'ARC':
            return
        for k in range(len(pts) - 1):
            ln = math.hypot(pts[k + 1][0] - pts[k][0], pts[k + 1][1] - pts[k][1])
            if 1800.0 <= ln <= 9000.0:
                long_segs.append((pts[k][0], pts[k][1], pts[k + 1][0], pts[k + 1][1]))

    _iter(msp, 0, [], _collect_long)
    if len(long_segs) < 2:
        return []
    rects = _x_rects_from_segs(long_segs, 1800.0, size_lo=1300.0)
    if not rects:
        return []
    boxes = []
    for r in rects:
        xs = [p[0] for p in r['corners']]
        ys = [p[1] for p in r['corners']]
        boxes.append((min(xs) - 200.0, min(ys) - 200.0, max(xs) + 200.0, max(ys) + 200.0))
    hcell = 3000.0
    bgrid = defaultdict(list)
    for bi, b in enumerate(boxes):
        for cx in range(int(b[0] // hcell), int(b[2] // hcell) + 1):
            for cy in range(int(b[1] // hcell), int(b[3] // hcell) + 1):
                bgrid[(cx, cy)].append(bi)
    found = []

    def _collect_members(et, layer, pts):
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        for bi in bgrid.get((int(((x0 + x1) / 2) // hcell), int(((y0 + y1) / 2) // hcell)), ()):
            b = boxes[bi]
            if x0 >= b[0] and y0 >= b[1] and x1 <= b[2] and y1 <= b[3]:
                cx = sum(xs) / len(xs)
                cy = sum(ys) / len(ys)
                segs = [(pts[k][0], pts[k][1], pts[k + 1][0], pts[k + 1][1]) for k in range(len(pts) - 1)] if et != 'ARC' else []
                found.append((cx, cy, x0, y0, x1, y1, pts, segs, layer))
                return

    _iter(msp, 0, [], _collect_members)
    return found


_ELEV_BLOCK_RE = re.compile(r'\d{3,4}\s*[Xx×*]\s*\d{3,4}.*인승')


def _elevator_block_names(doc):
    """'2500X2550_18인승' 형태의 이름을 가진 승강기 심볼 블록 — 블록 안 선의 레이어가 '0'처럼 단서가 없어도
    블록 자체로 찾는다. (이름 단서가 더 느슨하면 장애인용 소변기 같은 블록이 잡혀 오탐이 난다)"""
    return {blk.name for blk in doc.blocks
            if not blk.name.startswith('*') and _ELEV_BLOCK_RE.search(blk.name.split('$')[-1])}


def _find_shaft_points_by_layer(dxf_path: str, layer_keywords=SHAFT_LAYER_KEYWORDS,
                                 size_min_mm: float = 1300.0, size_max_mm: float = 7000.0,
                                 cluster_dist_mm: float = 4000.0, merge_bbox_cap_mm: float = 4500.0,
                                 max_block_depth: int = 6, use_auto: bool = True):
    """dxf_path 전체(모델스페이스 + 중첩 블록)에서 레이어명에 layer_keywords 중
    하나라도 포함된 LINE/LWPOLYLINE/ARC 개체를 모아 월드 좌표 기준으로 가까운
    것끼리 묶는다.

    단순히 "중심점 거리 < cluster_dist_mm면 묶는다"는 1차원 거리 기준만으로는
    묶을 수 없다 — 실제 도면으로 확인된 사례: 같은 승강로 심볼 하나(문턱/
    승강로 표시선 등으로 쪼개진 조각들) 안에서도 조각 간 거리가 900mm 가까이
    벌어지는데, 바로 옆에 나란히 붙어 있는(병렬) 서로 다른 두 승강로 사이의
    간격은 그보다도 더 좁은 700mm 안쪽인 경우가 있었다. 즉 "한 승강로 내부
    조각 사이 거리"보다 "서로 다른 두 승강로 사이 거리"가 더 좁을 수 있어서,
    거리 기준 하나만으로는 묶어야 할 것과 묶으면 안 되는 것을 구별할 수
    없다(실제로 거리 기준만 쓰면 병렬 승강로 두세 개가 하나의 거대한 뭉치로
    합쳐져 크기 상한을 넘겨 통째로 탈락 — 병렬 승강로와 그 옆 승강로가 함께
    사라지는 버그로 확인됨).

    그래서 "가까운 순서대로 묶되, 묶었을 때 합쳐진 bbox가 승강로 한 칸으로
    보기엔 비정상적으로 커지면(merge_bbox_cap_mm 초과) 묶지 않는다"는 크기
    제약을 추가한 탐욕적(greedy) 병합으로 바꿨다. 실측 확인: 이 도면에서
    정상적인 승강로 1칸의 최대 크기는 약 4300mm인 반면, 병렬 승강로 2칸이
    합쳐지면 최소 약 4900mm가 되므로, 그 사이값(기본 4500mm)을 상한으로 두면
    "같은 승강로의 조각끼리는 거리가 멀어도 합치고, 서로 다른 승강로끼리는
    거리가 가까워도 안 합친다"가 동시에 성립한다.

    뭉치의 최종 bbox 가로/세로가 모두 [size_min_mm, size_max_mm] 범위일 때만
    "승강로 칸 후보"로 인정해 반환한다(범위를 벗어나면 치수선/문자/계단실 등
    승강로가 아닌 다른 형상일 가능성이 높으므로 제외).

    반환: [{'center': (cx, cy), 'bbox': (x0, y0, x1, y1), 'n_entities': int}, ...]
    (x 좌표 오름차순 정렬 — 동/코어가 대개 부지를 따라 나란히 배열되므로 보기 편하게)."""
    try:
        import ezdxf
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
    except Exception:
        return []

    kws = [k.upper() for k in layer_keywords]
    hidden = _hidden_layer_names(doc)
    elev_blocks = _elevator_block_names(doc) if use_auto else set()

    def _layer_matches(layer_name):
        if layer_name in hidden:
            return False
        up = (layer_name or '').upper()
        return any(k in up for k in kws) or (use_auto and _has_elev_token(layer_name))

    def _entity_points(e):
        t = e.dxftype()
        try:
            if t == 'LINE':
                return [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]
            if t == 'LWPOLYLINE':
                return [(p[0], p[1]) for p in e.get_points('xy')]
            if t == 'ARC':
                c = e.dxf.center
                r = e.dxf.radius
                return [(c.x - r, c.y - r), (c.x + r, c.y + r)]
        except Exception:
            return None
        return None

    found = []  # (centroid_x, centroid_y, x0,y0,x1,y1, pts, segs)

    def _walk(entities, depth, clip_stack, force=False):
        for e in entities:
            try:
                etype = e.dxftype()
            except Exception:
                continue
            if etype in ('LINE', 'LWPOLYLINE', 'ARC'):
                try:
                    layer = e.dxf.layer
                except Exception:
                    layer = ''
                # ④가 만든 결과 레이어('승강로 사이즈')는 레이어명에 '승강'이 들어 있어
                # 결과 DXF를 다시 입력하면 심볼로 오인되므로 제외한다.
                if layer != '승강로 사이즈' and (_layer_matches(layer) or (force and layer not in hidden)):
                    pts = _entity_points(e)
                    if pts:
                        cx = sum(p[0] for p in pts) / len(pts)
                        cy = sum(p[1] for p in pts) / len(pts)
                        if _point_in_clip_stack((cx, cy), clip_stack):
                            xs = [p[0] for p in pts]
                            ys = [p[1] for p in pts]
                            # LINE/LWPOLYLINE의 실제 변(segment)들 — 나중에 이
                            # 승강로가 속한 구역의 벽 회전각을 심볼 자신의
                            # 선 방향으로부터 추정하는 데 쓴다(심볼이 보통
                            # 그 구역 벽과 같은 각도로 그려짐). ARC는 선분이
                            # 아니라 제외.
                            segs = [(pts[k][0], pts[k][1], pts[k + 1][0], pts[k + 1][1])
                                    for k in range(len(pts) - 1)] if etype != 'ARC' else []
                            found.append((cx, cy, min(xs), min(ys), max(xs), max(ys), pts, segs, layer))
            elif etype == 'INSERT' and depth < max_block_depth and not _insert_hidden(e, hidden):
                clip_poly = _active_xclip_polygon_world(e)
                new_stack = clip_stack + [clip_poly] if clip_poly else clip_stack
                is_elev_blk = force or e.dxf.name in elev_blocks
                try:
                    _walk(e.virtual_entities(), depth + 1, new_stack, is_elev_blk)
                except Exception:
                    continue

    _walk(msp, 0, [])
    if not found and use_auto:
        # 이름 단서로 하나도 못 찾았으면 X자 대각선 '형상'으로 보완 탐지한다.
        try:
            found = _geometry_x_fallback(msp, hidden, max_block_depth)
        except Exception:
            traceback.print_exc()
            found = []
    if not found:
        return []

    # ── 가까운 순서대로 묶되, 합친 bbox가 승강로 한 칸 크기를 넘기면 묶지
    #    않는 크기-제약 탐욕적 병합 (union-find + 루트별 bbox 추적) ──
    n = len(found)
    parent = list(range(n))
    root_bbox = {i: (found[i][2], found[i][3], found[i][4], found[i][5]) for i in range(n)}

    # 서로 다른 승강기 X자 심볼에 속한 조각끼리는 절대 하나로 묶지 않는다 — 승강기가 960mm 안팎으로 붙어 있으면
    # 한 승강기의 문틀/카운터웨이트 조각이 이웃 승강기 쪽에 먼저 합쳐져 이웃이 쪼개지거나 사라질 수 있다.
    try:
        xr_all = _x_rects_from_segs([sg for f_ in found for sg in f_[7]], 1500.0)
    except Exception:
        xr_all = []
    sym_tag = {}
    if len(xr_all) >= 2:
        for i in range(n):
            best = None
            for k, r in enumerate(xr_all):
                lx, ly = _rotate_point(found[i][0], found[i][1], -r['angle'], r['center'])
                nx = abs(lx - r['center'][0]) / (r['width'] / 2.0)
                ny = abs(ly - r['center'][1]) / (r['height'] / 2.0)
                sc = max(nx, ny)
                if sc <= 1.0 + 800.0 / max(min(r['width'], r['height']) / 2.0, 1.0) and (best is None or sc < best[0]):
                    best = (sc, k)
            if best:
                sym_tag[i] = best[1]
    root_tag = dict(sym_tag)

    def _find_root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    # 거리 cluster_dist_mm 이내인 후보 쌍만 모아 거리 오름차순으로 병합 시도
    # (가까운 조각부터 먼저 합쳐야 "같은 승강로 조각"이 뭉친 뒤에야 "다른
    # 승강로와의 거리"를 올바르게 판단할 수 있다).
    thresh2 = cluster_dist_mm * cluster_dist_mm
    pairs = []
    for i in range(n):
        xi, yi = found[i][0], found[i][1]
        for j in range(i + 1, n):
            dx = xi - found[j][0]
            dy = yi - found[j][1]
            d2 = dx * dx + dy * dy
            if d2 < thresh2:
                pairs.append((d2, i, j))
    pairs.sort(key=lambda p: p[0])

    # 크기 상한 때문에 "같은 승강로로는 못 묶지만, 그래도 가까이 붙어있는"
    # 쌍은 따로 적어둔다 — 이게 바로 "병렬(나란히 붙은) 승강로"끼리의 관계다.
    # 나중에 최종 승강로 단위로 변환해 "같은 코어"로 묶는 데 쓴다.
    adjacency_entity_pairs = []
    for _d2, i, j in pairs:
        ri, rj = _find_root(i), _find_root(j)
        if ri == rj:
            continue
        ta, tb = root_tag.get(ri), root_tag.get(rj)
        if ta is not None and tb is not None and ta != tb:
            adjacency_entity_pairs.append((i, j))
            continue          # 서로 다른 승강기 심볼 — 묶지 않고 인접(병렬 후보)으로만 기록
        bi, bj = root_bbox[ri], root_bbox[rj]
        merged = (min(bi[0], bj[0]), min(bi[1], bj[1]), max(bi[2], bj[2]), max(bi[3], bj[3]))
        mw, mh = merged[2] - merged[0], merged[3] - merged[1]
        if mw > merge_bbox_cap_mm or mh > merge_bbox_cap_mm:
            adjacency_entity_pairs.append((i, j))
            continue  # 병합하면 승강로 한 칸 크기를 넘어섬 — 서로 다른 승강로로 간주, 묶지 않음
        parent[ri] = rj
        root_bbox[rj] = merged
        if ta is not None or tb is not None:
            root_tag[rj] = ta if ta is not None else tb

    groups = {}
    for i in range(n):
        groups.setdefault(_find_root(i), []).append(i)

    clusters = []
    cluster_idxs_list = []
    for idxs in groups.values():
        xs0 = [found[i][2] for i in idxs]
        ys0 = [found[i][3] for i in idxs]
        xs1 = [found[i][4] for i in idxs]
        ys1 = [found[i][5] for i in idxs]
        x0, y0, x1, y1 = min(xs0), min(ys0), max(xs1), max(ys1)
        w, h = x1 - x0, y1 - y0
        if not (size_min_mm <= w <= size_max_mm and size_min_mm <= h <= size_max_mm):
            continue  # 승강로 칸다운 크기가 아니면 제외 (치수선/문자/오탐 등)
        center = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)

        # 심볼 자신이 그 구역 벽과 같은 각도로 회전되어 그려진 경우, 월드축
        # 기준 bbox(x0,y0,x1,y1)는 실제보다 부풀려져 있고 축도 안 맞는다 —
        # 심볼 자신의 선분 방향(벽 레이어 정보 없이도 구할 수 있음)에서
        # 회전각을 추정해, 축이 맞게 돌린 "진짜" 사각형(rotated_rect)도
        # 함께 구해둔다. ③ 벽체 스냅이 실패했을 때(symbol_bbox 폴백) 이
        # rotated_rect를 쓰면, 항상 가로로만 그려지던(실제 벽 방향과 다른)
        # 폴리선과 부풀려진 크기 문제를 피할 수 있다.
        member_segs = [s for i in idxs for s in found[i][7]]
        search_radius = max(w, h) * 1.5 + 1000.0
        angle = _dominant_wall_angle_deg(member_segs, center, search_radius, min_len_mm=200.0) \
            if member_segs else 0.0

        if abs(angle) < 1.0 or abs(angle - 90.0) < 1.0:
            rotated_rect = {
                'angle': 0.0,
                'points': [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
                'width_mm': w, 'height_mm': h,
            }
        else:
            member_pts = [p for i in idxs for p in found[i][6]]
            rot_pts = [_rotate_point(px, py, -angle, center) for px, py in member_pts]
            rxs = [p[0] for p in rot_pts]
            rys = [p[1] for p in rot_pts]
            rx0, ry0, rx1, ry1 = min(rxs), min(rys), max(rxs), max(rys)
            rw, rh = rx1 - rx0, ry1 - ry0
            rot_corners = [(rx0, ry0), (rx1, ry0), (rx1, ry1), (rx0, ry1)]
            world_corners = [_rotate_point(px, py, angle, center) for px, py in rot_corners]
            rotated_rect = {
                'angle': angle,
                'points': world_corners,
                'width_mm': rw, 'height_mm': rh,
            }

        # 심볼의 X자 대각선이 있으면 그것으로 차실 사각형/각도/중심을 정확히 잡는다(이웃 형상이 섞인
        # 클러스터에서도 흔들리지 않음). 문 방향은 그 사각형 주변 선분만으로 읽는다.
        xr = _x_symbol_rect(member_segs)
        door_segs, door_ctr = member_segs, center
        if xr:
            rotated_rect = {'angle': xr['angle'], 'points': xr['corners'],
                            'width_mm': xr['width'], 'height_mm': xr['height']}
            door_ctr = xr['center']
            rx, ry = xr['width'] * 0.75, xr['height'] * 0.75
            door_segs = []
            for sg in member_segs:
                mx_, my_ = (sg[0] + sg[2]) / 2, (sg[1] + sg[3]) / 2
                lx, ly = _rotate_point(mx_, my_, -xr['angle'], door_ctr)
                if abs(lx - door_ctr[0]) <= rx and abs(ly - door_ctr[1]) <= ry:
                    door_segs.append(sg)
        clusters.append({
            'center': center,
            'bbox': (x0, y0, x1, y1),
            'n_entities': len(idxs),
            'layers': sorted({found[i][8] for i in idxs}),
            'rotated_rect': rotated_rect,
            'door': _door_direction(door_segs, door_ctr, rotated_rect['angle']),
            'x_center': xr['center'] if xr else None,
        })
        cluster_idxs_list.append(idxs)

    # ── "병렬(나란히 붙은) 승강로" 그룹 묻기 ──
    # 위에서 크기 상한 때문에 하나로 못 묶은(adjacency_entity_pairs) 조각들을
    # 최종 승강로 단위로 변환해, 같은 코어(승강로 뱅크)에 속하는 승강로들을
    # group_id로 묻는다. 이렇게 찾은 그룹이 2개 이상의 승강로를 포함하면
    # "병렬", 1개뿐이면 "단독"으로 ⑤단계(검토표 반영)에서 쓴다.
    entity_to_cluster_idx = {}
    for ci, idxs in enumerate(cluster_idxs_list):
        for e in idxs:
            entity_to_cluster_idx[e] = ci

    n_clusters = len(clusters)
    group_parent = list(range(n_clusters))

    def _group_find(x):
        while group_parent[x] != x:
            group_parent[x] = group_parent[group_parent[x]]
            x = group_parent[x]
        return x

    for i, j in adjacency_entity_pairs:
        ci = entity_to_cluster_idx.get(i)
        cj = entity_to_cluster_idx.get(j)
        if ci is None or cj is None or ci == cj:
            continue
        if not _linkable(clusters[ci], clusters[cj]):
            continue          # 문 방향이 다르거나 차실 사이가 멀면(PARALLEL_MAX_GAP_MM 초과) 병렬 코어가 아님
        ra, rb = _group_find(ci), _group_find(cj)
        if ra != rb:
            group_parent[ra] = rb

    # ── 로비/통로를 사이에 둔 "병렬" 승강로 보강 탐지 ──
    # 위 adjacency_entity_pairs는 "심볼 조각 간 거리"(cluster_dist_mm, 기본
    # 4000mm) 이내인 개체 쌍만 후보로 보는데, 실제 도면에서는 승강로와
    # 승강로 사이에 로비/통로가 있어 심볼(중심점/외곽선) 간 거리가 그보다
    # 더 벌어지는 병렬 조합이 존재함이 확인됐다(예: 26인승 대형 승강로와
    # 16인승 승강로가 나란히 붙은 조합 — 심볼 중심간 거리는 약 8300mm지만,
    # 두 승강로의 외곽 bbox는 가로축으로 정확히 4430mm 떨어져 있고 세로축은
    # 완전히 겹침 — 같은 도면 안에서 같은 간격(4430mm)으로 여러 번 반복돼
    # 우연이 아니라 실제 설계상 "병렬(나란히 배치된 같은 코어)"임이 뚜렷함).
    # 이런 경우를 잡기 위해, 완성된 승강로 단위(클러스터) bbox끼리 비교해
    # "한쪽 축은 완전히 겹치고(나란히 배치) 다른 쪽 축 간격만 좁으면"
    # 같은 코어로 추가 연결한다. 두 축이 모두 벌어져 있으면(대각선으로
    # 떨어진, 서로 무관한 승강로)는 연결하지 않는다 — 실제 도면 실측으로
    # 교차검증: 이 조건에 맞는 쌍은 전부 "대형+소형 짝" 5쌍뿐이었고, 그 외
    # 떨어진 승강로들은 모두 두 축이 함께 벌어져 있어 걸리지 않았다.
    PARALLEL_GAP_MM = 5000.0
    OVERLAP_TOL_MM = 50.0
    for ci in range(n_clusters):
        bi = clusters[ci]['bbox']
        for cj in range(ci + 1, n_clusters):
            if _group_find(ci) == _group_find(cj):
                continue
            bj = clusters[cj]['bbox']
            gx = max(bi[0] - bj[2], bj[0] - bi[2], 0.0)
            gy = max(bi[1] - bj[3], bj[1] - bi[3], 0.0)
            aligned_x = gx <= OVERLAP_TOL_MM and OVERLAP_TOL_MM < gy <= PARALLEL_GAP_MM
            aligned_y = gy <= OVERLAP_TOL_MM and OVERLAP_TOL_MM < gx <= PARALLEL_GAP_MM
            if not (aligned_x or aligned_y):
                continue
            if not _linkable(clusters[ci], clusters[cj]):
                continue      # 문 방향이 다르거나(직각/마주 보기) 차실 사이가 멀면 병렬이 아님
            ra, rb = _group_find(ci), _group_find(cj)
            if ra != rb:
                group_parent[ra] = rb

    group_id_map = {}
    for ci in range(n_clusters):
        root = _group_find(ci)
        if root not in group_id_map:
            group_id_map[root] = len(group_id_map)
        clusters[ci]['group_id'] = group_id_map[root]

    # 그룹 판정이 끝났으니, X자 대각선을 찾은 클러스터는 중심/bbox를 X 사각형 기준으로 바로잡는다.
    for c in clusters:
        if c.get('x_center'):
            pts_ = c['rotated_rect']['points']
            c['center'] = c['x_center']
            c['bbox'] = (min(p[0] for p in pts_), min(p[1] for p in pts_), max(p[0] for p in pts_), max(p[1] for p in pts_))

    # 유령 심볼 제거 — 블록 배치 오류/거울상 복사 등으로 도면 본체에서 수백 m 이상 떨어진 곳(실측:
    # 다른 심볼들과 44km 떨어진 x좌표 거울상)에 같은 모양이 생기는 경우가 있다. 최근접 이웃이
    # OUTLIER_DIST_MM보다 먼 외톨이는, 다른 심볼이 3개 이상 있을 때만 승강로 후보에서 뺀다.
    OUTLIER_DIST_MM = 500000.0
    if len(clusters) >= 4:
        keep = []
        for i, c in enumerate(clusters):
            nn = min(math.hypot(c['center'][0] - o['center'][0], c['center'][1] - o['center'][1])
                     for j, o in enumerate(clusters) if j != i)
            if nn <= OUTLIER_DIST_MM:
                keep.append(c)
        clusters = keep
    clusters.sort(key=lambda c: c['center'][0])
    return clusters


def _collect_layer_line_stats(dxf_path: str, max_block_depth: int = 6):
    """도면 전체(중첩 블록/XREF/XCLIP 포함)를 한 번만 순회해, 레이어별로
    LINE/LWPOLYLINE/POLYLINE 선분 개수와 총 길이(mm)를 집계한다. AI에게
    "이 중 어느 레이어가 벽체냐"를 묻기 위한 재료 — 레이어명 자체는 설계사마다
    달라도, 그 레이어에 그려진 선분 통계(개수가 많고 총 길이가 길다 = 건물
    전체를 두르는 벽/기둥일 가능성)가 레이어명의 의미를 추측하는 보조 단서가
    된다. 반환: [{'layer':str, 'count':int, 'total_length_mm':float}, ...]
    총 길이 내림차순 정렬."""
    try:
        import ezdxf
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
    except Exception:
        return []

    stats = {}
    hidden = _hidden_layer_names(doc)

    def _add(layer, length):
        if layer in hidden:
            return
        rec = stats.setdefault(layer, {'count': 0, 'total_length_mm': 0.0})
        rec['count'] += 1
        rec['total_length_mm'] += length

    def _walk(entities, depth, clip_stack):
        for e in entities:
            try:
                etype = e.dxftype()
            except Exception:
                continue
            if etype == 'INSERT':
                if depth >= max_block_depth or _insert_hidden(e, hidden):
                    continue
                clip_poly = _active_xclip_polygon_world(e)
                new_stack = clip_stack + [clip_poly] if clip_poly else clip_stack
                try:
                    _walk(e.virtual_entities(), depth + 1, new_stack)
                except Exception:
                    continue
                continue
            try:
                layer = e.dxf.layer or '0'
                if etype == 'LINE':
                    p0, p1 = e.dxf.start, e.dxf.end
                    cx, cy = (p0.x + p1.x) / 2, (p0.y + p1.y) / 2
                    if _point_in_clip_stack((cx, cy), clip_stack):
                        _add(layer, math.hypot(p1.x - p0.x, p1.y - p0.y))
                elif etype in ('LWPOLYLINE', 'POLYLINE'):
                    if etype == 'LWPOLYLINE':
                        pts = [(p[0], p[1]) for p in e.get_points('xy')]
                    else:
                        pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                    if len(pts) >= 2:
                        cx = sum(p[0] for p in pts) / len(pts)
                        cy = sum(p[1] for p in pts) / len(pts)
                        if _point_in_clip_stack((cx, cy), clip_stack):
                            length = sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
                                         for i in range(len(pts) - 1))
                            _add(layer, length)
            except Exception:
                continue

    try:
        _walk(msp, 0, [])
    except Exception:
        return []

    out = [{'layer': k, 'count': v['count'], 'total_length_mm': v['total_length_mm']}
           for k, v in stats.items()]
    out.sort(key=lambda d: -d['total_length_mm'])
    return out


_CAPACITY_TEXT_RE = re.compile(r'(\d{1,3})\s*인승')


def _collect_capacity_text_points(msp, max_block_depth: int = 6):
    """도면 전체(중첩 블록 포함)에서 "N인승" 텍스트의 (x, y, 인승수)를 한 번만
    모아 반환한다 — 승강로 후보가 여러 개일 때 후보마다 파일을 다시 읽지
    않고 이 목록을 재사용하기 위함."""
    points = []

    def _walk(entities, depth):
        for e in entities:
            try:
                etype = e.dxftype()
            except Exception:
                continue
            if etype == 'INSERT':
                if depth >= max_block_depth:
                    continue
                try:
                    _walk(e.virtual_entities(), depth + 1)
                except Exception:
                    continue
            elif etype in ('TEXT', 'MTEXT'):
                try:
                    text = e.dxf.text if etype == 'TEXT' else e.text
                    x, y = e.dxf.insert.x, e.dxf.insert.y
                except Exception:
                    continue
                if not text:
                    continue
                m = _CAPACITY_TEXT_RE.search(text)
                if m:
                    points.append((x, y, int(m.group(1))))

    try:
        _walk(msp, 0)
    except Exception:
        pass
    return points


def _assign_capacities(centers, capacity_points, radius_mm: float = 4000.0):
    """승강로 후보 중심점 목록(centers, 인덱스 순서가 결과 키)에 "N인승"
    텍스트(capacity_points)를 매칭한다 — 텍스트마다 "반경 안 가장 가까운
    승강로 1곳"에만 배정하는 방식(텍스트 기준 최근접, 승강로 기준이 아님).

    처음에는 "승강로 기준 최근접, 1:1 배타"로 구현했으나(먼 승강로가 가까운
    승강로의 텍스트를 가로채는 문제를 막기 위함), 실제 도면에 같은 승강로를
    가리키는 텍스트가 2개 있는 경우(블록 속성 텍스트가 심볼 중심점에 정확히
    겹쳐 있고, 그 옆에 사람이 또 넣은 라벨 텍스트가 따로 있는 식)에 그
    방식이 오히려 새 버그를 만들었다: 승강로 A가 자신의 "가장 가까운"
    텍스트(거리 0)를 먼저 가져가면, A에 속한 "두 번째로 가까운" 텍스트가
    남게 되고, 그 텍스트가 실제로는 A의 것인데도 더 먼 다른(인승 표기가
    전혀 없는) 승강로 B가 "아직 비어 있다"는 이유로 가로채는 사례가
    실제로 확인됨.

    그래서 기준을 승강로가 아니라 텍스트로 뒤집었다: 텍스트 하나하나가
    "자기와 가장 가까운 승강로 1곳"에 배정되므로, 같은 승강로를 가리키는
    텍스트가 여러 개 있어도(모두 그 승강로가 자신들의 최근접 승강로이므로)
    전부 그 승강로로 모이고, 다른 승강로가 가로챌 수 없다. 한 승강로에
    배정된 텍스트가 여러 개면 그중 가장 가까운 것의 인승 값을 쓴다(값이
    다르면 더 가까운 쪽을 신뢰 — 보통은 모두 같은 값).

    반환: {center_index: capacity:int} — 반경 안에 가장 가까운 것으로 배정된
    텍스트가 없는 승강로는 결과에 없음(공란으로 둠 — AI 임의 추정 금지,
    사용자 지시)."""
    best_per_shaft = {}  # ci -> (dist, cap)
    for x, y, cap in capacity_points:
        best = None  # (dist, ci)
        for ci, (cx, cy) in enumerate(centers):
            dist = math.hypot(x - cx, y - cy)
            if dist <= radius_mm and (best is None or dist < best[0]):
                best = (dist, ci)
        if best is None:
            continue
        dist, ci = best
        if ci not in best_per_shaft or dist < best_per_shaft[ci][0]:
            best_per_shaft[ci] = (dist, cap)

    return {ci: cap for ci, (_dist, cap) in best_per_shaft.items()}


# ──────────────────────────────────────────────────────────────
#  병렬(나란히 붙은) 승강로 그룹 전용 AI 위치 보정
#
#  병렬 코어는 칸 사이가 칸막이벽 하나로 붙어 있고, 심볼 윤곽이 실제 칸보다
#  작거나 어긋나 있어서(실측: 병렬 2·3번째 칸이 벽미검출로 떨어져 심볼 크기
#  2077x2260로 표시됨) 심볼 bbox를 기준으로 한 일반 벽 스냅이 실패한다.
#  이 단계는 그 경우에만 쓴다: 그룹 주변의 벽선 좌표(회전 보정 후 로컬 좌표)를
#  텍스트로 AI에 주고, AI는 "그 목록에 있는 좌표 중에서" 칸별 안쪽 벽면
#  사각형을 고른다. AI가 좌표를 지어내지 못하도록 코드가 (1) 목록에 있는 선인지
#  (2) 칸 심볼 중심을 포함하는지 (3) 칸끼리 겹치지 않는지 (4) 선택된 좌우/상하
#  벽선이 실제로 칸 길이의 절반 이상을 덮는지 검증하고, 하나라도 어긋나면
#  결과를 버려 기존 결과(⚠벽미검출)를 그대로 둔다.
# ──────────────────────────────────────────────────────────────
PARALLEL_FIX_PROMPT = """당신은 건축 평면도에서 승강로(엘리베이터 샤프트) 안쪽 벽면 치수를 읽는 보조원입니다.
아래는 나란히 붙어 있는 승강로 {n}칸(병렬 코어)과 그 주변 벽선입니다.
좌표는 모두 이 코어가 축에 맞게 돌려 놓은 로컬 좌표(mm)입니다.

승강로 심볼 중심(칸 번호는 id):
{shaft_lines}

수직 벽선 (x좌표 | y구간):
{v_lines}

수평 벽선 (y좌표 | x구간):
{h_lines}

과제: 각 칸마다 그 칸의 "안쪽 벽면"(승강로 내부 공간을 감싸는 면) 사각형을 고르세요.
- x0,x1은 반드시 위 수직 벽선의 x좌표 중에서, y0,y1은 반드시 위 수평 벽선의 y좌표 중에서 고릅니다(목록에 없는 숫자 금지).
- 벽은 보통 안쪽/바깥쪽 두 줄로 그려집니다. 승강로 내부에 가까운 쪽(안쪽 면)을 고르세요.
- 인접한 칸 사이의 칸막이벽은 두 칸이 공유하거나 서로 붙어 있습니다. 한 칸이 옆 칸을 포함하면 안 됩니다.
- 각 칸 사각형은 그 칸 심볼 중심을 포함해야 하고, 일반적인 승강로 한 칸 크기(폭·깊이 각 1500~4500)여야 합니다.

반드시 JSON으로만 답하세요(설명 문장 금지):
{{"shafts": [{{"id": 1, "x0": 0, "x1": 0, "y0": 0, "y1": 0}}, ...]}}"""


def _local_wall_lines(segs, origin, angle_deg, window, min_len=300.0, max_len=12000.0,
                       axis_tol_deg=5.0, cluster_tol=40.0, join_gap=150.0, min_merged=400.0):
    """segs(월드 좌표 선분)를 origin 기준으로 -angle_deg 회전한 로컬 좌표로 바꾸고,
    window(x0,y0,x1,y1 — 로컬) 안의 수직/수평 벽선을 같은 좌표끼리 묶어 겹치지 않는
    구간 목록으로 돌려준다. 반환: (verticals, horizontals) —
    각 항목 {'pos': 좌표, 'spans': [(lo, hi), ...]}"""
    ox, oy = origin
    wx0, wy0, wx1, wy1 = window
    vs, hs = [], []
    for x0, y0, x1, y1 in segs:
        length = math.hypot(x1 - x0, y1 - y0)
        if length < min_len or length > max_len:
            continue
        rx0, ry0 = _rotate_point(x0, y0, -angle_deg, origin)
        rx1, ry1 = _rotate_point(x1, y1, -angle_deg, origin)
        lx0, ly0, lx1, ly1 = rx0 - ox, ry0 - oy, rx1 - ox, ry1 - oy
        mx, my = (lx0 + lx1) / 2, (ly0 + ly1) / 2
        if not (wx0 <= mx <= wx1 and wy0 <= my <= wy1):
            continue
        ang = math.degrees(math.atan2(abs(ly1 - ly0), abs(lx1 - lx0)))
        if ang >= 90 - axis_tol_deg:
            vs.append((mx, min(ly0, ly1), max(ly0, ly1)))
        elif ang <= axis_tol_deg:
            hs.append((my, min(lx0, lx1), max(lx0, lx1)))

    def _group(items):
        items.sort(key=lambda t: t[0])
        groups = []
        for pos, lo, hi in items:
            if groups and abs(pos - groups[-1]['pos_ref']) <= cluster_tol:
                groups[-1]['ivs'].append((lo, hi))
                groups[-1]['pos_sum'] += pos
                groups[-1]['n'] += 1
            else:
                groups.append({'pos_ref': pos, 'pos_sum': pos, 'n': 1, 'ivs': [(lo, hi)]})
        out = []
        for g in groups:
            ivs = sorted(g['ivs'])
            merged = []
            for lo, hi in ivs:
                if merged and lo <= merged[-1][1] + join_gap:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
                else:
                    merged.append((lo, hi))
            merged = [(lo, hi) for lo, hi in merged if hi - lo >= min_merged]
            if merged:
                out.append({'pos': g['pos_sum'] / g['n'], 'spans': merged})
        return out

    return _group(vs), _group(hs)


def _line_coverage(line, lo, hi):
    """벽선 하나(spans 합집합)가 [lo,hi] 구간을 덮는 비율."""
    if hi <= lo:
        return 0.0
    cov = sum(max(0.0, min(b, hi) - max(a, lo)) for a, b in line['spans'])
    return cov / (hi - lo)


# ──────────────────────────────────────────────────────────────
#  병렬(N대) 승강로 — 골조선만으로 승강로 치수 따기 (도면 텍스트 미사용)
#
#  실측 도면 구조(이미지로 확인): 병렬 승강기는 두 가지로 그려진다.
#   ① 칸마다 칸막이 골조가 있는 경우 — 칸마다 자기를 감싸는 사각형이 따로 있다.
#   ② 칸막이 없이 N대가 하나의 큰 골조 사각형(공용 승강로) 안에 들어 있는 경우 —
#      칸마다 "가장 가까운 벽"을 찾으면 옆 칸까지 포함한 같은 외곽이 나온다.
#  두 경우를 하나의 규칙으로 처리한다:
#   1) 각 칸 심볼에서 4방향으로, 심볼 가장자리 바깥의 가장 가까운 골조선(이중선이면
#      안쪽 면)을 찾아 그 칸을 감싸는 사각형 E_i를 만든다.
#   2) E_i가 다른 칸 심볼 중심을 포함하면(공용 외곽) 같은 bay로 묶는다.
#   3) bay에 N대가 있으면, 심볼 중심 좌표를 행/열로 나눠(2대=1x2, 3대=1x3, 2x2 등)
#      칸 사이마다 칸막이 골조선이 있으면 그 면으로, 없으면 이웃 중심의 중간선으로
#      자른다. 바깥 경계는 bay 외곽 E를 쓴다.
# ──────────────────────────────────────────────────────────────
def _enclosure_local(verticals, horizontals, gb, min_cover: float = 0.35, slack=None, want_info: bool = False,
                      max_out: float = 1000.0):
    """로컬 좌표의 심볼 윤곽 gb=(x0,y0,x1,y1) 바깥에서, 네 방향으로 가장 가까운 벽선
    (그 변 방향 심볼 폭의 min_cover 이상을 덮는 선)을 골라 (L, R, B, T)로 돌려준다.
    문 개구부로 벽이 끊겨도 합집합 커버리지로 판단한다.

    심볼 윤곽은 실제 벽 안쪽 면보다 안/밖으로 어긋나 그려지는 경우가 많다(실측: 심볼이 벽 안쪽
    면을 약 150mm 파고듦 — 허용치 150mm로는 안쪽 면을 놓치고 이중선의 바깥 줄을 잡아 폭이 250mm
    커졌다). 그래서 심볼 가장자리에서 '안쪽으로 slack'까지 들어온 선도 후보로 인정한다.
    slack=(sx, sy)를 안 주면 심볼 폭/높이의 25%(최소 150mm).

    문(승강장) 쪽 한 변은 벽선이 아예 없는 도면이 흔하다(실측: 그 변의 위/아래 벽이 문 개구부 앞에서
    끝나고 마주 보는 변은 다른 레이어/없음). 정확히 한 변만 없고 나머지 세 변이 확실하면, 그 변과
    직교하는 두 벽선이 끝나는 위치(둘이 250mm 이내로 일치할 때)를 빠진 변으로 추정한다.
    want_info=True면 ((L,R,B,T), 추정한 변 'L'/'R'/'B'/'T' 또는 None)을 돌려준다. 못 구하면 None."""
    x0, y0, x1, y1 = gb
    if slack is None:
        sx, sy = max(150.0, 0.25 * (x1 - x0)), max(150.0, 0.25 * (y1 - y0))
    else:
        sx, sy = slack

    def _pick(lines, lo, hi, edge, side, slack):
        best = None
        for ln in lines:
            # 승강로 안쪽 벽면은 심볼 가장자리에서 max_out(기본 1m) 이내에 있다 — 그보다 먼 선은 이 승강로의
            # 벽이 아니라 건물 외곽 등 다른 선이므로, 없는 변으로 보고(문 쪽 추정으로 넘김) 잡지 않는다.
            if side == 'low':
                ok = (edge - max_out <= ln['pos'] <= edge + slack)
            else:
                ok = (edge - slack <= ln['pos'] <= edge + max_out)
            if not ok or _line_coverage(ln, lo, hi) < min_cover:
                continue
            if best is None or (side == 'low' and ln['pos'] > best['pos']) or \
                    (side == 'high' and ln['pos'] < best['pos']):
                best = ln
        return best

    picks = {
        'L': _pick(verticals, y0, y1, x0, 'low', sx),
        'R': _pick(verticals, y0, y1, x1, 'high', sx),
        'B': _pick(horizontals, x0, x1, y0, 'low', sy),
        'T': _pick(horizontals, x0, x1, y1, 'high', sy),
    }
    pos = {k: (v['pos'] if v else None) for k, v in picks.items()}
    inferred = None
    missing = [k for k, v in picks.items() if v is None]
    if len(missing) == 1:
        k = missing[0]
        perp = [picks['B'], picks['T']] if k in ('L', 'R') else [picks['L'], picks['R']]
        lo_s, hi_s = (x0, x1) if k in ('L', 'R') else (y0, y1)
        if all(perp):
            ends = []
            for ln in perp:
                sp = [(a, b) for a, b in ln['spans'] if b > lo_s and a < hi_s]
                if not sp:
                    break
                ends.append(max(b for _a, b in sp) if k in ('R', 'T') else min(a for a, _b in sp))
            if len(ends) == 2 and abs(ends[0] - ends[1]) <= 250.0:
                v = (ends[0] + ends[1]) / 2.0
                edge = {'L': x0, 'R': x1, 'B': y0, 'T': y1}[k]
                sl = sx if k in ('L', 'R') else sy
                ok = (v >= edge - sl and v <= edge + 2000.0) if k in ('R', 'T') else \
                     (v <= edge + sl and v >= edge - 2000.0)
                if ok:
                    pos[k] = v
                    inferred = k
    if any(v is None for v in pos.values()):
        return None
    rect = (pos['L'], pos['R'], pos['B'], pos['T'])
    return (rect, inferred) if want_info else rect


def _split_axis(centers, lo, hi, lines, cross_lo, cross_hi, tol: float = 700.0,
                min_partition_cover: float = 0.6, double_line_mm: float = 500.0):
    """bay 안 한 축(예: 로컬 x)의 분할. centers: 그 축 좌표 목록(칸별). lo/hi: bay 외곽.
    lines: 이 축에 수직인 벽선들(위 축이 x면 수직선). 같은 위치(tol)의 중심은 같은 열로 묶고,
    인접한 두 열 사이에 [cross_lo,cross_hi]를 min_partition_cover 이상 덮는 칸막이 벽선이
    있으면 그 면(이중선이면 각 열에서 가까운 쪽)을, 없으면 두 열 중심의 중간선을 경계로 쓴다.
    반환: ([(lo_k, hi_k) per 열], 칸막이 존재 여부, 열 중심 목록)"""
    cols = []
    for c in sorted(centers):
        if cols and abs(c - cols[-1][-1]) <= tol:
            cols[-1].append(c)
        else:
            cols.append([c])
    col_c = [sum(g) / len(g) for g in cols]
    bounds_lo, bounds_hi = [lo], []
    has_partition = False
    for k in range(len(col_c) - 1):
        a, b = col_c[k], col_c[k + 1]
        part = sorted(ln['pos'] for ln in lines
                      if a < ln['pos'] < b and _line_coverage(ln, cross_lo, cross_hi) >= min_partition_cover)
        if part:
            has_partition = True
            p_lo, p_hi = part[0], part[-1]
            if p_hi - p_lo > double_line_mm:     # 이중선이 아니라 별개의 두 벽 — 가까운 면끼리
                bounds_hi.append(p_lo)
                bounds_lo.append(p_hi)
            else:
                bounds_hi.append(p_lo)
                bounds_lo.append(p_hi)
        else:
            mid = (a + b) / 2.0
            bounds_hi.append(mid)
            bounds_lo.append(mid)
    bounds_hi.append(hi)
    k = len(col_c)
    if k > 1 and not has_partition:
        # 칸막이가 전혀 없는 공용 승강로 — 외곽을 N등분(같은 규격 N대가 균등 배치되는 게 일반적)
        step = (hi - lo) / k
        bounds_lo = [lo + step * j for j in range(k)]
        bounds_hi = [lo + step * (j + 1) for j in range(k)]
    return list(zip(bounds_lo, bounds_hi)), has_partition, col_c


def _cell_slack(guesses, idxs):
    """bay 외곽 탐색의 '안쪽 허용치' — 합집합이 아니라 한 칸 심볼 크기의 25%(최소 150mm)로 잡는다.
    N대 bay에서 합집합 크기의 25%를 쓰면 4대 이상일 때 칸막이를 안쪽 벽으로 오인할 수 있다."""
    w = min(guesses[i][2] - guesses[i][0] for i in idxs)
    h = min(guesses[i][3] - guesses[i][1] for i in idxs)
    return max(150.0, 0.25 * w), max(150.0, 0.25 * h)


def _group_local_frame(segs, members, max_size_mm: float = 6500.0):
    """병렬 후보 그룹을 벽 각도로 돌려 축에 맞춘 로컬 좌표계로 옮긴다.
    반환: {'angle','origin','guesses'(칸별 심볼 윤곽),'centers','verticals','horizontals'}
    — 벽선이 부족하면 None."""
    n = len(members)
    if not segs or n < 1:
        return None
    gcx = sum(m['center'][0] for m in members) / n
    gcy = sum(m['center'][1] for m in members) / n
    extent = 0.0
    for m in members:
        x0, y0, x1, y1 = m['bbox']
        extent = max(extent, abs(m['center'][0] - gcx) + (x1 - x0) / 2, abs(m['center'][1] - gcy) + (y1 - y0) / 2)
    angle = _dominant_wall_angle_deg(segs, (gcx, gcy), extent + 3500.0)
    if abs(angle - 90.0) < 1.0:
        angle = 0.0
    origin = (gcx, gcy)
    guesses, centers = [], []
    for m in members:
        rr = m.get('rotated_rect')
        world_pts = rr['points'] if rr else [(m['bbox'][0], m['bbox'][1]), (m['bbox'][2], m['bbox'][1]),
                                              (m['bbox'][2], m['bbox'][3]), (m['bbox'][0], m['bbox'][3])]
        loc = [_rotate_point(x, y, -angle, origin) for x, y in world_pts]
        xs = [pt[0] - gcx for pt in loc]
        ys = [pt[1] - gcy for pt in loc]
        guesses.append((min(xs), min(ys), max(xs), max(ys)))
        cx, cy = _rotate_point(m['center'][0], m['center'][1], -angle, origin)
        centers.append((cx - gcx, cy - gcy))
    pad = max_size_mm
    window = (min(g[0] for g in guesses) - pad, min(g[1] for g in guesses) - pad,
              max(g[2] for g in guesses) + pad, max(g[3] for g in guesses) + pad)
    verticals, horizontals = _local_wall_lines(segs, origin, angle, window)
    if len(verticals) < 2 or len(horizontals) < 2:
        return None
    return {'angle': angle, 'origin': origin, 'guesses': guesses, 'centers': centers,
            'verticals': verticals, 'horizontals': horizontals}


def _bays_by_containment(frame):
    """규칙 기반 bay 판정(AI 없이). 1) 칸별로 감싸는 골조 사각형을 구해, 한 칸의 외곽이 다른 칸 중심을
    포함하면(= 공용 승강로) 같은 bay로 묶는다. 2) 그래도 자기 외곽을 못 구한 칸(공용 승강로에서 칸
    하나씩으로는 벽 커버리지가 모자란 경우 — 실측: 두 대가 한 벽 안에 있는 세대 코어)은 가장 가까운
    칸부터 합쳐 보고, 합친 외곽이 생기고 그 안에 다른 bay의 칸이 없으면 한 bay로 묶는다.
    반환: [bay_id per member]"""
    n = len(frame['centers'])
    centers, guesses = frame['centers'], frame['guesses']
    V, H = frame['verticals'], frame['horizontals']
    encl = [_enclosure_local(V, H, g) for g in guesses]
    parent = list(range(n))

    def _find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        if not encl[i]:
            continue
        L, R, B, T = encl[i]
        for j in range(n):
            cx, cy = centers[j]
            if i != j and L <= cx <= R and B <= cy <= T:
                parent[_find(i)] = _find(j)
    comps = {}
    for i in range(n):
        comps.setdefault(_find(i), []).append(i)
    comps = {k: v for k, v in enumerate(comps.values())}

    def _rect(idxs):
        union = (min(guesses[i][0] for i in idxs), min(guesses[i][1] for i in idxs),
                 max(guesses[i][2] for i in idxs), max(guesses[i][3] for i in idxs))
        return _enclosure_local(V, H, union, slack=_cell_slack(guesses, idxs))

    def _valid(idxs):
        r = _rect(idxs)
        if not r:
            return False
        L, R, B, T = r
        inside = [j for j in range(n) if L <= centers[j][0] <= R and B <= centers[j][1] <= T]
        return set(inside) == set(idxs)

    def _centroid(idxs):
        return (sum(centers[i][0] for i in idxs) / len(idxs), sum(centers[i][1] for i in idxs) / len(idxs))

    changed = True
    while changed:
        changed = False
        for bid in list(comps):
            if _valid(comps[bid]):
                continue
            c0 = _centroid(comps[bid])
            others = sorted((b for b in comps if b != bid),
                            key=lambda b: math.hypot(_centroid(comps[b])[0] - c0[0], _centroid(comps[b])[1] - c0[1]))
            for ob in others:
                if _valid(comps[bid] + comps[ob]):
                    comps[bid] = comps[bid] + comps[ob]
                    del comps[ob]
                    changed = True
                    break
            if changed:
                break
    out = [0] * n
    for bid, idxs in comps.items():
        for i in idxs:
            out[i] = bid
    return out


def _snap_parallel_bays(segs, members, bay_of=None, min_size_mm: float = 1200.0,
                         max_size_mm: float = 6500.0):
    """병렬 그룹(members: 클러스터 dict 리스트)의 칸별 승강로 사각형을 골조 벽선(segs)만으로
    구한다. bay_of(칸별 bay id)가 주어지면(AI/규칙으로 단독·병렬·대수가 이미 정해진 경우) 그
    구성을 그대로 따르고, 없으면 골조 포함관계로 직접 판정한다.
    bay마다: 대수 N=1이면 감싸는 골조 사각형 그대로, N>=2이면 그 사각형을 칸 심볼 중심 좌표의
    행/열 배치(2대=1x2, 3대=1x3, 2x2 등)로 나누되 칸 사이에 칸막이 골조선이 있으면 그 면,
    없으면 중심 사이 중간선으로 자른다. 반환: {member 리스트 인덱스: {...}} — 못 구한 칸은 빠진다."""
    frame = _group_local_frame(segs, members)
    if not frame:
        return {}
    n = len(members)
    gcx, gcy = frame['origin']
    angle, centers, guesses = frame['angle'], frame['centers'], frame['guesses']
    verticals, horizontals = frame['verticals'], frame['horizontals']
    if bay_of is None:
        bay_of = _bays_by_containment(frame)
    bays = {}
    for i, b in enumerate(bay_of):
        bays.setdefault(b, []).append(i)

    out = {}
    for idxs in bays.values():
        # bay 전체 심볼 윤곽(합집합) 바깥의 가장 가까운 골조선 = bay 외곽
        union = (min(guesses[i][0] for i in idxs), min(guesses[i][1] for i in idxs),
                 max(guesses[i][2] for i in idxs), max(guesses[i][3] for i in idxs))
        got = _enclosure_local(verticals, horizontals, union, slack=_cell_slack(guesses, idxs), want_info=True)
        if not got:
            continue
        (L, R, B, T), open_side = got
        if not all(L <= centers[i][0] <= R and B <= centers[i][1] <= T for i in idxs):
            continue
        rects, shared = {}, False
        if len(idxs) == 1:
            rects[idxs[0]] = (L, R, B, T)
        else:
            xs_cols, part_x, col_x = _split_axis([centers[i][0] for i in idxs], L, R, verticals, B, T)
            ys_rows, part_y, row_y = _split_axis([centers[i][1] for i in idxs], B, T, horizontals, L, R)
            if len(xs_cols) * len(ys_rows) != len(idxs):
                continue      # 격자(N=행x열)로 정리되지 않는 배치 — 포기
            shared = not (part_x or part_y)
            for i in idxs:
                ci = min(range(len(col_x)), key=lambda k: abs(col_x[k] - centers[i][0]))
                ri = min(range(len(row_y)), key=lambda k: abs(row_y[k] - centers[i][1]))
                rects[i] = (xs_cols[ci][0], xs_cols[ci][1], ys_rows[ri][0], ys_rows[ri][1])
        if not all(min_size_mm <= r[1] - r[0] <= max_size_mm and min_size_mm <= r[3] - r[2] <= max_size_mm
                   for r in rects.values()):
            continue
        bay_corners = [_rotate_point(gcx + x, gcy + y, angle, (gcx, gcy))
                       for x, y in ((L, B), (R, B), (R, T), (L, T))]
        for i, (rl, rr_, rb, rt) in rects.items():
            corners = [(rl, rb), (rr_, rb), (rr_, rt), (rl, rt)]
            world = [_rotate_point(gcx + x, gcy + y, angle, (gcx, gcy)) for x, y in corners]
            if shared:
                # 칸막이 없이 한 벽 안에 든 공용 승강로는 '하나의 승강로'다 — 폴리선과 치수는 승강로 전체를 쓴다
                # (칸별 분할값은 car_width/car_depth로만 남긴다).
                out[i] = {'points': bay_corners, 'width_mm': round(R - L, 1), 'depth_mm': round(T - B, 1),
                          'car_width_mm': round(rr_ - rl, 1), 'car_depth_mm': round(rt - rb, 1),
                          'shared': True, 'bay_n': len(idxs), 'bay_total': (round(R - L, 1), round(T - B, 1)),
                          'open_side': open_side}
                continue
            out[i] = {'points': world, 'width_mm': round(rr_ - rl, 1), 'depth_mm': round(rt - rb, 1),
                      'shared': shared, 'bay_n': len(idxs), 'bay_total': (round(R - L, 1), round(T - B, 1)),
                      'open_side': open_side}
    return out


def _bays_plausible(frame, bay_of):
    """AI가 낸 bay 구성이 도면 기하와 맞는지 검증한다: bay마다 심볼 합집합을 감싸는 골조선이 있고
    모든 칸 중심이 그 안에 있어야 한다."""
    bays = {}
    for i, b in enumerate(bay_of):
        bays.setdefault(b, []).append(i)
    for idxs in bays.values():
        g = frame['guesses']
        union = (min(g[i][0] for i in idxs), min(g[i][1] for i in idxs),
                 max(g[i][2] for i in idxs), max(g[i][3] for i in idxs))
        e = _enclosure_local(frame['verticals'], frame['horizontals'], union,
                             slack=_cell_slack(g, idxs))
        if not e:
            return False
        L, R, B, T = e
        if not all(L <= frame['centers'][i][0] <= R and B <= frame['centers'][i][1] <= T for i in idxs):
            return False
    return True


def _result_tag(r):
    """③ 결과 한 건의 신뢰도/구조 표시(미리보기·④ 도면 라벨·⑤ 검토표 공용)."""
    tag = {'wall_snap': '', 'ai_group_fix': ' ✎AI보정', 'bay_snap': '',
           'bay_shared': ''}.get(r.get('source'), ' ⚠벽미검출')
    if r.get('open_side'):
        tag += ' ⚠문쪽 벽선 없음→추정'
    if r.get('source') == 'bay_shared' and r.get('bay_info'):
        n, tw, td = r['bay_info']
        tag += f" ✎공용승강로{n}대 하나의 승강로({tw:.0f}x{td:.0f})"
    return tag


def _assign_core_labels(results):
    """③ 결과에 검토표와 똑같은 코어 라벨(코어3A 등)을 붙인다. 코어 번호는 그룹
    (병렬 묶음)의 최소 index 순, 병렬 그룹 안은 A/B/C. 미리보기·④ 도면 라벨·⑤
    검토표가 모두 이 한 함수의 결과를 써서 번호가 서로 어긋나지 않게 한다.
    반환: {index: (core_label, is_parallel)}"""
    groups = {}
    for r in results:
        groups.setdefault(r.get('group_id', r['index']), []).append(r)
    ordered = sorted(groups, key=lambda gid: min(r['index'] for r in groups[gid]))
    labels = {}
    for core_no, gid in enumerate(ordered, start=1):
        members = sorted(groups[gid], key=lambda r: r['index'])
        parallel = len(members) > 1
        for sub_i, r in enumerate(members):
            labels[r['index']] = (f"코어{core_no}" + (chr(ord('A') + sub_i) if parallel else ''), parallel)
    return labels


def write_shaft_layer_dxf(dxf_path: str, shapes, layer_name: str = '승강로 사이즈', suffix: str = '_검토'):
    """원본 DXF는 건드리지 않고 '<원본이름>_검토.dxf' 사본을 만들어, shapes(각 항목
    {'points': [(x,y),...], 'label': str, 'layer': str(선택)}) 를 지정한 레이어에
    "새로" 닫힌 폴리선(+라벨 텍스트)으로 추가한다. 성공 시 사본 경로, 실패 시 None.

    도면에 기존에 그려진 벽/기둥(골조) 자체는 승강로 개구부를 감싸는 닫힌 도형이
    아닌 경우가 흔해(개별 LINE으로만 그려짐), 그런 기존 도형을 "찾아서" 재사용하지
    않는다 — 여기 들어오는 shapes는 항상 호출하는 쪽(AI 정밀 재확인 또는 확정된
    실측 치수)에서 새로 계산해 만든 좌표다.

    shapes의 각 항목은 승강로 심볼 하나처럼 "실제로 그 부분을 감싸는" 폴리선이어야
    한다 — 여러 승강로를 합쳐 감싸는 큰 사각형 하나로 뭉뚱그리지 않는다(호출하는
    쪽에서 이미 승강로별로 나눠 넘겨준다). 항목별로 'layer'를 지정하면 그
    레이어에, 지정하지 않으면 layer_name(기본 레이어)에 그린다.

    육안으로 바로 확인되도록 모든 대상 레이어는 색상 주황(ACI 30), 선가중치
    2.00mm로 만든다(도면에 $LWDISPLAY를 켜서 저장하므로 AutoCAD에서 별도 설정 없이
    바로 굵게 보인다)."""
    try:
        import ezdxf
        doc = ezdxf.readfile(dxf_path)
        doc.header['$LWDISPLAY'] = 1  # 선가중치가 화면에 바로 보이도록 켠다

        def _ensure_layer(name):
            if name not in doc.layers:
                doc.layers.add(name=name, color=_SHAFT_LAYER_COLOR)
            layer = doc.layers.get(name)
            layer.dxf.color = _SHAFT_LAYER_COLOR
            layer.dxf.lineweight = _SHAFT_LAYER_LINEWEIGHT
            return name

        _ensure_layer(layer_name)
        msp = doc.modelspace()
        for shp in shapes:
            pts = shp.get('points') or []
            if len(pts) < 3:
                continue
            target_layer = _ensure_layer(shp.get('layer') or layer_name)
            msp.add_lwpolyline(pts, format='xy', close=True,
                                dxfattribs={'layer': target_layer, 'color': _SHAFT_LAYER_COLOR,
                                            'lineweight': _SHAFT_LAYER_LINEWEIGHT})
            label = shp.get('label')
            if label:
                cx = sum(p[0] for p in pts) / len(pts)
                cy = sum(p[1] for p in pts) / len(pts)
                try:
                    msp.add_text(str(label), height=150,
                                 dxfattribs={'layer': target_layer, 'color': _SHAFT_LAYER_COLOR}
                                 ).set_placement((cx, cy))
                except Exception:
                    pass  # 라벨은 부가 정보이므로 실패해도 폴리선 추가 자체는 유지
        base, ext = os.path.splitext(dxf_path)
        out_path = f'{base}{suffix}{ext or ".dxf"}'
        doc.saveas(out_path)
        return out_path
    except Exception:
        traceback.print_exc()
        return None


# ──────────────────────────────────────────────────────────────
#  프로젝트 관리(저장소) — 세션 JSON(검토표)을 매번 파일 대화상자로 아무
#  경로에나 저장/열기 하던 기존 방식(💾 저장 / 📂 이어하기) 대신, 이름 붙인
#  "프로젝트" 목록을 한 곳에서 관리한다.
#
#  저장 위치는 api_settings_module.py의 설정 파일(.api_settings.json)과
#  같은 관례로, 프로그램 파일 옆의 숨김 폴더에 프로젝트 1개당 JSON 파일 1개.
# ──────────────────────────────────────────────────────────────
def _user_data_dir():
    # Nuitka onefile은 __file__이 매번 지워지는 임시 폴더라, 프로젝트 저장 폴더는 exe 옆에 둔다.
    try:
        return __compiled__.containing_dir
    except NameError:
        return os.path.dirname(os.path.abspath(__file__))


PROJECTS_DIR = os.path.join(_user_data_dir(), '.elevator_projects')


def _ensure_projects_dir():
    try:
        os.makedirs(PROJECTS_DIR, exist_ok=True)
    except Exception:
        pass


def _safe_project_filename(name: str) -> str:
    safe = re.sub(r'[\\/:*?"<>|]', '_', (name or '').strip())
    return safe or '이름없음'


def project_file_path(name: str) -> str:
    return os.path.join(PROJECTS_DIR, _safe_project_filename(name) + '.json')


def list_saved_projects():
    """저장된 프로젝트를 [{'name','version','saved_at','path'}, ...]로 반환한다
    (최종수정 최신순). 프로젝트명은 JSON 안의 'project_name' 필드를 우선하고,
    없으면(예: 외부에서 가져온 파일) 파일명을 그대로 쓴다."""
    _ensure_projects_dir()
    out = []
    try:
        filenames = os.listdir(PROJECTS_DIR)
    except Exception:
        filenames = []
    for fn in filenames:
        if not fn.lower().endswith('.json'):
            continue
        path = os.path.join(PROJECTS_DIR, fn)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            continue
        out.append({
            'name': data.get('project_name') or os.path.splitext(fn)[0],
            'version': data.get('version', ''),
            'saved_at': data.get('saved_at', ''),
            'path': path,
        })
    out.sort(key=lambda d: d.get('saved_at') or '', reverse=True)
    return out


def project_name_exists(name: str) -> bool:
    return os.path.exists(project_file_path(name))


def delete_project_file(name: str) -> bool:
    path = project_file_path(name)
    if not os.path.exists(path):
        return False
    os.remove(path)
    return True


def write_project_file(name: str, session: dict):
    _ensure_projects_dir()
    session = dict(session)
    session['project_name'] = name
    path = project_file_path(name)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(session, f, ensure_ascii=False, indent=2)
    return path


# ──────────────────────────────────────────────────────────────
#  메인 모듈 클래스
# ──────────────────────────────────────────────────────────────
class ElevatorShaftReviewModule:
    """01 승강로 CAD 도면 분석 탭. Electrical Design Hub 계열 탭 로더 규약을 따름:
    ElevatorShaftReviewModule(parent, main_app).build_ui()"""

    LAYOUT_OPTIONS = ['단독', '병렬']

    def __init__(self, parent, main_app=None):
        self.parent = parent
        self.main_app = main_app

        # 지하주차장 전체평면도 승강로 탐지 — ①②③④⑤ 단계별로 나눠, 어느
        # 단계에서 틀어지는지(심볼 탐지 자체가 안 되는지/벽 레이어 판별이
        # 틀렸는지/스냅이 안 되는지) 단계마다 결과를 보고 다음 단계로 넘어갈
        # 수 있게 한다.
        self._ukw_cache = []
        self._wkw_cache = []
        self._wall_prompted = False
        self.parking_source_path = None
        self.parking_clusters = []       # ① 심볼 위치 탐지 결과(벽 스냅 전, bbox만)
        self.parking_wall_layers = []    # ② AI가 판별한 벽체 레이어명 목록
        self.parking_shaft_results = []  # ③ 벽체 스냅까지 끝난 최종 결과

        self._row_seq = 0
        self.rows = []  # 검토 결과 누적 리스트(dict)

        # 현재 작업 화면이 어느 저장된 프로젝트에 속해 있는지(🗂 프로젝트 관리에서
        # 불러왔거나 "새 이름으로 저장"한 경우) — None이면 아직 어느 프로젝트에도
        # 속하지 않은 임시 작업 상태(저장 안 됨). "선택 항목에 덮어쓰기" 없이도
        # 이 값이 있으면 상단에 현재 프로젝트명을 표시해 여러 동을 계속 같은
        # 프로젝트에 누적하고 있는지 헷갈리지 않게 한다.
        self.current_project_name = None

    # ── 승강기 레이어명 단어(사용자 입력) ───────────────────────
    def _elev_pref_path(self):
        return os.path.join(_user_data_dir(), '.elev_prefs.json')

    def _load_elev_pref(self, key='elev_layer'):
        try:
            with open(self._elev_pref_path(), 'r', encoding='utf-8') as f:
                return str(json.load(f).get(key, ''))
        except Exception:
            return ''

    def _save_elev_pref(self):
        try:
            with open(self._elev_pref_path(), 'w', encoding='utf-8') as f:
                json.dump({'elev_layer': self.var_elev_kw.get().strip(), 'wall_layer': self.var_wall_kw.get().strip()},
                          f, ensure_ascii=False)
        except Exception:
            pass

    def _ask_layer_names(self, need_elev=True, n_miss=0):
        """승강기/승강로 골조 레이어명(일부 단어 가능)을 입력받는 작은 창. 취소하면 None."""
        dlg = tk.Toplevel(self.parent)
        dlg.title('레이어 지정')
        dlg.transient(self.parent.winfo_toplevel())
        dlg.resizable(False, False)
        out = {'v': None}
        msg = ('승강로를 찾지 못했습니다.' if need_elev else f'벽(골조)을 찾지 못한 승강로가 {n_miss}곳 있습니다.') + \
              '\n도면의 레이어명(일부 단어)을 입력하세요. 여러 개는 쉼표로 구분합니다.'
        tk.Label(dlg, text=msg, justify='left', padx=16, pady=10).grid(row=0, column=0, columnspan=2, sticky='w')
        tk.Label(dlg, text='승강기 레이어').grid(row=1, column=0, sticky='e', padx=(16, 6), pady=4)
        e1 = tk.Entry(dlg, width=30)
        e1.grid(row=1, column=1, padx=(0, 16))
        e1.insert(0, self.var_elev_kw.get())
        tk.Label(dlg, text='승강로 골조 레이어').grid(row=2, column=0, sticky='e', padx=(16, 6), pady=4)
        e2 = tk.Entry(dlg, width=30)
        e2.grid(row=2, column=1, padx=(0, 16))
        e2.insert(0, self.var_wall_kw.get())

        def ok():
            out['v'] = (e1.get().strip(), e2.get().strip())
            dlg.destroy()

        row = tk.Frame(dlg)
        row.grid(row=3, column=0, columnspan=2, pady=12)
        tk.Button(row, text='다시 분석', width=10, command=ok).pack(side='left', padx=6)
        tk.Button(row, text='취소', width=8, command=dlg.destroy).pack(side='left', padx=6)
        (e1 if need_elev else e2).focus_set()
        dlg.grab_set()
        self.parent.wait_window(dlg)
        return out['v'] if out['v'] and any(out['v']) else None

    def _user_layer_keywords(self, wall=False):
        try:
            raw = (self.var_wall_kw if wall else self.var_elev_kw).get()
        except Exception:
            return []
        return [t.strip() for t in re.split(r'[,;\s]+', raw or '') if t.strip()]

    # ── 공통 API 키/모델 조회 ──────────────────────────────────
    # ── 카드 헬퍼 (허브 스타일과 동일) ─────────────────────────
    def _make_card(self, parent, padx=24, pady=20, bg=SURFACE):
        outer = tk.Frame(parent, bg=BORDER_SOFT)
        inner = tk.Frame(outer, bg=bg, padx=padx, pady=pady)
        inner.pack(fill='both', expand=True, padx=1, pady=1)
        return outer, inner

    def _metal_btn(self, parent, text, command, text_color=None, bold=False):
        btn = tk.Button(
            parent, text=text, command=command,
            bg=BTN_METAL_BG, fg=text_color or BTN_METAL_TEXT_NORMAL,
            font=(_F_KOR, 9, 'bold' if bold else 'normal'),
            relief='flat', padx=12, pady=6, cursor='hand2',
            activebackground=BTN_METAL_HOVER,
        )
        btn.bind('<Enter>', lambda e: btn.config(bg=BTN_METAL_HOVER))
        btn.bind('<Leave>', lambda e: btn.config(bg=BTN_METAL_BG))
        return btn

    # ══════════════════════════════════════════════════════════
    #  UI 구성
    # ══════════════════════════════════════════════════════════
    def build_ui(self):
        configure_designhub_ttk_styles()

        # 카드가 4개(도면로드 / 실측 입력 / 3.제조사별 적합성 비교 결과표 / 상세)라
        # 세로로 쌓이면 창 높이(1040px)를 쉽게 넘긴다 — 예전엔 self.parent에 바로
        # 패킹해서 스크롤 수단이 전혀 없었고, 그 결과 창 아래로 넘친 내용(특히 3번
        # 결과표 카드 전체)이 화면에 아예 나타나지 않고 접근할 방법도 없었다.
        # self.parent를 세로 스크롤 가능한 캔버스로 감싸, 창 크기와 무관하게 모든
        # 카드에 스크롤로 도달할 수 있게 한다.
        outer = tk.Frame(self.parent, bg=CANVAS_BG)
        outer.pack(fill='both', expand=True)
        canvas = tk.Canvas(outer, bg=CANVAS_BG, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')

        scroll_frame = tk.Frame(canvas, bg=CANVAS_BG)
        scroll_window = canvas.create_window((0, 0), window=scroll_frame, anchor='nw')

        def _on_frame_configure(_event=None):
            canvas.configure(scrollregion=canvas.bbox('all'))
        scroll_frame.bind('<Configure>', _on_frame_configure)

        def _on_canvas_configure(event):
            canvas.itemconfig(scroll_window, width=event.width)
        canvas.bind('<Configure>', _on_canvas_configure)

        # 마우스 휠 스크롤은 커서가 이 캔버스 위에 있을 때만 전역 바인딩한다
        # (Mr. Q Design Hub의 다른 탭에도 bind_all이 계속 걸려있으면 그쪽 스크롤을
        # 방해하게 되므로, <Enter>/<Leave>로 이 탭에 있을 때만 켜고 끈다).
        def _on_mousewheel(event):
            if getattr(event, 'num', None) == 4:
                canvas.yview_scroll(-1, 'units')
            elif getattr(event, 'num', None) == 5:
                canvas.yview_scroll(1, 'units')
            else:
                canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')

        def _bind_wheel(_event=None):
            canvas.bind_all('<MouseWheel>', _on_mousewheel)
            canvas.bind_all('<Button-4>', _on_mousewheel)
            canvas.bind_all('<Button-5>', _on_mousewheel)

        def _unbind_wheel(_event=None):
            canvas.unbind_all('<MouseWheel>')
            canvas.unbind_all('<Button-4>')
            canvas.unbind_all('<Button-5>')

        canvas.bind('<Enter>', _bind_wheel)
        canvas.bind('<Leave>', _unbind_wheel)

        p = scroll_frame

        # ── 헤더 ──────────────────────────────────────────────
        header = tk.Frame(p, bg=CANVAS_BG)
        header.pack(fill='x', padx=40, pady=(32, 16))
        title_row = tk.Frame(header, bg=CANVAS_BG)
        title_row.pack(fill='x')
        title_left = tk.Frame(title_row, bg=CANVAS_BG)
        title_left.pack(side='left', anchor='w')
        tk.Label(title_left, text='DESIGN REVIEW · ELEVATOR SHAFT SIZE',
                 font=(_F_LATIN, 8, 'bold'), bg=CANVAS_BG, fg=ACCENT_PRIMARY).pack(anchor='w')
        tk.Label(title_left, text='승강로 CAD 도면 분석', font=FH_LG,
                 bg=CANVAS_BG, fg=TEXT_PRIMARY).pack(anchor='w', pady=(4, 2))
        session_row = tk.Frame(title_row, bg=CANVAS_BG)
        session_row.pack(side='right', anchor='ne')
        self.project_label_var = tk.StringVar(value='')
        self.lbl_project_name = tk.Label(session_row, textvariable=self.project_label_var,
                                          font=FS, bg=CANVAS_BG, fg=TEXT_MUTED, anchor='e')
        self.lbl_project_name.pack(side='top', anchor='e', pady=(0, 4))
        session_btn_row = tk.Frame(session_row, bg=CANVAS_BG)
        session_btn_row.pack(side='top')
        self._metal_btn(session_btn_row, '🗂 프로젝트 관리', self._open_project_manager,
                         text_color=BTN_METAL_TEXT_PRIMARY, bold=True).pack(side='left')
        self._refresh_project_label()

        body = tk.Frame(p, bg=CANVAS_BG)
        body.pack(fill='both', expand=True, padx=40, pady=(0, 20))

        self._build_dxf_card(body)
        self._build_result_card(body)
        self._build_detail_card(body)

    # ── 카드 1: 평면도 로드 + AI 인식 ────────────────────
    def _build_dxf_card(self, parent):
        outer, card = self._make_card(parent, padx=24, pady=18)
        outer.pack(fill='x', pady=(0, 14))

        tk.Label(card, text='1. 지하주차장 전체평면도 승강로 탐지', font=FH, bg=SURFACE, fg=TEXT_PRIMARY).pack(anchor='w')

        btn_row = tk.Frame(card, bg=SURFACE)
        btn_row.pack(fill='x', pady=(12, 0))
        self.btn_load_dxf = self._metal_btn(btn_row, '📂 도면 불러오기', self._run_parking_load_dxf,
                                             text_color=BTN_METAL_TEXT_PRIMARY, bold=True)
        self.btn_load_dxf.pack(side='left')
        self.btn_analyze = self._metal_btn(btn_row, '승강로 분석하기', lambda: self._run_parking_analyze(),
                                            text_color=BTN_METAL_TEXT_PRIMARY, bold=True)
        self.btn_analyze.pack(side='left', padx=(10, 0))
        self.btn_analyze.config(state='disabled')
        self.var_elev_kw = tk.StringVar(value=self._load_elev_pref('elev_layer'))
        self.var_wall_kw = tk.StringVar(value=self._load_elev_pref('wall_layer'))

        self.lbl_dxf_file = tk.Label(card, text='불러온 도면: 없음', font=FS, bg=SURFACE, fg=TEXT_MUTED,
                                      anchor='w', justify='left')
        self.lbl_dxf_file.pack(anchor='w', pady=(8, 0))

        # 진행률 표시줄 — % 만 표시
        style = ttk.Style()
        style.configure('Elevator.Horizontal.TProgressbar',
                         troughcolor=SURFACE_HI, background=ACCENT_PRIMARY,
                         bordercolor=SURFACE_HI, lightcolor=ACCENT_PRIMARY, darkcolor=ACCENT_PRIMARY)
        progress_row = tk.Frame(card, bg=SURFACE)
        progress_row.pack(fill='x', pady=(10, 0))
        self.progress_bar = ttk.Progressbar(progress_row, style='Elevator.Horizontal.TProgressbar',
                                             orient='horizontal', mode='determinate', length=300, maximum=100)
        self.progress_bar.pack(side='left')
        self.lbl_progress_pct = tk.Label(progress_row, text='', font=(_F_KOR, 9, 'bold'),
                                          bg=SURFACE, fg=ACCENT_PRIMARY, width=6, anchor='w')
        self.lbl_progress_pct.pack(side='left', padx=(8, 0))

        # 상태 문구 라벨은 화면에 표시하지 않는다(내부 호출 호환용으로만 둔다)
        self.lbl_status = tk.Label(card, text='')

        parking_tree_frame = tk.Frame(card, bg=SURFACE)
        parking_tree_frame.pack(fill='x', pady=(12, 4))

        self.tree_parking = ttk.Treeview(
            parking_tree_frame, columns=('idx', 'w', 'd', 'cap'), show='headings',
            style=STYLE_TREEVIEW, height=6)
        for c, t, w in [('idx', '코어', 120), ('w', '폭', 80), ('d', '깊이', 80), ('cap', '인승', 60)]:
            self.tree_parking.heading(c, text=t)
            self.tree_parking.column(c, width=w, anchor='center')

        parking_vsb = ttk.Scrollbar(parking_tree_frame, orient='vertical', command=self.tree_parking.yview)
        self.tree_parking.configure(yscrollcommand=parking_vsb.set)
        self.tree_parking.grid(row=0, column=0, sticky='nsew')
        parking_vsb.grid(row=0, column=1, sticky='ns')
        parking_tree_frame.grid_columnconfigure(0, weight=1)


    def _clear_parking_tree(self):
        if hasattr(self, 'tree_parking'):
            self.tree_parking.delete(*self.tree_parking.get_children())

    def _refresh_parking_tree(self):
        self._clear_parking_tree()
        for r in self.parking_shaft_results:
            self.tree_parking.insert('', 'end', iid=str(r['index']), values=(
                r.get('core_label', r['index']), f"{r['width_mm']:.0f}", f"{r['depth_mm']:.0f}",
                r.get('capacity_guess') or '',
            ))

    # ── 카드 2: 결과 테이블 (확정 승강로 전체 검토표) ──────────────
    def _build_result_card(self, parent):
        outer, card = self._make_card(parent, padx=24, pady=18)
        outer.pack(fill='both', expand=True, pady=(0, 14))

        header_row = tk.Frame(card, bg=SURFACE)
        header_row.pack(fill='x')
        tk.Label(header_row, text='2. 확정 승강로 전체 검토표', font=FH, bg=SURFACE, fg=TEXT_PRIMARY).pack(side='left')
        self._metal_btn(header_row, '선택 행 삭제', self._delete_selected_row).pack(side='right')
        self._metal_btn(header_row, '📊 Excel로 내보내기', self._export_excel,
                         text_color=BTN_METAL_TEXT_PRIMARY, bold=True).pack(side='right', padx=(0, 8))

        tk.Label(card, text='행을 클릭하면 아래 "3. 선택한 행 상세"에 근거(도면 파싱 출처)가 표시되고, 셀을 더블클릭하면 값을 직접 수정할 수 있습니다.',
                 font=FS, bg=SURFACE, fg=TEXT_MUTED).pack(anchor='w', pady=(4, 8))

        cols = ['no', 'dong', 'core', 'cap', 'layout', 'act_w', 'act_d']
        headers = {
            'no': 'No', 'dong': '동', 'core': '코어',
            'cap': '인승', 'layout': '배치',
            'act_w': '실측W', 'act_d': '실측D',
        }

        tree_frame = tk.Frame(card, bg=SURFACE)
        tree_frame.pack(fill='both', expand=True, pady=(0, 0))

        self.tree = ttk.Treeview(tree_frame, columns=cols, show='headings',
                                  style=STYLE_TREEVIEW, height=10)
        for c in cols:
            self.tree.heading(c, text=headers[c])
            self.tree.column(c, width=70, anchor='center')
        self.tree.column('dong', width=48)
        self.tree.column('core', width=48)

        vsb = ttk.Scrollbar(tree_frame, orient='vertical', command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        self.tree.bind('<<TreeviewSelect>>', self._on_tree_select)
        self.tree.bind('<Double-1>', self._on_tree_double_click)
        self._cell_editor = None
        self._cell_edit_target = None

    # ── 카드 3: 선택한 행 상세 ───────────────────────────────────
    def _build_detail_card(self, parent):
        outer, card = self._make_card(parent, padx=24, pady=16)
        outer.pack(fill='both', expand=False)

        tk.Label(card, text='3. 선택한 행 상세', font=FH, bg=SURFACE, fg=TEXT_PRIMARY).pack(anchor='w', pady=(0, 8))
        self.txt_detail = tk.Text(card, height=10, font=(_F_MONO, 9),
                                   bg=SURFACE_HI, fg=TEXT_PRIMARY, relief='flat', wrap='word')
        self.txt_detail.pack(fill='both', expand=True)
        self.txt_detail.insert('1.0', '(표에서 행을 선택하면 상세 내용이 여기에 표시됩니다)')
        self.txt_detail.config(state='disabled')

    # ══════════════════════════════════════════════════════════
    #  지하주차장 전체평면도 승강로 탐지+스냅
    # ══════════════════════════════════════════════════════════
    # ── ① 승강기 심볼 위치 탐지 (로컬 전용 — AI 호출 없음, 빠르고 정확) ──
    def _reset_parking_state(self):
        self.parking_clusters = []
        self.parking_wall_layers = []
        self.parking_shaft_results = []
        self._clear_parking_tree()
        self._reset_progress(0)

    def _run_parking_load_dxf(self):
        """도면(DXF)을 불러온다."""
        path = filedialog.askopenfilename(
            title="도면 선택",
            filetypes=[('DXF Files', '*.dxf'), ('All Files', '*.*')]
        )
        if not path:
            return False
        self.parking_source_path = path
        self._reset_parking_state()
        try:
            size_txt = f' ({os.path.getsize(path) / (1024 * 1024):.1f}MB)'
        except Exception:
            size_txt = ''
        self.lbl_dxf_file.config(text=f'불러온 도면: {os.path.basename(path)}{size_txt}', fg=TEXT_PRIMARY)
        self.btn_analyze.config(state='normal')
        return True

    def _set_pct(self, pct):
        """진행률(%)만 표시한다. 어느 스레드에서 불러도 안전하다."""
        pct = max(0, min(100, int(pct)))
        self.parent.after(0, lambda: self._apply_pct(pct))

    def _apply_pct(self, pct):
        self.progress_bar['maximum'] = 100
        self.progress_bar['value'] = pct
        self.lbl_progress_pct.config(text=f'{pct}%')

    def _run_parking_analyze(self, retry=False):
        """도면 분석 전체(승강기 탐지 → 벽체 판별 → 치수 산출)를 한 번에 실행한다."""
        path = self.parking_source_path
        if not path:
            if not self._run_parking_load_dxf():
                return
            path = self.parking_source_path
        if not retry:
            self._wall_prompted = False
        self._ukw_cache = self._user_layer_keywords()
        self._wkw_cache = self._user_layer_keywords(wall=True)
        self._save_elev_pref()
        self._reset_parking_state()
        for b in (self.btn_load_dxf, self.btn_analyze):
            b.config(state='disabled')
        threading.Thread(target=self._parking_analyze_thread, args=(path,), daemon=True).start()

    def _parking_analyze_thread(self, path):
        clusters, wall_layers, results, failed = [], [], [], False
        try:
            self._set_pct(2)
            ukw = list(self._ukw_cache)
            clusters = []
            if ukw:     # 사용자가 지정한 레이어명 단어가 있으면 그 단어가 들어간 레이어만 먼저 본다
                clusters = _find_shaft_points_by_layer(path, layer_keywords=ukw, use_auto=False)
            if not clusters:
                clusters = _find_shaft_points_by_layer(path, layer_keywords=SHAFT_LAYER_KEYWORDS)
            self._set_pct(15)
            if clusters:
                wall_layers, _summary, _info = self._analyze_walls(path, clusters)
                self._set_pct(55)
                results = self._snap_parking_clusters_to_walls(
                    path, clusters, wall_layers,
                    progress_cb=lambda i, t: self._set_pct(55 + 45.0 * i / max(t, 1)))
        except Exception:
            traceback.print_exc()
            failed = True
        self.parent.after(0, lambda: self._on_parking_analyze_done(clusters, wall_layers, results, failed))

    def _on_parking_analyze_done(self, clusters, wall_layers, results, failed):
        self.btn_load_dxf.config(state='normal')
        self.btn_analyze.config(state='normal')
        self.parking_clusters = clusters
        self.parking_wall_layers = wall_layers
        self.parking_shaft_results = results
        self._refresh_parking_tree()
        n_miss = sum(1 for r in results if r.get('source') == 'symbol_bbox')
        if not results or n_miss:
            # 승강기를 못 찾았거나(레이어명 입력), 벽을 못 찾은 승강로가 하나라도 있으면(골조 레이어명 입력)
            # 여기서 멈추고 한 번 물어본다. 취소하거나 이미 한 번 물었으면 그대로 진행한다.
            if failed:
                self._reset_progress(0)
                messagebox.showwarning('안내', '분석 중 오류가 발생했습니다.')
                return
            if not results or not self._wall_prompted:
                self._reset_progress(0)
                got = self._ask_layer_names(need_elev=not results, n_miss=n_miss)
                if got is not None:
                    self._wall_prompted = bool(results)
                    self.var_elev_kw.set(got[0])
                    self.var_wall_kw.set(got[1])
                    self._save_elev_pref()
                    self.parent.after(50, lambda: self._run_parking_analyze(retry=True))
                    return
            if not results:
                return
        self._apply_pct(100)
        # 분석 결과를 도면에 표기하고 검토표에 반영하는 것까지 한 번에 처리한다.
        marked = self._run_parking_step4_export_layer(quiet=True)
        applied = self._run_parking_step5_apply_to_table(quiet=True)
        if applied is None:
            return
        msg = f'분석 완료: 승강로 {applied[0]}건을 검토표에 반영했습니다.'
        if applied[1]:
            msg += f'\n벽체 미검출 {applied[1]}건은 도면에서 직접 확인하세요.'
        msg += ('\n도면 표기본을 원본 옆에 저장했습니다.' if marked else '\n도면 표기본 저장에는 실패했습니다.')
        messagebox.showinfo('완료', msg)

    def _analyze_walls(self, path, clusters):
        wall_layers = []
        info = {'scores': [], 'error': None}
        try:
            self._set_pct(25)
            # 이름에 벽/구조체 단서가 있는 레이어도 후보 풀에 넣고,
            # 승강로 심볼을 실제로 둘러싸는 정도(기하 점수)로 최종 순위를 정한다 — 이름이 아니라 도면 기하가 구조체 레이어를 고른다. 점수 높은 상위 레이어들은 하나의
            #     벽선 집합으로 합쳐 '가장 가까운 선'을 쓴다(구조체 면이 기준선보다 승강로에 가깝다).
            import ezdxf
            msp = ezdxf.readfile(path).modelspace()
            stats = _collect_layer_line_stats(path)
            self._set_pct(32)
            name_cands = list(dict.fromkeys(_structural_layer_names(stats)))
            # 이름에 단서가 없는 벽 레이어('w', 'c' 같은 짧은 이름 — 실측)도 놓치지 않도록, 선이 충분히 있는
            # 모든 레이어를 후보 풀에 넣어 기하 점수로만 가린다(중심선/치수/승강기 자체 레이어는 이름으로 제외).
            # (XREF 접두 '지하1층주차장$0$'에도 '주차'가 들어 있으므로 이름 검사는 마지막 부분만 한다)
            geo_cands = [st['layer'] for st in stats
                         if st['count'] >= 8 and not any(t.upper() in st['layer'].split('$')[-1].upper() for t in
                                                          ('CEN', 'GRID', '중심선', '그리드', 'DIM', '치수', 'TEXT', '주차',
                                                           'DEFPOINT', 'ELEV', '승강'))
                         and not _has_elev_token(st['layer'])
                         and not any(k.upper() in st['layer'].split('$')[-1].upper() for k in self._ukw_cache)]
            ranked = []
            ucands = []
            auto_names = list(name_cands)
            wkw = list(self._wkw_cache)
            if wkw:     # 사용자가 지정한 골조 레이어명 단어가 들어간 레이어를 먼저 본다(없거나 못 감싸면 자동 판별)
                ucands = [st['layer'] for st in stats
                          if any(k.upper() in st['layer'].split('$')[-1].upper() for k in wkw)]
                if ucands:
                    ranked = _rank_wall_layers(msp, clusters, ucands, keep_hug=set(ucands))
            if ranked:
                name_cands = list(ucands)
            else:
                cands = list(dict.fromkeys(name_cands + geo_cands))
                ranked = _rank_wall_layers(msp, clusters, cands)
            self._set_pct(45)
            info['scores'] = [(l, sc) for l, sc, _cv in ranked]
            if ranked:
                # 한 도면에서 승강로 골조 레이어는 하나다(동/블록과 무관). 승강로 둘레 인식 점수 1위
                # 레이어 하나만 쓴다 — 여러 레이어를 합치거나 돌아가며 시도하면 기준선 같은 엉뚱한
                # 선에 스냅될 수 있다. 화면에서 동결/꺼진 레이어는 후보에서 이미 제외돼 있다.
                # 점수가 1 이상인(승강로 둘레를 실제로 감싼 적 있는) 레이어를 모두 후보로 두고, 우선순위
                # (골조/CON → 벽체 → 그 밖)와 점수 순으로 정렬한다. ③은 승강로마다 이 순서로 시도해 그 승강로를
                # 실제로 감싸는 첫 레이어를 쓴다(레이어를 합치지 않는다).
                top_sc = ranked[0][1]
                name_set = set(name_cands)
                # 이름 단서가 있는 후보는 점수 1 이상이면, 이름 단서가 없는 후보(짧은 이름 등)는 1위 점수의
                # 30% 이상일 때만 채택해 우연히 승강로 근처를 지나는 잡선 레이어를 걸러낸다.
                cand_layers = [(l, sc) for l, sc, _cv in ranked
                               if sc >= 1 and (l in name_set or sc >= 0.3 * top_sc)][:6]
                cand_layers.sort(key=lambda t: (_layer_priority(t[0]), -t[1]))
                wall_layers = [l for l, _sc in cand_layers]
                if ucands and any(l in ucands for l in wall_layers):
                    # 지정한 레이어가 못 감싸는 승강로는 자동 판별 레이어로도 시도하도록 뒤에 덧붙인다.
                    r2 = _rank_wall_layers(msp, clusters, list(dict.fromkeys(auto_names + geo_cands)))
                    if r2:
                        t2 = r2[0][1]
                        extra = [(l, sc) for l, sc, _cv in r2
                                 if sc >= 1 and l not in wall_layers and (l in auto_names or sc >= 0.3 * t2)][:6]
                        extra.sort(key=lambda t: (_layer_priority(t[0]), -t[1]))
                        wall_layers += [l for l, _sc in extra]
                info['covered'] = len(set().union(*[cv for _l, _sc, cv in ranked if _l in wall_layers])) if wall_layers else 0
        except Exception as e:
            traceback.print_exc()
            info['error'] = f'벽 레이어 순위 산정 실패: {e}'
        summary = None
        if wall_layers and clusters:
            try:
                summary = self._classify_parking_layouts(path, clusters, wall_layers)
            except Exception as e:
                traceback.print_exc()
                info['error'] = f"승강로 구성(단독/병렬) 판별 실패: {e}"
        return wall_layers, summary, info

    def _classify_parking_layouts(self, dxf_path, clusters, wall_layers):
        """②의 두 번째 일: ①이 거리 규칙으로 묶어둔 후보 그룹마다 "단독인지, 병렬이면 몇 대인지"
        (= 골조로 둘러싸인 승강로 bay 구성)를 AI가 기하(심볼 윤곽+골조선, 도면 문자 미사용)만 보고
        판별한다. AI 결과가 없거나 도면 기하와 안 맞으면 골조 포함관계 규칙으로 대체한다.
        결과로 클러스터의 group_id를 bay 단위로 다시 매기고 bay_checked=True를 남긴다 —
        ③/⑤가 이 구성(단독/병렬, 대수)을 그대로 따른다. 반환: 요약 dict."""
        import ezdxf
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
        def _segs_for(layer):
            return _cached_wall_segs(msp, dxf_path, layer)

        by_group = {}
        for i, c in enumerate(clusters):
            by_group.setdefault(c.get('group_id', i), []).append(i)

        uid = {}
        n_ai = n_rule = 0
        for gid, idxs in by_group.items():
            members = [clusters[i] for i in idxs]
            bay_of = None
            if len(idxs) >= 2:
                first = None
                for wl in wall_layers:
                    segs = _segs_for(wl)
                    if not segs:
                        continue
                    frame = _group_local_frame(segs, members)
                    if not frame:
                        continue
                    cand = _bays_by_containment(frame)
                    used_ai = False
                    ok = len(_snap_parallel_bays(segs, members, bay_of=cand)) == len(members)
                    if first is None:
                        first = (cand, used_ai)
                    if ok:               # 그룹 전체가 실제로 감싸지는 첫 레이어의 판정을 쓴다
                        first = (cand, used_ai)
                        break
                if first is not None:
                    bay_of = first[0]
                    if first[1]:
                        n_ai += 1
                    else:
                        n_rule += 1
            else:
                bay_of = [0]
            checked = bay_of is not None
            if bay_of is None:
                bay_of = [0] * len(idxs)
            for k, i in enumerate(idxs):
                key = (gid, bay_of[k])
                if key not in uid:
                    uid[key] = len(uid)
                # group_id(나란히 붙은 승강기 묶음 = 코어/단독·병렬 표시 기준)는 그대로 두고,
                # 치수 산출용 bay(골조로 둘러싸인 공간) 구성만 bay_id로 따로 기록한다.
                clusters[i]['bay_id'] = uid[key]
                clusters[i]['bay_checked'] = checked
        layout_sizes = Counter(len(v) for v in by_group.values())
        bay_members = Counter(c['bay_id'] for c in clusters)
        bay_sizes = Counter(n for n in bay_members.values() if n >= 2)
        return {'layout_sizes': dict(layout_sizes), 'bay_sizes': dict(bay_sizes),
                'n_ai': n_ai, 'n_rule': n_rule}

    def _snap_parking_clusters_to_walls(self, dxf_path, clusters, wall_layers, progress_cb=None,
                                         status_cb=None):
        """①에서 찾은 심볼 위치(clusters)마다, ②에서 판별한 벽체 레이어(wall_layers)의
        벽선에 스냅을 시도한다. 벽 스냅이 실패하면(벽 레이어를 못 찾았거나 주변에
        벽선이 없음) 심볼 자체의 윤곽 bbox를 그대로 쓰고 source를 'symbol_bbox'로
        낮춰 표시한다. 인승은 중심점 근처(4m 이내)에 "N인승" 텍스트가 있으면
        반영하고, 없으면 공란(None)으로 둔다(AI가 임의로 추정하지 않음).

        반환: [{'index':int, 'center':(x,y), 'points':[(x,y)x4],
                'width_mm':float, 'depth_mm':float, 'capacity_guess':int|None,
                'wall_layer_used':str|None, 'source':'wall_snap'|'symbol_bbox',
                'group_id':int}]
        (group_id가 같은 항목들은 ①단계 클러스터링에서 "병렬(나란히 붙은)
        승강로"로 묻인 같은 코어 — ⑤단계에서 이 값으로 코어/배치(단독·병렬)를
        정한다.)"""
        # [성능] 승강로 후보가 수십 개일 수 있는 전체평면도에서, 후보마다 파일을
        # 다시 읽고 중첩 블록(XREF)을 다시 순회하면 수십 초가 누적된다(실측:
        # 28MB급 도면 1회 읽기+순회에 약 10초). 파일은 한 번만 읽고, 벽 레이어별
        # 벽선 후보(segs)도 실제 쓰인 레이어만 한 번씩만 모아 재사용한다.
        segs_cache = {}
        try:
            import ezdxf
            doc = ezdxf.readfile(dxf_path)
            msp = doc.modelspace()
        except Exception:
            msp = None

        def _segs_for(layer_hint):
            return _cached_wall_segs(msp, dxf_path, layer_hint)

        capacity_points = _collect_capacity_text_points(msp) if msp else []
        # 승강로끼리 가까이 붙어 있을 때(병렬/나란한 코어) 후보마다 독립적으로
        # "내 반경 안 가장 가까운 텍스트"를 찾으면 옆 승강로 텍스트를 잘못
        # 가져가는 오배정이 생길 수 있어, 전역 1:1 배타 매칭으로 한 번에
        # 배정한다 (자세한 이유는 _assign_capacities 설명 참조).
        capacity_by_index = _assign_capacities([c['center'] for c in clusters], capacity_points)

        # 승강로끼리 가까이 붙어 있을 때(병렬/나란한 코어, 실측 간격 600~900mm)
        # 벽 검색창을 무작정 넓히면 바로 옆 승강로의 벽(또는 그 너머 외벽)을
        # 잘못 집어 깊이가 너무 짧게/길게 나오는 사례가 확인됨 — 승강로마다
        # "가장 가까운 다른 승강로 중심까지 거리의 절반"을 검색 반경 상한으로
        # 둬서, 구조적으로 옆 승강로 영역을 넘어 검색하지 못하게 한다.
        centers = [c['center'] for c in clusters]

        def _nearest_neighbor_dist(idx):
            cx, cy = centers[idx]
            best = None
            for j, (ox, oy) in enumerate(centers):
                if j == idx:
                    continue
                d = math.hypot(ox - cx, oy - cy)
                if best is None or d < best:
                    best = d
            return best

        results = []
        total = len(clusters)
        for i, c in enumerate(clusters, start=1):
            if progress_cb:
                try:
                    progress_cb(i, total)
                except Exception:
                    pass
            bbox = c['bbox']
            nn_dist = _nearest_neighbor_dist(i - 1)
            max_reach_mm = max(nn_dist / 2.0, 500.0) if nn_dist is not None else None
            snapped = None
            used_layer = None
            for wl in wall_layers:
                segs = _segs_for(wl)
                if not segs:
                    continue
                # strict_overlap_ratio(커버리지 기반 판정)를 우선 시도한다 — 기존
                # 느슨한 판정(_pick(use_overlap=True), 한 변이 guess 폭/높이의 15%만
                # 겹쳐도 통과)은 "중심점에 가장 가까운 변"을 고르다 보니, 승강로
                # 진짜 벽(문 개구부 때문에 짧은 조각들로 쪼개져 있지만 합치면 폭 전체를
                # 거의 다 덮음)보다, 전혀 무관한 옆 공간의 짧은 모서리 조각(폭의
                # 10~15%만 우연히 걸침)을 잘못 고르는 사례가 실제 도면으로 확인됨
                # (그 짧은 조각이 중심에 더 가까웠을 뿐). strict 모드는 "변 하나의
                # 커버리지 비율"로 판정해 이런 짧은 무관 조각을 배제하므로 먼저
                # 시도하고, 그래도 못 찾으면(벽이 정말 듬성듬성한 경우) 기존 느슨한
                # 판정으로 자동 폴백한다(_snap_bbox_to_wall_segs 내부에서 처리).
                snapped = _snap_bbox_to_wall_segs_any_angle(segs, bbox, layer_hint=wl,
                                                             max_reach_mm=max_reach_mm,
                                                             strict_overlap_ratio=0.5)
                if snapped:
                    used_layer = wl
                    break
            if snapped:
                points = snapped['points']
                width_mm, depth_mm = snapped['width_mm'], snapped['height_mm']
                source = 'wall_snap'
            else:
                # 벽 스냅 실패 시 심볼 윤곽으로 낮춰 표시 — 이 구역이 회전돼
                # 있으면(rotated_rect) 그 회전을 반영한 사각형을 쓴다. 그냥
                # 월드축 bbox(x0,y0)-(x1,y1)만 쓰면 회전된 구역에서 실제 벽
                # 방향과 다르게 항상 수평으로 그려지고, 축정렬 bbox 자체가
                # 회전된 사각형보다 부풀려져 있어 크기도 틀어진다.
                rot = c.get('rotated_rect')
                if rot:
                    points = rot['points']
                    width_mm, depth_mm = rot['width_mm'], rot['height_mm']
                else:
                    x0, y0, x1, y1 = bbox
                    points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
                    width_mm, depth_mm = abs(x1 - x0), abs(y1 - y0)
                source = 'symbol_bbox'
            capacity = capacity_by_index.get(i - 1)
            results.append({
                'index': i, 'center': c['center'], 'points': points,
                'width_mm': round(width_mm, 1), 'depth_mm': round(depth_mm, 1),
                'capacity_guess': capacity, 'wall_layer_used': used_layer, 'source': source,
                'primary_layer': wall_layers[0] if wall_layers else None,
                'group_id': c.get('group_id', i - 1),
            })

        # ── 병렬 그룹: 골조선만으로 N대 승강로 치수 따기 (텍스트·AI 미사용) ──
        # 구성(단독/병렬, 대수)은 ②에서 이미 정해졌다. 그룹마다 bay 외곽 골조 사각형을 N대로 나눠
        # 일반 스냅 결과보다 우선 적용한다. 벽 레이어는 앞쪽 후보 몇 개만 시도한다(레이어마다
        # 도면 순회에 수십 초가 들어 전부 돌리면 멈춘 것처럼 보임). 이 후처리가 어떤 이유로
        # 실패해도 위에서 구한 일반 스냅 결과는 그대로 남긴다.
        try:
            if msp is not None:
                by_group = {}
                for pos, r in enumerate(results):
                    by_group.setdefault(r['group_id'], []).append(pos)
                multi = [(gid, ps) for gid, ps in by_group.items() if len(ps) >= 2]
                for gi, (gid, positions) in enumerate(multi, start=1):
                    if status_cb:
                        try:
                            status_cb(f'③ 병렬 승강로 {gi}/{len(multi)}번째 그룹 골조선으로 치수 산출 중...')
                        except Exception:
                            pass
                    members = [clusters[results[p]['index'] - 1] for p in positions]
                    # ②에서 bay 구성(단독/병렬·대수)이 판정돼 있으면 그대로 따르고, 아니면 골조 포함관계로 직접 판정
                    bay_of = [m['bay_id'] for m in members] if all(m.get('bay_checked') for m in members) else None
                    for wl in wall_layers[:3]:
                        fixed = _snap_parallel_bays(_segs_for(wl), members, bay_of=bay_of)
                        if len(fixed) != len(positions):
                            continue        # 그룹 전체를 못 구하면 일부만 쓰지 않는다
                        for k, p in enumerate(positions):
                            f = fixed[k]
                            results[p].update({
                                'points': f['points'], 'width_mm': f['width_mm'], 'depth_mm': f['depth_mm'],
                                'wall_layer_used': wl,
                                'source': 'bay_shared' if f['shared'] else 'bay_snap',
                                'bay_info': (f['bay_n'], f['bay_total'][0], f['bay_total'][1]),
                                'open_side': f.get('open_side'),
                            })
                        break
                # 일반 스냅(4면 필요)이 실패한 단독 승강로 — 문 쪽 한 변이 비어 있는 경우를 위해
                # bay 방식(3면 + 한 변 추정)으로 한 번 더 시도한다.
                for r in results:
                    if r['source'] != 'symbol_bbox':
                        continue
                    for wl in wall_layers[:3]:
                        f = _snap_parallel_bays(_segs_for(wl), [clusters[r['index'] - 1]], bay_of=[0]).get(0)
                        if not f:
                            continue
                        r.update({'points': f['points'], 'width_mm': f['width_mm'], 'depth_mm': f['depth_mm'],
                                  'wall_layer_used': wl, 'source': 'bay_snap', 'open_side': f.get('open_side')})
                        break
        except Exception:
            traceback.print_exc()

        # 미리보기·④·⑤가 같은 코어 번호를 쓰도록 라벨을 한 번에 부여한다.
        for idx, (label, parallel) in _assign_core_labels(results).items():
            results[idx - 1]['core_label'] = label
            results[idx - 1]['is_parallel'] = parallel
        return results

    # ── ④ '승강로 사이즈' 레이어로 표시 (사본 DXF 저장) ──
    def _run_parking_step4_export_layer(self, quiet=False):
        if not self.parking_shaft_results:
            return False
        shapes = []
        merged = {}      # 같은 폴리선(공용 승강로) → shapes 항목
        for r in self.parking_shaft_results:
            cap_part = f" {r['capacity_guess']}인승" if r.get('capacity_guess') else ''
            core = r.get('core_label', '#' + str(r['index']))
            key = tuple((round(x), round(y)) for x, y in r['points']) if r.get('source') == 'bay_shared' else None
            if key is not None and key in merged:
                it = merged[key]
                it['cores'].append(core + cap_part)
                open_tag = ' ⚠문쪽 벽선 없음→추정' if r.get('open_side') else ''
                it['label'] = (f"{'·'.join(it['cores'])} 공용승강로{r['bay_info'][0]}대(하나의 승강로) "
                               f"{r['width_mm']:.0f}x{r['depth_mm']:.0f}{open_tag}")
                continue
            label = f"{core} {r['width_mm']:.0f}x{r['depth_mm']:.0f}{cap_part}{_result_tag(r)}"
            item = {'points': r['points'], 'label': label, 'layer': '승강로 사이즈', 'cores': [core + cap_part]}
            shapes.append(item)
            if key is not None:
                merged[key] = item
        try:
            out_path = write_shaft_layer_dxf(self.parking_source_path, shapes,
                                              layer_name='승강로 사이즈', suffix='_승강로검토')
        except Exception:
            traceback.print_exc()
            out_path = None
        if quiet:
            return bool(out_path)
        if out_path:
            msg = f'✅ "승강로 사이즈" 레이어로 표시한 사본을 원본 옆에 저장했습니다: {os.path.basename(out_path)}'
            self.lbl_status.config(text=msg, fg=ACCENT_SUCCESS)
        else:
            msg = '❌ 주황 폴리선 사본 저장에 실패했습니다.'
            self.lbl_status.config(text=msg, fg=ACCENT_DANGER)
        messagebox.showinfo('완료' if out_path else '오류', msg)

    # ── ⑤ 확정 승강로 전체 검토표에 일괄 반영 ──
    def _run_parking_step5_apply_to_table(self, quiet=False):
        if not self.parking_shaft_results:
            return None
        # ①단계에서 크기 상한 때문에 하나로 못 묶었지만 바로 옆에 붙어있던
        # 승강로들(group_id가 같음)은 "같은 코어에 나란히 붙은 승강로"로 보고
        # 같은 코어 번호 + 배치="병렬"로, 혼자인 승강로는 배치="단독"으로
        # 검토표에 반영한다(이전에는 전부 "단독"으로 잘못 표시됐었음).
        # 검토표는 누적하지 않는다 — 현재 도면의 결과만 담는다(여러 도면을 돌려도 이전 도면 행이 섞이지 않음).
        # 사용자가 표에서 직접 입력한 '동' 구분이 있으면 지우기 전에 한 번 확인한다.
        if self.rows:
            if any(r.get('dong') for r in self.rows) and not messagebox.askyesno(
                    '검토표 교체',
                    f'검토표에 있는 {len(self.rows)}행(직접 입력한 동 구분 포함)을 지우고\n'
                    '현재 도면의 결과로 교체합니다. 계속할까요?'):
                return None
            self.rows = []
            self._row_seq = 0
            for i in self.tree.get_children():
                self.tree.delete(i)
        n_added = 0
        n_low_conf = 0
        # 코어 번호/A·B·C는 ③에서 _assign_core_labels로 이미 부여돼 있다 — ④ 도면
        # 라벨, ③ 미리보기와 같은 값을 그대로 써서 번호가 어긋나지 않는다.
        # 동 구분은 전체평면도 한 장만으로는 알 수 없어 비워둔다(직접 입력).
        for r in sorted(self.parking_shaft_results, key=lambda r: r['index']):
            layout = self.LAYOUT_OPTIONS[1] if r.get('is_parallel') else self.LAYOUT_OPTIONS[0]  # '병렬' / '단독'
            # 자동 크기를 그대로 믿으면 안 되는 행(벽미검출/AI 보정/공용승강로)은
            # 코어 라벨에 표시를 남겨 검토표/엑셀/세션에서도 보이게 한다.
            core_label = r.get('core_label', f"코어{r['index']}") + _result_tag(r)
            if r.get('source') == 'symbol_bbox':
                n_low_conf += 1
            self._add_detection_row(
                core=core_label, cap=r.get('capacity_guess'), layout=layout,
                w=r.get('width_mm'), d=r.get('depth_mm'))
            n_added += 1
        msg = f'✅ 확정 승강로 전체 검토표를 현재 도면 결과 {n_added}건으로 교체했습니다(동 구분은 직접 입력하세요).'
        if n_low_conf:
            msg += f' ⚠ {n_low_conf}건은 벽체 미검출(심볼 윤곽 크기)로 표시됨 — 도면에서 직접 확인 필요.'
        self.lbl_status.config(text=msg, fg=ACCENT_SUCCESS if not n_low_conf else ACCENT_WARNING)
        if quiet:
            return n_added, n_low_conf
        messagebox.showinfo('완료', msg)
        return n_added, n_low_conf

    # ── 진행률 표시줄 ──────────────────────────────────
    # ── 진행률 표시줄 ──────────────────────────────────
    def _reset_progress(self, total=0):
        self.progress_bar['maximum'] = 100
        self.progress_bar['value'] = 0
        self.lbl_progress_pct.config(text='')

    def _parse_float(self, s):
        try:
            s = (s or '').strip().replace(',', '')
            return float(s) if s else None
        except ValueError:
            return None

    def _add_detection_row(self, dong='', core='', cap=None, layout=None, w=None, d=None):
        """지하주차장 승강로 탐지 결과(또는 저장된 세션 행) 하나를 "확정 승강로
        전체 검토표"에 행으로 추가한다. 예전에는 "2. 실측값 입력" 폼의 입력칸
        값을 읽어 행을 만들었지만, 이제는 탐지 결과(또는 세션 파일)에서 이미
        확정된 값을 그대로 받아 바로 행으로 쌓는다 — 동 구분은 전체평면도
        한 장만으로는 알 수 없어 비워두고, 사용자가 표에서 셀을 더블클릭해
        직접 채운다."""
        self._row_seq += 1
        row_data = {
            'no': self._row_seq,
            'dong': dong or '',
            'core': core or '',
            'cap': int(cap) if cap not in (None, '') else None,
            'layout': layout or self.LAYOUT_OPTIONS[0],
            'actual': {'w': w, 'd': d},
        }
        self.rows.append(row_data)
        self._insert_tree_row(row_data)
        return row_data

    def _fmt_val(self, v):
        return '' if v is None else v

    def _row_values(self, row_data):
        actual = row_data['actual']
        return [
            row_data['no'], row_data['dong'], row_data['core'],
            (row_data['cap'] if row_data['cap'] is not None else ''), row_data['layout'],
            self._fmt_val(actual['w']), self._fmt_val(actual['d']),
        ]

    def _insert_tree_row(self, row_data):
        self.tree.insert('', 'end', iid=str(row_data['no']), values=tuple(self._row_values(row_data)))

    # ── 표 셀 인라인 편집 ────────────────────────────────────────
    # [기능 추가] 예전에는 "3. 확정 승강로 전체 검토표"의 각 행은 더블클릭해도
    # 위쪽 "3. 선택한 행 상세"에 근거(출처)만 보여줄 뿐, 값 자체를 표에서 직접
    # 고칠 방법이 없었다 — AI가 잘못 읽은 값 하나를 고치려 해도 실측값 입력
    # 폼으로 다시 돌아가 새 행을 추가하는 수밖에 없었다. ttk.Treeview는 셀
    # 편집을 기본 지원하지 않으므로, 더블클릭한 셀 위치에 Entry(또는 '구분'/
    # '배치' 컬럼은 Combobox)를 겹쳐 띄워 그 자리에서 값을 고치고 Enter/포커스
    # 아웃 시 self.rows와 화면 표시에 반영하는 방식으로 구현한다.
    _EDITABLE_COLUMNS = {'dong', 'core', 'cap', 'layout', 'act_w', 'act_d'}
    _ACTUAL_COLUMN_KEYS = {'act_w': 'w', 'act_d': 'd'}

    def _on_tree_double_click(self, event):
        region = self.tree.identify('region', event.x, event.y)
        if region != 'cell':
            return
        row_iid = self.tree.identify_row(event.y)
        col_id = self.tree.identify_column(event.x)  # '#1', '#2', ...
        if not row_iid or not col_id:
            return
        try:
            col_index = int(col_id.replace('#', '')) - 1
        except ValueError:
            return
        cols = self.tree['columns']
        if col_index < 0 or col_index >= len(cols):
            return
        col_name = cols[col_index]
        if col_name not in self._EDITABLE_COLUMNS:
            return
        self._begin_cell_edit(row_iid, col_name, col_id)

    def _begin_cell_edit(self, row_iid, col_name, col_id):
        self._end_cell_edit(cancel=True)  # 편집 중이던 다른 셀이 있으면 먼저 정리
        bbox = self.tree.bbox(row_iid, col_id)
        if not bbox:
            return
        x, y, w, h = bbox
        current = self.tree.set(row_iid, col_name)

        if col_name == 'layout':
            editor = ttk.Combobox(self.tree, values=self.LAYOUT_OPTIONS, state='readonly', font=FB)
            editor.set(current if current in self.LAYOUT_OPTIONS else self.LAYOUT_OPTIONS[0])
        else:
            editor = tk.Entry(self.tree, font=FB)
            editor.insert(0, current)
            editor.select_range(0, 'end')

        editor.place(x=x, y=y, width=w, height=h)
        editor.focus_set()
        self._cell_editor = editor
        self._cell_edit_target = (row_iid, col_name)

        editor.bind('<Return>', lambda e: self._end_cell_edit())
        editor.bind('<KP_Enter>', lambda e: self._end_cell_edit())
        editor.bind('<Escape>', lambda e: self._end_cell_edit(cancel=True))
        editor.bind('<FocusOut>', lambda e: self._end_cell_edit())
        if isinstance(editor, ttk.Combobox):
            editor.bind('<<ComboboxSelected>>', lambda e: self._end_cell_edit())

    def _end_cell_edit(self, cancel=False):
        editor = self._cell_editor
        if editor is None:
            return
        target = self._cell_edit_target
        self._cell_editor = None
        self._cell_edit_target = None
        new_val = None
        if not cancel and target is not None:
            try:
                new_val = editor.get()
            except Exception:
                new_val = None
        try:
            editor.destroy()
        except Exception:
            pass
        if new_val is not None and target is not None:
            self._commit_cell_edit(target[0], target[1], new_val)

    def _commit_cell_edit(self, row_iid, col_name, new_val):
        row_data = next((r for r in self.rows if str(r['no']) == row_iid), None)
        if not row_data:
            return
        new_val = (new_val or '').strip()

        if col_name == 'cap':
            if new_val == '':
                row_data['cap'] = None
            else:
                try:
                    row_data['cap'] = int(float(new_val))
                except ValueError:
                    messagebox.showwarning('경고', '인승은 숫자로 입력하세요.')
                    return
        elif col_name in self._ACTUAL_COLUMN_KEYS:
            key = self._ACTUAL_COLUMN_KEYS[col_name]
            if new_val == '':
                row_data['actual'][key] = None
            else:
                parsed = self._parse_float(new_val)
                if parsed is None:
                    messagebox.showwarning('경고', '숫자로 입력하세요.')
                    return
                row_data['actual'][key] = parsed
        elif col_name in ('layout', 'dong', 'core'):
            row_data[col_name] = new_val

        self.tree.item(row_iid, values=tuple(self._row_values(row_data)))
        if self.tree.selection() and self.tree.selection()[0] == row_iid:
            self._on_tree_select(None)

    def _on_tree_select(self, event):
        sel = self.tree.selection()
        self.txt_detail.config(state='normal')
        self.txt_detail.delete('1.0', 'end')
        if not sel:
            self.txt_detail.insert('1.0', '(표에서 행을 선택하면 상세 내용이 여기에 표시됩니다)')
            self.txt_detail.config(state='disabled')
            return
        iid = sel[0]
        row_data = next((r for r in self.rows if str(r['no']) == iid), None)
        if not row_data:
            self.txt_detail.config(state='disabled')
            return

        actual = row_data['actual']
        cap_disp = row_data['cap'] if row_data['cap'] is not None else '(미입력)'
        lines = [
            f"[No.{row_data['no']}] {row_data['dong']} {row_data['core']}  "
            f"인승{cap_disp} / {row_data['layout']}",
            '',
            f"실측 폭(W)      : {self._fmt_val(actual['w'])} mm",
            f"실측 깊이(D)    : {self._fmt_val(actual['d'])} mm",
        ]
        self.txt_detail.insert('1.0', '\n'.join(lines))
        self.txt_detail.config(state='disabled')

    def _delete_selected_row(self):
        sel = self.tree.selection()
        if not sel:
            return
        for iid in sel:
            self.tree.delete(iid)
            self.rows = [r for r in self.rows if str(r['no']) != iid]

    # ══════════════════════════════════════════════════════════
    #  Excel 내보내기
    # ══════════════════════════════════════════════════════════
    def _export_excel(self):
        if not self.rows:
            messagebox.showwarning('경고', '내보낼 검토 결과가 없습니다.')
            return
        path = filedialog.asksaveasfilename(
            title='검토 결과 저장', defaultextension='.xlsx',
            initialfile=f'승강로사이즈검토_{datetime.now().strftime("%Y%m%d_%H%M")}.xlsx',
            filetypes=[('Excel Files', '*.xlsx')],
        )
        if not path:
            return
        try:
            import openpyxl
            from openpyxl.styles import Font, Alignment

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = '승강로 CAD 도면 분석'

            headers = ['No', '동', '코어', '인승', '배치',
                       '실측W', '실측D']
            ws.append(headers)
            for cell in ws[1]:
                cell.font = Font(bold=True)
                cell.alignment = Alignment(horizontal='center', wrap_text=True)

            for row_data in self.rows:
                actual = row_data['actual']
                ws.append([
                    row_data['no'], row_data['dong'], row_data['core'],
                    row_data['cap'], row_data['layout'],
                    actual['w'], actual['d'],
                ])

            for col in ws.columns:
                max_len = max((len(str(c.value)) for c in col if c.value is not None), default=8)
                ws.column_dimensions[col[0].column_letter].width = max(8, min(28, max_len + 2))
            ws.freeze_panes = 'A2'

            wb.save(path)
            messagebox.showinfo('완료', f'검토 결과를 저장했습니다.\n{path}')
        except Exception as e:
            traceback.print_exc()
            messagebox.showerror('저장 실패', f'{e}')

    # ══════════════════════════════════════════════════════════
    #  프로젝트 상태 직렬화 — 검토표(self.rows)를 다시 입력하지 않고,
    #  저장된 시점의 상태를 그대로 불러와 이어서 작업할 수 있게 한다.
    # ══════════════════════════════════════════════════════════
    SESSION_VERSION = 2

    def _serialize_row(self, r):
        return {k: r.get(k) for k in ('dong', 'core', 'cap', 'layout', 'actual')}

    def _build_session_dict(self):
        """현재 진행 상태(검토 행 전체)를 JSON 저장용 dict로 만든다. 저장된 프로젝트를 열면 검토표가 그대로 복원된다."""
        return {
            'version': self.SESSION_VERSION,
            'saved_at': datetime.now().isoformat(timespec='seconds'),
            'project_name': self.current_project_name,
            'rows': [self._serialize_row(r) for r in self.rows],
        }

    def _apply_session_dict(self, session, source_label):
        """세션 dict(파일로 연 것이든, 🗂 프로젝트 관리에서 불러온 것이든)를 화면에
        복원한다. 프로젝트 관리 "불러오기"가 쓰는 실제 복원
        로직 — source_label만 다르고(어디서 불러왔는지 안내문 첫 줄에 표시) 나머지는
        완전히 동일하다."""
        self.current_project_name = session.get('project_name')

        self.rows = []
        for i in self.tree.get_children():
            self.tree.delete(i)
        n_row_fail = 0
        for row_dict in session.get('rows') or []:
            if not self._add_row_from_dict(row_dict):
                n_row_fail += 1

        msgs = [source_label, f'검토 행 {len(self.rows)}건을 불러왔습니다.']
        if n_row_fail:
            msgs.append(f'⚠ 검토 행 {n_row_fail}건은 복원하지 못했습니다(값이 손상됐을 수 있습니다).')
        self.lbl_status.config(text='\n'.join(msgs), fg=ACCENT_DANGER if n_row_fail else ACCENT_SUCCESS)
        self._refresh_project_label()

    def _add_row_from_dict(self, d):
        """세션 JSON(또는 프로젝트 파일)에 저장된 행 데이터 그대로 검토표에 행을 복원한다."""
        actual = d.get('actual') or {}
        try:
            self._add_detection_row(
                dong=d.get('dong') or '', core=d.get('core') or '',
                cap=d.get('cap'), layout=d.get('layout'),
                w=actual.get('w'), d=actual.get('d'))
        except Exception:
            traceback.print_exc()
            return False
        return True

    # ══════════════════════════════════════════════════════════
    #  🗂 프로젝트 관리 — 이름 붙인 프로젝트 목록 관리(불러오기/저장/
    #  덮어쓰기/삭제/새 프로젝트/내보내기/가져오기).
    # ══════════════════════════════════════════════════════════
    def _refresh_project_label(self):
        if not hasattr(self, 'project_label_var'):
            return
        if self.current_project_name:
            self.project_label_var.set(f'현재 프로젝트: {self.current_project_name}')
        else:
            self.project_label_var.set('현재 프로젝트: (저장 안 된 임시 작업)')

    def _open_project_manager(self):
        _ProjectManagerDialog(self.parent, self)

    def _pm_confirm_discard_current(self, question):
        if not self.rows:
            return True
        return messagebox.askyesno('확인', question)

    def _pm_load(self, name):
        """선택한 프로젝트를 불러온다 — 현재 화면 내용은 사라지고 그 프로젝트로
        완전히 교체된다(여러 동을 이미 누적해온 다른 프로젝트라면 그 동들이
        전부 함께 들어있는 상태로 열린다)."""
        if not self._pm_confirm_discard_current(
                f'현재 화면의 내용을 지우고 프로젝트 "{name}"을(를) 불러오시겠습니까?'):
            return False
        path = project_file_path(name)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                session = json.load(f)
        except Exception as e:
            traceback.print_exc()
            messagebox.showerror('불러오기 실패', f'{e}')
            return False
        self._apply_session_dict(session, f'🗂 프로젝트 불러옴: {name}')
        return True

    def _pm_save_as(self, suggested_name=None):
        """현재 화면 상태를 새 이름의 프로젝트로 저장한다(최초 동 작업을 마치고
        처음 이 프로젝트를 만들 때 사용). 이미 있는 이름이면 확인 후 덮어쓴다."""
        name = simpledialog.askstring(
            '새 이름으로 저장', '프로젝트 이름을 입력하세요(예: 블럭명):',
            initialvalue=suggested_name or self.current_project_name or '', parent=self.parent)
        if not name:
            return None
        name = name.strip()
        if not name:
            return None
        if project_name_exists(name) and not messagebox.askyesno(
                '확인', f'프로젝트 "{name}"이(가) 이미 있습니다. 덮어쓰시겠습니까?'):
            return None
        self.current_project_name = name
        session = self._build_session_dict()
        try:
            write_project_file(name, session)
        except Exception as e:
            traceback.print_exc()
            messagebox.showerror('저장 실패', f'{e}')
            return None
        self._refresh_project_label()
        messagebox.showinfo('저장 완료', f'프로젝트 "{name}"(으)로 저장했습니다.')
        return name

    def _pm_overwrite(self, name):
        """선택한 프로젝트에 현재 화면 상태를 덮어쓴다 — 동을 하나 추가해 ①②③을
        다시 실행한 뒤 이 버튼으로 같은 프로젝트에 반영하면, 그 프로젝트는 이전
        동들 + 이번에 추가한 동까지 모두 포함한 상태로 갱신된다."""
        if not messagebox.askyesno('확인', f'현재 화면 상태를 프로젝트 "{name}"에 덮어쓰시겠습니까?'):
            return False
        self.current_project_name = name
        session = self._build_session_dict()
        try:
            write_project_file(name, session)
        except Exception as e:
            traceback.print_exc()
            messagebox.showerror('저장 실패', f'{e}')
            return False
        self._refresh_project_label()
        messagebox.showinfo('저장 완료', f'프로젝트 "{name}"에 덮어썼습니다.')
        return True

    def _pm_delete(self, name):
        if not messagebox.askyesno('삭제 확인', f'프로젝트 "{name}"을(를) 삭제하시겠습니까? 되돌릴 수 없습니다.'):
            return False
        try:
            ok = delete_project_file(name)
        except Exception as e:
            traceback.print_exc()
            messagebox.showerror('삭제 실패', f'{e}')
            return False
        if ok and self.current_project_name == name:
            self.current_project_name = None
            self._refresh_project_label()
        return ok

    def _pm_new_project(self):
        """현재 화면을 완전히 비운다(등록된 프로젝트 목록 자체는 건드리지 않음) —
        새 블럭의 첫 동부터 시작할 때 사용. 이후 "새 이름으로 저장"으로 새
        프로젝트를 만들면 된다."""
        if not self._pm_confirm_discard_current('현재 화면의 내용을 모두 지우고 새로 시작하시겠습니까?'):
            return False
        blank = {
            'version': self.SESSION_VERSION, 'project_name': None, 'rows': [],
        }
        self._apply_session_dict(blank, '🆕 새 프로젝트(초기화)')
        return True

    def _pm_export_file(self, name):
        path_src = project_file_path(name)
        dest = filedialog.asksaveasfilename(
            title='파일로 내보내기', defaultextension='.json', initialfile=f'{name}.json',
            filetypes=[('JSON Files', '*.json'), ('All Files', '*.*')])
        if not dest:
            return False
        try:
            with open(path_src, 'r', encoding='utf-8') as f:
                data = f.read()
            with open(dest, 'w', encoding='utf-8') as f:
                f.write(data)
        except Exception as e:
            traceback.print_exc()
            messagebox.showerror('내보내기 실패', f'{e}')
            return False
        messagebox.showinfo('내보내기 완료', f'"{name}"을(를) 다음 경로로 내보냈습니다:\n{dest}')
        return True

    def _pm_import_file(self):
        src = filedialog.askopenfilename(
            title='파일에서 가져오기', filetypes=[('JSON Files', '*.json'), ('All Files', '*.*')])
        if not src:
            return None
        try:
            with open(src, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            traceback.print_exc()
            messagebox.showerror('가져오기 실패', f'{e}')
            return None
        default_name = data.get('project_name') or os.path.splitext(os.path.basename(src))[0]
        name = simpledialog.askstring(
            '파일에서 가져오기', '등록할 프로젝트 이름을 입력하세요:',
            initialvalue=default_name, parent=self.parent)
        if not name:
            return None
        name = name.strip()
        if not name:
            return None
        if project_name_exists(name) and not messagebox.askyesno(
                '확인', f'프로젝트 "{name}"이(가) 이미 있습니다. 덮어쓰시겠습니까?'):
            return None
        try:
            write_project_file(name, data)
        except Exception as e:
            traceback.print_exc()
            messagebox.showerror('가져오기 실패', f'{e}')
            return None
        messagebox.showinfo('가져오기 완료', f'"{name}"(으)로 등록했습니다.')
        return name


class _ProjectManagerDialog(tk.Toplevel):
    """🗂 프로젝트 관리 창 — 저장된 프로젝트 목록(이름/버전/최종수정)과
    불러오기/새 이름으로 저장/선택 항목에 덮어쓰기/삭제/새 프로젝트(초기화)/
    파일로 내보내기/파일에서 가져오기/닫기 버튼을 제공한다."""

    def __init__(self, parent, module: 'ElevatorShaftReviewModule'):
        super().__init__(parent)
        self.module = module
        self.title('프로젝트 관리')
        self.geometry('720x460')
        self.minsize(560, 360)
        self.configure(bg=CANVAS_BG)
        self.transient(parent.winfo_toplevel())
        self.grab_set()

        tk.Label(self, text='🗂 프로젝트 관리', font=FH, bg=CANVAS_BG, fg=TEXT_PRIMARY).pack(
            anchor='w', padx=20, pady=(16, 4))
        tk.Label(self, text='한 프로젝트 = 한 블럭. 동을 추가할 때마다 ①②③을 다시 실행한 뒤 '
                             '"선택 항목에 덮어쓰기"로 같은 프로젝트에 누적하세요.',
                 font=FS, bg=CANVAS_BG, fg=TEXT_SECONDARY, wraplength=660, justify='left').pack(
            anchor='w', padx=20, pady=(0, 12))

        table_wrap = tk.Frame(self, bg=BORDER_SOFT)
        table_wrap.pack(fill='both', expand=True, padx=20, pady=(0, 12))
        table_inner = tk.Frame(table_wrap, bg=SURFACE, padx=1, pady=1)
        table_inner.pack(fill='both', expand=True, padx=1, pady=1)

        columns = ('name', 'version', 'saved_at')
        self.tree = ttk.Treeview(table_inner, columns=columns, show='headings',
                                  style=STYLE_TREEVIEW, selectmode='browse')
        self.tree.heading('name', text='프로젝트명')
        self.tree.heading('version', text='버전')
        self.tree.heading('saved_at', text='최종 수정')
        self.tree.column('name', width=280, anchor='w')
        self.tree.column('version', width=80, anchor='center')
        self.tree.column('saved_at', width=200, anchor='center')
        vsb = ttk.Scrollbar(table_inner, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')
        self.tree.bind('<Double-1>', lambda _e: self._on_load())

        btn_row1 = tk.Frame(self, bg=CANVAS_BG)
        btn_row1.pack(fill='x', padx=20, pady=(0, 6))
        for text, cmd in (
            ('✓ 불러오기', self._on_load),
            ('🆕 새 이름으로 저장', self._on_save_as),
            ('📝 선택 항목에 덮어쓰기', self._on_overwrite),
            ('🗑 삭제', self._on_delete),
        ):
            module._metal_btn(btn_row1, text, cmd, text_color=BTN_METAL_TEXT_PRIMARY).pack(side='left', padx=(0, 8))

        btn_row2 = tk.Frame(self, bg=CANVAS_BG)
        btn_row2.pack(fill='x', padx=20, pady=(0, 16))
        for text, cmd in (
            ('🆕 새 프로젝트(초기화)', self._on_new_project),
            ('📤 파일로 내보내기', self._on_export),
            ('📥 파일에서 가져오기', self._on_import),
        ):
            module._metal_btn(btn_row2, text, cmd, text_color=BTN_METAL_TEXT_PRIMARY).pack(side='left', padx=(0, 8))
        module._metal_btn(btn_row2, '닫기', self.destroy).pack(side='right')

        self._refresh_list()

    def _refresh_list(self):
        self.tree.delete(*self.tree.get_children())
        for proj in list_saved_projects():
            self.tree.insert('', 'end', iid=proj['name'],
                              values=(proj['name'], proj['version'], proj['saved_at']))

    def _selected_name(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning('선택 필요', '목록에서 프로젝트를 먼저 선택하세요.', parent=self)
            return None
        return sel[0]

    def _on_load(self):
        name = self._selected_name()
        if not name:
            return
        if self.module._pm_load(name):
            self.destroy()

    def _on_save_as(self):
        name = self.module._pm_save_as()
        if name:
            self._refresh_list()

    def _on_overwrite(self):
        name = self._selected_name()
        if not name:
            return
        if self.module._pm_overwrite(name):
            self._refresh_list()

    def _on_delete(self):
        name = self._selected_name()
        if not name:
            return
        if self.module._pm_delete(name):
            self._refresh_list()

    def _on_new_project(self):
        if self.module._pm_new_project():
            self.destroy()

    def _on_export(self):
        name = self._selected_name()
        if not name:
            return
        self.module._pm_export_file(name)

    def _on_import(self):
        name = self.module._pm_import_file()
        if name:
            self._refresh_list()


# ──────────────────────────────────────────────────────────────
#  단독 실행 테스트 (허브 없이 이 모듈만 열어보기)
# ──────────────────────────────────────────────────────────────
if __name__ == '__main__':
    class _FakeMainApp:
        pass

    root = tk.Tk()
    root.title('승강로 CAD 도면 분석 (단독 실행)')
    root.geometry('1480x1040')
    root.configure(bg=CANVAS_BG)

    frame = tk.Frame(root, bg=CANVAS_BG)
    frame.pack(fill='both', expand=True)

    mod = ElevatorShaftReviewModule(frame, _FakeMainApp())
    mod.build_ui()

    root.mainloop()
