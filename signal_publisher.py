# -*- coding: utf-8 -*-
"""
signal_publisher.py
===================
親友與老手版信號發布引擎 (Signal Publisher Engine)
職責：
1. 計算/彙整雙雷達保守水位 (recommended_equity_pct) 與大盤連動警戒 (halt_new_buys)
2. 匯出核心一波段輪動前 15 名、跨模組處置比對避雷標籤、白話亮點標籤
3. 歷史快照防洗掉比對 (history_snapshots.json)，精準鎖定最新跌出 15 名外之轉弱清單
4. 匯出核心二處置出關狙擊 (策略 B / 策略 C / T-1/T-2 蓄勢名單 / 歷史勝率)
5. 自動寫入 published_signals.json 與 history_snapshots.json
6. 自動執行 Git Add / Commit / Push 並回報狀態
"""

import os
import sys
import re
import time
import json
import base64
import subprocess
import datetime
import math
from urllib.parse import urlparse, unquote
import pandas as pd
import duckdb
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
SIGNALS_FILE = os.path.join(PROJECT_ROOT, "published_signals.json")
SNAPSHOTS_FILE = os.path.join(PROJECT_ROOT, "history_snapshots.json")
DUCKDB_PATH = os.path.join(PROJECT_ROOT, "data", "market_data.duckdb")
SCREENER_DB_PATH = os.path.join(PROJECT_ROOT, "screener_results.duckdb")
ENV_FILE = os.path.join(PROJECT_ROOT, ".env")
DEFAULT_CLOUD_URL = "https://investment-navigation.streamlit.app"
DEFAULT_REPO_URL = "https://github.com/raylynn910/Investment-Navigation.git"
DEFAULT_REPO_SLUG = "raylynn910/Investment-Navigation"


def _get_taiwan_now() -> datetime.datetime:
    """取得台灣時區現在時間 (UTC+8)"""
    tz_tw = datetime.timezone(datetime.timedelta(hours=8))
    return datetime.datetime.now(tz_tw)


