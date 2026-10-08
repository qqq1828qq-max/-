# -*- coding: utf-8 -*-
"""
elevator_shaft_review_module.py — 승강로 CAD 도면 분석
────────────────────────────────────────────────────────
"01 승강로 CAD 도면 분석" 탭.

입력은 지하주차장 전체평면도 DXF 1장(동/층별로 미리 잘라줄 필요 없음)
하나뿐이다. 이 한 장 안에서 승강로(전부) 폭(W)/깊이(D) 실측값을 자동
추출해 "확정 승강로 전체 검토표"에 올리는 것까지가 이 탭의 범위다.
제조사(미쓰비시/OTIS/TK/현대)·당사(POSCO) 규격표와의 적합/부적합 비교,
법적 규격(승강기 인승산정 등)은 별도 탭이 맡고, 이 탭은 그 탭이 쓸
정확한 실측값만 만들어낸다.

탐지는 AI 이미지 판독(도면 렌더링 후 비전 모델에 "여기 승강로 있어?"
묻는 방식)이 아니라, 실제 DXF 벡터 좌표를 직접 읽어서 한다 — 빠르고
정확하며, 전체 도면을 렌더링할 필요가 없다. 사람이 실패를 구간별로
확인할 수 있도록 아래 5단계로 나눠 각각 버튼으로 실행한다(한 단계가
실패해도 어느 단계인지 바로 알 수 있게):

  ① 승강기 심볼 위치 탐지 — "ELEV/승강/LIFT" 등 레이어명의 선/폴리선을
     모아 가까운 것끼리 묶어 승강로 칸(과, 나란히 붙은 병렬 코어 그룹)을
     찾는다 (_find_shaft_points_by_layer).
  ② AI 벽체 레이어 판별 — 이 도면에서 실제 "벽체"를 그린 레이어가
     무엇인지, 레이어별 선 통계만 AI에게 보내 텍스트로 판별한다
     (이미지 전송 없음). 도면 전체에 한 번만 묻고 그 결과를 전체
     승강로에 재사용한다.
  ③ 벽체 스냅 — ②에서 고른 벽 레이어의 실제 선 좌표에 각 승강로를
     스냅해 폭/깊이를 산출한다. 실패하면(벽을 못 찾음) 심볼 자체의
     윤곽(회전 보정 포함)으로 낮춰 표시하고 "⚠벽미검출"로 남긴다 —
     틀린 벽에 잘못 스냅되는 것보다 안전하다.
  ④ '승강로 사이즈' 레이어로 표시 — ③ 결과를 DXF에 별도 레이어로
     그려 넣은 사본을 저장해 도면에서 바로 눈으로 대조할 수 있게 한다.
  ⑤ 확정 검토표에 일괄 반영 — ③ 결과(코어/배치/인승/폭/깊이)를
     "확정 승강로 전체 검토표"에 한 번에 행으로 추가한다.

AI가 채운 값(②의 벽 레이어 판별, 인승 텍스트 인식 등)은 모두 사람이
도면과 대조해 확정하는 것을 전제로 하며, 검토표 셀은 전부 더블클릭으로
직접 수정할 수 있다.
"""

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

