"""
SCANMAP Backend – Flask server for invoice OCR extraction and PDF generation.
Requires: Tesseract OCR installed (https://github.com/tesseract-ocr/tesseract)
"""
import os, re, math, tempfile, shutil
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename
import pytesseract
from PIL import Image, ImageOps
import fitz  # PyMuPDF
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import Paragraph

# Auto-detect Tesseract on Windows
if not shutil.which("tesseract"):
    win_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if os.path.exists(win_path):
        pytesseract.pytesseract.tesseract_cmd = win_path

app = Flask(__name__, static_folder="../frontend", static_url_path="")
CORS(app)
UPLOAD_FOLDER = tempfile.mkdtemp()
ALLOWED_EXT = {"png", "jpg", "jpeg", "gif", "bmp", "tiff", "pdf"}

# ══════════════════════════════════════════════════════════════════════════════
# OCR ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def allowed_file(fn):
    return "." in fn and fn.rsplit(".", 1)[1].lower() in ALLOWED_EXT

def score_text(t):
    c = " ".join(t.split())
    if not c: return 0
    kw = len(re.findall(r"\b(invoice|total|amount|session|date|rate|price|from|to|period)\b", c, re.I))
    return len(c) + kw * 35 + len(re.findall(r"\d", c)) * 2

def pick_best(a, b):
    a, b = a.strip(), b.strip()
    if not a: return b
    if not b: return a
    sa, sb = score_text(a), score_text(b)
    if sb > sa * 1.1: return b
    if sa > sb * 1.1: return a
    return f"{a}\n{b}"

def ocr_image(img):
    t1 = pytesseract.image_to_string(img, lang="eng")
    bw = ImageOps.grayscale(img).point(lambda x: 255 if x > 140 else 0)
    t2 = pytesseract.image_to_string(bw, lang="eng")
    return pick_best(t1, t2)

def extract_text_from_pdf_by_coords(page):
    Y_TOL, COL_GAP = 3, 90
    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
    items = []
    for b in blocks:
        for line in b.get("lines", []):
            for span in line["spans"]:
                t = span.get("text", "").strip()
                if t:
                    bb = span.get("bbox", [0,0,0,0])
                    items.append({"str": t, "x": bb[0], "y": bb[1]})
    if not items: return ""
    rows = {}
    for it in items:
        k = round(it["y"] / Y_TOL) * Y_TOL
        rows.setdefault(k, []).append(it)
    is_num = lambda v: bool(re.match(r"^(?:[₹$€£]?\d+[\d,.-]*|[,.-]\d+)$", v))
    out = []
    for _, row in sorted(rows.items()):
        row.sort(key=lambda i: i["x"])
        line, prev = "", None
        for cell in row:
            if prev:
                gap = cell["x"] - prev["x"]
                if gap > COL_GAP: line += "    "
                elif is_num(prev["str"]) and is_num(cell["str"]) and gap < 30: pass
                else: line += " "
            line += cell["str"]; prev = {"x": cell["x"], "str": cell["str"]}
        out.append(line.strip())
    return "\n".join(filter(None, out))

def should_fallback_ocr(text):
    c = " ".join(text.split())
    if len(c) < 120: return True
    return not re.search(r"(invoice|total|amount|session|rate|price|period)", c, re.I) or \
           not re.search(r"(₹|rs\.?|inr|\d{3,})", c, re.I)

def extract_text_from_pdf(fp):
    doc = fitz.open(fp)
    full = ""
    for pg in doc:
        t = extract_text_from_pdf_by_coords(pg)
        if should_fallback_ocr(t):
            pix = pg.get_pixmap(dpi=250)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            ocr = ocr_image(img)
            t = f"{t}\n{ocr}" if t.strip() else ocr
        full += t + "\n"
    doc.close()
    return full

def extract_text_from_image(fp):
    return ocr_image(Image.open(fp))

# ══════════════════════════════════════════════════════════════════════════════
# PARSING HELPERS
# ══════════════════════════════════════════════════════════════════════════════

MONTH_PAT = r"[A-Za-z]{3,9}"
DATE_PAT = rf"(?:\d{{1,2}}(?:st|nd|rd|th)?[\/\-\.]\d{{1,2}}[\/\-\.]\d{{2,4}}|\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTH_PAT}\s+\d{{2,4}}|{MONTH_PAT}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{2,4}})"
DATE_PARTIAL = rf"(?:\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTH_PAT}|{MONTH_PAT}\s+\d{{1,2}}(?:st|nd|rd|th)?)"
DATE_RANGE_PAT = rf"(?:{DATE_PAT}|{DATE_PARTIAL})"
AMT_PAT = r"\d{1,3}(?:\s*[,.]\s*\d{2,3})*(?:\s*[,.]\s*\d{1,2})?|\d+(?:\s*[,.]\s*\d{1,2})?"

def normalize_text(t):
    return t.replace("\u2013", "-").replace("\u2014", "-").replace("\u00a0", " ").replace("\r", "\n").replace("|", " ").replace("\t", " ")

def normalize_merged_cs(t):
    t = re.sub(r"((?:₹|rs\.?|inr)\s*\d{1,3}\s*,\s*\d{3})(\d{1,2})(\s*(?:₹|rs\.?|inr))", r"\1 \2\3", t, flags=re.I)
    return t

def normalize_ocr_digits(s):
    return re.sub(r"[Oo]", "0", re.sub(r"[lI|]", "1", re.sub(r"[Ss]", "5", re.sub(r"[Bb]", "8", re.sub(r"[Zz]", "2", re.sub(r"[Ggq]", "9", s))))))

def parse_amount(s):
    s = normalize_ocr_digits(str(s or ""))
    s = re.sub(r"[₹$€£¥]", "", s)
    s = re.sub(r"\s*([,.])\s*", r"\1", s).strip()
    n = re.sub(r"[^0-9.,]", "", s)
    if not n: return 0
    lc, ld = n.rfind(","), n.rfind(".")
    if lc != -1 and ld != -1:
        n = n.replace(".", "").replace(",", ".") if lc > ld else n.replace(",", "")
    elif lc != -1:
        n = n.replace(",", ".") if n.count(",") == 1 and re.search(r",\d{1,2}$", n) else n.replace(",", "")
    elif ld != -1 and n.count(".") > 1:
        n = n.replace(".", "")
    try: return round(float(n))
    except: return 0

def parse_int(s):
    s = re.sub(r"[Oo]", "0", re.sub(r"[lI|]", "1", re.sub(r"[Zz]", "2", str(s or ""))))
    s = re.sub(r"[^0-9]", "", s)
    try: return int(s)
    except: return 0

def sanitize_date(v):
    v = v.replace(",", "")
    v = re.sub(r"\b(\d{1,2})(st|nd|rd|th)\b", r"\1", v, flags=re.I)
    return " ".join(v.split()).strip()