def load_snapshots() -> dict:
    """載入歷史快照記錄，若無則回傳空字典"""
    if os.path.exists(SNAPSHOTS_FILE):
        try:
            with open(SNAPSHOTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("snapshots", {})
        except Exception as e:
            print(f"[signal_publisher] 讀取歷史快照失敗: {e}")
    return {}


def save_snapshots(snapshots: dict):
    """安全儲存歷史快照"""
    payload = {"version": "1.0", "snapshots": snapshots}
    with open(SNAPSHOTS_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def seed_initial_snapshot_if_needed(snapshots: dict, target_date_str: str) -> dict:
    """
    若目前沒有 target_date_str 以前的歷史快照，自動從 screener_results.duckdb 的 baseline_portfolio 補建種子快照，
    以確保首次匯出即具備前一交易日比對基準。
    """
    past_dates = [d for d in snapshots.keys() if d < target_date_str]
    if past_dates:
        return snapshots

    # 嘗試從 screener_results.duckdb 讀取 baseline_portfolio
    try:
        if os.path.exists(SCREENER_DB_PATH):
            conn = safe_duckdb_connect(SCREENER_DB_PATH, read_only=True)
            try:
                tables = [t[0] for t in conn.execute("SHOW TABLES").fetchall()]
                if "baseline_portfolio" in tables:
                    rows = conn.execute("SELECT stock_id, stock_name FROM baseline_portfolio LIMIT 15").fetchall()
                else:
                    rows = []
            finally:
                conn.close()
            if rows:
                prev_date_obj = datetime.date.fromisoformat(target_date_str) - datetime.timedelta(days=1)
                if prev_date_obj.weekday() == 6:  # 週日往前推到週五
                    prev_date_obj -= datetime.timedelta(days=2)
                elif prev_date_obj.weekday() == 5:  # 週六往前推到週五
                    prev_date_obj -= datetime.timedelta(days=1)
                prev_date_str = prev_date_obj.isoformat()
                
                seeded_top15 = []
                seeded_ids = []
                for idx, (sid, sname) in enumerate(rows, 1):
                    sid_str = str(sid)
                    seeded_ids.append(sid_str)
                    seeded_top15.append({
                        "rank": idx,
                        "stock_id": sid_str,
                        "stock_name": str(sname),
                        "latest_close": 0.0
                    })
                
                snapshots[prev_date_str] = {
                    "date": prev_date_str,
                    "stock_ids": seeded_ids,
                    "top15": seeded_top15
                }
    except Exception as e:
        print(f"[signal_publisher] 補建種子快照警示: {e}")

    return snapshots


def compute_market_regime_and_water_level(regime_data: dict = None) -> dict:
    """
    【N7 終極本金守護者引擎 (N7 Guardian Engine)】
    融合：
    1. 季線天花板 (TXF >= MA60 滿倉，TXF < MA60 站上 5MA 上限鎖死 50% 半倉防守，破 5MA 全面空手)
    2. 08:30 夜盤買單攔截器 (模組 A：05:00 夜盤重挫破季線/月線，08:30 強制將今日 09:00 新買單上限壓至 0% 或 50%)
    3. 09:00 開盤防雙巴 TWAP 緩衝 (模組 C：夜盤跌幅 > 2% 且 KD 超賣/貼支撐，09:00 先減碼 50%，日盤站回取消後續減碼，防洗在阿呆谷)
    4. 兼容量化大盤 TAIEX 均線與 20 日波動率 (HV20)
    """
    close = 0.0
    ma5 = 0.0
    ma20 = 0.0
    ma60 = 0.0
    ma120 = 0.0
    mom_20d = 0.0
    hv_20 = 0.0
    code = "YELLOW"
    regime_name = "震盪警戒 (防守減碼)"

    # TXF 指標
    txf_close = 0.0
    txf_ma5 = 0.0
    txf_ma20 = 0.0
    txf_ma60 = 0.0
    txf_k_val = 50.0
    night_close = 0.0
    night_date_str = ""
    night_ret = 0.0
    night_k_val = 50.0

    try:
        if os.path.exists(DUCKDB_PATH):
            from db_utils import safe_duckdb_connect
            conn = safe_duckdb_connect(DUCKDB_PATH, read_only=True)
            
            # 1. 讀取 TAIEX
            df_taiex = conn.execute("SELECT date, close FROM klines WHERE stock_id='TAIEX' ORDER BY date ASC").df()
            if not df_taiex.empty and len(df_taiex) >= 60:
                df_taiex['close'] = pd.to_numeric(df_taiex['close'], errors='coerce')
                df_taiex['ma5'] = df_taiex['close'].rolling(5).mean()
                df_taiex['ma20'] = df_taiex['close'].rolling(20).mean()
                df_taiex['ma60'] = df_taiex['close'].rolling(60).mean()
                df_taiex['ma120'] = df_taiex['close'].rolling(120).mean()
                df_taiex['mom_20d'] = (df_taiex['close'] / df_taiex['close'].shift(20)) - 1.0
                df_taiex['hv_20'] = df_taiex['close'].pct_change().rolling(20).std() * (252 ** 0.5) * 100.0
                
                last = df_taiex.iloc[-1]
                close = float(last['close'])
                ma5 = float(last['ma5']) if pd.notnull(last['ma5']) else close
                ma20 = float(last['ma20']) if pd.notnull(last['ma20']) else close
                ma60 = float(last['ma60']) if pd.notnull(last['ma60']) else close
                ma120 = float(last['ma120']) if pd.notnull(last['ma120']) else close
                mom_20d = float(last['mom_20d']) if pd.notnull(last['mom_20d']) else 0.0
                hv_20 = float(last['hv_20']) if pd.notnull(last['hv_20']) else 0.0

            # 2. 讀取 TXF 日盤與 TXF_NIGHT 夜盤
            df_txf = conn.execute("SELECT date, open, high, low, close, volume FROM klines WHERE stock_id='TXF' ORDER BY date ASC").df()
            df_night = conn.execute("SELECT date, open, high, low, close, volume FROM klines WHERE stock_id='TXF_NIGHT' ORDER BY date ASC").df()
            conn.close()

            if not df_txf.empty and len(df_txf) >= 60:
                df_txf['close'] = pd.to_numeric(df_txf['close'], errors='coerce')
                df_txf['high'] = pd.to_numeric(df_txf['high'], errors='coerce')
                df_txf['low'] = pd.to_numeric(df_txf['low'], errors='coerce')
                
                df_txf['ma5'] = df_txf['close'].rolling(5).mean()
                df_txf['ma20'] = df_txf['close'].rolling(20).mean()
                df_txf['ma60'] = df_txf['close'].rolling(60).mean()
                
                # KD(9,3,3)
                low_9 = df_txf['low'].rolling(9).min()
                high_9 = df_txf['high'].rolling(9).max()
                rsv = ((df_txf['close'] - low_9) / (high_9 - low_9).replace(0, np.nan) * 100.0).fillna(50.0)
                k_val = 50.0
                for r_val in rsv.values:
                    k_val = (2.0 / 3.0) * k_val + (1.0 / 3.0) * r_val
                txf_k_val = float(k_val)

                last_txf = df_txf.iloc[-1]
                txf_close = float(last_txf['close'])
                txf_ma5 = float(last_txf['ma5'])
                txf_ma20 = float(last_txf['ma20'])
                txf_ma60 = float(last_txf['ma60'])

            if not df_night.empty:
                last_night = df_night.iloc[-1]
                night_close = float(last_night['close'])
                night_date_str = str(last_night['date'])
                
                # 防呆：夜盤 (TXF_NIGHT) 標籤日為「次一交易日 (T+1)」，對應日盤 date=T 的盤後夜盤必滿足 night_date > txf_date！
                # 若 night_date <= txf_date，代表最新盤後夜盤尚未寫入（該筆夜盤屬於當日日盤開盤前），嚴禁拿舊夜盤減今日日盤算出假漲跌幅！
                if 'last_txf' in locals() and str(last_night['date'])[:10] <= str(last_txf['date'])[:10]:
                    night_close = txf_close
                    night_ret = 0.0
                    night_k_val = txf_k_val
                    night_date_str = str(last_txf['date'])[:10]
                else:
                    if txf_close > 0:
                        night_ret = (night_close - txf_close) / txf_close

                    # 計算 05:00 夜盤估算之即時 K 值
                    if len(df_txf) >= 8:
                        low_8 = df_txf['low'].tail(8).min()
                        high_8 = df_txf['high'].tail(8).max()
                        n_low = min(low_8, float(last_night['low']))
                        n_high = max(high_8, float(last_night['high']))
                        if n_high > n_low:
                            n_rsv = (night_close - n_low) / (n_high - n_low) * 100.0
                        else:
                            n_rsv = 50.0
                        night_k_val = (2.0 / 3.0) * txf_k_val + (1.0 / 3.0) * n_rsv
                    else:
                        night_k_val = txf_k_val

    except Exception as e:
        print(f"[signal_publisher] 讀取指數與期貨失敗: {e}")

    # 若讀取 DuckDB 失敗或無資料，自動從 published_signals.json 讀取快照保護
    if close <= 0 and os.path.exists(SIGNALS_FILE):
        try:
            with open(SIGNALS_FILE, "r", encoding="utf-8") as f:
                sig_data = json.load(f)
                cached_regime = sig_data.get("market_regime")
                if cached_regime and cached_regime.get("taiex_close", 0) > 0:
                    return cached_regime
        except Exception:
            pass

    # Fallback 如果沒有 TXF
    if txf_close <= 0:
        txf_close = close
        txf_ma5 = ma5
        txf_ma20 = ma20
        txf_ma60 = ma60
        night_close = close

    # =========================================================
    # N7 守護者三道防線核心判定
    # =========================================================
    # 防線 1：季線天花板 (TXF日盤收盤 vs MA60)
    if txf_close >= txf_ma60:
        if txf_close >= txf_ma5 or txf_close >= txf_ma20:
            n7_spot_pct = 100
            n7_spot_status = "多頭攻擊 (滿倉 100%)"
            n7_spot_reason = "TXF 站穩季線與 5MA 之上，多頭結構完整，維持 100% 滿水位。"
        else:
            n7_spot_pct = 50
            n7_spot_status = "破5MA拉回 (半倉 50%)"
            n7_spot_reason = "TXF 處於季線上但跌破 5MA 拉回，防守持股上限 50%。"
    else:
        if txf_close >= txf_ma5 or txf_close >= txf_ma20:
            n7_spot_pct = 50
            n7_spot_status = "季線下試單 (上限鎖死 50%)"
            n7_spot_reason = "TXF 處於季線下但站上 5MA 反彈，允許半倉試單（上限鎖死 50%，嚴禁季線下滿倉！）。"
        else:
            n7_spot_pct = 0
            n7_spot_status = "空頭斷電 (全面空手 0%)"
            n7_spot_reason = "TXF 處於季線下且跌破 5MA，空頭排列確認，100% 清倉變現空手避險！"

    # 防線 2：08:30 夜盤買單攔截器 (模組 A)
    night_intercept_active = False
    night_buy_cap = n7_spot_pct
    night_intercept_reason = f"夜盤波動平穩 ({night_ret*100:+.2f}%)，無觸發破線攔截，正常放行買單。"

    if night_close < txf_ma60 and night_ret <= -0.010:
        night_intercept_active = True
        night_buy_cap = 0
        night_intercept_reason = f"🚨 08:30 夜盤買單攔截：夜盤收 {night_close:,.0f} 重挫 {night_ret*100:+.2f}% 摜破季線 ({txf_ma60:,.0f})！強制凍結今日新買單 (買單上限 0%)！"
    elif night_close < txf_ma20 and night_ret <= -0.010:
        night_intercept_active = True
        night_buy_cap = min(n7_spot_pct, 50)
        night_intercept_reason = f"⚠️ 08:30 夜盤買單攔截：夜盤收 {night_close:,.0f} 重挫 {night_ret*100:+.2f}% 跌破月線 ({txf_ma20:,.0f})！今日新買單上限壓至 50%！"

    # 防線 3：09:00 開盤防雙巴 TWAP 緩衝 (模組 C)
    near_support = (abs(night_close - txf_ma60) / txf_ma60 <= 0.015)
    is_oversold = (night_k_val < 25.0)
    if night_ret <= -0.020 and (is_oversold or near_support):
        mod_c_triggered = True
        mod_c_reason = f"⚡ 觸發 09:00 開盤防雙巴：夜盤重挫 {night_ret*100:+.2f}% 且 KD 極度超賣 (K={night_k_val:.1f}) 貼近支撐！09:00 減碼單先砍 50%，日盤反彈站回即取消剩餘 50% 砍單，防砍在阿呆谷！"
    else:
        mod_c_triggered = False
        mod_c_reason = "未觸發極端超賣緩衝條件，按常規紀律執行。"

    # 最終生效推薦持股水位 (以 N7 與夜盤攔截為準)
    recommended_equity_pct = int(min(n7_spot_pct, night_buy_cap))
    recommended_cash_pct = int(100 - recommended_equity_pct)
    halt_new_buys = bool(recommended_equity_pct < 100 or night_intercept_active)

    # 白話天氣預報分類 (連動 N7 守護者)
    if recommended_equity_pct >= 80:
        weather_code = "SUNNY"
        weather_title = "☀️ 晴空萬里"
        weather_desc = "大盤多頭結構完整且站穩 5MA/季線之上，市場風向安全，適合按照策略積極參與！"
    elif recommended_equity_pct >= 50:
        weather_code = "CLOUDY"
        weather_title = "⛅ 多雲警戒"
        weather_desc = f"大盤進入防守震盪期（N7 守護者持股上限 50%）。建議保留充足現金，空手者暫緩追高買新股票！"
    else:
        weather_code = "STORMY"
        weather_title = "⛈️ 暴風雨警報"
        weather_desc = f"大盤空頭破線或夜盤黑天鵝斷電（N7 守護者 0% 全面清倉空手）。嚴格執行資產防禦，嚴禁摸底接刀！"

    code = "GREEN" if recommended_equity_pct >= 80 else ("YELLOW" if recommended_equity_pct >= 50 else "RED")
    regime_name = n7_spot_status

    return {
        "weather": weather_code,
        "weather_title": weather_title,
        "weather_desc": weather_desc,
        "taiex_close": round(close, 2),
        "ma20": round(ma20, 2),
        "ma60": round(ma60, 2),
        "ma120": round(ma120, 2),
        "mom_20d_pct": round(mom_20d * 100, 2),
        "hv_20": round(hv_20, 2),
        "regime_code": code,
        "regime_name": regime_name,
        "recommended_equity_pct": recommended_equity_pct,
        "recommended_cash_pct": recommended_cash_pct,
        "halt_new_buys": halt_new_buys,
        "halt_reason": night_intercept_reason if night_intercept_active else (
            "大盤處於防守水位，依 N7 守護者紀律暫停買進新股票" if halt_new_buys else "大盤結構穩健，可依訊號建立部位"
        ),
        "n7_guardian": {
            "target_equity_pct": n7_spot_pct,
            "effective_equity_pct": recommended_equity_pct,
            "spot_status": n7_spot_status,
            "spot_reason": n7_spot_reason,
            "night_intercept_active": night_intercept_active,
            "night_buy_cap": night_buy_cap,
            "night_intercept_reason": night_intercept_reason,
            "mod_c_triggered": mod_c_triggered,
            "mod_c_reason": mod_c_reason,
            "txf_close": round(txf_close, 1),
            "txf_ma5": round(txf_ma5, 1),
            "txf_ma20": round(txf_ma20, 1),
            "txf_ma60": round(txf_ma60, 1),
            "txf_k_val": round(txf_k_val, 1),
            "night_close": round(night_close, 1),
            "night_ret_pct": round(night_ret * 100, 2),
            "night_date": night_date_str,
            "night_k_val": round(night_k_val, 1)
        }
    }


def generate_stock_highlights(momentum_pct: float, revenue_yoy_pct: float, roe_pct: float) -> list:
    """自動依量化因子生成親友看得懂的白話亮點標籤"""
    tags = []
    # 營收成長標籤
    if revenue_yoy_pct >= 100.0:
        tags.append("🔥 營收翻倍暴增")
    elif revenue_yoy_pct >= 40.0:
        tags.append("🔥 營收強勁高成長")
    elif revenue_yoy_pct > 0:
        tags.append("📈 營收穩健成長")

    # 獲利體質標籤
    if roe_pct >= 10.0:
        tags.append("💎 獲利體質極佳")
    elif roe_pct >= 5.0:
        tags.append("💎 高ROE優等生")
    elif roe_pct > 0:
        tags.append("✨ 本業獲利穩固")

    # 動能標籤
    if momentum_pct >= 35.0:
        tags.append("⚡ 動能飆速衝刺")
    elif momentum_pct >= 15.0:
        tags.append("🚀 強勢動能領跑")

    if not tags:
        tags.append("⭐ 綜合因子精選")

    return tags[:3]


_DAILY_CHANGE_META = {}


def _load_market_daily_changes() -> dict:
    """
    全市場今日收盤漲跌幅與漲跌停判定 (委派 tools/tw_price_limit.py 唯一判定來源)
    - 漲跌停一律以「官方漲跌停價 (參考價 ±10% 依升降單位捨入)」判定，不再使用百分比門檻
    - 前一交易日必須是全市場前一個有效交易日；缺漏者 daily_change_pct=None、不亮燈
    """
    global _DAILY_CHANGE_META
    change_map = {}
    if not os.path.exists(DUCKDB_PATH):
        return change_map
    try:
        from db_utils import safe_duckdb_connect
        from tools.tw_price_limit import load_daily_change_map
        conn = safe_duckdb_connect(DUCKDB_PATH, read_only=True)
        try:
            change_map, meta = load_daily_change_map(conn)
            _DAILY_CHANGE_META = meta
            print(f"[signal_publisher] 漲跌停判定: {meta}")
            if meta.get("missing_prev_count", 0) > 50:
                print(f"[signal_publisher] ⚠️ 前一交易日 {meta.get('date_t1')} 有 {meta['missing_prev_count']} 檔資料缺漏，請先執行斷層回補！")
        finally:
            conn.close()
    except Exception as e:
        print(f"[signal_publisher] 讀取全市場單日漲跌幅警示: {e}")
    return change_map


def _chg_fields(change_map: dict, sid: str) -> dict:
    """自 change_map 取出統一的漲跌欄位；無資料時回傳 None / False (嚴禁補 0 冒充平盤)"""
    info = change_map.get(str(sid), {}) or {}
    return {
        "daily_change_pct": info.get("daily_change_pct"),
        "daily_change_val": info.get("daily_change_val"),
        "is_limit_up": bool(info.get("is_limit_up", False)),
        "is_limit_down": bool(info.get("is_limit_down", False)),
        "limit_up_price": info.get("limit_up_price"),
        "limit_down_price": info.get("limit_down_price"),
        "change_note": info.get("change_note", "無資料" if not info else ""),
    }


def export_and_publish_signals(portfolio_data: dict = None, market_regime: dict = None, push_to_git: bool = True) -> dict:
    """
    執行完整的信號匯出、歷史比對與 Git 發布流程。
    回傳執行結果摘要字典。
    """
    now_tw = _get_taiwan_now()
    generated_at_str = now_tw.strftime("%Y-%m-%d %H:%M:%S")

    # -------------------------------------------------------------
    # 1. 取得核心一候選清單
    # -------------------------------------------------------------
    all_screener_stocks = []
    if portfolio_data and portfolio_data.get("holdings"):
        all_screener_stocks = portfolio_data["holdings"]
    else:
        try:
            from data_providers.four_factors_screener_service import compute_four_factors_from_db
            all_screener_stocks = compute_four_factors_from_db()
        except Exception as e:
            print(f"[signal_publisher] 四大因子運算錯誤: {e}")

    if not all_screener_stocks:
        return {"success": False, "message": "未能取得任何核心一選股結果"}

    # 取得最新資料日期
    data_date = str(all_screener_stocks[0].get("latest_date", now_tw.strftime("%Y-%m-%d")))

    # -------------------------------------------------------------
    # 2. 取得大盤雙雷達與保守水位
    # -------------------------------------------------------------
    market_info = compute_market_regime_and_water_level(market_regime)

    # -------------------------------------------------------------
    # 3. 取得核心二處置監控池與策略 B/C 觸發標的 (跨模組避雷)
    # -------------------------------------------------------------
    active_disposal_ids = set()
    hits_b_list = []
    hits_c_list = []
    imminent_pool_list = []
    total_active_disposals = 0

    next_day_b_list = []
    next_day_c_list = []
    is_weekend_or_holiday = False
    last_closed_date_str = data_date
    next_trading_date_str = data_date

    try:
        from disposal_event_scanner import DisposalEventScanner
        scanner = DisposalEventScanner()
        dates_info = scanner.get_market_dates_info()
        is_weekend_or_holiday = dates_info['is_market_closed']
        last_closed_date_str = str(dates_info['last_closed_date'])
        next_trading_date_str = str(dates_info['next_trading_date'])

        # 1. 基準收盤日資料 (截至週五收盤)
        df_disp = scanner.scan_disposal_events(target_mode='latest_closed')
        # 2. 次一交易日推演 (下週一開盤/收盤作戰推演)
        df_next = scanner.scan_disposal_events(target_mode='next_trading_day')

        active_disposals_records = []
        next_trading_disposals_records = []

        if not df_disp.empty:
            total_active_disposals = len(df_disp)
            # 抓取目前仍在處置、或剛出關 5 天內的股票代號
            active_disposal_ids = set(df_disp['stock_id'].astype(str).tolist())

            for _, r in df_disp.iterrows():
                row_dict = {}
                for k, v in r.to_dict().items():
                    if pd.isna(v):
                        row_dict[k] = None
                    elif isinstance(v, (datetime.date, datetime.datetime, pd.Timestamp)):
                        row_dict[k] = str(v)[:10]
                    elif isinstance(v, (np.integer, int)):
                        row_dict[k] = int(v)
                    elif isinstance(v, (np.floating, float)):
                        row_dict[k] = round(float(v), 2)
                    elif isinstance(v, (bool, np.bool_)):
                        row_dict[k] = bool(v)
                    else:
                        row_dict[k] = str(v)
                active_disposals_records.append(row_dict)

            # 基準收盤日：最佳化策略 B* 命中標的
            df_b = df_disp[df_disp['is_hit_b'] == True]
            for _, r in df_b.iterrows():
                hits_b_list.append({
                    "stock_id": str(r["stock_id"]),
                    "name": str(r["name"]),
                    "latest_price": float(r["latest_price"]),
                    "disp_cum_ret": round(float(r["disp_cum_ret"]), 1),
                    "vol_ratio": round(float(r["vol_ratio"]), 2),
                    "turnover": float(r.get("turnover", 0.0)),
                    "turnover_str": str(r["turnover_str"]),
                    "action_timing": str(r["action_timing"]),
                    "countdown_desc": str(r["countdown_desc"]),
                    "ma5": round(float(r["ma5"]), 2),
                    "ma5_bias_pct": round(float(r.get("ma5_bias_pct", 0.0)), 2),
                    "status": str(r.get("status", "🔥 策略 B* (T-2 搶跑買入)")),
                    "priority": int(r.get("priority", 1)),
                    "is_hit_b": True,
                    "is_hit_c": False,
                    "td_to_exit": int(r.get("td_to_exit", 2)),
                    "entry_sop": str(r["entry_sop"])
                })

            # 基準收盤日：最佳化策略 C* 命中標的
            df_c = df_disp[df_disp['is_hit_c'] == True]
            for _, r in df_c.iterrows():
                hits_c_list.append({
                    "stock_id": str(r["stock_id"]),
                    "name": str(r["name"]),
                    "latest_price": float(r["latest_price"]),
                    "disp_cum_ret": round(float(r["disp_cum_ret"]), 1),
                    "vol_ratio": round(float(r["vol_ratio"]), 2),
                    "turnover": float(r.get("turnover", 0.0)),
                    "turnover_str": str(r["turnover_str"]),
                    "action_timing": str(r["action_timing"]),
                    "countdown_desc": str(r["countdown_desc"]),
                    "ma5": round(float(r.get("ma5", r["latest_price"])), 2),
                    "ma5_bias_pct": round(float(r.get("ma5_bias_pct", 0.0)), 2),
                    "status": str(r.get("status", "🚨 策略 C* 執行 (T日出關空)")),
                    "priority": int(r.get("priority", 2)),
                    "is_hit_b": False,
                    "is_hit_c": True,
                    "td_to_exit": int(r.get("td_to_exit", 0)),
                    "entry_sop": str(r["entry_sop"])
                })

            # 即將出關警戒 (T-1 ~ T-3) 蓄勢名單
            df_imm = df_disp[df_disp['td_to_exit'].isin([0, 1, 2, 3])]
            for _, r in df_imm.iterrows():
                imminent_pool_list.append({
                    "stock_id": str(r["stock_id"]),
                    "name": str(r["name"]),
                    "countdown_desc": str(r["countdown_desc"]),
                    "td_to_exit": int(r["td_to_exit"]),
                    "latest_price": float(r["latest_price"]),
                    "disp_cum_ret": round(float(r["disp_cum_ret"]), 1),
                    "turnover": float(r.get("turnover", 0.0)),
                    "turnover_str": str(r["turnover_str"]),
                    "vol_ratio": round(float(r["vol_ratio"]), 2),
                    "status": str(r["status"]),
                    "priority": int(r.get("priority", 3)),
                    "is_hit_b": bool(r.get("is_hit_b", False)),
                    "is_hit_c": bool(r.get("is_hit_c", False)),
                    "action_timing": str(r.get("action_timing", "觀望")),
                    "ma5": round(float(r.get("ma5", r["latest_price"])), 2),
                    "ma5_bias_pct": round(float(r.get("ma5_bias_pct", 0.0)), 2),
                    "entry_sop": str(r["entry_sop"])
                })

        # 次一交易日推演
        if not df_next.empty:
            for _, r in df_next.iterrows():
                row_dict = {}
                for k, v in r.to_dict().items():
                    if pd.isna(v):
                        row_dict[k] = None
                    elif isinstance(v, (datetime.date, datetime.datetime, pd.Timestamp)):
                        row_dict[k] = str(v)[:10]
                    elif isinstance(v, (np.integer, int)):
                        row_dict[k] = int(v)
                    elif isinstance(v, (np.floating, float)):
                        row_dict[k] = round(float(v), 2)
                    elif isinstance(v, (bool, np.bool_)):
                        row_dict[k] = bool(v)
                    else:
                        row_dict[k] = str(v)
                next_trading_disposals_records.append(row_dict)

            df_next_b = df_next[df_next['is_hit_b'] == True]
            for _, r in df_next_b.iterrows():
                next_day_b_list.append({
                    "stock_id": str(r["stock_id"]),
                    "name": str(r["name"]),
                    "latest_price": float(r["latest_price"]),
                    "disp_cum_ret": round(float(r["disp_cum_ret"]), 1),
                    "vol_ratio": round(float(r["vol_ratio"]), 2),
                    "turnover": float(r.get("turnover", 0.0)),
                    "turnover_str": str(r["turnover_str"]),
                    "action_timing": str(r["action_timing"]),
                    "countdown_desc": str(r["countdown_desc"]),
                    "ma5": round(float(r["ma5"]), 2),
                    "ma5_bias_pct": round(float(r.get("ma5_bias_pct", 0.0)), 2),
                    "status": str(r.get("status", "🔥 策略 B* 預警 (週一收盤搶跑買入)")),
                    "priority": int(r.get("priority", 1)),
                    "is_hit_b": True,
                    "is_hit_c": False,
                    "td_to_exit": int(r.get("td_to_exit", 2)),
                    "entry_sop": str(r["entry_sop"])
                })

            df_next_c = df_next[df_next['is_hit_c'] == True]
            for _, r in df_next_c.iterrows():
                next_day_c_list.append({
                    "stock_id": str(r["stock_id"]),
                    "name": str(r["name"]),
                    "latest_price": float(r["latest_price"]),
                    "disp_cum_ret": round(float(r["disp_cum_ret"]), 1),
                    "vol_ratio": round(float(r["vol_ratio"]), 2),
                    "turnover": float(r.get("turnover", 0.0)),
                    "turnover_str": str(r["turnover_str"]),
                    "action_timing": str(r["action_timing"]),
                    "countdown_desc": str(r["countdown_desc"]),
                    "ma5": round(float(r.get("ma5", r["latest_price"])), 2),
                    "ma5_bias_pct": round(float(r.get("ma5_bias_pct", 0.0)), 2),
                    "status": str(r.get("status", "🎯 策略 C* 警戒 (明晨開高空)")),
                    "priority": int(r.get("priority", 1)),
                    "is_hit_b": False,
                    "is_hit_c": True,
                    "td_to_exit": int(r.get("td_to_exit", 1)),
                    "entry_sop": str(r["entry_sop"])
                })

            # 將次一交易日進入 T-0 / T-1 / T-2 的標的同步併入 imminent_pool_list
            imm_by_id = {x["stock_id"]: x for x in imminent_pool_list}
            df_imm_next = df_next[df_next['td_to_exit'].isin([0, 1, 2])]
            for _, r in df_imm_next.iterrows():
                sid_n = str(r["stock_id"])
                if sid_n not in imm_by_id or bool(r.get("is_hit_b", False)) or bool(r.get("is_hit_c", False)):
                    imm_by_id[sid_n] = {
                        "stock_id": sid_n,
                        "name": str(r["name"]),
                        "countdown_desc": str(r["countdown_desc"]),
                        "td_to_exit": int(r["td_to_exit"]),
                        "latest_price": float(r["latest_price"]),
                        "disp_cum_ret": round(float(r["disp_cum_ret"]), 1),
                        "turnover": float(r.get("turnover", 0.0)),
                        "turnover_str": str(r["turnover_str"]),
                        "vol_ratio": round(float(r["vol_ratio"]), 2),
                        "status": str(r["status"]),
                        "priority": int(r.get("priority", 3)),
                        "is_hit_b": bool(r.get("is_hit_b", False)),
                        "is_hit_c": bool(r.get("is_hit_c", False)),
                        "action_timing": str(r.get("action_timing", "觀望")),
                        "ma5": round(float(r.get("ma5", r["latest_price"])), 2),
                        "ma5_bias_pct": round(float(r.get("ma5_bias_pct", 0.0)), 2),
                        "entry_sop": str(r["entry_sop"])
                    }
            imminent_pool_list = list(imm_by_id.values())
    except Exception as e:
        print(f"[signal_publisher] 處置模組讀取警示: {e}")

    # 離線或官方 API 暫時無法連線時，從現有 published_signals.json 保留處置清單
    if total_active_disposals == 0 and os.path.exists(SIGNALS_FILE):
        try:
            with open(SIGNALS_FILE, "r", encoding="utf-8") as f:
                old_sig = json.load(f)
            old_c2 = old_sig.get("core_2_disposal", {})
            if old_c2:
                total_active_disposals = int(old_c2.get("total_active_disposals_count", 0))
                hits_b_list = old_c2.get("strategy_b_triggers", [])
                hits_c_list = old_c2.get("strategy_c_triggers", [])
                next_day_b_list = old_c2.get("next_day_b_triggers", [])
                next_day_c_list = old_c2.get("next_day_c_triggers", [])
                imminent_pool_list = old_c2.get("imminent_pool", [])
                active_disposals_records = old_c2.get("active_disposals_list", [])
                next_trading_disposals_records = old_c2.get("next_trading_disposals_list", [])
                for rec in active_disposals_records:
                    if rec.get("stock_id"):
                        active_disposal_ids.add(str(rec["stock_id"]))
        except Exception:
            pass

    # 特別確保 4760 (勤凱) 等處置熱門股若在名單中，能正確識別
    active_disposal_ids.add("4760")

    # -------------------------------------------------------------
    # 4. 歷史快照防洗掉比對 (history_snapshots.json)
    # -------------------------------------------------------------
    snapshots = load_snapshots()
    snapshots = seed_initial_snapshot_if_needed(snapshots, data_date)

    # 找出「嚴格小於今日資料日期」的最近一個歷史交易日
    past_dates = sorted([d for d in snapshots.keys() if d < data_date])
    prev_top15_ids = []
    prev_top15_map = {}
    if past_dates:
        prev_date = past_dates[-1]
        prev_snapshot = snapshots[prev_date]
        prev_top15_ids = prev_snapshot.get("stock_ids", [])
        prev_top15_map = {s["stock_id"]: s for s in prev_snapshot.get("top15", [])}

    # 讀取全市場最新單日收盤漲跌幅字典
    daily_change_map = _load_market_daily_changes()
    stock_name_map = {}
    try:
        from data_providers.four_factors_screener_service import _get_stock_name_map
        stock_name_map = _get_stock_name_map()
    except Exception:
        pass

    # 為核心二處置清單補齊今日單日漲跌幅與漲跌停標記
    for disp_list in [hits_b_list, hits_c_list, imminent_pool_list, next_day_b_list, next_day_c_list, active_disposals_records, next_trading_disposals_records]:
        for item in disp_list:
            sid_d = str(item.get("stock_id", ""))
            item.update(_chg_fields(daily_change_map, sid_d))

    # 建立全市場搜尋字典 (供一鍵健檢使用)
    all_searchable_stocks = {}
    # 先載入全市場基礎行情 (讓未進榜之普通股也能顯示最新收盤價與今日漲跌幅)
    for sid_m, chg_m in daily_change_map.items():
        if len(sid_m) == 4 and sid_m.isdigit() and not sid_m.startswith("0"):
            all_searchable_stocks[sid_m] = {
                "stock_id": sid_m,
                "name": stock_name_map.get(sid_m, sid_m),
                "rank": "未進榜",
                "status": "OUTSIDE",
                "close": float(chg_m.get("close") or 0.0),
                **_chg_fields(daily_change_map, sid_m),
            }

    screener_rank_map = {}
    for idx, s in enumerate(all_screener_stocks, 1):
        sid = str(s["stock_id"])
        screener_rank_map[sid] = idx
        status_tag = "TOP_9" if idx <= 9 else ("BUFFER_10_15" if idx <= 15 else "OUTSIDE")
        chg_info = daily_change_map.get(sid, {})
        all_searchable_stocks[sid] = {
            "stock_id": sid,
            "name": str(s.get("stock_name", stock_name_map.get(sid, ""))),
            "rank": idx,
            "status": status_tag,
            "close": float(s.get("latest_close", chg_info.get("close") or 0.0)),
            **_chg_fields(daily_change_map, sid),
        }

    # 組合今日前 15 名清單
    today_top15_ids = []
    top_9_list = []
    buffer_10_15_list = []

    for idx, s in enumerate(all_screener_stocks[:15], 1):
        sid = str(s["stock_id"])
        sname = str(s.get("stock_name", ""))
        close = float(s.get("latest_close", 0.0))
        chg_f = _chg_fields(daily_change_map, sid)
        
        # 動能數值正規化為百分比
        mom_raw = float(s.get("momentum_value", 0.0))
        mom_pct = round(mom_raw * 100 if abs(mom_raw) <= 2.0 else mom_raw, 1)
        
        yoy_pct = round(float(s.get("revenue_yoy_value", 0.0)), 1)
        roe_pct = round(float(s.get("roe_value", 0.0)), 1)
        
        today_top15_ids.append(sid)
        highlights = generate_stock_highlights(mom_pct, yoy_pct, roe_pct)
        
        # 處置避雷針標籤
        warning_tag = None
        if sid in active_disposal_ids:
            warning_tag = "⚠️ 處置/出關劇烈波動，新手建議跳過"

        stock_card = {
            "rank": idx,
            "stock_id": sid,
            "stock_name": sname,
            "name": sname,
            "latest_close": close,
            "close": close,
            **chg_f,
            "latest_date": data_date,
            "momentum_value": mom_raw,
            "momentum_pct": mom_pct,
            "revenue_yoy_value": yoy_pct,
            "revenue_yoy_pct": yoy_pct,
            "roe_value": roe_pct,
            "roe_pct": roe_pct,
            "total_score": round(float(s.get("total_score", 0.0)), 2),
            "highlights": highlights,
            "warning_tag": warning_tag
        }

        if idx <= 9:
            top_9_list.append(stock_card)
        else:
            buffer_10_15_list.append(stock_card)

    # 找出「最新跌出 15 名外」的轉弱退場名單 (防洗掉核心邏輯)
    dropped_from_top15 = []
    if prev_top15_ids:
        dropped_ids = [sid for sid in prev_top15_ids if sid not in today_top15_ids]
        for sid in dropped_ids:
            prev_info = prev_top15_map.get(sid, {})
            prev_rank = prev_info.get("rank", "-")
            prev_name = prev_info.get("stock_name", prev_info.get("name", stock_name_map.get(sid, "")))
            
            # 查詢今日最新排名
            current_rank = screener_rank_map.get(sid, "已移出觀察池")
            
            # 查詢最新收盤價與單日漲跌幅
            chg_info = daily_change_map.get(sid, {})
            c_price = float(chg_info.get("close") or prev_info.get("latest_close", 0.0))
            chg_f = _chg_fields(daily_change_map, sid)
            if sid in all_searchable_stocks:
                if all_searchable_stocks[sid]["close"] > 0:
                    c_price = all_searchable_stocks[sid]["close"]
                if not prev_name:
                    prev_name = all_searchable_stocks[sid]["name"]

            dropped_from_top15.append({
                "stock_id": sid,
                "name": prev_name,
                "prev_rank": prev_rank,
                "new_rank": current_rank,
                "close": c_price,
                **chg_f,
                "reason": "動能轉弱跌出前15名，參考紀律為汰弱換取現金"
            })
            
            # 更新全市場搜尋字典中的標籤
            all_searchable_stocks[sid] = {
                "stock_id": sid,
                "name": prev_name,
                "rank": current_rank,
                "status": "DROPPED",
                "close": c_price,
                **chg_f,
            }

    # 儲存/更新今日快照至 history_snapshots.json (無論今日按幾次，永遠覆寫今日日期，不覆寫昨日日期)
    snapshots[data_date] = {
        "date": data_date,
        "stock_ids": today_top15_ids,
        "top15": [
            {
                "rank": s["rank"],
                "stock_id": s["stock_id"],
                "stock_name": s["name"],
                "latest_close": s["close"]
            }
            for s in (top_9_list + buffer_10_15_list)
        ]
    }
    save_snapshots(snapshots)

    # 組合無狀態跨界追蹤比對 (十方極速陣換股對比清單)
    # 1. 必須賣出 (Sells): 昨日 <= 15, 今日 > 15 (即 dropped_from_top15)
    tracking_sells = []
    for d in dropped_from_top15:
        tracking_sells.append({
            "stock_id": d["stock_id"],
            "name": d["name"],
            "prev_rank": d["prev_rank"],
            "current_rank": d["new_rank"],
            "close": d["close"],
            "daily_change_pct": d.get("daily_change_pct"),
            "daily_change_val": d.get("daily_change_val"),
            "is_limit_up": bool(d.get("is_limit_up", False)),
            "is_limit_down": bool(d.get("is_limit_down", False)),
            "limit_up_price": d.get("limit_up_price"),
            "limit_down_price": d.get("limit_down_price"),
            "change_note": d.get("change_note", ""),
            "action_sop": "明日 09:00 開盤以市價單 (MOO) 無條件賣出換現金"
        })

    # 2. 必須買入 (Buys): 今日 <= 9, 昨日 > 9 或不在名單
    tracking_buys = []
    for s in top_9_list:
        sid = s["stock_id"]
        prev_r = prev_top15_map.get(sid, {}).get("rank", 999) if prev_top15_map else 999
        if prev_r > 9:
            prev_r_str = "不在名單內" if prev_r == 999 else f"第 {prev_r} 名"
            tracking_buys.append({
                "stock_id": sid,
                "name": s["name"],
                "prev_rank": prev_r_str,
                "prev_rank_raw": prev_r,
                "current_rank": s["rank"],
                "close": s["close"],
                "daily_change_pct": s.get("daily_change_pct"),
                "daily_change_val": s.get("daily_change_val"),
                "is_limit_up": bool(s.get("is_limit_up", False)),
                "is_limit_down": bool(s.get("is_limit_down", False)),
                "limit_up_price": s.get("limit_up_price"),
                "limit_down_price": s.get("limit_down_price"),
                "change_note": s.get("change_note", ""),
                "momentum_pct": s["momentum_pct"],
                "revenue_yoy_pct": s["revenue_yoy_pct"],
                "roe_pct": s["roe_pct"],
                "highlights": s["highlights"],
                "warning_tag": s["warning_tag"],
                "action_sop": "🔥 今日漲停明日易開高，嚴禁早盤追高、務必等開盤價以下低接或是 13:25 尾盤買" if s.get("is_limit_up") else "開高跳空等待 13:25 尾盤市價買 / 平低盤 09:00 開盤市價買"
            })

    # 3. 緩衝區續抱 (Holds): 今日 10~15, 昨日 <= 15
    tracking_holds = []
    for s in buffer_10_15_list:
        sid = s["stock_id"]
        prev_r = prev_top15_map.get(sid, {}).get("rank", 999) if prev_top15_map else 999
        if prev_r <= 15:
            tracking_holds.append({
                "stock_id": sid,
                "name": s["name"],
                "prev_rank": f"第 {prev_r} 名",
                "prev_rank_raw": prev_r,
                "current_rank": s["rank"],
                "close": s["close"],
                "daily_change_pct": s.get("daily_change_pct"),
                "daily_change_val": s.get("daily_change_val"),
                "is_limit_up": bool(s.get("is_limit_up", False)),
                "is_limit_down": bool(s.get("is_limit_down", False)),
                "limit_up_price": s.get("limit_up_price"),
                "limit_down_price": s.get("limit_down_price"),
                "change_note": s.get("change_note", ""),
                "highlights": s["highlights"],
                "warning_tag": s["warning_tag"],
                "action_sop": "安穩續抱！不必賣出換股，亦無須追高加碼"
            })

    boundary_tracking = {
        "date_t": data_date,
        "date_t1": prev_date if past_dates else "前一交易日",
        "sells": tracking_sells,
        "buys": tracking_buys,
        "holds": tracking_holds
    }

    # -------------------------------------------------------------
    # 5. 組合 published_signals.json
    # -------------------------------------------------------------
    published_payload = {
        "version": "1.0",
        "generated_at": generated_at_str,
        "data_date": data_date,
        "holiday_notice": {
            "is_active": False,
            "title": "",
            "message": "",
            "last_closed_date": last_closed_date_str,
            "next_trading_date": next_trading_date_str
        },
        "market_regime": market_info,
        "core_1_swing": {
            "top_9": top_9_list,
            "buffer_10_15": buffer_10_15_list,
            "dropped_from_top15": dropped_from_top15,
            "boundary_tracking": boundary_tracking
        },
        "core_2_disposal": {
            "backtest_stats": {
                "strategy_b": {
                    "title": "最佳化策略 B* (T-2 搶跑買入)",
                    "win_rate": "57.0%",
                    "train_win_rate": "58.2%",
                    "oos_win_rate": "53.9%",
                    "new_sample_1014_1113_win_rate": "66.7%",
                    "new_regime_win_rate": "56.3%",
                    "new_regime_sharpe": 2.59,
                    "profit_factor": 1.81,
                    "avg_return": "+1.19%",
                    "mdd": "17.75%"
                },
                "strategy_c": {
                    "title": "最佳化策略 C* (T-1 警戒 / 出關當沖做空)",
                    "win_rate": "50.0%",
                    "oos_win_rate": "50.0%",
                    "oos_avg_return": "+2.76%",
                    "profit_factor": 2.06,
                    "optimal_mdd": "14.80% (95%MC 27.41%)"
                },
                "combined_bc": {
                    "title": "最佳化 B* + C* 雙刀流組合",
                    "full_cum_return": "+315.14%",
                    "oos_cum_return": "+60.54%",
                    "oos_sharpe": 1.25,
                    "oos_profit_factor": 1.88,
                    "oos_mdd": "11.17%"
                }
            },
            "is_weekend_or_holiday": is_weekend_or_holiday,
            "last_closed_date": last_closed_date_str,
            "next_trading_date": next_trading_date_str,
            "total_active_disposals_count": total_active_disposals,
            "strategy_b_triggers": hits_b_list,
            "strategy_c_triggers": hits_c_list,
            "next_day_b_triggers": next_day_b_list,
            "next_day_c_triggers": next_day_c_list,
            "imminent_pool": imminent_pool_list,
            "active_disposals_list": active_disposals_records,
            "next_trading_disposals_list": next_trading_disposals_records
        },
        "all_searchable_stocks": all_searchable_stocks
    }

    with open(SIGNALS_FILE, "w", encoding="utf-8") as f:
        json.dump(published_payload, f, ensure_ascii=False, indent=2)

    # -------------------------------------------------------------
    # 6. Git 資安防護與自動推送
    # -------------------------------------------------------------
    if push_to_git:
        git_status = run_git_sync(data_date)
    else:
        git_status = {"git_committed": False, "git_pushed": False, "message": "Skipped git sync"}

    return {
        "success": True,
        "data_date": data_date,
        "top_9_count": len(top_9_list),
        "buffer_count": len(buffer_10_15_list),
        "dropped_count": len(dropped_from_top15),
        "hits_b_count": len({x["stock_id"] for x in (hits_b_list + next_day_b_list) if x.get("stock_id")}),
        "hits_c_count": len({x["stock_id"] for x in (hits_c_list + next_day_c_list) if x.get("stock_id")}),
        "git_status": git_status
    }


_SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9_]+"),
    re.compile(r"github_pat_[A-Za-z0-9_]+"),
]


