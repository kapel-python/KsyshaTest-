"""
pdf_story_template.py
Emotional Couple Story PDF
Стиль:
Apple Journal × cinematic diary × premium mobile photobook

Главная идея:
не документ,
а последовательность эмоциональных сцен.
"""

import os
import json
import random
import html
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    PageBreak,
    KeepTogether,
    Flowable,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# ════════════════════════════════════════════════════════════════════════════
# ШРИФТЫ
# ════════════════════════════════════════════════════════════════════════════

FONT_REGULAR = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
try:
    reg_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    bold_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    if os.path.exists(reg_path) and os.path.exists(bold_path):
        pdfmetrics.registerFont(TTFont("R", reg_path))
        pdfmetrics.registerFont(TTFont("B", bold_path))
        FONT_REGULAR = "R"
        FONT_BOLD = "B"
except Exception:
    pass


# ════════════════════════════════════════════════════════════════════════════
# PAGE
# ════════════════════════════════════════════════════════════════════════════

PW = 360
PH = 640
MG = 22

CW = PW - MG * 2


# ════════════════════════════════════════════════════════════════════════════
# COLORS
# ════════════════════════════════════════════════════════════════════════════

P = colors.HexColor("#6E67D8")
AC = colors.HexColor("#D98A9A")

TX = colors.HexColor("#1C1C1E")
MT = colors.HexColor("#6B7280")

BG = colors.HexColor("#FAF8FF")

BG_MEM = colors.HexColor("#FFFFFF")
BG_EVENT = colors.HexColor("#FFF7F8")
BG_WISH = colors.HexColor("#F5FBF7")

DIV = colors.HexColor("#ECECF1")

WHT = colors.white


# ════════════════════════════════════════════════════════════════════════════
# FLOWABLES
# ════════════════════════════════════════════════════════════════════════════

class MemoryCard(Flowable):

    PAD = 18

    def __init__(self, flowables, width, bg):
        Flowable.__init__(self)

        self.fls = flowables
        self.cw = width
        self.bg = bg
        self.radius = 14

        self._h = None

    def wrap(self, aw, ah):

        inner_w = self.cw - self.PAD * 2

        h = self.PAD

        for f in self.fls:
            _, fh = f.wrap(inner_w, ah)
            h += fh

        h += self.PAD

        self._h = h
        self.height = h
        self.width = self.cw

        return self.cw, h

    def draw(self):

        c = self.canv
        h = self._h

        c.saveState()

        # subtle shadow
        c.setFillColor(colors.Color(0, 0, 0, alpha=0.03))
        c.roundRect(0, -2, self.cw, h, self.radius, stroke=0, fill=1)

        # card
        c.setFillColor(self.bg)
        c.roundRect(0, 0, self.cw, h, self.radius, stroke=0, fill=1)

        c.restoreState()

        ix = self.PAD
        iw = self.cw - self.PAD * 2

        y = h - self.PAD

        for f in self.fls:
            _, fh = f.wrap(iw, y)
            y -= fh
            f.drawOn(c, ix, y)


class HeroDate(Flowable):

    def __init__(self, text):
        Flowable.__init__(self)

        self.text = text
        self.width = CW
        self.height = 120

    def draw(self):

        c = self.canv

        c.saveState()

        c.setFont("B", 34)
        c.setFillColor(P)

        c.drawString(0, 52, self.text)

        c.restoreState()


# ════════════════════════════════════════════════════════════════════════════
# STYLES
# ════════════════════════════════════════════════════════════════════════════

