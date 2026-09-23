;;; ===========================================================================
;;; [DXFS] DXF drawing splitter
;;;
;;; Splits a sheet that holds several drawings into separate .dxf files.
;;;
;;;   DXFS  - Pick the two corners of one drawing. A closed rectangle is drawn
;;;           on layer "DXF" and every object lying COMPLETELY inside it is
;;;           written to its own .dxf file. Repeats until you press Enter.
;;;   DXFSA - Re-export every "DXF" rectangle in the current space in one go
;;;           (e.g. after editing the drawings). Each rectangle remembers the
;;;           file name it was exported under, so the same files are rewritten.
;;;
;;; Output folder : <drawing folder>\<drawing name>_DXF\
;;; Default names : <drawing name>_01.dxf, _02.dxf, ... (Enter to accept, or
;;;                 type any name)
;;;
;;; Notes
;;;   - Objects on layer "DXF" (the split rectangles themselves) are never
;;;     exported.
;;;   - "Inside" = the object's bounding box is fully inside the rectangle
;;;     (same rule as a window selection), checked in the database, so it
;;;     works even when the area is zoomed off-screen.
;;;   - XCLIPped blocks are judged by the part that is actually VISIBLE
;;;     (clip boundary), not by the whole block. In the DXF they can be
;;;       Keep : written as the block + its xclip (AutoCAD shows it clipped)
;;;       Trim : written as loose objects cut at the clip boundary, so any
;;;              DXF viewer / CAM tool shows exactly what you see (needs
;;;              XCT.lsp loaded). The drawing itself is not changed.
;;;     You are asked once per DXFS / DXFSA run, when such a block is found.
;;;   - Layer "DXF" is created red and non-plotting.
;;;   - Uses the shared _AuthCheck from MM.lsp, like the rest of the suite.
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

(setq *dxfs-layer* "DXF")          ; layer for the split rectangles
(setq *dxfs-app*   "MY_DXF_DATA")  ; XData app name - stores the file name
(setq *dxfs-fuzz*  1e-6)           ; tolerance for the "inside" test

;;; ===========================================================================
;;; [1] Start / end / error handling
;;; ===========================================================================
(defun _DXFS-Begin ()
  (setq *dxfs-doc* (vla-get-ActiveDocument (vlax-get-acad-object)))
  (setq *dxfs-oldvars*
    (mapcar '(lambda (v) (cons v (getvar v))) '("CMDECHO" "FILEDIA")))
  (setvar "CMDECHO" 0)
  (setvar "FILEDIA" 0)
  (setq *dxfs-olderr*   *error*
        *error*         _DXFS-Error
        *dxfs-clipcache* nil      ; ((ename . visible-bbox) ...) for this run
        *dxfs-cliprun*   nil      ; clip mode already asked in this run?
        ;; sn.lsp's auto-Q reactor must not tag the temporary objects made
        ;; here (clip outlines, trimmed pieces) - queue restored at the end
        *dxfs-qsave*     *Q-pending*)
  (vla-StartUndoMark *dxfs-doc*)
)

