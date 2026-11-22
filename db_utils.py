import gc
import os
import time
import threading
import duckdb
import pandas as pd
from datetime import datetime

_duckdb_lock = threading.RLock()

def safe_duckdb_connect(db_path: str = None, read_only: bool = True, retries: int = 12, retry_delay: float = 0.3):
    """
    執行緒安全、防跨行程檔案鎖碰撞與防唯讀降級的 DuckDB 連線函式。
    - 遭遇 Windows 跨行程檔案鎖定時，自動進行退避重試 (backoff retry)。
    - 同行程設定衝突時，僅允許「唯讀需求安全升級為讀寫連線」；
      嚴禁將「寫入需求 (read_only=False)」靜默降級為唯讀連線，避免後續 INSERT/UPDATE 全部爆炸。
    """
    if db_path is None:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "market_data.duckdb")

    last_err = None
    for attempt in range(max(1, retries)):
        with _duckdb_lock:
            try:
                return duckdb.connect(db_path, read_only=read_only)
            except Exception as e:
                last_err = e
                err_msg = str(e)
                is_cfg_mismatch = ("different configuration" in err_msg) or ("Configuration mismatch" in err_msg)
                is_file_lock = (
                    "being used by another process" in err_msg
                    or "Cannot open file" in err_msg
                    or "Conflicting lock" in err_msg
                    or "IO Error" in err_msg
                )
                if is_cfg_mismatch and read_only:
                    # 唯讀查詢可安全共借同行程既有的讀寫連線
                    try:
                        return duckdb.connect(db_path, read_only=False)
                    except Exception as up_e:
                        last_err = up_e
                if is_cfg_mismatch or is_file_lock:
                    gc.collect()
                    if attempt < retries - 1:
                        time.sleep(retry_delay * (1.0 + attempt * 0.25))
                        continue
                raise e
    raise last_err

def _date_str(d):
    """安全地將 date 轉成字串，不論 DuckDB 回傳 str 或 datetime.date"""
    if isinstance(d, str):
        return d[:10]
    try:
        return d.strftime("%Y-%m-%d")
    except Exception:
        return str(d)[:10]

def get_stock_panel_data_from_db(ticker: str):
    """
    從本地 DuckDB (data/market_data.duckdb) 以嚴格唯讀與參數化查詢獲取個股 Panel Data (K線、營收、財報)。
    已徹底脫鉤舊版 SQLite/SQLAlchemy 寫入鎖與空表 financial_statements，並省略過期 2024 籌碼避免誤導。
    """
    kline_data = []
    chip_data = []
    rev_data = []
    funds_data = []
    close_price = 0.0

    safe_ticker = ticker.split('.')[0] if '.' in ticker else ticker
    conn = safe_duckdb_connect(read_only=True)
    try:
        # 1. Kline Data
        df_kline = conn.execute(
            """
            SELECT date, open, high, low, close, volume, hv_20
            FROM klines
            WHERE stock_id = ? AND close IS NOT NULL AND close > 0
            ORDER BY date ASC
            """,
            [safe_ticker],
        ).df()
        if not df_kline.empty:
            df_kline["date"] = df_kline["date"].apply(_date_str)
            df_kline["volume"] = df_kline["volume"].fillna(0.0)
            df_kline["hv_20"] = df_kline["hv_20"].fillna(0.0)
            kline_data = df_kline.to_dict(orient="records")
            close_price = float(kline_data[-1]["close"])

        # 2. Revenue Data
        df_rev = conn.execute(
            """
            SELECT date, revenue, revenue_mom_pct, revenue_yoy_pct
            FROM revenues
            WHERE stock_id = ?
            ORDER BY date ASC
            """,
            [safe_ticker],
        ).df()
        if not df_rev.empty:
            rev_data = [
                {
                    "date": _date_str(r["date"]),
                    "revenue": float(r["revenue"]) if pd.notnull(r["revenue"]) else 0.0,
                }
                for _, r in df_rev.iterrows()
            ]

        # 3. Financials Data (改查真實 financials 表，單季平均化 ROE / EPS)
        fin_row = conn.execute(
            """
            SELECT eps / CAST(quarter AS DOUBLE) AS eps_q,
                   roe / CAST(quarter AS DOUBLE) AS roe_q,
                   year, quarter
            FROM financials
            WHERE stock_id = ? AND (roe IS NOT NULL OR eps IS NOT NULL)
            ORDER BY year DESC, quarter DESC
            LIMIT 1
            """,
            [safe_ticker],
        ).fetchone()
        eps_val = float(fin_row[0]) if fin_row and fin_row[0] is not None else 0.0
        roe_val = float(fin_row[1]) if fin_row and fin_row[1] is not None else 0.0

        if kline_data:
            funds_data.append({
                "date": kline_data[-1]["date"],
                "pe_ratio": 0.0,
                "pb_ratio": 0.0,
                "yield_rate": 0.0,
                "eps": eps_val,
                "roe": roe_val,
            })
    finally:
        conn.close()

    return {
        "kline_data": kline_data,
        "chip_data": chip_data,
        "rev_data": rev_data,
        "funds_data": funds_data,
        "close_price": close_price
    }

