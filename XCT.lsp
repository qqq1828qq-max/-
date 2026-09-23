;;; ===========================================================================
;;; [XCT] Xclip Trim - trim (xclipped) blocks to a drawing frame
;;;
;;; For blocks that are XCLIPped so only part of them shows inside a drawing
;;; frame (title block): explodes the block(s) and keeps ONLY the geometry inside the
;;; frame. Curves that cross the frame are cut exactly at the frame line, as
;;; if TRIM had been run on every one of them; everything outside is deleted.
;;;
;;;   XCT  1) Pick the frame:
;;;           - click the frame's border line - a closed polyline, either loose
;;;             or INSIDE a title-block block (the nested border is used)
;;;           - click any other object / block  -> its bounding box is used
;;;           - Enter / "W"                     -> pick two corners
;;;        2) Select the block(s) to trim (the frame block itself is ignored).
;;;
;;; How objects are handled
;;;   - LINE / ARC / CIRCLE / ELLIPSE / LWPOLYLINE : cut exactly at the frame
;;;     (a polyline stays a polyline, arcs stay arcs, circles become arcs)
;;;   - 2D/3D POLYLINE : exploded to lines/arcs, then cut
;;;   - SPLINE : the kept part is rebuilt as a fitted spline (close approx.)
;;;   - Nested blocks : exploded too (all levels), then cut the same way
;;;   - Everything else (TEXT, MTEXT, HATCH, DIMENSION, ...) : kept if its
;;;     centre is inside the frame, deleted otherwise (not cut)
;;;   - Visible attributes become TEXT; invisible ones are dropped
;;;   - Objects on layer 0 / ByBlock take the block's layer, colour, linetype
;;;     and lineweight, so the result looks the same as the block did.
;;;
;;; Works in the current space, on 2D (plan-view) geometry. One UNDO reverts
;;; the whole run. Uses the shared _AuthCheck from MM.lsp.
;;; ===========================================================================

(vl-load-com)