def S():

    return {

            "title": ParagraphStyle(
                "title",
                fontName=FONT_BOLD,
            fontSize=28,
            leading=38,
            textColor=P,
            alignment=TA_CENTER,
        ),

        "names": ParagraphStyle(
            "names",
                fontName=FONT_BOLD,
            fontSize=30,
            leading=38,
            textColor=TX,
            alignment=TA_CENTER,
        ),

        "period": ParagraphStyle(
            "period",
                fontName=FONT_REGULAR,
            fontSize=12,
            leading=18,
            textColor=MT,
            alignment=TA_CENTER,
        ),

        "quote": ParagraphStyle(
            "quote",
                fontName=FONT_BOLD,
            fontSize=24,
            leading=36,
            textColor=TX,
            alignment=TA_CENTER,
        ),

        "scene": ParagraphStyle(
            "scene",
                fontName=FONT_REGULAR,
            fontSize=14,
            leading=24,
            textColor=MT,
            alignment=TA_CENTER,
        ),

        "h2": ParagraphStyle(
            "h2",
                fontName=FONT_BOLD,
            fontSize=18,
            leading=28,
            textColor=TX,
            spaceAfter=14,
        ),

        "card_title": ParagraphStyle(
            "card_title",
                fontName=FONT_BOLD,
            fontSize=15,
            leading=22,
            textColor=TX,
            spaceAfter=6,
        ),

        "meta": ParagraphStyle(
            "meta",
                fontName=FONT_REGULAR,
            fontSize=9,
            leading=13,
            textColor=MT,
            spaceAfter=8,
        ),

        "body": ParagraphStyle(
            "body",
                fontName=FONT_REGULAR,
            fontSize=13,
            leading=23,
            textColor=TX,
        ),

        "tiny": ParagraphStyle(
            "tiny",
                fontName=FONT_REGULAR,
            fontSize=8,
            leading=12,
            textColor=MT,
            alignment=TA_CENTER,
        ),

        "ending": ParagraphStyle(
            "ending",
                fontName=FONT_BOLD,
            fontSize=26,
            leading=38,
            textColor=P,
            alignment=TA_CENTER,
        ),

    }


# ════════════════════════════════════════════════════════════════════════════
# BACKGROUNDS
# ════════════════════════════════════════════════════════════════════════════

def bg(canvas, doc):

    canvas.saveState()

    canvas.setFillColor(BG)
    canvas.rect(0, 0, PW, PH, stroke=0, fill=1)

    # atmosphere circles

    r, g, b = P.red, P.green, P.blue

    canvas.setFillColor(colors.Color(r, g, b, alpha=0.04))
    canvas.circle(PW - 30, PH - 50, 120, stroke=0, fill=1)

    canvas.setFillColor(colors.Color(r, g, b, alpha=0.02))
    canvas.circle(40, 80, 80, stroke=0, fill=1)

    canvas.restoreState()


# ════════════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════════════

def random_space():
    return random.choice([10, 14, 18, 24, 32])


def emotional_page(text, st):

    return [

        PageBreak(),

        Spacer(1, random.choice([160, 190, 220])),

        Paragraph(text, st["quote"]),

        Spacer(1, 24),

    ]


def section_card(item, st, bg_color, offset=0):

    inner = [

        Paragraph(item.get("title", "Без названия"), st["card_title"]),

        Paragraph(
            item.get("date", "—")
            + (
                f"  ·  {item['author']}"
                if item.get("author")
                else ""
            ),
            st["meta"],
        ),

    ]

    if item.get("text"):
        inner.append(
            Paragraph(item["text"], st["body"])
        )

    card = MemoryCard(inner, CW - offset, bg_color)

    return KeepTogether([
        Spacer(1, 0),
        card,
    ])


# ════════════════════════════════════════════════════════════════════════════
# COVER
# ════════════════════════════════════════════════════════════════════════════

def cover(data, st):

    names = data.get("couple_names", ["Имя1", "Имя2"])

    ps = data.get("period_start", "—")
    pe = data.get("period_end", "сейчас")

    return [

        Spacer(1, 90),

        Paragraph("История пары", st["title"]),

        Spacer(1, 30),

        Paragraph(html.escape(str(names[0])), st["names"]),
        Paragraph(html.escape(str(names[1])), st["names"]),

        Spacer(1, 18),

        Paragraph(f"{ps} — {pe}", st["period"]),

        Spacer(1, 120),

        Paragraph(
            "Каждый момент — маленькая вселенная",
            st["scene"],
        ),

        PageBreak(),
    ]


# ════════════════════════════════════════════════════════════════════════════
# TIMELINE
# ════════════════════════════════════════════════════════════════════════════

def timeline(data, st):

    out = []

    tl = data.get("timeline", [])

    if not tl:
        return out

    out.append(
        Paragraph("Таймлайн", st["h2"])
    )

    out.append(
        Spacer(1, 20)
    )

    for i, day in enumerate(tl):

        out.append(
            HeroDate(day.get("date", "—"))
        )

        out.append(
            Spacer(1, 12)
        )

        items = day.get("items", [])

        for idx, item in enumerate(items):

            t = item.get("type", "moment")

            bg_color = {
                "moment": BG_MEM,
                "event": BG_EVENT,
                "wish": BG_WISH,
            }.get(t, BG_MEM)

            offset = random.choice([0, 10, 18, 24])

            out.append(
                section_card(
                    {
                        "title": item.get("title"),
                        "date": t,
                        "text": item.get("text"),
                    },
                    st,
                    bg_color,
                    offset,
                )
            )

            out.append(
                Spacer(1, random_space())
            )

        # emotional pause
        if i < len(tl) - 1:

            out += emotional_page(
                random.choice([
                    "Иногда всё начинается очень тихо.",
                    "Некоторые вечера остаются навсегда.",
                    "Самые важные моменты обычно выглядят обычными.",
                ]),
                st,
            )

    out.append(PageBreak())

    return out


