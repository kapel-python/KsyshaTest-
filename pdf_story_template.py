"""
pdf_story_template.py — Album-style Couple Story PDF  v2
Design: warm editorial album, not corporate report
Supports: Cyrillic, emoji (stripped for clean rendering), long texts,
          inline photo embedding, media metadata display.
"""
import os
import html
import logging
from datetime import datetime, date as _date
from collections import defaultdict

from reportlab.lib import colors
from reportlab.lib.pagesizes import A5
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.lib.colors import HexColor, Color
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak,
    KeepTogether, Flowable, Table, TableStyle, HRFlowable,
    Image as RL_Image,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════
# PAGE  ·  FONTS
# ══════════════════════════════════════════════════════════════════

PAGE_W, PAGE_H = A5         # 419.5 × 595.3 pt
MG   = 15 * mm              # side margin  ≈ 42 pt
MG_T = 16 * mm              # top margin
MG_B = 14 * mm              # bottom margin
CW   = PAGE_W - 2 * MG     # content width ≈ 335 pt

FR = "Helvetica"
FB = "Helvetica-Bold"
FI = "Helvetica-Oblique"


def _load_fonts() -> None:
    global FR, FB, FI
    candidates = [
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
        ),
        (
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf",
        ),
    ]
    for r, b, i in candidates:
        if os.path.exists(r) and os.path.exists(b):
            try:
                pdfmetrics.registerFont(TTFont("FR", r))
                pdfmetrics.registerFont(TTFont("FB", b))
                pdfmetrics.registerFont(TTFont("FI", i if os.path.exists(i) else r))
                FR, FB, FI = "FR", "FB", "FI"
                logger.info(f"[PDF] Fonts loaded from {r}")
                return
            except Exception as ex:
                logger.warning(f"[PDF] Font load failed: {ex}")
    logger.warning("[PDF] Falling back to Helvetica — Cyrillic may not render")
    FB = "Helvetica-Bold"
    FI = "Helvetica-Oblique"


_load_fonts()

# ══════════════════════════════════════════════════════════════════
# COLOURS
# ══════════════════════════════════════════════════════════════════

BG       = HexColor("#FDFCF8")   # warm cream page background
C_DARK   = HexColor("#1A1A2E")   # deep navy — headings
C_BODY   = HexColor("#2D2D3F")   # body text
C_MUTED  = HexColor("#8B8B9A")   # secondary / meta text
C_ACCENT = HexColor("#7066CC")   # brand purple
C_PINK   = HexColor("#D98A9A")   # brand pink
C_BORDER = HexColor("#E0DBF0")   # card borders / dividers
C_CARD   = HexColor("#FFFFFF")   # card background

# Category badge colours
CAT_BG: dict = {
    "important_moments": HexColor("#FEF3C7"),   # amber-100
    "memories":          HexColor("#D1FAE5"),   # emerald-100
    "important_dates":   HexColor("#DBEAFE"),   # blue-100
    "events":            HexColor("#FCE7F3"),   # pink-100
    "wishes":            HexColor("#F0FDF4"),   # green-50
    "default":           HexColor("#F3F4F6"),   # gray-100
}
CAT_FG: dict = {
    "important_moments": HexColor("#92400E"),
    "memories":          HexColor("#065F46"),
    "important_dates":   HexColor("#1E40AF"),
    "events":            HexColor("#9D174D"),
    "wishes":            HexColor("#14532D"),
    "default":           HexColor("#374151"),
}
CAT_LABEL: dict = {
    "important_moments": "важный момент",
    "memories":          "воспоминание",
    "important_dates":   "важная дата",
    "events":            "событие",
    "wishes":            "желание",
    "default":           "запись",
}

MONTHS_NOM = [
    "", "январь","февраль","март","апрель","май","июнь",
    "июль","август","сентябрь","октябрь","ноябрь","декабрь",
]
MONTHS_GEN = [
    "", "января","февраля","марта","апреля","мая","июня",
    "июля","августа","сентября","октября","ноября","декабря",
]

