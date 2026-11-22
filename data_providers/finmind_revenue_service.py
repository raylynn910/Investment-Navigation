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
    """嚴格自 .env / 環境變數讀取 FinMind Token，嚴禁硬編碼 JWT 密鑰。"""
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

    from db_utils import safe_duckdb_connect
    conn = safe_duckdb_connect(DB_PATH, read_only=False)
    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_revenues_stock_date ON revenues(stock_id, date);")

        # 1. 寫入官方全量當期營收
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

        # 2. 校正前一期營收金額 (依官方上月營收精準比對)
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
    finally:
        conn.close()
    print(f"[DB Update Log] {datetime.now():%Y-%m-%d %H:%M:%S} revenues 官方 TWSE/TPEx UPSERT {len(df_cur)} 檔 ({detected_ym_label}, 標籤日: {detected_pub_date})")
    return {
        "count": len(df_cur),
        "publish_date": detected_pub_date,
        "rev_ym_label": detected_ym_label,
    }


def _get_default_expected_pub_date(now: datetime = None) -> tuple[str, str]:
    """依台灣每月 10 日營收法定公布規則，動態推算最新目標標籤日與名稱"""
    if now is None:
        now = datetime.now()
    if now.day > 10:
        rev_y = now.year - 1 if now.month == 1 else now.year
        rev_m = 12 if now.month == 1 else (now.month - 1)
        pub_date = f"{now.year}-{now.month:02d}-10"
    else:
        prev_y = now.year - 1 if now.month == 1 else now.year
        prev_m = 12 if now.month == 1 else (now.month - 1)
        rev_y = prev_y - 1 if prev_m == 1 else prev_y
        rev_m = 12 if prev_m == 1 else (prev_m - 1)
        pub_date = f"{prev_y}-{prev_m:02d}-10"
    return pub_date, f"{rev_y}年{rev_m}月營收"


