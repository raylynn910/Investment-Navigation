import requests
import urllib3
from bs4 import BeautifulSoup
import re
import duckdb
from datetime import datetime
import argparse
import os
import sys

urllib3.disable_warnings()

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'market_data.duckdb')

def get_default_revenue_target_date():
    """
    根據當前日期自動推算營收發布日標籤 (YYYY-MM-10)。
    台灣法規：M 月份營收於 M+1 月 10 日前公布，系統統一以發布月 10 日 (YYYY-{M+1}-10) 作為標籤：
    - 例如 11 月 1~15 日期間公告的是 10 月份營收 -> 標籤為 2026-11-10
    - 例如 10 月 1~15 日期間公告的是 9 月份營收 -> 標籤為 2026-10-10
    """
    now = datetime.now()
    return f"{now.year}-{now.month:02d}-10"

def safe_float(val):
    if not val or val.strip() == '-' or val.strip() == '':
        return None
    val = val.replace(',', '').replace('%', '').strip()
    try:
        return float(val)
    except ValueError:
        return None

def scrape_moneydj_revenue(target_date=None):
    """
    爬取 MoneyDJ「今日營收快報」頁面 (https://www.moneydj.com/Z/ZE/ZEX/ZEX.djhtm?a=1)
    取得當天最新公告之個股單月營收、YoY%、MoM%，清洗後寫入 DuckDB revenues 表。
    """
    if not target_date:
        target_date = get_default_revenue_target_date()

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    url = 'https://www.moneydj.com/Z/ZE/ZEX/ZEX.djhtm?a=1'
    print(f"開始爬取 MoneyDJ 最新營收快報，目標歸屬月份: {target_date}，網址: {url}")
    
    try:
        res = requests.get(url, headers=headers, verify=False, timeout=15)
        # MoneyDJ 網頁編碼為 Big5 / CP950
        res.encoding = 'cp950'
    except Exception as e:
        print(f"連線失敗: {e}")
        return {"status": "error", "message": f"連線失敗: {e}", "count": 0}

    soup = BeautifulSoup(res.text, 'html.parser')
    
    # 尋找資料表格：尋找無巢狀子 table 且列數 > 10 的內層數據表格
    target_table = None
    for t in soup.find_all('table'):
        if t.find('table') is None and len(t.find_all('tr')) > 10:
            target_table = t
            break
            
    if not target_table:
        print("找不到符合的營收表格")
        return {"status": "error", "message": "找不到符合的營收表格", "count": 0}
        
    rows = target_table.find_all('tr')
    
    # 連線資料庫，確保唯一索引存在
    conn = duckdb.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_revenues_stock_date ON revenues(stock_id, date);")
    
    upsert_count = 0
    parsed_samples = []
    
    # 從第 2 列開始 (第 0 列是標題與日期，第 1 列是欄位名稱)
    for r in rows[2:]:
        cols = r.find_all('td')
        if len(cols) < 5:
            continue
            
        td0_str = str(cols[0])
        match = re.search(r"GenLink2stk\('([A-Za-z0-9]+)','([^']+)'\)", td0_str)
        if match:
            raw_code = match.group(1)
            name = match.group(2)
        else:
            code_match = re.search(r"'(?:AS)?([0-9]{4,6})'", td0_str)
            if not code_match:
                continue
            raw_code = code_match.group(1)
            name = cols[0].text.strip()
            
        # 去除非數字前綴 (如 AS1414 -> 1414)
        stock_id = re.sub(r'^[A-Za-z]+', '', raw_code)
        if not stock_id.isdigit():
            continue
            
        # 欄位解析:
        # cols[1]: 營收(千)
        # cols[2]: 年增率 (YoY %)
        # cols[3]: 比上月(%) (MoM %)
        rev_str = cols[1].text.replace('\xa0', '').strip()
        yoy_str = cols[2].text.replace('\xa0', '').strip()
        mom_str = cols[3].text.replace('\xa0', '').strip()
        
        rev_k = safe_float(rev_str)
        # 嚴格正則化：千元轉為元 (乘上 1000)
        revenue_ntd = (rev_k * 1000.0) if rev_k is not None else 0.0
        
        yoy_pct = safe_float(yoy_str)
        mom_pct = safe_float(mom_str)
        
        cursor.execute("""
            INSERT INTO revenues (stock_id, date, revenue, revenue_yoy_pct, revenue_mom_pct)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(stock_id, date) DO UPDATE SET
                revenue = excluded.revenue,
                revenue_yoy_pct = excluded.revenue_yoy_pct,
                revenue_mom_pct = excluded.revenue_mom_pct
        """, (stock_id, target_date, revenue_ntd, yoy_pct, mom_pct))
        
        upsert_count += 1
        if len(parsed_samples) < 5:
            parsed_samples.append({
                "stock_id": stock_id,
                "name": name,
                "revenue": revenue_ntd,
                "yoy": yoy_pct,
                "mom": mom_pct
            })
            
    conn.commit()
    conn.close()
    
    print(f"MoneyDJ 今日營收快報爬取完成！成功更新/寫入 {upsert_count} 筆記錄 (目標歸屬月: {target_date})")
    return {
        "status": "success",
        "count": upsert_count,
        "target_date": target_date,
        "samples": parsed_samples
    }

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', type=str, default=None, help='Target publish date (e.g., 2026-09-10)')
    args = parser.parse_args()
    
    res = scrape_moneydj_revenue(args.date)
    print(res)
