import os
import sys
import requests
import pandas as pd
import duckdb
from datetime import datetime
import time
import logging

logger = logging.getLogger(__name__)
LAST_TXF_SYNC_RESULT = {}

def get_tw_date(d_str):
    # '115/08/03' -> '2026-08-03'
    parts = d_str.split('/')
    if len(parts) == 3:
        return f"{int(parts[0])+1911}-{parts[1]}-{parts[2]}"
    return d_str

def fetch_fmtqik_month(date_str=""):
    url = "https://www.twse.com.tw/exchangeReport/FMTQIK?response=json"
    if date_str:
        url += f"&date={date_str}"
    
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.get(url, headers=headers, verify=False, timeout=15)
        data = resp.json()
        if data.get('stat') == 'OK':
            return data.get('data', [])
    except Exception as e:
        logger.error(f"FMTQIK fetch error: {e}")
    return []

def fetch_taiex_twse_recent():
    logger.info("Fetching TAIEX from TWSE FMTQIK (Recent 2 months)...")
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    records = []
    # Current month
    now = datetime.now()
    records.extend(fetch_fmtqik_month(now.strftime("%Y%m01")))
    
    # Previous month
    if now.month == 1:
        prev_date = f"{now.year-1}1201"
    else:
        prev_date = f"{now.year}{now.month-1:02d}01"
    
    time.sleep(1) # Be nice to TWSE
    records.extend(fetch_fmtqik_month(prev_date))
    
    df_records = []
    for row in records:
        try:
            date_str = get_tw_date(row[0])
            volume = int(row[1].replace(',', ''))
            close_p = float(row[4].replace(',', ''))
            
            df_records.append({
                'stock_id': 'TAIEX',
                'date': date_str,
                'open': close_p,
                'high': close_p,
                'low': close_p,
                'close': close_p,
                'volume': volume
            })
        except Exception as e:
            continue
            
    return pd.DataFrame(df_records)

def fetch_taiex_finmind(start_date="2019-01-01"):
    logger.info(f"Fetching TAIEX from FinMind (Start: {start_date})...")
    url = "https://api.finmindtrade.com/api/v4/data"
    token = os.environ.get("FINMIND_TOKEN", "")
    
    params = {
        "dataset": "TaiwanStockPrice",
        "data_id": "TAIEX",
        "start_date": start_date,
        "end_date": datetime.today().strftime('%Y-%m-%d'),
    }
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    
    try:
        resp = requests.get(url, params=params, headers=headers)
        data = resp.json()
        if data.get("status") != 200:
            logger.error(f"Error fetching TAIEX: {data.get('msg')}")
            return pd.DataFrame()
            
        df = pd.DataFrame(data.get("data", []))
        if df.empty:
            return df
            
        return pd.DataFrame({
            'stock_id': 'TAIEX',
            'date': pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d'),
            'open': df['open'],
            'high': df['max'],
            'low': df['min'],
            'close': df['close'],
            'volume': df['Trading_Volume']
        })
    except Exception as e:
        logger.error(f"FinMind error: {e}")
        return pd.DataFrame()

