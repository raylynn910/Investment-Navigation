# -*- coding: utf-8 -*-
"""
four_factors_screener_service.py
================================
新科狀元系統 多因子選股引擎 (十方極速陣 Top 9 / 15 微緩衝)。
100% 基於本地 data/market_data.duckdb，純 SQL + pandas 向量化運算。

四大因子黃金比例 (10:20:0:70) — 1%~99% Winsorized 截面 Z-Score 加權：
  1. 股東權益報酬率 (ROE) — 10% 權重 (最新一季單季平均化 Z-Score)
  2. 月營收年增率 (YoY)   — 20% 權重 (最新月營收年增率 Z-Score)
  3. 月營收月增率 (MoM)   —  0% 權重 (防禦濾網 MoM > -20%，不參與加權)
  4. 近 10 日極速動能 (Mom10) — 70% 權重 (10日還原報酬率 Z-Score)

五大絕對防禦濾網 (T 日收盤判定)：
  1. ROE > 0 (季財報累計 ROE 除以 quarter 轉為單季平均；若 ROE 為空值且 EPS < 0 則強制設為負數剔除)
  2. YoY > 0 (月營收年增率 > 0%)
  3. MoM > -20% (月營收月增率 > -20%)
  4. 10日動能 > 0 (Close_T / Close_{T-10} - 1 > 0，經 >11% / <-11% 除權息減資跳空向後還原校正)
  5. 5日均成交金額 >= 2億 (MA5(Raw_Close * Volume) >= 200,000,000 NTD)

跨界追蹤鐵律 (Top 9 / 15 微緩衝)：
  - 🟢 必須買入 (Buys)：排名 <= 9 且未持有之個股依序遞補，T+1 開盤 MOO 買入
  - ⚪ 緩衝續抱 (Holds)：已持有且排名落在 1 ~ 15 名者續抱不換股
  - 🔴 必須賣出 (Sells)：已持有之個股排名 > 15 或跌出五大濾網母體，T+1 開盤 MOO 全數賣出

輸出：total_score (截面 Z-Score 加權總分) 由高至低排序的清單。
"""

import pandas as pd
import numpy as np
import os
import duckdb


def _winsorized_zscore(series: pd.Series) -> pd.Series:
    """
    針對當日通過五大濾網之母體，經 1%~99% 截面去極值 (Winsorize) 後計算標準化 Z-Score：
    Z(X) = (Clipped_X - mean) / std
    """
    valid = series.dropna()
    if len(valid) < 2:
        return pd.Series(0.0, index=series.index)
    q_low = valid.quantile(0.01)
    q_high = valid.quantile(0.99)
    clipped = series.clip(lower=q_low, upper=q_high)
    mean = clipped.mean()
    std = clipped.std()
    if pd.isna(std) or std == 0:
        return pd.Series(0.0, index=series.index)
    return ((clipped - mean) / std).fillna(0.0)


