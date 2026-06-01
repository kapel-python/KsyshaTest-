import re

with open('/root/KsyshaTest/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Define the new formatRemaining function code
new_func = """            function formatRemaining(iso) {
              if (!iso) return { text: '—', done: true };
              var eventDate = new Date(iso);
              if (isNaN(eventDate.getTime())) return { text: '—', done: true };
              var now = new Date();
              var totalSec = Math.max(0, Math.floor((eventDate - now) / 1000));
              if (totalSec <= 0) return { text: _str('countdownDone'), done: true };

              var activeLang = (_stgState && _stgState.lang) || 'ru';

              function getDaysInMonth(year, month) {
                return new Date(year, month + 1, 0).getDate();
              }

              function addMonths(date, m) {
                var d = new Date(date.getTime());
                var day = d.getDate();
                d.setDate(1);
                d.setMonth(d.getMonth() + m);
                var daysInMonth = getDaysInMonth(d.getFullYear(), d.getMonth());
                d.setDate(Math.min(day, daysInMonth));
                return d;
              }

              function pluralize(n, one, two, many) {
                if (n % 10 === 1 && n % 100 !== 11) {
                  return n + ' ' + one;
                } else if ([2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100)) {
                  return n + ' ' + two;
                } else {
                  return n + ' ' + many;
                }
              }

              function getPluralText(n, unit) {
                if (activeLang === 'de') {
                  if (unit === 'year') return n + (n === 1 ? ' Jahr' : ' Jahre');
                  if (unit === 'month') return n + (n === 1 ? ' Monat' : ' Monate');
                  if (unit === 'day') return n + (n === 1 ? ' Tag' : ' Tage');
                  if (unit === 'hour') return n + (n === 1 ? ' Stunde' : ' Stunden');
                  if (unit === 'minute') return n + (n === 1 ? ' Minute' : ' Minuten');
                  if (unit === 'second') return n + (n === 1 ? ' Sekunde' : ' Sekunden');
                }
                if (activeLang === 'en') {
                  if (unit === 'year') return n + (n === 1 ? ' year' : ' years');
                  if (unit === 'month') return n + (n === 1 ? ' month' : ' months');
                  if (unit === 'day') return n + (n === 1 ? ' day' : ' days');
                  if (unit === 'hour') return n + (n === 1 ? ' hour' : ' hours');
                  if (unit === 'minute') return n + (n === 1 ? ' minute' : ' minutes');
                  if (unit === 'second') return n + (n === 1 ? ' second' : ' seconds');
                }
                if (unit === 'year') return pluralize(n, 'год', 'года', 'лет');
                if (unit === 'month') return pluralize(n, 'месяц', 'месяца', 'месяцев');
                if (unit === 'day') return pluralize(n, 'день', 'дня', 'дней');
                if (unit === 'hour') return pluralize(n, 'час', 'часа', 'часов');
                if (unit === 'minute') return pluralize(n, 'минута', 'минуты', 'минут');
                if (unit === 'second') return pluralize(n, 'секунда', 'секунды', 'секунд');
                return n + ' ' + unit;
              }

              var months = 0;
              var temp = new Date(now.getTime());
              while (true) {
                var nextTemp = addMonths(temp, 1);
                if (nextTemp > eventDate) {
                  break;
                }
                temp = nextTemp;
                months++;
              }

              var years = Math.floor(months / 12);
              months = months % 12;

              var diffMs = eventDate.getTime() - temp.getTime();
              var totalSecRest = Math.max(0, Math.floor(diffMs / 1000));
              var days = Math.floor(totalSecRest / 86400);
              var rest = totalSecRest % 86400;
              var hours = Math.floor(rest / 3600);
              rest = rest % 3600;
              var mins = Math.floor(rest / 60);
              var secs = rest % 60;

              var parts = [];
              if (years > 0) parts.push(getPluralText(years, 'year'));
              if (months > 0) parts.push(getPluralText(months, 'month'));
              if (days > 0) parts.push(getPluralText(days, 'day'));
              if (hours > 0) parts.push(getPluralText(hours, 'hour'));
              if (mins > 0) parts.push(getPluralText(mins, 'minute'));
              if (secs > 0 || parts.length === 0) parts.push(getPluralText(secs, 'second'));

              return { text: parts.join(' '), done: false };
            }"""

# Use regex to find and replace the formatRemaining function in index.html
# We match "function formatRemaining(iso) {" up to the corresponding closing "}"
pattern = r'(            function formatRemaining\(iso\) \{.*?\n            \})'
match = re.search(pattern, content, re.DOTALL)
if match:
    print("Found formatRemaining, replacing...")
    new_content = content.replace(match.group(1), new_func)
    with open('/root/KsyshaTest/index.html', 'w', encoding='utf-8') as f:
        f.write(new_content)
    print("Successfully replaced!")
else:
    print("Could not find formatRemaining pattern!")