def parse_date(val, fy=None):
    if not isinstance(val, str): return None
    val = sanitize_date(val)
    if not val: return None
    m = re.match(r"^(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{2,4})$", val)
    if m:
        d, mo, y = int(m[1]), int(m[2]), int(m[3])
        if y < 100: y += 2000
        try: return datetime(y, mo, d)
        except: return None
    m = re.match(r"^(\d{1,2})\s+([A-Za-z]{3,9})(?:\s+(\d{2,4}))?$", val, re.I)
    if m:
        y = int(m[3]) if m[3] else (fy or None)
        if not y: return None
        if y < 100: y += 2000
        try: return datetime.strptime(f"{m[2]} {m[1]} {y}", "%B %d %Y")
        except:
            try: return datetime.strptime(f"{m[2]} {m[1]} {y}", "%b %d %Y")
            except: return None
    m = re.match(r"^([A-Za-z]{3,9})\s+(\d{1,2})(?:\s+(\d{2,4}))?$", val, re.I)
    if m:
        y = int(m[3]) if m[3] else (fy or None)
        if not y: return None
        if y < 100: y += 2000
        try: return datetime.strptime(f"{m[1]} {m[2]} {y}", "%B %d %Y")
        except:
            try: return datetime.strptime(f"{m[1]} {m[2]} {y}", "%b %d %Y")
            except: return None
    return None

def fmt_date(d): return d.strftime("%d-%m-%Y")

def norm_date_out(tok, fy=None):
    d = parse_date(tok, fy)
    return fmt_date(d) if d else sanitize_date(tok)

def calc_days(s, e):
    a, b = parse_date(s), parse_date(e)
    if not a or not b: return 0
    d = (b - a).days + 1
    return d if 0 < d < 366 else 0

def compact_inv(v): return re.sub(r"\s*([\/-])\s*", r"\1", v).strip()

# ══════════════════════════════════════════════════════════════════════════════
# EXTRACTORS
# ══════════════════════════════════════════════════════════════════════════════

def extract_date_range(text, inv_date=""):
    rd = parse_date(inv_date) if inv_date else None
    ry = rd.year if rd else datetime.now().year
    pats = [
        rf"(?:from|between|period\s*from|training\s*period|program\s*period|session\s*period|start(?:ing)?\s*date)[:\s-]*({DATE_RANGE_PAT})\s*(?:to|till|until|through|and|-)\s*({DATE_RANGE_PAT})",
        rf"(?:duration|period)[:\s-]*(?:of\s*)?({DATE_RANGE_PAT})\s*(?:to|till|until|through|-)\s*({DATE_RANGE_PAT})",
        rf"({DATE_RANGE_PAT})\s*(?:to|till|until|through|-)\s*({DATE_RANGE_PAT})",
    ]
    for p in pats:
        m = re.search(p, text, re.I)
        if m:
            s, e = parse_date(m[1], ry), parse_date(m[2], ry)
            if s and e and s > e:
                if not re.search(r"\d{4}", m[1]) and not re.search(r"\d{4}", m[2]):
                    e = parse_date(m[2], ry + 1) or e if s.month > e.month else e
                    if s > e: s, e = e, s
            if s and e and s > e: s, e = e, s
            if s or e:
                return (fmt_date(s) if s else norm_date_out(m[1], ry), fmt_date(e) if e else norm_date_out(m[2], ry))
    # Context-line fallback
    ctx = [l.strip() for l in text.split("\n") if re.search(r"(?:training|session|program|period|from\s+\d|to\s+\d|duration|days?)", l, re.I)]
    toks = re.findall(DATE_PAT, "\n".join(ctx), re.I)
    if len(toks) >= 2:
        parsed = [(t, parse_date(t, ry)) for t in toks]
        parsed = [(t, d) for t, d in parsed if d]
        for i in range(len(parsed)):
            for j in range(i+1, len(parsed)):
                s, e = parsed[i][1], parsed[j][1]
                if s > e: s, e = e, s
                if 0 < (e-s).days+1 <= 120:
                    return fmt_date(s), fmt_date(e)
    return "", ""

def extract_inline_dates(text, inv_date=""):
    rd = parse_date(inv_date) if inv_date else None
    ry = rd.year if rd else datetime.now().year
    m = re.search(r"\((\d{1,2})\s+([A-Za-z]{3,9})[\s-]+(\d{1,2})\s+([A-Za-z]{3,9})\)", text, re.I)
    if m:
        s = parse_date(f"{m[1]} {m[2]} {ry}", ry)
        e = parse_date(f"{m[3]} {m[4]} {ry}", ry)
        if s and e and s > e: s = parse_date(f"{m[1]} {m[2]} {ry-1}", ry-1)
        return (fmt_date(s) if s else "", fmt_date(e) if e else "")
    m = re.search(r"\((\d{1,2})\s*[-\u2013]\s*(\d{1,2})\s+([A-Za-z]{3,9})\)", text, re.I)
    if m:
        s = parse_date(f"{m[1]} {m[3]} {ry}", ry)
        e = parse_date(f"{m[2]} {m[3]} {ry}", ry)
        return (fmt_date(s) if s else "", fmt_date(e) if e else "")
    return "", ""

def extract_sessions(text):
    cands = []
    def add(raw, sc, src=""):
        v = parse_int(raw)
        if v <= 0 or v >= 366: return
        if re.search(r"per\s*day|/\s*day|each\s*day|per\s*session", src, re.I): return
        if re.search(r"\b(?:rs\.?|inr|₹)\b", src, re.I) and v > 120: return
        cands.append((v, sc))
    for m in re.finditer(r"(?:total\s*(?:no\.?\s*of\s*)?(?:sessions?|days?)|(?:no\.?\s*of|number\s*of)\s*(?:sessions?|days?|working\s*days?))[:\s-]*(\d{1,3})", text, re.I|re.M):
        add(m[1], 4, m[0])
    for m in re.finditer(r"(\d{1,3})\s*(?:sessions?|working\s*days?|training\s*days?|days?|classes?)(?!\s*(?:per|/|each)\s*day)", text, re.I):
        add(m[1], 3, m[0])
    if not cands: return 0
    cands.sort(key=lambda c: (-c[1], -c[0]))
    return cands[0][0]

def extract_total(text):
    cands = []
    def add(raw, sc, src=""):
        if re.search(r"per\s*(?:session|day|class)|/\s*(?:session|day|class)", src, re.I): return
        a = parse_amount(raw)
        if a > 0: cands.append((a, sc))
    for m in re.finditer(rf"(?:grand\s*total|total\s*amount|invoice\s*total|net\s*amount|amount\s*payable|total\s*payable)[:\s-]*(?:₹|rs\.?|inr)?\s*({AMT_PAT})", text, re.I):
        add(m[1], 8, m[0])
    idx = 0
    for m in re.finditer(rf"(?:^|\n)\s*total\s*[:\s-]*(?:₹|rs\.?|inr)?\s*({AMT_PAT})(?!\s*(?:sessions?|days?))", text, re.I|re.M):
        add(m[1], 4 + idx*0.1, m[0]); idx += 1
    for m in re.finditer(rf"(?:₹|rs\.?|inr)\s*({AMT_PAT})", text, re.I):
        add(m[1], 2, m[0])
    if not cands: return 0
    cands.sort(key=lambda c: (-c[1], -c[0]))
    return cands[0][0]