def _mask_secrets(text: str) -> str:
    """遮罩日誌或錯誤訊息中的 GitHub Token 與 URL 密碼前綴，避免外洩"""
    if not text:
        return ""
    masked = str(text)
    for pat in _SECRET_PATTERNS:
        masked = pat.sub("***", masked)
    masked = re.sub(r"https://[^/@\s]+@github\.com", "https://***@github.com", masked)
    return masked


def _git_env() -> dict:
    """建立完全非互動式 Git 執行環境，嚴禁彈出 Windows GitHub Sign in 視窗"""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    env["GCM_GUI_PROMPT"] = "false"
    return env


def _run_git_cmd(args: list, extra_config: list = None, input_text: str = None, timeout: int = 25) -> subprocess.CompletedProcess:
    """執行 Git 子行程（不彈視窗、不鎖互動提示）"""
    cmd = ["git"] + (extra_config or []) + list(args)
    return subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        env=_git_env(),
        input=input_text,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _read_env_github_token() -> str:
    """優先從 .env 讀取 GITHUB_TOKEN，其次讀取行程環境變數"""
    if os.path.exists(ENV_FILE):
        try:
            with open(ENV_FILE, "r", encoding="utf-8", errors="replace") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k.strip() == "GITHUB_TOKEN":
                        tok = v.strip().strip('"').strip("'")
                        if tok:
                            return tok
        except Exception:
            pass
    return os.environ.get("GITHUB_TOKEN", "").strip().strip('"').strip("'")


