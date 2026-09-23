;;; ===========================================================================
;;; [MM] MyTools OpenDCL Palette Loader
;;; - Type 'MM' to open the palette manually
;;; - S::STARTUP auto-launches on AutoCAD startup
;;; - APPLOAD > Add to Startup Suite (first time only)
;;;
;;; [ODCL PATH RESOLUTION - in order of priority]
;;; 1. AutoCAD support file search paths  (findfile "MyTools.odcl")
;;; 2. Same folder as MM.lsp              (when running as loose .lsp files)
;;; 3. Same folder as SSO_select_.lsp     (another known file in the package)
;;; => For VLX deployment: add the VLX folder to AutoCAD support file search
;;;    paths (Options > Files > Support File Search Path).
;;; ===========================================================================

(defun alert-user (msg) (alert msg))

;;; [Access control]
(defun _AuthCheck (/ strComputerName baseNames allowedComputers n k)
  (vl-load-com)
  (setq strComputerName (getenv "COMPUTERNAME"))
  (setq baseNames '("P161808N" "P162561N" "P161664N" "P161743N" "P162567N" "P143093N" "P161664N" "P154400N" "P157001N"))
  (setq allowedComputers '())
  (setq n 1)
  (repeat 99
    (setq k (itoa n))
    (foreach baseName baseNames
      (setq allowedComputers
        (cons (strcat baseName (if (< n 10) "0" "") k) allowedComputers))
    )
    (setq n (1+ n))
  )
  (if (member strComputerName allowedComputers)
    T
    (progn
      (alert-user (strcat
        "Authentication Failed\n"
        "This program is for authorized users only.\n"
        "Please contact the administrator."))
      nil
    )
  )
)

;;; [Find MyTools.odcl using multiple methods]
(defun _MM-find-odcl (/ f candidate)
  (cond
    ;; Method 1: AutoCAD support file search paths
    ;;           (works when VLX folder is added to support paths)
    ((setq f (findfile "MyTools.odcl")) f)

    ;; Method 2: same folder as MyTools.vlx
    ;;           (works for VLX deployment without support path registration)
    ((setq f (findfile "MyTools.vlx"))
     (setq candidate (strcat (vl-filename-directory f) "\\MyTools.odcl"))
     (if (findfile candidate) candidate nil))

    ;; Method 3: same folder as MM.lsp
    ;;           (works when running as loose .lsp files)
    ((setq f (findfile "MM.lsp"))
     (setq candidate (strcat (vl-filename-directory f) "\\MyTools.odcl"))
     (if (findfile candidate) candidate nil))

    ;; Method 4: same folder as SSO_select_.lsp (another package file)
    ((setq f (findfile "SSO_select_.lsp"))
     (setq candidate (strcat (vl-filename-directory f) "\\MyTools.odcl"))
     (if (findfile candidate) candidate nil))

    ;; Not found
    (t nil)
  )
)

;;; [Launch: find odcl -> load -> show palette]
;;; No auth gate here on purpose - MM is exempt from _AuthCheck while every
;;; other tool in the suite still goes through the shared _AuthCheck defined
;;; elsewhere (that function is untouched, so nothing else is affected).
(defun _MM-Launch (/ odcl-path)
  (setq odcl-path (_MM-find-odcl))
  (if (not odcl-path)
    (progn
      (alert (strcat
        "MyTools.odcl not found.\n\n"
        "Please add the MyTools folder to AutoCAD support file search paths:\n"
        "Options > Files > Support File Search Path > Add"))
      (exit "")
    )
  )
  (if (not (dcl-project-load odcl-path))
    (progn
      (alert (strcat "Failed to load:\n" odcl-path))
      (exit "")
    )
  )
  (dcl-form-show MyTools/Pal_MyTools)
  (princ "\n[MM] Palette activated.")
  (princ)
)

;;; ===========================================================================
;;; 1. Manual command
;;; ===========================================================================
(defun c:MM (/)
  (_MM-Launch)
)

;;; ===========================================================================
;;; 2. Auto-launch via S::STARTUP (chains with existing S::STARTUP)
;;; ===========================================================================
(if (atoms-family 1 (list "S::STARTUP"))
  (progn
    (setq *_MM-prev-startup* S::STARTUP)
    (defun S::STARTUP ()
      (_MM-Launch)
      (if *_MM-prev-startup* (*_MM-prev-startup*))
    )
  )
  (defun S::STARTUP ()
    (_MM-Launch)
  )
)

;;; ===========================================================================
;;; 3. Button event handlers
;;;    dcl-SendString = posts command to AutoCAD command line
;;;    (same as typing at command line - ensures drawing focus for entsel/ssget)
;;; ===========================================================================
(defun c:MyTools/Pal_MyTools/btnff#OnClicked          (/) (dcl-SendString "ff\n"))
(defun c:MyTools/Pal_MyTools/btnxc#OnClicked          (/) (dcl-SendString "xc\n"))
(defun c:MyTools/Pal_MyTools/btnbind#OnClicked        (/) (dcl-SendString "bind\n"))
(defun c:MyTools/Pal_MyTools/btnburst#OnClicked       (/) (dcl-SendString "burst\n"))
(defun c:MyTools/Pal_MyTools/btnfc#OnClicked          (/) (dcl-SendString "fc\n"))
(defun c:MyTools/Pal_MyTools/btnree#OnClicked         (/) (dcl-SendString "ree\n"))
(defun c:MyTools/Pal_MyTools/btnpdf#OnClicked         (/) (dcl-SendString "pdf\n"))
(defun c:MyTools/Pal_MyTools/btnasum#OnClicked        (/) (dcl-SendString "asum\n"))
(defun c:MyTools/Pal_MyTools/btnlsum#OnClicked        (/) (dcl-SendString "lsum\n"))
(defun c:MyTools/Pal_MyTools/btnrlh#OnClicked         (/) (dcl-SendString "rlh\n"))
(defun c:MyTools/Pal_MyTools/btnbca#OnClicked         (/) (dcl-SendString "bca\n"))
(defun c:MyTools/Pal_MyTools/btnlca#OnClicked         (/) (dcl-SendString "lca\n"))
(defun c:MyTools/Pal_MyTools/btntca#OnClicked         (/) (dcl-SendString "tca\n"))
(defun c:MyTools/Pal_MyTools/btnsso#OnClicked         (/) (dcl-SendString "sso\n"))
(defun c:MyTools/Pal_MyTools/btnssob#OnClicked        (/) (dcl-SendString "ssob\n"))
(defun c:MyTools/Pal_MyTools/btnsssor#OnClicked       (/) (dcl-SendString "ssor\n"))
(defun c:MyTools/Pal_MyTools/btnl0#OnClicked          (/) (dcl-SendString "l0\n"))
(defun c:MyTools/Pal_MyTools/btnl1#OnClicked          (/) (dcl-SendString "l1\n"))
(defun c:MyTools/Pal_MyTools/btnl2#OnClicked          (/) (dcl-SendString "l2\n"))
(defun c:MyTools/Pal_MyTools/btnl3#OnClicked          (/) (dcl-SendString "l3\n"))
(defun c:MyTools/Pal_MyTools/btnl4#OnClicked          (/) (dcl-SendString "l4\n"))
(defun c:MyTools/Pal_MyTools/btnl5#OnClicked          (/) (dcl-SendString "l5\n"))
(defun c:MyTools/Pal_MyTools/btnl6#OnClicked          (/) (dcl-SendString "l6\n"))
(defun c:MyTools/Pal_MyTools/btnl7#OnClicked          (/) (dcl-SendString "l7\n"))
(defun c:MyTools/Pal_MyTools/btnbb#OnClicked          (/) (dcl-SendString "bb\n"))
(defun c:MyTools/Pal_MyTools/btnbb1#OnClicked         (/) (dcl-SendString "bb1\n"))
(defun c:MyTools/Pal_MyTools/btnbpm#OnClicked         (/) (dcl-SendString "bpm\n"))
(defun c:MyTools/Pal_MyTools/btnq#OnClicked           (/) (dcl-SendString "q\n"))
(defun c:MyTools/Pal_MyTools/btnq1#OnClicked          (/) (dcl-SendString "q1\n"))
(defun c:MyTools/Pal_MyTools/btnq2#OnClicked          (/) (dcl-SendString "q2\n"))
(defun c:MyTools/Pal_MyTools/btnqt#OnClicked          (/) (dcl-SendString "qt\n"))
(defun c:MyTools/Pal_MyTools/btnhh#OnClicked          (/) (dcl-SendString "hh\n"))
(defun c:MyTools/Pal_MyTools/btnmcl#OnClicked         (/) (dcl-SendString "mcl\n"))
(defun c:MyTools/Pal_MyTools/btnmcl2#OnClicked        (/) (dcl-SendString "mcl2\n"))
(defun c:MyTools/Pal_MyTools/btnlw1#OnClicked         (/) (dcl-SendString "lw1\n"))
(defun c:MyTools/Pal_MyTools/btntata#OnClicked        (/) (dcl-SendString "tata\n"))
(defun c:MyTools/Pal_MyTools/btntata1#OnClicked       (/) (dcl-SendString "tata1\n"))
(defun c:MyTools/Pal_MyTools/btntata2#OnClicked       (/) (dcl-SendString "tata2\n"))
(defun c:MyTools/Pal_MyTools/btncme#OnClicked         (/) (dcl-SendString "cme\n"))
(defun c:MyTools/Pal_MyTools/btnconvertpoly#OnClicked (/) (dcl-SendString "convertpoly\n"))
(defun c:MyTools/Pal_MyTools/btnrecenter#OnClicked   (/) (dcl-SendString "recenter\n"))
(defun c:MyTools/Pal_MyTools/btndelout#OnClicked      (/) (dcl-SendString "delout\n"))
(defun c:MyTools/Pal_MyTools/btnmclr#OnClicked      (/) (dcl-SendString "mclr\n"))
(defun c:MyTools/Pal_MyTools/btnmclroute#OnClicked      (/) (dcl-SendString "mclroute\n"))
(defun c:MyTools/Pal_MyTools/btnmclr2#OnClicked      (/) (dcl-SendString "mclr2\n"))
(defun c:MyTools/Pal_MyTools/btnmcldebug#OnClicked      (/) (dcl-SendString "mcldebug\n"))
(defun c:MyTools/Pal_MyTools/btnmclr2debug#OnClicked      (/) (dcl-SendString "mclr2debug\n"))
(defun c:MyTools/Pal_MyTools/btnmclbd2#OnClicked      (/) (dcl-SendString "mclbd2\n"))
(defun c:MyTools/Pal_MyTools/btnpdf2cad#OnClicked      (/) (dcl-SendString "pdf2cad\n"))
(defun c:MyTools/Pal_MyTools/btnbdcapmap#OnClicked      (/) (dcl-SendString "bdcapmap\n"))
(defun c:MyTools/Pal_MyTools/btntxf#OnClicked      (/) (dcl-SendString "txf\n"))
(defun c:MyTools/Pal_MyTools/btndxfs#OnClicked     (/) (dcl-SendString "dxfs\n"))
(defun c:MyTools/Pal_MyTools/btndxfsa#OnClicked    (/) (dcl-SendString "dxfsa\n"))
(defun c:MyTools/Pal_MyTools/btnxct#OnClicked      (/) (dcl-SendString "xct\n"))
(defun c:MyTools/Pal_MyTools/btnClose#OnClicked (/)
  (dcl-form-close MyTools/Pal_MyTools)
)

(princ "\n[OpenDCL] Loaded. Type 'MM' to open palette manually.")
(princ)