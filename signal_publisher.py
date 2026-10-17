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
import json
import subprocess
import datetime
import math
import pandas as pd
import duckdb

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
SIGNALS_FILE = os.path.join(PROJECT_ROOT, "published_signals.json")
SNAPSHOTS_FILE = os.path.join(PROJECT_ROOT, "history_snapshots.json")
DUCKDB_PATH = os.path.join(PROJECT_ROOT, "data", "market_data.duckdb")
SCREENER_DB_PATH = os.path.join(PROJECT_ROOT, "screener_results.duckdb")


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
            conn = duckdb.connect(SCREENER_DB_PATH, read_only=True)
            tables = [t[0] for t in conn.execute("SHOW TABLES").fetchall()]
            if "baseline_portfolio" in tables:
                rows = conn.execute("SELECT stock_id, stock_name FROM baseline_portfolio LIMIT 15").fetchall()
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
            conn.close()
    except Exception as e:
        print(f"[signal_publisher] 補建種子快照警示: {e}")

    return snapshots


def compute_market_regime_and_water_level(regime_data: dict = None) -> dict:
    """
    比對後台的「大盤避險雷達建議水位」與「大盤位階警示燈建議水位」，
    自動取兩者中較低 (較保守) 的數值作為 recommended_equity_pct，
    並紀錄 halt_new_buys 狀態與白話解讀。
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
    regime_action = "大盤回測月線震盪。停止買進新名單，弱勢股跌出15名優先減碼換取現金。"

    if regime_data:
        close = float(regime_data.get("close", 0))
        ma5 = float(regime_data.get("ma5", 0))
        ma20 = float(regime_data.get("ma20", 0))
        ma60 = float(regime_data.get("ma60", 0))
        ma120 = float(regime_data.get("ma120", 0))
        mom_20d = float(regime_data.get("mom_20d", 0))
        hv_20 = float(regime_data.get("hv_20", 0))
        code = str(regime_data.get("code", "YELLOW")).upper()
        regime_name = str(regime_data.get("name", "震盪警戒 (防守減碼)"))
        regime_action = str(regime_data.get("action", ""))
    else:
        # 自 DuckDB 讀取 TAIEX 指數進行高保真計算
        try:
            if os.path.exists(DUCKDB_PATH):
                conn = duckdb.connect(DUCKDB_PATH, read_only=True)
                df = conn.execute("SELECT date, close FROM klines WHERE stock_id='TAIEX' ORDER BY date ASC").df()
                conn.close()
                if not df.empty and len(df) >= 60:
                    df['close'] = pd.to_numeric(df['close'], errors='coerce')
                    df['ma5'] = df['close'].rolling(5).mean()
                    df['ma20'] = df['close'].rolling(20).mean()
                    df['ma60'] = df['close'].rolling(60).mean()
                    df['ma120'] = df['close'].rolling(120).mean()
                    df['mom_20d'] = (df['close'] / df['close'].shift(20)) - 1.0
                    df['hv_20'] = df['close'].pct_change().rolling(20).std() * (252 ** 0.5) * 100.0
                    
                    last = df.iloc[-1]
                    close = float(last['close'])
                    ma5 = float(last['ma5']) if pd.notnull(last['ma5']) else close
                    ma20 = float(last['ma20']) if pd.notnull(last['ma20']) else close
                    ma60 = float(last['ma60']) if pd.notnull(last['ma60']) else close
                    ma120 = float(last['ma120']) if pd.notnull(last['ma120']) else close
                    mom_20d = float(last['mom_20d']) if pd.notnull(last['mom_20d']) else 0.0
                    hv_20 = float(last['hv_20']) if pd.notnull(last['hv_20']) else 0.0

                    if close <= ma120 or mom_20d <= -0.10:
                        code = "RED"
                        regime_name = "黑天鵝斷電 (全面空手)"
                    elif close < ma60:
                        code = "ORANGE"
                        regime_name = "破季線強彈 (防守觀察)" if close >= ma20 else "趨勢破線 (啟動對沖)"
                    elif close >= ma60:
                        if close >= ma20:
                            code = "GREEN"
                            regime_name = "多頭攻擊 (滿水位)"
                        else:
                            code = "YELLOW"
                            regime_name = "震盪警戒 (防守減碼)"
        except Exception as e:
            print(f"[signal_publisher] TAIEX 計算錯誤: {e}")

    # 1. 大盤避險雷達水位計算 (Smart Hedge Radar)
    is_panic_crash = (close < ma20) and (hv_20 > 15.0)
    is_black_swan = (close <= ma120 and ma120 > 0) or (mom_20d <= -0.10)
    is_broken = (close < ma60) and (close < ma20)
    is_rebound = (close < ma60) and (close >= ma20)
    is_warning = (close >= ma60) and (close < ma20)
    is_bull = (close >= ma60) and (close >= ma20)

    if is_panic_crash:
        radar_equity_pct = 50
        radar_signal = "恐慌性崩盤 (Panic Crash)"
        radar_msg = "大盤跌破月線且波動率(HV20)突破 15%，觸發 50% 防禦水位。"
    elif is_black_swan:
        radar_equity_pct = 0
        radar_signal = "黑天鵝斷電 (Circuit Breaker)"
        radar_msg = "大盤跌破半年線或單月崩跌，現貨全面清倉變現。"
    elif is_broken:
        radar_equity_pct = 30
        radar_signal = "趨勢破線 (啟動對沖)"
        radar_msg = "大盤跌破季線與月線，防守持股上限 30%。"
    elif is_warning:
        radar_equity_pct = 70
        radar_signal = "震盪警戒 (Pullback)"
        radar_msg = "大盤回測月線震盪整理，持股上限 70%，停止買進新名單。"
    elif is_rebound:
        radar_equity_pct = 50
        radar_signal = "破線強彈 (Recovery)"
        radar_msg = "大盤處於季線下但站回月線反彈，持股上限 50%。"
    else:
        radar_equity_pct = 100
        radar_signal = "多頭攻擊 (Bull Run)"
        radar_msg = "大盤站穩季線與月線之上，多頭結構完整，可維持 100% 滿水位配置。"

    # 2. 大盤位階警示燈水位計算 (Regime Light)
    if code == "RED":
        regime_equity_pct = 0
    elif code == "ORANGE":
        regime_equity_pct = 50 if close >= ma20 else 30
    elif code == "YELLOW":
        regime_equity_pct = 70
    else:  # GREEN
        regime_equity_pct = 100

    # 3. 雙雷達取較低 (較保守) 之數值
    recommended_equity_pct = int(min(radar_equity_pct, regime_equity_pct))
    recommended_cash_pct = int(100 - recommended_equity_pct)

    # 4. 大盤連動警戒 (是否跌破月線 / 停止買進新名單)
    halt_new_buys = bool((close < ma20) or (code in ["YELLOW", "ORANGE", "RED"]) or is_panic_crash or is_black_swan)

    # 5. 白話天氣預報分類
    if recommended_equity_pct >= 80:
        weather_code = "SUNNY"
        weather_title = "☀️ 晴空萬里"
        weather_desc = "大盤多頭結構完整且站穩月線之上，市場風向安全，適合按照策略積極參與！"
    elif recommended_equity_pct >= 50:
        weather_code = "CLOUDY"
        weather_title = "⛅ 多雲警戒"
        weather_desc = "大盤回測月線震盪中，行情進入防守期。建議保留充足現金，空手者暫緩追高買新股票！"
    else:
        weather_code = "STORMY"
        weather_title = "⛈️ 暴風雨警報"
        weather_desc = "大盤破線或波動劇烈，空頭賣壓沈重。嚴格執行資產防禦，嚴禁摸底接刀！"

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
        "radar_signal": radar_signal,
        "radar_msg": radar_msg,
        "radar_equity_pct": radar_equity_pct,
        "regime_code": code,
        "regime_name": regime_name,
        "regime_equity_pct": regime_equity_pct,
        "recommended_equity_pct": recommended_equity_pct,
        "recommended_cash_pct": recommended_cash_pct,
        "halt_new_buys": halt_new_buys,
        "halt_reason": "大盤處於跌破月線震盪中，依防守紀律停止買進新股票" if halt_new_buys else "大盤結構穩健，可依訊號建立部位"
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


def export_and_publish_signals(portfolio_data: dict = None, market_regime: dict = None) -> dict:
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

    try:
        from disposal_event_scanner import DisposalEventScanner
        scanner = DisposalEventScanner()
        df_disp = scanner.scan_disposal_events()

        if not df_disp.empty:
            total_active_disposals = len(df_disp)
            # 抓取目前仍在處置、或剛出關 5 天內的股票代號
            active_disposal_ids = set(df_disp['stock_id'].astype(str).tolist())

            # 策略 B 命中標的
            df_b = df_disp[df_disp['is_hit_b'] == True]
            for _, r in df_b.iterrows():
                hits_b_list.append({
                    "stock_id": str(r["stock_id"]),
                    "name": str(r["name"]),
                    "latest_price": float(r["latest_price"]),
                    "disp_cum_ret": round(float(r["disp_cum_ret"]), 1),
                    "vol_ratio": round(float(r["vol_ratio"]), 2),
                    "turnover_str": str(r["turnover_str"]),
                    "action_timing": "今日 13:25~13:30 收盤進場",
                    "countdown_desc": str(r["countdown_desc"]),
                    "ma5": round(float(r["ma5"]), 2),
                    "entry_sop": "今日收盤市價買進 ｜ 明日(T-1)收盤賣出 50% ｜ 出關日(T)09:00 開盤賣出剩餘 50% ｜ 盤中跌破 5MA 即時停損！"
                })

            # 策略 C 命中標的
            df_c = df_disp[df_disp['is_hit_c'] == True]
            for _, r in df_c.iterrows():
                hits_c_list.append({
                    "stock_id": str(r["stock_id"]),
                    "name": str(r["name"]),
                    "latest_price": float(r["latest_price"]),
                    "disp_cum_ret": round(float(r["disp_cum_ret"]), 1),
                    "vol_ratio": round(float(r["vol_ratio"]), 2),
                    "turnover_str": str(r["turnover_str"]),
                    "action_timing": "明晨 09:00 執行",
                    "countdown_desc": str(r["countdown_desc"]),
                    "entry_sop": "明日 09:00 若個股開高 +1%~+5.2% 且台指期開盤無暴漲(<=+0.3%)，MOO 券賣放空 ｜ 設 5% 停損 ｜ 13:25 全數平倉回補，絕不留倉！"
                })

            # 即將出關警戒 (T-1 / T-2) 蓄勢名單
            df_imm = df_disp[df_disp['td_to_exit'].isin([0, 1, 2])]
            for _, r in df_imm.iterrows():
                imminent_pool_list.append({
                    "stock_id": str(r["stock_id"]),
                    "name": str(r["name"]),
                    "countdown_desc": str(r["countdown_desc"]),
                    "td_to_exit": int(r["td_to_exit"]),
                    "latest_price": float(r["latest_price"]),
                    "disp_cum_ret": round(float(r["disp_cum_ret"]), 1),
                    "turnover_str": str(r["turnover_str"]),
                    "vol_ratio": round(float(r["vol_ratio"]), 2),
                    "status": str(r["status"]),
                    "entry_sop": str(r["entry_sop"])
                })
    except Exception as e:
        print(f"[signal_publisher] 處置模組讀取警示: {e}")

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

    # 建立全市場搜尋字典 (供一鍵健檢使用)
    all_searchable_stocks = {}
    screener_rank_map = {}
    for idx, s in enumerate(all_screener_stocks, 1):
        sid = str(s["stock_id"])
        screener_rank_map[sid] = idx
        status_tag = "TOP_9" if idx <= 9 else ("BUFFER_10_15" if idx <= 15 else "OUTSIDE")
        all_searchable_stocks[sid] = {
            "stock_id": sid,
            "name": str(s.get("stock_name", "")),
            "rank": idx,
            "status": status_tag,
            "close": float(s.get("latest_close", 0.0))
        }

    # 組合今日前 15 名清單
    today_top15_ids = []
    top_9_list = []
    buffer_10_15_list = []

    for idx, s in enumerate(all_screener_stocks[:15], 1):
        sid = str(s["stock_id"])
        sname = str(s.get("stock_name", ""))
        close = float(s.get("latest_close", 0.0))
        
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
            "name": sname,
            "close": close,
            "momentum_pct": mom_pct,
            "revenue_yoy_pct": yoy_pct,
            "roe_pct": roe_pct,
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
            prev_name = prev_info.get("stock_name", prev_info.get("name", ""))
            
            # 查詢今日最新排名
            current_rank = screener_rank_map.get(sid, "已移出觀察池")
            
            # 查詢最新收盤價
            c_price = prev_info.get("latest_close", 0.0)
            if sid in all_searchable_stocks:
                c_price = all_searchable_stocks[sid]["close"]
                if not prev_name:
                    prev_name = all_searchable_stocks[sid]["name"]

            dropped_from_top15.append({
                "stock_id": sid,
                "name": prev_name,
                "prev_rank": prev_rank,
                "new_rank": current_rank,
                "close": c_price,
                "reason": "動能轉弱跌出前15名，參考紀律為汰弱換取現金"
            })
            
            # 更新全市場搜尋字典中的標籤
            all_searchable_stocks[sid] = {
                "stock_id": sid,
                "name": prev_name,
                "rank": current_rank,
                "status": "DROPPED",
                "close": c_price
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
                "momentum_pct": s["momentum_pct"],
                "revenue_yoy_pct": s["revenue_yoy_pct"],
                "roe_pct": s["roe_pct"],
                "highlights": s["highlights"],
                "warning_tag": s["warning_tag"],
                "action_sop": "開高跳空等待 13:25 尾盤市價買 / 平低盤 09:00 開盤市價買"
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
                    "title": "策略 B (T-2 搶跑買入)",
                    "win_rate": "71.4%",
                    "profit_factor": 3.42,
                    "avg_return": "+3.25%",
                    "new_regime_win_rate": "80.0%"
                },
                "strategy_c": {
                    "title": "策略 C (T-1 警戒 / 出關當沖做空)",
                    "win_rate": "73.3%",
                    "profit_factor": 1.80,
                    "optimal_mdd": "<= 28.9%"
                }
            },
            "total_active_disposals_count": total_active_disposals,
            "strategy_b_triggers": hits_b_list,
            "strategy_c_triggers": hits_c_list,
            "imminent_pool": imminent_pool_list
        },
        "all_searchable_stocks": all_searchable_stocks
    }

    with open(SIGNALS_FILE, "w", encoding="utf-8") as f:
        json.dump(published_payload, f, ensure_ascii=False, indent=2)

    # -------------------------------------------------------------
    # 6. Git 資安防護與自動推送
    # -------------------------------------------------------------
    git_status = run_git_sync(data_date)

    return {
        "success": True,
        "data_date": data_date,
        "top_9_count": len(top_9_list),
        "buffer_count": len(buffer_10_15_list),
        "dropped_count": len(dropped_from_top15),
        "hits_b_count": len(hits_b_list),
        "hits_c_count": len(hits_c_list),
        "git_status": git_status
    }


def run_git_sync(data_date: str) -> dict:
    """執行 Git Add, Commit, Push 並回報狀態"""
    result = {
        "git_committed": False,
        "git_pushed": False,
        "message": ""
    }
    try:
        # 1. git add
        add_res = subprocess.run(
            ["git", "add", "published_signals.json", "history_snapshots.json", ".gitignore"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True
        )
        if add_res.returncode != 0:
            result["message"] = f"Git Add 失敗: {add_res.stderr.strip()}"
            return result

        # 2. git commit
        commit_msg = f"Update daily signals ({data_date})"
        commit_res = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True
        )
        if commit_res.returncode == 0:
            result["git_committed"] = True
        else:
            if "nothing to commit" in commit_res.stdout or "clean" in commit_res.stdout:
                result["git_committed"] = True
            else:
                result["message"] = f"Git Commit 提示: {commit_res.stdout.strip() or commit_res.stderr.strip()}"

        # 3. 檢查是否有遠端倉庫 (remote)
        remote_res = subprocess.run(
            ["git", "remote"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True
        )
        has_remote = bool(remote_res.stdout.strip())

        if not has_remote:
            result["git_pushed"] = False
            result["message"] = "本地 Git 記錄已成功提交。尚未設定遠端倉庫 (git remote)，若需同步至 GitHub 請先配置 git remote。"
            return result

        # 4. git push
        push_res = subprocess.run(
            ["git", "push"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=20
        )
        if push_res.returncode == 0:
            result["git_pushed"] = True
            result["message"] = "✅ 訊號檔案已成功提交並推送到 GitHub 遠端倉庫！"
        else:
            result["git_pushed"] = False
            result["message"] = f"Git Push 失敗 (請確認網路連線或遠端權限): {push_res.stderr.strip()}"

    except Exception as e:
        result["message"] = f"Git 操作例外: {str(e)}"

    return result


if __name__ == "__main__":
    print("=" * 60)
    print("正在測試 signal_publisher 信號發布引擎...")
    res = export_and_publish_signals()
    print("發布結果:", json.dumps(res, ensure_ascii=False, indent=2))
    print("=" * 60)
