import datetime
import pandas as pd

# 台灣股市已知休假日 (2025 - 2026 範例，可自行擴充)
# 格式為 YYYY-MM-DD
KNOWN_HOLIDAYS = {
    # 2025
    "2025-01-01", # 元旦
    "2025-01-25", # 春節
    "2025-01-26", 
    "2025-01-27", 
    "2025-01-28", 
    "2025-01-29",
    "2025-01-30",
    "2025-01-31",
    "2025-02-28", # 和平紀念日
    "2025-04-03", # 兒童節
    "2025-04-04", # 清明節
    "2025-05-01", # 勞動節
    "2025-05-30", # 端午節
    "2025-10-06", # 中秋節
    "2025-10-10", # 國慶日
    
    # 2026
    "2026-01-01", # 元旦
    "2026-02-13", # 春節
    "2026-02-16", 
    "2026-02-17",
    "2026-02-18",
    "2026-02-19",
    "2026-02-20",
    "2026-02-27", # 和平紀念日
    "2026-04-03", # 兒童節/清明節
    "2026-04-06",
    "2026-05-01", # 勞動節
    "2026-06-19", # 端午節
    "2026-09-25", # 中秋節
    "2026-10-09", # 國慶日
    "2026-10-23", # 颱風假休市 (天然災害停止上班)
    "2026-10-26", # 國定假日補假休市
}

# 補班日但有開市 (通常六日不開市，除非補班)
MAKE_UP_WORKDAYS = {
    # 2025 (若有補班且開市的日子填入此)
}

def is_trading_day(target_date) -> bool:
    """判斷給定的日期是否為台股交易日 (支援 'YYYY-MM-DD' 字串、date 或 datetime 物件)"""
    if isinstance(target_date, (datetime.date, datetime.datetime)):
        date_str = target_date.strftime("%Y-%m-%d")
        dt = target_date if isinstance(target_date, datetime.date) else target_date.date()
    elif isinstance(target_date, str):
        date_str = target_date
        try:
            dt = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return False
    else:
        return False
        
    # 如果在補班日名單內，則是交易日
    if date_str in MAKE_UP_WORKDAYS:
        return True
        
    # 如果是國定假日，則非交易日
    if date_str in KNOWN_HOLIDAYS:
        return False
        
    # 週末(週六=5, 週日=6)非交易日
    if dt.weekday() >= 5:
        return False
        
    return True

def get_last_trading_day(target_date=None) -> str:
    """取得目標日期前（含當日若為交易日）之最近交易日，或往前推算"""
    if target_date is None:
        target_date = datetime.date.today()
    elif isinstance(target_date, str):
        target_date = datetime.datetime.strptime(target_date, "%Y-%m-%d").date()
    elif isinstance(target_date, datetime.datetime):
        target_date = target_date.date()
        
    curr = target_date
    while not is_trading_day(curr):
        curr -= datetime.timedelta(days=1)
    return curr.strftime("%Y-%m-%d")

def get_next_trading_day(target_date=None) -> str:
    """取得目標日期之後的第一個正常開市交易日"""
    if target_date is None:
        target_date = datetime.date.today()
    elif isinstance(target_date, str):
        target_date = datetime.datetime.strptime(target_date, "%Y-%m-%d").date()
    elif isinstance(target_date, datetime.datetime):
        target_date = target_date.date()
        
    curr = target_date + datetime.timedelta(days=1)
    while not is_trading_day(curr):
        curr += datetime.timedelta(days=1)
    return curr.strftime("%Y-%m-%d")

def get_recent_trading_days(days: int = 10, end_date: str = None) -> list:
    """取得最近 N 個交易日的日期字串 (YYYY-MM-DD)"""
    if end_date is None:
        end_date = datetime.datetime.now().strftime("%Y-%m-%d")
        
    dt_end = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()
    trading_days = []
    
    # 往前推 30 天，過濾出交易日，取前 days 個
    for i in range(days * 3):
        test_date = dt_end - datetime.timedelta(days=i)
        test_str = test_date.strftime("%Y-%m-%d")
        if is_trading_day(test_str):
            trading_days.append(test_str)
            if len(trading_days) == days:
                break
                
    return trading_days[::-1] # 反轉，讓舊的在前面，新的在後面

if __name__ == "__main__":
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    print(f"Today {today} is trading day? {is_trading_day(today)}")
    print(f"Recent 5 trading days: {get_recent_trading_days(5)}")
