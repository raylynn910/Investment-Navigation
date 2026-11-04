import requests
import duckdb
import urllib3
import os
from datetime import datetime

urllib3.disable_warnings()

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "market_data.duckdb")

def fetch_and_update_openapi_revenue():
    """
    從 TWSE 與 TPEx 官方 OpenAPI 下載全量上市櫃最新營業收入資訊，
    清洗後寫入 DuckDB revenues 表 (千元轉元，單位正則化)。
    """
    print("Fetching TWSE...")
    res1 = requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap05_L", verify=False, timeout=20)
    twse_data = res1.json() if res1.status_code == 200 else []

    print("Fetching TPEx...")
    res2 = requests.get("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O", verify=False, timeout=20)
    tpex_data = res2.json() if res2.status_code == 200 else []

    all_data = twse_data + tpex_data
    print(f"Total records fetched: {len(all_data)}")

    if not all_data:
        return {"status": "error", "message": "未能取得 TWSE/TPEx OpenAPI 資料", "count": 0}

    conn = duckdb.connect(DB_PATH)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_revenues_stock_date ON revenues(stock_id, date);")

    count = 0
    sample_date = None
    for row in all_data:
        try:
            keys = list(row.keys())
            date_str = str(row[keys[1]]).strip()
            if len(date_str) == 5:
                rev_year = int(date_str[:3]) + 1911
                rev_month = int(date_str[3:])
                # 對齊全系統發布日標籤規範：M 月份營收於 M+1 月 10 日前發布 (例如 11509 九月營收 -> 2026-10-10)
                if rev_month == 12:
                    target_date = f"{rev_year + 1}-01-10"
                else:
                    target_date = f"{rev_year}-{rev_month + 1:02d}-10"
                if not sample_date:
                    sample_date = f"{target_date} ({rev_year}年{rev_month}月營收)"
            else:
                continue

            stock_id = str(row[keys[2]]).strip()

            def _to_ntd(val):
                s = str(val).replace(",", "").strip()
                return float(s) * 1000.0 if s and s not in ("None", "-", "") else None

            revenue = _to_ntd(row[keys[5]]) or 0.0
            rev_last_month = _to_ntd(row[keys[6]]) if len(keys) > 6 else None
            rev_last_year = _to_ntd(row[keys[7]]) if len(keys) > 7 else None
            cum_rev = _to_ntd(row[keys[10]]) if len(keys) > 10 else 0.0

            mom_str = str(row[keys[8]]).replace(",", "").strip()
            try:
                mom_pct = float(mom_str) if mom_str and mom_str not in ("None", "-", "") else None
            except Exception:
                mom_pct = None

            yoy_str = str(row[keys[9]]).replace(",", "").strip()
            try:
                yoy_pct = float(yoy_str) if yoy_str and yoy_str not in ("None", "-", "") else None
            except Exception:
                yoy_pct = None

            conn.execute("""
                INSERT INTO revenues (
                    stock_id, date, revenue, revenue_last_month, revenue_last_year,
                    revenue_mom_pct, revenue_yoy_pct, cumulative_revenue
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(stock_id, date) DO UPDATE SET
                    revenue = excluded.revenue,
                    revenue_last_month = COALESCE(excluded.revenue_last_month, revenues.revenue_last_month),
                    revenue_last_year = COALESCE(excluded.revenue_last_year, revenues.revenue_last_year),
                    revenue_yoy_pct = excluded.revenue_yoy_pct,
                    revenue_mom_pct = excluded.revenue_mom_pct,
                    cumulative_revenue = COALESCE(excluded.cumulative_revenue, revenues.cumulative_revenue)
            """, (stock_id, target_date, revenue, rev_last_month, rev_last_year, mom_pct, yoy_pct, cum_rev))
            count += 1
        except Exception as e:
            continue

    conn.commit()
    conn.close()
    msg = f"官方 OpenAPI 全量營收同步成功！共更新 {count} 筆記錄 (資料月份: {sample_date})。"
    print(msg)
    return {"status": "success", "message": msg, "count": count, "date": sample_date}

if __name__ == '__main__':
    result = fetch_and_update_openapi_revenue()
    print(result)