(defun _DXFS-End ()
  (foreach e *dxfs-temps* (if (entget e) (entdel e)))
  (setq *dxfs-temps* nil)
  (setq *Q-pending* *dxfs-qsave*)
  (foreach pair *dxfs-oldvars* (setvar (car pair) (cdr pair)))
  (setq *error* *dxfs-olderr*)
  (vl-catch-all-apply 'vla-EndUndoMark (list *dxfs-doc*))
)

(defun _DXFS-Error (msg)
  (if (not (wcmatch (strcase msg) "*BREAK*,*CANCEL*,*EXIT*"))
    (princ (strcat "\n[DXFS] Error: " msg))
  )
  (_DXFS-End)
  (princ)
)

;;; ===========================================================================
;;; [2] Layer / rectangle helpers
;;; ===========================================================================
(defun _DXFS-EnsureLayer (/ layers lay)
  (setq layers (vla-get-Layers *dxfs-doc*))
  (if (tblsearch "LAYER" *dxfs-layer*)
    (setq lay (vla-Item layers *dxfs-layer*))
    (progn
      (setq lay (vla-Add layers *dxfs-layer*))
      (vla-put-Color lay 1)
    )
  )
  (vl-catch-all-apply 'vla-put-Plottable (list lay :vlax-false))
  lay
)

;;; Keep sn.lsp's auto-Q reactor away from our rectangle: that reactor tags
;;; every new LWPOLYLINE as a Q object (lineweight 0.50 + MY_Q_DATA) when the
;;; next command ends, which would make the split frame show up in Q2/QT.
(defun _DXFS-UnQueue (ent)
  (if (and (boundp '*Q-pending*) *Q-pending*)
    (setq *Q-pending*
      (vl-remove-if
        '(lambda (x) (or (equal x ent) (and (listp x) (member ent x))))
        *Q-pending*))
  )
)

;;; pmin / pmax are WCS (x y) lists
(defun _DXFS-MakeRect (pmin pmax / ent)
  (if (entmake
        (list '(0 . "LWPOLYLINE") '(100 . "AcDbEntity")
              (cons 8 *dxfs-layer*) '(62 . 256)
              '(100 . "AcDbPolyline") '(90 . 4) '(70 . 1)
              (cons 10 (list (car pmin) (cadr pmin)))
              (cons 10 (list (car pmax) (cadr pmin)))
              (cons 10 (list (car pmax) (cadr pmax)))
              (cons 10 (list (car pmin) (cadr pmax)))))
    (progn
      (setq ent (entlast))
      (_DXFS-UnQueue ent)
      ent
    )
  )
)

;;; WCS min/max (x y) of any entity via its bounding box, nil if it has none
(defun _DXFS-BBox (ent / r ll ur)
  (setq r (vl-catch-all-apply 'vla-GetBoundingBox
            (list (vlax-ename->vla-object ent) 'll 'ur)))
  (if (and (not (vl-catch-all-error-p r)) ll ur)
    (list (vlax-safearray->list ll) (vlax-safearray->list ur))
  )
)

;;; Store / read the exported file name on the rectangle
(defun _DXFS-SetName (ent name / ed)
  (regapp *dxfs-app*)
  (setq ed (entget ent))
  (setq ed (vl-remove (assoc -3 ed) ed))
  (entmod (append ed (list (list -3 (list *dxfs-app* (cons 1000 name))))))
)

(defun _DXFS-GetName (ent / xd)
  (setq xd (assoc -3 (entget ent (list *dxfs-app*))))
  (if xd (cdr (assoc 1000 (cdr (assoc *dxfs-app* (cdr xd))))))
)

;;; ===========================================================================
;;; [3] XCLIPped blocks
;;; ===========================================================================
;;; run a command without sn.lsp's Q reactor picking up what it creates
(defun _DXFS-Cmd (lst)
  (setq *Q-pending* nil)
  (apply 'command lst)
  (while (> (getvar "CMDACTIVE") 0) (command ""))
  (setq *Q-pending* nil)
)

;;; T when the INSERT has an active XCLIP (spatial filter, display on)
(defun _DXFS-Clipped-p (e / xd d f)
  (and (= (cdr (assoc 0 (entget e))) "INSERT")
       (setq xd (cdr (assoc 360 (entget e))))
       (setq d (dictsearch xd "ACAD_FILTER"))
       (setq f (dictsearch (cdr (assoc -1 d)) "SPATIAL"))
       (/= 0 (cond ((cdr (assoc 71 f))) (1)))
  )
)

;;; WCS polyline of the clip boundary (XCLIP > generate Polyline). The
;;; caller deletes it.
(defun _DXFS-ClipPoly (e / mark new)
  (setq mark (entlast))
  (_DXFS-Cmd (list "_.XCLIP" e "" "_P"))
  (setq new (entlast))
  (if (not (eq new mark)) new)
)

;;; visible extents of a clipped block = its extents AND the clip boundary
(defun _DXFS-ClipBBox (e full / hit pl cb)
  (if (setq hit (assoc e *dxfs-clipcache*))
    (cdr hit)
    (progn
      (if (setq pl (_DXFS-ClipPoly e))
        (progn
          (setq cb (_DXFS-BBox pl))
          (entdel pl)
          (setq cb (list (list (max (car (car full)) (car (car cb)))
                               (max (cadr (car full)) (cadr (car cb))))
                         (list (min (car (cadr full)) (car (cadr cb)))
                               (min (cadr (cadr full)) (cadr (cadr cb))))))
        )
        (setq cb full)
      )
      (setq *dxfs-clipcache* (cons (cons e cb) *dxfs-clipcache*))
      cb
    )
  )
)

;;; Keep / Trim - asked once per run; the answer is remembered as default
(defun _DXFS-ClipMode (/ ans)
  (if (not *dxfs-cliprun*)
    (progn
      (if (not *dxfs-clipmode*) (setq *dxfs-clipmode* "Trim"))
      (initget "Keep Trim")
      (setq ans (getkword (strcat "\nXclipped block(s) found. Keep = block + xclip, "
                                  "Trim = cut at clip boundary. [Keep/Trim] <"
                                  *dxfs-clipmode* ">: ")))
      (if ans (setq *dxfs-clipmode* ans))
      (if (and (= *dxfs-clipmode* "Trim") (not (boundp '_XCT-ExplodeAll)))
        (progn
          (princ "\n[DXFS] XCT.lsp is not loaded - xclipped blocks are exported as Keep.")
          (setq *dxfs-clipmode* "Keep")
        )
      )
      (setq *dxfs-cliprun* T)
    )
  )
  *dxfs-clipmode*
)

;;; Trim mode: replace each clipped block in ss by a trimmed, exploded copy.
;;; The copies are temporary (recorded in *dxfs-temps*, removed after export).
(defun _DXFS-TrimClipped (ss / out i e pl pts bnd bmin bmax cp atoms e2 typ)
  (setq out (ssadd) i 0 *xct-doc* *dxfs-doc*)
  (repeat (sslength ss)
    (setq e (ssname ss i))
    (if (and (_DXFS-Clipped-p e) (setq pl (_DXFS-ClipPoly e)))
      (progn
        (setq pts  (mapcar '(lambda (p) (list (car p) (cadr p))) (_XCT-Sample pl))
              bnd  (vlax-ename->vla-object pl)
              bmin (list (apply 'min (mapcar 'car pts)) (apply 'min (mapcar 'cadr pts)))
              bmax (list (apply 'max (mapcar 'car pts)) (apply 'max (mapcar 'cadr pts)))
              *xct-etol* (* 1e-6 (max 1.0 (distance bmin bmax))))
        (setq cp (vla-Copy (vlax-ename->vla-object e)))
        (if (setq atoms (_XCT-ExplodeAll cp 0))
          (progn
            (vla-Delete cp)
            (foreach o atoms (_XCT-SafeProcess o bnd pts bmin bmax))
            ;; everything created after the clip outline = the trimmed pieces
            (setq e2 pl)
            (while (setq e2 (entnext e2))
              (setq typ (cdr (assoc 0 (entget e2))))
              (if (not (member typ '("VERTEX" "SEQEND" "ATTRIB")))
                (progn
                  (ssadd e2 out)
                  (setq *dxfs-temps* (cons e2 *dxfs-temps*))
                )
              )
            )
            (entdel pl)
          )
          (progn            ; could not explode -> export the block as it is
            (vla-Delete cp)
            (entdel pl)
            (ssadd e out)
          )
        )
      )
      (ssadd e out)
    )
    (setq i (1+ i))
  )
  (setq *Q-pending* nil)
  out
)

;;; ===========================================================================
;;; [4] Collect every object fully inside the rectangle (current space)
;;; ===========================================================================
(defun _DXFS-Collect (pmin pmax / ss out i e bb ll ur)
  (setq ss  (ssget "_X" (list (cons 410 (getvar "CTAB"))))
        out (ssadd)
        i   0)
  (if ss
    (repeat (sslength ss)
      (setq e (ssname ss i))
      (if (and (/= (strcase (cdr (assoc 8 (entget e)))) (strcase *dxfs-layer*))
               (setq bb (_DXFS-BBox e)))
        (progn
          ;; xclipped block overlapping the area: judge by its visible part
          (if (and (_DXFS-Clipped-p e)
                   (<= (car (car bb)) (car pmax)) (<= (cadr (car bb)) (cadr pmax))
                   (>= (car (cadr bb)) (car pmin)) (>= (cadr (cadr bb)) (cadr pmin)))
            (setq bb (_DXFS-ClipBBox e bb))
          )
          (setq ll (car bb) ur (cadr bb))
          (if (and (>= (car ll)  (- (car pmin)  *dxfs-fuzz*))
                   (>= (cadr ll) (- (cadr pmin) *dxfs-fuzz*))
                   (<= (car ur)  (+ (car pmax)  *dxfs-fuzz*))
                   (<= (cadr ur) (+ (cadr pmax) *dxfs-fuzz*)))
            (ssadd e out)
          )
        )
      )
      (setq i (1+ i))
    )
  )
  (if (> (sslength out) 0) out)
)

;;; ===========================================================================
;;; [5] File name helpers
;;; ===========================================================================
(defun _DXFS-BaseName ()
  (vl-filename-base (getvar "DWGNAME"))
)

(defun _DXFS-OutDir (/ dir)
  (setq dir (strcat (getvar "DWGPREFIX") (_DXFS-BaseName) "_DXF"))
  (if (not (vl-file-directory-p dir)) (vl-mkdir dir))
  (strcat dir "\\")
)

;;; Replace characters Windows does not allow in file names, drop ".dxf"
(defun _DXFS-CleanName (name / c)
  (foreach c '("\\" "/" ":" "*" "?" "\"" "<" ">" "|")
    (while (vl-string-search c name)
      (setq name (vl-string-subst "_" c name))
    )
  )
  (setq name (vl-string-trim " ." name))
  (if (wcmatch (strcase name) "*.DXF")
    (setq name (substr name 1 (- (strlen name) 4)))
  )
  name
)

;;; First <drawing>_NN not already used by a file or by a name in "taken"
(defun _DXFS-NextName (dir taken / n name)
  (setq n 1)
  (while
    (progn
      (setq name (strcat (_DXFS-BaseName) "_" (if (< n 10) "0" "") (itoa n)))
      (or (findfile (strcat dir name ".dxf"))
          (member (strcase name) (mapcar 'strcase taken)))
    )
    (setq n (1+ n))
  )
  name
)

;;; Ask for a file name (Enter = default). Confirms before overwriting.
(defun _DXFS-AskName (dir default / name ans done)
  (while (not done)
    (setq name (getstring T (strcat "\nDXF file name <" default ">: ")))
    (setq name (if (= name "") default (_DXFS-CleanName name)))
    (cond
      ((= name "") (princ "\n[DXFS] Invalid file name."))
      ((findfile (strcat dir name ".dxf"))
       (initget "Yes No")
       (setq ans (getkword (strcat "\n" name ".dxf already exists. Overwrite? [Yes/No] <No>: ")))
       (if (= ans "Yes") (setq done T))
      )
      (T (setq done T))
    )
  )
  name
)

;;; ===========================================================================
;;; [6] Write a selection set to a .dxf file (DXFOUT > Objects)
;;; ===========================================================================
(defun _DXFS-HasClipped (ss / i hit)
  (setq i 0)
  (repeat (sslength ss)
    (if (_DXFS-Clipped-p (ssname ss i)) (setq hit T))
    (setq i (1+ i))
  )
  hit
)

(defun _DXFS-Export (ss fname / ok)
  (if (and (_DXFS-HasClipped ss) (= (_DXFS-ClipMode) "Trim"))
    (setq ss (_DXFS-TrimClipped ss))
  )
  (setq ok (_DXFS-Write ss fname))
  ;; temporary trimmed pieces are only for the file
  (foreach e *dxfs-temps* (if (entget e) (entdel e)))
  (setq *dxfs-temps* nil)
  ok
)

(defun _DXFS-Write (ss fname)
  (if (findfile fname) (vl-file-delete fname))
  (if (findfile fname)
    (progn
      (princ (strcat "\n[DXFS] Cannot overwrite (file open elsewhere?): " fname))
      nil
    )
    (progn
      ;; file name -> Objects -> select -> finish selection; any prompt still
      ;; open afterwards (decimal accuracy etc.) gets its default via Enter
      (_DXFS-Cmd (list "_.DXFOUT" fname "_O" ss ""))
      (findfile fname)
    )
  )
)

;;; ===========================================================================
;;; [7] DXFS - pick areas one by one
;;; ===========================================================================
(defun c:DXFS (/ dir p1 p2 w1 w2 pmin pmax rect ss name fname cnt)
  (if (_AuthCheck)
    (progn
      (_DXFS-Begin)
      (_DXFS-EnsureLayer)
      (setq dir (_DXFS-OutDir) cnt 0)
      (princ (strcat "\n[DXFS] Output folder: " dir))
      (while (setq p1 (getpoint "\nFirst corner of drawing area (Enter=finish): "))
        (if (setq p2 (getcorner p1 "\nOpposite corner: "))
          (progn
            (setq w1   (trans p1 1 0)
                  w2   (trans p2 1 0)
                  pmin (list (min (car w1) (car w2)) (min (cadr w1) (cadr w2)))
                  pmax (list (max (car w1) (car w2)) (max (cadr w1) (cadr w2))))
            (setq ss (_DXFS-Collect pmin pmax))
            (if (not ss)
              (princ "\n[DXFS] No objects fully inside that area - skipped.")
              (progn
                (setq rect  (_DXFS-MakeRect pmin pmax))
                (princ (strcat "\n[DXFS] " (itoa (sslength ss)) " object(s) in area."))
                (setq name  (_DXFS-AskName dir (_DXFS-NextName dir nil))
                      fname (strcat dir name ".dxf"))
                (if (_DXFS-Export ss fname)
                  (progn
                    (if rect (_DXFS-SetName rect name))
                    (setq cnt (1+ cnt))
                    (princ (strcat "\n[DXFS] Saved: " fname))
                  )
                  (princ (strcat "\n[DXFS] Failed: " fname))
                )
              )
            )
          )
        )
      )
      (_DXFS-End)
      (princ (strcat "\n[DXFS] Done. " (itoa cnt) " DXF file(s) saved to " dir))
    )
  )
  (princ)
)

;;; ===========================================================================
;;; [8] DXFSA - re-export every "DXF" rectangle in the current space
;;; ===========================================================================
(defun c:DXFSA (/ dir rs i e bb items name taken used ss fname cnt skip)
  (if (_AuthCheck)
    (progn
      (_DXFS-Begin)
      (setq rs (ssget "_X" (list '(0 . "LWPOLYLINE") (cons 8 *dxfs-layer*)
                                 (cons 410 (getvar "CTAB")))))
      (if (not rs)
        (princ (strcat "\n[DXFS] No rectangles on layer \"" *dxfs-layer* "\" in this space. Use DXFS first."))
        (progn
          (setq dir (_DXFS-OutDir) cnt 0 skip 0 i 0 items '() used '())
          ;; (ent (xmin ymin) (xmax ymax))
          (repeat (sslength rs)
            (setq e (ssname rs i))
            (if (setq bb (_DXFS-BBox e))
              (setq items (cons (list e (car bb) (cadr bb)) items))
            )
            (setq i (1+ i))
          )
          ;; reading order: top row first, then left to right
          (setq items
            (vl-sort items
              '(lambda (a b)
                 (if (equal (cadr (caddr a)) (cadr (caddr b)) 1e-3)
                   (< (car (cadr a)) (car (cadr b)))
                   (> (cadr (caddr a)) (cadr (caddr b)))))))
          ;; names already remembered on rectangles are reserved first
          (setq taken (vl-remove nil (mapcar '(lambda (it) (_DXFS-GetName (car it))) items)))
          (foreach it items
            (setq ss (_DXFS-Collect (cadr it) (caddr it)))
            (if (not ss)
              (setq skip (1+ skip))
              (progn
                ;; no remembered name, or a copied rectangle carrying the same
                ;; name as one already exported in this run -> give it a new one
                (setq name (_DXFS-GetName (car it)))
                (if (or (not name) (member (strcase name) used))
                  (progn
                    (setq name (_DXFS-NextName dir taken))
                    (setq taken (cons name taken))
                    (_DXFS-SetName (car it) name)
                  )
                )
                (setq used  (cons (strcase name) used)
                      fname (strcat dir name ".dxf"))
                (if (_DXFS-Export ss fname)
                  (progn
                    (setq cnt (1+ cnt))
                    (princ (strcat "\n[DXFS] Saved: " fname " (" (itoa (sslength ss)) " objects)"))
                  )
                  (princ (strcat "\n[DXFS] Failed: " fname))
                )
              )
            )
          )
          (princ (strcat "\n[DXFS] Done. " (itoa cnt) " DXF file(s) saved to " dir
                         (if (> skip 0) (strcat ", " (itoa skip) " empty rectangle(s) skipped.") "")))
        )
      )
      (_DXFS-End)
    )
  )
  (princ)
)

(princ "\n[DXFS] Loaded. DXFS = split by picked areas, DXFSA = re-export all DXF rectangles.")
(princ)
