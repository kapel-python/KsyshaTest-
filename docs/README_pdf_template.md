# README — PDF «История пары»

Генератор красивого PDF-отчёта для бота «История пары».  
Стек: Python 3.11+, ReportLab, Pillow (опционально).

---

## Структура проекта

```
pdf_story_template.py   ← главный файл генерации
sample_data.json        ← мок-данные для тестового запуска
exports/                ← папка для готовых PDF (создаётся автоматически)
  couple_story_template.pdf
```

---

## Быстрый старт

### 1. Установить зависимости

```bash
pip install reportlab pillow
```

> `pillow` пока не используется напрямую, но зарезервирован для будущих
> градиентных фонов через Pillow ImageDraw.

### 2. Запустить генерацию

```bash
python pdf_story_template.py
```

PDF появится в `exports/couple_story_template.pdf`.

---

## Точка входа функции (для бота)

```python
from pdf_story_template import generate_couple_story_pdf

path = generate_couple_story_pdf(data, "exports/report.pdf")
# path → абсолютный путь к созданному файлу
```

### Структура `data`

```python
data = {
    "couple_names": ["Алина", "Максим"],          # [str, str]
    "period_start": "14 февраля 2023",             # str
    "period_end":   "настоящее время",             # str
    "stats": {
        "memories": 12,                            # int
        "events":   5,
        "wishes":   8,
    },
    "timeline": [                                  # список дней
        {
            "date": "14 февраля 2023",
            "items": [
                {
                    "type":  "moment",             # "moment" | "event" | "wish"
                    "title": "Первая встреча",
                    "text":  "Описание...",
                }
            ]
        }
    ],
    "sections": {
        "memories": [                              # воспоминания
            {
                "title":  "Название",
                "date":   "14.02.2023",
                "text":   "Текст...",
                "author": "Максим",               # опционально
            }
        ],
        "events":  [...],                          # события (та же структура)
        "wishes":  [...],                          # желания (та же структура)
    }
}
```

Если секции или таймлайн пустые (`[]`) — отображаются аккуратные
empty-state блоки, PDF не ломается.

---

## Как менять цвета

Все цвета объявлены в начале `pdf_story_template.py` в блоке **«Палитра»**:

```python
C_PRIMARY   = colors.HexColor("#4C6FFF")   # основной синий (заголовки, бейджи)
C_ACCENT    = colors.HexColor("#FF6B8A")   # акцент (имена, разделители)
C_TEXT      = colors.HexColor("#1F2937")   # основной текст
C_MUTED     = colors.HexColor("#6B7280")   # подписи, мета
C_BG_PAGE   = colors.HexColor("#FDF8F6")   # фон обычных страниц
C_BG_COVER  = colors.HexColor("#F0F4FF")   # фон обложки
C_CARD_MEM  = colors.HexColor("#EEF2FF")   # фон карточки «воспоминание»
C_CARD_EVT  = colors.HexColor("#FFF0F3")   # фон карточки «событие»
C_CARD_WSH  = colors.HexColor("#F0FDF4")   # фон карточки «желание»
```

Просто меняйте hex-значения — остальное пересчитается автоматически.

---

## Как менять шрифты

В начале файла:

```python
FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD    = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

pdfmetrics.registerFont(TTFont("DejaVu",     FONT_REGULAR))
pdfmetrics.registerFont(TTFont("DejaVuBold", FONT_BOLD))
```

Замените пути на свои TTF-файлы (например, Inter или Nunito),
после чего псевдонимы `"DejaVu"` / `"DejaVuBold"` будут указывать на них.  
Кириллица работает корректно в любом шрифте с поддержкой Unicode.

---

## Типы записей таймлайна

| Тип | Цвет маркера | Цвет карточки |
|-----|-------------|---------------|
| `moment` | синий `#4C6FFF` | нежно-голубой |
| `event`  | розовый `#FF6B8A` | нежно-розовый |
| `wish`   | зелёный `#22C55E` | нежно-зелёный |

---

## Технические особенности

- **Перенос текста** — обрабатывается автоматически через `Paragraph`
  с шириной контента `PAGE_W − 2 × MARGIN`.
- **Автоперенос страниц** — `SimpleDocTemplate` переносит блоки;
  `KeepTogether` не разрывает карточки напополам.
- **UTF-8 / кириллица** — TTF DejaVu Sans зарегистрирован через
  `pdfmetrics.registerFont`, квадратиков нет.
- **Empty-state** — при пустых массивах выводится мягкий текст-подсказка.
- **Фон страниц** — рисуется через `handle_pageBegin` в кастомном
  `StoryDocTemplate` ещё до отрисовки контента.

---

## Будущие расширения

- Добавление реальных фото из записей (через `Image` flowable ReportLab).
- Градиентный фон обложки через Pillow + вставка как background image.
- Экспорт нескольких пар в разные файлы параллельно.