def extract_price(text):
    cands = []
    def add(raw, sc, src=""):
        if re.search(r"total\s*amount|grand\s*total|amount\s*payable", src, re.I): return
        a = parse_amount(raw)
        if a > 0: cands.append((a, sc))
    for m in re.finditer(rf"(?:price|rate|fee|charges?|session\s*rate|rate\s*per\s*(?:session|day)|price\s*per\s*(?:session|day)|unit\s*(?:rate|price|cost))\s*(?:per\s*(?:session|day))?[:\s-]*(?:₹|rs\.?|inr)?\s*({AMT_PAT})", text, re.I):
        add(m[1], 6, m[0])
    for m in re.finditer(rf"\d{{1,3}}\s*(?:sessions?|days?)\s*[x\u00d7*]\s*(?:₹|rs\.?|inr)?\s*({AMT_PAT})", text, re.I):
        add(m[1], 5, m[0])
    if not cands: return 0
    cands.sort(key=lambda c: (-c[1], c[0]))
    return cands[0][0]

def extract_equation(text):
    cands = []
    pats = [
        rf"(\d{{1,3}})\s*(?:sessions?|days?)\s*[x\u00d7*]\s*(?:₹|rs\.?|inr)?\s*({AMT_PAT})\s*(?:=|:)\s*(?:₹|rs\.?|inr)?\s*({AMT_PAT})",
        rf"(?:₹|rs\.?|inr)?\s*({AMT_PAT})\s*[x\u00d7*]\s*(\d{{1,3}})\s*(?:sessions?|days?)?\s*(?:=|:)\s*(?:₹|rs\.?|inr)?\s*({AMT_PAT})",
    ]
    for i, p in enumerate(pats):
        for m in re.finditer(p, text, re.I):
            ses = parse_int(m[1] if i == 0 else m[2])
            pri = parse_amount(m[2] if i == 0 else m[1])
            tot = parse_amount(m[3])
            if ses and pri and tot and ses <= 365:
                diff = abs(ses*pri - tot) / max(1, tot)
                cands.append((ses, pri, tot, diff))
    if not cands: return 0, 0, 0
    cands.sort(key=lambda c: (c[3], -c[2]))
    b = cands[0]
    return (b[0], b[1], b[2]) if b[3] <= 0.2 else (0, 0, 0)

# ── Table extraction ──

def split_cells(line):
    s = [p.strip() for p in re.split(r"\s{2,}|\t|\|", line) if p.strip()]
    return s if len(s) >= 2 else [p.strip() for p in re.split(r"\s{1,}(?=(?:₹|rs\.?|inr|\d|\w))", line, flags=re.I) if p.strip()]

def norm_col(n): return re.sub(r"\s+", " ", re.sub(r"[|:_/\\-]+", " ", n.lower())).strip()

def find_col(headers, names):
    nh = [norm_col(h) for h in headers]
    nn = [norm_col(n) for n in names]
    for n in nn:
        if n in nh: return nh.index(n)
    for n in nn:
        for i, h in enumerate(nh):
            if h.startswith(n): return i
    for n in nn:
        for i, h in enumerate(nh):
            if n in h: return i
    return -1

def nums_from(cell):
    return [parse_amount(m[1]) for m in re.finditer(rf"({AMT_PAT})", cell, re.I) if parse_amount(m[1]) > 0]

def sess_from(cell):
    vs = [parse_int(m[1]) for m in re.finditer(r"\b([\dOolI|SBZ]{1,3})\b", cell) if 0 < parse_int(m[1]) <= 365]
    return min(vs) if vs else 0

def extract_table(text):
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.split("\n") if l.strip()]
    pa = ["price per session","rate per session","rate","unit price","fee","price"]
    sa = ["total sessions","sessions","qty","quantity","days","nos"]
    ta = ["total amount","grand total","total","amount"]
    best = {"price": 0, "sessions": 0, "total": 0, "score": 0}

    # Header-aware scan
    for i, hline in enumerate(lines):
        hp = split_cells(hline)
        if not hp: continue
        pc, sc, tc = find_col(hp, pa), find_col(hp, sa), find_col(hp, ta)
        matched = sum(1 for c in [pc, sc, tc] if c != -1)
        if matched < 2: continue
        for j in range(i+1, min(i+9, len(lines))):
            row = lines[j]
            if not re.search(r"\d", row): continue
            if re.search(r"date\b|period\b", row, re.I) and not re.search(r"₹|amount|total|rate|session", row, re.I): continue
            rc = split_cells(row)
            p = s = t = 0
            if rc and len(rc) >= max(pc, sc, tc)+1:
                if pc != -1: ns = nums_from(rc[pc]); p = ns[0] if ns else 0
                if sc != -1: s = sess_from(rc[sc])
                if tc != -1: ns = nums_from(rc[tc]); t = max(ns) if ns else 0
            if not p or not t:
                rn = nums_from(row)
                if len(rn) >= 2: t = max(t, max(rn)); p = p or min(rn)
            if not s: s = sess_from(row)
            if not p or not s or not t or s > 365: continue
            diff = abs(p*s - t) / max(1, t)
            if diff <= 0.3:
                sc2 = 8 + (3 if matched >= 3 else 1) + (2 if diff <= 0.1 else 0)
                if sc2 > best["score"]: best = {"price": p, "sessions": s, "total": t, "score": sc2}

    # Row-level scan
    def push(p, s, t, sc):
        nonlocal best
        if not p or not s or not t or s > 365: return
        d = abs(p*s - t) / max(1, t)
        q = sc + (4 if d <= 0.1 else 2 if d <= 0.25 else 0)
        if q > best["score"]: best = {"price": p, "sessions": s, "total": t, "score": q}

    for line in lines:
        if len(line) < 8: continue
        rn = [parse_amount(m[1]) for m in re.finditer(rf"({AMT_PAT})", line, re.I) if parse_amount(m[1]) > 0]
        ri = [parse_int(m[1]) for m in re.finditer(r"\b([\dOolI|SBZ]{1,3})\b", line) if 0 < parse_int(m[1]) <= 365]
        if len(rn) >= 2 and ri and any(n >= 50 for n in rn) and re.search(r"₹|rs\.?|inr|price|rate|amount|total|qty", line, re.I):
            sn = sorted(rn)
            hh = 2 if re.search(r"price|rate|session|qty|amount|total", line, re.I) else 0
            for sc in ri:
                for ti in range(len(sn)-1, 0, -1):
                    for pi in range(ti):
                        if sn[pi] <= 1 and sn[ti] >= 500: continue
                        push(sn[pi], sc, sn[ti], 4+hh)
        cr = re.search(r"(?:₹|rs\.?|inr)\s*([\d,.]+).{0,30}?([\dOolI|SBZ]{1,3}).{0,30}?(?:₹|rs\.?|inr)?\s*([\d,.]+)", line, re.I)
        if cr: push(parse_amount(cr[1]), parse_int(cr[2]), parse_amount(cr[3]), 7)
    return best