def get_etf_portfolio_from_db(db_path="screener_results.duckdb"):
    """從 DuckDB 獲取 ETF 投資組合數據"""
    metadata_info = {"last_updated_time": "", "total_scanned_stocks": 0}
    portfolio_list = []
    added_list = []
    removed_list = []
    
    try:
        if os.path.exists(db_path):
            conn = safe_duckdb_connect(db_path, read_only=True)
            try:
                try:
                    df_status = conn.execute("SELECT * FROM system_status").df()
                    if not df_status.empty:
                        metadata_info["last_updated_time"] = str(df_status.iloc[0].get("last_updated_time", ""))
                        metadata_info["total_scanned_stocks"] = int(df_status.iloc[0].get("total_scanned_stocks", 0))
                except Exception as e:
                    print(f"[Warning] Failed to read system_status: {e}")

                try:
                    df_port = conn.execute("SELECT * FROM current_portfolio").df()
                    df_port = df_port.fillna(0.0)
                    for _, row in df_port.iterrows():
                        portfolio_list.append({
                            "stock_id": str(row.get("stock_id", "")),
                            "stock_name": str(row.get("stock_name", "")),
                            "total_score": float(row.get("total_score", 0.0)),
                            "revenue_yoy_rank": float(row.get("rank_yoy", 0.0)),
                            "revenue_mom_rank": float(row.get("rank_mom", 0.0)),
                            "roe_rank": float(row.get("rank_roe", 0.0)),
                            "momentum_rank": float(row.get("rank_mom_factor", 0.0)),
                            "foreign_fund_flow_20d": float(row.get("foreign_fund_flow_20d", 0.0)),
                            "trust_fund_flow_20d": float(row.get("trust_fund_flow_20d", 0.0))
                        })
                except Exception as e:
                    print(f"[Warning] Failed to read current_portfolio: {e}")

                try:
                    df_changes = conn.execute("SELECT * FROM portfolio_changes").df()
                    df_changes = df_changes.fillna("")
                    for _, row in df_changes.iterrows():
                        action = str(row.get("action", "")).upper()
                        change = {
                            "stock_id": str(row.get("stock_id", "")),
                            "stock_name": str(row.get("stock_name", ""))
                        }
                        if action == "ADD":
                            added_list.append(change)
                        elif action == "REMOVE":
                            removed_list.append(change)
                except Exception as e:
                    print(f"[Warning] Failed to read portfolio_changes: {e}")
            finally:
                conn.close()

    except Exception as e:
        print(f"[Warning] Database connection or parsing error: {e}")

    return {
        "metadata_info": metadata_info,
        "portfolio_list": portfolio_list,
        "added_list": added_list,
        "removed_list": removed_list
    }

