"""
pdf_story_template.py — SureMemory Couple Story PDF  v3
Design: faithfully mirrors /stats, /profile and /main visual system.
Colour tokens, card anatomy, typography hierarchy are all derived
from the site's CSS variables — not invented from scratch.
Supports: Cyrillic, emoji (stripped), long texts, inline images,
          media metadata, dark-cream warm background.
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
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
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
# PAGE  ·  MARGINS
# ══════════════════════════════════════════════════════════════════

PAGE_W, PAGE_H = A5          # 419.5 × 595.3 pt
MG    = 14 * mm              # side margin
MG_T  = 14 * mm              # top margin
MG_B  = 13 * mm              # bottom margin
CW    = PAGE_W - 2 * MG      # content width ≈ 337 pt

# ══════════════════════════════════════════════════════════════════
# FONTS
# ══════════════════════════════════════════════════════════════════

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
# COLOUR TOKENS  (mirrors stats.html CSS variables)
# ══════════════════════════════════════════════════════════════════
# --bg: #fdf6f9   --surface: #ffffff
# --accent: #d43f8d  --accent2: #7c3aed  --accent3: #4158d0
# --text: #1a1a2e   --muted: #9b7fa8   --meta: #c4a8cc
# --pink-soft: #fff0f7  --pink-border: #fce4f2

BG           = HexColor("#FDF6F9")   # warm rose-cream page background
C_SURFACE    = HexColor("#FFFFFF")   # card fill
C_DARK       = HexColor("#1A1A2E")   # --text  headings
C_BODY       = HexColor("#2D2840")   # body text (slightly lighter than heading)
C_MUTED      = HexColor("#9B7FA8")   # --muted secondary
C_META       = HexColor("#C4A8CC")   # --meta  tiny labels
C_ACCENT     = HexColor("#D43F8D")   # --accent pink (primary brand)
C_ACCENT2    = HexColor("#7C3AED")   # --accent2 violet
C_ACCENT3    = HexColor("#4158D0")   # --accent3 indigo
C_PINK_SOFT  = HexColor("#FFF0F7")   # --pink-soft  badge fill
C_PINK_BRD   = HexColor("#FCE4F2")   # --pink-border  card border

# Stripe colours taken from .mc1–mc4 / .sc1–sc6 in stats.html
STRIPE_PINK   = (HexColor("#FF9FD4"), HexColor("#D43F8D"))
STRIPE_ORANGE = (HexColor("#FFB347"), HexColor("#FF6B35"))
STRIPE_VIOLET = (HexColor("#7C3AED"), HexColor("#4158D0"))
STRIPE_GREEN  = (HexColor("#16A34A"), HexColor("#0D9488"))
STRIPE_ROSE   = (HexColor("#DB2777"), HexColor("#BE185D"))
STRIPE_AMBER  = (HexColor("#F59E0B"), HexColor("#D97706"))

# Category → stripe colour mapping
CAT_STRIPE = {
    "important_moments": STRIPE_ORANGE,
    "memories":          STRIPE_PINK,
    "important_dates":   STRIPE_VIOLET,
    "events":            STRIPE_ROSE,
    "wishes":            STRIPE_GREEN,
    "default":           STRIPE_AMBER,
}

# Category badge (pill) colours — same as v2 palette, now consistent with site
CAT_BG: dict = {
    "important_moments": HexColor("#FEF3C7"),
    "memories":          HexColor("#FCE7F3"),
    "important_dates":   HexColor("#EDE9FE"),
    "events":            HexColor("#FCE7F3"),
    "wishes":            HexColor("#D1FAE5"),
    "default":           HexColor("#F3F0FF"),
}
CAT_FG: dict = {
    "important_moments": HexColor("#92400E"),
    "memories":          HexColor("#9D174D"),
    "important_dates":   HexColor("#5B21B6"),
    "events":            HexColor("#9D174D"),
    "wishes":            HexColor("#065F46"),
    "default":           HexColor("#5B21B6"),
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


def _items_word(n: int) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return "записей"
    n %= 10
    if n == 1: return "запись"
    if 2 <= n <= 4: return "записи"
    return "записей"


def _parse_date(s) -> "_date | None":
    try:
        return _date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def _embed_image(path, max_w: float, max_h: float = 180):
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


def _rgba(hex_color: HexColor, alpha: float) -> Color:
    return Color(hex_color.red, hex_color.green, hex_color.blue, alpha)


# ══════════════════════════════════════════════════════════════════
# PARAGRAPH STYLES  (mirrors /stats typography)
# ══════════════════════════════════════════════════════════════════

def _ST() -> dict:
    def S(name, fn=None, sz=10, ld=None, col=None,
          al=TA_LEFT, sb=0, sa=0):
        return ParagraphStyle(
            name,
            fontName    = fn  or FR,
            fontSize    = sz,
            leading     = ld  or int(sz * 1.55),
            textColor   = col or C_BODY,
            alignment   = al,
            spaceBefore = sb,
            spaceAfter  = sa,
        )

    return {
        # ── Cover ──────────────────────────────────────────────────
        # hero-title: Playfair Display 900 → FB large, centred
        "cn":   S("cn",  FB, 28, 36, C_DARK,    TA_CENTER, sa=2),
        # hero-sub ampersand
        "ca":   S("ca",  FR, 15, 21, C_MUTED,   TA_CENTER, sa=2),
        # hero-sub  date/days line
        "cm":   S("cm",  FR, 9,  15, C_MUTED,   TA_CENTER, sa=2),
        # counter-num (big gradient number equivalent) → FB large accent
        "csn":  S("csn", FB, 20, 26, C_ACCENT,  TA_CENTER),
        # counter-label (tiny uppercase)
        "csl":  S("csl", FR, 7,  11, C_META,    TA_CENTER),
        # tiny timestamp
        "ct":   S("ct",  FR, 6.5, 10, C_META,   TA_CENTER),
        # hero-badge text
        "cbg":  S("cbg", FB, 7,  10, C_ACCENT,  TA_CENTER),

        # ── Table of contents ──────────────────────────────────────
        "toch": S("toch", FB, 13, 20, C_DARK,   TA_LEFT, sb=0,  sa=8),
        "toci": S("toci", FR, 10, 17, C_BODY,   TA_LEFT),
        "tocs": S("tocs", FR, 8,  13, C_MUTED,  TA_LEFT),

        # ── Section headers ───────────────────────────────────────
        # section-title style (.section-title in stats.html: uppercase, muted)
        "sh":  S("sh",  FB, 7,  11, C_MUTED,  TA_LEFT, sb=4,  sa=5),
        # year heading — accent (mirrors .hm-day .d5 colour)
        "yh":  S("yh",  FB, 15, 22, C_ACCENT, TA_LEFT, sb=14, sa=2),
        # month label — muted italic (mirrors stat-card-sub)
        "mh":  S("mh",  FI, 10, 16, C_MUTED,  TA_LEFT, sb=6,  sa=2),

        # ── Memory card content ────────────────────────────────────
        # stat-card-value (bold, --text)
        "ctl": S("ctl", FB, 11, 17, C_DARK),
        # stat-card-sub (small muted)
        "cme": S("cme", FR, 7.5, 12, C_MUTED, sa=3),
        # body text
        "cbd": S("cbd", FR, 9.5, 16, C_BODY),
        # media meta italic
        "cmd": S("cmd", FI, 7.5, 12, C_META,  sb=3),

        # ── Stats block (mirrors .counter-wrap + .mini-card) ───────
        # counter-num equivalent (big white-on-gradient number)
        "sv":  S("sv",  FB, 20, 28, C_SURFACE, TA_CENTER),
        # counter-label equivalent (small white-on-gradient label)
        "sl":  S("sl",  FR, 7,  11, HexColor("#FFDDF0"), TA_CENTER),
        # mini-card-value
        "smv": S("smv", FB, 14, 20, C_DARK,   TA_CENTER),
        # mini-card-label
        "sml": S("sml", FR, 6.5, 10, C_MUTED, TA_CENTER),
        # section header
        "sth": S("sth", FB, 7,  11, C_MUTED,  TA_LEFT, sb=0,  sa=6),

        # ── Category section big title (like page section in stats) ─
        "sech": S("sech", FB, 16, 22, C_DARK, TA_LEFT, sb=2, sa=4),

        # ── Ending page ────────────────────────────────────────────
        "eb":  S("eb",  FB, 15, 22, C_ACCENT,  TA_CENTER, sb=0, sa=6),
        "es":  S("es",  FI, 9,  15, C_MUTED,   TA_CENTER),
        "emp": S("emp", FI, 9,  15, C_META,    TA_CENTER, sb=6),
    }


# ══════════════════════════════════════════════════════════════════
# PAGE BACKGROUND  (mirrors stats.html body::before / body::after)
# ══════════════════════════════════════════════════════════════════

def _page_bg(canvas, doc) -> None:
    canvas.saveState()

    # Warm rose-cream base (--bg: #fdf6f9)
    canvas.setFillColor(BG)
    canvas.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)

    # body::before — top-right radial pink glow
    canvas.setFillColor(_rgba(C_ACCENT, 0.055))
    canvas.circle(PAGE_W + 10, PAGE_H + 10, 90, stroke=0, fill=1)

    # body::after — bottom-left radial indigo glow
    canvas.setFillColor(_rgba(C_ACCENT3, 0.04))
    canvas.circle(-10, -10, 70, stroke=0, fill=1)

    # Footer page number
    canvas.setFont(FB, 6)
    canvas.setFillColor(C_META)
    page_num = str(canvas.getPageNumber())
    canvas.drawCentredString(PAGE_W / 2, 9, page_num)

    canvas.restoreState()


# ══════════════════════════════════════════════════════════════════
# CUSTOM FLOWABLE: GradientBox  (mirrors .counter-wrap)
# ══════════════════════════════════════════════════════════════════

class GradientBox(Flowable):
    """Full-width gradient banner — mirrors .counter-wrap from stats.html.
    Renders a pink→violet→indigo gradient rectangle with circular overlays."""

    def __init__(self, rows: list, box_w: float = None, pad_h: float = 28,
                 radius: float = 18):
        """
        rows: list of (Paragraph, height_estimate) tuples
        """
        Flowable.__init__(self)
        self._rows   = rows    # list of Paragraph objects
        self._bw     = box_w or CW
        self._pad_h  = pad_h
        self._pad_x  = 16
        self._radius = radius
        self._h      = 0

    def wrap(self, aw, ah):
        iw = self._bw - self._pad_x * 2
        h  = self._pad_h
        for p in self._rows:
            _, ph = p.wrap(iw, ah)
            h += ph + 2
        h += self._pad_h
        self._h      = max(h, 60)
        self.width   = self._bw
        self.height  = self._h
        return self._bw, self._h

    def draw(self):
        c   = self.canv
        w, h = self._bw, self._h
        r    = self._radius

        # Gradient simulation: three overlapping rects (pink, violet, indigo)
        c.saveState()
        # Base pink layer
        c.setFillColor(C_ACCENT)
        c.roundRect(0, 0, w, h, r, stroke=0, fill=1)
        # Violet overlay (right half, blended)
        c.setFillColor(_rgba(C_ACCENT2, 0.72))
        c.roundRect(w * 0.3, 0, w * 0.7, h, r, stroke=0, fill=1)
        # Indigo accent (far right)
        c.setFillColor(_rgba(C_ACCENT3, 0.55))
        c.roundRect(w * 0.6, 0, w * 0.4, h, r, stroke=0, fill=1)
        c.restoreState()

        # ::before — top-right decorative circle
        c.saveState()
        c.setFillColor(Color(1, 1, 1, alpha=0.07))
        c.circle(w - 20, h + 20, 60, stroke=0, fill=1)
        # ::after — bottom-left decorative circle
        c.setFillColor(Color(1, 1, 1, alpha=0.05))
        c.circle(-10, -20, 50, stroke=0, fill=1)
        c.restoreState()

        # Text rows
        iw = w - self._pad_x * 2
        y  = h - self._pad_h
        for p in self._rows:
            _, ph = p.wrap(iw, h)
            y -= ph
            p.drawOn(c, self._pad_x, y)
            y -= 2


# ══════════════════════════════════════════════════════════════════
# CUSTOM FLOWABLE: MiniStatCard  (mirrors .mini-card from stats.html)
# ══════════════════════════════════════════════════════════════════

class MiniStatCard(Flowable):
    """White card with coloured top stripe — mirrors .mini-card + .mc1–mc4."""

    def __init__(self, value: str, label: str,
                 stripe: tuple = None,
                 card_w: float = None, styles: dict = None):
        Flowable.__init__(self)
        self._st     = styles or _ST()
        self._value  = value
        self._label  = label
        self._stripe = stripe or STRIPE_PINK
        self._cw     = card_w or (CW / 2 - 4)
        self._h      = 0

    def wrap(self, aw, ah):
        self.width  = self._cw
        self.height = 56
        self._h     = 56
        return self._cw, self._h

    def draw(self):
        c   = self.canv
        w, h = self._cw, self._h
        r    = 12

        # Card background + border (--surface + --pink-border)
        c.saveState()
        c.setFillColor(C_SURFACE)
        c.setStrokeColor(C_PINK_BRD)
        c.setLineWidth(0.8)
        c.roundRect(0, 0, w, h, r, stroke=1, fill=1)
        c.restoreState()

        # Top stripe (3px, mirrors mini-card::before)
        c.saveState()
        c.setFillColor(self._stripe[0])
        c.roundRect(0, h - 3, w / 2, 3, 0, stroke=0, fill=1)
        c.setFillColor(self._stripe[1])
        c.roundRect(w / 2, h - 3, w / 2, 3, 0, stroke=0, fill=1)
        # Round the top corners
        c.setFillColor(self._stripe[0])
        c.roundRect(0, h - 3, w, 3, r, stroke=0, fill=1)
        c.restoreState()

        # Value + label text
        pad = 10
        iw  = w - pad * 2
        pv  = Paragraph(_e(self._value), self._st["smv"])
        pl  = Paragraph(_e(self._label), self._st["sml"])
        _, vh = pv.wrap(iw, h)
        _, lh = pl.wrap(iw, h)
        total = vh + lh + 3
        base  = (h - total) / 2
        pv.drawOn(c, pad, base + lh + 3)
        pl.drawOn(c, pad, base)


# ══════════════════════════════════════════════════════════════════
# CUSTOM FLOWABLE: MemoryCard  (mirrors .stat-card from stats.html)
# ══════════════════════════════════════════════════════════════════

_CARD_PAD   = 11    # internal horizontal padding
_CARD_PAD_V = 10    # internal vertical padding
_STRIPE_W   = 4     # left vertical stripe width (mirrors .stat-card-stripe)
_PILL_H     = 14    # category pill height


class MemoryCard(Flowable):
    """White rounded card with left colour stripe + category pill.
    Mirrors .stat-card from stats.html exactly."""

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
        self._st    = styles or _ST()
        self._title = str(title or "")
        self._meta  = str(meta  or "")
        self._body  = str(body  or "")
        self._cat   = category or "default"
        self._mtext = str(media_text or "")
        self._ipath = str(img_path   or "")
        self._cw    = card_w or CW
        self._img   = None
        self._paras = []
        self._h     = 0

    def _build_paras(self):
        st   = self._st
        # content starts after the left stripe gap
        iw   = self._cw - _CARD_PAD - _STRIPE_W - 8
        out  = []
        if self._title:
            out.append(Paragraph(_e(self._title), st["ctl"]))
        if self._meta:
            out.append(Paragraph(_e(self._meta), st["cme"]))
        if self._body:
            out.append(Paragraph(_e(self._body), st["cbd"]))
        if self._mtext:
            out.append(Paragraph(_e(self._mtext), st["cmd"]))
        return out, iw

    def wrap(self, aw, ah):
        self._paras, iw = self._build_paras()
        h = _CARD_PAD_V + _PILL_H + 5
        for p in self._paras:
            _, ph = p.wrap(iw, ah)
            h += ph
        self._img = _embed_image(self._ipath, max_w=iw, max_h=140)
        if self._img:
            h += self._img.drawHeight + 8
        h += _CARD_PAD_V
        self._h      = max(h, 48)
        self.width   = self._cw
        self.height  = self._h
        return self._cw, self._h

    def draw(self):
        c    = self.canv
        w, h = self._cw, self._h
        r    = 14   # border-radius: 24px scaled for A5 context

        # Drop shadow (very subtle)
        c.saveState()
        c.setFillColor(Color(0, 0, 0, alpha=0.022))
        c.roundRect(1, -2, w, h, r, stroke=0, fill=1)
        c.restoreState()

        # Card background + --pink-border stroke
        c.saveState()
        c.setFillColor(C_SURFACE)
        c.setStrokeColor(C_PINK_BRD)
        c.setLineWidth(0.8)
        c.roundRect(0, 0, w, h, r, stroke=1, fill=1)
        c.restoreState()

        # Left colour stripe — .stat-card-stripe (4px wide, full height, rounded)
        stripe = CAT_STRIPE.get(self._cat, STRIPE_PINK)
        c.saveState()
        c.setFillColor(stripe[0])
        c.roundRect(0, h / 2, _STRIPE_W, h / 2, r, stroke=0, fill=1)
        c.setFillColor(stripe[1])
        c.roundRect(0, 0,     _STRIPE_W, h / 2, r, stroke=0, fill=1)
        # Overwrite overlap band in the centre so colours blend without gap
        mid_col = Color(
            (stripe[0].red   + stripe[1].red)   / 2,
            (stripe[0].green + stripe[1].green) / 2,
            (stripe[0].blue  + stripe[1].blue)  / 2,
        )
        c.setFillColor(mid_col)
        c.rect(0, h / 2 - 2, _STRIPE_W, 4, stroke=0, fill=1)
        c.restoreState()

        # Category pill  (mirrors .stat-card-category from stats.html)
        cat_lbl = CAT_LABEL.get(self._cat, CAT_LABEL["default"])
        bg_col  = CAT_BG.get(self._cat, CAT_BG["default"])
        fg_col  = CAT_FG.get(self._cat, CAT_FG["default"])
        x0      = _STRIPE_W + _CARD_PAD
        pill_w  = min(len(cat_lbl) * 5.0 + 16, self._cw - x0 - 8)
        py      = h - _CARD_PAD_V - _PILL_H
        c.saveState()
        c.setFillColor(bg_col)
        c.roundRect(x0, py, pill_w, _PILL_H, 6, stroke=0, fill=1)
        c.setFillColor(fg_col)
        c.setFont(FB, 6)
        c.drawString(x0 + 6, py + 4, cat_lbl)
        c.restoreState()

        y = py - 5

        # Text paragraphs
        iw = self._cw - _CARD_PAD - _STRIPE_W - 8
        for p in self._paras:
            _, ph = p.wrap(iw, y)
            y -= ph
            p.drawOn(c, x0, y)

        # Embedded image
        if self._img:
            y -= 8
            y -= self._img.drawHeight
            self._img.drawOn(c, x0, y)


# ══════════════════════════════════════════════════════════════════
# COVER PAGE  (mirrors .hero + .counter-wrap + stats row)
# ══════════════════════════════════════════════════════════════════

def _build_cover(data: dict, st: dict) -> list:
    out   = []
    names = data.get("couple_names") or ["—", "—"]
    n1    = str(names[0]) if names else "—"
    n2    = str(names[1]) if len(names) > 1 else "—"

    out.append(Spacer(1, 38))

    # hero-badge  (mirrors .hero-badge: uppercase accent pill)
    badge_rows = [Paragraph("SureMemory · История пары", st["cbg"])]
    badge_box  = GradientBox(badge_rows, box_w=CW * 0.52, pad_h=6, radius=999)
    badge_tbl  = Table([[badge_box]], colWidths=[CW])
    badge_tbl.setStyle(TableStyle([
        ("ALIGN",  (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    out.append(badge_tbl)
    out.append(Spacer(1, 18))

    # hero-title: Names (mirrors .hero-title Playfair Display 900)
    out.append(Paragraph(_e(n1), st["cn"]))
    out.append(Spacer(1, 3))
    out.append(Paragraph("&amp;", st["ca"]))
    out.append(Spacer(1, 3))
    out.append(Paragraph(_e(n2), st["cn"]))

    out.append(Spacer(1, 16))

    # Thin divider  (mirrors the hr in stats between sections)
    out.append(HRFlowable(
        width="50%", thickness=0.5,
        color=C_PINK_BRD, hAlign="CENTER", spaceAfter=14,
    ))

    # hero-sub lines
    met_disp  = data.get("met_date_display", "")
    days_tog  = data.get("days_together")
    export_dt = data.get("export_date", "")

    if met_disp:
        out.append(Paragraph(f"познакомились {_e(met_disp)}", st["cm"]))
    if days_tog is not None:
        out.append(Paragraph(
            f"вместе {days_tog} {_days_word(days_tog)}",
            st["cm"],
        ))
    out.append(Spacer(1, 22))

    # ── counter-wrap block (pink→violet gradient banner, 3 numbers) ──
    stats  = data.get("stats", {})
    s_mem  = str(stats.get("memories", 0))
    s_ev   = str(stats.get("events",   0))
    s_wish = str(stats.get("wishes",   0))
    cw3    = CW / 3

    val_row = [
        Paragraph(s_mem,  st["sv"]),
        Paragraph(s_ev,   st["sv"]),
        Paragraph(s_wish, st["sv"]),
    ]
    lbl_row = [
        Paragraph("воспоминаний", st["sl"]),
        Paragraph("событий",      st["sl"]),
        Paragraph("желаний",      st["sl"]),
    ]

    inner_tbl = Table([val_row, lbl_row], colWidths=[cw3, cw3, cw3])
    inner_tbl.setStyle(TableStyle([
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 2),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 2),
    ]))

    counter_box = GradientBox([inner_tbl], box_w=CW, pad_h=18, radius=20)
    out.append(counter_box)
    out.append(Spacer(1, 22))

    if export_dt:
        out.append(Paragraph(f"экспортировано {_e(export_dt)}", st["ct"]))

    out.append(PageBreak())
    return out


# ══════════════════════════════════════════════════════════════════
# TABLE OF CONTENTS
# ══════════════════════════════════════════════════════════════════

def _build_toc(data: dict, st: dict) -> list:
    stats = data.get("stats", {})
    total = stats.get("memories", 0) + stats.get("events", 0) + stats.get("wishes", 0)
    if total < 13:
        return []

    out = [
        Paragraph("СОДЕРЖАНИЕ", st["sth"]),
        HRFlowable(width="100%", thickness=0.4, color=C_PINK_BRD, spaceAfter=10),
    ]

    sections_data = data.get("sections", {})
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
            out.append(Paragraph(
                f"    {yr} — {by_year[yr]} {_items_word(by_year[yr])}",
                st["tocs"],
            ))

    if sections_data.get("events"):
        cnt = len(sections_data["events"])
        out.append(Paragraph(f"События — {cnt}", st["toci"]))

    if sections_data.get("wishes"):
        cnt = len(sections_data["wishes"])
        out.append(Paragraph(f"Желания — {cnt}", st["toci"]))

    out.append(Spacer(1, 8))
    photos = stats.get("photos", 0)
    if photos:
        out.append(Paragraph(f"Фотографий: {photos}", st["tocs"]))

    out.append(PageBreak())
    return out


# ══════════════════════════════════════════════════════════════════
# SECTION HEADER  (mirrors .section-title: tiny uppercase muted text)
# ══════════════════════════════════════════════════════════════════

def _section_header(title: str, st: dict) -> list:
    return [
        PageBreak(),
        Spacer(1, 6),
        Paragraph(title.upper(), st["sh"]),
        HRFlowable(
            width="100%", thickness=0.4,
            color=C_PINK_BRD, spaceAfter=8,
        ),
    ]


# ══════════════════════════════════════════════════════════════════
# MEMORIES  (grouped year → month)
# ══════════════════════════════════════════════════════════════════

def _build_memories(data: dict, st: dict) -> list:
    items = (data.get("sections") or {}).get("memories") or []
    if not items:
        return _section_header("Воспоминания", st) + [
            Spacer(1, 10),
            Paragraph("Воспоминаний пока нет.", st["emp"]),
        ]

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
                out.append(KeepTogether([Spacer(1, 5), card]))
                out.append(Spacer(1, 9))

    if undated:
        out.append(Paragraph("Разное", st["mh"]))
        for item in undated:
            out.append(KeepTogether([Spacer(1, 5), _make_card(item, st)]))
            out.append(Spacer(1, 9))

    return out


def _make_card(item: dict, st: dict) -> MemoryCard:
    author = item.get("author", "")
    date   = item.get("date", "")
    meta   = (
        f"{_e(date)}  ·  {_e(author)}" if author and date
        else _e(date or author)
    )
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
        out.append(KeepTogether([Spacer(1, 5), _make_card(item, st)]))
        out.append(Spacer(1, 9))
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
        out.append(KeepTogether([Spacer(1, 5), _make_card(item, st)]))
        out.append(Spacer(1, 9))
    return out


# ══════════════════════════════════════════════════════════════════
# STATS PAGE  (mirrors .counter-wrap + 2×3 .mini-card grid)
# ══════════════════════════════════════════════════════════════════

def _build_stats(data: dict, st: dict) -> list:
    s    = data.get("stats", {})
    days = data.get("days_together")

    out = [
        PageBreak(),
        Spacer(1, 8),
        Paragraph("СТАТИСТИКА", st["sth"]),
        HRFlowable(width="100%", thickness=0.4, color=C_PINK_BRD, spaceAfter=10),
    ]

    # ── Top banner: big numbers on gradient  (.counter-wrap) ──────
    photos  = s.get("photos",  0)
    videos  = s.get("videos",  0) + s.get("voices", 0)
    files   = s.get("files",   0)
    days_v  = str(days) if days is not None else "—"
    days_l  = _days_word(days) if days else "дней вместе"

    val_row  = [
        Paragraph(str(s.get("memories", 0)), st["sv"]),
        Paragraph(days_v,                    st["sv"]),
        Paragraph(str(photos),               st["sv"]),
    ]
    lbl_row  = [
        Paragraph("воспоминаний", st["sl"]),
        Paragraph(days_l,         st["sl"]),
        Paragraph("фотографий",   st["sl"]),
    ]
    cw3 = CW / 3
    inner = Table([val_row, lbl_row], colWidths=[cw3, cw3, cw3])
    inner.setStyle(TableStyle([
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING",   (0, 0), (-1, -1), 2),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 2),
    ]))
    counter = GradientBox([inner], box_w=CW, pad_h=18, radius=20)
    out.append(counter)
    out.append(Spacer(1, 12))

    # ── Mini-card grid  (.mini-grid 2-column) ─────────────────────
    total_media = videos + files
    grid_items = [
        (str(s.get("events",  0)), "событий",       STRIPE_ROSE),
        (str(s.get("wishes",  0)), "желаний",        STRIPE_GREEN),
        (str(photos),              "фото",            STRIPE_PINK),
        (str(videos),              "видео и голос",   STRIPE_ORANGE),
        (str(files),               "файлов",          STRIPE_VIOLET),
        (str(total_media),         "медиафайлов",     STRIPE_AMBER),
    ]
    card_w = (CW - 8) / 2
    pairs  = [(grid_items[i], grid_items[i+1]) for i in range(0, len(grid_items), 2)]
    for left, right in pairs:
        cl = MiniStatCard(left[0],  left[1],  left[2],  card_w=card_w, styles=st)
        cr = MiniStatCard(right[0], right[1], right[2], card_w=card_w, styles=st)
        row_tbl = Table([[cl, cr]], colWidths=[card_w, card_w])
        row_tbl.setStyle(TableStyle([
            ("ALIGN",   (0, 0), (-1, -1), "LEFT"),
            ("VALIGN",  (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING",   (0, 0), (-1, -1), 0),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
            ("TOPPADDING",    (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("COLPADDING",    (0, 0), (0, -1), 8, 0, 0, 0),
        ]))
        out.append(row_tbl)
        out.append(Spacer(1, 8))

    # ── Popular categories (if available) ─────────────────────────
    cat_counts: dict = defaultdict(int)
    for section_key in ("memories", "events", "wishes"):
        for item in (data.get("sections") or {}).get(section_key, []):
            cat = item.get("category", "default")
            cat_counts[cat] += 1

    if cat_counts:
        out.append(Spacer(1, 4))
        out.append(Paragraph("ПОПУЛЯРНЫЕ КАТЕГОРИИ", st["sth"]))
        out.append(HRFlowable(width="100%", thickness=0.4, color=C_PINK_BRD, spaceAfter=8))
        sorted_cats = sorted(cat_counts.items(), key=lambda x: -x[1])
        total_items = sum(cat_counts.values()) or 1
        for cat, cnt in sorted_cats[:5]:
            lbl  = CAT_LABEL.get(cat, CAT_LABEL["default"])
            pct  = int(cnt / total_items * 100)
            stripe = CAT_STRIPE.get(cat, STRIPE_PINK)
            bar_card = _CategoryBar(lbl, cnt, pct, stripe, CW, st)
            out.append(bar_card)
            out.append(Spacer(1, 6))

    return out


# ══════════════════════════════════════════════════════════════════
# CUSTOM FLOWABLE: CategoryBar (mirrors .time-row / .featured-bar)
# ══════════════════════════════════════════════════════════════════

class _CategoryBar(Flowable):
    """Horizontal bar for category frequency — mirrors .time-bar in stats.html."""

    def __init__(self, label: str, count: int, pct: int,
                 stripe: tuple, bar_w: float, styles: dict):
        Flowable.__init__(self)
        self._label  = label
        self._count  = count
        self._pct    = pct
        self._stripe = stripe
        self._bw     = bar_w
        self._st     = styles
        self._h      = 24

    def wrap(self, aw, ah):
        self.width  = self._bw
        self.height = self._h
        return self._bw, self._h

    def draw(self):
        c    = self.canv
        w, h = self._bw, self._h
        pad  = 6

        # Label
        c.setFont(FR, 7.5)
        c.setFillColor(C_MUTED)
        c.drawString(pad, h / 2 - 3, self._label)

        # Percentage text
        pct_str = f"{self._pct}%"
        c.setFont(FB, 7.5)
        c.setFillColor(C_ACCENT)
        c.drawRightString(w - pad, h / 2 - 3, pct_str)

        # Bar background
        lbl_w    = 90
        pct_w    = 24
        bar_x    = lbl_w + pad
        bar_end  = w - pct_w - pad
        bar_w_px = bar_end - bar_x
        bar_h    = 5
        bar_y    = h / 2 - bar_h / 2

        c.setFillColor(C_PINK_BRD)
        c.roundRect(bar_x, bar_y, bar_w_px, bar_h, 3, stroke=0, fill=1)

        # Bar fill — two-tone gradient (left colour → right colour)
        fill_w = max(bar_w_px * self._pct / 100, 4)
        half   = fill_w / 2
        c.setFillColor(self._stripe[0])
        c.roundRect(bar_x, bar_y, fill_w, bar_h, 3, stroke=0, fill=1)
        c.setFillColor(self._stripe[1])
        # Right half rect (no rounding on left side — overlap with rounded left)
        c.rect(bar_x + half, bar_y, half, bar_h, stroke=0, fill=1)


# ══════════════════════════════════════════════════════════════════
# ENDING PAGE
# ══════════════════════════════════════════════════════════════════

def _build_ending(data: dict, st: dict) -> list:
    today = datetime.now().strftime("%d.%m.%Y")
    return [
        PageBreak(),
        Spacer(1, 140),
        Paragraph("История продолжается", st["eb"]),
        Spacer(1, 14),
        Paragraph("Каждый момент, записанный здесь,", st["es"]),
        Paragraph("остаётся с вами навсегда", st["es"]),
        Spacer(1, 50),
        Paragraph(_e(today), st["ct"]),
    ]


# ══════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════

def generate_couple_story_pdf(data: dict, output_path: str) -> str:
    """
    Generate a styled PDF album from couple story data.
    Returns the absolute path to the created file.
    Raises ValueError on bad arguments; all other exceptions propagate.
    """
    logger.info(f"[PDF] START: output={output_path}")

    if not isinstance(data, dict):
        raise ValueError("data must be a dict")
    if not output_path or not isinstance(output_path, str):
        raise ValueError("output_path must be a non-empty string")

    try:
        names = list(data.get("couple_names") or [])
        while len(names) < 2:
            names.append("—")
        safe_data = dict(data)
        safe_data["couple_names"] = names[:2]

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        doc = SimpleDocTemplate(
            output_path,
            pagesize    = (PAGE_W, PAGE_H),
            leftMargin  = MG,
            rightMargin = MG,
            topMargin   = MG_T,
            bottomMargin= MG_B,
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
    import tempfile

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
            "photos": 2,   "videos": 1, "voices": 0, "files": 1,
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
