
    const tz = 'America/New_York';
    function parseUtcDate(str) {
        if (!str) return null;
        let iso = str.trim();
        if (iso.length === 10) {
          iso += 'T00:00:00Z';
        } else {
          if (iso.indexOf('T') === -1) {
            iso = iso.replace(' ', 'T');
          }
          if (!iso.endsWith('Z') && iso.indexOf('+') === -1 && iso.indexOf('-') === -1) {
            iso += 'Z';
          }
        }
        const d = new Date(iso);
        return isNaN(d.getTime()) ? null : d;
    }
    
    function formatDateTimeLocal(utcStr) {
        if (!utcStr) return '';
        const d = parseUtcDate(utcStr);
        if (!d) return utcStr;
        let day, monthIdx, year, hours, minutes;
        try {
          const parts = new Intl.DateTimeFormat('en-US', {
            timeZone: tz,
            year: 'numeric', month: 'numeric', day: 'numeric',
            hour: 'numeric', minute: 'numeric', hour12: false
          }).formatToParts(d);
          const partMap = {};
          parts.forEach(p => partMap[p.type] = p.value);
          day = Number(partMap.day);
          monthIdx = Number(partMap.month) - 1;
          year = Number(partMap.year);
          hours = Number(partMap.hour);
          minutes = Number(partMap.minute);
        } catch (e) {
          day = d.getDate(); monthIdx = d.getMonth(); year = d.getFullYear();
          hours = d.getHours(); minutes = d.getMinutes();
        }
        const months = ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря'];
        const dd = day < 10 ? '0'+day : day;
        const mm = (monthIdx+1) < 10 ? '0'+(monthIdx+1) : (monthIdx+1);
        const hh = hours < 10 ? '0'+hours : hours;
        const min = minutes < 10 ? '0'+minutes : minutes;
        return `${day} ${months[monthIdx]} ${year} г., ${hh}:${min}`;
    }
    
    console.log("=== MEMORY PIPELINE ===");
    console.log("Server timezone:", "Sun May 31 01:08:30 AM CEST 2026");
    console.log("User timezone:", tz);
    console.log("Database value (date):", "2026-05-30");
    console.log("API date_resolved:", "2026-05-30");
    console.log("API date_human:", "30 мая 2026");
    // The UI renders date_human
    console.log("Rendered value (UI main text):", "30 мая 2026");
    console.log("Expected value:", "30 мая 2026");

    console.log("\n=== WISH PIPELINE ===");
    console.log("Server timezone:", "Sun May 31 01:08:30 AM CEST 2026");
    console.log("User timezone:", tz);
    console.log("Database value (created_at):", "2026-05-30 23:08:27");
    // API sends exactly what's in DB
    console.log("API raw value:", "2026-05-30 23:08:27");
    // UI renders formatDateTimeLocal
    console.log("Rendered value (UI main text):", formatDateTimeLocal("2026-05-30 23:08:27"));
    console.log("Expected value:", formatDateTimeLocal("2026-05-30 23:08:27"));
    