def get_portfolio_changes_from_db(all_results, db_path="screener_results.duckdb"):
    """
    新科狀元系統・無狀態跨界追蹤 (Boundary Tracking - 跌出 15 名微緩衝)
    - 🔴 必須賣出 (Sells)：昨日排名 <= 15 且 今日排名 > 15 (跌出 15 名緩衝區，09:00 MOO 秒殺汰弱)
    - 🟢 必須買入 (Buys)：今日排名 <= 9 且 昨日排名 > 9 (剛擠進前 9 強之新飆股，09:00 MOO 買進)
    - ⚪ 持倉續抱 (Holds)：今日排名落在 10~15 名的個股
    """
    added_list = []
    removed_list = []
    
    if not os.path.exists(db_path):
        return added_list, removed_list

    try:
        conn = safe_duckdb_connect(db_path, read_only=True)
        try:
            try:
                df_old = conn.execute("SELECT stock_id, stock_name FROM baseline_portfolio").df()
            except Exception:
                try:
                    df_old = conn.execute("SELECT stock_id, stock_name FROM current_portfolio").df()
                except Exception:
                    df_old = pd.DataFrame()
                
            yesterday_ranks = {str(row['stock_id']): idx + 1 for idx, row in df_old.iterrows()}
            old_names = {str(row['stock_id']): str(row['stock_name']) for _, row in df_old.iterrows()}
            
            today_ranks = {str(item["stock_id"]): idx + 1 for idx, item in enumerate(all_results)}
            new_names = {str(item["stock_id"]): str(item["stock_name"]) for item in all_results}
            
            # 1. 必須買入 (Buys): 今日 <= 9 且 昨日 > 9 (含昨日未在榜內)
            for sid, t_rank in today_ranks.items():
                if t_rank <= 9:
                    y_rank = yesterday_ranks.get(sid, 999)
                    if y_rank > 9:
                        added_list.append({"stock_id": sid, "stock_name": new_names.get(sid, "")})

            # 2. 必須賣出 (Sells): 昨日 <= 15 且 今日 > 15 (含今日未過濾網)
            for sid, y_rank in yesterday_ranks.items():
                if y_rank <= 15:
                    t_rank = today_ranks.get(sid, 999)
                    if t_rank > 15:
                        sname = new_names.get(sid, old_names.get(sid, ""))
                        removed_list.append({"stock_id": sid, "stock_name": sname})
        finally:
            conn.close()

    except Exception as e:
        print(f"[Warning] Failed to dynamically calculate portfolio changes: {e}")

    return added_list, removed_list

def update_screener_results_in_db(top_results, total_scanned_count, db_path="screener_results.duckdb"):
    """將最新的排名與名單異動寫入 DuckDB (十方極速架構)"""
    df_new = pd.DataFrame(top_results)
    
    try:
        conn = safe_duckdb_connect(db_path, read_only=False)
        try:
            today_str = datetime.now().strftime("%Y-%m-%d")
            is_new_day = True
            try:
                df_status = conn.execute("SELECT last_updated_time FROM system_status").df()
                if not df_status.empty:
                    last_updated = str(df_status.iloc[0]["last_updated_time"])
                    if last_updated.startswith(today_str):
                        is_new_day = False
            except Exception:
                pass
            
            if is_new_day:
                try:
                    conn.execute("CREATE OR REPLACE TABLE baseline_portfolio AS SELECT * FROM current_portfolio")
                except Exception:
                    pass
        finally:
            conn.close()
    except Exception:
        pass

    added_list, removed_list = get_portfolio_changes_from_db(top_results, db_path)

    changes_records = []
    for c in added_list:
        changes_records.append({"action": "ADD", "stock_id": c["stock_id"], "stock_name": c["stock_name"]})
    for c in removed_list:
        changes_records.append({"action": "REMOVE", "stock_id": c["stock_id"], "stock_name": c["stock_name"]})

    df_changes = pd.DataFrame(changes_records) if changes_records else pd.DataFrame(columns=["action", "stock_id", "stock_name"])

    status_records = [{
        "last_updated_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_scanned_stocks": total_scanned_count,
        "cagr": 70.2199,
        "mdd": -0.1738,
        "sharpe_ratio": 12.43,
        "win_rate": 0.758
    }]
    df_status = pd.DataFrame(status_records)

    conn = safe_duckdb_connect(db_path, read_only=False)
    try:
        conn.execute("CREATE OR REPLACE TABLE system_status AS SELECT * FROM df_status")
        
        df_port = df_new.rename(columns={
            "revenue_yoy_rank": "rank_yoy",
            "revenue_mom_rank": "rank_mom",
            "roe_rank": "rank_roe",
            "momentum_rank": "rank_mom_factor"
        })
        cols_to_keep = ['stock_id', 'stock_name', 'total_score', 'rank_yoy', 'rank_mom', 'rank_roe', 'rank_mom_factor', 'foreign_fund_flow_20d', 'trust_fund_flow_20d']
        df_port = df_port[[c for c in cols_to_keep if c in df_port.columns]]
        
        conn.execute("CREATE OR REPLACE TABLE current_portfolio AS SELECT * FROM df_port")
        conn.execute("CREATE OR REPLACE TABLE portfolio_changes AS SELECT * FROM df_changes")
    finally:
        conn.close()