def extract_currency_triplet(text):
    for line in [l.strip() for l in text.split("\n") if l.strip()]:
        if not re.search(r"₹|rs\.?|inr", line, re.I): continue
        m = re.search(rf"(?:₹|rs\.?|inr)\s*({AMT_PAT})\s*([\dOolI|SBZ]{{1,2}})\s*(?:₹|rs\.?|inr)\s*({AMT_PAT})", line, re.I)
        if not m: continue
        p, s, t = parse_amount(m[1]), parse_int(m[2]), parse_amount(m[3])
        if p and s and t and s <= 365 and abs(p*s - t)/max(1,t) <= 0.15:
            return {"price": p, "sessions": s, "total": t}
    return {"price": 0, "sessions": 0, "total": 0}

def derive_from_currency(text, sd, ed):
    amts = [parse_amount(m[1]) for m in re.finditer(rf"(?:₹|rs\.?|inr)\s*({AMT_PAT})", text, re.I)]
    amts = [a for a in amts if a > 0]
    if len(amts) < 2: return {"price": 0, "sessions": 0, "total": 0}
    total = max(amts)
    days = calc_days(sd, ed) if sd and ed else 0
    for p in sorted(set(a for a in amts if 0 < a < total), reverse=True):
        if total % p != 0: continue
        s = total // p
        if s <= 0 or s > 365: continue
        if days > 0 and (s == days or s <= days*4): return {"price": p, "sessions": s, "total": total}
        if days == 0 and s <= 31: return {"price": p, "sessions": s, "total": total}
    return {"price": 0, "sessions": 0, "total": 0}

def extract_service_rows(text, inv_date=""):
    rows = [l.strip() for l in text.split("\n") if l.strip()]
    rd = parse_date(inv_date) if inv_date else None
    ry = rd.year if rd else datetime.now().year
    price = sessions = training_total = accommodation = grand_total = 0
    start_date = end_date = ""

    for row in rows:
        cv = [parse_amount(m[1]) for m in re.finditer(rf"(?:₹|rs\.?|inr)\s*({AMT_PAT})", row, re.I) if parse_amount(m[1]) > 0]
        pv = [parse_amount(m[1]) for m in re.finditer(rf"({AMT_PAT})", row, re.I) if parse_amount(m[1]) > 0]

        if re.match(r"^\s*(?:grand\s*)?total\b", row, re.I):
            vals = cv or pv
            if vals: grand_total = max(grand_total, max(vals))
            continue
        if re.search(r"accommodation|accomodation", row, re.I):
            vals = cv or [v for v in pv if v >= 100]
            if vals: accommodation = max(accommodation, vals[-1])
            continue
        if not re.search(r"training|workshop|program|session", row, re.I): continue

        mv = cv if len(cv) >= 2 else [v for v in set(pv) if v >= 100]
        if len(mv) < 2: continue
        ua = sorted(set(v for v in mv if v >= 100))
        ints = [parse_int(m[1]) for m in re.finditer(r"\b([\dOolI|SBZ]{1,3})\b", row) if 0 < parse_int(m[1]) <= 365]

        row_best = None
        for q in ints:
            for tc in ua:
                for pr in ua:
                    if pr == tc: continue
                    candidates = [pr]
                    ds = str(pr)
                    if len(ds) >= 5:
                        stripped = int(ds[1:]) if ds[1:].isdigit() else 0
                        if stripped > 0: candidates.append(stripped)
                    for cp in candidates:
                        if cp <= 0: continue
                        d = abs(cp*q - tc) / max(1, tc)
                        if row_best is None or d < row_best[3]:
                            row_best = (cp, q, tc, d)

        if row_best and row_best[3] <= 0.15:
            price, sessions, training_total = row_best[0], row_best[1], row_best[2]

        if not start_date or not end_date:
            m = re.search(r"\(?\s*(\d{1,2})\s+([A-Za-z]{3,9})\s*[-\u2013]\s*(\d{1,2})\s+([A-Za-z]{3,9})\s*\)?", row, re.I)
            if m:
                s = parse_date(f"{m[1]} {m[2]} {ry}", ry)
                e = parse_date(f"{m[3]} {m[4]} {ry}", ry)
                if s and e and s > e: e = parse_date(f"{m[3]} {m[4]} {ry+1}", ry+1) or e
                if s: start_date = fmt_date(s)
                if e: end_date = fmt_date(e)
            if not start_date or not end_date:
                m = re.search(r"\(?\s*(\d{1,2})\s*[-\u2013]\s*(\d{1,2})\s+([A-Za-z]{3,9})\s*\)?", row, re.I)
                if m:
                    s = parse_date(f"{m[1]} {m[3]} {ry}", ry)
                    e = parse_date(f"{m[2]} {m[3]} {ry}", ry)
                    if s: start_date = fmt_date(s)
                    if e: end_date = fmt_date(e)

    if not grand_total and training_total > 0:
        grand_total = training_total + accommodation
    return {"price": price, "sessions": sessions, "trainingTotal": training_total,
            "accommodation": accommodation, "grandTotal": grand_total,
            "startDate": start_date, "endDate": end_date}

def extract_accommodation(text, lines):
    pats = [
        r"(?:accommodation|accomodation)\s*(?:charge|charges|cost|fee|amount)[:\s-]*(?:₹|rs\.?|inr)?\s*([\d,.\s]+)",
        r"(?:₹|rs\.?|inr)\s*([\d,.\s]+)\s*(?:for|towards)?\s*(?:accommodation|accomodation)",
    ]
    for line in lines:
        if not re.search(r"accommodation|accomodation", line, re.I): continue
        amts = list(re.finditer(r"(?:₹|rs\.?|inr)\s*([\d,.\s]+)", line, re.I))
        if amts:
            v = parse_amount(amts[-1][1])
            if v > 0: return v
        for p in pats:
            m = re.search(p, line, re.I)
            if m:
                v = parse_amount(m[1])
                if v > 0: return v
    for p in pats:
        m = re.search(p, text, re.I)
        if m:
            v = parse_amount(m[1])
            if v > 0: return v
    return 0

# ══════════════════════════════════════════════════════════════════════════════
# MAIN PARSER
# ══════════════════════════════════════════════════════════════════════════════

