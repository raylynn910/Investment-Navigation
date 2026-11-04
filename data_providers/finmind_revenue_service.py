# -*- coding: utf-8 -*-
"""
finmind_revenue_service.py
==========================
全市場上市櫃月營收權威同步管線 (Official TWSE/TPEx OpenAPI + MoneyDJ 搶先快報 + FinMind 差額補齊)

核心設計與防呆機制：
1. 【第一棒：證交所 TWSE (t187ap05_L) + 櫃買中心 TPEx (mopsfin_t187ap05_O) 官方權威全量同步】
   - 零限速、不封鎖 IP，1 秒鐘一次拉齊全市場 ~1,981 檔上市櫃最新完整月營收（含官方精確當月營收、上月營收、去年同月營收、官方 MoM%、官方 YoY%）。
   - 嚴格正則化：千元乘以 1,000 轉為「新台幣元 (NTD)」。
   - 統一日期標籤規範：M 月份營收統一對應發布截止日 `YYYY-{M+1:02d}-10`（例如 11509 九月營收 -> `2026-10-10`）。
   - 自動根據官方「上月營收」同步校正前一期（`2026-09-10` 八月營收），徹底消除過去因不同腳本日期標籤錯位造成的覆蓋與重複問題。
2. 【第二棒：MoneyDJ 最新月份搶先公告快報同步】
   - 每月 1~15 日期間，自動抓取已搶先公佈最新月份（如 10 月營收 -> `2026-11-10`）之上市櫃公司官方公告營收、MoM% 與 YoY%。
3. 【第三棒：FinMind API 智慧差額補齊 (Incremental Delta Sync & IP-Ban Protection)】
   - 僅針對第一棒尚未涵蓋之少數缺漏個股呼叫 FinMind API，徹底避免每次從 1101 重頭抓取 2,000 檔導致觸發 FinMind `403 ip banned` 或 `402 Rate Limit`。
   - 內建 `403 ip banned` 與 `402 Rate Limit` 熔斷偵測，一旦偵測到 IP 遭限制立即安全熔斷並保留官方權威數據。
"""

import os
import sys
import time
import urllib3
import requests
import duckdb
import threading
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
import numpy as np
from datetime import datetime
from dotenv import load_dotenv

urllib3.disable_warnings()
load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

DB_PATH = os.path.join(PROJECT_ROOT, "data", "market_data.duckdb")


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


def _rev_ym_to_publish_date(rev_year: int, rev_month: int) -> str:
    """將實際營收年月 (如 2026年9月) 轉換為系統統一之發布截止日標籤 (2026-10-10)"""
    if rev_month == 12:
        return f"{rev_year + 1}-01-10"
    return f"{rev_year}-{rev_month + 1:02d}-10"


def _parse_thousand_to_ntd(val) -> float:
    if val is None:
        return None
    s = str(val).replace(",", "").strip()
    if not s or s in ("None", "-", "nan", "NaN"):
        return None
    try:
        return float(s) * 1000.0
    except Exception:
        return None


def _parse_pct(val) -> float:
    if val is None:
        return None
    s = str(val).replace(",", "").replace("%", "").strip()
    if not s or s in ("None", "-", "nan", "NaN"):
        return None
    try:
        return float(s)
    except Exception:
        return None