def fetch_and_update_taiex():
    from db_utils import safe_duckdb_connect
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'market_data.duckdb')
    conn = safe_duckdb_connect(db_path, read_only=False)
    
    # Check if we need full history
    try:
        count = conn.execute("SELECT COUNT(*) FROM klines WHERE stock_id = 'TAIEX'").fetchone()[0]
    except Exception:
        count = 0
        
    df_klines = pd.DataFrame()
    
    if count < 240:
        logger.info("Not enough TAIEX history in DB, trying FinMind full history...")
        df_klines = fetch_taiex_finmind("2019-01-01")
        
    # If FinMind failed or we only need recent updates, use TWSE
    if df_klines.empty:
        df_klines = fetch_taiex_twse_recent()
        
    if df_klines.empty:
        logger.error("Failed to fetch TAIEX data from all sources.")
        # TAIEX 失敗不可連帶跳過台指期日夜盤 (舊版在此直接 return，夜盤永遠不會同步)
        try:
            LAST_TXF_SYNC_RESULT.clear()
            LAST_TXF_SYNC_RESULT.update(sync_txf_recent_if_needed(conn))
        except Exception as e:
            logger.error(f"[TXF Sync] 例外: {e}")
        conn.close()
        return False
        
    logger.info(f"Fetched {len(df_klines)} rows for TAIEX. Upserting to DB...")
    
    try:
        conn.execute("CREATE TEMP TABLE temp_taiex AS SELECT * FROM df_klines")
        conn.execute("""
            INSERT INTO klines (stock_id, date, open, high, low, close, volume)
            SELECT stock_id, CAST(date AS DATE), open, high, low, close, volume 
            FROM temp_taiex
            ON CONFLICT(stock_id, date) 
            DO UPDATE SET 
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume;
        """)
        logger.info("Successfully upserted TAIEX data into DuckDB!")
        
        # 重新從資料庫讀取完整的 TAIEX 歷史，計算並更新 HV_20
        import numpy as np
        df_all = conn.execute("SELECT date, close FROM klines WHERE stock_id = 'TAIEX' ORDER BY date").fetchdf()
        df_all['daily_return'] = df_all['close'].pct_change()
        df_all['hv_20'] = df_all['daily_return'].rolling(20).std() * np.sqrt(252) * 100
        
        df_hv = df_all.dropna(subset=['hv_20'])[['date', 'hv_20']]
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS temp_hv AS SELECT * FROM df_hv")
        conn.execute("DELETE FROM temp_hv") # clear in case of reuse
        conn.execute("INSERT INTO temp_hv SELECT * FROM df_hv")
        
        conn.execute("""
            UPDATE klines 
            SET hv_20 = temp_hv.hv_20 
            FROM temp_hv 
            WHERE klines.stock_id = 'TAIEX' AND klines.date = temp_hv.date
        """)
        logger.info(f"Successfully calculated and updated HV_20 for {len(df_hv)} days.")
    except Exception as e:
        logger.error(f"Database error during TAIEX upsert: {e}")

    # 同步 TXF 與 TXF_NIGHT，確保 N7 守護者四大面板與大盤同步更新 (獨立於 TAIEX 成敗)
    try:
        LAST_TXF_SYNC_RESULT.clear()
        LAST_TXF_SYNC_RESULT.update(sync_txf_recent_if_needed(conn))
    except Exception as e:
        logger.error(f"[TXF Sync] 例外: {e}")
    finally:
        conn.close()
        
    return True