import ai_client

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

    def _layer_matches(e):
        try:
            layer = e.dxf.layer or ''
        except Exception:
            return False
        if layer_hint not in layer:
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
                if depth >= max_block_depth:
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
    for x0, y0, x1, y1 in segs:
        length = math.hypot(x1 - x0, y1 - y0)
        if length < min_len_mm:
            continue
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        if math.hypot(mx - cx, my - cy) > radius_mm:
            continue
        angle = math.degrees(math.atan2(y1 - y0, x1 - x0)) % 90.0
        buckets[round(angle)] += 1
    if not buckets:
        return 0.0
    return float(buckets.most_common(1)[0][0])


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
def _find_shaft_points_by_layer(dxf_path: str, layer_keywords=SHAFT_LAYER_KEYWORDS,
                                 size_min_mm: float = 700.0, size_max_mm: float = 7000.0,
                                 cluster_dist_mm: float = 4000.0, merge_bbox_cap_mm: float = 4500.0,
                                 max_block_depth: int = 6):
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

    def _layer_matches(layer_name):
        up = (layer_name or '').upper()
        return any(k in up for k in kws)

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

    def _walk(entities, depth, clip_stack):
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
                if _layer_matches(layer):
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
                            found.append((cx, cy, min(xs), min(ys), max(xs), max(ys), pts, segs))
            elif etype == 'INSERT' and depth < max_block_depth:
                clip_poly = _active_xclip_polygon_world(e)
                new_stack = clip_stack + [clip_poly] if clip_poly else clip_stack
                try:
                    _walk(e.virtual_entities(), depth + 1, new_stack)
                except Exception:
                    continue

    _walk(msp, 0, [])
    if not found:
        return []

    # ── 가까운 순서대로 묶되, 합친 bbox가 승강로 한 칸 크기를 넘기면 묶지
    #    않는 크기-제약 탐욕적 병합 (union-find + 루트별 bbox 추적) ──
    n = len(found)
    parent = list(range(n))
    root_bbox = {i: (found[i][2], found[i][3], found[i][4], found[i][5]) for i in range(n)}

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
        bi, bj = root_bbox[ri], root_bbox[rj]
        merged = (min(bi[0], bj[0]), min(bi[1], bj[1]), max(bi[2], bj[2]), max(bi[3], bj[3]))
        mw, mh = merged[2] - merged[0], merged[3] - merged[1]
        if mw > merge_bbox_cap_mm or mh > merge_bbox_cap_mm:
            adjacency_entity_pairs.append((i, j))
            continue  # 병합하면 승강로 한 칸 크기를 넘어섬 — 서로 다른 승강로로 간주, 묶지 않음
        parent[ri] = rj
        root_bbox[rj] = merged

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

        clusters.append({
            'center': center,
            'bbox': (x0, y0, x1, y1),
            'n_entities': len(idxs),
            'rotated_rect': rotated_rect,
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
            ra, rb = _group_find(ci), _group_find(cj)
            if ra != rb:
                group_parent[ra] = rb

    group_id_map = {}
    for ci in range(n_clusters):
        root = _group_find(ci)
        if root not in group_id_map:
            group_id_map[root] = len(group_id_map)
        clusters[ci]['group_id'] = group_id_map[root]

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

    def _add(layer, length):
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
                if depth >= max_block_depth:
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


WALL_LAYER_AI_PROMPT_TEMPLATE = """당신은 건축 CAD 도면의 레이어 구성을 분석하는 보조원입니다.
아래는 DXF 도면 하나에 있는 레이어 목록이며, 각 레이어에 그려진 직선/폴리선의
개수와 총 길이(미터)를 함께 보여줍니다(이 도면 전체를 한 번 순회해 집계한
실측값입니다).

레이어명은 설계사(회사)마다 전혀 다른 규칙을 씁니다 — 예: "골조", "G-WAL",
"A-Wall", "CON", "G-CON", "WAL-H", "0-Def-기준Wall", "벽체" 등이 모두 "건물
구조체(콘크리트) 벽/기둥"을 의미할 수 있습니다. 반대로 총 길이가 길어도 벽이
아닌 레이어도 많습니다 — 예: 주차선/가이드선, 중심선(CEN/GRID), 치수선,
마감재(피니시), 전기차 충전구역 표시, 장애인 주차구역 표시 등.

레이어 목록(총길이 내림차순):
{layer_lines}

과제: 이 중 승강기 승강로(엘리베이터 샤프트)를 둘러싼 "구조체 콘크리트
벽/기둥"을 나타낼 가능성이 높은 레이어를 모두 골라, 가능성이 높은 순서로
정렬해 답하세요. 레이어명에 "골조/WALL/WAL/CON/벽체/기둥/구조" 같은 단서가
있으면 유력하지만, 단서가 없어도 통계상 벽체스러우면 후보에 넣을 수 있습니다.
확신이 없으면 여러 개를 후보로 제시해도 됩니다(호출하는 쪽에서 순서대로
시도합니다). 주차선/중심선/그리드/치수선/마감재/설비 레이어는 제외하세요.

반드시 JSON으로만 답하세요(설명 문장 금지):
{{"wall_layers": ["레이어명1", "레이어명2", ...], "reason": "간단한 판단 근거"}}
레이어명은 위 목록에 있는 것과 정확히 똑같은 문자열이어야 합니다."""


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


def _validate_parallel_fix(raw_shafts, member_local, verticals, horizontals,
                            snap_tol=30.0, size_range=(1500.0, 4500.0), min_cover=0.5):
    """AI가 고른 칸별 사각형을 검증한다. member_local: {id: (cx, cy)} 로컬 중심.
    통과하면 {id: (x0,x1,y0,y1)}(목록의 실제 벽선 좌표로 보정됨), 하나라도 어긋나면 None."""
    def _nearest(val, lines):
        best = None
        for ln in lines:
            d = abs(ln['pos'] - val)
            if d <= snap_tol and (best is None or d < best[0]):
                best = (d, ln)
        return best[1] if best else None

    if not isinstance(raw_shafts, list):
        return None
    rects = {}
    for s in raw_shafts:
        try:
            sid = int(s['id'])
            vals = [float(s[k]) for k in ('x0', 'x1', 'y0', 'y1')]
        except Exception:
            return None
        if sid not in member_local or sid in rects:
            return None
        lx0, lx1 = _nearest(vals[0], verticals), _nearest(vals[1], verticals)
        ly0, ly1 = _nearest(vals[2], horizontals), _nearest(vals[3], horizontals)
        if not (lx0 and lx1 and ly0 and ly1):
            return None  # 목록에 없는 좌표 — AI가 지어낸 값
        x0, x1 = sorted((lx0['pos'], lx1['pos']))
        y0, y1 = sorted((ly0['pos'], ly1['pos']))
        w, d = x1 - x0, y1 - y0
        if not (size_range[0] <= w <= size_range[1] and size_range[0] <= d <= size_range[1]):
            return None
        cx, cy = member_local[sid]
        if not (x0 <= cx <= x1 and y0 <= cy <= y1):
            return None
        # 선택한 좌우/상하 벽선이 실제로 칸 길이의 절반 이상을 덮어야 한다.
        for ln, lo, hi in ((lx0, y0, y1), (lx1, y0, y1), (ly0, x0, x1), (ly1, x0, x1)):
            if _line_coverage(ln, lo, hi) < min_cover:
                return None
        rects[sid] = (x0, x1, y0, y1)
    if set(rects) != set(member_local):
        return None
    ids = list(rects)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = rects[ids[i]], rects[ids[j]]
            ox = min(a[1], b[1]) - max(a[0], b[0])
            oy = min(a[3], b[3]) - max(a[2], b[2])
            if ox > 50.0 and oy > 50.0:
                return None  # 칸끼리 겹침 — 옆 칸을 통째로 포함한 경우
    return rects


def _ai_fix_parallel_group(segs, members, api_key, model, provider, max_lines=70):
    """병렬 그룹(members: 클러스터 dict 리스트, 각각 'center'/'rotated_rect' 포함)의
    칸별 안쪽 벽면 사각형을 AI+벡터 검증으로 구한다.
    반환: {member 리스트 인덱스: {'points':[(x,y)x4], 'width_mm', 'depth_mm'}} 또는 None."""
    if not segs or len(members) < 2 or not api_key:
        return None
    centers = [m['center'] for m in members]
    gcx = sum(c[0] for c in centers) / len(centers)
    gcy = sum(c[1] for c in centers) / len(centers)
    x0s = [m['bbox'][0] for m in members]; x1s = [m['bbox'][2] for m in members]
    y0s = [m['bbox'][1] for m in members]; y1s = [m['bbox'][3] for m in members]
    extent = max(max(x1s) - min(x0s), max(y1s) - min(y0s))
    angle = _dominant_wall_angle_deg(segs, (gcx, gcy), extent / 2 + 3500.0)
    if abs(angle - 90.0) < 1.0:
        angle = 0.0
    origin = (gcx, gcy)

    # 로컬 창: 그룹 전체 + 사방 여유(벽 두께·바깥벽 포함)
    local_pts = [_rotate_point(px, py, -angle, origin) for px, py in
                 [(x, y) for m in members for x, y in
                  ((m['bbox'][0], m['bbox'][1]), (m['bbox'][2], m['bbox'][3]),
                   (m['bbox'][0], m['bbox'][3]), (m['bbox'][2], m['bbox'][1]))]]
    lx = [p[0] - gcx for p in local_pts]; ly = [p[1] - gcy for p in local_pts]
    pad = 1500.0
    window = (min(lx) - pad, min(ly) - pad, max(lx) + pad, max(ly) + pad)
    verticals, horizontals = _local_wall_lines(segs, origin, angle, window)
    if len(verticals) < 2 or len(horizontals) < 2:
        return None

    def _top(lines):
        # 프롬프트 길이 제한: 구간 총 길이가 긴(=진짜 벽다운) 선 위주로 남기고 좌표순 정렬
        lines = sorted(lines, key=lambda l: -sum(b - a for a, b in l['spans']))[:max_lines]
        return sorted(lines, key=lambda l: l['pos'])

    verticals, horizontals = _top(verticals), _top(horizontals)

    member_local = {}
    shaft_lines = []
    for k, m in enumerate(members, start=1):
        rx, ry = _rotate_point(m['center'][0], m['center'][1], -angle, origin)
        member_local[k] = (rx - gcx, ry - gcy)
        rr = m.get('rotated_rect') or {}
        shaft_lines.append(f"- id {k}: 중심 ({member_local[k][0]:.0f}, {member_local[k][1]:.0f}), "
                           f"심볼 윤곽 약 {rr.get('width_mm', 0):.0f}x{rr.get('height_mm', 0):.0f}")

    def _fmt(lines, tag):
        return '\n'.join(
            f"{tag}={ln['pos']:.0f} | " + ', '.join(f"[{a:.0f}~{b:.0f}]" for a, b in ln['spans'][:4])
            for ln in lines)

    prompt = PARALLEL_FIX_PROMPT.format(
        n=len(members), shaft_lines='\n'.join(shaft_lines),
        v_lines=_fmt(verticals, 'x'), h_lines=_fmt(horizontals, 'y'))
    try:
        raw = ai_client.generate_text(
            api_key, model, prompt, provider=provider,
            response_format={'type': 'json_object'}, _context='ai_fix_parallel_group')
        data = _extract_json(raw)
    except Exception:
        traceback.print_exc()
        return None

    rects = _validate_parallel_fix(data.get('shafts') if isinstance(data, dict) else None,
                                   member_local, verticals, horizontals)
    if not rects:
        return None
    out = {}
    for k, (x0, x1, y0, y1) in rects.items():
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        world = [_rotate_point(gcx + cx, gcy + cy, angle, origin) for cx, cy in corners]
        out[k - 1] = {'points': world, 'width_mm': round(x1 - x0, 1), 'depth_mm': round(y1 - y0, 1)}
    return out


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


def _extract_json(text: str) -> dict:
    """AI 응답 문자열에서 JSON 객체를 최대한 관대하게 추출한다."""
    if not text:
        return {}
    text = text.strip()
    text = re.sub(r'^```(?:json)?', '', text.strip())
    text = re.sub(r'```$', '', text.strip())
    start = text.find('{')
    end = text.rfind('}')
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        return json.loads(text[start:end + 1])
    except Exception:
        return {}



# ──────────────────────────────────────────────────────────────
#  프로젝트 관리(저장소) — 세션 JSON(검토표)을 매번 파일 대화상자로 아무
#  경로에나 저장/열기 하던 기존 방식(💾 저장 / 📂 이어하기) 대신, 이름 붙인
#  "프로젝트" 목록을 한 곳에서 관리한다.
#
#  저장 위치는 api_settings_module.py의 설정 파일(.api_settings.json)과
#  같은 관례로, 프로그램 파일 옆의 숨김 폴더에 프로젝트 1개당 JSON 파일 1개.
# ──────────────────────────────────────────────────────────────
PROJECTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.elevator_projects')


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

    # ── 공통 API 키/모델 조회 ──────────────────────────────────
    def _get_api(self):
        sa = getattr(self.main_app, 'shared_api', None) or {}
        return sa.get('api_key', ''), sa.get('model', ''), sa.get('provider', 'nvidia')

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
                         text_color=BTN_METAL_TEXT_PRIMARY, bold=True).pack(side='left', padx=(0, 8))
        self._metal_btn(session_btn_row, '📂 이어하기', self._resume_session,
                         text_color=BTN_METAL_TEXT_PRIMARY, bold=True).pack(side='left')
        self._refresh_project_label()
        tk.Label(header,
                 text='승강기 관련 텍스트가 전혀 없는 지하주차장 전체평면도에서, 승강기 심볼(ELEV 계열 레이어) '
                      '형상만으로 승강로 위치를 찾고 벽체(콘크리트 구조체)에 스냅해 실측 폭/깊이를 뽑아냅니다. '
                      '(수직 방향 치수(피트/오버헤드/기계실높이)나 제조사 규격 비교는 다루지 않습니다 — 평면 '
                      '폭/깊이만 다룹니다) — 탐지 결과는 아래 검토표에 바로 쌓이며, "💾 진행상태 저장"으로 '
                      'JSON에 저장해두고 "📂 이어하기"로 다시 열 수 있습니다. 여러 블럭을 관리할 땐 '
                      '"🗂 프로젝트 관리"로 이름 붙여 저장하세요.',
                 font=FB, bg=CANVAS_BG, fg=TEXT_SECONDARY, wraplength=1080, justify='left').pack(anchor='w')

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
        tk.Label(card, text='승강기 관련 텍스트가 전혀 없는 지하주차장(또는 그 외 텍스트 없는) 전체평면도 DXF를 '
                            '선택하면, 승강기 심볼(ELEV 계열 레이어) 형상만으로 승강로 위치를 찾고, AI가 벽체 '
                            '(콘크리트 구조체) 레이어를 판별해 그 벽에 스냅한 실측 폭/깊이를 뽑아냅니다. '
                            '"인승" 표기가 심볼 근처에 있으면 함께 인식합니다. 동 전체가 한 장에 들어있는 '
                            '전체평면도 1개만 넣으면 되고, 동별로 나눠 넣을 필요는 없습니다.',
                 font=FS, bg=SURFACE, fg=TEXT_MUTED, wraplength=1080, justify='left').pack(anchor='w', pady=(2, 10))

        # ── ①②③④⑤ 단계 버튼: 안 되는 구간을 그 자리에서 바로 찾을 수 있도록
        # 한 번에 다 처리하지 않고 단계를 나눈다 — ①에서 심볼이 몇 곳 잡히는지,
        # ②에서 AI가 벽 레이어를 제대로 골랐는지, ③에서 실제로 몇 곳이 벽에
        # 스냅됐는지를 각각 확인한 뒤 다음 단계로 넘어간다.
        btn_row = tk.Frame(card, bg=SURFACE)
        btn_row.pack(fill='x', pady=(4, 0))
        self.btn_step1 = self._metal_btn(btn_row, '① 승강기 심볼 위치 탐지', self._run_parking_step1_detect,
                                          text_color=BTN_METAL_TEXT_PRIMARY, bold=True)
        self.btn_step1.pack(side='left')
        self.btn_step2 = self._metal_btn(btn_row, '② AI 벽체 레이어 판별', self._run_parking_step2_identify_walls)
        self.btn_step2.pack(side='left', padx=(10, 0))
        self.btn_step2.config(state='disabled')
        self.btn_step3 = self._metal_btn(btn_row, '③ 벽체 스냅', self._run_parking_step3_snap)
        self.btn_step3.pack(side='left', padx=(10, 0))
        self.btn_step3.config(state='disabled')
        self.btn_step4 = self._metal_btn(btn_row, "④ '승강로 사이즈' 레이어로 표시", self._run_parking_step4_export_layer)
        self.btn_step4.pack(side='left', padx=(10, 0))
        self.btn_step4.config(state='disabled')
        self.btn_step5 = self._metal_btn(btn_row, '⑤ 확정 검토표에 일괄 반영', self._run_parking_step5_apply_to_table)
        self.btn_step5.pack(side='left', padx=(10, 0))
        self.btn_step5.config(state='disabled')

        # ── 진행률 표시줄 ──
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
                                          bg=SURFACE, fg=ACCENT_PRIMARY, width=16, anchor='w')
        self.lbl_progress_pct.pack(side='left', padx=(8, 0))

        self.lbl_ai_status = tk.Label(card, text='', font=FS, bg=SURFACE, fg=TEXT_MUTED,
                                       wraplength=1080, justify='left')
        self.lbl_ai_status.pack(anchor='w', pady=(8, 0))

        # ── ③ 결과 미리보기 — 어느 승강로가 벽 스냅에 실패했는지(symbol_bbox) 바로 확인 ──
        tk.Label(card, text='③ 스냅 결과 (더블클릭 없이 확인용) — source가 wall_snap이 아니면 벽 미검출입니다',
                 font=FS, bg=SURFACE, fg=TEXT_MUTED).pack(anchor='w', pady=(10, 2))
        # 승강로가 수십 개일 수 있어 높이를 6행으로 고정해두되, 스크롤바를
        # 붙여 전체 결과를 끝까지 넘겨볼 수 있게 한다(그렇지 않으면 6개를
        # 넘는 결과는 화면에서 잘려 안 보임 — "안되는 구간을 찾는다"는
        # 목적상 전체를 다 봐야 함).
        parking_tree_frame = tk.Frame(card, bg=SURFACE)
        parking_tree_frame.pack(fill='x', pady=(0, 4))

        self.tree_parking = ttk.Treeview(
            parking_tree_frame, columns=('idx', 'w', 'd', 'cap', 'source', 'wall_layer'), show='headings',
            style=STYLE_TREEVIEW, height=6)
        for c, t, w in [('idx', '#', 36), ('w', '폭', 60), ('d', '깊이', 60), ('cap', '인승', 50),
                        ('source', '출처', 90), ('wall_layer', '사용된 벽 레이어', 220)]:
            self.tree_parking.heading(c, text=t)
            self.tree_parking.column(c, width=w, anchor='center')

        parking_vsb = ttk.Scrollbar(parking_tree_frame, orient='vertical', command=self.tree_parking.yview)
        self.tree_parking.configure(yscrollcommand=parking_vsb.set)
        self.tree_parking.grid(row=0, column=0, sticky='nsew')
        parking_vsb.grid(row=0, column=1, sticky='ns')
        parking_tree_frame.grid_columnconfigure(0, weight=1)

        save_row = tk.Frame(card, bg=SURFACE)
        save_row.pack(fill='x', pady=(10, 0))
        self._metal_btn(save_row, '💾 진행상태 저장', self._save_session).pack(side='left')

    def _clear_parking_tree(self):
        if hasattr(self, 'tree_parking'):
            self.tree_parking.delete(*self.tree_parking.get_children())

    def _refresh_parking_tree(self):
        self._clear_parking_tree()
        for r in self.parking_shaft_results:
            self.tree_parking.insert('', 'end', iid=str(r['index']), values=(
                r['index'], f"{r['width_mm']:.0f}", f"{r['depth_mm']:.0f}",
                r.get('capacity_guess') or '', r['source'], r.get('wall_layer_used') or '',
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
    def _ai_identify_wall_layers(self, dxf_path, api_key, model, provider):
        """이 도면에서 승강로를 둘러싼 "구조체 콘크리트 벽/기둥" 레이어가 어느
        것인지 AI에게 물어 판별한다. 설계사마다 벽 레이어명이 제각각이라(골조/
        G-WAL/A-Wall/CON/G-CON/WAL-H 등) 하드코딩된 키워드 하나로는 범용적으로
        대응할 수 없다는 사용자 피드백에 따라 추가 — 레이어별 선분 통계를
        계산해 보여주고, 레이어"명"의 의미를 읽을 수 있는 AI가 직접 고르게
        한다. 같은 파일을 여러 번 분석해도 매번 다시 묻지 않도록 파일 경로
        기준으로 캐시한다. 반환: 유력한 순서로 정렬된 레이어명 리스트(검증
        실패/빈 결과면 빈 리스트 — 호출부가 폴백하게 한다)."""
        cache = getattr(self, '_wall_layer_cache', None)
        if cache is None:
            cache = self._wall_layer_cache = {}
        if dxf_path in cache:
            return cache[dxf_path]

        stats = _collect_layer_line_stats(dxf_path)
        if not stats:
            cache[dxf_path] = []
            return []
        valid_names = {s['layer'] for s in stats}
        # 노이즈(아주 짧은 선 몇 개뿐인 레이어)는 제외하고, 프롬프트 길이를
        # 위해 상위 60개로 제한한다 — 벽 레이어가 60위 밖일 정도로 짧게
        # 그려진 경우는 현실적으로 거의 없다.
        shown = [s for s in stats if s['count'] >= 5][:60]
        layer_lines = '\n'.join(
            f"- {s['layer']}  (선분 {s['count']}개, 총길이 {s['total_length_mm'] / 1000:.1f}m)"
            for s in shown
        )
        prompt = WALL_LAYER_AI_PROMPT_TEMPLATE.format(layer_lines=layer_lines)
        try:
            raw = ai_client.generate_text(
                api_key, model, prompt, provider=provider,
                response_format={'type': 'json_object'}, _context='ai_identify_wall_layers')
            data = _extract_json(raw)
            candidates = data.get('wall_layers') if isinstance(data, dict) else None
        except Exception:
            traceback.print_exc()
            candidates = None

        result = [c for c in (candidates or []) if c in valid_names]
        cache[dxf_path] = result
        return result

    # ── ① 승강기 심볼 위치 탐지 (로컬 전용 — AI 호출 없음, 빠르고 정확) ──
    def _run_parking_step1_detect(self):
        path = filedialog.askopenfilename(
            title="승강로를 자동탐지할 지하주차장(또는 승강기 텍스트가 없는) 전체평면도 DXF 선택",
            filetypes=[('DXF Files', '*.dxf'), ('All Files', '*.*')]
        )
        if not path:
            return
        self.parking_source_path = path
        self.parking_clusters = []
        self.parking_wall_layers = []
        self.parking_shaft_results = []
        self._clear_parking_tree()
        self.btn_step1.config(state='disabled')
        self._reset_progress(0)
        self.lbl_ai_status.config(
            text=f'① "{os.path.basename(path)}"에서 승강기 심볼로 승강로 위치를 찾는 중...',
            fg=ACCENT_WARNING)
        threading.Thread(target=self._parking_step1_thread, args=(path,), daemon=True).start()

    def _parking_step1_thread(self, path):
        try:
            clusters = _find_shaft_points_by_layer(path, layer_keywords=SHAFT_LAYER_KEYWORDS)
        except Exception:
            traceback.print_exc()
            clusters = []
        self.parent.after(0, lambda: self._on_parking_step1_done(clusters))

    def _on_parking_step1_done(self, clusters):
        self.btn_step1.config(state='normal')
        self._finish_progress(len(clusters) or 1)
        self.parking_clusters = clusters
        if not clusters:
            messagebox.showwarning(
                '안내',
                '"ELEV" 계열 레이어에서 승강기 심볼로 보이는 형상을 찾지 못했습니다.\n'
                '이 도면은 승강기 심볼이 다른 레이어명을 쓰거나, 해당 레이어에 그려져 있지 '
                '않을 수 있습니다.'
            )
            self.lbl_ai_status.config(text='⚠ ① 심볼 탐지 결과 없음.', fg=ACCENT_WARNING)
            self.btn_step2.config(state='disabled')
            return
        self.lbl_ai_status.config(
            text=f'✅ ① 승강기 심볼 {len(clusters)}곳 발견. 다음: ② AI로 벽체 레이어 판별을 실행하세요.',
            fg=ACCENT_SUCCESS)
        self.btn_step2.config(state='normal')

    # ── ② AI 벽체(콘크리트 구조체) 레이어 판별 ──
    def _run_parking_step2_identify_walls(self):
        if not self.parking_clusters or not self.parking_source_path:
            messagebox.showinfo('안내', "먼저 '① 승강기 심볼 위치 탐지'를 실행하세요.")
            return
        api_key, model, provider = self._get_api()
        if not api_key:
            messagebox.showwarning(
                '안내',
                '벽체(콘크리트 구조체) 레이어를 AI로 자동 판별하려면 API 키가 필요합니다.\n'
                '상단 "API 설정"에서 키를 입력해주세요. (키가 없으면 승강기 심볼 윤곽만으로 '
                '대략의 크기를 표시하고, ③ 벽체 스냅은 건너뜁니다.)'
            )
        self.btn_step2.config(state='disabled')
        self._reset_progress(0)
        self.lbl_ai_status.config(text='② AI가 벽체(콘크리트 구조체) 레이어를 판별하는 중...', fg=ACCENT_WARNING)
        threading.Thread(target=self._parking_step2_thread,
                          args=(self.parking_source_path, api_key, model, provider), daemon=True).start()

    def _parking_step2_thread(self, path, api_key, model, provider):
        wall_layers = []
        if api_key:
            try:
                wall_layers = self._ai_identify_wall_layers(path, api_key, model, provider)
            except Exception:
                traceback.print_exc()
                wall_layers = []
        self.parent.after(0, lambda: self._on_parking_step2_done(wall_layers))

    def _on_parking_step2_done(self, wall_layers):
        self.btn_step2.config(state='normal')
        self._finish_progress(1)
        self.parking_wall_layers = wall_layers
        self.btn_step3.config(state='normal')
        if wall_layers:
            self.lbl_ai_status.config(
                text=f"✅ ② AI가 판별한 벽체 레이어: {', '.join(wall_layers)}. "
                     f"다음: ③ 벽체 스냅을 실행하세요.",
                fg=ACCENT_SUCCESS)
        else:
            self.lbl_ai_status.config(
                text='⚠ ② 벽체 레이어를 판별하지 못했습니다 — ③을 실행하면 심볼 윤곽 크기(낮은 신뢰도)로 대체됩니다.',
                fg=ACCENT_WARNING)

    # ── ③ 실제 벽선에 스냅 ──
    def _run_parking_step3_snap(self):
        if not self.parking_clusters or not self.parking_source_path:
            messagebox.showinfo('안내', "먼저 '① 승강기 심볼 위치 탐지'를 실행하세요.")
            return
        self.btn_step3.config(state='disabled')
        self._reset_progress(len(self.parking_clusters))
        self.lbl_ai_status.config(text='③ 벽체 선에 스냅하는 중...', fg=ACCENT_WARNING)
        api = self._get_api()
        threading.Thread(target=self._parking_step3_thread, args=(api,), daemon=True).start()

    def _parking_step3_thread(self, api=None):
        def _progress(i, total):
            self.parent.after(0, lambda i=i, total=total: self._set_progress(i, total))
        try:
            results = self._snap_parking_clusters_to_walls(
                self.parking_source_path, self.parking_clusters, self.parking_wall_layers,
                progress_cb=_progress, api=api)
        except Exception:
            traceback.print_exc()
            results = []
        self.parent.after(0, lambda: self._on_parking_step3_done(results))

    def _snap_parking_clusters_to_walls(self, dxf_path, clusters, wall_layers, progress_cb=None, api=None):
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
            if layer_hint not in segs_cache:
                segs_cache[layer_hint] = _wall_segment_candidates(msp, layer_hint=layer_hint) if msp else []
            return segs_cache[layer_hint]

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
                'group_id': c.get('group_id', i - 1),
            })

        # ── 병렬 그룹 전용 AI 위치 보정 ──
        # 병렬 그룹에 벽 스냅 실패 칸이 하나라도 있으면, 그 그룹만 AI에게 칸별
        # 안쪽 벽면을 고르게 하고(좌표 목록에서만 선택, 코드가 검증) 그룹 전체를
        # 교체한다. 단독 승강로나 전부 정상 스냅된 병렬 그룹은 건드리지 않는다.
        if api and api[0] and msp is not None:
            by_group = {}
            for pos, r in enumerate(results):
                by_group.setdefault(r['group_id'], []).append(pos)
            for gid, positions in by_group.items():
                if len(positions) < 2:
                    continue
                if all(results[p]['source'] == 'wall_snap' for p in positions):
                    continue
                members = [clusters[results[p]['index'] - 1] for p in positions]
                for wl in wall_layers:
                    fixed = _ai_fix_parallel_group(_segs_for(wl), members, api[0], api[1], api[2])
                    if not fixed:
                        continue
                    for k, p in enumerate(positions):
                        results[p].update({
                            'points': fixed[k]['points'],
                            'width_mm': fixed[k]['width_mm'],
                            'depth_mm': fixed[k]['depth_mm'],
                            'wall_layer_used': wl, 'source': 'ai_group_fix',
                        })
                    break
        return results

    def _on_parking_step3_done(self, results):
        self.btn_step3.config(state='normal')
        self._finish_progress(len(results) or 1)
        self.parking_shaft_results = results
        self._refresh_parking_tree()
        if not results:
            self.lbl_ai_status.config(text='⚠ ③ 스냅 결과가 없습니다.', fg=ACCENT_WARNING)
            self.btn_step4.config(state='disabled')
            self.btn_step5.config(state='disabled')
            return
        n_wall = sum(1 for r in results if r['source'] in ('wall_snap', 'ai_group_fix'))
        n_ai = sum(1 for r in results if r['source'] == 'ai_group_fix')
        n_fallback = len(results) - n_wall
        n_cap = sum(1 for r in results if r.get('capacity_guess'))
        msg = (f'✅ ③ 스냅 완료 — 벽체 스냅 {n_wall}곳'
               + (f'(병렬 AI 위치보정 {n_ai}곳 포함)' if n_ai else '')
               + (f', 벽 미검출(심볼 윤곽만) {n_fallback}곳' if n_fallback else '')
               + f', 인승 표기 인식 {n_cap}곳. 아래 결과를 확인한 뒤 ④/⑤를 실행하세요.')
        self.lbl_ai_status.config(text=msg, fg=ACCENT_SUCCESS if not n_fallback else ACCENT_WARNING)
        self.btn_step4.config(state='normal')
        self.btn_step5.config(state='normal')

    # ── ④ '승강로 사이즈' 레이어로 표시 (사본 DXF 저장) ──
    def _run_parking_step4_export_layer(self):
        if not self.parking_shaft_results:
            messagebox.showinfo('안내', "먼저 '③ 벽체 스냅'을 실행하세요.")
            return
        shapes = []
        for r in self.parking_shaft_results:
            cap_part = f" {r['capacity_guess']}인승" if r.get('capacity_guess') else ''
            tag = {'wall_snap': '', 'ai_group_fix': ' ✎병렬AI보정'}.get(r['source'], ' ⚠벽미검출(심볼윤곽)')
            label = f"#{r['index']} {r['width_mm']:.0f}x{r['depth_mm']:.0f}{cap_part}{tag}"
            shapes.append({'points': r['points'], 'label': label, 'layer': '승강로 사이즈'})
        out_path = write_shaft_layer_dxf(self.parking_source_path, shapes,
                                          layer_name='승강로 사이즈', suffix='_승강로검토')
        if out_path:
            msg = f'✅ ④ "승강로 사이즈" 레이어로 표시한 사본을 원본 옆에 저장했습니다: {os.path.basename(out_path)}'
            self.lbl_ai_status.config(text=msg, fg=ACCENT_SUCCESS)
        else:
            msg = '❌ ④ 주황 폴리선 사본 저장에 실패했습니다.'
            self.lbl_ai_status.config(text=msg, fg=ACCENT_DANGER)
        messagebox.showinfo('완료' if out_path else '오류', msg)

    # ── ⑤ 확정 승강로 전체 검토표에 일괄 반영 ──
    def _run_parking_step5_apply_to_table(self):
        if not self.parking_shaft_results:
            messagebox.showinfo('안내', "먼저 '③ 벽체 스냅'을 실행하세요.")
            return
        # ①단계에서 크기 상한 때문에 하나로 못 묶었지만 바로 옆에 붙어있던
        # 승강로들(group_id가 같음)은 "같은 코어에 나란히 붙은 승강로"로 보고
        # 같은 코어 번호 + 배치="병렬"로, 혼자인 승강로는 배치="단독"으로
        # 검토표에 반영한다(이전에는 전부 "단독"으로 잘못 표시됐었음).
        groups = {}
        for r in self.parking_shaft_results:
            groups.setdefault(r.get('group_id', r['index']), []).append(r)
        # 그룹 번호는 도면에서 보기 편하도록 그룹 내 최소 index 순으로 부여
        ordered_group_ids = sorted(groups.keys(), key=lambda gid: min(r['index'] for r in groups[gid]))

        n_added = 0
        n_low_conf = 0
        for core_no, gid in enumerate(ordered_group_ids, start=1):
            members = sorted(groups[gid], key=lambda r: r['index'])
            is_parallel = len(members) > 1
            layout = self.LAYOUT_OPTIONS[1] if is_parallel else self.LAYOUT_OPTIONS[0]  # '병렬' / '단독'
            for sub_i, r in enumerate(members):
                # 동 구분은 전체평면도 한 장만으로는 자동으로 알 수 없어 비워두고
                # (사용자가 셀을 더블클릭해 직접 채움). 병렬 그룹은 같은 코어
                # 번호에 A/B/C로 구분, 단독은 번호만.
                core_label = f"코어{core_no}" + (chr(ord('A') + sub_i) if is_parallel else '')
                # ③단계 미리보기에서는 source가 symbol_bbox(벽 미검출→심볼
                # 윤곽으로 대체)인 행을 "⚠벽미검출"로 표시해주지만, 그 정보가
                # 여기 확정 검토표/세션 저장까지는 전달되지 않아 사용자가 나중에
                # 세션 파일만 보고는 "이 승강로는 자동 크기가 아니라 직접
                # 벽 치수를 확인해야 한다"는 걸 알 방법이 없었다(실제 벽 스냅에
                # 실패한 승강로의 크기가 틀렸다는 사용자 피드백의 원인) — 코어
                # 라벨에 ⚠ 표시를 남겨 검토표/엑셀/세션에 그대로 보이게 한다.
                if r.get('source') == 'symbol_bbox':
                    core_label += ' ⚠벽미검출'
                    n_low_conf += 1
                elif r.get('source') == 'ai_group_fix':
                    core_label += ' ✎AI보정'  # 병렬 전용 AI 위치 보정 — 눈으로 대조 권장
                self._add_detection_row(
                    core=core_label, cap=r.get('capacity_guess'), layout=layout,
                    w=r.get('width_mm'), d=r.get('depth_mm'))
                n_added += 1
        msg = f'✅ ⑤ 확정 승강로 전체 검토표에 {n_added}건을 추가했습니다(동 구분은 직접 입력하세요).'
        if n_low_conf:
            msg += f' ⚠ {n_low_conf}건은 벽체 미검출(심볼 윤곽 크기)로 표시됨 — 도면에서 직접 확인 필요.'
        self.lbl_ai_status.config(text=msg, fg=ACCENT_SUCCESS if not n_low_conf else ACCENT_WARNING)
        messagebox.showinfo('완료', msg)

    # ── 진행률 표시줄 ──────────────────────────────────
    # ── 진행률 표시줄 ──────────────────────────────────
    def _reset_progress(self, total=0):
        """단계 시작 시 진행률 표시줄을 0%로 초기화한다."""
        self.progress_bar['maximum'] = max(total, 1)
        self.progress_bar['value'] = 0
        self.lbl_progress_pct.config(text=f'0/{total} (0%)' if total else '')

    def _set_progress(self, current, total):
        """단계 진행 중 콜백에서 호출 — 진행률 표시줄과 퍼센트 텍스트를 함께 갱신한다."""
        self.progress_bar['maximum'] = max(total, 1)
        self.progress_bar['value'] = current
        pct = int(round((current / total) * 100)) if total else 0
        self.lbl_progress_pct.config(text=f'{current}/{total} ({pct}%)')

    def _finish_progress(self, total):
        """단계 완료 시 100%로 채워서 "다 끝났다"는 것을 눈으로 바로 알 수 있게 한다."""
        self.progress_bar['maximum'] = max(total, 1)
        self.progress_bar['value'] = total
        self.lbl_progress_pct.config(text=f'완료 {total}/{total} (100%)' if total else '')

    # ══════════════════════════════════════════════════════════
    #  행 추가 / 삭제 / 계산
    # ══════════════════════════════════════════════════════════
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
    #  진행상태 저장 / 이어하기(JSON) — 검토표(self.rows)를 다시 입력하지 않고,
    #  저장된 시점의 상태를 그대로 불러와 이어서 작업할 수 있게 한다.
    # ══════════════════════════════════════════════════════════
    SESSION_VERSION = 2

    def _serialize_row(self, r):
        return {k: r.get(k) for k in ('dong', 'core', 'cap', 'layout', 'actual')}

    def _build_session_dict(self):
        """현재 진행 상태(검토 행 전체)를 JSON 저장용 dict로 만든다. "이어하기"가
        이 파일을 열면 검토표가 그대로 복원된다(탐지 자체를 다시 돌릴 필요 없음)."""
        return {
            'version': self.SESSION_VERSION,
            'saved_at': datetime.now().isoformat(timespec='seconds'),
            'project_name': self.current_project_name,
            'rows': [self._serialize_row(r) for r in self.rows],
        }

    def _save_session(self):
        """현재 "확정 승강로 전체 검토표"를 JSON으로 저장한다 — "📂 이어하기"로
        다시 열면 검토표가 그대로 복원된다."""
        if not self.rows:
            messagebox.showwarning('경고', '저장할 검토 행이 없습니다. 먼저 승강로를 탐지하세요.')
            return
        path = filedialog.asksaveasfilename(
            title='진행상태 저장', defaultextension='.json', initialfile='승강로검토_진행상태.json',
            filetypes=[('JSON Files', '*.json'), ('All Files', '*.*')])
        if not path:
            return
        session = self._build_session_dict()
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(session, f, ensure_ascii=False, indent=2)
        except Exception as e:
            traceback.print_exc()
            messagebox.showerror('저장 실패', f'{e}')
            return
        messagebox.showinfo('저장 완료', f'검토 행 {len(self.rows)}건을 저장했습니다.\n{path}')

    def _resume_session(self):
        path = filedialog.askopenfilename(
            title='이어하기 — 저장된 진행 상태 불러오기', filetypes=[('JSON Files', '*.json'), ('All Files', '*.*')])
        if not path:
            return
        if self.rows and not messagebox.askyesno(
                '확인', '현재 화면의 내용을 지우고 저장된 진행 상태를 불러오시겠습니까?'):
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                session = json.load(f)
        except Exception as e:
            traceback.print_exc()
            messagebox.showerror('불러오기 실패', f'{e}')
            return
        self._apply_session_dict(session, f'📂 이어하기: {os.path.basename(path)}')

    def _apply_session_dict(self, session, source_label):
        """세션 dict(파일로 연 것이든, 🗂 프로젝트 관리에서 불러온 것이든)를 화면에
        복원한다. _resume_session과 프로젝트 관리 "불러오기"가 공유하는 실제 복원
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
        self.lbl_ai_status.config(text='\n'.join(msgs), fg=ACCENT_DANGER if n_row_fail else ACCENT_SUCCESS)
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
        def __init__(self):
            self.shared_api = {'api_key': '', 'model': '', 'provider': 'nvidia'}

    root = tk.Tk()
    root.title('승강로 CAD 도면 분석 (단독 실행)')
    root.geometry('1480x1040')
    root.configure(bg=CANVAS_BG)

    frame = tk.Frame(root, bg=CANVAS_BG)
    frame.pack(fill='both', expand=True)

    mod = ElevatorShaftReviewModule(frame, _FakeMainApp())
    mod.build_ui()

    root.mainloop()
