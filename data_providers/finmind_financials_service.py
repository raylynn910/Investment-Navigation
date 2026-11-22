# -*- coding: utf-8 -*-
"""
finmind_financials_service.py
=============================
使用 FinMind API 全量同步全市場 (~2,000 檔) 上市櫃個股最新財報與季 ROE。
- 支援多 Token (4組) 輪替請求與多執行緒平行加速
- 嚴格正則化：計算單季與累計 ROE、EPS、營業利益率
- 支援自動辨識並同步最新發布之 2026 Q3 財報
- 寫入本地 DuckDB (data/market_data.duckdb) 之 financials 表
"""

import os
import sys
import time
import requests
import duckdb
import threading
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
import numpy as np
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

DB_PATH = os.path.join(PROJECT_ROOT, "data", "market_data.duckdb")

def get_finmind_tokens():
    """嚴格自 .env / 環境變數讀取 FinMind Token，嚴禁在程式碼內硬編碼 JWT。"""
    load_dotenv()
    tokens = [
        os.environ.get("FINMIND_TOKEN"),
        os.environ.get("FINMIND_TOKEN_2"),
        os.environ.get("FINMIND_TOKEN_3"),
        os.environ.get("FINMIND_TOKEN_4"),
    ]
    cleaned = []
    for t in tokens:
        if t:
            val = t.strip().strip('"').strip("'")
            if val and val not in cleaned:
                cleaned.append(val)
    return cleaned

def _get_target_financial_period(now: datetime = None) -> tuple[int, int]:
    """依台灣主管機關申報期限動態計算目標季度 (year, quarter)"""
    if now is None:
        now = datetime.now()
    m, d = now.month, now.day
    if (m == 5 and d > 15) or (m in (6, 7)) or (m == 8 and d <= 14):
        return now.year, 1
    elif (m == 8 and d > 14) or (m in (9, 10)) or (m == 11 and d <= 14):
        return now.year, 2
    elif (m == 11 and d > 14) or (m == 12):
        return now.year, 3
    elif m in (1, 2, 3):
        return now.year - 1, 3
    else:
        return now.year - 1, 4

class TokenRotator:
    def __init__(self, tokens):
        self.tokens = tokens
        self.lock = threading.Lock()
        self.index = 0

    def get_token(self):
        with self.lock:
            tok = self.tokens[self.index % len(self.tokens)]
            self.index += 1
            return tok