;;; [Access control] - shared with MM.lsp (same safety-net stub as sn.lsp)
(if (not (boundp '_AuthCheck))
  (defun _AuthCheck ()
    (alert (strcat
      "Authentication Not Ready\n"
      "MM (MyTools palette) has not been loaded in this session.\n"
      "Please load/run MM first, then retry this command."))
    nil
  )
)

(setq *xct-pfuzz* 1e-7)   ; parameter tolerance

;;; ===========================================================================
;;; [1] Start / end / error handling
;;; ===========================================================================
(defun _XCT-Begin ()
  (setq *xct-doc* (vla-get-ActiveDocument (vlax-get-acad-object)))
  (setq *xct-oldvars* (mapcar '(lambda (v) (cons v (getvar v))) '("CMDECHO")))
  (setvar "CMDECHO" 0)
  (setq *xct-olderr* *error*
        *error*      _XCT-Error
        *xct-tmp*    nil
        ;; sn.lsp's auto-Q reactor queues every new line/arc/polyline and tags
        ;; it as a Q object when the next command ends. The pieces made here
        ;; are not new drawing work, so the queue is put back as it was.
        *xct-qsave*  *Q-pending*)
  (vla-StartUndoMark *xct-doc*)
)

(defun _XCT-End ()
  (if (and *xct-tmp* (not (vlax-erased-p *xct-tmp*)))
    (vl-catch-all-apply 'vla-Delete (list *xct-tmp*))
  )
  (setq *xct-tmp* nil)
  (setq *Q-pending* *xct-qsave*)
  (foreach pair *xct-oldvars* (setvar (car pair) (cdr pair)))
  (setq *error* *xct-olderr*)
  (vl-catch-all-apply 'vla-EndUndoMark (list *xct-doc*))
)

(defun _XCT-Error (msg)
  (if (not (wcmatch (strcase msg) "*BREAK*,*CANCEL*,*EXIT*"))
    (princ (strcat "\n[XCT] Error: " msg))
  )
  (_XCT-End)
  (princ)
)

;;; ===========================================================================
;;; [2] Small helpers
;;; ===========================================================================
(defun _XCT-Tan (a) (/ (sin a) (cos a)))

(defun _XCT-BBox (o / r ll ur)
  (setq r (vl-catch-all-apply 'vla-GetBoundingBox (list o 'll 'ur)))
  (if (and (not (vl-catch-all-error-p r)) ll ur)
    (list (vlax-safearray->list ll) (vlax-safearray->list ur))
  )
)

;;; the space new objects go to (same space as the selected blocks)
(defun _XCT-Space ()
  (if (= (vla-get-ActiveSpace *xct-doc*) acModelSpace)
    (vla-get-ModelSpace *xct-doc*)
    (vla-get-PaperSpace *xct-doc*)
  )
)

;;; entget list without identity / ownership codes, ready for entmake
(defun _XCT-CleanEd (ed)
  (vl-remove-if '(lambda (x) (member (car x) '(-1 330 5 102 360))) ed)
)

;;; common entity properties (for building a different entity type)
(defun _XCT-Props (ed)
  (vl-remove nil (mapcar '(lambda (c) (assoc c ed)) '(8 6 62 420 370 48 60)))
)

(defun _XCT-Subst (ed pairs)
  (foreach p pairs
    (setq ed (if (assoc (car p) ed) (subst p (assoc (car p) ed) ed) (append ed (list p))))
  )
  ed
)

(defun _XCT-CopyProps (src dst)
  (foreach f '((vla-get-Layer vla-put-Layer)
               (vla-get-TrueColor vla-put-TrueColor)
               (vla-get-Linetype vla-put-Linetype)
               (vla-get-LinetypeScale vla-put-LinetypeScale)
               (vla-get-Lineweight vla-put-Lineweight))
    (vl-catch-all-apply
      '(lambda () (apply (cadr f) (list dst (apply (car f) (list src))))))
  )
)

;;; Layer 0 / ByBlock objects coming out of a block take the block's values,
;;; so the exploded result looks exactly like the block did.
(defun _XCT-Inherit (o par)
  (vl-catch-all-apply
    '(lambda ()
       (if (= (strcase (vla-get-Layer o)) "0")
         (vla-put-Layer o (vla-get-Layer par)))
       (if (= (vla-get-Color o) acByBlock)
         (vla-put-TrueColor o (vla-get-TrueColor par)))
       (if (= (strcase (vla-get-Linetype o)) "BYBLOCK")
         (vla-put-Linetype o (vla-get-Linetype par)))
       (if (= (vla-get-Lineweight o) acLnWtByBlock)
         (vla-put-Lineweight o (vla-get-Lineweight par)))
     )
  )
)

;;; ===========================================================================
;;; [3] Frame (boundary) polygon
;;; ===========================================================================
;;; Points along a curve (in its own coordinates) - dense enough for arcs
(defun _XCT-Sample (e / sp ep n i step pts)
  (setq sp (vlax-curve-getStartParam e)
        ep (vlax-curve-getEndParam e))
  (setq n (if (wcmatch (cdr (assoc 0 (entget e))) "*POLYLINE")
            (* 16 (max 1 (fix (+ 0.5 (- ep sp)))))
            128))
  (setq step (/ (- ep sp) n) i 0)
  (repeat n
    (setq pts (cons (vlax-curve-getPointAtParam e (+ sp (* i step))) pts)
          i   (1+ i))
  )
  (reverse pts)
)

;;; block coordinates -> WCS with the matrix from nentsel
(defun _XCT-Xform (p m)
  (mapcar '+
    (mapcar '(lambda (a) (* (car p) a)) (car m))
    (mapcar '(lambda (a) (* (cadr p) a)) (cadr m))
    (mapcar '(lambda (a) (* (caddr p) a)) (caddr m))
    (cadddr m))
)

(defun _XCT-RectPts (bb)
  (list (car bb)
        (list (car (cadr bb)) (cadr (car bb)))
        (cadr bb)
        (list (car (car bb)) (cadr (cadr bb))))
)

;;; temporary closed LWPOLYLINE through WCS points (used for IntersectWith)
(defun _XCT-TempPoly (pts)
  (if (entmake
        (append
          (list '(0 . "LWPOLYLINE") '(100 . "AcDbEntity") '(8 . "0")
                '(100 . "AcDbPolyline") (cons 90 (length pts)) '(70 . 1))
          (mapcar '(lambda (p) (list 10 (car p) (cadr p))) pts)))
    (setq *xct-tmp* (vlax-ename->vla-object (entlast)))
  )
)

(defun _XCT-ClosedCurve-p (e / typ)
  (setq typ (cdr (assoc 0 (entget e))))
  (and (wcmatch typ "LWPOLYLINE,POLYLINE,CIRCLE,ELLIPSE,SPLINE")
       (vlax-curve-isClosed e))
)

;;; picking a heavy polyline returns one of its VERTEX entities
(defun _XCT-Vertex->Pline (e)
  (if (= (cdr (assoc 0 (entget e))) "VERTEX")
    (cdr (assoc 330 (entget e)))
    e
  )
)

;;; Ask for the frame. Returns (boundaryObj polygonPts frameEname) or nil.
(defun _XCT-GetFrame (/ sel done e ed m owner bb p1 p2 pts frameEnt)
  (while (not done)
    (setvar "ERRNO" 0)
    (initget "Window")
    (setq sel (nentsel "\nPick frame border line (closed polyline / frame block) or [Window] <Window>: "))
    (if (and (null sel) (= (getvar "ERRNO") 7))
      (princ "\n  Nothing there - try again.")
      (setq done T)
    )
  )
  (cond
    ;; ---- two corners
    ((or (null sel) (= sel "Window"))
     (if (and (setq p1 (getpoint "\nFrame first corner: "))
              (setq p2 (getcorner p1 "\nOpposite corner: ")))
       (progn
         (setq p1 (trans p1 1 0) p2 (trans p2 1 0))
         (setq pts (_XCT-RectPts
                     (list (list (min (car p1) (car p2)) (min (cadr p1) (cadr p2)))
                           (list (max (car p1) (car p2)) (max (cadr p1) (cadr p2))))))
       )
     )
    )
    ;; ---- picked inside a block (title-block border, nested)
    ((= (length sel) 4)
     ;; owners list runs innermost -> outermost; the outermost is the
     ;; frame block actually placed in the drawing
     (setq e (_XCT-Vertex->Pline (car sel)) m (caddr sel)
           owner (last (last sel)) frameEnt owner)
     (if (_XCT-ClosedCurve-p e)
       (setq pts (mapcar '(lambda (p) (_XCT-Xform p m)) (_XCT-Sample e)))
       (if (setq bb (_XCT-BBox (vlax-ename->vla-object owner)))
         (setq pts (_XCT-RectPts bb)))
     )
    )
    ;; ---- picked a top-level object (or an attribute of a block)
    (T
     (setq e (_XCT-Vertex->Pline (car sel)) ed (entget e))
     (if (= (cdr (assoc 0 ed)) "ATTRIB") (setq e (cdr (assoc 330 ed))))
     (setq frameEnt e)
     (if (_XCT-ClosedCurve-p e)
       (setq pts (_XCT-Sample e))
       (if (setq bb (_XCT-BBox (vlax-ename->vla-object e)))
         (setq pts (_XCT-RectPts bb)))
     )
    )
  )
  (if (and pts (> (length pts) 2) (_XCT-TempPoly pts))
    (list *xct-tmp* (mapcar '(lambda (p) (list (car p) (cadr p))) pts) frameEnt)
  )
)

;;; point on (or within tolerance of) a polygon edge
(defun _XCT-OnEdge (p poly tol / a b d ab ap u q hit)
  (setq b (last poly))
  (foreach a poly
    (if (not hit)
      (progn
        (setq ab (mapcar '- b a) ap (mapcar '- (list (car p) (cadr p)) a))
        (setq d (+ (* (car ab) (car ab)) (* (cadr ab) (cadr ab))))
        (setq u (if (> d 0.0) (/ (+ (* (car ap) (car ab)) (* (cadr ap) (cadr ab))) d) 0.0))
        (setq u (max 0.0 (min 1.0 u)))
        (setq q (mapcar '+ a (mapcar '(lambda (x) (* u x)) ab)))
        (if (< (distance q (list (car p) (cadr p))) tol) (setq hit T))
      )
    )
    (setq b a)
  )
  hit
)

;;; point in polygon (ray casting); points ON the frame line count as inside
(defun _XCT-Inside (p poly / x y in a b)
  (if (_XCT-OnEdge p poly *xct-etol*)
    T
    (progn
      (setq x (car p) y (cadr p) b (last poly))
      (foreach a poly
        (if (and (/= (> (cadr a) y) (> (cadr b) y))
                 (< x (+ (car a) (/ (* (- (car b) (car a)) (- y (cadr a)))
                                    (- (cadr b) (cadr a))))))
          (setq in (not in))
        )
        (setq b a)
      )
      in
    )
  )
)

;;; ===========================================================================
;;; [4] Explode a block completely (all nesting levels)
;;; ===========================================================================
(defun _XCT-TextFrom (ed)
  (if (entmake
        (append '((0 . "TEXT"))
                (vl-remove nil (mapcar '(lambda (c) (assoc c ed))
                                       '(8 6 62 420 370 48 7 10 40 41 50 51 71 72 1 39)))
                (if (assoc 11 ed) (list (assoc 11 ed)))
                (if (assoc 74 ed) (list (cons 73 (cdr (assoc 74 ed)))))
                (if (assoc 210 ed) (list (assoc 210 ed)))))
    (vlax-ename->vla-object (entlast))
  )
)

;;; visible attribute values -> TEXT
(defun _XCT-AttribTexts (ref / out tx)
  (if (= (vla-get-HasAttributes ref) :vlax-true)
    (foreach a (vlax-invoke ref 'GetAttributes)
      (if (and (= (vla-get-Invisible a) :vlax-false)
               (setq tx (_XCT-TextFrom (entget (vlax-vla-object->ename a)))))
        (progn
          (_XCT-Inherit tx ref)
          (setq out (cons tx out))
        )
      )
    )
  )
  out
)

;;; fallback when the API explode refuses (e.g. some non-uniformly scaled
;;; blocks): explode a copy with the EXPLODE command
(defun _XCT-ExplodeCmd (ref / mark cp cpe e out qtmp)
  (setq mark (entlast))
  (setq cp (vla-Copy ref) cpe (vlax-vla-object->ename cp))
  (setq qtmp *Q-pending* *Q-pending* nil)   ; keep the Q reactor out of it
  (command "_.EXPLODE" cpe)
  (while (> (getvar "CMDACTIVE") 0) (command ""))
  (setq *Q-pending* qtmp)
  (if (entget cpe)
    (progn (vl-catch-all-apply 'vla-Delete (list cp)) nil)
    (progn
      (setq e mark)
      (while (setq e (entnext e))
        (setq out (cons (vlax-ename->vla-object e) out))
      )
      (reverse out)
    )
  )
)

(defun _XCT-ExplodeOne (ref / r)
  (setq r (vl-catch-all-apply 'vlax-invoke (list ref 'Explode)))
  (if (and r (not (vl-catch-all-error-p r)))
    r
    (_XCT-ExplodeCmd ref)
  )
)

;;; returns the list of "leaf" objects (no block references left, unless one
;;; could not be exploded). Does NOT delete "ref" itself.
(defun _XCT-ExplodeAll (ref depth / parts out o nm ed flg sub)
  (setq parts (_XCT-ExplodeOne ref))
  (if parts
    (progn
      (setq out (_XCT-AttribTexts ref))
      (foreach o parts
        (_XCT-Inherit o ref)
        (setq nm (vla-get-ObjectName o))
        (cond
          ;; attribute definitions: only constant + visible ones show in a block
          ((= nm "AcDbAttributeDefinition")
           (setq ed (entget (vlax-vla-object->ename o)) flg (cdr (assoc 70 ed)))
           (if (and (= 2 (logand 2 flg)) (= 0 (logand 1 flg)))
             (if (setq sub (_XCT-TextFrom ed)) (setq out (cons sub out)))
           )
           (vla-Delete o)
          )
          ;; nested blocks
          ((and (member nm '("AcDbBlockReference" "AcDbMInsertBlock")) (< depth 30))
           (if (setq sub (_XCT-ExplodeAll o (1+ depth)))
             (progn (vla-Delete o) (setq out (append sub out)))
             (setq out (cons o out))
           )
          )
          (T (setq out (cons o out)))
        )
      )
      out
    )
  )
)

;;; ===========================================================================
;;; [5] Cut one curve at the frame
;;; ===========================================================================
;;; current curve's parameter range (set by _XCT-TrimCurve)
(defun _XCT-Norm (prm)
  (if *xct-closed*
    (while (> prm (+ *xct-ep* *xct-pfuzz*)) (setq prm (- prm *xct-period*)))
  )
  ;; clamp into the curve's range: getPointAtParam returns nil for a
  ;; parameter even a hair outside it (floating-point round-off)
  (max *xct-sp* (min prm *xct-ep*))
)

(defun _XCT-PtAt (o prm / p)
  (setq prm (_XCT-Norm prm))
  (cond
    ((vlax-curve-getPointAtParam o prm))
    ((equal prm *xct-sp* *xct-pfuzz*) (vlax-curve-getStartPoint o))
    ((equal prm *xct-ep* *xct-pfuzz*) (vlax-curve-getEndPoint o))
    (T (_XCT-Fail "no point on curve"))
  )
)

;;; sorted, de-duplicated parameters where the curve crosses the frame
(defun _XCT-CutParams (o bnd / v pts prm lst out)
  (setq v (vl-catch-all-apply 'vlax-invoke (list o 'IntersectWith bnd acExtendNone)))
  (if (and v (not (vl-catch-all-error-p v)))
    (progn
      (while v
        (setq pts (cons (list (car v) (cadr v) (caddr v)) pts)
              v   (cdddr v))
      )
      (foreach p pts
        (setq prm (vlax-curve-getParamAtPoint o (vlax-curve-getClosestPointTo o p)))
        (if prm
          (progn
            (if (and *xct-closed* (equal prm *xct-ep* *xct-pfuzz*)) (setq prm *xct-sp*))
            (if (or *xct-closed*
                    (and (> prm (+ *xct-sp* *xct-pfuzz*)) (< prm (- *xct-ep* *xct-pfuzz*))))
              (setq lst (cons prm lst))
            )
          )
        )
      )
      (foreach prm (vl-sort lst '<)
        (if (not (and out (equal prm (car out) *xct-pfuzz*)))
          (setq out (cons prm out))
        )
      )
    )
  )
  (reverse out)
)

;;; join neighbouring kept pieces -> list of (t0 t1) runs to keep
(defun _XCT-Merge (pieces / runs cur)
  (foreach p pieces
    (if (caddr p)
      (setq cur (if cur (list (car cur) (cadr p)) (list (car p) (cadr p))))
      (if cur (setq runs (cons cur runs) cur nil))
    )
  )
  (if cur (setq runs (cons cur runs)))
  (setq runs (reverse runs))
  ;; closed curve: the last run continues into the first one
  (if (and *xct-closed* (> (length runs) 1)
           (caddr (car pieces)) (caddr (last pieces)))
    (setq runs (cons (list (car (last runs)) (+ (cadr (car runs)) *xct-period*))
                     (cdr (reverse (cdr (reverse runs))))))
  )
  runs
)

;;; ----- build one kept piece (t0 < t1, t1 may run past the end on closed curves)
(defun _XCT-LWPiece (o ed t0 t1 / bul nrm cur k nxt b out)
  (setq bul (mapcar 'cdr (vl-remove-if-not '(lambda (x) (= (car x) 42)) ed))
        nrm (cond ((cdr (assoc 210 ed))) ('(0.0 0.0 1.0)))
        cur t0)
  (while (< cur (- t1 1e-9))
    (setq k   (fix (+ cur 1e-9))
          nxt (min (+ k 1) t1)
          b   (nth (rem k (length bul)) bul))
    ;; partial arc segment: bulge = tan(theta/4), theta scales with the fraction
    (setq b (if (equal b 0.0 1e-12) 0.0 (_XCT-Tan (* (- nxt cur) (atan b)))))
    (setq out (cons (list (trans (_XCT-PtAt o cur) 0 nrm) b) out)
          cur nxt)
  )
  (setq out (reverse (cons (list (trans (_XCT-PtAt o t1) 0 nrm) 0.0) out)))
  (entmake
    (append
      (list '(0 . "LWPOLYLINE") '(100 . "AcDbEntity"))
      (_XCT-Props ed)
      (list '(100 . "AcDbPolyline") (cons 90 (length out))
            (cons 70 (logand (cdr (assoc 70 ed)) 128)))
      (vl-remove nil (list (assoc 43 ed) (assoc 38 ed) (assoc 39 ed)))
      (apply 'append
        (mapcar '(lambda (v) (list (list 10 (car (car v)) (cadr (car v))) (cons 42 (cadr v))))
                out))
      (list (cons 210 nrm))))
)

(defun _XCT-SplinePiece (o t0 t1 / n i flat arr d0 d1 new pts)
  (setq n 24 i 0)
  (repeat (1+ n)
    (setq pts (cons (_XCT-PtAt o (+ t0 (* i (/ (- t1 t0) n)))) pts) i (1+ i))
  )
  (setq pts (reverse pts) flat (apply 'append pts))
  (setq arr (vlax-make-safearray vlax-vbDouble (cons 0 (1- (length flat)))))
  (vlax-safearray-fill arr flat)
  (setq d0 (vlax-curve-getFirstDeriv o (_XCT-Norm t0))
        d1 (vlax-curve-getFirstDeriv o (_XCT-Norm t1)))
  (setq new (vl-catch-all-apply 'vla-AddSpline
              (list (_XCT-Space) arr (vlax-3d-point d0) (vlax-3d-point d1))))
  (if (vl-catch-all-error-p new)
    ;; tangents unusable -> polyline through the same points
    (setq new (vla-AddLightWeightPolyline (_XCT-Space)
                (vlax-safearray-fill
                  (vlax-make-safearray vlax-vbDouble (cons 0 (1- (* 2 (length pts)))))
                  (apply 'append (mapcar '(lambda (p) (list (car p) (cadr p))) pts)))))
  )
  (_XCT-CopyProps o new)
)

(defun _XCT-MakePiece (o t0 t1 / typ ed twoPi)
  (setq typ   (vla-get-ObjectName o)
        ed    (_XCT-CleanEd (entget (vlax-vla-object->ename o)))
        twoPi (* 2.0 pi))
  (cond
    ((= typ "AcDbLine")
     (entmake (_XCT-Subst ed (list (cons 10 (_XCT-PtAt o t0)) (cons 11 (_XCT-PtAt o t1))))))
    ((= typ "AcDbArc")          ; arc parameter = angle
     (entmake (_XCT-Subst ed (list (cons 50 (rem t0 twoPi)) (cons 51 (rem t1 twoPi))))))
    ((= typ "AcDbCircle")       ; circle piece -> arc
     (entmake (append '((0 . "ARC")) (_XCT-Props ed)
                      (vl-remove nil (list (assoc 39 ed) (assoc 10 ed) (assoc 40 ed) (assoc 210 ed)))
                      (list (cons 50 (rem t0 twoPi)) (cons 51 (rem t1 twoPi))))))
    ((= typ "AcDbEllipse")      ; ellipse parameter = eccentric angle (41/42)
     (entmake (_XCT-Subst ed (list (cons 41 (rem t0 twoPi)) (cons 42 (rem t1 twoPi))))))
    ((= typ "AcDbPolyline") (_XCT-LWPiece o ed t0 t1))
    ((= typ "AcDbSpline")   (_XCT-SplinePiece o t0 t1))
  )
)

;;; returns 'keep / 'trim / 'del
(defun _XCT-TrimCurve (o bnd poly / bb)
  (setq *xct-sp*     (vlax-curve-getStartParam o)
        *xct-ep*     (vlax-curve-getEndParam o)
        *xct-closed* (vlax-curve-isClosed o))
  (if (and (numberp *xct-sp*) (numberp *xct-ep*) (> *xct-ep* *xct-sp*))
    (_XCT-TrimCurve2 o bnd poly)
    ;; degenerate curve (no parameter range) -> judged by its centre, like text
    (progn
      (setq bb (_XCT-BBox o))
      (_XCT-KeepByCentre o poly (car bb) (cadr bb))
    )
  )
)

(defun _XCT-TrimCurve2 (o bnd poly / cuts bounds pieces runs t0 t1)
  (setq *xct-period* (- *xct-ep* *xct-sp*))
  (setq cuts (_XCT-CutParams o bnd))
  (setq bounds (if *xct-closed*
                 (if cuts (append cuts (list (+ (car cuts) *xct-period*)))
                          (list *xct-sp* *xct-ep*))
                 (append (list *xct-sp*) cuts (list *xct-ep*))))
  (while (cdr bounds)
    (setq t0 (car bounds) t1 (cadr bounds))
    (setq pieces (cons (list t0 t1 (_XCT-Inside (_XCT-PtAt o (/ (+ t0 t1) 2.0)) poly)) pieces))
    (setq bounds (cdr bounds))
  )
  (setq pieces (reverse pieces))
  (setq runs (_XCT-Merge pieces))
  (cond
    ((null runs) (vla-Delete o) 'del)
    ((vl-every 'caddr pieces) 'keep)          ; nothing crosses the frame
    (T
     (foreach r runs (_XCT-MakePiece o (car r) (cadr r)))
     (vla-Delete o)
     'trim
    )
  )
)

;;; deliberate error, caught by _XCT-SafeProcess
(defun _XCT-Fail (msg)
  (setq *xct-failmsg* msg)
  (/ 1 0)
)

;;; _XCT-Process guarded: if anything goes wrong on one object, whatever was
;;; half-made for it is removed, the object itself is left untouched, and
;;; the run carries on. Returns 'keep / 'trim / 'del / 'err.
(defun _XCT-SafeProcess (o bnd poly bmin bmax / mark r e typ)
  (setq mark (entlast) *xct-failmsg* nil)
  (setq typ (vl-catch-all-apply 'vla-get-ObjectName (list o)))
  (setq r (vl-catch-all-apply '_XCT-Process (list o bnd poly bmin bmax)))
  (if (vl-catch-all-error-p r)
    (progn
      (setq e mark)
      (while (setq e (entnext e)) (entdel e))
      (if (not *xct-lasterr*)
        (setq *xct-lasterr*
          (strcat (if (vl-catch-all-error-p typ) "?" typ) ": "
                  (cond (*xct-failmsg*) ((vl-catch-all-error-message r))))))
      'err
    )
    r
  )
)

;;; ===========================================================================
;;; [6] Decide one object: keep / cut / delete
;;; ===========================================================================
(defun _XCT-Process (o bnd poly bmin bmax / typ bb ll ur parts res r p)
  (setq typ (vla-get-ObjectName o) bb (_XCT-BBox o))
  (cond
    ;; no extents (xline, ray, empty text ...) -> nothing sensible to keep
    ((null bb) (vl-catch-all-apply 'vla-Delete (list o)) 'del)
    ;; completely clear of the frame's box -> delete
    ((progn (setq ll (car bb) ur (cadr bb))
            (or (> (car ll) (car bmax)) (> (cadr ll) (cadr bmax))
                (< (car ur) (car bmin)) (< (cadr ur) (cadr bmin))))
     (vla-Delete o) 'del)
    ;; cuttable curves
    ((member typ '("AcDbLine" "AcDbArc" "AcDbCircle" "AcDbEllipse" "AcDbPolyline" "AcDbSpline"))
     (_XCT-TrimCurve o bnd poly))
    ;; heavy polylines -> lines/arcs, then cut each
    ((member typ '("AcDb2dPolyline" "AcDb3dPolyline"))
     (setq parts (vl-catch-all-apply 'vlax-invoke (list o 'Explode)))
     (if (or (null parts) (vl-catch-all-error-p parts))
       (_XCT-KeepByCentre o poly ll ur)
       (progn
         (vla-Delete o)
         (setq res 'del)
         (foreach p parts
           (setq r (_XCT-Process p bnd poly bmin bmax))
           (if (/= r 'del) (setq res 'trim))
         )
         res
       )
     )
    )
    ;; text, hatch, dimension, ... -> by centre point
    (T (_XCT-KeepByCentre o poly ll ur))
  )
)

(defun _XCT-KeepByCentre (o poly ll ur)
  (if (not (and ll ur)) (_XCT-Fail "no extents"))
  (if (_XCT-Inside (mapcar '(lambda (a b) (/ (+ a b) 2.0)) ll ur) poly)
    'keep
    (progn (vla-Delete o) 'del)
  )
)

;;; ===========================================================================
;;; [7] XCT command
;;; ===========================================================================
(defun c:XCT (/ frame bnd poly frameEnt bmin bmax ss i ref atoms r o
                nBlk nKeep nTrim nDel nFail nErr)
  (if (_AuthCheck)
    (progn
      (_XCT-Begin)
      (if (not (setq frame (_XCT-GetFrame)))
        (princ "\n[XCT] No frame - cancelled.")
        (progn
          (setq bnd (car frame) poly (cadr frame) frameEnt (caddr frame))
          (setq bmin (list (apply 'min (mapcar 'car poly)) (apply 'min (mapcar 'cadr poly)))
                bmax (list (apply 'max (mapcar 'car poly)) (apply 'max (mapcar 'cadr poly))))
          ;; "on the frame line" tolerance, relative to the frame size
          (setq *xct-etol* (* 1e-6 (max 1.0 (distance bmin bmax))))
          (princ "\nSelect block(s) to trim to the frame: ")
          (setq ss (ssget '((0 . "INSERT"))))
          (if (and ss frameEnt) (ssdel frameEnt ss))
          (if (or (null ss) (= (sslength ss) 0))
            (princ "\n[XCT] No blocks selected.")
            (progn
              (setq i 0 nBlk 0 nKeep 0 nTrim 0 nDel 0 nFail 0 nErr 0 *xct-lasterr* nil)
              (repeat (sslength ss)
                (setq ref (vlax-ename->vla-object (ssname ss i)))
                (setq atoms (vl-catch-all-apply '_XCT-ExplodeAll (list ref 0)))
                (if (vl-catch-all-error-p atoms)
                  (progn
                    (if (not *xct-lasterr*)
                      (setq *xct-lasterr* (strcat "explode: " (vl-catch-all-error-message atoms))))
                    (setq atoms nil)
                  )
                )
                (if atoms
                  (progn
                    (vla-Delete ref)
                    (setq nBlk (1+ nBlk))
                    (foreach o atoms
                      (setq r (_XCT-SafeProcess o bnd poly bmin bmax))
                      (cond ((= r 'keep) (setq nKeep (1+ nKeep)))
                            ((= r 'trim) (setq nTrim (1+ nTrim)))
                            ((= r 'err)  (setq nErr (1+ nErr)))
                            (T (setq nDel (1+ nDel))))
                    )
                  )
                  (setq nFail (1+ nFail))
                )
                (setq i (1+ i))
              )
              (princ (strcat "\n[XCT] Done. " (itoa nBlk) " block(s): "
                             (itoa nKeep) " kept, " (itoa nTrim) " cut at frame, "
                             (itoa nDel) " deleted (outside)."
                             (if (> nFail 0)
                               (strcat " " (itoa nFail) " block(s) could not be exploded - left as is.")
                               "")
                             (if (> nErr 0)
                               (strcat "\n[XCT] " (itoa nErr) " object(s) could not be cut - left uncut.")
                               "")
                             (if *xct-lasterr*
                               (strcat "\n[XCT] First problem: " *xct-lasterr*)
                               "")))
            )
          )
        )
      )
      (_XCT-End)
    )
  )
  (princ)
)

(princ "\n[XCT] Loaded. XCT = trim (xclipped) blocks to a drawing frame.")
(princ)