def compute_four_factors_from_db() -> list[dict]:
    """
    從 market_data.duckdb 跨表計算五大濾網與 1%~99% Winsorized 截面 Z-Score，回傳排名後字典清單。
    """
    db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "market_data.duckdb")
    from db_utils import safe_duckdb_connect
    db = safe_duckdb_connect(db_path, read_only=True)
    try:
        # ===================================================================
        # 第 1 步：基本面 — 取各檔股票「最新一筆」營收的 YoY% 與 MoM%
        # ===================================================================
        rev_sql = """
            SELECT r.stock_id, r.revenue_yoy_pct, r.revenue_mom_pct
            FROM revenues r
            INNER JOIN (
                SELECT stock_id, MAX(date) AS max_date
                FROM revenues
                GROUP BY stock_id
            ) latest ON r.stock_id = latest.stock_id AND r.date = latest.max_date
        """
        df_rev = db.execute(rev_sql).df()
        df_rev = df_rev.drop_duplicates(subset=["stock_id"], keep="last")

        # ===================================================================
        # 第 2 步：基本面 — 取各檔股票「最新可用」的單季平均 ROE
        # (若 ROE 為空值且 EPS < 0 強制設為 -0.01 剔除；對齊回測披露日與 ffill 邏輯)
        # ===================================================================
        roe_sql = """
            SELECT stock_id, roe_adj AS roe, eps_adj AS eps
            FROM (
                SELECT stock_id,
                       CASE
                           WHEN roe IS NULL AND eps < 0 THEN -0.01
                           ELSE roe / CAST(quarter AS DOUBLE)
                       END AS roe_adj,
                       eps / CAST(quarter AS DOUBLE) AS eps_adj,
                       ROW_NUMBER() OVER(
                           PARTITION BY stock_id
                           ORDER BY year DESC, quarter DESC
                       ) AS rn
                FROM financials
                WHERE roe IS NOT NULL OR eps < 0
            ) sub
            WHERE rn = 1
        """
        df_roe = db.execute(roe_sql).df()

        # ===================================================================
        # 第 3 步：價量與動能 — 掃描歷史 K 線
        # - 針對單日跳空 > 11% 或 < -11% 之除權息/減資斷層完成向後還原校正，計算 10 日動能
        # - 保留原始收盤價 × 原始成交股數計算真實 5 日均成交金額
        # ===================================================================
        momentum_sql = """
            SELECT stock_id, date, open, close, volume
            FROM klines
            WHERE close > 0
              AND date >= (
                SELECT d FROM (
                    SELECT DISTINCT date AS d FROM klines WHERE close > 0 ORDER BY date DESC LIMIT 60
                ) sub ORDER BY d ASC LIMIT 1
              )
            ORDER BY stock_id, date
        """
        df_kline_raw = db.execute(momentum_sql).df()

        momentum_records = []
        for sid, grp in df_kline_raw.groupby("stock_id"):
            grp = grp.sort_values("date").drop_duplicates(subset=["date"], keep="last")
            if len(grp) < 11:
                continue

            raw_c = grp["close"].values.astype(float).copy()
            raw_o = grp["open"].values.astype(float).copy()
            vol = grp["volume"].fillna(0).values.astype(float)

            latest_close = float(raw_c[-1])
            if not latest_close or latest_close <= 0:
                continue

            # 5日平均成交金額 (元) — 使用原始收盤價 × 原始成交股數，不受除權息還原比例影響
            if len(grp) >= 5:
                turnover_ma5 = float(np.mean(raw_c[-5:] * vol[-5:]))
            else:
                turnover_ma5 = 0.0

            # 除權息與減資跳空還原 (Auto-Adjuster)：單日跳空 > 11% 或 < -11% 向後還原校正
            adj_c = raw_c.copy()
            adj_o = raw_o.copy()
            for t in range(1, len(adj_c)):
                prev_c = adj_c[t - 1]
                curr_o = adj_o[t]
                if prev_c > 0 and curr_o > 0:
                    gap = (curr_o / prev_c) - 1.0
                    if gap > 0.11 or gap < -0.11:
                        ratio = curr_o / prev_c
                        adj_c[:t] *= ratio
                        adj_o[:t] *= ratio

            # 10 日極速動能 (Close_T / Close_{T-10} - 1)
            close_10d_ago = adj_c[-11]
            mom_10d = float((adj_c[-1] / close_10d_ago) - 1.0) if close_10d_ago and close_10d_ago > 0 else 0.0

            # 20 日動能 (保留供參考)
            if len(adj_c) >= 21 and adj_c[-21] > 0:
                mom_20d = float((adj_c[-1] / adj_c[-21]) - 1.0)
            else:
                mom_20d = float((adj_c[-1] / adj_c[0]) - 1.0) if adj_c[0] > 0 else 0.0

            momentum_records.append({
                "stock_id": sid,
                "momentum_10d": mom_10d,
                "momentum_20d": mom_20d,
                "turnover_ma5": turnover_ma5,
                "latest_date": str(grp["date"].iloc[-1]) if not grp.empty else "",
                "latest_close": latest_close,
            })

        df_momentum = (
            pd.DataFrame(momentum_records)
            if momentum_records
            else pd.DataFrame(columns=["stock_id", "momentum_10d", "momentum_20d", "turnover_ma5", "latest_date", "latest_close"])
        )

        # 使用台股官方升降單位漲跌停判定模組 (Single Source of Truth)
        from tools.tw_price_limit import load_daily_change_map
        daily_chg_map, _ = load_daily_change_map(db)

        # ===================================================================
        # 第 4 步：籌碼面 — 近 20 日外資與投信淨買超累計
        # ===================================================================
        chip_sql = """
            SELECT stock_id,
                   SUM(foreign_buy - foreign_sell) AS foreign_net_20d,
                   SUM(trust_buy - trust_sell) AS trust_net_20d
            FROM chips
            WHERE date >= (
                SELECT d FROM (
                    SELECT DISTINCT date AS d FROM chips ORDER BY date DESC LIMIT 20
                ) sub ORDER BY d ASC LIMIT 1
            )
            GROUP BY stock_id
        """
        df_chip = db.execute(chip_sql).df()

        # ===================================================================
        # 進行合併全部因子
        # ===================================================================
        all_stocks_sql = "SELECT DISTINCT stock_id FROM klines WHERE LENGTH(stock_id) = 4"
        df_universe = db.execute(all_stocks_sql).df()

        df = df_universe.copy()
        df = df.merge(df_rev, on="stock_id", how="left")
        df = df.merge(df_roe, on="stock_id", how="left")
        df = df.merge(df_momentum, on="stock_id", how="left")
        df = df.merge(df_chip, on="stock_id", how="left")

        # ===================================================================
        # 終極過濾網 (全市場上市櫃普通股 + 五大絕對防禦濾網)
        # ===================================================================
        name_map = _get_stock_name_map()
        df["stock_name"] = df["stock_id"].map(name_map).fillna(df["stock_id"])

        # 1. 排除特別股、權證、0 開頭之 ETF 與指數：長度剛好 4 碼數字且非 '0' 開頭
        df = df[
            (df["stock_id"].str.match(r"^\d{4}$") == True)
            & (~df["stock_id"].str.startswith("0"))
            & (~df["stock_id"].isin(["TAIEX", "TXF"]))
        ]

        # 2. 排除存託憑證 (-DR)
        df = df[~df["stock_name"].str.contains("-DR", na=False)]

        # 3. 套用 五大絕對防禦濾網 (T 日收盤判定)：
        #    - ROE > 0 (單季平均)
        #    - YoY > 0
        #    - MoM > -20%
        #    - 10日動能 > 0 (Close_T / Close_{T-10} - 1 > 0)
        #    - 5日均成交金額 >= 2億 (200,000,000 NTD)
        df = df[
            (df["roe"] > 0)
            & (df["revenue_yoy_pct"] > 0)
            & (df["revenue_mom_pct"] > -20.0)
            & (df["momentum_10d"] > 0.0)
            & (df["turnover_ma5"] >= 200_000_000)
        ].copy()

        if df.empty:
            return []

        # ===================================================================
        # 計算 1%~99% Winsorized 截面標準化 Z-Score
        # Score_T = 0.20 * Z(YoY_T) + 0.10 * Z(ROE_T) + 0.70 * Z(Mom_{10,T})
        # ===================================================================
        df["rank_yoy"] = _winsorized_zscore(df["revenue_yoy_pct"])
        df["rank_mom"] = _winsorized_zscore(df["revenue_mom_pct"])
        df["rank_roe"] = _winsorized_zscore(df["roe"])
        df["rank_mom_10d"] = _winsorized_zscore(df["momentum_10d"])
        df["rank_momentum"] = df["rank_mom_10d"]

        w_roe, w_yoy, w_mom, w_momentum = 0.10, 0.20, 0.00, 0.70

        df["total_score"] = (
            df["rank_yoy"] * w_yoy
            + df["rank_mom"] * w_mom
            + df["rank_roe"] * w_roe
            + df["rank_momentum"] * w_momentum
        )

        # 依綜合 Z-Score 由高至低排序
        df = df.sort_values("total_score", ascending=False).reset_index(drop=True)

        # ===================================================================
        # 組裝輸出
        # ===================================================================
        result = []
        for _, row in df.iterrows():
            sid = row["stock_id"]
            foreign_net = row.get("foreign_net_20d", 0) or 0
            trust_net = row.get("trust_net_20d", 0) or 0
            if pd.isna(foreign_net):
                foreign_net = 0
            if pd.isna(trust_net):
                trust_net = 0

            chg_info = daily_chg_map.get(sid, {})
            result.append({
                "stock_id": sid,
                "stock_name": row["stock_name"],
                "total_score": round(float(row["total_score"]), 4),
                "revenue_yoy_rank": round(float(row.get("rank_yoy", 0) or 0), 4),
                "revenue_mom_rank": round(float(row.get("rank_mom", 0) or 0), 4),
                "roe_rank": round(float(row.get("rank_roe", 0) or 0), 4),
                "momentum_rank": round(float(row.get("rank_momentum", 0) or 0), 4),
                "foreign_fund_flow_20d": round(float(foreign_net) / 1000, 2),  # 轉為「張」
                "trust_fund_flow_20d": round(float(trust_net) / 1000, 2),
                "revenue_yoy_value": round(float(row.get("revenue_yoy_pct", 0) or 0), 4),
                "revenue_mom_value": round(float(row.get("revenue_mom_pct", 0) or 0), 4),
                "roe_value": round(float(row.get("roe", 0) or 0), 4),
                "momentum_value": round(float(row.get("momentum_10d", 0) or 0), 4),
                "latest_date": str(row.get("latest_date", "")),
                "latest_close": round(float(row.get("latest_close", 0) or 0), 2),
                "daily_change_pct": chg_info.get("daily_change_pct"),
                "daily_change_val": chg_info.get("daily_change_val"),
                "is_limit_up": bool(chg_info.get("is_limit_up", False)),
                "is_limit_down": bool(chg_info.get("is_limit_down", False)),
                "limit_up_price": chg_info.get("limit_up_price"),
                "limit_down_price": chg_info.get("limit_down_price"),
            })

        return result

    finally:
        db.close()


def _get_stock_name_map() -> dict[str, str]:
    """
    輕量取得 stock_id -> stock_name 的 mapping。
    先嘗試從快取檔讀取，若無則回傳空 dict（前端會顯示代號）。
    """
    import os, json

    cache_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data", "stock_names.json"
    )
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    # 若快取不存在，嘗試即時從 Open API 抓取並快取
    try:
        import requests, urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        name_map = {}
        # TWSE
        twse = requests.get(
            "https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
            verify=False, timeout=10
        ).json()
        for r in twse:
            name_map[r.get("公司代號", "")] = r.get("公司簡稱", "")

        # TPEx
        tpex = requests.get(
            "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O",
            verify=False, timeout=10
        ).json()
        for r in tpex:
            name_map[r.get("公司代號", "")] = r.get("公司簡稱", "")

        # 寫入快取
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(name_map, f, ensure_ascii=False)

        return name_map

    except Exception:
        return {}