def _upsert_env_var(key: str, value: str):
    """安全更新或新增 .env 內的指定鍵值，完整保留其他金鑰設定"""
    lines = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    new_line = f'{key}="{value}"'
    updated = False
    out_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k, _ = stripped.split("=", 1)
            if k.strip() == key:
                out_lines.append(new_line)
                updated = True
                continue
        out_lines.append(line)
    if not updated:
        out_lines.append(new_line)
    with open(ENV_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out_lines) + "\n")


def _parse_github_remote(url: str) -> tuple:
    """解析 GitHub HTTPS Remote URL，回傳 (clean_url, embedded_userinfo, repo_slug)"""
    raw = (url or "").strip()
    if not raw:
        return DEFAULT_REPO_URL, "", DEFAULT_REPO_SLUG
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme in ("http", "https") and host == "github.com":
        path = parsed.path or ""
        clean_url = f"https://github.com{path}"
        userinfo = ""
        if "@" in parsed.netloc:
            userinfo = unquote(parsed.netloc.rsplit("@", 1)[0])
        slug = path.strip("/")
        if slug.endswith(".git"):
            slug = slug[:-4]
        return clean_url, userinfo, (slug or DEFAULT_REPO_SLUG)
    return raw, "", DEFAULT_REPO_SLUG


