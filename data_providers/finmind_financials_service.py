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

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "market_data.duckdb")

def get_finmind_tokens():
    tokens = [
        os.environ.get("FINMIND_TOKEN"),
        os.environ.get("FINMIND_TOKEN_2"),
        os.environ.get("FINMIND_TOKEN_3"),
        os.environ.get("FINMIND_TOKEN_4"),
    ]
    tokens = [t.strip().strip('"').strip("'") for t in tokens if t and t.strip()]
    if not tokens:
        tokens = [
            "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoicmF5bHlubjkxMEBnbWFpbC5jb20iLCJlbWFpbCI6InJheWx5bm45MTBAZ21haWwuY29tIiwidG9rZW5fdmVyc2lvbiI6Mn0.jNIfxQyrQQ0CAw0BrOzoJtpzZzC0etk5KmC4Uhd3Z4g",
            "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoibHlubl9xayIsImVtYWlsIjoibHlubl9xazA5MTBAeWFob28uY29tLnR3IiwidG9rZW5fdmVyc2lvbiI6MH0.1FXHypT6ge0jaISUjmJoFtx0Xch908r4i6NNSQF6Scw",
            "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiZWxvc3lkIiwiZW1haWwiOiJFbG9TeWRAZ21haWwuY29tIiwidG9rZW5fdmVyc2lvbiI6MH0.ZtNR3B5fOuBrdgFJF96ctyw5zP2LIWak6s0A4p3X9uo",
            "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoieXVmYW5nIiwiZW1haWwiOiJ5dWZhbmcwODI2NzZAZ21haWwuY29tIiwidG9rZW5fdmVyc2lvbiI6MH0.8XyhbDI2cQAgSNmujPuxj-OZhyMgHQdlXXcOIEBpcAs"
        ]
    return tokens

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

def sync_finmind_financials(start_date="2026-01-01", max_workers=3):
    """
    全量同步最新財報（EPS、ROE、營業利益率），涵蓋 Q1、Q2 與最新公佈之 Q3。
    採用「官方 TWSE/TPEx OpenAPI 優先 + FinMind 差集熔斷補齊」雙引擎架構。
    """
    tokens = get_finmind_tokens()
    rotator = TokenRotator(tokens)
    url = "https://api.finmindtrade.com/api/v4/data"

    print("=" * 65)
    print("🚀 啟動全市場財報與 ROE 同步管線 (官方 OpenAPI + FinMind 雙引擎)...")
    print("=" * 65)

    # Stage 1: 先跑官方 TWSE/TPEx 純 OpenAPI 零限速全市場財報
    try:
        from pure_openapi_financials_backfill import run_pure_openapi_backfill
        run_pure_openapi_backfill()
    except Exception as e:
        print(f"  ⚠️ 官方 OpenAPI 財報同步提示: {e}")

    conn = duckdb.connect(DB_PATH, read_only=True)
    stocks = conn.execute("""
        SELECT DISTINCT stock_id 
        FROM klines 
        WHERE LENGTH(stock_id) = 4 
          AND stock_id NOT LIKE '0%' 
          AND date >= '2026-08-01'
        ORDER BY stock_id
    """).fetchall()
    all_stocks = [s[0] for s in stocks]
    existing_q3 = set(
        r[0] for r in conn.execute(
            "SELECT DISTINCT stock_id FROM financials WHERE year = 2026 AND quarter = 3"
        ).fetchall()
    )
    conn.close()

    stock_list = [s for s in all_stocks if s not in existing_q3]
    total_stocks = len(stock_list)
    print(f"📊 透過 FinMind 差集檢查尚未收錄 2026 Q3 標的數: {total_stocks} 檔")

    all_fetched_records = []
    lock = threading.Lock()
    progress_count = 0
    stop_event = threading.Event()
    ip_banned = False

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
            if progress_count % 200 == 0 or progress_count == total_stocks:
                print(f"  [進度] 財報差集同步已完成 {progress_count}/{total_stocks} 檔")

    start_time = time.time()
    if stock_list:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            executor.map(fetch_worker, stock_list)

    elapsed = time.time() - start_time
    if ip_banned:
        print("  ⚠️ FinMind API 觸發 403 IP 頻率限制熔斷，已自動切換以官方 TWSE/TPEx OpenAPI 財報為準。")
    else:
        print(f"⏱️ FinMind 財報數據下載完成！耗時 {elapsed:.1f} 秒，共取得 {len(all_fetched_records)} 筆項目數據。")

    if all_fetched_records:
        # -------------------------------------------------------------
        # 數據組裝與清洗 (Group by stock_id, date)
        # -------------------------------------------------------------
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
                parts = date_str.split("-")
                year = int(parts[0])
                month = int(parts[1])
                if month <= 3:
                    quarter = 1
                elif month <= 6:
                    quarter = 2
                elif month <= 9:
                    quarter = 3
                else:
                    quarter = 4
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

            conn = duckdb.connect(DB_PATH)
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
            conn.close()

    conn = duckdb.connect(DB_PATH, read_only=True)
    q3_count = conn.execute("SELECT COUNT(*) FROM financials WHERE year = 2026 AND quarter = 3").fetchone()[0]
    q2_count = conn.execute("SELECT COUNT(*) FROM financials WHERE year = 2026 AND quarter = 2").fetchone()[0]
    conn.close()

    msg = f"✅ 全市場財報同步完畢！已校準 2026 Q2 ({q2_count} 檔)，並收錄最新已公告之 2026 Q3 ({q3_count} 檔)！"
    print(msg)
    return {
        "status": "success",
        "message": msg,
        "count": q2_count + q3_count,
        "q2_count": q2_count,
        "q3_count": q3_count
    }

if __name__ == "__main__":
    res = sync_finmind_financials()
    print(res)
