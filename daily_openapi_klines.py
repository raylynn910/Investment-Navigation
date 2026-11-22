import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import requests
import duckdb
import datetime
import urllib3
import os

urllib3.disable_warnings()

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "market_data.duckdb")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

def safe_float(val):
    if not val: return None
    val = str(val).replace(',', '').strip()
    if val in ['--', '-', 'X', '']: return None
    try:
        return float(val)
    except ValueError:
        return None

def parse_roc_date(roc_str):
    """
    將民國年日期字串 (如 '1150807' 或 '115/08/07') 轉為西元日期字串 'YYYY-MM-DD'。
    回傳 None 表示解析失敗。
    """
    if not roc_str:
        return None
    roc_str = str(roc_str).replace('/', '').strip()
    if len(roc_str) == 7:
        try:
            year = int(roc_str[:3]) + 1911
            month = int(roc_str[3:5])
            day = int(roc_str[5:7])
            return f"{year:04d}-{month:02d}-{day:02d}"
        except (ValueError, IndexError):
            return None
    return None

def fetch_openapi_klines(target_date: str = None, run_adjuster: bool = True):
    print("=" * 60)
    print("🚀 啟動每日收盤 K 線全量更新 (純 OpenAPI + TWSE 雙軌容錯)")
    print("=" * 60)

    from db_utils import safe_duckdb_connect
    from tools.market_calendar import is_trading_day, get_last_trading_day

    now_tw = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    today_str = now_tw.strftime('%Y-%m-%d')
    if target_date:
        expected_date = target_date
    elif is_trading_day(today_str) and (now_tw.hour < 14 or (now_tw.hour == 14 and now_tw.minute < 30)):
        yesterday_str = (now_tw - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        expected_date = get_last_trading_day(yesterday_str)
    else:
        expected_date = get_last_trading_day(today_str)

    if not is_trading_day(today_str):
        print(f"ℹ️ 今日 ({today_str}) 為休市日，改以最近有效交易日 ({expected_date}) 補齊官方 OpenAPI 收盤 K 線...")

    upsert_count = 0
    twse_count = 0
    tpex_count = 0
    twse_date = None
    tpex_date = None
    twse_success = False
    records_to_upsert = []

    # 1. TWSE (上市) - 雙軌機制：優先檢查 OpenAPI，若快取滯後於 expected_date 自動切換至 MI_INDEX
    print(f"[1/2] 下載上市 K 線資料 (TWSE，目標交易日: {expected_date})...")
    twse_url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    use_mi_index = False

    try:
        res = requests.get(twse_url, headers=HEADERS, verify=False, timeout=30)
        if res.status_code == 200:
            data = res.json()
            if data and len(data) > 0:
                sample_date = parse_roc_date(data[0].get("Date"))
                if sample_date and sample_date < expected_date:
                    print(f"      TWSE OpenAPI 資料日期為 {sample_date} (落後於目標 {expected_date})，自動切換至 MI_INDEX 即時端點...")
                    use_mi_index = True
                else:
                    for r in data:
                        stock_id = str(r.get("Code", "")).strip()
                        if not stock_id or len(stock_id) != 4 or not stock_id.isdigit():
                            continue

                        trade_date = parse_roc_date(r.get("Date"))
                        if trade_date is None:
                            continue
                        if twse_date is None:
                            twse_date = trade_date

                        vol = safe_float(r.get("TradeVolume"))
                        open_p = safe_float(r.get("OpeningPrice"))
                        high_p = safe_float(r.get("HighestPrice"))
                        low_p = safe_float(r.get("LowestPrice"))
                        close_p = safe_float(r.get("ClosingPrice"))

                        if close_p is not None and close_p > 0:
                            records_to_upsert.append((stock_id, trade_date, open_p, high_p, low_p, close_p, vol))
                            twse_count += 1
                    twse_success = twse_count > 0
                    print(f"      TWSE 交易日期 (OpenAPI): {twse_date} ({twse_count} 檔)")
        else:
            print(f"      TWSE OpenAPI 失敗，狀態碼: {res.status_code}，切換 MI_INDEX...")
            use_mi_index = True
    except Exception as e:
        print(f"      TWSE OpenAPI 連線錯誤: {e}，切換 MI_INDEX...")
        use_mi_index = True

    # MI_INDEX 備援線路
    if use_mi_index or not twse_success:
        try:
            mi_url = "https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&type=ALLBUT0999"
            mi_res = requests.get(mi_url, headers=HEADERS, verify=False, timeout=20)
            if mi_res.status_code == 200:
                mi_json = mi_res.json()
                raw_d = str(mi_json.get("date", ""))
                trade_date = f"{raw_d[:4]}-{raw_d[4:6]}-{raw_d[6:8]}" if len(raw_d) == 8 else expected_date
                twse_date = trade_date

                data_table = None
                for tbl in mi_json.get("tables", []):
                    title = tbl.get("title", "")
                    if "每日收盤行情" in title or "收盤行情" in title:
                        data_table = tbl.get("data", [])
                        break

                if not data_table and "data9" in mi_json:
                    data_table = mi_json["data9"]

                if data_table:
                    mi_rows = []
                    for row in data_table:
                        stock_id = str(row[0]).strip()
                        if not stock_id or len(stock_id) != 4 or not stock_id.isdigit():
                            continue
                        vol = safe_float(row[2])
                        open_p = safe_float(row[5])
                        high_p = safe_float(row[6])
                        low_p = safe_float(row[7])
                        close_p = safe_float(row[8])

                        if close_p is not None and close_p > 0:
                            mi_rows.append((stock_id, trade_date, open_p, high_p, low_p, close_p, vol))
                    if mi_rows:
                        records_to_upsert.extend(mi_rows)
                        twse_count += len(mi_rows)
                        print(f"      TWSE 交易日期 (MI_INDEX 即時): {twse_date} ({len(mi_rows)} 檔)")
                        twse_success = True
                else:
                    print("      TWSE MI_INDEX 未找到收盤行情資料表")
        except Exception as mi_err:
            print(f"      TWSE MI_INDEX 備援連線失敗: {mi_err}")

    # 2. TPEx (上櫃)
    print(f"[2/2] 下載上櫃 K 線資料 (tpex_mainboard_quotes，目標交易日: {expected_date})...")
    tpex_url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
    for attempt in range(2):
        try:
            res = requests.get(tpex_url, headers=HEADERS, verify=False, timeout=30)
            if res.status_code == 200:
                data = res.json()
                for r in data:
                    stock_id = str(r.get("SecuritiesCompanyCode", "")).strip()
                    if not stock_id or len(stock_id) != 4 or not stock_id.isdigit():
                        continue

                    trade_date = parse_roc_date(r.get("Date"))
                    if trade_date is None:
                        continue
                    if tpex_date is None:
                        tpex_date = trade_date

                    vol = safe_float(r.get("TradingShares"))
                    open_p = safe_float(r.get("Open"))
                    high_p = safe_float(r.get("High"))
                    low_p = safe_float(r.get("Low"))
                    close_p = safe_float(r.get("Close"))

                    if close_p is not None and close_p > 0:
                        records_to_upsert.append((stock_id, trade_date, open_p, high_p, low_p, close_p, vol))
                        tpex_count += 1
                print(f"      TPEx 交易日期: {tpex_date} ({tpex_count} 檔)")
                break
            else:
                print(f"TPEx API 失敗，狀態碼: {res.status_code}")
        except Exception as e:
            if attempt == 1:
                print(f"TPEx API 連線錯誤: {e}")

    if not records_to_upsert:
        return {
            "status": "error",
            "message": f"TWSE/TPEx OpenAPI 均未取得有效 K 線資料 (目標日: {expected_date})",
            "updated_records": 0,
            "twse_date": twse_date,
            "tpex_date": tpex_date
        }

    # 先網路抓完再瞬間開啟 DuckDB 寫入連線，將檔案鎖佔用時間壓至 <1 秒
    import pandas as pd
    df_upsert = pd.DataFrame(
        records_to_upsert,
        columns=["stock_id", "date", "open", "high", "low", "close", "volume"]
    ).drop_duplicates(subset=["stock_id", "date"], keep="last")

    conn = safe_duckdb_connect(DB_PATH, read_only=False)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS klines (
                stock_id TEXT,
                date DATE,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                PRIMARY KEY (stock_id, date)
            )
        """)
        conn.register("df_kline_upsert", df_upsert)
        conn.execute("""
            INSERT INTO klines (stock_id, date, open, high, low, close, volume)
            SELECT stock_id, CAST(date AS DATE), open, high, low, close, volume
            FROM df_kline_upsert
            ON CONFLICT(stock_id, date) DO UPDATE SET
                open=excluded.open, high=excluded.high, low=excluded.low,
                close=excluded.close, volume=excluded.volume
        """)
        conn.commit()
        upsert_count = len(df_upsert)
        print(f"[DB Update Log] {datetime.datetime.now():%Y-%m-%d %H:%M:%S} klines UPSERT {upsert_count} 筆 (TWSE={twse_date}/{twse_count}檔, TPEx={tpex_date}/{tpex_count}檔)")
    finally:
        conn.close()

    # 3. 自動執行除權息/減資向後還原校正
    if run_adjuster:
        try:
            from data_providers.price_adjuster import auto_adjust_historical_prices
            print("[Auto-Adjuster] 正在執行除權息與減資跳空還原校正...")
            auto_adjust_historical_prices()
        except Exception as adj_e:
            print(f"[Auto-Adjuster] 校正警示: {adj_e}")

    return {
        "status": "success",
        "updated_records": upsert_count,
        "twse_date": twse_date,
        "tpex_date": tpex_date,
        "expected_date": expected_date,
    }

if __name__ == "__main__":
    fetch_openapi_klines()