# ══════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════

def _e(text) -> str:
    """HTML-escape for ReportLab Paragraph (XML-safe)."""
    return html.escape(str(text or ""))


def _days_word(n: int) -> str:
    n = abs(int(n)) % 100
    if 11 <= n <= 14:
        return "дней"
    n %= 10
    if n == 1:
        return "день"
    if 2 <= n <= 4:
        return "дня"
    return "дней"


def _parse_date(s) -> "_date | None":
    try:
        return _date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def _embed_image(path, max_w: float, max_h: float = 180):
    """Load local image into an RL_Image flowable, or return None."""
    if not path:
        return None
    p = str(path).strip()
    if not os.path.isfile(p):
        return None
    try:
        img = RL_Image(p)
        iw, ih = img.imageWidth, img.imageHeight
        if iw <= 0 or ih <= 0:
            return None
        ratio = min(max_w / iw, max_h / ih, 1.0)
        img.drawWidth  = iw * ratio
        img.drawHeight = ih * ratio
        return img
    except Exception as ex:
        logger.debug(f"[PDF] Image embed failed '{p}': {ex}")
        return None


# ══════════════════════════════════════════════════════════════════
# STYLES
# ══════════════════════════════════════════════════════════════════

def _ST() -> dict:
    def S(name, fn=None, sz=10, ld=None, col=None,
          al=TA_LEFT, sb=0, sa=0):
        return ParagraphStyle(
            name,
            fontName  = fn  or FR,
            fontSize  = sz,
            leading   = ld  or int(sz * 1.55),
            textColor = col or C_BODY,
            alignment = al,
            spaceBefore = sb,
            spaceAfter  = sa,
        )

    return {
        # ── Cover ──────────────────────────────────────────
        "cn":   S("cn",  FB, 26, 34, C_DARK,   TA_CENTER, sa=2),
        "ca":   S("ca",  FR, 16, 22, C_MUTED,  TA_CENTER, sa=2),
        "cm":   S("cm",  FR, 10, 16, C_MUTED,  TA_CENTER, sa=3),
        "csn":  S("csn", FB, 18, 24, C_ACCENT, TA_CENTER),
        "csl":  S("csl", FR, 8,  12, C_MUTED,  TA_CENTER),
        "ct":   S("ct",  FR, 7,  11, C_MUTED,  TA_CENTER),
        # ── TOC ────────────────────────────────────────────
        "toch": S("toch", FB, 14, 22, C_DARK, TA_LEFT, sb=0, sa=8),
        "toci": S("toci", FR, 10, 18, C_BODY, TA_LEFT),
        "tocs": S("tocs", FR, 8,  14, C_MUTED,TA_LEFT),
        # ── Section / grouping ─────────────────────────────
        "sh":  S("sh",  FB, 17, 25, C_DARK,   TA_LEFT, sb=4,  sa=8),
        "yh":  S("yh",  FB, 16, 24, C_ACCENT, TA_LEFT, sb=14, sa=2),
        "mh":  S("mh",  FI, 11, 17, C_MUTED,  TA_LEFT, sb=6,  sa=2),
        # ── Memory card ────────────────────────────────────
        "ctl": S("ctl", FB, 12, 18, C_DARK),
        "cme": S("cme", FR, 8,  12, C_MUTED, sa=4),
        "cbd": S("cbd", FR, 10, 17, C_BODY),
        "cmd": S("cmd", FI, 8,  12, C_MUTED, sb=3),
        # ── Stats page ─────────────────────────────────────
        "sv":  S("sv",  FB, 22, 30, C_ACCENT, TA_CENTER),
        "sl":  S("sl",  FR, 8,  12, C_MUTED,  TA_CENTER),
        "sth": S("sth", FB, 14, 22, C_DARK,   TA_LEFT, sb=0, sa=6),
        # ── Ending ─────────────────────────────────────────
        "eb":  S("eb",  FB, 16, 24, C_ACCENT, TA_CENTER, sb=0, sa=6),
        "es":  S("es",  FI, 10, 16, C_MUTED,  TA_CENTER),
        "emp": S("emp", FI, 10, 16, C_MUTED,  TA_CENTER, sb=6),
    }


