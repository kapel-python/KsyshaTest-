def run():
    with open('index.html', 'r', encoding='utf-8') as f:
        lines = f.readlines()
        
    def find_line(prefix, start=0):
        for i in range(start, len(lines)):
            if prefix in lines[i]:
                return i
        return -1
        
    print("css_start:", find_line("        .memory {\n"))
    print("css_end:", find_line("        .memory-views-hint {\n"))
    
    print("css_lock_start:", find_line("        .memory-lock-overlay {\n"))
    print("css_lock_end:", find_line("        @keyframes shimmer-title {\n"))
    
    # functions
    funcs = [
        "_normalizeMediaItem",
        "_createVideoPreview",
        "_attachMediaOpenHandler",
        "openMediaActionModal",
        "_showMediaActionModal",
        "renderMetaElement",
        "createMemoryElement"
    ]
    for fn in funcs:
        start = find_line(f"      function {fn}(")
        if start == -1:
            print(f"FAILED TO FIND {fn}")
            continue
            
        # find the end (next function or specific end)
        # for _showMediaActionModal, next is _clearMediaViewerShell
        if fn == "_showMediaActionModal":
            end = find_line("      function _clearMediaViewerShell() {\n", start + 1)
        elif fn == "renderMetaElement":
            end = find_line("      function createMemoryElement(", start + 1)
        elif fn == "createMemoryElement":
            end = find_line("      function createEventElement(", start + 1)
        else:
            end = find_line("      function ", start + 1)
            
        print(f"{fn}: {start} to {end}")

run()