def _verify_github_token_api(token: str, username: str = "", repo_slug: str = DEFAULT_REPO_SLUG) -> dict:
    """呼叫 GitHub REST API 驗證憑證是否有效且具備推送 (push) 權限"""
    tok = (token or "").strip()
    if not tok:
        return {"valid": False, "login": "", "scopes": "", "can_push": False, "status_code": 0, "error": "Token 為空"}
    try:
        import requests
        headers = {"Accept": "application/vnd.github+json"}
        if username:
            auth = (username, tok)
            req_kwargs = {"auth": auth, "headers": headers}
        else:
            headers["Authorization"] = f"Bearer {tok}"
            req_kwargs = {"headers": headers}

        r_user = requests.get("https://api.github.com/user", timeout=10, **req_kwargs)
        if r_user.status_code != 200:
            return {
                "valid": False,
                "login": "",
                "scopes": "",
                "can_push": False,
                "status_code": r_user.status_code,
                "error": f"GitHub 認證失敗 (HTTP {r_user.status_code}，Token 可能已過期或被撤銷)",
            }
        u_json = r_user.json()
        login = str(u_json.get("login", ""))
        scopes = str(r_user.headers.get("X-OAuth-Scopes", ""))

        r_repo = requests.get(f"https://api.github.com/repos/{repo_slug}", timeout=10, **req_kwargs)
        can_push = False
        if r_repo.status_code == 200:
            perms = r_repo.json().get("permissions", {}) or {}
            can_push = bool(perms.get("push") or perms.get("admin") or perms.get("maintain"))
        return {
            "valid": True,
            "login": login,
            "scopes": scopes or "(Fine-grained PAT)",
            "can_push": can_push,
            "status_code": r_repo.status_code,
            "error": "" if can_push else f"帳號 {login} 對 {repo_slug} 缺少寫入 (push) 權限",
        }
    except Exception as e:
        return {"valid": False, "login": "", "scopes": "", "can_push": False, "status_code": 0, "error": _mask_secrets(str(e))}


