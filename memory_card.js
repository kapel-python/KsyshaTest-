/**
 * memory_card.js — shared memory-card renderer + media viewer
 * Used by: index.html, maintenance.html
 * Exports: window.MemoryCard = { createMemoryElement, openMediaActionModal }
 */
(function (root) {
  'use strict';

  // ── i18n ──────────────────────────────────────────────────────────────────
  var _MC_I18N = {
    ru: {
      catImportantMoments: '💫 Важные моменты',
      catMemories:         '📖 Воспоминания',
      catDates:            '📅 Важные даты',
      memoryNoTitle:       '(без названия)',
      metaAddedBy:         '👤 Добавил(а):',
      metaAdded:           '📌 Добавлено:',
      metaEdited:          '• отредактировано:',
      atWord:              'в',
      monthJan:'января',monthFeb:'февраля',monthMar:'марта',monthApr:'апреля',
      monthMay:'мая',monthJun:'июня',monthJul:'июля',monthAug:'августа',
      monthSep:'сентября',monthOct:'октября',monthNov:'ноября',monthDec:'декабря',
      mediaOpen:           'Открыть',
      mediaDownload:       'Скачать',
      mediaClose:          'Закрыть',
      mediaExit:           'Выйти',
      mediaPhoto:          'Фото',
      mediaVideo:          'Видео',
      mediaVoice:          'Голосовое сообщение',
      mediaAudioLabel:     'Аудио',
      mediaAudio:          'Аудиофайл',
      mediaDocument:       'Документ',
      mediaArchive:        'Архив',
      mediaMedia:          'Медиа',
      mediaFormatHint:     'Этот формат удобнее открыть или скачать',
      mediaFormatNotReady: 'Просмотр для этого формата пока не настроен.',
      mediaOpenFull:       'Открыть полностью',
      mediaCollapse:       'Свернуть',
      mediaOpenFile:       'Открыть файл',
      mediaOpenMedia:      'Открыть медиа',
    },
    en: {
      catImportantMoments: '💫 Important moments',
      catMemories:         '📖 Memories',
      catDates:            '📅 Important dates',
      memoryNoTitle:       '(untitled)',
      metaAddedBy:         '👤 Added by:',
      metaAdded:           '📌 Added:',
      metaEdited:          '• edited:',
      atWord:              'at',
      monthJan:'January',monthFeb:'February',monthMar:'March',monthApr:'April',
      monthMay:'May',monthJun:'June',monthJul:'July',monthAug:'August',
      monthSep:'September',monthOct:'October',monthNov:'November',monthDec:'December',
      mediaOpen:           'Open',
      mediaDownload:       'Download',
      mediaClose:          'Close',
      mediaExit:           'Exit',
      mediaPhoto:          'Photo',
      mediaVideo:          'Video',
      mediaVoice:          'Voice message',
      mediaAudioLabel:     'Audio',
      mediaAudio:          'Audio file',
      mediaDocument:       'Document',
      mediaArchive:        'Archive',
      mediaMedia:          'Media',
      mediaFormatHint:     'This format is easier to open or download',
      mediaFormatNotReady: 'Preview for this format is not set up yet.',
      mediaOpenFull:       'Open full',
      mediaCollapse:       'Collapse',
      mediaOpenFile:       'Open file',
      mediaOpenMedia:      'Open media',
    },
    de: {
      catImportantMoments: '💫 Wichtige Momente',
      catMemories:         '📖 Erinnerungen',
      catDates:            '📅 Wichtige Daten',
      memoryNoTitle:       '(ohne Titel)',
      metaAddedBy:         '👤 Hinzugefügt von:',
      metaAdded:           '📌 Hinzugefügt:',
      metaEdited:          '• bearbeitet:',
      atWord:              'um',
      monthJan:'Januar',monthFeb:'Februar',monthMar:'März',monthApr:'April',
      monthMay:'Mai',monthJun:'Juni',monthJul:'Juli',monthAug:'August',
      monthSep:'September',monthOct:'Oktober',monthNov:'November',monthDec:'Dezember',
      mediaOpen:           'Öffnen',
      mediaDownload:       'Herunterladen',
      mediaClose:          'Schließen',
      mediaExit:           'Beenden',
      mediaPhoto:          'Foto',
      mediaVideo:          'Video',
      mediaVoice:          'Sprachnachricht',
      mediaAudioLabel:     'Audio',
      mediaAudio:          'Audiodatei',
      mediaDocument:       'Dokument',
      mediaArchive:        'Archiv',
      mediaMedia:          'Medien',
      mediaFormatHint:     'Dieses Format lässt sich besser öffnen oder herunterladen',
      mediaFormatNotReady: 'Für dieses Format ist die Vorschau noch nicht eingerichtet.',
      mediaOpenFull:       'Vollständig öffnen',
      mediaCollapse:       'Einklappen',
      mediaOpenFile:       'Datei öffnen',
      mediaOpenMedia:      'Medien öffnen',
    },
  };

  function _mcLang() {
    // 1. index.html exposes current lang via window._stgState.lang
    if (root._stgState && root._stgState.lang) return root._stgState.lang;
    // 2. maintenance.html exposes currentLang global
    if (typeof root.currentLang === 'string' && root.currentLang) return root.currentLang;
    // 3. localStorage fallback (same key used by both pages)
    try { var l = localStorage.getItem('memories_lang'); if (l) return l; } catch (_) {}
    return 'ru';
  }

  function _t(key) {
    var l = _mcLang();
    var tr = _MC_I18N[l] || _MC_I18N.ru;
    return tr[key] || _MC_I18N.ru[key] || key;
  }

  // ── Utilities ─────────────────────────────────────────────────────────────
  function _mcEscapeHtml(text) {
    return String(text || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function _mcGetTz() {
    try {
      var s = localStorage.getItem('memories_tz_override');
      if (s && s !== '__auto__') return s;
    } catch (_) {}
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (_) { return ''; }
  }

  function _mcParseUtcDate(str) {
    if (!str) return null;
    var iso = str.trim();
    if (iso.length === 10) {
      iso += 'T00:00:00Z';
    } else {
      if (iso.indexOf('T') === -1) iso = iso.replace(' ', 'T');
      var tp = iso.substring(10);
      if (!iso.endsWith('Z') && tp.indexOf('+') === -1 && tp.indexOf('-') === -1) iso += 'Z';
    }
    var d = new Date(iso);
    return isNaN(d.getTime()) ? null : d;
  }

  function _mcFormatDate(utcStr) {
    if (!utcStr) return '';
    var d = _mcParseUtcDate(utcStr);
    if (!d) return utcStr;
    var tz = _mcGetTz();
    var lang = _mcLang();
    var day, monthIdx, year, hours, minutes;
    try {
      var parts = new Intl.DateTimeFormat('en-US', {
        timeZone: tz || undefined,
        year: 'numeric', month: 'numeric', day: 'numeric',
        hour: 'numeric', minute: 'numeric', hour12: false,
      }).formatToParts(d);
      var pm = {};
      parts.forEach(function (p) { pm[p.type] = p.value; });
      day = Number(pm.day);
      monthIdx = Number(pm.month) - 1;
      year = Number(pm.year);
      hours = String(pm.hour).padStart(2, '0');
      if (hours === '24') hours = '00';
      minutes = String(pm.minute).padStart(2, '0');
    } catch (_) {
      day = d.getDate(); monthIdx = d.getMonth(); year = d.getFullYear();
      hours = String(d.getHours()).padStart(2, '0');
      minutes = String(d.getMinutes()).padStart(2, '0');
    }
    var timeStr = hours + ':' + minutes;
    var mk = 'month' + ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][monthIdx];
    var mn = _t(mk);
    var atW = _t('atWord');
    if (lang === 'en') return mn + ' ' + day + ', ' + year + ' ' + atW + ' ' + timeStr;
    if (lang === 'de') return day + '. ' + mn + ' ' + year + ' ' + atW + ' ' + timeStr;
    return day + ' ' + mn + ' ' + year + ' ' + atW + ' ' + timeStr;
  }

  function _mcRenderMeta(metaDiv) {
    var who = metaDiv.dataset.who || '';
    var createdAt = metaDiv.dataset.createdAt || '';
    var updatedAt = metaDiv.dataset.updatedAt || '';
    var html = '';
    if (who) html += _t('metaAddedBy') + ' ' + _mcEscapeHtml(who);
    if (createdAt) {
      if (html) html += '<br>';
      html += _t('metaAdded') + ' ' + _mcEscapeHtml(_mcFormatDate(createdAt));
      if (updatedAt && updatedAt !== createdAt) {
        var tC = _mcParseUtcDate(createdAt), tU = _mcParseUtcDate(updatedAt);
        var diff = tC && tU ? Math.round((tU - tC) / 1000) : 0;
        if (diff >= 60) {
          html += ' ' + _t('metaEdited') + ' ' + _mcEscapeHtml(_mcFormatDate(updatedAt));
        }
      }
    }
    metaDiv.innerHTML = html;
  }

  // ── Media helpers ─────────────────────────────────────────────────────────
  var _IMG_EX = new Set(['jpg','jpeg','png','gif','webp','avif','bmp','svg']);
  var _VID_EX = new Set(['mp4','mov','webm','mkv','m4v','avi']);
  var _AUD_EX = new Set(['mp3','ogg','oga','wav','aac','m4a','flac','opus']);

  function _mcPathToUrl(path) {
    var raw = String(path || '').trim();
    if (!raw) return '';
    var idx = raw.lastIndexOf('/media/');
    if (idx >= 0) return '/' + raw.slice(idx + 1);
    return '/media/' + raw.split('/').pop();
  }

  function _mcThumbUrl(item) {
    if (!item || typeof item !== 'object') return '';
    return _mcPathToUrl(String(item.preview_url || item.thumb_url || item.thumb_path || item.url || item.media_url || '').trim());
  }

  function _mcOriginalUrl(item) {
    if (!item || typeof item !== 'object') return '';
    return _mcPathToUrl(String(item.original_url || item.url || item.media_url || item.media_path || item.path || '').trim());
  }

  function _mcFileNameFromUrl(url) {
    var raw = String(url || '').trim();
    if (!raw) return '';
    try { return decodeURIComponent((new URL(raw, location.origin).pathname.split('/').pop() || '').trim()); }
    catch (_) { return (raw.split('?')[0].split('#')[0].split('/').pop() || '').trim(); }
  }

  function _mcExtFromName(name) {
    var raw = String(name || '').trim().toLowerCase();
    if (!raw) return '';
    var idx = raw.lastIndexOf('.');
    return idx <= 0 ? '' : raw.slice(idx + 1);
  }

  function _mcKind(type, ext) {
    var e = String(ext || '').toLowerCase(), t = String(type || '').toLowerCase();
    if (_IMG_EX.has(e)) return 'photo';
    if (_VID_EX.has(e)) return 'video';
    if (_AUD_EX.has(e)) return 'audio';
    if (t === 'photo') return 'photo';
    if (t === 'video' || t === 'video_note') return 'video';
    if (t === 'audio' || t === 'voice') return 'audio';
    return 'file';
  }

  function _mcNormalize(item) {
    if (!item || typeof item !== 'object') return null;
    var src = _mcOriginalUrl(item) || _mcThumbUrl(item) || _mcPathToUrl(String(item.src || item.url || item.media_url || item.media_path || item.path || '').trim());
    if (!src) return null;
    var thumb = _mcThumbUrl(item) || src;
    var type = String(item.type || item.media_type || '').trim().toLowerCase();
    var name = String(item.original_filename || item.name || item.filename || item.media_name || '').trim() || _mcFileNameFromUrl(src);
    var ext = _mcExtFromName(name || src);
    var kind = _mcKind(type, ext);
    return {
      src: src, thumb: thumb, type: type, name: name, ext: ext, kind: kind,
      mimeType: String(item.mime_type || '').trim(),
      fileSize: Number.isFinite(Number(item.file_size)) ? Number(item.file_size) : null,
      durationSec: Number.isFinite(Number(item.duration_sec)) ? Number(item.duration_sec) : null,
      width: Number.isFinite(Number(item.width)) ? Number(item.width) : null,
      height: Number.isFinite(Number(item.height)) ? Number(item.height) : null
    };
  }

  function _mcFmtSize(bytes) {
    if (bytes === null || bytes === undefined || bytes === '') return '';
    var value = Number(bytes);
    if (!isFinite(value) || value < 0) return '';
    if (value < 1024) return Math.round(value) + ' Б';
    var units = ['КБ','МБ','ГБ','ТБ'];
    var size = value / 1024;
    var idx = 0;
    while (size >= 1024 && idx < units.length - 1) { size /= 1024; idx += 1; }
    return size.toFixed(size >= 10 ? 0 : 1) + ' ' + units[idx];
  }

  function _mcFmtResolution(w, h) {
    w = Number(w); h = Number(h);
    if (!isFinite(w) || !isFinite(h) || w <= 0 || h <= 0) return '';
    return Math.round(w) + '×' + Math.round(h);
  }

  function _mcFmtPhotoQuality(w, h) {
    w = Number(w); h = Number(h);
    var maxSide = Math.max(w || 0, h || 0);
    if (!isFinite(maxSide) || maxSide <= 0) return '';
    var buckets = [4320, 2160, 1440, 1080, 720, 480, 360, 240];
    for (var i = 0; i < buckets.length; i += 1) {
      if (maxSide >= buckets[i]) return buckets[i] + 'p';
    }
    return Math.round(maxSide) + 'p';
  }

  function _mcMetaLines(media) {
    if (!media) return [];
    var lines = [];
    var duration = (isFinite(media.durationSec) && media.durationSec >= 0) ? _mcFmtDuration(media.durationSec) : '';
    var resolution = _mcFmtResolution(media.width, media.height);
    var photoQuality = _mcFmtPhotoQuality(media.width, media.height);
    var size = _mcFmtSize(media.fileSize);
    if (media.kind === 'photo') {
      if (photoQuality) lines.push(photoQuality);
      return lines;
    }
    if (media.kind === 'video') {
      if (duration) lines.push(duration);
      if (resolution) lines.push(resolution);
      if (size) lines.push(size);
      return lines;
    }
    if (media.kind === 'audio') {
      if (duration) lines.push(duration);
      if (size) lines.push(size);
      return lines;
    }
    if (media.name) lines.push(media.name);
    if (size) lines.push(size);
    else if (media.ext) lines.push('.' + media.ext.toUpperCase());
    if (media.mimeType) lines.push(media.mimeType);
    return lines;
  }

  // ── Media viewer state + core functions ───────────────────────────────────
  var _vcCache = {}, _vdCache = {};
  var _vs = { media: null, actionOpen: false, viewerOpen: false, returnToAction: false, loadSeqToken: '' };

  function _mcDownload(media) {
    if (!media || !media.src) return;
    var a = document.createElement('a');
    a.href = media.src;
    a.download = media.name || _mcFileNameFromUrl(media.src) || '';
    a.rel = 'noopener noreferrer'; a.target = '_blank';
    document.body.appendChild(a); a.click(); a.remove();
  }

  function _mcExtractThumb(src, cb) {
    if (_vcCache[src]) { cb(_vcCache[src], _vdCache[src] || null); return; }
    var v = document.createElement('video');
    v.preload = 'auto'; v.muted = true; v.playsInline = true;
    var done = false;
    function cleanup() { if (done) return; done = true; v.removeAttribute('src'); v.load(); }
    v.addEventListener('loadedmetadata', function () {
      if (v.duration && isFinite(v.duration)) _vdCache[src] = v.duration;
      v.currentTime = Math.min(0.1, v.duration || 0);
    });
    v.addEventListener('seeked', function () {
      try {
        var c = document.createElement('canvas');
        c.width = v.videoWidth || 320; c.height = v.videoHeight || 240;
        c.getContext('2d').drawImage(v, 0, 0, c.width, c.height);
        var d = c.toDataURL('image/jpeg', 0.7);
        _vcCache[src] = d; cb(d, _vdCache[src] || null);
      } catch (_) { cb(null, _vdCache[src] || null); }
      cleanup();
    });
    v.addEventListener('error', function () { cb(null, null); cleanup(); });
    v.src = src;
  }

  function _mcGetDuration(src, cb) {
    if (_vdCache[src]) { cb(_vdCache[src]); return; }
    var v = document.createElement('video');
    v.preload = 'metadata'; v.muted = true; v.playsInline = true;
    v.addEventListener('loadedmetadata', function () {
      if (v.duration && isFinite(v.duration)) { _vdCache[src] = v.duration; cb(v.duration); }
      else cb(null);
      v.removeAttribute('src'); v.load();
    });
    v.addEventListener('error', function () { cb(null); v.removeAttribute('src'); v.load(); });
    v.src = src;
  }

  function _mcFmtDuration(s) {
    s = Math.round(s);
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    if (h > 0) return h + ':' + String(m).padStart(2,'0') + ':' + String(sec).padStart(2,'0');
    return m + ':' + String(sec).padStart(2,'0');
  }

  function _mcEnsureUi() {
    var ao = document.getElementById('mediaActionOverlay');
    var vo = document.getElementById('mediaViewerOverlay');
    if (!ao) {
      ao = document.createElement('div');
      ao.id = 'mediaActionOverlay';
      ao.className = 'media-action-backdrop hidden';
      ao.setAttribute('role', 'dialog');
      ao.setAttribute('aria-modal', 'true');
      ao.innerHTML = (
        '<div class="media-action-card" id="mediaActionCard">' +
          '<div class="media-action-topline"></div>' +
          '<div class="media-action-body">' +
            '<div class="media-action-kind" id="mediaActionKind"></div>' +
            '<div class="media-action-title" id="mediaActionTitle"></div>' +
            '<div class="media-action-meta" id="mediaActionMeta"></div>' +
            '<div class="media-action-preview" id="mediaActionPreview" style="display:none"></div>' +
            '<div class="media-action-buttons">' +
              '<button type="button" class="media-action-btn primary" id="mediaActionOpenBtn">' + _t('mediaOpen') + '</button>' +
              '<button type="button" class="media-action-btn secondary" id="mediaActionDownloadBtn">' + _t('mediaDownload') + '</button>' +
              '<button type="button" class="media-action-btn danger" id="mediaActionCloseBtn">' + _t('mediaClose') + '</button>' +
            '</div>' +
          '</div>' +
        '</div>'
      );
      ao.addEventListener('click', function (e) { if (e.target === ao) _mcHideAction(); });
      document.body.appendChild(ao);
    }
    if (!vo) {
      vo = document.createElement('div');
      vo.id = 'mediaViewerOverlay';
      vo.className = 'media-viewer-backdrop hidden';
      vo.setAttribute('role', 'dialog');
      vo.setAttribute('aria-modal', 'true');
      vo.innerHTML = '<div class="media-viewer-shell" id="mediaViewerShell"></div>';
      vo.addEventListener('click', function (e) { if (e.target === vo) _mcCloseViewer(); });
      document.body.appendChild(vo);
    }
    var ui = {
      action: {
        overlay: ao,
        kind:    document.getElementById('mediaActionKind'),
        title:   document.getElementById('mediaActionTitle'),
        meta:    document.getElementById('mediaActionMeta'),
        preview: document.getElementById('mediaActionPreview'),
        openBtn: document.getElementById('mediaActionOpenBtn'),
        dlBtn:   document.getElementById('mediaActionDownloadBtn'),
        closeBtn:document.getElementById('mediaActionCloseBtn'),
      },
      viewer: {
        overlay: vo,
        shell:   document.getElementById('mediaViewerShell'),
      },
    };
    if (!ui.action.openBtn._mcBound) {
      ui.action.openBtn._mcBound = true;
      ui.action.openBtn.addEventListener('click', function () {
        if (_vs.media) _mcOpenViewer(_vs.media, true);
      });
    }
    if (!ui.action.dlBtn._mcBound) {
      ui.action.dlBtn._mcBound = true;
      ui.action.dlBtn.addEventListener('click', function () {
        if (_vs.media) _mcDownload(_vs.media);
      });
    }
    if (!ui.action.closeBtn._mcBound) {
      ui.action.closeBtn._mcBound = true;
      ui.action.closeBtn.addEventListener('click', function () { _mcHideAction(); });
    }
    if (!root._mcEscBound) {
      root._mcEscBound = true;
      document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape') return;
        if (_vs.viewerOpen) _mcCloseViewer();
        else if (_vs.actionOpen) _mcHideAction();
      });
    }
    return ui;
  }

  function _mcHideAction() {
    var ui = _mcEnsureUi();
    _vs.actionOpen = false;
    ui.action.overlay.classList.add('hidden');
    document.body.style.overflow = '';
  }

  function _mcGlassbar(media) {
    var bar = document.createElement('div');
    bar.className = 'media-viewer-glassbar';
    var acts = document.createElement('div');
    acts.className = 'media-viewer-glass-actions';
    var exitBtn = document.createElement('button');
    exitBtn.type = 'button'; exitBtn.className = 'media-viewer-glass-btn secondary';
    exitBtn.textContent = _t('mediaExit');
    exitBtn.onclick = function () { _mcCloseViewer(); };
    var dlBtn = document.createElement('button');
    dlBtn.type = 'button'; dlBtn.className = 'media-viewer-glass-btn primary';
    dlBtn.textContent = _t('mediaDownload');
    dlBtn.onclick = function () { _mcDownload(media); };
    acts.appendChild(exitBtn); acts.appendChild(dlBtn);
    bar.appendChild(acts);
    return bar;
  }

  function _mcClearShell() {
    var ui = _mcEnsureUi();
    ui.viewer.shell.querySelectorAll('video').forEach(function (v) { v.pause(); v.removeAttribute('src'); v.load(); });
    ui.viewer.shell.querySelectorAll('audio').forEach(function (a) { a.pause(); a.removeAttribute('src'); a.load(); });
    ui.viewer.shell.innerHTML = '';
  }

  function _mcOpenViewer(media, fromAction) {
    var ui = _mcEnsureUi();
    if (!media) return;
    _vs.media = media; _vs.viewerOpen = true;
    _vs.returnToAction = !!fromAction;
    _vs.loadSeqToken = media.src;
    ui.action.overlay.classList.add('hidden');
    ui.viewer.overlay.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
    _mcClearShell();
    var stage = document.createElement('div');
    stage.className = 'media-viewer-photo-stage';
    if (media.kind === 'photo') {
      var img = document.createElement('img');
      img.className = 'media-viewer-media';
      img.src = media.src; img.alt = media.name || '';
      img.decoding = 'async'; img.loading = 'eager';
      stage.appendChild(img);
      ui.viewer.shell.appendChild(stage);
      ui.viewer.shell.appendChild(_mcGlassbar(media));
    } else if (media.kind === 'video') {
      var vid = document.createElement('video');
      vid.className = 'media-viewer-media';
      vid.src = media.src; vid.controls = true;
      vid.autoplay = false; vid.playsInline = true; vid.preload = 'metadata';
      if (_vcCache[media.src]) vid.poster = _vcCache[media.src];
      stage.appendChild(vid);
      ui.viewer.shell.appendChild(stage);
      ui.viewer.shell.appendChild(_mcGlassbar(media));
    } else if (media.kind === 'audio') {
      var aw = document.createElement('div');
      aw.style.cssText = 'width:min(720px,100%);padding:24px;border-radius:24px;background:var(--media-panel-bg);border:1px solid var(--media-panel-border);box-shadow:var(--media-panel-shadow);color:var(--media-panel-text);';
      var at = document.createElement('div');
      at.style.cssText = 'color:var(--media-panel-text);font-size:1rem;font-weight:700;margin-bottom:14px;';
      at.textContent = media.name || _t('mediaAudio');
      var au = document.createElement('audio');
      au.controls = true; au.preload = 'metadata'; au.src = media.src;
      au.style.cssText = 'width:100%;display:block;';
      aw.appendChild(at); aw.appendChild(au);
      stage.appendChild(aw);
      ui.viewer.shell.appendChild(stage);
      ui.viewer.shell.appendChild(_mcGlassbar(media));
    } else {
      var ff = document.createElement('div');
      ff.className = 'media-file-viewer';
      ff.innerHTML = (
        '<div class="media-file-header">' +
          '<div class="media-file-title">' +
            '<div class="media-file-name">' + (media.name || _t('mediaMedia')) + '</div>' +
            '<div class="media-file-subtitle">' + _t('mediaFormatHint') + '</div>' +
          '</div>' +
          '<div class="media-file-header-actions">' +
            '<button type="button" class="media-file-header-btn primary" id="mcDlBtn">' + _t('mediaDownload') + '</button>' +
            '<button type="button" class="media-file-header-btn" id="mcClBtn">' + _t('mediaClose') + '</button>' +
          '</div>' +
        '</div>' +
        '<div class="media-file-codewrap"><div class="media-file-empty">' + _t('mediaFormatNotReady') + '</div></div>'
      );
      ui.viewer.shell.appendChild(ff);
      var dlb = ff.querySelector('#mcDlBtn'), clb = ff.querySelector('#mcClBtn');
      if (dlb) dlb.onclick = function () { _mcDownload(media); };
      if (clb) clb.onclick = function () { _mcCloseViewer(); };
    }
  }

  function _mcCloseViewer() {
    var ui = _mcEnsureUi();
    _vs.viewerOpen = false; _vs.loadSeqToken = '';
    ui.viewer.overlay.classList.add('hidden');
    _mcClearShell();
    if (_vs.returnToAction && _vs.media) {
      _vs.returnToAction = false;
      ui.action.overlay.classList.remove('hidden');
      document.body.style.overflow = 'hidden';
      return;
    }
    _vs.returnToAction = false;
    if (!_vs.actionOpen) document.body.style.overflow = '';
  }

  // ── Public: openMediaActionModal ──────────────────────────────────────────
  function openMediaActionModal(item) {
    var media = _mcNormalize(item);
    if (!media) return;
    var ui = _mcEnsureUi();
    _vs.media = media; _vs.actionOpen = true; _vs.returnToAction = false;
    var kind = media.kind, type = (media.type || '').toLowerCase(), ext = (media.ext || '').toLowerCase();
    var isArchive = ['zip','rar','tar','gz','7z','bz2','xz'].includes(ext);
    if (kind === 'photo') {
      ui.action.kind.textContent = '';
      ui.action.title.textContent = '📷 ' + _t('mediaPhoto');
    } else if (kind === 'video') {
      ui.action.kind.textContent = '';
      ui.action.title.textContent = '🎥 ' + _t('mediaVideo');
    } else if (kind === 'audio') {
      ui.action.kind.textContent = '';
      ui.action.title.textContent = type === 'voice' ? '🎵 ' + _t('mediaVoice') : '🎵 ' + _t('mediaAudioLabel');
    } else {
      ui.action.kind.textContent = isArchive ? '📦 ' + _t('mediaArchive') : '📄 ' + _t('mediaDocument');
      ui.action.title.textContent = media.name || _mcFileNameFromUrl(media.src) || _t('mediaDocument');
    }
    ui.action.meta.innerHTML = '';
    var renderMeta = function () {
      ui.action.meta.innerHTML = '';
      _mcMetaLines(media).forEach(function (line) {
        var row = document.createElement('div');
        row.textContent = line;
        ui.action.meta.appendChild(row);
      });
    };
    renderMeta();
    if ((kind === 'video' || kind === 'audio') && !media.durationSec) {
      _mcGetDuration(media.src, function (dur) {
        if (_vs.media === media && dur && isFinite(dur)) {
          media.durationSec = Math.round(dur);
          renderMeta();
        }
      });
    }
    ui.action.preview.innerHTML = '';
    if (kind === 'photo') {
      var pi = document.createElement('img');
      pi.src = media.thumb || media.src; pi.alt = media.name || '';
      ui.action.preview.appendChild(pi);
      ui.action.preview.style.display = 'block';
    } else {
      ui.action.preview.style.display = 'none';
    }
    ui.action.overlay.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
  }

  // ── Media handler + video preview (used by createMemoryElement) ───────────
  function _mcAttach(el, item) {
    var media = _mcNormalize(item);
    if (!el || !media) return el;
    el.style.cursor = 'pointer';
    el.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      openMediaActionModal(media);
    });
    return el;
  }

  function _mcVideoPreview(media) {
    var wrap = document.createElement('div');
    wrap.className = 'memory-media-video-wrap';
    var img = document.createElement('img');
    img.className = 'media'; img.loading = 'lazy'; img.decoding = 'async'; img.alt = '';
    img.onerror = function () { this.style.display = 'none'; };
    var hasPreview = media.thumb && media.thumb !== media.src && /\.(jpe?g|png|webp|gif)/i.test(media.thumb);
    if (hasPreview) {
      img.src = media.thumb;
    } else {
      _mcExtractThumb(media.src, function (dataUrl) {
        if (dataUrl) img.src = dataUrl; else img.style.display = 'none';
      });
    }
    wrap.appendChild(img);
    var play = document.createElement('div');
    play.className = 'memory-media-video-play';
    wrap.appendChild(play);
    _mcAttach(wrap, media);
    return wrap;
  }

  // ── Public: createMemoryElement ───────────────────────────────────────────
  function createMemoryElement(m, categoriesMap) {
    var cats = categoriesMap || {};
    var div = document.createElement('div');
    div.className = 'memory';
    div.dataset.memoryId = String(m.id);
    if (m.category) div.dataset.category = m.category;
    if (m.privacy_type) div.dataset.privacyType = m.privacy_type;
    if (m.privacy_views_limit != null) div.dataset.privacyViewsLimit = String(m.privacy_views_limit);
    if (m.privacy_question) div.dataset.privacyQuestion = m.privacy_question;

    var cat = cats[m.category] || {};
    var _catKeys = { important_moments: 'catImportantMoments', memories: 'catMemories', important_dates: 'catDates' };
    var _catI18nKey = _catKeys[m.category];
    var catTitle = _catI18nKey ? _t(_catI18nKey) : ((cat.emoji || '') + ' ' + (cat.title || m.category || ''));

    var categorySpan = document.createElement('span');
    categorySpan.className = 'category';
    categorySpan.dataset.catKey = m.category || '';
    categorySpan.textContent = catTitle.trim();
    div.appendChild(categorySpan);

    var titleDiv = document.createElement('div');
    titleDiv.className = 'title';
    titleDiv.textContent = m.title || _t('memoryNoTitle');
    div.appendChild(titleDiv);

    var dateDiv = document.createElement('div');
    dateDiv.className = 'date';
    var humanDate = m.date_human || m.date_resolved || m.date;
    dateDiv.textContent = humanDate ? ('📅 ' + humanDate) : '';
    div.appendChild(dateDiv);

    var contentDiv = document.createElement('div');
    contentDiv.className = 'content';
    contentDiv.innerHTML = m.content_html || '';
    div.appendChild(contentDiv);

    var fullTextLength = (contentDiv.textContent || '').trim().length;
    var MAX_CHARS_COLLAPSED = 500;
    if (fullTextLength > MAX_CHARS_COLLAPSED) {
      contentDiv.classList.add('content--collapsed');
      var toggleBtn = document.createElement('button');
      toggleBtn.type = 'button';
      toggleBtn.className = 'content-toggle';
      toggleBtn.textContent = _t('mediaOpenFull');
      toggleBtn.addEventListener('click', function () {
        var isCollapsed = contentDiv.classList.toggle('content--collapsed');
        toggleBtn.textContent = isCollapsed ? _t('mediaOpenFull') : _t('mediaCollapse');
      });
      div.appendChild(toggleBtn);
    }

    var mediaItems = Array.isArray(m.media_items) ? m.media_items.slice(0, 6) : [];
    if (mediaItems.length > 0) {
      var grid = document.createElement('div');
      grid.className = 'memory-media-grid';
      mediaItems.forEach(function (mi) {
        var media = _mcNormalize(mi);
        if (!media) return;
        var item = document.createElement('div');
        item.className = 'memory-media-item';
        if (media.kind === 'photo') {
          var img = document.createElement('img');
          img.className = 'media'; img.loading = 'lazy'; img.decoding = 'async';
          img.src = media.src || media.thumb; img.alt = '';
          _mcAttach(img, media);
          item.appendChild(img);
        } else if (media.kind === 'video') {
          item.appendChild(_mcVideoPreview(media));
        } else if (media.kind === 'audio') {
          item.classList.add('memory-media-item--full');
          var audio = document.createElement('audio');
          audio.controls = true; audio.preload = 'none'; audio.src = media.src;
          _mcAttach(audio, media);
          item.appendChild(audio);
        } else {
          item.classList.add('memory-media-item--full');
          var link = document.createElement('a');
          link.textContent = media.kind === 'file' ? _t('mediaOpenFile') : _t('mediaOpenMedia');
          link.className = 'memory-media-file-link';
          _mcAttach(link, media);
          item.appendChild(link);
        }
        grid.appendChild(item);
      });
      if (grid.children.length > 0) {
        if (grid.children.length === 1) grid.classList.add('memory-media-grid--single');
        div.appendChild(grid);
      }
    } else if (m.media_url) {
      var media2 = _mcNormalize({
        url: m.media_url,
        original_url: m.original_url || m.media_url,
        thumb_url: m.thumb_url || m.media_url,
        type: m.media_type,
        name: m.name || m.filename || m.media_name || '',
        original_filename: m.original_filename || m.name || m.filename || m.media_name || '',
        stored_filename: m.stored_filename || '',
        mime_type: m.mime_type || '',
        file_size: m.file_size,
        duration_sec: m.duration_sec,
        width: m.width,
        height: m.height
      });
      if (media2) {
        var sg = document.createElement('div');
        sg.className = 'memory-media-grid memory-media-grid--single';
        var si = document.createElement('div');
        si.className = 'memory-media-item memory-media-item--single-viewport';
        if (media2.kind === 'photo') {
          var img2 = document.createElement('img');
          img2.className = 'media'; img2.loading = 'lazy'; img2.decoding = 'async';
          img2.src = media2.src || media2.thumb; img2.alt = '';
          _mcAttach(img2, media2); si.appendChild(img2);
        } else if (media2.kind === 'video') {
          si.appendChild(_mcVideoPreview(media2));
        } else if (media2.kind === 'audio') {
          si.classList.add('memory-media-item--full');
          var au2 = document.createElement('audio');
          au2.controls = true; au2.preload = 'none'; au2.src = media2.src;
          _mcAttach(au2, media2); si.appendChild(au2);
        } else {
          si.classList.add('memory-media-item--full');
          var lk2 = document.createElement('a');
          lk2.textContent = _t('mediaOpenFile');
          lk2.className = 'memory-media-file-link';
          _mcAttach(lk2, media2); si.appendChild(lk2);
        }
        sg.appendChild(si);
        div.appendChild(sg);
      }
    }

    var metaDiv = document.createElement('div');
    metaDiv.className = 'meta';
    metaDiv.dataset.who = m.display_name || (m.first_name ? (m.last_name ? m.first_name + ' ' + m.last_name : m.first_name) : null) || (m.username ? '@' + m.username : null) || '';
    metaDiv.dataset.createdAt = m.created_at || '';
    metaDiv.dataset.updatedAt = m.updated_at || '';
    _mcRenderMeta(metaDiv);
    div.appendChild(metaDiv);

    return div;
  }

  // ── Export ────────────────────────────────────────────────────────────────
  root.MemoryCard = {
    createMemoryElement:  createMemoryElement,
    openMediaActionModal: openMediaActionModal,
  };

}(window));