def sync_finmind_revenues(start_date: str = None, max_workers: int = 3):
    """
    全量同步最新月營收（全面動態月份化，零整表 DELETE 風險）：
    1. 優先執行 TWSE / TPEx 官方 OpenAPI 全量權威同步（零限速涵蓋一般業與金融/金控業）。
    2. 執行 MoneyDJ 最新月份搶先快報同步（補齊月初搶先公佈之個股）。
    3. 針對仍缺漏目標月份營收之個股，透過 FinMind API 進行低併發差額補齊（嚴格讀 .env，具 403/402 熔斷）。
    4. 僅針對缺少 MoM% 或 YoY% 之紀錄，以精準「上月同日 / 去年同月」比對進行增量 UPDATE，嚴禁整表 DELETE。
    """
    from db_utils import safe_duckdb_connect
    now = datetime.now()
    if not start_date:
        # 往回抓約 3 個月供 FinMind 差額補齊
        m3 = now.month - 3
        y3 = now.year
        if m3 <= 0:
            m3 += 12
            y3 -= 1
        start_date = f"{y3}-{m3:02d}-01"

    print("=" * 68)
    print("🚀 啟動全市場月營收權威同步管線 (TWSE/TPEx 官方 + MoneyDJ + FinMind 差額補齊)...")
    print("=" * 68)

    # -------------------------------------------------------------
    # Stage 1: 官方 TWSE / TPEx 全量同步
    # -------------------------------------------------------------
    off_res = _sync_official_twse_tpex_revenues()
    default_pub_date, default_ym_label = _get_default_expected_pub_date(now)
    target_pub_date = off_res.get("publish_date") or default_pub_date
    target_ym_label = off_res.get("rev_ym_label") or default_ym_label

    # -------------------------------------------------------------
    # Stage 2: MoneyDJ 當月搶先營收快報同步 (僅於每月 1~15 日申報期執行，避免誤寫下月)
    # -------------------------------------------------------------
    early_cnt = 0
    if now.day <= 15:
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
    conn = safe_duckdb_connect(DB_PATH, read_only=True)
    try:
        active_stocks = [
            r[0] for r in conn.execute(
                """
                SELECT DISTINCT stock_id
                FROM klines
                WHERE LENGTH(stock_id) = 4
                  AND stock_id NOT LIKE '0%'
                  AND date >= CAST(? AS DATE)
                ORDER BY stock_id
                """,
                [start_date],
            ).fetchall()
        ]
        have_target_stocks = set(
            r[0] for r in conn.execute(
                "SELECT DISTINCT stock_id FROM revenues WHERE date = ?",
                [target_pub_date],
            ).fetchall()
        )
    finally:
        conn.close()

    missing_stocks = [s for s in active_stocks if s not in have_target_stocks]
    print(f"📊 全市場普通股母體: {len(active_stocks)} 檔 ｜ 已具備 {target_pub_date} ({target_ym_label}): {len(have_target_stocks)} 檔 ｜ 待 FinMind 差額補齊: {len(missing_stocks)} 檔")

    finmind_added = 0
    ip_banned_flag = False

    if missing_stocks:
        tokens = get_finmind_tokens()
        if not tokens:
            print("  ⚠️ 未在 .env 偵測到 FINMIND_TOKEN，略過 FinMind 差額補齊。")
        else:
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
                                    print("  ⚠️ [FinMind 防護熔斷] 偵測到 FinMind 回傳 403 (ip banned)，已自動熔斷停止 FinMind 請求！")
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

                conn = safe_duckdb_connect(DB_PATH, read_only=False)
                try:
                    conn.register("df_fm_delta", df_raw[["stock_id", "date", "revenue"]])
                    conn.execute("""
                        INSERT INTO revenues (stock_id, date, revenue)
                        SELECT stock_id, date, revenue FROM df_fm_delta
                        ON CONFLICT(stock_id, date) DO UPDATE SET
                            revenue = excluded.revenue
                    """)
                    conn.commit()
                    finmind_added = len(df_raw)
                    print(f"[DB Update Log] {datetime.now():%Y-%m-%d %H:%M:%S} revenues FinMind 差額 UPSERT {finmind_added} 筆")
                finally:
                    conn.close()

    # -------------------------------------------------------------
    # Stage 4: 安全增量補算 NULL 之 MoM% / YoY%（嚴禁整表 DELETE！）
    # -------------------------------------------------------------
    conn = safe_duckdb_connect(DB_PATH, read_only=False)
    try:
        # 透過嚴格日期關聯 (精準上個月 / 去年同月) 補齊缺少 MoM 或 YoY 之紀錄，絕不覆蓋官方已提供的精確百分比
        conn.execute("""
            UPDATE revenues AS cur
            SET
                revenue_last_month = COALESCE(cur.revenue_last_month, prev_m.revenue),
                revenue_mom_pct = COALESCE(
                    cur.revenue_mom_pct,
                    CASE WHEN prev_m.revenue > 0 THEN (cur.revenue - prev_m.revenue) / prev_m.revenue * 100.0 ELSE NULL END
                )
            FROM revenues AS prev_m
            WHERE cur.revenue_mom_pct IS NULL
              AND prev_m.stock_id = cur.stock_id
              AND strftime(CAST(prev_m.date AS DATE), '%Y-%m') = strftime(CAST(cur.date AS DATE) - INTERVAL 1 MONTH, '%Y-%m')
        """)
        conn.execute("""
            UPDATE revenues AS cur
            SET
                revenue_last_year = COALESCE(cur.revenue_last_year, prev_y.revenue),
                revenue_yoy_pct = COALESCE(
                    cur.revenue_yoy_pct,
                    CASE WHEN prev_y.revenue > 0 THEN (cur.revenue - prev_y.revenue) / prev_y.revenue * 100.0 ELSE NULL END
                )
            FROM revenues AS prev_y
            WHERE cur.revenue_yoy_pct IS NULL
              AND prev_y.stock_id = cur.stock_id
              AND strftime(CAST(prev_y.date AS DATE), '%Y-%m') = strftime(CAST(cur.date AS DATE) - INTERVAL 1 YEAR, '%Y-%m')
        """)
        target_count = conn.execute(
            "SELECT COUNT(DISTINCT stock_id) FROM revenues WHERE date = ?",
            [target_pub_date],
        ).fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    msg = (
        f"✅ 全市場月營收權威同步完畢！{target_ym_label} ({target_pub_date}) 共 {target_count} 檔齊備"
        f"（官方OpenAPI: {off_res.get('count', 0)}檔, MoneyDJ快報: {early_cnt}檔, FinMind補齊: {finmind_added}筆）"
    )
    print(msg)
    return {
        "status": "success",
        "message": msg,
        "count": target_count,
        "latest_date": target_pub_date,
        "rev_ym_label": target_ym_label,
    }


if __name__ == "__main__":
    res = sync_finmind_revenues()
    print(res)