# ══════════════════════════════════════════════════════════════════
# PAGE BACKGROUND CALLBACK
# ══════════════════════════════════════════════════════════════════

def _page_bg(canvas, doc) -> None:
    canvas.saveState()
    # Cream background
    canvas.setFillColor(BG)
    canvas.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
    # Subtle accent blobs (top-right, bottom-left)
    r, g, b = C_ACCENT.red, C_ACCENT.green, C_ACCENT.blue
    canvas.setFillColor(Color(r, g, b, alpha=0.032))
    canvas.circle(PAGE_W - 12, PAGE_H - 10, 70, stroke=0, fill=1)
    canvas.setFillColor(Color(r, g, b, alpha=0.018))
    canvas.circle(12, 16, 48, stroke=0, fill=1)
    canvas.restoreState()


# ══════════════════════════════════════════════════════════════════
# CUSTOM FLOWABLE: MemoryCard
# ══════════════════════════════════════════════════════════════════

_CARD_PAD = 10
_PILL_H   = 13


class MemoryCard(Flowable):
    """White rounded-corner card with category pill, title, meta, body, optional image."""

    def __init__(
        self,
        title: str,
        meta: str,
        body: str,
        category: str = "default",
        media_text: str = "",
        img_path: str = "",
        card_w: float = None,
        styles: dict = None,
    ):
        Flowable.__init__(self)
        self._st   = styles or _ST()
        self._title = str(title or "")
        self._meta  = str(meta or "")
        self._body  = str(body or "")
        self._cat   = category or "default"
        self._mtext = str(media_text or "")
        self._ipath = str(img_path or "")
        self._cw    = card_w or CW
        self._img   = None   # computed in wrap()
        self._paras = []     # computed in wrap()
        self._h     = 0

    # ── internal helpers ──────────────────────────────────

    def _build_paras(self):
        st  = self._st
        iw  = self._cw - _CARD_PAD * 2
        out = []
        if self._title:
            out.append(Paragraph(_e(self._title), st["ctl"]))
        if self._meta:
            out.append(Paragraph(_e(self._meta), st["cme"]))
        if self._body:
            out.append(Paragraph(_e(self._body), st["cbd"]))
        if self._mtext:
            out.append(Paragraph(_e(self._mtext), st["cmd"]))
        return out, iw

    # ── Flowable protocol ─────────────────────────────────

    def wrap(self, aw, ah):
        self._paras, iw = self._build_paras()
        h = _CARD_PAD + _PILL_H + 4  # top pad + pill
        for p in self._paras:
            _, ph = p.wrap(iw, ah)
            h += ph
        self._img = _embed_image(self._ipath, max_w=iw, max_h=160)
        if self._img:
            h += self._img.drawHeight + 6
        h += _CARD_PAD
        self._h   = max(h, 44)
        self.width  = self._cw
        self.height = self._h
        return self._cw, self._h

    def draw(self):
        c  = self.canv
        w, h = self._cw, self._h
        iw = w - _CARD_PAD * 2

        # Shadow
        c.saveState()
        c.setFillColor(Color(0, 0, 0, alpha=0.025))
        c.roundRect(1, -2, w, h, 8, stroke=0, fill=1)
        c.restoreState()

        # Card background + border
        c.saveState()
        c.setFillColor(C_CARD)
        c.setStrokeColor(C_BORDER)
        c.setLineWidth(0.4)
        c.roundRect(0, 0, w, h, 8, stroke=1, fill=1)
        c.restoreState()

        # Category pill
        cat_lbl = CAT_LABEL.get(self._cat, CAT_LABEL["default"])
        bg_col  = CAT_BG.get(self._cat, CAT_BG["default"])
        fg_col  = CAT_FG.get(self._cat, CAT_FG["default"])
        pill_w  = min(len(cat_lbl) * 5.3 + 14, iw)
        py      = h - _CARD_PAD - _PILL_H
        c.saveState()
        c.setFillColor(bg_col)
        c.roundRect(_CARD_PAD, py, pill_w, _PILL_H, 4, stroke=0, fill=1)
        c.setFillColor(fg_col)
        c.setFont(FR, 6.5)
        c.drawString(_CARD_PAD + 5, py + 3, cat_lbl)
        c.restoreState()

        y = py - 4  # below pill

        # Text paragraphs
        for p in self._paras:
            _, ph = p.wrap(iw, y)
            y -= ph
            p.drawOn(c, _CARD_PAD, y)

        # Embedded image
        if self._img:
            y -= 6
            y -= self._img.drawHeight
            self._img.drawOn(c, _CARD_PAD, y)