def sync_finmind_financials(start_date: str = None, max_workers: int = 3):
    """
    全量同步最新財報（EPS、ROE、營業利益率）。
    採用「官方 TWSE/TPEx OpenAPI (全 6 業別) 優先 + FinMind 差集熔斷補齊」雙引擎架構。
    """
    from db_utils import safe_duckdb_connect
    now = datetime.now()
    target_year, target_quarter = _get_target_financial_period(now)
    if not start_date:
        start_date = f"{target_year}-01-01"

    tokens = get_finmind_tokens()
    url = "https://api.finmindtrade.com/api/v4/data"

    print("=" * 65)
    print(f"🚀 啟動全市場財報與 ROE 同步管線 (目標: {target_year} Q{target_quarter}，官方 OpenAPI + FinMind 雙引擎)...")
    print("=" * 65)

    # Stage 1: 先跑官方 TWSE/TPEx 純 OpenAPI 零限速全市場財報 (涵蓋 6 大產業)
    try:
        from pure_openapi_financials_backfill import run_pure_openapi_backfill
        run_pure_openapi_backfill()
    except Exception as e:
        print(f"  ⚠️ 官方 OpenAPI 財報同步提示: {e}")

    kline_cutoff = f"{target_year}-01-01"
    conn = safe_duckdb_connect(DB_PATH, read_only=True)
    try:
        stocks = conn.execute(
            """
            SELECT DISTINCT stock_id
            FROM klines
            WHERE LENGTH(stock_id) = 4
              AND stock_id NOT LIKE '0%'
              AND date >= CAST(? AS DATE)
            ORDER BY stock_id
            """,
            [kline_cutoff],
        ).fetchall()
        all_stocks = [s[0] for s in stocks]
        # 同時把「尚未有目標季」或「目標季 ROE 仍為 NULL」之標的納入差集補齊
        complete_target = set(
            r[0] for r in conn.execute(
                "SELECT DISTINCT stock_id FROM financials WHERE year = ? AND quarter = ? AND roe IS NOT NULL",
                [target_year, target_quarter],
            ).fetchall()
        )
    finally:
        conn.close()

    stock_list = [s for s in all_stocks if s not in complete_target]
    total_stocks = len(stock_list)
    print(f"📊 透過 FinMind 差集檢查尚未具備完整 {target_year} Q{target_quarter} ROE 標的數: {total_stocks} 檔")

    all_fetched_records = []
    lock = threading.Lock()
    progress_count = 0
    stop_event = threading.Event()
    ip_banned = False

    if not tokens and stock_list:
        print("  ⚠️ 未於 .env 偵測到 FINMIND_TOKEN，略過 Stage 2 FinMind 差集補齊，採用官方 OpenAPI 財報。")
        stock_list = []

    rotator = TokenRotator(tokens) if tokens else None

    def fetch_worker(stock_id):
        nonlocal progress_count, ip_banned
        if stop_event.is_set():
            return
        retries = 2
        while retries > 0 and not stop_event.is_set():
            token = rotator.get_token()
            headers = {"Authorization": f"Bearer {token}"}
            params = {
                "dataset": "TaiwanStockFinancialStatements",
                "data_id": stock_id,
                "start_date": start_date
            }
            try:
                res = requests.get(url, params=params, headers=headers, timeout=10)
                if res.status_code == 200:
                    payload = res.json()
                    if payload.get("status") == 403 or "ip banned" in str(payload.get("msg", "")).lower():
                        ip_banned = True
                        stop_event.set()
                        break
                    data = payload.get("data", [])
                    if data:
                        with lock:
                            all_fetched_records.extend(data)
                    break
                elif res.status_code == 403:
                    ip_banned = True
                    stop_event.set()
                    break
                elif res.status_code == 402:
                    retries -= 1
                else:
                    break
            except Exception:
                retries -= 1

        with lock:
            progress_count += 1
            if progress_count % 50 == 0 or progress_count == total_stocks:
                print(f"  [進度] 財報差集同步已完成 {progress_count}/{total_stocks} 檔")

    start_time = time.time()
    if stock_list:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            executor.map(fetch_worker, stock_list)

    elapsed = time.time() - start_time
    if ip_banned:
        print("  ⚠️ FinMind API 觸發 403 IP 頻率限制熔斷，已自動切換以官方 TWSE/TPEx OpenAPI 財報為準。")
    else:
        print(f"⏱️ FinMind 財報差集檢查完成！耗時 {elapsed:.1f} 秒，共取得 {len(all_fetched_records)} 筆項目數據。")

    if all_fetched_records:
        df_raw = pd.DataFrame(all_fetched_records)
        target_types = [
            "IncomeAfterTaxes",
            "EquityAttributableToOwnersOfParent",
            "EPS",
            "OperatingIncome",
            "Revenue"
        ]
        df_sub = df_raw[df_raw["type"].isin(target_types)].copy()
        if not df_sub.empty:
            pivoted = df_sub.pivot_table(index=["stock_id", "date"], columns="type", values="value", aggfunc="last").reset_index()

            def parse_quarter(date_str):
                parts = str(date_str).split("-")
                year = int(parts[0])
                month = int(parts[1])
                quarter = 1 if month <= 3 else (2 if month <= 6 else (3 if month <= 9 else 4))
                return pd.Series([year, quarter], index=["year", "quarter"])

            y_q = pivoted["date"].apply(parse_quarter)
            pivoted["year"] = y_q["year"]
            pivoted["quarter"] = y_q["quarter"]

            income = pivoted.get("IncomeAfterTaxes", pd.Series(np.nan, index=pivoted.index))
            equity = pivoted.get("EquityAttributableToOwnersOfParent", pd.Series(np.nan, index=pivoted.index))
            eps = pivoted.get("EPS", pd.Series(np.nan, index=pivoted.index))
            op_inc = pivoted.get("OperatingIncome", pd.Series(np.nan, index=pivoted.index))
            rev = pivoted.get("Revenue", pd.Series(np.nan, index=pivoted.index))

            pivoted["roe"] = np.where((equity.notna()) & (equity != 0), (income / equity) * 100.0, np.nan)
            pivoted["eps"] = eps
            pivoted["operating_margin"] = np.where((rev.notna()) & (rev != 0), (op_inc / rev) * 100.0, np.nan)

            clean_df = pivoted[["stock_id", "year", "quarter", "eps", "roe", "operating_margin"]].copy()
            clean_df = clean_df.dropna(subset=["stock_id", "year", "quarter"])
            clean_df = clean_df.drop_duplicates(subset=["stock_id", "year", "quarter"], keep="last")

            conn = safe_duckdb_connect(DB_PATH, read_only=False)
            try:
                conn.register("df_fin_clean", clean_df)
                conn.execute("""
                    INSERT INTO financials (stock_id, year, quarter, eps, roe, operating_margin)
                    SELECT stock_id, year, quarter, eps, roe, operating_margin FROM df_fin_clean
                    ON CONFLICT(stock_id, year, quarter) DO UPDATE SET
                        eps = COALESCE(excluded.eps, financials.eps),
                        roe = COALESCE(excluded.roe, financials.roe),
                        operating_margin = COALESCE(excluded.operating_margin, financials.operating_margin)
                """)
                conn.commit()
                print(f"[DB Update Log] {datetime.now():%Y-%m-%d %H:%M:%S} financials FinMind 差集 UPSERT {len(clean_df)} 筆")
            finally:
                conn.close()

    conn = safe_duckdb_connect(DB_PATH, read_only=True)
    try:
        target_count = conn.execute(
            "SELECT COUNT(*) FROM financials WHERE year = ? AND quarter = ?",
            [target_year, target_quarter],
        ).fetchone()[0]
        target_roe_valid = conn.execute(
            "SELECT COUNT(*) FROM financials WHERE year = ? AND quarter = ? AND roe IS NOT NULL",
            [target_year, target_quarter],
        ).fetchone()[0]
        q2_count = conn.execute("SELECT COUNT(*) FROM financials WHERE year = ? AND quarter = 2", [target_year]).fetchone()[0]
        q3_count = conn.execute("SELECT COUNT(*) FROM financials WHERE year = ? AND quarter = 3", [target_year]).fetchone()[0]
    finally:
        conn.close()

    msg = f"✅ 全市場財報同步完畢！目標 {target_year} Q{target_quarter} 已收錄 {target_count} 檔 (有效 ROE: {target_roe_valid} 檔)！"
    print(msg)
    return {
        "status": "success",
        "message": msg,
        "count": target_count,
        "roe_valid_count": target_roe_valid,
        "q2_count": q2_count,
        "q3_count": q3_count,
        "target_year": target_year,
        "target_quarter": target_quarter,
    }

if __name__ == "__main__":
    res = sync_finmind_financials()
    print(res)
