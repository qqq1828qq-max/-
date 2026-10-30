
;;; ===========================================================================
;;; [0] Authorization check
;;; ===========================================================================
;;; [Access control] - shared with MM.lsp (single source of truth).
;;; The real whitelist-check _AuthCheck is defined once in MM.lsp and
;;; loaded into the same AutoLISP session (MM auto-loads via S::STARTUP).
;;; This stub only kicks in as a safety net if MM.lsp was NOT loaded yet
;;; (e.g. this file was APPLOAD-ed by itself for isolated testing).
(if (not (boundp '_AuthCheck))
  (defun _AuthCheck ()
    (alert (strcat
      "Authentication Not Ready\n"
      "MM (MyTools palette) has not been loaded in this session.\n"
      "Please load/run MM first, then retry this command."))
    nil
  )
)

;;; ===========================================================================
;;; [1] Set Q XData tag
;;; ===========================================================================
(defun _SetQData (ename / exdata)
  (regapp "MY_Q_DATA")
  (setq exdata '((-3 ("MY_Q_DATA" (1000 . "Q_OBJECT")))))
  (entmod (append (entget ename) exdata))
)

;;; ===========================================================================
;;; [2] Q command - change layer + set Q tag
;;;
;;; Layer name / color number separated input
;;;   - Name: Korean/English/Number (e.g. "CABLE-1", "EL-1")
;;;   - Color: 1-255, Enter=7(white)
;;;   - Existing layer: color unchanged by default
;;; ===========================================================================
(defun c:Q (/ ss lay_name col_num col_input i ent obj pre doc layers newLayer)
  (if (_AuthCheck)
    (progn
      ;; Pre-selection check
      (setq pre (ssget "_:L"))
      (if pre
        (setq ss pre)
        (setq ss (ssget))
      )

      (if ss
        (progn
          ;; 1. Layer name input (T flag = allow spaces)
          (setq lay_name
            (getstring T (strcat
              "\n"
              "Layer name (existing or new): "
            ))
          )

          (if (and lay_name (/= lay_name ""))
            (progn
              ;; 2. Color number input
              ;;    Existing: keep or change / New: enter color
              (if (tblsearch "LAYER" lay_name)
                ;; Existing layer - keep or change color
                (progn
                  (setq col_input
                    (getstring (strcat
                      "\n  [" lay_name "] "
                      "Layer color number (1-255, Enter=keep current): "
                    ))
                  )
                  (if (or (not col_input) (= col_input ""))
                    (setq col_num -1) ; -1 = keep the current color
                    (progn
                      (setq col_num (atoi col_input))
                      (if (or (< col_num 1) (> col_num 255)) (setq col_num -1))
                    )
                  )
                )
                ;; New layer - input color (default 7)
                (progn
                  (setq col_input
                    (getstring (strcat
                      "\n  "
                      "Layer color number 1-255 (Enter=7/White): "
                    ))
                  )
                  (if (or (not col_input) (= col_input ""))
                    (setq col_num 7)
                    (progn
                      (setq col_num (atoi col_input))
                      (if (or (< col_num 1) (> col_num 255)) (setq col_num 7))
                    )
                  )
                )
              )

              ;; 3. Create or update layer
              ;; Uses ActiveX (vla-Add / vla-put-color) instead of the command
              ;; line - this sidesteps the "too few arguments" desync risk that
              ;; (command "-layer" ...) is prone to (its interactive prompt
              ;; sequence doesn't always line up 1:1 with the scripted args).
              (vl-load-com)
              (setq doc (vla-get-ActiveDocument (vlax-get-acad-object)))
              (setq layers (vla-get-Layers doc))
              (cond
                ;; New layer
                ((not (tblsearch "LAYER" lay_name))
                 (setq newLayer (vla-Add layers lay_name))
                 (vl-catch-all-apply 'vla-put-color (list newLayer col_num)))
                ;; Existing + change color
                ((and (tblsearch "LAYER" lay_name) (/= col_num -1))
                 (vl-catch-all-apply 'vla-put-color (list (vla-Item layers lay_name) col_num)))
                ;; Existing + keep color
              )

              ;; 4. Apply layer + Q tag to selected objects
              (setq i 0)
              (repeat (sslength ss)
                (setq ent (ssname ss i))
                (setq obj (vlax-ename->vla-object ent))
                (vla-put-layer obj lay_name)
                (vla-put-color obj 256)
                (vla-put-lineweight obj 50)
                (_SetQData ent)
                (setq i (1+ i))
              )
              (setvar "LWDISPLAY" 1)

              (princ (strcat
                "\n[" "Complete" "] "
                (itoa (sslength ss))
                " object(s) -> Layer [" lay_name "] "
                "have been changed."
              ))
            )
          )
        )
      )
    )
  )
  (princ)
)

;;; ===========================================================================
;;; [3] Q1 - Turn off every layer except the ones used by Q objects
;;; ===========================================================================
(defun c:Q1 (/ ss_Q doc layers usedLayers i ent lay curLayerName)
  (setq ss_Q (ssget "X" '((-3 ("MY_Q_DATA")))))
  (if ss_Q
    (progn
      (vl-load-com)
      (setq doc    (vla-get-ActiveDocument (vlax-get-acad-object)))
      (setq layers (vla-get-Layers doc))

      ;; collect the set of layer names actually used by Q objects
      (setq usedLayers '() i 0)
      (repeat (sslength ss_Q)
        (setq ent (ssname ss_Q i))
        (setq lay (cdr (assoc 8 (entget ent))))
        (if (not (member lay usedLayers)) (setq usedLayers (cons lay usedLayers)))
        (setq i (1+ i))
      )

      ;; the active layer can't be turned off, so switch to a Q layer first if needed
      (setq curLayerName (vla-get-Name (vla-get-ActiveLayer doc)))
      (if (not (member curLayerName usedLayers))
        (vla-put-ActiveLayer doc (vla-item layers (car usedLayers)))
      )

      ;; turn on layers used by Q objects, turn off every other layer
      (vlax-for lay-obj layers
        (if (member (vla-get-Name lay-obj) usedLayers)
          (vla-put-LayerOn lay-obj :vlax-true)
          (vla-put-LayerOn lay-obj :vlax-false)
        )
      )
      (princ (strcat "\n[Q] Layers containing Q " "object(s)" " are on; all other layers " "have been turned off."))
    )
    (princ (strcat "\n[" "Notice" "] " "There are no" " Q " "object(s)" " " "in the drawing."))
  )
  (princ)
)

;;; ===========================================================================
;;; [4] Q2 - Select all Q objects
;;; ===========================================================================
(defun c:Q2 (/ ss)
  (setq ss (ssget "X" '((-3 ("MY_Q_DATA")))))
  (if ss
    (progn
      (sssetfirst nil ss)
      (princ (strcat "\n[Q] " (itoa (sslength ss)) " object(s)" " Q " "have been selected."))
    )
    (princ (strcat "\n[" "Notice" "] There are no Q " "object(s)" "."))
  )
  (princ)
)

;;; ===========================================================================
;;; [5] Reactor - auto Q tag on draw
;;; ===========================================================================
;;; :vlr-objectAppended fires on EVERY new database object append, by ANY
;;; command or internal AutoCAD process (layers, regapps, table records -
;;; and, crucially, even the housekeeping objects AutoCAD itself creates
;;; while SAVING the drawing, not just user-drawn entities). At the moment
;;; this fires, the object is still mid-append/not fully committed, so
;;; touching it via entget/vla-*/entmod right there is unsafe and can throw
;;; spurious errors (e.g. "too few arguments") on completely unrelated
;;; operations like QSAVE. vl-catch-all-apply only guards against normal
;;; Lisp-level errors raised INSIDE the callback - it can't help if the
;;; object itself isn't in a safe state to touch yet, so wrapping alone
;;; isn't enough.
;;;
;;; Fix: do nothing risky inside the append reactor - just remember the
;;; entity name (cheap/safe). A separate command-ended reactor then does
;;; the actual tagging afterward, once the command (or save) has fully
;;; finished and every object is safely committed.
(setq *Q-pending* '())

;;; vlr-acdb-reactor callbacks (unlike vlr-object-reactor) take exactly TWO
;;; arguments: (reactor_object obj) - "obj" IS the database object/ename
;;; itself, not a parameter-list to unwrap. The previous version declared
;;; THREE required parameters (matching vlr-object-reactor's convention
;;; instead), so AutoCAD's 2-argument call was always one short - this is
;;; the actual, guaranteed-every-time source of "too few arguments" on
;;; every single append event (drawing, saving, anything).
(defun Q-collect (reactor-object obj)
  (vl-catch-all-apply
    '(lambda ()
       (if obj (setq *Q-pending* (cons obj *Q-pending*)))
     )
  )
)

(defun Q-process-pending (notifier-object parameter-list / ent obj pending)
  (vl-catch-all-apply
    '(lambda ()
       (setq pending *Q-pending*)
       (setq *Q-pending* '())
       (foreach ent pending
         (if (and ent (entget ent))
           (progn
             (setq obj (vlax-ename->vla-object ent))
             (if (member (vla-get-ObjectName obj)
                         '("AcDbLine" "AcDbPolyline" "AcDb2dPolyline" "AcDbArc" "AcDbCircle"))
               (progn
                 (vla-put-lineweight obj 50)
                 (vla-put-color obj 256)
                 (_SetQData ent)
               )
             )
           )
         )
       )
     )
  )
)

;;; Defensive cleanup before (re)registering: VLX files generally can't be
;;; cleanly unloaded from a live AutoCAD session (APPLOAD's unload doesn't
;;; reliably tear reactors down), so reloading this file after a recompile
;;; can otherwise leave an old reactor running side by side with the new
;;; one. This removes any reactor still registered under this session's
;;; variable names - including the old pre-split single-reactor name
;;; (Q-vlr) - before creating fresh ones, so at least same-session reloads
;;; don't stack duplicates. Restarting AutoCAD is still the only guaranteed
;;; way to clear a genuinely separate old compiled VLX still resident from
;;; a previous session.
(if (and (boundp 'Q-vlr) Q-vlr (vlr-added-p Q-vlr)) (vlr-remove Q-vlr))
(if (and (boundp 'Q-vlr-append) Q-vlr-append (vlr-added-p Q-vlr-append)) (vlr-remove Q-vlr-append))
(if (and (boundp 'Q-vlr-cmdend) Q-vlr-cmdend (vlr-added-p Q-vlr-cmdend)) (vlr-remove Q-vlr-cmdend))

(setq Q-vlr-append (vlr-acdb-reactor nil '((:vlr-objectAppended . Q-collect))))
(setq Q-vlr-cmdend (vlr-editor-reactor nil '((:vlr-commandEnded . Q-process-pending))))

(princ (strcat "\n[Q-System] Loaded. Run Q, Q1, Q2 " "commands to" " " "use them."))
(princ)

;;; ===========================================================================
;;; [BD capacity map] Bus duct width(mm) -> capacity(A) lookup, for QT's
;;; Note column. Bus duct's physical size does NOT determine its rating on
;;; its own (unlike tray/raceway) - the actual current rating only lives in
;;; the cable schedule table, not the drawing geometry or layer name. This
;;; is a simple, per-project manual lookup table (you fill it in once per
;;; project from the schedule) rather than automated cross-referencing.
;;; Saved next to the current drawing as MCL_BD_CAPMAP.csv, so it persists
;;; across sessions without needing to be re-entered every time.
;;; ===========================================================================
(defun _BD_CapMapPath ()
  (strcat (getvar "DWGPREFIX") "MCL_BD_CAPMAP.csv")
)

(defun _BD_SplitComma (str / pos parts)
  (setq parts '())
  (while (setq pos (vl-string-search "," str))
    (setq parts (cons (substr str 1 pos) parts))
    (setq str (substr str (+ pos 2)))
  )
  (setq parts (cons str parts))
  (reverse parts)
)

(defun _BD_LoadCapMap (/ path f line parts w cap result)
  (setq path (_BD_CapMapPath))
  (setq result '())
  (if (findfile path)
    (progn
      (setq f (open path "r"))
      (while (setq line (read-line f))
        (setq parts (_BD_SplitComma line))
        (if (= (length parts) 2)
          (progn
            (setq w (atof (nth 0 parts)))
            (setq cap (nth 1 parts))
            (if (> w 0.0) (setq result (cons (cons w cap) result)))
          )
        )
      )
      (close f)
    )
  )
  (reverse result)
)

(defun _BD_SaveCapMap (capmap / path f pair)
  (setq path (_BD_CapMapPath))
  (setq f (open path "w"))
  (foreach pair capmap
    (write-line (strcat (rtos (car pair) 2 0) "," (cdr pair)) f)
  )
  (close f)
)

;;; c:BDCAPMAP - view/add/replace the width(mm) -> capacity(A) entries for
;;; the current drawing. Enter blank at the width prompt to finish.
(defun c:BDCAPMAP (/ capmap w cap done)
  (setq capmap (_BD_LoadCapMap))
  (princ (strcat "\n현재 매핑 (" (itoa (length capmap)) "개):"))
  (foreach pair capmap (princ (strcat "\n  " (rtos (car pair) 2 0) "mm -> " (cdr pair))))
  (princ "\n\n폭(mm)과 용량(A)을 순서대로 입력하세요. 이미 있는 폭을 다시 입력하면 값이 교체됩니다.")
  (setq done nil)
  (while (not done)
    (setq w (getreal "\n폭(mm) (엔터=종료): "))
    (if (not w)
      (setq done T)
      (progn
        (setq cap (getstring T (strcat "\n" (rtos w 2 0) "mm의 용량 (예: 2,000A): ")))
        (setq capmap (cons (cons w cap) (vl-remove-if '(lambda (p) (= (car p) w)) capmap)))
      )
    )
  )
  (_BD_SaveCapMap capmap)
  (princ (strcat "\n저장 완료: " (_BD_CapMapPath)))
  (princ)
)

(defun _BD_CapacityFor (width capmap / pair)
  (setq pair (assoc width capmap))
  (if pair (cdr pair) nil)
)

(princ "\n[BDCAPMAP] Loaded. Run 'BDCAPMAP' to set up bus duct width->capacity mapping for QT's Note column.")
(princ)

;;; ===========================================================================
;;; [SHAFT] Elevator shaft size rectangles -> one layer per core, counted by QT
;;; ===========================================================================
;;; QT runs the QEV step automatically before it tallies (see _SH-AutoRunSafe).
;;; QEV takes the shaft-size rectangles written by the elevator review program
;;; (closed 4-vertex polylines on the shaft-size layer, each with a text label
;;; at its center such as "<core>22A 2800x2500 ...") and, in core order:
;;;   - creates/uses one layer per core: SHAFT-01, SHAFT-02, ... (SHAFT-22A /
;;;     SHAFT-22B when one core number has separate A/B rectangles)
;;;   - moves the rectangle onto that layer, BYLAYER color, lineweight 50 and
;;;     the Q tag (MY_Q_DATA) so QT counts it
;;;   - records the rectangle's width (1st edge) and depth (2nd edge) on the
;;;     LAYER record itself (xdata MY_SHAFT_SIZE) - QT reads it from there.
;;; Korean text is written as \U+XXXX escapes so this file loads correctly
;;; whatever the file encoding is.
(defun _SH-SrcLayer () "\U+C2B9\U+AC15\U+B85C \U+C0AC\U+C774\U+C988")
(defun _SH-CoreWord () "\U+CF54\U+C5B4")
(defun _SH-ShaftWord () "\U+C2B9\U+AC15\U+B85C")

;;; "<core>22A ... <core>22B ..." -> ((22 . "A") (22 . "B"))
(defun _SH-Tokens (str / key klen pos start n ch len res num letter done)
  (setq key (_SH-CoreWord) klen (strlen key) res '() start 0)
  (setq len (strlen str))
  (while (setq pos (vl-string-search key str start))
    (setq n (+ pos klen 1) num "" letter "" done nil)
    (while (and (not done) (<= n len))
      (setq ch (substr str n 1))
      (if (and (>= (ascii ch) 48) (<= (ascii ch) 57))
        (progn (setq num (strcat num ch)) (setq n (1+ n)))
        (setq done T)
      )
    )
    (if (and (/= num "") (<= n len))
      (progn
        (setq ch (substr str n 1))
        (if (and (>= (ascii ch) 65) (<= (ascii ch) 90)) (setq letter ch))
      )
    )
    (if (/= num "") (setq res (cons (cons (atoi num) letter) res)))
    (setq start (+ pos klen))
  )
  (reverse res)
)

(defun _SH-Pad (num width / s)
  (setq s (itoa num))
  (while (< (strlen s) width) (setq s (strcat "0" s)))
  s
)

;;; two A/B rectangles of one core -> "22A"/"22B"; one shared rectangle that
;;; carries several tokens of the same core number (2A+2B) -> "02"
(defun _SH-IdFromTokens (toks width / num1 allSame)
  (setq num1 (car (car toks)))
  (setq allSame (not (vl-some '(lambda (tk) (/= (car tk) num1)) toks)))
  (if (and (> (length toks) 1) allSame)
    (_SH-Pad num1 width)
    (strcat (_SH-Pad num1 width) (cdr (car toks)))
  )
)

(defun _SH-TokText (toks / s)
  (setq s "")
  (foreach tk toks
    (setq s (strcat s (if (= s "") "" "+") (itoa (car tk)) (cdr tk)))
  )
  s
)

;;; closed 4-vertex polyline -> (width depth centerX centerY), else nil
(defun _SH-RectInfo (ent / pts)
  (setq pts (mapcar 'cdr (vl-remove-if-not '(lambda (p) (= (car p) 10)) (entget ent))))
  (if (= (length pts) 4)
    (list (distance (nth 0 pts) (nth 1 pts))
          (distance (nth 1 pts) (nth 2 pts))
          (/ (+ (car (nth 0 pts)) (car (nth 1 pts)) (car (nth 2 pts)) (car (nth 3 pts))) 4.0)
          (/ (+ (cadr (nth 0 pts)) (cadr (nth 1 pts)) (cadr (nth 2 pts)) (cadr (nth 3 pts))) 4.0))
    nil
  )
)

;;; token list of the label text sitting at (cx,cy) - nil when no label there
(defun _SH-LabelTokens (cx cy txtList / best bd d)
  (setq best nil bd 1.0e99)
  (foreach tx txtList
    (setq d (distance (list cx cy) (list (car tx) (cadr tx))))
    (if (< d bd) (setq bd d best tx))
  )
  (if (and best (< bd 200.0)) (_SH-Tokens (nth 2 best)) nil)
)

;;; (width depth coreText) stored on the layer record, or nil
(defun _ShaftInfo (lay / le ed xd grp vals)
  (setq le (tblobjname "LAYER" lay))
  (if le
    (progn
      (setq ed (entget le (list "MY_SHAFT_SIZE")))
      (setq xd (assoc -3 ed))
      (if xd
        (progn
          (setq grp (cdr (assoc "MY_SHAFT_SIZE" (cdr xd))))
          (if grp
            (progn
              (setq vals (mapcar 'cdr (vl-remove-if-not '(lambda (p) (= (car p) 1040)) grp)))
              (if (= (length vals) 2)
                (list (nth 0 vals) (nth 1 vals) (cdr (assoc 1000 grp)))
                nil
              )
            )
            nil
          )
        )
        nil
      )
    )
    nil
  )
)

(defun _SH-SetLayerInfo (lay w d coreText / le ed)
  (regapp "MY_SHAFT_SIZE")
  (setq le (tblobjname "LAYER" lay))
  (if le
    (progn
      (setq ed (entget le))
      (entmod (append ed
                      (list (list -3 (list "MY_SHAFT_SIZE"
                                           (cons 1000 coreText)
                                           (cons 1040 w)
                                           (cons 1040 d))))))
    )
  )
)

(defun _SH-Note (info)
  (strcat (_SH-ShaftWord) " " (_SH-CoreWord) " " (nth 2 info))
)

;;; Core of QEV. ss = selection set of shaft rectangles (closed 4-vertex
;;; polylines). quiet = T suppresses the per-layer listing (used by the
;;; automatic run inside QT). Returns the number of rectangles processed.
(defun _SH-Process (ss quiet / tss txtList i ent ri items withTok noTok maxnum width k item id
                    lay_name used doc layers newLayer obj cnt old)
  (setq cnt 0)
  (vl-load-com)
  ;; label texts (center of each rectangle) on the shaft-size layer
  (setq txtList '())
  (setq tss (ssget "X" (list '(0 . "TEXT") (cons 8 (_SH-SrcLayer)))))
  (if tss
    (progn
      (setq i 0)
      (repeat (sslength tss)
        (setq ent (entget (ssname tss i)))
        (setq txtList (cons (list (cadr (assoc 10 ent)) (caddr (assoc 10 ent)) (cdr (assoc 1 ent))) txtList))
        (setq i (1+ i))
      )
    )
  )
  ;; rectangle data: (ent width depth cx cy tokens)
  (setq items '() i 0)
  (repeat (sslength ss)
    (setq ent (ssname ss i))
    (setq ri (_SH-RectInfo ent))
    (if ri
      (setq items (cons (list ent (nth 0 ri) (nth 1 ri) (nth 2 ri) (nth 3 ri)
                              (_SH-LabelTokens (nth 2 ri) (nth 3 ri) txtList))
                        items))
      (if (not quiet) (princ "\n[QEV] Skipped a polyline that is not a 4-vertex rectangle."))
    )
    (setq i (1+ i))
  )
  (if items
    (progn
      ;; core order: labelled rectangles by (core number, letter);
      ;; unlabelled ones after them, by position
      (setq withTok (vl-remove-if '(lambda (it) (null (nth 5 it))) items))
      (setq noTok (vl-remove-if-not '(lambda (it) (null (nth 5 it))) items))
      (setq withTok (vl-sort withTok
        '(lambda (a b)
           (or (< (car (car (nth 5 a))) (car (car (nth 5 b))))
               (and (= (car (car (nth 5 a))) (car (car (nth 5 b))))
                    (< (cdr (car (nth 5 a))) (cdr (car (nth 5 b)))))))))
      (setq noTok (vl-sort noTok
        '(lambda (a b)
           (or (< (nth 3 a) (nth 3 b))
               (and (= (nth 3 a) (nth 3 b)) (< (nth 4 a) (nth 4 b)))))))
      ;; numbering width (at least 2 digits)
      (setq maxnum 0)
      (foreach it withTok
        (foreach tk (nth 5 it) (if (> (car tk) maxnum) (setq maxnum (car tk))))
      )
      (setq maxnum (+ maxnum (length noTok)))
      (setq width (max 2 (strlen (itoa maxnum))))
      ;; unlabelled rectangles get the next free numbers
      (setq k (- maxnum (length noTok)))
      (setq noTok
        (mapcar '(lambda (it)
                   (setq k (1+ k))
                   (append (list (nth 0 it) (nth 1 it) (nth 2 it) (nth 3 it) (nth 4 it)) (list (list (cons k "")))))
                noTok))
      (setq items (append withTok noTok))
      ;; layers + tag + size
      (setq doc (vla-get-ActiveDocument (vlax-get-acad-object)))
      (setq layers (vla-get-Layers doc))
      (setq used '())
      (foreach item items
        (setq id (_SH-IdFromTokens (nth 5 item) width))
        (if (assoc id used)
          (progn
            (setq used (subst (cons id (1+ (cdr (assoc id used)))) (assoc id used) used))
            (setq id (strcat id "-" (itoa (cdr (assoc id used)))))
          )
          (setq used (cons (cons id 1) used))
        )
        (setq lay_name (strcat "SHAFT-" id))
        (if (not (tblsearch "LAYER" lay_name))
          (progn
            (setq newLayer (vla-Add layers lay_name))
            (vl-catch-all-apply 'vla-put-color (list newLayer 30))
          )
          ;; the layer already holds rectangles from an earlier run: they would be
          ;; counted again by QT, so tell the user (always, even in the quiet run)
          (progn
            (setq old (ssget "X" (list '(0 . "LWPOLYLINE") (cons 8 lay_name))))
            (if old
              (princ (strcat "\n[QEV] Warning: " lay_name " already has " (itoa (sslength old))
                             " rectangle(s) from an earlier run - QT will count them too."))
            )
          )
        )
        (setq obj (vlax-ename->vla-object (nth 0 item)))
        (vla-put-layer obj lay_name)
        (vla-put-color obj 256)
        (vla-put-lineweight obj 50)
        (_SetQData (nth 0 item))
        (_SH-SetLayerInfo lay_name (nth 1 item) (nth 2 item) (_SH-TokText (nth 5 item)))
        (if (not quiet)
          (princ (strcat "\n  " lay_name "  W=" (rtos (nth 1 item) 2 0) "  D=" (rtos (nth 2 item) 2 0)))
        )
        (setq cnt (1+ cnt))
      )
      (setvar "LWDISPLAY" 1)
    )
  )
  cnt
)

;;; Manual command: all rectangles on the shaft-size layer, or a selection.
(defun c:QEV (/ ss cnt)
  (if (_AuthCheck)
    (progn
      (vl-load-com)
      (setq ss (ssget "X" (list '(0 . "LWPOLYLINE") (cons 8 (_SH-SrcLayer)))))
      (if (not ss)
        (progn
          (princ "\n[QEV] No rectangles on the shaft-size layer. Select the shaft rectangles: ")
          (setq ss (ssget '((0 . "LWPOLYLINE"))))
        )
      )
      (if (not ss)
        (princ "\n[QEV] Nothing selected.")
        (progn
          (setq cnt (_SH-Process ss nil))
          (if (> cnt 0)
            (princ (strcat "\n[QEV] " (itoa cnt) " shaft rectangle(s) moved to SHAFT-nn layers. Run QT to tally."))
            (princ "\n[QEV] No usable rectangles.")
          )
        )
      )
    )
  )
  (princ)
)

;;; Automatic run, called by QT before it tallies. Silent when the shaft-size
;;; layer is missing or empty. Never lets an error stop QT.
(defun _SH-AutoRun (/ ss cnt)
  (setq ss (ssget "X" (list '(0 . "LWPOLYLINE") (cons 8 (_SH-SrcLayer)))))
  (if ss
    (progn
      (setq cnt (_SH-Process ss T))
      (if (> cnt 0)
        (princ (strcat "\n[QEV] Auto: " (itoa cnt) " shaft rectangle(s) moved to SHAFT-nn layers."))
      )
    )
  )
  (princ)
)

(defun _SH-AutoRunSafe (/ r)
  (setq r (vl-catch-all-apply '_SH-AutoRun nil))
  (if (vl-catch-all-error-p r)
    (princ (strcat "\n[QEV] Auto step skipped: " (vl-catch-all-error-message r)))
  )
  (princ)
)

(princ "\n[QEV] Loaded. QT now runs QEV automatically first; you can also run QEV by hand.")
(princ)

;;; ===========================================================================
;;; [QT] Q-object count table
;;; ===========================================================================
;;; ===========================================================================
;;; [Helper] Classify an MCL/MCL2, MCLR/MCLR2, or MCLBD2-generated layer
;;; name into a human-readable note. Everything on a CEN-, RW-, or BD-
;;; layer is created by MCL/MCL2 (tray), MCLR/MCLR2 (raceway), or MCLBD2
;;; (bus duct) by construction (the prefix is no longer user-choosable),
;;; so layer-name pattern matching alone is enough - no per-object check
;;; needed. Same suffix convention across all three, just a different
;;; prefix length (CEN- is 4 chars, RW-/BD- are 3) and different wording
;;; for raceway specifically (tray and bus duct share the same wording):
;;;   <prefix><width>            -> straight run (centerline)
;;;   <prefix><width+1>          -> end closure   (touchCount=1)
;;;   <prefix><width+2>          -> elbow          (touchCount=2)
;;;   <prefix><width+3>          -> tee            (touchCount=3)
;;;   <prefix><width+4>          -> cross          (touchCount=4)
;;;   CEN-RED-<small>-<big> / RW-RED-<small>-<big> -> reducer
;;;   CEN-REVIEW / BD-REVIEW     -> unclassified, needs manual review
;;; Bus duct's capacity(A) suffix (from BDCAPMAP) is only ever added for an
;;; actual BD-/bd- prefixed layer - MCLBD2 always draws bus duct on BD-
;;; (see *mbd_prefix* in MCL_multicline_v4_step2_busduct.lsp), so CEN- is
;;; tray-only and must never pick up a capacity suffix.
;;; Uses mod 5 (not mod 10) to decide "is this a base width or a +1..+4
;;; offset" - safe for any realistic rounding increment (5/10/50/100/...),
;;; since a +1..+4 offset can never itself land on a multiple of 5.
;;; ===========================================================================
;;; [Helper] Read the MCL_ROUTE_KIND tag off a LAYER table record itself
;;; (not an object on it) - set by MCLROUTE when it creates a power-cable
;;; or conduit layer under whatever name the user chose. Since that name
;;; has no fixed convention QT could otherwise pattern-match on, this is
;;; how QT's Note recognizes those layers regardless of their name.
(defun _GetLayerKind (layerName / layerEnt edata xd grp)
  (setq layerEnt (tblobjname "LAYER" layerName))
  (if layerEnt
    (progn
      (setq edata (entget layerEnt (list "MCL_ROUTE_KIND")))
      (setq xd (assoc -3 edata))
      (if xd
        (progn
          (setq grp (assoc "MCL_ROUTE_KIND" (cdr xd)))
          (if grp (cdr (assoc 1000 (cdr grp))) nil)
        )
        nil
      )
    )
    nil
  )
)

(defun _MCLLayerNote (lay / prefixLen suffix num isRaceway base note cap kind)
  (setq kind (_GetLayerKind lay))
  (cond
    ((_ShaftInfo lay) (_SH-Note (_ShaftInfo lay)))
    ((= kind "POWER") "Power Cable")
    ((= kind "CONDUIT") "Power Cable Conduit")
    (T (_MCLLayerNote_Prefix lay))
  )
)

(defun _MCLLayerNote_Prefix (lay / prefixLen suffix num isRaceway isBusDuct base note cap)
  (setq prefixLen nil isRaceway nil isBusDuct nil)
  (cond
    ((and (>= (strlen lay) 4) (= (strcase (substr lay 1 4)) "CEN-")) (setq prefixLen 4))
    ((and (>= (strlen lay) 3) (= (strcase (substr lay 1 3)) "RW-")) (setq prefixLen 3 isRaceway T))
    ((and (>= (strlen lay) 3) (= (strcase (substr lay 1 3)) "BD-")) (setq prefixLen 3 isBusDuct T)) ;; bus duct - separate prefix, same word labels as tray
  )
  (cond
    ((not prefixLen) "")
    ((member (strcase lay) '("CEN-REVIEW" "BD-REVIEW")) "미분류 (확인 필요)")
    ((wcmatch (strcase lay) "CEN-RED-*,RW-RED-*,BD-RED-*") "리듀서")
    (T
     (setq suffix (substr lay (1+ prefixLen)))
     (if (distof suffix)
       (progn
         (setq num (fix (distof suffix)))
         (setq base num) ;; overwritten below once the real offset is known
         (setq note
           (if isRaceway
             (cond
               ((= (rem num 5) 0) (setq base num) "RW body+cover")
               ((= (rem (- num 1) 5) 0) (setq base (- num 1)) "end cap")
               ((= (rem (- num 2) 5) 0) (setq base (- num 2)) "junction box 2방")
               ((= (rem (- num 3) 5) 0) (setq base (- num 3)) "junction box 3방")
               ((= (rem (- num 4) 5) 0) (setq base (- num 4)) "junction box 4방")
               (T "")
             )
             (cond
               ((= (rem num 5) 0) (setq base num) "직선부(중심선)")
               ((= (rem (- num 1) 5) 0) (setq base (- num 1)) "앤드클로저")
               ((= (rem (- num 2) 5) 0) (setq base (- num 2)) "엘보우")
               ((= (rem (- num 3) 5) 0) (setq base (- num 3)) "티")
               ((= (rem (- num 4) 5) 0) (setq base (- num 4)) "크로스")
               (T "")
             )
           )
         )
         ;; capacity(A) lookup only applies to actual bus duct (BD-/bd-)
         ;; layers - CEN- (tray) layers must NOT get a capacity suffix even
         ;; though they share the same word labels and suffix convention.
         (if (and isBusDuct (/= note "") (boundp '*bd_capmap_cache*))
           (progn
             (setq cap (_BD_CapacityFor (float base) *bd_capmap_cache*))
             (if cap (setq note (strcat note " (" cap ")")))
           )
         )
         note
       )
       ""
     )
    )
  )
)

;;; [Helper] make sure a Korean-capable text style exists, so QT's table
;;; reads correctly even if the FC (font change) command was never run.
(defun _QTEnsureStyle (/ ed)
  (if (not (tblsearch "STYLE" "QT-KR"))
    (progn
      (entmake (list '(0 . "STYLE") '(100 . "AcDbSymbolTableRecord") '(100 . "AcDbTextStyleTableRecord")
                     (cons 2 "QT-KR") (cons 70 0) (cons 40 0.0) (cons 41 1.0)
                     (cons 50 0.0) (cons 71 0) (cons 42 0.0)
                     (cons 3 "malgun.ttf") (cons 4 "")))
    )
  )
)

;;; [Helper] one centered TEXT cell, created ON layer "lay" (so its real
;;; DXF layer property IS that layer - not just colored to look like it).
(defun _QTCellText (x y str h lay)
  (entmake (list '(0 . "TEXT") (cons 8 lay) (cons 7 "QT-KR")
                 (cons 10 (list x y 0.0)) (cons 11 (list x y 0.0))
                 (cons 40 h) (cons 1 str) (cons 72 1) (cons 73 2)))
)

;;; [Helper] draw the tally table by hand (grid lines + individual TEXT
;;; entities) instead of a native ACAD TABLE object. A native table's cells
;;; all share the table's own single layer, so there's no way to make one
;;; cell's text genuinely live on a different layer - this build-it-by-hand
;;; approach is what lets each row's "Layer(Layer)" cell actually be placed
;;; ON that row's real tray layer (BYLAYER color included), not just
;;; colored to resemble it.

;;; [Helper] QT sort key: first character of the layer (as before), then - for
;;; SHAFT layers only - the full layer name so SHAFT-01, SHAFT-02, ... stay in
;;; core order.
(defun _QTSortKey (item / lay)
  (setq lay (car item))
  (strcat (substr lay 1 1) (if (_ShaftInfo lay) (strcase lay) ""))
)

(defun _QTDrawTable (basePt res rowHeight colWidths textHeight / nCols nRows
                        x0 y0 r c lay yMid totalWidth colLeft colCenter cw withSize info)
  (setq nCols (length colWidths) nRows (+ (length res) 2))
  ;; 6 columns = Layer / Length / Qty / Width / Depth / Note (shaft rows present)
  (setq withSize (= nCols 6))
  (setq x0 (car basePt) y0 (cadr basePt))
  (setq totalWidth (apply '+ colWidths))

  ;; cumulative left-edge x of each column (nCols+1 entries: col0 left,
  ;; col1 left, ..., right edge of the last column)
  (setq colLeft (list x0))
  (foreach cw colWidths
    (setq colLeft (append colLeft (list (+ (last colLeft) cw))))
  )
  ;; center x of each column, for centered cell text
  (setq colCenter '())
  (setq c 0)
  (repeat nCols
    (setq colCenter (append colCenter (list (/ (+ (nth c colLeft) (nth (1+ c) colLeft)) 2.0))))
    (setq c (1+ c))
  )

  ;; horizontal grid lines
  (setq r 0)
  (repeat (1+ nRows)
    (entmake (list '(0 . "LINE") (cons 8 "0")
                   (cons 10 (list x0 (- y0 (* r rowHeight)) 0.0))
                   (cons 11 (list (+ x0 totalWidth) (- y0 (* r rowHeight)) 0.0))))
    (setq r (1+ r))
  )
  ;; outer verticals (full height)
  (entmake (list '(0 . "LINE") (cons 8 "0")
                 (cons 10 (list x0 y0 0.0)) (cons 11 (list x0 (- y0 (* nRows rowHeight)) 0.0))))
  (entmake (list '(0 . "LINE") (cons 8 "0")
                 (cons 10 (list (+ x0 totalWidth) y0 0.0))
                 (cons 11 (list (+ x0 totalWidth) (- y0 (* nRows rowHeight)) 0.0))))
  ;; inner verticals - only below the title row (row 0), so row 0 stays
  ;; visually merged across all columns
  (setq c 1)
  (repeat (1- nCols)
    (entmake (list '(0 . "LINE") (cons 8 "0")
                   (cons 10 (list (nth c colLeft) (- y0 rowHeight) 0.0))
                   (cons 11 (list (nth c colLeft) (- y0 (* nRows rowHeight)) 0.0))))
    (setq c (1+ c))
  )

  ;; title row (row 0) - centered across the full width
  (_QTCellText (+ x0 (/ totalWidth 2.0)) (- y0 (* 0.5 rowHeight))
               (strcat "Q-" "Object" " " "Tally Table" " (" "Standard" ")") textHeight "0")
  ;; header row (row 1)
  (_QTCellText (nth 0 colCenter) (- y0 (* 1.5 rowHeight)) (strcat "Layer" "(Layer)") textHeight "0")
  (_QTCellText (nth 1 colCenter) (- y0 (* 1.5 rowHeight)) (strcat "Length" " (m)") textHeight "0")
  (_QTCellText (nth 2 colCenter) (- y0 (* 1.5 rowHeight)) (strcat "Qty" " (EA)") textHeight "0")
  (if withSize
    (progn
      (_QTCellText (nth 3 colCenter) (- y0 (* 1.5 rowHeight)) "Width (mm)" textHeight "0")
      (_QTCellText (nth 4 colCenter) (- y0 (* 1.5 rowHeight)) "Depth (mm)" textHeight "0")
      (_QTCellText (nth 5 colCenter) (- y0 (* 1.5 rowHeight)) "Note" textHeight "0")
    )
    (_QTCellText (nth 3 colCenter) (- y0 (* 1.5 rowHeight)) "Note" textHeight "0")
  )

  ;; data rows
  (setq r 0)
  (foreach item res
    (setq lay (car item))
    (setq info (if withSize (_ShaftInfo lay) nil))
    (setq yMid (- y0 (* (+ 2.5 r) rowHeight)))
    ;; this is the one cell that genuinely lives on the tray's own layer
    (_QTCellText (nth 0 colCenter) yMid lay textHeight lay)
    (_QTCellText (nth 1 colCenter) yMid (if info "-" (rtos (/ (nth 1 item) 1000.0) 2 2)) textHeight "0")
    (_QTCellText (nth 2 colCenter) yMid (itoa (fix (nth 2 item))) textHeight "0")
    (if withSize
      (progn
        (if info
          (progn
            (_QTCellText (nth 3 colCenter) yMid (rtos (nth 0 info) 2 0) textHeight "0")
            (_QTCellText (nth 4 colCenter) yMid (rtos (nth 1 info) 2 0) textHeight "0")
          )
        )
        (_QTCellText (nth 5 colCenter) yMid (_MCLLayerNote lay) textHeight "0")
      )
      (_QTCellText (nth 3 colCenter) yMid (_MCLLayerNote lay) textHeight "0")
    )
    (setq r (1+ r))
  )
)

(defun c:QT (/ ss p1 p2 i ent obj lay data_list res ins_pt rowHeight textHeight colWidth lenTypes qtyTypes isQtyType otype val exist shcache sh hasShaft)
  (if (_AuthCheck)
    (progn
      (vl-load-com)
      (_QTEnsureStyle)
      (_SH-AutoRunSafe)
      (setq *bd_capmap_cache* (_BD_LoadCapMap))
      (setq lenTypes '("AcDbLine" "AcDbPolyline" "AcDb2dPolyline" "AcDbArc"))
      ;; Circle markers (MCL2 fitting markers) count as 1 EA, same as blocks -
      ;; they have no meaningful "length" for tally purposes.
      (setq qtyTypes '("AcDbBlockReference" "AcDbMInsertBlock" "AcDbCircle" "AcDbText" "AcDbMText"))
      (setq p1 (getpoint (strcat "\n" "Area to tally first corner: ")))
      (if (null p1) (exit ""))
      (setq p2 (getcorner p1 (strcat "\n" "Opposite corner: ")))
      (if (null p2) (exit ""))
      (command "_.RECTANG" p1 p2)
      (setq ss (ssget "C" p1 p2 '((-3 ("MY_Q_DATA")))))
      (if (not ss)
        (princ (strcat "\n[" "Notice" "] No Q-object(s) found within the selected area."))
        (progn
          (setq data_list '() i 0)
          (repeat (sslength ss)
            (setq ent (ssname ss i)  obj (vlax-ename->vla-object ent)
                  lay (vla-get-layer obj)  otype (vla-get-objectname obj)  val 0.0)
            (setq sh (assoc lay shcache))
            (if (not sh)
              (progn
                (setq sh (cons lay (_ShaftInfo lay)))
                (setq shcache (cons sh shcache))
              )
            )
            (setq isQtyType (or (member otype qtyTypes) (cdr sh)))
            (if (cdr sh) (setq hasShaft T))
            (if (member otype lenTypes)
              (progn
                (setq val (vl-catch-all-apply 'vlax-curve-getDistAtParam
                  (list obj (vlax-curve-getEndParam obj))))
                (if (vl-catch-all-error-p val) (setq val 0.0))
              )
            )
            (setq exist (assoc lay data_list))
            (if exist
              (setq data_list (subst (list lay
                (+ (nth 1 exist) (if isQtyType 0.0 val))
                (+ (nth 2 exist) (if isQtyType 1.0 0.0)))
                exist data_list))
              (setq data_list (cons (list lay
                (if isQtyType 0.0 val)
                (if isQtyType 1.0 0.0))
                data_list))
            )
            (setq i (1+ i))
          )
          (setq ins_pt (getpoint (strcat "\n" "Click the location to insert the tally table: ")))
          (if ins_pt
            (progn
              (setq res (vl-sort data_list '(lambda (a b) (< (_QTSortKey a) (_QTSortKey b)))))
              (setq textHeight 400  rowHeight 800.0)
              ;; Layer / Length / Qty / Note - Note gets extra room since
              ;; the Korean fitting-type labels run longer than a plain number
              (setq colWidth (if hasShaft '(7600.0 4400.0 3600.0 3600.0 3600.0 13000.0) '(7600.0 4400.0 3600.0 13000.0)))
              (_QTDrawTable ins_pt res rowHeight colWidth textHeight)
              (princ (strcat "\n[" "Complete" "] Q-" "Object" " " "Tally Table" " has been created."))
            )
          )
        )
      )
    )
  )
  (princ)
)
