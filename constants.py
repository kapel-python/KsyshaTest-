
PER_PAGE_DEFAULT = 10       # Элементов на странице по умолчанию (пагинация)
PER_PAGE_FAVORITES = 10     # Элементов на странице в разделе избранного
PER_PAGE_EVENTS = 10        # Событий на странице в «События на дату»
PER_PAGE_MEMORIES = 50      # Воспоминаний на странице в категории
LIMIT_RECENT_MEMORIES = 5   # Последних воспоминаний для блока «Последнее добавлено» в статистике
LIMIT_SEARCH_MEMORIES = 20  # Макс. результатов поиска воспоминаний по тексту
LIMIT_SEARCH_FUZZY = 50     # Макс. результатов нечёткого поиска воспоминаний
TITLE_PREVIEW_LENGTH = 25   # Символов в превью заголовка (кнопки избранного, списков)
WISH_CONTENT_PREVIEW_LENGTH = 50  # Символов в превью текста желания в списке избранного
SEARCH_QUERY_MAX_LENGTH = 200     # Макс. длина поискового запроса

CREATOR_TG_LINK = "https://t.me/very_fast_earn_money"
CREATOR_BUTTON_TEXT = "💬 Написать"

MSG_ACCESS_DENIED = "Доступ запрещен"
MSG_ACCESS_DENIED_OR_FAVORITES_OFF = "Доступ запрещен или избранное выключено"
MSG_ACCESS_DENIED_CREATOR = "Только создатель может добавлять админов"
MSG_NOT_FOUND = "Не найдено"
MSG_MEMORY_NOT_FOUND = "Воспоминание не найдено"
MSG_EVENT_NOT_FOUND = "Событие не найдено"
MSG_WISH_NOT_FOUND = "Желание не найдено"
MSG_ADMIN_NOT_FOUND = "Администратор не найден"
MSG_ERROR = "Ошибка"
MSG_CANCEL = "Отмена"
MSG_ERROR_UNEXPECTED = "Произошла непредвиденная ошибка. Напиши создателю."

MSG_ERROR_TEMPLATE = "❌ Ошибка: {detail}\n\n👤 Напиши создателю о ней"

COMPANION_WINDOW_HOURS = 24
COMPANION_LIMIT_BY_TIER: dict = {
    "free":    50,
    "plus":    500,
    "premium": None,
}
