import datetime

US_HOLIDAYS_2026 = {
    (1, 1),   # New Year
    (1, 19),  # MLK Day
    (2, 16),  # Presidents Day
    (4, 3),   # Good Friday
    (5, 25),  # Memorial Day
    (6, 19),  # Juneteenth
    (7, 4),   # Independence Day
    (9, 7),   # Labor Day
    (11, 26), # Thanksgiving
    (12, 25), # Christmas
}

def is_market_open(date=None):
    if date is None:
        date = datetime.datetime.now()
    
    # Выходные
    if date.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    
    # Праздники
    if (date.month, date.day) in US_HOLIDAYS_2026:
        return False
    
    return True

def next_trading_day(date=None):
    if date is None:
        date = datetime.datetime.now()
    next_day = date + datetime.timedelta(days=1)
    while not is_market_open(next_day):
        next_day += datetime.timedelta(days=1)
    return next_day.strftime('%Y-%m-%d')