# ══════════════════════════════════════════════════════════════════
# COVER PAGE
# ══════════════════════════════════════════════════════════════════

def _build_cover(data: dict, st: dict) -> list:
    out = []
    names = data.get("couple_names") or ["—", "—"]
    n1    = str(names[0]) if names else "—"
    n2    = str(names[1]) if len(names) > 1 else "—"

    out.append(Spacer(1, 52))

    out.append(Paragraph(_e(n1), st["cn"]))
    out.append(Spacer(1, 4))
    out.append(Paragraph("&amp;", st["ca"]))
    out.append(Spacer(1, 4))
    out.append(Paragraph(_e(n2), st["cn"]))

    out.append(Spacer(1, 20))
    out.append(HRFlowable(width="55%", thickness=0.6, color=C_BORDER, hAlign="CENTER", spaceAfter=16))

    met_disp   = data.get("met_date_display", "")
    days_tog   = data.get("days_together")
    export_dt  = data.get("export_date", "")

    if met_disp:
        out.append(Paragraph(f"познакомились {_e(met_disp)}", st["cm"]))
    if days_tog is not None:
        out.append(Paragraph(f"вместе {days_tog} {_days_word(days_tog)}", st["cm"]))
    out.append(Spacer(1, 24))

    # Stats row
    stats  = data.get("stats", {})
    s_mem  = str(stats.get("memories", 0))
    s_ev   = str(stats.get("events",   0))
    s_wish = str(stats.get("wishes",   0))
    cw3    = CW / 3

    tdata = [
        [Paragraph(s_mem,  st["csn"]), Paragraph(s_ev,   st["csn"]), Paragraph(s_wish,  st["csn"])],
        [Paragraph("воспоминаний", st["csl"]), Paragraph("событий", st["csl"]), Paragraph("желаний", st["csl"])],
    ]
    tbl = Table(tdata, colWidths=[cw3, cw3, cw3])
    r, g, b = C_ACCENT.red, C_ACCENT.green, C_ACCENT.blue
    tbl.setStyle(TableStyle([
        ("ALIGN",      (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, -1), Color(r, g, b, 0.055)),
        ("TOPPADDING",    (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("LINEABOVE",     (0, 1), (-1, 1), 0.3, C_BORDER),
    ]))
    out.append(tbl)
    out.append(Spacer(1, 32))

    if export_dt:
        out.append(Paragraph(f"экспортировано {_e(export_dt)}", st["ct"]))

    out.append(PageBreak())
    return out


# ══════════════════════════════════════════════════════════════════
# TABLE OF CONTENTS  (generated when total items > 12)
# ══════════════════════════════════════════════════════════════════

def _build_toc(data: dict, st: dict) -> list:
    stats = data.get("stats", {})
    total = stats.get("memories", 0) + stats.get("events", 0) + stats.get("wishes", 0)
    if total < 13:
        return []

    out = [
        Paragraph("Содержание", st["toch"]),
        HRFlowable(width="100%", thickness=0.4, color=C_BORDER, spaceAfter=10),
    ]

    sections_data = data.get("sections", {})

    # Group memories by year
    mems = sections_data.get("memories", [])
    if mems:
        by_year: dict = defaultdict(int)
        for m in mems:
            d = _parse_date(m.get("date_iso", ""))
            year = str(d.year) if d else "?"
            by_year[year] += 1
        years = sorted(by_year.keys(), reverse=True)
        out.append(Paragraph("Воспоминания", st["toci"]))
        for yr in years:
            out.append(Paragraph(f"    {yr} — {by_year[yr]} {_items_word(by_year[yr])}",
                                 st["tocs"]))

    if sections_data.get("events"):
        cnt = len(sections_data["events"])
        out.append(Paragraph(f"События — {cnt}", st["toci"]))

    if sections_data.get("wishes"):
        cnt = len(sections_data["wishes"])
        out.append(Paragraph(f"Желания — {cnt}", st["toci"]))

    out.append(Spacer(1, 8))
    stats2 = data.get("stats", {})
    photos = stats2.get("photos", 0)
    if photos:
        out.append(Paragraph(f"Фотографий: {photos}", st["tocs"]))

    out.append(PageBreak())
    return out


def _items_word(n: int) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return "записей"
    n %= 10
    if n == 1: return "запись"
    if 2 <= n <= 4: return "записи"
    return "записей"


# ══════════════════════════════════════════════════════════════════
# SECTION HEADER HELPER
# ══════════════════════════════════════════════════════════════════

def _section_header(title: str, st: dict) -> list:
    return [
        PageBreak(),
        Spacer(1, 8),
        Paragraph(_e(title), st["sh"]),
        HRFlowable(width="100%", thickness=0.5, color=C_BORDER, spaceAfter=6),
    ]


# ══════════════════════════════════════════════════════════════════
# MEMORIES  (grouped by year → month)
# ══════════════════════════════════════════════════════════════════

def _build_memories(data: dict, st: dict) -> list:
    items = (data.get("sections") or {}).get("memories") or []
    if not items:
        return _section_header("Воспоминания", st) + [
            Spacer(1, 10),
            Paragraph("Воспоминаний пока нет.", st["emp"]),
        ]

    # Group by year → month
    by_year: dict = defaultdict(lambda: defaultdict(list))
    undated: list = []
    for item in items:
        d = _parse_date(item.get("date_iso", ""))
        if d:
            by_year[d.year][d.month].append(item)
        else:
            undated.append(item)

    out = _section_header("Воспоминания", st)

    for year in sorted(by_year.keys()):
        out.append(Paragraph(str(year), st["yh"]))
        for month in sorted(by_year[year].keys()):
            m_items = by_year[year][month]
            out.append(Paragraph(MONTHS_NOM[month].capitalize(), st["mh"]))
            for item in m_items:
                card = _make_card(item, st)
                out.append(KeepTogether([Spacer(1, 4), card]))
                out.append(Spacer(1, 8))

    if undated:
        out.append(Paragraph("Разное", st["mh"]))
        for item in undated:
            out.append(KeepTogether([Spacer(1, 4), _make_card(item, st)]))
            out.append(Spacer(1, 8))

    return out


def _make_card(item: dict, st: dict) -> MemoryCard:
    author = item.get("author", "")
    date   = item.get("date", "")
    meta   = f"{_e(date)}  ·  {_e(author)}" if author and date else _e(date or author)
    return MemoryCard(
        title      = item.get("title", ""),
        meta       = meta,
        body       = item.get("text", ""),
        category   = item.get("category", "default"),
        media_text = item.get("media_text", ""),
        img_path   = item.get("media_path", ""),
        styles     = st,
    )


# ══════════════════════════════════════════════════════════════════
# EVENTS
# ══════════════════════════════════════════════════════════════════

def _build_events(data: dict, st: dict) -> list:
    items = (data.get("sections") or {}).get("events") or []
    out   = _section_header("События", st)
    if not items:
        out.append(Paragraph("Событий пока нет.", st["emp"]))
        return out
    for item in items:
        out.append(KeepTogether([Spacer(1, 4), _make_card(item, st)]))
        out.append(Spacer(1, 8))
    return out


# ══════════════════════════════════════════════════════════════════
# WISHES
# ══════════════════════════════════════════════════════════════════

def _build_wishes(data: dict, st: dict) -> list:
    items = (data.get("sections") or {}).get("wishes") or []
    out   = _section_header("Желания", st)
    if not items:
        out.append(Paragraph("Желаний пока нет.", st["emp"]))
        return out
    for item in items:
        out.append(KeepTogether([Spacer(1, 4), _make_card(item, st)]))
        out.append(Spacer(1, 8))
    return out


# ══════════════════════════════════════════════════════════════════
# STATS PAGE
# ══════════════════════════════════════════════════════════════════

def _build_stats(data: dict, st: dict) -> list:
    s     = data.get("stats", {})
    days  = data.get("days_together")
    names = data.get("couple_names", ["—", "—"])

    out = [
        PageBreak(),
        Spacer(1, 12),
        Paragraph("Статистика пары", st["sth"]),
        HRFlowable(width="100%", thickness=0.4, color=C_BORDER, spaceAfter=10),
    ]

    # 3-column grid, 2 rows
    stats_items = [
        (str(s.get("memories",  0)), "воспоминаний"),
        (str(s.get("events",    0)), "событий"),
        (str(s.get("wishes",    0)), "желаний"),
        (str(days) if days is not None else "—", _days_word(days) if days else "дней вместе"),
        (str(s.get("photos",    0)), "фотографий"),
        (str(s.get("videos",    0) + s.get("voices", 0) + s.get("files", 0)), "медиафайлов"),
    ]

    cw3 = CW / 3
    r, g, b = C_ACCENT.red, C_ACCENT.green, C_ACCENT.blue
    grid_style = TableStyle([
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND",    (0, 0), (-1, -1), Color(r, g, b, 0.045)),
        ("TOPPADDING",    (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("LINEBELOW",     (0, 0), (-1, 0),  0.3, C_BORDER),
        ("LINEABOVE",     (0, 1), (-1, 1),  0.3, C_BORDER),
    ])

    row1_vals  = [Paragraph(stats_items[i][0], st["sv"]) for i in range(3)]
    row1_lbls  = [Paragraph(stats_items[i][1], st["sl"]) for i in range(3)]
    row2_vals  = [Paragraph(stats_items[i][0], st["sv"]) for i in range(3, 6)]
    row2_lbls  = [Paragraph(stats_items[i][1], st["sl"]) for i in range(3, 6)]

    tbl1 = Table([row1_vals, row1_lbls], colWidths=[cw3] * 3)
    tbl1.setStyle(grid_style)
    tbl2 = Table([row2_vals, row2_lbls], colWidths=[cw3] * 3)
    tbl2.setStyle(grid_style)

    out += [tbl1, Spacer(1, 8), tbl2]
    return out


# ══════════════════════════════════════════════════════════════════
# ENDING PAGE
# ══════════════════════════════════════════════════════════════════

def _build_ending(data: dict, st: dict) -> list:
    today = datetime.now().strftime("%d.%m.%Y")
    return [
        PageBreak(),
        Spacer(1, 160),
        Paragraph("История продолжается", st["eb"]),
        Spacer(1, 16),
        Paragraph("Каждый момент, записанный здесь,", st["es"]),
        Paragraph("остаётся с вами навсегда", st["es"]),
        Spacer(1, 60),
        Paragraph(_e(today), st["ct"]),
    ]


# ══════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════

def generate_couple_story_pdf(data: dict, output_path: str) -> str:
    """
    Generate a PDF album from couple story data.
    Returns the absolute path to the created file.
    Raises ValueError on bad arguments; all other exceptions propagate with logging.
    """
    logger.info(f"[PDF] START: output={output_path}")

    if not isinstance(data, dict):
        raise ValueError("data must be a dict")
    if not output_path or not isinstance(output_path, str):
        raise ValueError("output_path must be a non-empty string")

    try:
        # Normalise couple_names
        names = list(data.get("couple_names") or [])
        while len(names) < 2:
            names.append("—")
        safe_data = dict(data)
        safe_data["couple_names"] = names[:2]

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        doc = SimpleDocTemplate(
            output_path,
            pagesize   = (PAGE_W, PAGE_H),
            leftMargin = MG,
            rightMargin= MG,
            topMargin  = MG_T,
            bottomMargin=MG_B,
        )

        st    = _ST()
        story = []

        logger.info("[PDF] Building cover")
        story += _build_cover(safe_data, st)

        logger.info("[PDF] Building TOC")
        story += _build_toc(safe_data, st)

        mem_count = len((safe_data.get("sections") or {}).get("memories") or [])
        ev_count  = len((safe_data.get("sections") or {}).get("events")   or [])
        wsh_count = len((safe_data.get("sections") or {}).get("wishes")   or [])

        logger.info(f"[PDF] Memories={mem_count}, Events={ev_count}, Wishes={wsh_count}")

        story += _build_memories(safe_data, st)
        story += _build_events(safe_data, st)
        story += _build_wishes(safe_data, st)
        story += _build_stats(safe_data, st)
        story += _build_ending(safe_data, st)

        logger.info(f"[PDF] Building document ({len(story)} flowables)…")
        doc.build(story, onFirstPage=_page_bg, onLaterPages=_page_bg)

        abs_path = os.path.abspath(output_path)
        logger.info(f"[PDF] SUCCESS: {abs_path}")
        return abs_path

    except Exception as e:
        logger.exception(f"[PDF] FAILED: {type(e).__name__}: {e}")
        raise


# ══════════════════════════════════════════════════════════════════
# CLI SMOKE-TEST
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import json, tempfile

    sample = {
        "couple_names":     ["Алиса", "Боб"],
        "met_date":         "2023-05-15",
        "met_date_display": "15 мая 2023",
        "days_together":    387,
        "export_date":      "06.06.2026",
        "period_start":     "15 мая 2023",
        "period_end":       "6 июня 2026",
        "stats": {
            "memories": 3, "events": 1, "wishes": 1,
            "photos": 2,   "videos": 0, "voices": 0, "files": 0,
        },
        "timeline": [],
        "sections": {
            "memories": [
                {
                    "title":    "Первый поход в кино",
                    "date":     "20 мая 2023",
                    "date_iso": "2023-05-20",
                    "text":     "Смотрели новый фильм, было так здорово...",
                    "author":   "Алиса",
                    "category": "memories",
                    "media_type": None, "media_path": None, "media_text": None,
                },
                {
                    "title":    "День рождения",
                    "date":     "10 августа 2023",
                    "date_iso": "2023-08-10",
                    "text":     "Отметили в любимом кафе — лучший день.",
                    "author":   "Боб",
                    "category": "important_moments",
                    "media_type": None, "media_path": None, "media_text": None,
                },
                {
                    "title":    "Новый год",
                    "date":     "1 января 2024",
                    "date_iso": "2024-01-01",
                    "text":     "Встретили вместе, было так весело и тепло!",
                    "author":   "Алиса",
                    "category": "important_dates",
                    "media_type": None, "media_path": None, "media_text": None,
                },
            ],
            "events": [
                {
                    "title":    "Годовщина",
                    "date":     "15 мая 2024",
                    "date_iso": "2024-05-15",
                    "text":     "Ровно год вместе — планируем что-то особенное.",
                    "author":   "Боб",
                    "category": "events",
                    "media_type": None, "media_path": None, "media_text": None,
                },
            ],
            "wishes": [
                {
                    "title":    "Желание",
                    "date":     "20 ноября 2023",
                    "date_iso": "2023-11-20",
                    "text":     "Съездить вместе в Японию весной.",
                    "author":   "Алиса",
                    "category": "wishes",
                    "media_type": None, "media_path": None, "media_text": None,
                },
            ],
        },
    }

    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "test_album.pdf")
        path = generate_couple_story_pdf(sample, out)
        size = os.path.getsize(path)
        print(f"PDF created: {path}  ({size // 1024} KB)")
