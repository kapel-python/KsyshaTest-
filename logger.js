/**
 * MemoriesLogger — клиентский логгер
 * Собирает логи в буфер и каждые 5 секунд отправляет пакетом на /api/log
 * который пересылает их создателю в Telegram.
 *
 * Использование:
 *   _log('тег', 'сообщение', { любые: 'данные' })
 *   _log('WS', 'connected')
 *   _log('Stars', 'btnSend clicked', { count: 3 })
 *
 * Уровни: _log, _logWarn, _logErr
 */
(function() {
  const FLUSH_INTERVAL_MS = 5000;
  const MAX_BUFFER = 50; // максимум в одном пакете

  let _buf = [];
  let _flushTimer = null;
  let _apiKey = null;
  let _visitorId = null;
  let _enabled = true;
  let _page = location.pathname || '/';

  // Определяем API ключ — из window.API_SECRET_KEY или window.__STATS_APIKEY__
  function _getApiKey() {
    return _apiKey
      || window.API_SECRET_KEY
      || window.__STATS_APIKEY__
      || '';
  }

  // Определяем visitor_id
  function _getVisitorId() {
    if (_visitorId) return _visitorId;
    // Пробуем из cookie
    try {
      const cookies = document.cookie.split(';');
      for (const c of cookies) {
        const [k, v] = c.trim().split('=');
        if (k === 'visitor_id' && v) return decodeURIComponent(v).trim();
        if (k === 'memories_role' && v) return decodeURIComponent(v).trim();
      }
    } catch(e) {}
    return 'unknown';
  }

  // Форматирует аргументы в строку
  function _fmt(...args) {
    return args.map(a => {
      if (a === null) return 'null';
      if (a === undefined) return 'undefined';
      if (typeof a === 'object') {
        try { return JSON.stringify(a); } catch(e) { return String(a); }
      }
      return String(a);
    }).join(' ');
  }

  // Добавляет запись в буфер
  function _push(level, tag, ...args) {
    if (!_enabled) return;
    const msg = _fmt(...args);
    const now = new Date();
    const time = now.getHours().toString().padStart(2,'0') + ':' +
                 now.getMinutes().toString().padStart(2,'0') + ':' +
                 now.getSeconds().toString().padStart(2,'0');
    _buf.push({ level, tag, msg, time });
    // Тоже пишем в консоль для удобства
    const prefix = `[${tag}]`;
    if (level === 'error') console.error(prefix, ...args);
    else if (level === 'warn') console.warn(prefix, ...args);
    else console.log(prefix, ...args);
    // Если буфер переполнен — флашим сразу
    if (_buf.length >= MAX_BUFFER) _flush();
    else _scheduleFlush();
  }

  function _scheduleFlush() {
    if (_flushTimer) return;
    _flushTimer = setTimeout(_flush, FLUSH_INTERVAL_MS);
  }

  async function _flush() {
    clearTimeout(_flushTimer);
    _flushTimer = null;
    if (!_buf.length) return;
    const entries = _buf.splice(0, MAX_BUFFER);
    const apiKey = _getApiKey();
    if (!apiKey) return; // ключ ещё не загружен — теряем (редкий случай при старте)
    try {
      await fetch('/api/log', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Api-Key': apiKey },
        body: JSON.stringify({
          visitor_id: _getVisitorId(),
          page: _page,
          ua: navigator.userAgent.slice(0, 120),
          entries,
        }),
        keepalive: true,
      });
    } catch(e) {
      // тихо — не хотим рекурсивных логов об ошибке логгера
    }
  }

  // Сбрасываем при закрытии страницы
  window.addEventListener('beforeunload', _flush);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) _flush();
  });

  // Публичный API
  window._log    = (tag, ...args) => _push('info',  tag, ...args);
  window._logWarn = (tag, ...args) => _push('warn',  tag, ...args);
  window._logErr  = (tag, ...args) => _push('error', tag, ...args);

  // Настройка
  window._loggerSetVisitorId = (id) => { _visitorId = id; };
  window._loggerDisable      = ()   => { _enabled = false; };
  window._loggerFlushNow     = ()   => _flush();

  // Удобный алиас — логировать объект целиком
  window._logObj = (tag, label, obj) => _push('info', tag, label, obj);

  console.log('[Logger] MemoriesLogger initialized, flush every', FLUSH_INTERVAL_MS / 1000, 's');
})();