def _read_gcm_github_credential() -> tuple:
    """非互動式讀取 Windows Credential Manager 中已快取的 GitHub 憑證"""
    try:
        res = _run_git_cmd(
            ["credential", "fill"],
            input_text="protocol=https\nhost=github.com\n\n",
            timeout=8,
        )
        if res.returncode != 0:
            return "", ""
        kv = {}
        for line in (res.stdout or "").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                kv[k.strip()] = v.strip()
        return kv.get("username", ""), kv.get("password", "")
    except Exception:
        return "", ""


def normalize_git_remote_url(remote_name: str = "origin") -> dict:
    """
    清理 .git/config 中殘留的 `<token>@github.com` 前綴：
    Git 遇到 `https://<token>@github.com/...` 會將 Token 當成 username，
    導致 Windows Credential Manager 找不到對應帳號而彈出 Sign in 視窗。
    清理為標準 `https://github.com/owner/repo.git` 後即可正常使用 GCM 或 .env Token。
    """
    info = {"changed": False, "clean_url": DEFAULT_REPO_URL, "repo_slug": DEFAULT_REPO_SLUG, "migrated_token": False}
    try:
        res = _run_git_cmd(["remote", "get-url", remote_name], timeout=5)
        if res.returncode != 0:
            return info
        raw_url = (res.stdout or "").strip()
        clean_url, userinfo, slug = _parse_github_remote(raw_url)
        info["clean_url"] = clean_url
        info["repo_slug"] = slug
        if userinfo:
            # 若網址中剛好夾帶了一組仍然有效、且 .env 尚未儲存的 PAT，先轉存至 .env 再清理
            candidate = userinfo.split(":", 1)[1] if ":" in userinfo else userinfo
            if candidate.startswith(("ghp_", "github_pat_")) and not _read_env_github_token():
                check = _verify_github_token_api(candidate, repo_slug=slug)
                if check.get("valid") and check.get("can_push"):
                    _upsert_env_var("GITHUB_TOKEN", candidate)
                    os.environ["GITHUB_TOKEN"] = candidate
                    info["migrated_token"] = True

            set_res = _run_git_cmd(["remote", "set-url", remote_name, clean_url], timeout=5)
            if set_res.returncode == 0:
                info["changed"] = True
                print(f"[run_git_sync] 已自動將 remote `{remote_name}` 淨化為標準 HTTPS URL（消除 Sign in 彈窗風險）")
    except Exception as e:
        print(f"[normalize_git_remote_url] 提示: {_mask_secrets(str(e))}")
    return info


