/* Memory Card Shared JS */
window.MemoryCard = {};

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
  mediaOpenMedia:      'Открыть медиа',
  mediaArchive:        'Архив',
  mediaDocument:       'Документ'
};

if (typeof window._ui === 'undefined') {
  window._ui = function(k) { return _MC_I18N[k] || k; };
}
if (typeof window._i === 'undefined') {
  window._i = function(k) { return _MC_I18N[k] || k; };
}
if (typeof window.escapeHtml === 'undefined') {
  window.escapeHtml = function(unsafe) {
    if (!unsafe) return '';
    return unsafe
         .replace(/&/g, "&amp;")
         .replace(/</g, "&lt;")
         .replace(/>/g, "&gt;")
         .replace(/"/g, "&quot;")
         .replace(/'/g, "&#039;");
  };
}
if (typeof window._getVideoDuration === 'undefined') {
  window._getVideoDuration = function(url, cb) { cb(0); };
}
if (typeof window._formatVideoDuration === 'undefined') {
  window._formatVideoDuration = function(d) { return d + 's'; };
}
if (typeof window._ensureMediaUi === 'undefined') {
  window._ensureMediaUi = function() {
    return {
      action: {
        kind: document.createElement('div'),
        title: document.createElement('div'),
        preview: document.createElement('div'),
        overlay: document.createElement('div')
      }
    };
  };
}
window._mediaViewerState = {};
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
                  });
                };
                if (window.requestIdleCallback) {
                  window.requestIdleCallback(runDurationFetch, { timeout: 2000 });
                } else {
                  setTimeout(runDurationFetch, 200);
                }
              }
            });
          }, { rootMargin: '150px' });
          observer.observe(wrap);
        } else {
          const observer = new IntersectionObserver((entries, obs) => {
            entries.forEach(entry => {
              if (entry.isIntersecting) {
                obs.disconnect();
                const runExtraction = () => {
                  _extractVideoThumb(media.src, function(dataUrl, duration) {
                    if (dataUrl) img.src = dataUrl;
                    else img.style.display = 'none';
                    if (duration && isFinite(duration)) {
                      console.log('[DURATION_DEBUG] Displaying badge (extracted) for:', media.src, 'duration:', duration);
                      badge.textContent = _formatVideoDuration(duration);
                      badge.style.display = '';
                    } else {
                      console.warn('[DURATION_DEBUG] No badge displayed (extracted, invalid/missing duration) for:', media.src);
                    }
                  });
                };
                if (window.requestIdleCallback) {
                  window.requestIdleCallback(runExtraction, { timeout: 2000 });
                } else {
                  setTimeout(runExtraction, 200);
                }
              }
            });
          }, { rootMargin: '150px' });
          observer.observe(wrap);
        }

        _attachMediaOpenHandler(wrap, media);
        return wrap;
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
        return el;
      }

      function renderMetaElement(metaDiv) {
        const who = metaDiv.dataset.who || '';
        const createdAt = metaDiv.dataset.createdAt || '';
        const updatedAt = metaDiv.dataset.updatedAt || '';
        let metaHtml = '';
        if (who) {
          metaHtml += _i('metaAddedBy') + ' ' + escapeHtml(who);
        }
        if (createdAt) {
          if (metaHtml) metaHtml += '<br>';
          metaHtml += _i('metaAdded') + ' ' + escapeHtml(formatDateTimeLocal(createdAt));
          if (updatedAt && updatedAt !== createdAt) {
            const tCreated = parseUtcDate(createdAt);
            const tUpdated = parseUtcDate(updatedAt);
            const diffSeconds = tCreated && tUpdated ? Math.round((tUpdated - tCreated) / 1000) : 0;
            if (diffSeconds >= 60) {
              metaHtml += ' ' + _i('metaEdited') + ' ' + escapeHtml(formatDateTimeLocal(updatedAt));
            }
          }
        }
        metaDiv.innerHTML = metaHtml;
      }

      function createMemoryElement(m, categoriesMap) {

        const div = document.createElement('div');

        div.className = 'memory';

        div.dataset.memoryId = String(m.id);

        if (m.category) {

          div.dataset.category = m.category;

        }

        if (m.privacy_type) {

          div.dataset.privacyType = m.privacy_type;

        }

        if (m.privacy_views_limit != null) {

          div.dataset.privacyViewsLimit = String(m.privacy_views_limit);

        }

        if (m.privacy_question) {

          div.dataset.privacyQuestion = m.privacy_question;

        }

        const cat = categoriesMap[m.category] || {};

        // Use translated category name if available
        const _catKeys = { important_moments:'catImportantMoments', memories:'catMemories', important_dates:'catDates' };
        const _catI18nKey = _catKeys[m.category];
        const catTitle = _catI18nKey ? _i(_catI18nKey) : ((cat.emoji || '') + ' ' + (cat.title || m.category));

        const categorySpan = document.createElement('span');

        categorySpan.className = 'category';
        categorySpan.dataset.catKey = m.category || '';

        categorySpan.textContent = catTitle.trim();

        div.appendChild(categorySpan);

        const titleDiv = document.createElement('div');

        titleDiv.className = 'title';

        titleDiv.textContent = m.title || _ui('memoryNoTitle');

        div.appendChild(titleDiv);

        const dateDiv = document.createElement('div');

        dateDiv.className = 'date';

        const humanDate = m.date_human || m.date_resolved || m.date;

        dateDiv.textContent = humanDate ? ('📅 ' + humanDate) : '';

        div.appendChild(dateDiv);

        const contentDiv = document.createElement('div');

        contentDiv.className = 'content';

        contentDiv.innerHTML = m.content_html || '';

        div.appendChild(contentDiv);

        // Если текст слишком длинный — показываем только часть и кнопку "Открыть полностью"

        const fullTextLength = (contentDiv.textContent || '').trim().length;

        const MAX_CHARS_COLLAPSED = 500;

        if (fullTextLength > MAX_CHARS_COLLAPSED) {

          contentDiv.classList.add('content--collapsed');

          const toggleBtn = document.createElement('button');

          toggleBtn.type = 'button';

          toggleBtn.className = 'content-toggle';

          toggleBtn.textContent = _ui('mediaOpenFull');

          toggleBtn.addEventListener('click', () => {

            const isCollapsed = contentDiv.classList.toggle('content--collapsed');

            toggleBtn.textContent = isCollapsed ? _ui('mediaOpenFull') : _ui('mediaCollapse');

          });

          div.appendChild(toggleBtn);

        }

        const mediaItems = Array.isArray(m.media_items) ? m.media_items.slice(0, 6) : [];
        if (mediaItems.length > 0) {
          const grid = document.createElement('div');
          grid.className = 'memory-media-grid';
          mediaItems.forEach((mi) => {
            const media = _normalizeMediaItem(mi);
            if (!media) return;
            const item = document.createElement('div');
            item.className = 'memory-media-item';
            if (media.kind === 'photo') {
              const img = document.createElement('img');
              img.className = 'media';
              img.loading = 'lazy';
              img.decoding = 'async';
              img.src = media.src || media.thumb;
              img.alt = '';
              _attachMediaOpenHandler(img, media);
              item.appendChild(img);
            } else if (media.kind === 'video') {
              item.appendChild(_createVideoPreview(media));
            } else if (media.kind === 'audio') {
              item.classList.add('memory-media-item--full');
              const audio = document.createElement('audio');
              audio.controls = true;
              audio.preload = 'none';
              audio.src = media.src;
              _attachMediaOpenHandler(audio, media);
              item.appendChild(audio);
            } else {
              item.classList.add('memory-media-item--full');
              const link = document.createElement('a');
              link.textContent = media.kind === 'file' ? _ui('mediaOpenFile') : _ui('mediaOpenMedia');
              link.className = 'memory-media-file-link';
              _attachMediaOpenHandler(link, media);
              item.appendChild(link);
            }
            grid.appendChild(item);
          });
          if (grid.children.length > 0) {
            if (grid.children.length === 1) grid.classList.add('memory-media-grid--single');
            div.appendChild(grid);
          }
        } else if (m.media_url) {
          const media = _normalizeMediaItem({
            url: m.media_url,
            original_url: m.original_url || m.media_url,
            thumb_url: m.thumb_url || m.media_url,
            type: m.media_type,
            name: m.name || m.filename || m.media_name || '',
          });
          if (media) {
            const singleGrid = document.createElement('div');
            singleGrid.className = 'memory-media-grid memory-media-grid--single';
            const item = document.createElement('div');
            item.className = 'memory-media-item memory-media-item--single-viewport';
            if (media.kind === 'photo') {
              const img = document.createElement('img');
              img.className = 'media';
              img.loading = 'lazy';
              img.decoding = 'async';
              img.src = media.src || media.thumb;
              img.alt = '';
              _attachMediaOpenHandler(img, media);
              item.appendChild(img);
            } else if (media.kind === 'video') {
              item.appendChild(_createVideoPreview(media));
            } else if (media.kind === 'audio') {
              item.classList.add('memory-media-item--full');
              const audio = document.createElement('audio');
              audio.controls = true;
              audio.preload = 'none';
              audio.src = media.src;
              _attachMediaOpenHandler(audio, media);
              item.appendChild(audio);
            } else {
              item.classList.add('memory-media-item--full');
              const link = document.createElement('a');
              link.textContent = _ui('mediaOpenFile');
              link.className = 'memory-media-file-link';
              _attachMediaOpenHandler(link, media);
              item.appendChild(link);
            }
            singleGrid.appendChild(item);
            div.appendChild(singleGrid);
          }

        }

        const metaDiv = document.createElement('div');
        metaDiv.className = 'meta';
        metaDiv.dataset.who = m.display_name || (m.first_name ? (m.last_name ? m.first_name + ' ' + m.last_name : m.first_name) : null) || (m.username ? '@' + m.username : null) || '';
        metaDiv.dataset.createdAt = m.created_at || '';
        metaDiv.dataset.updatedAt = m.updated_at || '';
        renderMetaElement(metaDiv);
        div.appendChild(metaDiv);

        return div;

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

        ui.action.meta.textContent = '';
        if (showMeta) {
          const parts = [];
          if (kind === 'video' || kind === 'audio') {
            _getVideoDuration(media.src, function(dur) {
              if (_mediaViewerState.media === media && dur && isFinite(dur)) {
                ui.action.meta.textContent = _formatVideoDuration(dur);
              }
            });
          } else {
            if (media.ext) {
              parts.push('.' + media.ext.toUpperCase());
            }
            ui.action.meta.textContent = parts.join(' • ');
          }
        }

        ui.action.preview.innerHTML = '';
        if (media.kind === 'photo') {
          const img = document.createElement('img');
          img.src = media.thumb || media.src;
          img.alt = media.name || 'media';
          ui.action.preview.appendChild(img);
          ui.action.preview.style.display = 'block';
        } else {
          ui.action.preview.style.display = 'none';
        }
        ui.action.overlay.classList.remove('hidden');
        document.body.style.overflow = 'hidden';
      }

window.createMemoryElement = createMemoryElement;
window.MemoryCard.createMemoryElement = createMemoryElement;
window.openMediaActionModal = openMediaActionModal;
window.MemoryCard.openMediaActionModal = openMediaActionModal;
window._normalizeMediaItem = _normalizeMediaItem;
window._createVideoPreview = _createVideoPreview;
window._attachMediaOpenHandler = _attachMediaOpenHandler;
window._showMediaActionModal = _showMediaActionModal;
