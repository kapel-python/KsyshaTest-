/* Memory Card Shared JS */
window.MemoryCard = {};
      function createMemoryElement(m, categoriesMap) {

        const div = document.createElement('div');

        div.className = 'memory';

        div.dataset.memoryId = String(m.id);

        if (m.category) {

          div.dataset.category = m.category;

        }

      function _normalizeMediaItem(item) {
        if (!item || typeof item !== 'object') return null;
        const src = _mediaOriginalUrl(item) || _mediaThumbUrl(item) || _mediaPathToUrl(String(item.src || item.url || item.media_url || item.media_path || item.path || '').trim());
        if (!src) return null;
        const thumb = _mediaThumbUrl(item) || _mediaPathToUrl(String(item.thumb_path || '').trim()) || src;
        const type = String(item.type || item.media_type || '').trim().toLowerCase();
        const name = String(item.name || item.filename || item.media_name || '').trim() || _mediaFileNameFromUrl(src);
        const ext = _mediaExtFromName(name || src);
        const kind = _mediaKindFromType(type, ext, name);
        return {
          raw: item,
          src,
          thumb,
          type,
          name,
          ext,
          kind,
          language: _mediaLanguageFromFile({ name, ext }),
        };
      }

      function _createVideoPreview(media) {
        const wrap = document.createElement('div');
        wrap.className = 'memory-media-video-wrap';

        const img = document.createElement('img');
        img.className = 'media';
        img.loading = 'lazy';
        img.decoding = 'async';
        img.alt = '';
        img.onerror = function() { this.style.display = 'none'; };
        wrap.appendChild(img);

        const playOverlay = document.createElement('div');
        playOverlay.className = 'memory-media-video-play';
        wrap.appendChild(playOverlay);

        const badge = document.createElement('span');
        badge.className = 'memory-media-video-badge';
        badge.style.display = 'none';
        wrap.appendChild(badge);

        const hasServerPreview = media.thumb && media.thumb !== media.src && /\.(jpe?g|png|webp|gif)/i.test(media.thumb);

        if (hasServerPreview) {
          img.src = media.thumb;
          const observer = new IntersectionObserver((entries, obs) => {
            entries.forEach(entry => {
              if (entry.isIntersecting) {
                obs.disconnect();
                const runDurationFetch = () => {
                  _getVideoDuration(media.src, function(duration) {
                    if (duration && isFinite(duration)) {
                      console.log('[DURATION_DEBUG] Displaying badge for:', media.src, 'duration:', duration);
                      badge.textContent = _formatVideoDuration(duration);
                      badge.style.display = '';
                    } else {
                      console.warn('[DURATION_DEBUG] No badge displayed (invalid/missing duration) for:', media.src);
                    }

      function _attachMediaOpenHandler(el, item, options = {}) {
        const media = _normalizeMediaItem(item);
        if (!el || !media) return el;
        const openOnClick = options.openOnClick !== false;
        el.dataset.mediaKind = media.kind;
        el.dataset.mediaName = media.name || '';
        el.style.cursor = 'pointer';
        if (openOnClick) {
          el.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            openMediaActionModal(media);
          });
        }

      function openMediaActionModal(item) {
        const media = _normalizeMediaItem(item);
        if (!media) return;
        _showMediaActionModal(media);
      }

      function _showMediaActionModal(media) {
        const ui = _ensureMediaUi();
        _mediaViewerState.media = media;
        _mediaViewerState.actionOpen = true;
        _mediaViewerState.returnToAction = false;

        const kind = String(media && media.kind || '').trim();
        const type = String(media && media.type || '').trim().toLowerCase();
        const ext = String(media && media.ext || '').trim().toLowerCase();

        let showMeta = false;
        let isArchive = ['zip', 'rar', 'tar', 'gz', '7z', 'bz2', 'xz'].includes(ext);

        if (kind === 'photo') {
          ui.action.kind.textContent = '';
          ui.action.title.textContent = '📷 ' + _ui('mediaPhoto');
        } else if (kind === 'video') {
          ui.action.kind.textContent = '';
          ui.action.title.textContent = '🎥 ' + _ui('mediaVideo');
          showMeta = true;
        } else if (kind === 'audio') {
          ui.action.kind.textContent = '';
          ui.action.title.textContent = type === 'voice' ? '🎵 ' + _ui('mediaVoice') : '🎵 ' + _ui('mediaAudioLabel');
          showMeta = true;
        } else {
          ui.action.kind.textContent = isArchive ? '📦 ' + _ui('mediaArchive') : '📄 ' + _ui('mediaDocument');
          ui.action.title.textContent = media.name || _mediaFileNameFromUrl(media.src) || _ui('mediaDocument');
          showMeta = true;
        }

      function renderMetaElement(metaDiv) {
        const who = metaDiv.dataset.who || '';
        const createdAt = metaDiv.dataset.createdAt || '';
        const updatedAt = metaDiv.dataset.updatedAt || '';
        let metaHtml = '';
        if (who) {
          metaHtml += _i('metaAddedBy') + ' ' + escapeHtml(who);
        }

window.MemoryCard.createMemoryElement = createMemoryElement;
window.createMemoryElement = createMemoryElement;
window.openMediaActionModal = openMediaActionModal;
window.MemoryCard.openMediaActionModal = openMediaActionModal;

const _MC_I18N = {
  catImportantMoments: '💫 Важные моменты',
  catMemories:         '📖 Воспоминания',
  catDates:            '📅 Важные даты',
  memoryNoTitle:       '(без названия)',
  metaAddedBy:         '👤 Добавил(а):',
  metaAdded:           '📌 Добавлено:',
  metaEdited:          '• отредактировано:',
  mediaOpen:           'Открыть',
  mediaDownload:       'Скачать',
  mediaClose:          'Закрыть',
  mediaExit:           'Выйти',
  mediaPhoto:          'Фото',
  mediaVideo:          'Видео',
  mediaVoice:          'Голосовое сообщение',
  mediaAudioLabel:     'Аудио',
  mediaOpenFull:       'Открыть полностью',
  mediaCollapse:       'Свернуть',
  mediaOpenFile:       'Открыть файл',
  mediaOpenMedia:      'Открыть медиа'
};

if (typeof window._ui === 'undefined') {
  window._ui = function(k) { return _MC_I18N[k] || k; };
}
if (typeof window._i === 'undefined') {
  window._i = function(k) { return _MC_I18N[k] || k; };
}