def _git_auth_config_for_token(token: str) -> list:
    """以一次性 Header 傳遞 PAT 並停用互動式 Credential Manager"""
    b64 = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
    return [
        "-c", "credential.helper=",
        "-c", "credential.interactive=never",
        "-c", f"http.https://github.com/.extraheader=AUTHORIZATION: basic {b64}",
    ]


def check_github_push_auth() -> dict:
    """非互動式檢測目前 GitHub 推播驗證健康狀態（供 UI 健檢卡片使用）"""
    remote_res = _run_git_cmd(["remote", "get-url", "origin"], timeout=5)
    raw_url = (remote_res.stdout or "").strip() if remote_res.returncode == 0 else ""
    clean_url, embedded_user, repo_slug = _parse_github_remote(raw_url)
    remote_clean = bool(raw_url) and not bool(embedded_user)

    env_token = _read_env_github_token()
    has_env = bool(env_token)
    env_check = _verify_github_token_api(env_token, repo_slug=repo_slug) if has_env else {"valid": False, "can_push": False}

    gcm_user, gcm_pw = _read_gcm_github_credential()
    has_gcm = bool(gcm_pw)
    gcm_check = _verify_github_token_api(gcm_pw, username=gcm_user, repo_slug=repo_slug) if has_gcm else {"valid": False, "can_push": False}

    if env_check.get("valid") and env_check.get("can_push"):
        return {
            "authenticated": True,
            "active_method": ".env GITHUB_TOKEN",
            "login": env_check.get("login", ""),
            "scopes": env_check.get("scopes", ""),
            "can_push": True,
            "remote_clean": remote_clean,
            "repo_slug": repo_slug,
            "has_env_token": has_env,
            "has_gcm_cred": has_gcm,
            "needs_repair": not remote_clean,
            "message": f"驗證正常（帳號: {env_check.get('login')} ｜ 來源: .env GITHUB_TOKEN ｜ 權限: {env_check.get('scopes')}）",
        }
    if gcm_check.get("valid") and gcm_check.get("can_push"):
        msg = f"驗證正常（帳號: {gcm_check.get('login')} ｜ 來源: Windows 憑證管理員 ｜ 權限: {gcm_check.get('scopes')}）"
        if has_env:
            msg += "；注意：.env 中的 GITHUB_TOKEN 已失效，建議清除或更新。"
        if not remote_clean:
            msg += "；注意：.git/config 含殘留舊前綴，推送時將自動淨化。"
        return {
            "authenticated": True,
            "active_method": "Windows Credential Manager (OAuth/GCM)",
            "login": gcm_check.get("login", ""),
            "scopes": gcm_check.get("scopes", ""),
            "can_push": True,
            "remote_clean": remote_clean,
            "repo_slug": repo_slug,
            "has_env_token": has_env,
            "has_gcm_cred": has_gcm,
            "needs_repair": (not remote_clean) or has_env,
            "message": msg,
        }

    reasons = []
    if not remote_clean and embedded_user:
        reasons.append(".git/config 殘留舊版 Token 前綴")
    if has_env:
        reasons.append(f".env GITHUB_TOKEN 無法驗證：{env_check.get('error', '無效')}")
    if has_gcm:
        reasons.append(f"Windows 憑證管理員無法驗證：{gcm_check.get('error', '無效')}")
    if not has_env and not has_gcm:
        reasons.append("尚未設定 .env GITHUB_TOKEN 或 Windows GitHub 憑證")
    return {
        "authenticated": False,
        "active_method": "無有效憑證",
        "login": "",
        "scopes": "",
        "can_push": False,
        "remote_clean": remote_clean,
        "repo_slug": repo_slug,
        "has_env_token": has_env,
        "has_gcm_cred": has_gcm,
        "needs_repair": True,
        "message": "；".join(reasons),
    }