def sync_txf_recent_if_needed(conn, lookback_days: int = 20) -> dict:
    """
    確保 TXF (日盤) 與 TXF_NIGHT (夜盤) 同步更新，避免 N7 守護者計算時產生斷層。

    日期標籤慣例 (已用 FinMind 與期交所 OpenAPI 實測比對 2026-11-03 夜盤 O45227/C44310 一致)：
      夜盤 (after_market / 盤後) 以「所歸屬之次一交易日」為 date，
      例如 11/04 15:00 ~ 11/05 05:00 之夜盤 -> date = 2026-11-05。

    來源順序：
      1. FinMind TaiwanFuturesDaily (4 組 Token 輪替，偵測 402 額度 / 403 IP 封鎖)
      2. 期交所 TAIFEX OpenAPI DailyMarketReportFut (官方備援)
    回傳 {"source", "rows", "txf_max", "night_max", "errors"}，失敗不再靜默吞掉。
    """
    from datetime import timedelta
    result = {"source": None, "rows": 0, "txf_max": None, "night_max": None, "errors": []}
    now = datetime.now()
    start_date = (now - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    end_date = now.strftime("%Y-%m-%d")

    try:
        from fetch_txf_klines import FINMIND_TOKENS, _extract_front_month
    except Exception as e:
        result["errors"].append(f"載入 fetch_txf_klines 失敗: {e}")
        return result

    tokens = []
    env_tok = os.environ.get("FINMIND_TOKEN", "").strip().strip('"').strip("'")
    if env_tok:
        tokens.append(env_tok)
    tokens.extend([t for t in FINMIND_TOKENS if t not in tokens])

    df_comb = pd.DataFrame()

    # ---------- 來源 1：FinMind ----------
    for idx, tok in enumerate(tokens):
        try:
            resp = requests.get(
                "https://api.finmindtrade.com/api/v4/data",
                params={"dataset": "TaiwanFuturesDaily", "data_id": "TX",
                        "start_date": start_date, "end_date": end_date},
                headers={"Authorization": f"Bearer {tok}"}, timeout=15,
            )
            if resp.status_code == 403 or "ip banned" in resp.text.lower():
                result["errors"].append("FinMind 403 ip banned (同 IP 已被封鎖，停止輪替)")
                break
            if resp.status_code == 402:
                result["errors"].append(f"FinMind token#{idx} 402 額度用盡")
                continue
            data = resp.json()
            if data.get("status") != 200:
                result["errors"].append(f"FinMind token#{idx} status={data.get('status')} msg={data.get('msg')}")
                continue
            rows = data.get("data") or []
            if not rows:
                result["errors"].append(f"FinMind token#{idx} 回傳 0 筆")
                break
            df_sub = pd.DataFrame(rows)
            df_day = _extract_front_month(df_sub[df_sub["trading_session"] == "position"], "TXF")
            df_night = _extract_front_month(df_sub[df_sub["trading_session"] == "after_market"], "TXF_NIGHT")
            df_comb = pd.concat([df_day, df_night], ignore_index=True)
            result["source"] = "FinMind TaiwanFuturesDaily"
            break
        except Exception as e:
            result["errors"].append(f"FinMind token#{idx} 例外: {e}")

    # ---------- 來源 2：期交所官方 OpenAPI 備援 ----------
    if df_comb.empty:
        try:
            resp = requests.get(
                "https://openapi.taifex.com.tw/v1/DailyMarketReportFut",
                headers={"User-Agent": "Mozilla/5.0", "accept": "application/json"},
                timeout=30, verify=False,
            )
            js = resp.json()
            recs = []
            for d in js:
                if str(d.get("Contract", "")).strip() != "TX":
                    continue
                session = str(d.get("TradingSession", "")).strip()
                sid = "TXF" if session == "一般" else ("TXF_NIGHT" if session == "盤後" else None)
                if sid is None:
                    continue
                try:
                    recs.append({
                        "date": datetime.strptime(str(d["Date"]).strip(), "%Y%m%d").strftime("%Y-%m-%d"),
                        "stock_id": sid,
                        "contract_date": str(d.get("ContractMonth(Week)", "")).strip(),
                        "open": float(d["Open"]), "high": float(d["High"]),
                        "low": float(d["Low"]), "close": float(d["Last"]),
                        "volume": float(d["Volume"]),
                    })
                except (ValueError, TypeError, KeyError):
                    continue  # '-' 等無成交欄位直接略過，不補值
            if recs:
                df_t = pd.DataFrame(recs)
                df_t = df_t[df_t["contract_date"].str.match(r"^\d{6}$") & (df_t["close"] > 0)]
                idx_max = df_t.groupby(["date", "stock_id"])["volume"].idxmax()
                df_comb = df_t.loc[idx_max, ["date", "stock_id", "open", "high", "low", "close", "volume"]].reset_index(drop=True)
                result["source"] = "TAIFEX OpenAPI DailyMarketReportFut"
            else:
                result["errors"].append("TAIFEX OpenAPI 無 TX 有效資料")
        except Exception as e:
            result["errors"].append(f"TAIFEX OpenAPI 例外: {e}")

    if df_comb.empty:
        logger.error(f"[TXF Sync] 所有來源皆失敗，保留既有資料庫快照: {result['errors']}")
        return result

    conn.execute("CREATE TEMP TABLE IF NOT EXISTS temp_txf_sync AS SELECT * FROM df_comb WHERE 1=0")
    conn.execute("DELETE FROM temp_txf_sync")
    conn.execute("INSERT INTO temp_txf_sync SELECT * FROM df_comb")
    conn.execute("""
        INSERT INTO klines (stock_id, date, open, high, low, close, volume)
        SELECT stock_id, CAST(date AS DATE), open, high, low, close, volume
        FROM temp_txf_sync
        ON CONFLICT (stock_id, date) DO UPDATE SET
            open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close, volume = EXCLUDED.volume;
    """)
    result["rows"] = int(len(df_comb))
    result["txf_max"] = str(conn.execute("SELECT MAX(date) FROM klines WHERE stock_id='TXF'").fetchone()[0])
    result["night_max"] = str(conn.execute("SELECT MAX(date) FROM klines WHERE stock_id='TXF_NIGHT'").fetchone()[0])
    logger.info(
        f"[DB Update Log] {datetime.now():%Y-%m-%d %H:%M:%S} klines UPSERT TXF/TXF_NIGHT {result['rows']} 筆 "
        f"(來源: {result['source']}, 區間 {start_date}~{end_date}) -> TXF 最新 {result['txf_max']} / TXF_NIGHT 最新 {result['night_max']}"
    )
    return result

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    fetch_and_update_taiex()