# ════════════════════════════════════════════════════════════════════════════
# SECTIONS
# ════════════════════════════════════════════════════════════════════════════

def section(title, items, st, bg_color):

    out = [

        Paragraph(title, st["h2"]),

        Spacer(1, 18),

    ]

    if not items:

        out.append(
            Paragraph(
                "Пока здесь пусто.",
                st["scene"]
            )
        )

        return out

    for idx, item in enumerate(items):

        # occasional free text block
        if idx % 3 == 1:

            out.append(
                Spacer(1, 30)
            )

            out.append(
                Paragraph(
                    item.get("text", ""),
                    st["scene"]
                )
            )

            out.append(
                Spacer(1, 50)
            )

            continue

        offset = random.choice([0, 12, 24])

        out.append(
            section_card(
                item,
                st,
                bg_color,
                offset,
            )
        )

        out.append(
            Spacer(1, random_space())
        )

    return out


# ════════════════════════════════════════════════════════════════════════════
# FINAL
# ════════════════════════════════════════════════════════════════════════════

def final_page(data, st):

    gd = datetime.now().strftime("%d.%m.%Y")

    return [

        PageBreak(),

        Spacer(1, 200),

        Paragraph(
            "Эта история продолжается",
            st["ending"]
        ),

        Spacer(1, 36),

        Paragraph(
            "Самые важные моменты остаются",
            st["scene"]
        ),

        Spacer(1, 120),

        Paragraph(
            gd,
            st["tiny"]
        )

    ]


# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════

def generate_couple_story_pdf(data, output_path):
    if not isinstance(data, dict):
        raise ValueError("data must be a dict")
    if not output_path or not isinstance(output_path, str):
        raise ValueError("output_path must be a non-empty string")

    safe_data = {
        "couple_names": list((data.get("couple_names") or ["Имя1", "Имя2"]))[:2],
        "period_start": str(data.get("period_start") or "—"),
        "period_end": str(data.get("period_end") or "настоящее время"),
        "timeline": data.get("timeline") if isinstance(data.get("timeline"), list) else [],
        "sections": data.get("sections") if isinstance(data.get("sections"), dict) else {},
    }
    if len(safe_data["couple_names"]) < 2:
        safe_data["couple_names"] = (safe_data["couple_names"] + ["Имя2"])[:2]

    os.makedirs(
        os.path.dirname(os.path.abspath(output_path)),
        exist_ok=True,
    )

    doc = SimpleDocTemplate(
        output_path,
        pagesize=(PW, PH),
        leftMargin=MG,
        rightMargin=MG,
        topMargin=MG,
        bottomMargin=MG,
    )

    st = S()

    story = []

    # cover
    story += cover(safe_data, st)

    # timeline
    story += timeline(safe_data, st)

    # memories
    story += section(
        "Воспоминания",
        safe_data.get("sections", {}).get("memories", []),
        st,
        BG_MEM,
    )

    story.append(PageBreak())

    # events
    story += section(
        "События",
        safe_data.get("sections", {}).get("events", []),
        st,
        BG_EVENT,
    )

    story.append(PageBreak())

    # wishes
    story += section(
        "Желания",
        safe_data.get("sections", {}).get("wishes", []),
        st,
        BG_WISH,
    )

    # ending
    story += final_page(safe_data, st)

    doc.build(
        story,
        onFirstPage=bg,
        onLaterPages=bg,
    )

    return os.path.abspath(output_path)


# ════════════════════════════════════════════════════════════════════════════
# RUN
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":

    sp = os.path.join(
        os.path.dirname(__file__),
        "sample_data.json"
    )

    with open(sp, encoding="utf-8") as f:
        data = json.load(f)

    out = generate_couple_story_pdf(
        data,
        "exports/couple_story_cinematic.pdf"
    )

    print(f"PDF создан: {out}")