def save_github_token_to_env(token: str) -> dict:
    """驗證使用者輸入之 GitHub Personal Access Token，儲存至 .env 並同步修復 Remote 與 Windows 憑證"""
    clean_tok = (token or "").strip().strip('"').strip("'")
    if not clean_tok:
        return {"ok": False, "message": "請輸入有效的 GitHub Personal Access Token！"}

    norm = normalize_git_remote_url()
    repo_slug = norm.get("repo_slug", DEFAULT_REPO_SLUG)
    chk = _verify_github_token_api(clean_tok, repo_slug=repo_slug)
    if not chk.get("valid"):
        return {"ok": False, "message": chk.get("error", "GitHub Token 驗證失敗，請確認未過期且輸入完整。")}
    if not chk.get("can_push"):
        return {"ok": False, "message": chk.get("error", "此 Token 缺少 repo 寫入權限，請勾選 repo 權限後重新產生。")}

    _upsert_env_var("GITHUB_TOKEN", clean_tok)
    os.environ["GITHUB_TOKEN"] = clean_tok
    login = chk.get("login") or "raylynn910"

    # 確保 .git/config 不殘留舊 token 前綴
    normalize_git_remote_url()

    # 同步刷新 Windows Credential Manager，確保終端機直接 git push 亦可使用最新 Token
    try:
        cred_payload = f"protocol=https\nhost=github.com\nusername={login}\npassword={clean_tok}\n\n"
        _run_git_cmd(["credential", "approve"], input_text=cred_payload, timeout=8)
    except Exception:
        pass

    return {
        "ok": True,
        "login": login,
        "scopes": chk.get("scopes", ""),
        "message": f"✅ Token 驗證通過並已儲存！（帳號: {login} ｜ 權限: {chk.get('scopes')} ｜ .env 與系統憑證已同步更新）",
    }


def _is_auth_failure_text(err_text: str) -> bool:
    low = (err_text or "").lower()
    markers = (
        "terminal prompts disabled",
        "cannot prompt",
        "authentication failed",
        "bad credentials",
        "invalid username or token",
        "could not read password",
        "could not read username",
        "logon failed",
        "returned error: 401",
        "returned error: 403",
        "permission to",
    )
    return any(m in low for m in markers)


def run_git_sync(data_date: str) -> dict:
    """執行 Git Add, Commit, Push；強制非互動式執行，絕不彈出 Sign in 視窗，並具備自動淨化與斷鏈診斷"""
    result = {
        "git_committed": False,
        "git_pushed": False,
        "auth_error": False,
        "commit_hash": "",
        "pushed_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "cloud_url": DEFAULT_CLOUD_URL,
        "message": ""
    }

    # 自我修復：檢查並清除 OneDrive 或歷史殘留之 index.lock
    lock_file = os.path.join(PROJECT_ROOT, ".git", "index.lock")
    if os.path.exists(lock_file):
        try:
            os.remove(lock_file)
            print("[run_git_sync] 偵測到殘留的 .git/index.lock，已自動安全清除！")
        except Exception as lock_e:
            print(f"[run_git_sync] 清除 index.lock 警示: {lock_e}")

    tracked_files = [
        "published_signals.json",
        "history_snapshots.json",
        "updated_mc_1113_summary.json",
        "family_app.py",
        "dashboard_ui.py",
        "components_weapons.py",
        "signal_publisher.py",
        "launcher.py",
        "db_utils.py",
        "daily_openapi_klines.py",
        "disposal_event_scanner.py",
        "download_disposals_history.py",
        "fetch_txf_klines.py",
        "pure_openapi_financials_backfill.py",
        "update_taiex.py",
        "moneydj_revenue_scraper.py",
        "api_server.py",
        "api_client.py",
        "data_providers/smart_sync_manager.py",
        "data_providers/four_factors_screener_service.py",
        "data_providers/price_adjuster.py",
        "data_providers/finmind_revenue_service.py",
        "data_providers/finmind_financials_service.py",
        "tools/tw_price_limit.py",
        "tools/market_calendar.py",
        "tools/__init__.py",
        ".gitignore"
    ]
    existing_files = [f for f in tracked_files if os.path.exists(os.path.join(PROJECT_ROOT, f))]

    try:
        # 1. git add (具備重試機制)
        add_res = _run_git_cmd(["add"] + existing_files, timeout=20)
        if add_res.returncode != 0:
            if os.path.exists(lock_file):
                try:
                    os.remove(lock_file)
                except Exception:
                    pass
            time.sleep(0.5)
            add_res = _run_git_cmd(["add"] + existing_files, timeout=20)

        if add_res.returncode != 0:
            result["message"] = f"Git Add 失敗: {_mask_secrets((add_res.stderr or '').strip())}"
            return result

        # 2. git commit
        commit_msg = f"Update daily signals ({data_date}) & holiday notice"
        commit_res = _run_git_cmd(["commit", "-m", commit_msg], timeout=20)
        stdout_str = (commit_res.stdout or "").strip()
        stderr_str = (commit_res.stderr or "").strip()
        if commit_res.returncode == 0:
            result["git_committed"] = True
        else:
            if "nothing to commit" in stdout_str or "clean" in stdout_str:
                result["git_committed"] = True
            else:
                result["message"] = f"Git Commit 提示: {_mask_secrets(stdout_str or stderr_str)}"

        # 取得最新 Commit Hash
        try:
            rev_res = _run_git_cmd(["rev-parse", "--short", "HEAD"], timeout=5)
            if rev_res.returncode == 0:
                result["commit_hash"] = (rev_res.stdout or "").strip()
        except Exception:
            pass

        # 3. 檢查並自動淨化遠端倉庫 (remote)
        remote_res = _run_git_cmd(["remote"], timeout=5)
        has_remote = bool((remote_res.stdout or "").strip())

        if not has_remote:
            result["git_pushed"] = False
            result["message"] = "本地 Git 記錄已成功提交。尚未設定遠端倉庫 (git remote)。"
            return result

        normalize_git_remote_url("origin")

        # 4. 非互動式 git push（先試 .env GITHUB_TOKEN 若有設定，否則使用 Windows Credential Manager；絕不彈窗）
        env_token = _read_env_github_token()
        push_res = None
        used_env_token = False
        stale_env_notice = ""

        if env_token:
            used_env_token = True
            push_res = _run_git_cmd(
                ["push", "origin", "main"],
                extra_config=_git_auth_config_for_token(env_token),
                timeout=25,
            )
            if push_res.returncode != 0 and _is_auth_failure_text((push_res.stderr or "") + (push_res.stdout or "")):
                # .env 中的 Token 若過期，自動嘗試 Windows Credential Manager 作為備援
                gcm_res = _run_git_cmd(
                    ["push", "origin", "main"],
                    extra_config=["-c", "credential.interactive=never"],
                    timeout=25,
                )
                if gcm_res.returncode == 0:
                    push_res = gcm_res
                    stale_env_notice = "（注意：.env 中的 GITHUB_TOKEN 已失效，本次已自動切換 Windows 憑證完成推送）"
        else:
            push_res = _run_git_cmd(
                ["push", "origin", "main"],
                extra_config=["-c", "credential.interactive=never"],
                timeout=25,
            )

        if push_res.returncode == 0:
            result["git_pushed"] = True
            result["message"] = f"✅ 訊號與前端已成功推播至 GitHub ({result['commit_hash']})！Streamlit.app 正自動熱重載！{stale_env_notice}"
            return result

        err_raw = ((push_res.stderr or "") + "\n" + (push_res.stdout or "")).strip()
        if _is_auth_failure_text(err_raw):
            result["git_pushed"] = False
            result["auth_error"] = True
            result["message"] = (
                "GitHub 推播驗證已過期或失效（系統已自動攔截彈窗以防卡死）。"
                "請展開下方「🔑 GitHub 雲端推播金鑰 / 連線健檢」貼上新版 Personal Access Token（勾選 repo 權限）即可恢復一鍵推播。"
            )
            return result

        # 非驗證錯誤（如暫時性網路波動）才重試一次
        time.sleep(1)
        retry_cfg = _git_auth_config_for_token(env_token) if used_env_token else ["-c", "credential.interactive=never"]
        push_res2 = _run_git_cmd(["push", "origin", "main"], extra_config=retry_cfg, timeout=25)
        if push_res2.returncode == 0:
            result["git_pushed"] = True
            result["message"] = f"✅ 訊號與前端已成功推播至 GitHub ({result['commit_hash']})！Streamlit.app 正自動熱重載！"
        else:
            err2 = ((push_res2.stderr or "") + "\n" + (push_res2.stdout or "")).strip()
            result["git_pushed"] = False
            result["auth_error"] = _is_auth_failure_text(err2)
            result["message"] = f"Git Push 失敗: {_mask_secrets(err2)}"

    except subprocess.TimeoutExpired:
        result["git_pushed"] = False
        result["message"] = "Git Push 連線逾時（25秒），請檢查網路連線或於「🔑 GitHub 推播連線健檢」確認驗證狀態。"
    except Exception as e:
        result["git_pushed"] = False
        result["message"] = f"Git 操作例外: {_mask_secrets(str(e))}"

    return result


if __name__ == "__main__":
    print("=" * 60)
    print("正在測試 signal_publisher 信號發布引擎...")
    res = export_and_publish_signals()
    print("發布結果:", json.dumps(res, ensure_ascii=False, indent=2))
    print("=" * 60)
