def extract_and_refactor():
    with open('index.html', 'r', encoding='utf-8') as f:
        lines = f.readlines()
        
    def find_line(prefix, start=0):
        for i in range(start, len(lines)):
            if prefix in lines[i]:
                return i
        return -1

    def find_block_end(start_idx):
        open_braces = 0
        started = False
        for i in range(start_idx, len(lines)):
            line = lines[i]
            for char in line:
                if char == '{':
                    open_braces += 1
                    started = True
                elif char == '}':
                    open_braces -= 1
            if started and open_braces == 0:
                return i + 1
        return -1
        
    # CSS blocks
    css1_start = find_line("        .memory {\n")
    css1_end = find_line("        .memory-views-hint {\n")
    
    css2_start = find_line("        .memory-lock-overlay {\n")
    css2_end = find_line("        @keyframes shimmer-title {\n")
    
    css_content = "/* Extracted from index.html */\n"
    css_content += "".join(lines[css1_start:css1_end])
    css_content += "".join(lines[css2_start:css2_end])
    
    with open('memory_card.css', 'w', encoding='utf-8') as f:
        f.write(css_content)
        
    # JS Functions
    js_blocks = []
    
    def extract_func(start_prefix):
        start_idx = find_line(start_prefix)
        if start_idx == -1:
            print(f"NOT FOUND: {start_prefix}")
            return -1, -1
            
        end_idx = find_block_end(start_idx)
        if end_idx == -1:
            print(f"NOT FOUND END for {start_prefix}")
            return -1, -1
            
        js_blocks.append("".join(lines[start_idx:end_idx]))
        return start_idx, end_idx
        
    ranges = [
        (css1_start, css1_end),
        (css2_start, css2_end)
    ]
    
    funcs = [
        "      function _normalizeMediaItem(item) {\n",
        "      function _createVideoPreview(media) {\n",
        "      function _attachMediaOpenHandler(el, item, options = {}) {\n",
        "      function renderMetaElement(metaDiv) {\n",
        "      function createMemoryElement(m, categoriesMap) {\n",
        "      function openMediaActionModal(item) {\n",
        "      function _showMediaActionModal(media) {\n"
    ]
    
    for fn in funcs:
        s, e = extract_func(fn)
        ranges.append((s, e))
        
    for r in ranges:
        if -1 in r:
            print("ERROR: missing extraction!")
            return
            
    # Write memory_card.js
    with open('memory_card.js', 'w', encoding='utf-8') as f:
        f.write("""/* Memory Card Shared JS */
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
""")
        f.write("\n".join(js_blocks))
        f.write("""
window.createMemoryElement = createMemoryElement;
window.MemoryCard.createMemoryElement = createMemoryElement;
window.openMediaActionModal = openMediaActionModal;
window.MemoryCard.openMediaActionModal = openMediaActionModal;
window._normalizeMediaItem = _normalizeMediaItem;
window._createVideoPreview = _createVideoPreview;
window._attachMediaOpenHandler = _attachMediaOpenHandler;
window._showMediaActionModal = _showMediaActionModal;
""")

    # modify index.html
    ranges.sort(reverse=True)
    
    with open('index.html', 'r', encoding='utf-8') as f:
        idx_content = f.read()
        
    for s, e in ranges:
        block = "".join(lines[s:e])
        if block in idx_content:
            idx_content = idx_content.replace(block, "")
            
    # Add script/link tags
    if '    <link rel="stylesheet" href="/memory-card.css' not in idx_content:
        idx_content = idx_content.replace('    <link rel="stylesheet" href="/style.css">', '    <link rel="stylesheet" href="/memory-card.css?v=6">\n    <link rel="stylesheet" href="/style.css">')
        
    if '  <script src="/memory-card.js' not in idx_content:
        idx_content = idx_content.replace('</body>\n</html>', '  <script src="/memory-card.js?v=6"></script>\n</body>\n</html>')
        
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(idx_content)
        
    print("DONE SCRIPT")

extract_and_refactor()