def parse_invoice_text(text):
    text = normalize_merged_cs(normalize_text(text))
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    data = {
        "invoiceNumber": "", "invoiceDate": "",
        "billedTo": "The Training and Placement Cell, College of Engineering Thalassery",
        "fromCompany": "", "fromEmail": "", "fromWebsite": "",
        "serviceDescription": "", "pricePerSession": 0, "totalSessions": 0,
        "totalAmount": 0, "accommodationCharge": 0,
        "accountNumber": "", "accountName": "", "ifscCode": "", "bankName": "",
        "startDate": "", "endDate": "",
    }

    # Invoice number
    for line in lines:
        nl = compact_inv(line)
        labeled = re.search(r"(?:invoice|bill)\s*(?:no\.?|number|#)\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9\/-]{1,})", nl, re.I)
        direct = re.match(r"^((?:INV|INVOICE|BILL)[A-Za-z0-9\/-]*\d[A-Za-z0-9\/-]*)$", nl, re.I)
        c = (labeled[1] if labeled else "") or (direct[1] if direct else "")
        c = c.strip()
        if not c or re.match(r"^(invoice|bill|number|no|na)$", c, re.I): continue
        if re.match(r"^\d{1,2}[\/\-.]\d{1,2}[\/\-.]\d{2,4}$", c): continue
        if not re.search(r"date|amount|total", c, re.I):
            data["invoiceNumber"] = c; break

    if not data["invoiceNumber"]:
        ct = compact_inv(text)
        for p in [r"\b((?:INV|INVOICE|BILL)[A-Za-z0-9\/-]{2,})\b", r"\b([A-Z]{2,6}[\/-]\d{1,6}(?:[\/-]\d{1,6})?)\b"]:
            m = re.search(p, ct, re.I)
            if m:
                c = m[1].strip()
                if re.match(r"^(invoice|bill)$", c, re.I): continue
                if not re.search(r"\d", c) and not re.search(r"[\/-]", c): continue
                if not re.search(r"date|amount|total", c, re.I):
                    data["invoiceNumber"] = c; break

    # Invoice date
    for p in [rf"(?:invoice\s*date|date\s*of\s*invoice|^\s*date)[:\s-]*({DATE_PAT})", rf"(?:date)[:\s]+({DATE_PAT})", rf"\b({DATE_PAT})\b"]:
        try:
            m = re.search(p, text, re.I|re.M)
            if m: data["invoiceDate"] = norm_date_out(m[1]); break
        except: continue

    # From company (side-by-side layout)
    bf_idx = next((i for i, l in enumerate(lines) if re.search(r"billed\s*to", l, re.I) and re.search(r"\bfrom\b", l, re.I)), -1)
    if bf_idx != -1:
        rp = []
        for i in range(bf_idx+1, min(bf_idx+5, len(lines))):
            l = lines[i]
            if re.search(r"payment\s*details|account\s*number|ifsc|bank", l, re.I): break
            if re.search(r"\bservices?\b|\bprice\b|\btotal\s*sessions?\b|\btotal\b", l, re.I): break
            tc = [p.strip() for p in re.split(r"\s{4,}", l) if p.strip()]
            if len(tc) >= 2:
                r = tc[-1]
                if r and not re.search(r"[\w.-]+@[\w.-]+\.\w+", r) and not re.search(r"(?:https?://|www\.)", r, re.I):
                    rp.append(r)
            else:
                mp = re.split(r"the\s+training\s+and\s+placement\s+cell|college\s+of\s+engineering|thalasseri", l, flags=re.I)
                mp = [s.strip() for s in mp if s.strip()]
                if mp:
                    r = mp[-1]
                    if r and not re.search(r"[\w.-]+@[\w.-]+\.\w+", r) and not re.search(r"(?:https?://|www\.)", r, re.I):
                        rp.append(r)
        if rp: data["fromCompany"] = " ".join(rp).strip()

    if not data["fromCompany"]:
        fi = next((i for i, l in enumerate(lines) if re.match(r"^from\b[:\s-]*", l, re.I)), -1)
        if fi != -1:
            src = []
            sl = re.sub(r"^from\b[:\s-]*", "", lines[fi], flags=re.I).strip()
            if sl: src.append(sl)
            for i in range(fi+1, min(fi+4, len(lines))):
                l = lines[i].strip()
                if not l: continue
                if re.match(r"^(to|bill\s*to|invoice|date|total|amount|rate|price|session|period)\b", l, re.I): break
                if re.search(r"payment\s*details|account\s*number|ifsc|bank", l, re.I): break
                if re.search(r"[\w.-]+@[\w.-]+\.\w+", l) or re.search(r"(?:https?://|www\.)", l, re.I): break
                src.append(l)
            if src: data["fromCompany"] = " ".join(src).strip()

    if not data["fromCompany"]:
        orgline = next((l for l in lines if re.search(r"\b(?:academy|technologies|solutions?|services?|institute|ltd|inc)\b", l, re.I) and not re.search(r"[\w.-]+@[\w.-]+\.\w+", l)), None)
        if orgline:
            data["fromCompany"] = re.sub(r"\b(?:the\s+training\s+and\s+placement\s+cell|college\s+of\s+engineering|thalasseri)\b", " ", orgline, flags=re.I).strip()

    if re.search(r"training\s+and\s+placement\s*cell|college\s+of\s+engineering\s+thalassery", data["fromCompany"], re.I):
        data["fromCompany"] = ""

    # Email + website
    for line in lines:
        if not data["fromEmail"]:
            m = re.search(r"([\w.-]+\s*@\s*[\w.-]+\s*\.\s*\w+)", line, re.I)
            if m: data["fromEmail"] = re.sub(r"\s+", "", m[1])
        if not data["fromWebsite"]:
            m = re.search(r"((?:https?://\s*)?(?:www\s*\.)?[\w-]+(?:\s*\.\s*[\w-]+)+)", line, re.I)
            if m: data["fromWebsite"] = re.sub(r"\s+", "", m[1]).lstrip("https://").lstrip("http://")
        if data["fromEmail"] and data["fromWebsite"]: break

    # Run all extractors
    tbl = extract_table(text)
    trip = extract_currency_triplet(text)
    srv = extract_service_rows(text, data["invoiceDate"])

    sd, ed = extract_date_range(text, data["invoiceDate"])
    data["startDate"], data["endDate"] = sd, ed
    if not sd and not ed:
        if srv["startDate"] and srv["endDate"]:
            data["startDate"], data["endDate"] = srv["startDate"], srv["endDate"]
        else:
            data["startDate"], data["endDate"] = extract_inline_dates(text, data["invoiceDate"])

    cur = derive_from_currency(text, data["startDate"], data["endDate"])
    eq_s, eq_p, eq_t = extract_equation(text)

    # Sessions
    data["totalSessions"] = srv["sessions"] or eq_s or tbl["sessions"] or trip["sessions"] or cur["sessions"] or extract_sessions(text)
    if not data["totalSessions"] and data["startDate"] and data["endDate"]:
        data["totalSessions"] = calc_days(data["startDate"], data["endDate"])

    # Financials
    data["totalAmount"] = srv["grandTotal"] or eq_t or tbl["total"] or trip["total"] or cur["total"] or extract_total(text)
    data["pricePerSession"] = srv["price"] or eq_p or tbl["price"] or trip["price"] or cur["price"] or extract_price(text)

    if cur["total"] >= 500 and data["totalAmount"] < cur["total"] * 0.5:
        data["totalAmount"], data["pricePerSession"], data["totalSessions"] = cur["total"], cur["price"], cur["sessions"]

    # Accommodation
    data["accommodationCharge"] = srv["accommodation"] or extract_accommodation(text, lines)
    if data["accommodationCharge"] > 0:
        sub = data["pricePerSession"] * data["totalSessions"]
        exp = sub + data["accommodationCharge"]
        gt = extract_total(text)
        if gt > 0 and abs(gt - exp) / max(1, exp) <= 0.1:
            data["totalAmount"] = gt
        elif sub > 0:
            data["totalAmount"] = exp

    # Smart fallbacks
    if not data["pricePerSession"] and data["totalAmount"] and data["totalSessions"]:
        data["pricePerSession"] = max(1, round(data["totalAmount"] / data["totalSessions"]))
    if not data["totalSessions"] and data["totalAmount"] and data["pricePerSession"]:
        data["totalSessions"] = max(1, round(data["totalAmount"] / data["pricePerSession"]))
    if not data["totalAmount"] and data["pricePerSession"] and data["totalSessions"]:
        data["totalAmount"] = data["pricePerSession"] * data["totalSessions"]

    # Consistency corrections
    if data["totalAmount"] and data["pricePerSession"] and data["totalSessions"]:
        sub = data["pricePerSession"] * data["totalSessions"]
        if data["accommodationCharge"] <= 0:
            diff = abs(sub - data["totalAmount"]) / max(1, data["totalAmount"])
            low_price = data["pricePerSession"] < 100 and data["totalAmount"] >= 500
            if diff > 0.2 or low_price:
                for src in [(eq_s, eq_p, eq_t), (tbl["sessions"], tbl["price"], tbl["total"]),
                            (trip["sessions"], trip["price"], trip["total"]), (cur["sessions"], cur["price"], cur["total"])]:
                    if all(src):
                        data["totalSessions"], data["pricePerSession"], data["totalAmount"] = src
                        break
                else:
                    if data["totalAmount"] and data["totalSessions"]:
                        data["pricePerSession"] = max(1, round(data["totalAmount"] / data["totalSessions"]))
                data["totalAmount"] = data["pricePerSession"] * data["totalSessions"]

    if data["pricePerSession"] and data["totalAmount"] and data["pricePerSession"] > data["totalAmount"]:
        data["pricePerSession"] = max(1, data["totalAmount"])

    # Derive missing dates
    if data["startDate"] and not data["endDate"] and data["totalSessions"] > 1:
        d = parse_date(data["startDate"])
        if d: data["endDate"] = fmt_date(d + timedelta(days=data["totalSessions"]-1))
    if not data["startDate"] and data["endDate"] and data["totalSessions"] > 1:
        d = parse_date(data["endDate"])
        if d: data["startDate"] = fmt_date(d - timedelta(days=data["totalSessions"]-1))

    # Service description
    for p in [r"\b(S\d+\s+\w+\s+\w+\s+Training)\b", r"([A-Za-z][A-Za-z0-9 &\/-]{2,80}\s+Training)"]:
        m = re.search(p, text, re.I)
        if m:
            data["serviceDescription"] = re.sub(r"\s*\(.*?\)\s*", "", m[1]).strip()
            break

    # Bank details
    m = re.search(r"Account\s*(?:number|no\.?)\s*[:\s]*(\d[\d\s]*)", text, re.I)
    if m: data["accountNumber"] = re.sub(r"\s+", "", m[1])
    m = re.search(r"Account\s*name\s*[:\s]*(.+?)(?:\n|IFSC)", text, re.I)
    if m: data["accountName"] = m[1].strip().lstrip(": ")
    m = re.search(r"IFSC\s*(?:Code)?\s*[:\s]*([A-Z0-9]+)", text, re.I)
    if m: data["ifscCode"] = m[1]
    m = re.search(r"Bank\s*[:\s]*(.+?)(?:\n|$)", text, re.I)
    if m: data["bankName"] = m[1].strip().lstrip(": ")

    return data

