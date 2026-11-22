# -*- coding: utf-8 -*-
"""
fetch_txf_klines.py
===================
從 FinMind API (TaiwanFuturesDaily) 下載 2018~2026 台指期 (TX) 完整歷史資料：
1. 日盤 (trading_session == 'position', 08:45~13:45) -> 寫入 klines (stock_id = 'TXF')
2. 夜盤 (trading_session == 'after_market', 15:00~次日05:00) -> 寫入 klines (stock_id = 'TXF_NIGHT')
遵循專案日誌紀錄準則，完整輸出更新筆數與最新日期。
"""

import os
import sys
import logging
import requests
import pandas as pd
import duckdb
from dotenv import load_dotenv

load_dotenv()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def get_finmind_tokens() -> list[str]:
    """嚴格自 .env / 環境變數載入 FinMind Token，程式碼內絕不硬編碼任何 JWT 密鑰。"""
    load_dotenv()
    raw = [
        os.environ.get("FINMIND_TOKEN"),
        os.environ.get("FINMIND_TOKEN_2"),
        os.environ.get("FINMIND_TOKEN_3"),
        os.environ.get("FINMIND_TOKEN_4"),
    ]
    tokens = []
    for t in raw:
        if t:
            cleaned = t.strip().strip('"').strip("'")
            if cleaned and cleaned not in tokens:
                tokens.append(cleaned)
    return tokens


FINMIND_TOKENS = get_finmind_tokens()


def _extract_front_month(df_sub: pd.DataFrame, stock_id_label: str) -> pd.DataFrame:
    df_sub = df_sub.copy()
    # 排除跨月價差合約 (contract_date 含有 '/' 或長度非 6 碼之週契約/價差單)
    if "contract_date" in df_sub.columns:
        df_sub["contract_date"] = df_sub["contract_date"].astype(str).str.strip()
        cond_std = df_sub["contract_date"].str.match(r"^\d{6}$")
        if cond_std.any():
            df_sub = df_sub[cond_std].copy()

    for col in ["open", "max", "min", "close", "volume"]:
        df_sub[col] = pd.to_numeric(df_sub[col], errors="coerce")

    df_sub = df_sub.dropna(subset=["open", "close", "volume"])
    df_sub = df_sub[df_sub["close"] > 0]

    # 每日選取成交量最大之近月主力合約
    idx_max_vol = df_sub.groupby("date")["volume"].idxmax()
    df_front = df_sub.loc[idx_max_vol].copy()

    df_front["date"] = pd.to_datetime(df_front["date"]).dt.strftime("%Y-%m-%d")
    df_front = df_front.sort_values("date").rename(columns={"max": "high", "min": "low"})
    df_front["stock_id"] = stock_id_label

    return df_front[["date", "stock_id", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


def fetch_txf_day_and_night():
    logger.info("開始下載台指期 (TX) 2018~2026 日盤 (TXF) 與夜盤 (TXF_NIGHT) 歷史資料...")
    url = "https://api.finmindtrade.com/api/v4/data"
    years = range(2018, 2027)
    all_data = []

    tokens = get_finmind_tokens()
    if not tokens:
        logger.error("未在 .env 或環境變數設定 FINMIND_TOKEN，中止 FinMind 歷史回補。")
        return False

    for i, year in enumerate(years):
        token = tokens[i % len(tokens)]
        params = {
            "dataset": "TaiwanFuturesDaily",
            "data_id": "TX",
            "start_date": f"{year}-01-01",
            "end_date": f"{year}-12-31",
        }
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        data = resp.json()
        if data.get("status") == 200:
            df = pd.DataFrame(data.get("data", []))
            if not df.empty:
                all_data.append(df)
                logger.info(f"  - {year} 年下載成功: {len(df)} 筆原始合約紀錄")
        else:
            logger.error(f"  - {year} 年下載失敗: {data.get('msg')}")

    if not all_data:
        logger.error("未取得任何 TaiwanFuturesDaily 資料，中止更新。")
        return False

    df_all = pd.concat(all_data, ignore_index=True)

    # 1. 日盤 (position) -> TXF
    df_day_raw = df_all[df_all["trading_session"] == "position"]
    df_day = _extract_front_month(df_day_raw, "TXF")

    # 2. 夜盤 (after_market) -> TXF_NIGHT
    df_night_raw = df_all[df_all["trading_session"] == "after_market"]
    df_night = _extract_front_month(df_night_raw, "TXF_NIGHT")

    logger.info(f"日盤 (TXF) 共整理出 {len(df_day)} 個交易日 ({df_day['date'].min()} ~ {df_day['date'].max()})")
    logger.info(f"夜盤 (TXF_NIGHT) 共整理出 {len(df_night)} 個交易日 ({df_night['date'].min()} ~ {df_night['date'].max()})")

    df_combined = pd.concat([df_day, df_night], ignore_index=True)

    from db_utils import safe_duckdb_connect
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "market_data.duckdb")
    conn = safe_duckdb_connect(db_path, read_only=False)
    try:
        conn.execute("CREATE TEMP TABLE temp_txf_all AS SELECT * FROM df_combined")
        conn.execute("""
            INSERT INTO klines (stock_id, date, open, high, low, close, volume)
            SELECT stock_id, CAST(date AS DATE), open, high, low, close, volume
            FROM temp_txf_all
            ON CONFLICT (stock_id, date)
            DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume;
        """)
        logger.info(f"[DB Update Log] 成功寫入/更新 klines: TXF ({len(df_day)} 筆) 與 TXF_NIGHT ({len(df_night)} 筆)")
    finally:
        conn.close()

    print("\n--- 最新 5 筆 TXF (日盤) ---")
    print(df_day.tail(5).to_string(index=False))
    print("\n--- 最新 5 筆 TXF_NIGHT (夜盤) ---")
    print(df_night.tail(5).to_string(index=False))
    return True


if __name__ == "__main__":
    fetch_txf_day_and_night()