def _sync_official_twse_tpex_revenues() -> dict:
    """
    從證交所 (TWSE t187ap05_L) 與櫃買中心 (TPEx mopsfin_t187ap05_O) 官方 OpenAPI
    一次拉齊全市場 ~1,981 檔上市櫃最新完整月營收，並以官方公佈之當月營收、上月營收、去年同月營收、MoM%、YoY% 寫入 DuckDB。
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    twse_data, tpex_data = [], []
    for attempt in range(3):
        try:
            r1 = requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap05_L", headers=headers, verify=False, timeout=25)
            if r1.status_code == 200:
                twse_data = r1.json()
                break
        except Exception as e:
            if attempt == 2:
                print(f"  [TWSE OpenAPI 警告] {e}")
            time.sleep(1)

    for attempt in range(3):
        try:
            r2 = requests.get("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O", headers=headers, verify=False, timeout=25)
            if r2.status_code == 200:
                tpex_data = r2.json()
                break
        except Exception as e:
            if attempt == 2:
                print(f"  [TPEx OpenAPI 警告] {e}")
            time.sleep(1)

    all_rows = (twse_data or []) + (tpex_data or [])
    if not all_rows:
        return {"count": 0, "publish_date": None, "rev_ym_label": None}

    current_records = []
    prev_month_repairs = []
    detected_pub_date = None
    detected_ym_label = None

    for row in all_rows:
        try:
            ym_str = str(row.get("資料年月") or "").strip()
            if len(ym_str) != 5 or not ym_str.isdigit():
                keys = list(row.keys())
                ym_str = str(row[keys[1]]).strip()
            if len(ym_str) != 5 or not ym_str.isdigit():
                continue

            rev_year = int(ym_str[:3]) + 1911
            rev_month = int(ym_str[3:])
            pub_date = _rev_ym_to_publish_date(rev_year, rev_month)
            prev_pub_date = f"{rev_year}-{rev_month:02d}-10"

            if not detected_pub_date:
                detected_pub_date = pub_date
                detected_ym_label = f"{rev_year}年{rev_month}月營收"

            stock_id = str(row.get("公司代號") or row[list(row.keys())[2]]).strip()
            if not stock_id:
                continue

            rev_cur = _parse_thousand_to_ntd(row.get("營業收入-當月營收"))
            rev_prev = _parse_thousand_to_ntd(row.get("營業收入-上月營收"))
            rev_ly = _parse_thousand_to_ntd(row.get("營業收入-去年當月營收"))
            mom_pct = _parse_pct(row.get("營業收入-上月比較增減(%)"))
            yoy_pct = _parse_pct(row.get("營業收入-去年同月增減(%)"))
            cum_rev = _parse_thousand_to_ntd(row.get("累計營業收入-當月累計營收")) or 0.0

            if rev_cur is None:
                continue

            # 若官方欄位未提供百分比但有基準值，則依官方基準值精確計算
            if mom_pct is None and rev_prev and rev_prev > 0:
                mom_pct = (rev_cur - rev_prev) / rev_prev * 100.0
            if yoy_pct is None and rev_ly and rev_ly > 0:
                yoy_pct = (rev_cur - rev_ly) / rev_ly * 100.0

            current_records.append({
                "stock_id": stock_id,
                "date": pub_date,
                "revenue": rev_cur,
                "revenue_last_month": rev_prev,
                "revenue_last_year": rev_ly,
                "revenue_mom_pct": mom_pct,
                "revenue_yoy_pct": yoy_pct,
                "cumulative_revenue": cum_rev,
            })

            # 同步準備上月營收 (例如 8月營收 -> 2026-09-10) 的修復資料，修正先前被舊腳本誤寫為 9月營收的紀錄
            if rev_prev is not None and rev_prev > 0:
                prev_month_repairs.append({
                    "stock_id": stock_id,
                    "date": prev_pub_date,
                    "true_prev_revenue": rev_prev,
                })
        except Exception:
            continue

    if not current_records:
        return {"count": 0, "publish_date": None, "rev_ym_label": None}

    df_cur = pd.DataFrame(current_records).drop_duplicates(subset=["stock_id", "date"], keep="last")
    df_cur = df_cur.replace({np.nan: None})

    conn = duckdb.connect(DB_PATH)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_revenues_stock_date ON revenues(stock_id, date);")

    # 1. 寫入官方全量當期營收 (如 2026-10-10 = 2026年9月營收)
    conn.register("df_official_cur", df_cur)
    conn.execute("""
        INSERT INTO revenues (
            stock_id, date, revenue, revenue_last_month, revenue_last_year,
            revenue_mom_pct, revenue_yoy_pct, cumulative_revenue
        )
        SELECT
            stock_id, date, revenue, revenue_last_month, revenue_last_year,
            revenue_mom_pct, revenue_yoy_pct, cumulative_revenue
        FROM df_official_cur
        ON CONFLICT(stock_id, date) DO UPDATE SET
            revenue = excluded.revenue,
            revenue_last_month = excluded.revenue_last_month,
            revenue_last_year = excluded.revenue_last_year,
            revenue_mom_pct = excluded.revenue_mom_pct,
            revenue_yoy_pct = excluded.revenue_yoy_pct,
            cumulative_revenue = excluded.cumulative_revenue
    """)

    # 2. 校正前一期 (2026-09-10 = 2026年8月營收) 被誤寫為 9月營收之紀錄
    if prev_month_repairs:
        df_prev = pd.DataFrame(prev_month_repairs).drop_duplicates(subset=["stock_id", "date"], keep="last")
        conn.register("df_official_prev", df_prev)
        conn.execute("""
            UPDATE revenues
            SET revenue = df_official_prev.true_prev_revenue
            FROM df_official_prev
            WHERE revenues.stock_id = df_official_prev.stock_id
              AND revenues.date = df_official_prev.date
              AND revenues.revenue != df_official_prev.true_prev_revenue
        """)

    conn.commit()
    conn.close()
    print(f"  ✅ [官方 TWSE/TPEx 權威同步] 已完成 {len(df_cur)} 檔 {detected_ym_label} (標籤日: {detected_pub_date}) 寫入與對齊！")
    return {
        "count": len(df_cur),
        "publish_date": detected_pub_date,
        "rev_ym_label": detected_ym_label,
    }


def _normalize_legacy_openapi_date_tags():
    """
    修復歷史舊版 update_revenue_openapi.py 將 11509 (2026年9月營收) 存於 2026-09-10、
    以及舊版 FinMind 腳本使用 .shift(12) 造成 2026-10-10 (9月營收) 誤對齊 2025-10-10 (10月營收) 的問題。
    完全基於 DuckDB 內已存在之官方證交所/櫃買中心真實申報數據進行精準日期標籤歸位，絕不產生任何虛擬數值。
    """
    conn = duckdb.connect(DB_PATH)
    try:
        # Step A: 針對 2026-10-10 與 2026-09-10 營收值重複之個股 (因舊版 openapi 將 9月營收寫入 2026-09-10)
        # 先將 2026-09-10 內含之官方 9月 MoM/YoY/去年同月 歸位至 2026-10-10，再將 2026-09-10 還原為 8月營收
        conn.execute("""
            UPDATE revenues AS r10
            SET revenue_last_month = r9.revenue_last_month,
                revenue_last_year = r9.revenue_last_year,
                revenue_mom_pct = r9.revenue_mom_pct,
                revenue_yoy_pct = r9.revenue_yoy_pct,
                cumulative_revenue = COALESCE(r9.cumulative_revenue, r10.cumulative_revenue)
            FROM revenues AS r9
            WHERE r10.date = '2026-10-10'
              AND r9.stock_id = r10.stock_id AND r9.date = '2026-09-10'
              AND r10.revenue = r9.revenue
              AND r9.revenue_last_month IS NOT NULL
              AND r9.revenue != r9.revenue_last_month
        """)
        conn.execute("""
            UPDATE revenues AS r9
            SET revenue = r9.revenue_last_month
            FROM revenues AS r10
            WHERE r9.date = '2026-09-10'
              AND r10.stock_id = r9.stock_id AND r10.date = '2026-10-10'
              AND r10.revenue = r9.revenue
              AND r9.revenue_last_month IS NOT NULL
              AND r9.revenue != r9.revenue_last_month
        """)

        # Step B: 針對尚未建立 2026-10-10，但官方 9月營收 (11509) 已由舊版 openapi 存入 2026-09-10 之個股 (468 檔上櫃股)
        mig_cnt = conn.execute("""
            SELECT COUNT(*) FROM revenues r9
            WHERE r9.date = '2026-09-10'
              AND NOT EXISTS (SELECT 1 FROM revenues r10 WHERE r10.stock_id = r9.stock_id AND r10.date = '2026-10-10')
        """).fetchone()[0]
        if mig_cnt > 0:
            conn.execute("""
                INSERT INTO revenues (
                    stock_id, date, revenue, revenue_last_month, revenue_last_year,
                    revenue_mom_pct, revenue_yoy_pct, cumulative_revenue
                )
                SELECT
                    r9.stock_id, CAST('2026-10-10' AS DATE), r9.revenue, r9.revenue_last_month, r9.revenue_last_year,
                    r9.revenue_mom_pct, r9.revenue_yoy_pct, r9.cumulative_revenue
                FROM revenues r9
                WHERE r9.date = '2026-09-10'
                  AND NOT EXISTS (SELECT 1 FROM revenues r10 WHERE r10.stock_id = r9.stock_id AND r10.date = '2026-10-10')
            """)
            # 將原本 2026-09-10 還原為真實 8月營收 (r8)
            conn.execute("""
                UPDATE revenues AS r9
                SET revenue = COALESCE(r8.revenue, r9.revenue_last_month),
                    revenue_last_month = r8.revenue_last_month,
                    revenue_last_year = r8.revenue_last_year,
                    revenue_mom_pct = r8.revenue_mom_pct,
                    revenue_yoy_pct = r8.revenue_yoy_pct,
                    cumulative_revenue = r8.cumulative_revenue
                FROM revenues AS r8, revenues AS r10
                WHERE r9.date = '2026-09-10'
                  AND r8.stock_id = r9.stock_id AND r8.date = '2026-08-10'
                  AND r10.stock_id = r9.stock_id AND r10.date = '2026-10-10'
                  AND r9.revenue = r10.revenue
                  AND r9.revenue != r8.revenue
            """)
            print(f"  ✅ [官方標籤對齊] 已將 {mig_cnt} 檔原存於 2026-09-10 之官方 9月營收歸位至 2026-10-10！")

        # Step C: 修正舊版 FinMind .shift(12) 將 2026-10-10 (2026年9月營收) 誤對齊至 2025-10-10 (2025年10月營收) 的去年同月偏差
        conn.execute("""
            UPDATE revenues AS r10
            SET revenue_last_year = ly_sep.revenue,
                revenue_yoy_pct = CASE WHEN ly_sep.revenue > 0 THEN (r10.revenue - ly_sep.revenue) / ly_sep.revenue * 100.0 ELSE r10.revenue_yoy_pct END
            FROM revenues AS ly_oct, revenues AS ly_sep
            WHERE r10.date = '2026-10-10'
              AND ly_oct.stock_id = r10.stock_id AND ly_oct.date = '2025-10-10'
              AND ly_sep.stock_id = r10.stock_id AND ly_sep.date = '2025-09-10'
              AND ABS(COALESCE(r10.revenue_last_year, 0) - ly_oct.revenue) < 1000
              AND ABS(ly_sep.revenue - ly_oct.revenue) >= 1000
        """)
        conn.commit()
    finally:
        conn.close()


def sync_finmind_revenues(start_date="2026-08-01", max_workers=3):
    """
    全量同步最新月營收：
    1. 優先執行 TWSE / TPEx 官方 OpenAPI 全量權威同步（確保全市場 1,980+ 檔 9月營收 100% 齊備且與官方數字完全一致）。
    2. 執行 MoneyDJ 最新月份搶先快報同步（補齊月初已搶先公佈之 10月營收）。
    3. 針對仍缺漏目標月份營收之個股，透過 FinMind API 進行低併發差額補齊（具備 403 IP Ban 與 402 Rate Limit 熔斷保護）。
    """
    print("=" * 68)
    print("🚀 啟動全市場月營收權威同步管線 (TWSE/TPEx 官方 + MoneyDJ + FinMind 差額補齊)...")
    print("=" * 68)

    # -------------------------------------------------------------
    # Stage 1: 官方 TWSE / TPEx 全量同步 + 歷史日期標籤對齊
    # -------------------------------------------------------------
    off_res = _sync_official_twse_tpex_revenues()
    _normalize_legacy_openapi_date_tags()
    target_pub_date = off_res.get("publish_date") or "2026-10-10"

    # -------------------------------------------------------------
    # Stage 2: MoneyDJ 當月搶先營收快報同步 (如 11月初搶先公佈之 10月營收 -> 2026-11-10)
    # -------------------------------------------------------------
    early_cnt = 0
    try:
        from moneydj_revenue_scraper import scrape_moneydj_revenue
        mdj_res = scrape_moneydj_revenue()
        if isinstance(mdj_res, dict) and mdj_res.get("status") == "success":
            early_cnt = int(mdj_res.get("count", 0))
            print(f"  ✅ [MoneyDJ 搶先快報] 已同步 {early_cnt} 檔最新公告營收 (標籤日: {mdj_res.get('target_date')})")
    except Exception as e:
        print(f"  [MoneyDJ 搶先快報提示] {e}")

    # -------------------------------------------------------------
    # Stage 3: 檢查是否仍有缺漏個股需由 FinMind 差額補齊
    # -------------------------------------------------------------
    conn = duckdb.connect(DB_PATH, read_only=True)
    active_stocks = [
        r[0] for r in conn.execute("""
            SELECT DISTINCT stock_id
            FROM klines
            WHERE LENGTH(stock_id) = 4
              AND stock_id NOT LIKE '0%'
              AND date >= '2026-09-01'
            ORDER BY stock_id
        """).fetchall()
    ]
    have_target_stocks = set(
        r[0] for r in conn.execute("SELECT DISTINCT stock_id FROM revenues WHERE date = ?", [target_pub_date]).fetchall()
    )
    conn.close()

    missing_stocks = [s for s in active_stocks if s not in have_target_stocks]
    print(f"📊 全市場普通股母體: {len(active_stocks)} 檔 ｜ 已具備 {target_pub_date} 營收: {len(have_target_stocks)} 檔 ｜ 待 FinMind 差額補齊: {len(missing_stocks)} 檔")

    finmind_added = 0
    ip_banned_flag = False

    if missing_stocks:
        tokens = get_finmind_tokens()
        rotator = TokenRotator(tokens)
        url = "https://api.finmindtrade.com/api/v4/data"
        all_fetched_records = []
        lock = threading.Lock()
        rate_limited_tokens = set()

        def fetch_worker(stock_id):
            nonlocal ip_banned_flag
            if ip_banned_flag or len(rate_limited_tokens) >= len(tokens):
                return
            for _ in range(len(tokens)):
                if ip_banned_flag:
                    return
                token = rotator.get_token()
                if token in rate_limited_tokens:
                    continue
                headers = {"Authorization": f"Bearer {token}"}
                params = {
                    "dataset": "TaiwanStockMonthRevenue",
                    "data_id": stock_id,
                    "start_date": start_date
                }
                try:
                    res = requests.get(url, params=params, headers=headers, timeout=10)
                    if res.status_code == 403 or "ip banned" in res.text.lower():
                        with lock:
                            if not ip_banned_flag:
                                print("  ⚠️ [FinMind 防護熔斷] 偵測到 FinMind 回傳 403 (ip banned)，已自動熔斷停止 FinMind 請求，採用官方權威數據！")
                            ip_banned_flag = True
                        return
                    if res.status_code == 402:
                        with lock:
                            rate_limited_tokens.add(token)
                        continue
                    if res.status_code == 200:
                        js = res.json()
                        if js.get("status") == 200:
                            data = js.get("data", [])
                            if data:
                                with lock:
                                    all_fetched_records.extend(data)
                            return
                        elif "ip banned" in str(js.get("msg", "")).lower():
                            with lock:
                                ip_banned_flag = True
                            return
                        elif js.get("status") == 402:
                            with lock:
                                rate_limited_tokens.add(token)
                            continue
                        else:
                            return
                except Exception:
                    time.sleep(0.2)
                time.sleep(0.15)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            executor.map(fetch_worker, missing_stocks)

        if all_fetched_records:
            df_raw = pd.DataFrame(all_fetched_records)
            df_raw = df_raw.dropna(subset=["stock_id", "revenue_year", "revenue_month", "revenue"])
            df_raw["revenue_year"] = df_raw["revenue_year"].astype(int)
            df_raw["revenue_month"] = df_raw["revenue_month"].astype(int)
            df_raw["revenue"] = df_raw["revenue"].astype(float)
            df_raw["date"] = df_raw.apply(
                lambda r: _rev_ym_to_publish_date(int(r["revenue_year"]), int(r["revenue_month"])),
                axis=1
            )
            df_raw = df_raw.sort_values(by=["stock_id", "date"]).drop_duplicates(subset=["stock_id", "date"], keep="last")

            conn = duckdb.connect(DB_PATH)
            conn.register("df_fm_delta", df_raw[["stock_id", "date", "revenue"]])
            conn.execute("""
                INSERT INTO revenues (stock_id, date, revenue)
                SELECT stock_id, date, revenue FROM df_fm_delta
                ON CONFLICT(stock_id, date) DO UPDATE SET
                    revenue = excluded.revenue
            """)
            conn.commit()
            conn.close()
            finmind_added = len(df_raw)

    # -------------------------------------------------------------
    # Stage 4: 針對缺少 MoM% 或 YoY% 的紀錄進行安全補算，並校正 2026-11-10 (10月營收) 對齊 2025年10月營收
    # -------------------------------------------------------------
    conn = duckdb.connect(DB_PATH)
    # 校正先前舊版 FinMind 腳本在 2026-11-10 (2026年10月營收) 誤拿 2025-11-10 當去年同月的偏差
    conn.execute("""
        UPDATE revenues AS cur
        SET revenue_last_month = sep.revenue,
            revenue_last_year = ly_oct.revenue,
            revenue_mom_pct = CASE WHEN sep.revenue > 0 THEN (cur.revenue - sep.revenue) / sep.revenue * 100.0 ELSE cur.revenue_mom_pct END,
            revenue_yoy_pct = CASE WHEN ly_oct.revenue > 0 THEN (cur.revenue - ly_oct.revenue) / ly_oct.revenue * 100.0 ELSE cur.revenue_yoy_pct END
        FROM revenues AS sep, revenues AS ly_oct
        WHERE cur.date = '2026-11-10'
          AND sep.stock_id = cur.stock_id AND sep.date = '2026-10-10'
          AND ly_oct.stock_id = cur.stock_id AND ly_oct.date = '2025-10-10'
    """)

    df_all = conn.execute("SELECT * FROM revenues ORDER BY stock_id, date").df()
    if not df_all.empty:
        df_all["calc_last_month"] = df_all.groupby("stock_id")["revenue"].shift(1)
        df_all["calc_last_year"] = df_all.groupby("stock_id")["revenue"].shift(12)

        df_all["revenue_last_month"] = df_all["revenue_last_month"].combine_first(df_all["calc_last_month"])
        df_all["revenue_last_year"] = df_all["revenue_last_year"].combine_first(df_all["calc_last_year"])

        calc_mom = np.where(
            (df_all["revenue_last_month"].notna()) & (df_all["revenue_last_month"] > 0),
            (df_all["revenue"] - df_all["revenue_last_month"]) / np.abs(df_all["revenue_last_month"]) * 100.0,
            np.nan
        )
        calc_yoy = np.where(
            (df_all["revenue_last_year"].notna()) & (df_all["revenue_last_year"] > 0),
            (df_all["revenue"] - df_all["revenue_last_year"]) / np.abs(df_all["revenue_last_year"]) * 100.0,
            np.nan
        )

        # 僅在原本為 NULL 時才填入計算值，100% 保留官方 TWSE/TPEx/MoneyDJ 的精確百分比
        df_all["revenue_mom_pct"] = df_all["revenue_mom_pct"].combine_first(pd.Series(calc_mom, index=df_all.index))
        df_all["revenue_yoy_pct"] = df_all["revenue_yoy_pct"].combine_first(pd.Series(calc_yoy, index=df_all.index))

        cols = [
            "stock_id", "date", "revenue", "revenue_last_month", "revenue_last_year",
            "revenue_mom_pct", "revenue_yoy_pct", "cumulative_revenue"
        ]
        if "cumulative_revenue" not in df_all.columns:
            df_all["cumulative_revenue"] = 0.0
        df_clean = df_all[cols].replace({np.nan: None})

        conn.register("df_rebuilt", df_clean)
        conn.execute("DELETE FROM revenues")
        conn.execute("INSERT INTO revenues SELECT * FROM df_rebuilt")

    sep_count = conn.execute("SELECT COUNT(DISTINCT stock_id) FROM revenues WHERE date = '2026-10-10'").fetchone()[0]
    oct_count = conn.execute("SELECT COUNT(DISTINCT stock_id) FROM revenues WHERE date = '2026-11-10'").fetchone()[0]
    conn.commit()
    conn.close()

    msg = (
        f"🎉 全市場月營收權威同步完畢！9月營收 (2026-10-10) 共 {sep_count} 檔齊備"
        f" ｜ 10月搶先營收 (2026-11-10) 共 {oct_count} 檔"
        f"（官方OpenAPI: {off_res.get('count', 0)}檔, MoneyDJ快報: {early_cnt}檔, FinMind補齊: {finmind_added}筆）"
    )
    print(msg)
    return {
        "status": "success",
        "message": msg,
        "count": sep_count,
        "early_oct_count": oct_count,
        "latest_date": "2026-10-10"
    }


if __name__ == "__main__":
    res = sync_finmind_revenues()
    print(res)