# ══════════════════════════════════════════════════════════════════════════════
# PDF GENERATION
# ══════════════════════════════════════════════════════════════════════════════

def number_to_words(num):
    num = int(num) if isinstance(num, (float, str)) else num
    ones = ['','One','Two','Three','Four','Five','Six','Seven','Eight','Nine',
            'Ten','Eleven','Twelve','Thirteen','Fourteen','Fifteen','Sixteen',
            'Seventeen','Eighteen','Nineteen']
    tens = ['','','Twenty','Thirty','Forty','Fifty','Sixty','Seventy','Eighty','Ninety']
    if num == 0: return "Zero"
    if num < 20: return ones[num]
    if num < 100: return tens[num//10] + (" " + ones[num%10] if num%10 else "")
    if num < 1000: return ones[num//100] + " Hundred" + (" and " + number_to_words(num%100) if num%100 else "")
    if num < 100000: return number_to_words(num//1000) + " Thousand" + (" " + number_to_words(num%1000) if num%1000 else "")
    if num < 10000000: return number_to_words(num//100000) + " Lakh" + (" " + number_to_words(num%100000) if num%100000 else "")
    return str(num)

def _wrap(text, mx):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur)+1+len(w) > mx: lines.append(cur); cur = w
        else: cur = f"{cur} {w}" if cur else w
    if cur: lines.append(cur)
    return lines or [""]

def _int(v, d=0):
    try: return int(v)
    except: return d

def get_type_texts(tt):
    tt = (tt or "").lower()
    if tt == "coding":
        return {
            "expenditure": lambda t: f"The Placement Cell at the {t['collegeName']} organized coding training for {t.get('year','')} year students ({t['targetStudents']}) of {t['department']} department. The objective of the training is to acquaint students with comprehensive coverage of coding skill sets to do assessment in upcoming Placements.",
            "reportIntro": lambda t: f"A {t['numberOfDays']} day training program on basic coding in {t['trainingTopic']} was conducted for {t['targetStudents']} students from {t['startDate']} to {t['endDate']} as part of the placement-oriented training initiatives. The program aimed to introduce {t.get('year','')} year students to fundamental programming concepts and help them develop the logical thinking required for future technical assessments. {t['trainingTopic']}, being one of the most widely used languages in industry and recruitment, was chosen to give students an early advantage in their preparation.",
            "reportMiddle": lambda t: "During the sessions, students were guided through essential programming topics including variables, data types, control structures, loops, arrays, and functions. The training followed a hands-on approach, allowing students to practice each concept through interactive coding examples and short exercises. By the end of the program, students were able to write basic programs and apply their knowledge to problem-solving tasks.",
            "reportConclusion": lambda t: "Overall, the training received positive participation from the students, who showed enthusiasm and interest throughout the program. The sessions helped build their confidence in coding and laid a strong foundation for more advanced programming training in the coming years. The initiative successfully met its objective of preparing students early for placement-related technical skills.",
            "scheduleIntro": lambda t: f"The Placement Cell at the {t['collegeName']} conducted Coding training for {t.get('year','')} year students of {t['department']} department ({t['targetStudents']}), applied for improving the basic coding skills. The program aimed to equip students with the necessary skills to excel in programming, which is the major component of recruitment processes by Major MNCs.",
        }
    elif tt == "aptitude":
        return {
            "expenditure": lambda t: f"The Placement Cell at the {t['collegeName']} organized aptitude training for {t.get('year','')} year students ({t['targetStudents']}) of {t['department']} department. The objective of the training is to enhance students' quantitative, logical, and verbal reasoning abilities to excel in placement aptitude assessments.",
            "reportIntro": lambda t: f"A {t['numberOfDays']} day training program on aptitude skills covering {t['trainingTopic']} was conducted for {t['targetStudents']} students from {t['startDate']} to {t['endDate']} as part of the placement-oriented training initiatives. The program aimed to strengthen {t.get('year','')} year students' problem-solving abilities in quantitative aptitude, logical reasoning, and verbal ability.",
            "reportMiddle": lambda t: "During the sessions, students were guided through essential aptitude topics including number systems, percentages, time and work, probability, data interpretation, logical puzzles, and verbal reasoning. The training followed a practice-oriented approach with timed exercises, mock tests, and shortcut techniques.",
            "reportConclusion": lambda t: "Overall, the training received enthusiastic participation from the students. The sessions significantly improved their speed and accuracy in solving aptitude problems. The initiative successfully prepared students for the quantitative and reasoning sections of placement exams.",
            "scheduleIntro": lambda t: f"The Placement Cell at the {t['collegeName']} conducted Aptitude training for {t.get('year','')} year students of {t['department']} department ({t['targetStudents']}), applied for improving quantitative, logical, and verbal reasoning skills.",
        }
    elif tt == "soft skill":
        return {
            "expenditure": lambda t: f"The Placement Cell at the {t['collegeName']} organized soft skill training for {t.get('year','')} year students ({t['targetStudents']}) of {t['department']} department. The objective of the training is to develop students' communication, interpersonal, and professional skills essential for placement interviews and corporate readiness.",
            "reportIntro": lambda t: f"A {t['numberOfDays']} day training program on soft skills covering {t['trainingTopic']} was conducted for {t['targetStudents']} students from {t['startDate']} to {t['endDate']} as part of the placement-oriented training initiatives. The program focused on developing {t.get('year','')} year students' communication skills, interview etiquette, group discussion techniques, and professional presentation abilities.",
            "reportMiddle": lambda t: "During the sessions, students were trained in effective communication, body language, resume writing, email etiquette, group discussion strategies, and mock interview techniques. The training included interactive role-plays, group activities, and presentation exercises.",
            "reportConclusion": lambda t: "Overall, the training received active participation and positive feedback from the students. The sessions helped build their confidence in professional communication and interview readiness.",
            "scheduleIntro": lambda t: f"The Placement Cell at the {t['collegeName']} conducted Soft Skill training for {t.get('year','')} year students of {t['department']} department ({t['targetStudents']}), applied for improving communication and interpersonal skills.",
        }
    else:
        return {
            "expenditure": lambda t: f"The Placement Cell at the {t['collegeName']} organized {t['trainingType'].lower()} training for {t.get('year','')} year students ({t['targetStudents']}) of {t['department']} department. The objective of the training is to acquaint students with comprehensive coverage of {t['trainingType'].lower()} skill sets to do assessment in upcoming Placements.",
            "reportIntro": lambda t: f"A {t['numberOfDays']} day training program on {t['trainingType'].lower()} in {t['trainingTopic']} was conducted for {t['targetStudents']} students from {t['startDate']} to {t['endDate']} as part of the placement-oriented training initiatives.",
            "reportMiddle": lambda t: f"During the sessions, students were guided through essential {t['trainingType'].lower()} topics. The training followed a hands-on approach, allowing students to practice each concept through interactive examples and short exercises.",
            "reportConclusion": lambda t: f"Overall, the training received positive participation from the students. The sessions helped build their confidence in {t['trainingType'].lower()} and laid a strong foundation for more advanced training.",
            "scheduleIntro": lambda t: f"The Placement Cell at the {t['collegeName']} conducted {t['trainingType']} training for {t.get('year','')} year students of {t['department']} department ({t['targetStudents']}), applied for improving the basic {t['trainingType'].lower()} skills.",
        }

def generate_expenditure_pdf(data):
    fp = os.path.join(UPLOAD_FOLDER, "Expenditure_Statement.pdf")
    c = rl_canvas.Canvas(fp, pagesize=A4)
    w, h = A4
    inv, trn = data.get("invoice", {}), data.get("training", {})
    texts = get_type_texts(trn.get("trainingType", ""))
    y = h - 40*mm
    js = ParagraphStyle('J', parent=getSampleStyleSheet()['Normal'], fontName='Helvetica', fontSize=11, leading=15, alignment=4)

    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(w/2, y, "Submitted"); y -= 15*mm

    for para in [
        texts["expenditure"](trn),
        f"The training took place on {trn.get('startDate','N/A')} to {trn.get('endDate','N/A')}. Each day consisted of {trn.get('sessionsPerDay',2)} sessions (per class), from {trn.get('sessionTimings','9:00 AM - 4:00 PM')}."
    ]:
        p = Paragraph(para, js); _, ph = p.wrap(170*mm, 200*mm); p.drawOn(c, 20*mm, y-ph); y -= ph + 5*mm
    y -= 5*mm

    c.setFont("Helvetica-Bold", 13); c.drawCentredString(w/2, y, "Expenditure Statement"); y -= 10*mm
    c.setFont("Helvetica", 10); c.line(20*mm, y, 190*mm, y); y -= 5*mm

    nd = _int(trn.get("numberOfDays", 0))
    pps = _int(inv.get("pricePerSession", 0))
    sub = pps * nd

    c.drawString(22*mm, y, "1"); c.drawString(30*mm, y, f"Remuneration for Placement Training - {trn.get('trainingType','')}")
    y -= 5*mm; c.drawString(30*mm, y, f"Invoice No - {inv.get('invoiceNumber','')} dated {inv.get('invoiceDate','')}")
    y -= 5*mm; c.drawString(30*mm, y, f"Duration - {nd} days"); c.drawString(130*mm, y, f"{nd} * {pps} = Rs {sub}/-")
    y -= 6*mm; c.line(20*mm, y, 190*mm, y)

    accom = _int(inv.get("accommodationCharge", 0))
    if accom > 0:
        y -= 5*mm; c.drawString(22*mm, y, "2"); c.drawString(30*mm, y, "Accommodation Charges")
        c.drawString(160*mm, y, f"Rs {accom:,}/-"); y -= 6*mm; c.line(20*mm, y, 190*mm, y)

    total = _int(inv.get("totalAmount", sub + accom))
    y -= 5*mm; c.setFont("Helvetica-Bold", 10)
    c.drawString(22*mm, y, "Total"); c.drawString(160*mm, y, f"Rs {total:,}/-")
    y -= 3*mm; c.line(20*mm, y, 190*mm, y); y -= 10*mm

    for para in [
        f"As per the Expression of Interest received and recommendation by placement committee and sanction the above training program is satisfactory and the amount of Rs {total}/- may be sanctioned.",
        f"Kindly sanction an amount of Rs {total}/- (Rs {number_to_words(total)} only) from Training and Placement cell."
    ]:
        p = Paragraph(para, js); _, ph = p.wrap(170*mm, 200*mm); p.drawOn(c, 20*mm, y-ph); y -= ph + 8*mm

    c.setFont("Helvetica", 11)
    c.drawString(20*mm, y, "Tutor/ Faculty in charge:"); y -= 25*mm
    c.drawString(20*mm, y, "Principal"); c.drawString(130*mm, y, "Placement officer")
    c.save(); return fp

def generate_report_pdf(data):
    fp = os.path.join(UPLOAD_FOLDER, "Training_Report.pdf")
    c = rl_canvas.Canvas(fp, pagesize=A4)
    w, h = A4
    trn = data.get("training", {})
    texts = get_type_texts(trn.get("trainingType", ""))
    y = h - 40*mm
    js = ParagraphStyle('J', parent=getSampleStyleSheet()['Normal'], fontName='Helvetica', fontSize=11, leading=15, alignment=4)

    nd = _int(trn.get("numberOfDays", 0))
    c.setFont("Helvetica-Bold", 14)
    for line in _wrap(f"REPORT ON THE {nd} DAY TRAINING PROGRAMME ON {trn.get('trainingType','').upper()}", 70):
        c.drawCentredString(w/2, y, line); y -= 6*mm
    y -= 8*mm

    for key in ["reportIntro", "reportMiddle", "reportConclusion"]:
        p = Paragraph(texts[key](trn), js); _, ph = p.wrap(170*mm, 200*mm); p.drawOn(c, 20*mm, y-ph); y -= ph + 6*mm

    y -= 10*mm; c.setFont("Helvetica-Bold", 11)
    c.drawString(20*mm, y, "Tutor/ Faculty in charge:"); y -= 25*mm
    c.setFont("Helvetica", 11)
    c.drawString(20*mm, y, "Principal"); c.drawString(130*mm, y, "Placement officer")
    c.save(); return fp

def generate_schedule_pdf(data):
    fp = os.path.join(UPLOAD_FOLDER, "Training_Schedule.pdf")
    c = rl_canvas.Canvas(fp, pagesize=A4)
    w, h = A4
    trn, sched = data.get("training", {}), data.get("schedule", [])
    texts = get_type_texts(trn.get("trainingType", ""))
    y = h - 30*mm
    js = ParagraphStyle('Js', parent=getSampleStyleSheet()['Normal'], fontName='Helvetica', fontSize=10, leading=13, alignment=4)

    nd = _int(trn.get("numberOfDays", 0))
    c.setFont("Helvetica-Bold", 12)
    for line in _wrap(f"TRAINING SCHEDULE FOR REPORT OF THE {number_to_words(nd).upper()} DAY PLACEMENT TRAINING", 75):
        c.drawCentredString(w/2, y, line); y -= 6*mm
    y -= 8*mm

    p = Paragraph(texts["scheduleIntro"](trn), js); _, ph = p.wrap(170*mm, 200*mm); p.drawOn(c, 20*mm, y-ph); y -= ph + 6*mm

    c.setFont("Helvetica-Bold", 10); c.drawString(20*mm, y, "Objectives"); y -= 5*mm
    c.setFont("Helvetica", 10); c.drawString(20*mm, y, trn.get("objective", "")); y -= 8*mm
    c.setFont("Helvetica-Bold", 10); c.drawString(20*mm, y, "Program Details:"); y -= 5*mm
    c.setFont("Helvetica", 10)
    c.drawString(20*mm, y, f"Dates: {trn.get('startDate','')} to {trn.get('endDate','')}"); y -= 5*mm
    c.drawString(20*mm, y, f"Daily Sessions: {trn.get('sessionsPerDay',2)}"); y -= 5*mm
    c.drawString(20*mm, y, f"Session Timings: {trn.get('sessionTimings','')}"); y -= 10*mm

    tx = 20*mm; tw = 170*mm
    cw = [30*mm, 38*mm, 22*mm, 80*mm]
    hdrs = ["DATE", "TIME", "SESSION", "TOPICS"]
    c.setFont("Helvetica-Bold", 9)
    c.setFillColorRGB(0.9, 0.92, 0.96)
    c.rect(tx, y-3*mm, tw, 8*mm, fill=1, stroke=0)
    c.setFillColorRGB(0, 0, 0)
    cx = tx
    for i, h in enumerate(hdrs):
        c.rect(cx, y-3*mm, cw[i], 8*mm, fill=0, stroke=1)
        c.drawCentredString(cx+cw[i]/2, y, h); cx += cw[i]
    y -= 8*mm

    c.setFont("Helvetica", 9)
    for day in sched:
        tw2 = _wrap(day.get("topics",""), 35)
        rh = max(len(tw2)*4+2, 8)*mm
        cx = tx
        c.rect(cx, y-3*mm, cw[0], rh, fill=0, stroke=1); c.drawCentredString(cx+cw[0]/2, y, day.get("date","N/A")); cx += cw[0]
        c.rect(cx, y-3*mm, cw[1], rh, fill=0, stroke=1); c.drawCentredString(cx+cw[1]/2, y, day.get("time","")); cx += cw[1]
        c.rect(cx, y-3*mm, cw[2], rh, fill=0, stroke=1); c.drawCentredString(cx+cw[2]/2, y, str(day.get("sessions",2))); cx += cw[2]
        c.rect(cx, y-3*mm, cw[3], rh, fill=0, stroke=1)
        for j, tl in enumerate(tw2): c.drawString(cx+2*mm, y-j*4*mm, tl)
        y -= rh
        if y < 30*mm: c.showPage(); y = h - 20*mm; c.setFont("Helvetica", 9)

    y -= 15*mm; c.setFont("Helvetica-Bold", 11)
    c.drawString(20*mm, y, "Tutor/ Faculty in charge:"); y -= 25*mm
    c.setFont("Helvetica", 11)
    c.drawString(20*mm, y, "Principal"); c.drawString(130*mm, y, "Placement officer")
    c.save(); return fp

# ══════════════════════════════════════════════════════════════════════════════
# API ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def serve_index():
    return send_from_directory("../frontend", "index.html")

@app.route("/api/extract", methods=["POST"])
def extract_invoice():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["file"]
    if not file or not allowed_file(file.filename):
        return jsonify({"error": "Invalid file type"}), 400
    fn = secure_filename(file.filename)
    fp = os.path.join(UPLOAD_FOLDER, fn)
    file.save(fp)
    try:
        text = extract_text_from_pdf(fp) if fn.lower().endswith(".pdf") else extract_text_from_image(fp)
        return jsonify({"data": parse_invoice_text(text), "rawText": text})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        if os.path.exists(fp): os.remove(fp)

@app.route("/api/generate/<ttype>", methods=["POST"])
def generate_pdf(ttype):
    data = request.get_json()
    if not data: return jsonify({"error": "No data provided"}), 400
    try:
        gen = {"expenditure": generate_expenditure_pdf, "report": generate_report_pdf, "schedule": generate_schedule_pdf}
        if ttype not in gen: return jsonify({"error": "Unknown template type"}), 400
        path = gen[ttype](data)
        return send_file(path, as_attachment=True, download_name=os.path.basename(path))
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    print("SCANMAP Backend running at http://localhost:5000")
    app.run(debug=True, port=5000)
