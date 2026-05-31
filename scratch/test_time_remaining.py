from datetime import datetime, timezone, timedelta
import calendar

def format_time_remaining_new(event_utc: datetime, now_utc: datetime) -> str:
    if event_utc <= now_utc:
        return "0"
        
    # Calculate calendar difference precisely
    years = event_utc.year - now_utc.year
    try:
        temp = now_utc.replace(year=event_utc.year)
    except ValueError:
        temp = now_utc.replace(year=event_utc.year, day=28)
        
    if temp > event_utc:
        years -= 1
        y = event_utc.year - 1
        try:
            temp = now_utc.replace(year=y)
        except ValueError:
            temp = now_utc.replace(year=y, day=28)
            
    def add_months(dt, m):
        y_add, m_add = divmod(dt.month - 1 + m, 12)
        new_year = dt.year + y_add
        new_month = m_add + 1
        _, max_days = calendar.monthrange(new_year, new_month)
        new_day = min(dt.day, max_days)
        return dt.replace(year=new_year, month=new_month, day=new_day)
        
    months = 0
    while True:
        next_temp = add_months(temp, months + 1)
        if next_temp > event_utc:
            break
        months += 1
        
    temp_months = add_months(temp, months)
    delta = event_utc - temp_months
    days = delta.days
    
    total_seconds = delta.seconds
    hours, rest = divmod(total_seconds, 3600)
    mins, _ = divmod(rest, 60)
    
    parts = []
    if years > 0:
        parts.append(f"{years} г.")
    if months > 0:
        parts.append(f"{months} мес")
    if days > 0:
        parts.append(f"{days} д")
    if hours > 0 or mins > 0 or not parts:
        parts.append(f"{hours}ч")
        parts.append(f"{mins}м")
    return " ".join(parts)

# Test exact 1 year minus 4 minutes
now = datetime(2026, 5, 31, 15, 57, 3)
event = datetime(2027, 5, 31, 15, 53, 0)
print("1 year - 4 mins:", format_time_remaining_new(event, now))

# Test exact 1 year
event_exact = datetime(2027, 5, 31, 15, 57, 3)
print("Exact 1 year:", format_time_remaining_new(event_exact, now))

# Test leap year handling (e.g. Feb 29 to next year Feb 28)
now_leap = datetime(2024, 2, 29, 12, 0, 0)
event_leap = datetime(2025, 2, 28, 12, 0, 0)
print("Leap Year (29 Feb 2024 -> 28 Feb 2025):", format_time_remaining_new(event_leap, now_leap))
