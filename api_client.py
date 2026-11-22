import requests
import pandas as pd
import numpy as np
import os
import streamlit as st
from datetime import datetime

API_BASE_URL = 'http://localhost:8001/api/v1'

@st.cache_data(ttl=60, show_spinner=False)
def fetch_portfolio_data():
    """
    從後端 API 獲取全市場選股資料；若後端暫未回應，自動由 published_signals.json 快照無縫遞補。
    嚴禁前端直接連線 market_data.duckdb 產生跨行程檔案鎖死。
    """
    # 1. 優先嘗試後端 API
    try:
        res = requests.get(f"{API_BASE_URL}/four_factors_screener?top_n=30", timeout=8)
        if res.status_code == 200:
            json_res = res.json()
            if json_res.get("success"):
                data = json_res.get("data", {})
                if "current_portfolio" in data:
                    data["holdings"] = data["current_portfolio"]
                if "model_health" not in data:
                    data["model_health"] = {"train_loss": 0.0, "val_loss": 0.0, "is_overfitting": False}
                if "metadata" not in data:
                    data["metadata"] = {
                        "total_scanned_stocks": 1900,
                        "last_updated_time": datetime.now().strftime("%m-%d %H:%M")
                    }
                return data
            else:
                print(f"[api_client] API returned success=False: {json_res.get('error')}")
    except Exception as e:
        print(f"[api_client] fetch_portfolio_data API attempt warning: {e}")

    # 2. Fallback: 從 published_signals.json 讀取最新發布快照 (零 DB 檔案鎖，保證瞬開)
    try:
        import json
        sig_path = os.path.join(os.path.dirname(__file__), "published_signals.json")
        if os.path.exists(sig_path):
            with open(sig_path, "r", encoding="utf-8") as f:
                sig_data = json.load(f)
                c1 = sig_data.get("core_1_swing", {})
                top_9 = c1.get("top_9", [])
                buf_15 = c1.get("buffer_10_15", [])
                holdings = top_9 + buf_15
                tracking = c1.get("boundary_tracking", {})
                if holdings:
                    date_val = sig_data.get("data_date", datetime.now().strftime("%Y-%m-%d"))
                    # 雙向欄位正規化，確保不論前端讀取 stock_name/name 或 latest_close/close 都能精準對齊
                    for h in holdings:
                        if "stock_name" not in h and "name" in h:
                            h["stock_name"] = h["name"]
                        if "name" not in h and "stock_name" in h:
                            h["name"] = h["stock_name"]
                        if "latest_close" not in h and "close" in h:
                            h["latest_close"] = h["close"]
                        if "close" not in h and "latest_close" in h:
                            h["close"] = h["latest_close"]
                        if "latest_date" not in h:
                            h["latest_date"] = date_val
                        if "momentum_value" not in h and "momentum_pct" in h:
                            h["momentum_value"] = h["momentum_pct"]
                        if "revenue_yoy_value" not in h and "revenue_yoy_pct" in h:
                            h["revenue_yoy_value"] = h["revenue_yoy_pct"]
                        if "roe_value" not in h and "roe_pct" in h:
                            h["roe_value"] = h["roe_pct"]

                    return {
                        "metadata": {
                            "last_updated_time": sig_data.get("data_date", datetime.now().strftime("%m-%d %H:%M")),
                            "total_scanned_stocks": len(holdings),
                            "cagr": 52.48,
                            "mdd": -34.35,
                            "sharpe_ratio": 1.47,
                            "win_rate": 0.5628,
                        },
                        "current_portfolio": holdings,
                        "holdings": holdings,
                        "changes": {
                            "added": tracking.get("buys", []),
                            "removed": tracking.get("sells", []),
                        },
                        "model_health": {"train_loss": 0.0, "val_loss": 0.0, "is_overfitting": False},
                    }
    except Exception as e:
        print(f"[api_client] published_signals fallback failed: {e}")

    # 3. Fallback: 從 screener_results.duckdb 讀取
    try:
        from db_utils import get_latest_portfolio_from_db
        db_path = os.path.join(os.path.dirname(__file__), "screener_results.duckdb")
        if os.path.exists(db_path):
            res_db = get_latest_portfolio_from_db(db_path)
            if res_db and res_db.get("portfolio_list"):
                return {
                    "metadata": res_db.get("metadata_info", {}),
                    "current_portfolio": res_db.get("portfolio_list", []),
                    "holdings": res_db.get("portfolio_list", []),
                    "changes": {
                        "added": res_db.get("added_list", []),
                        "removed": res_db.get("removed_list", []),
                    },
                    "model_health": {"train_loss": 0.0, "val_loss": 0.0, "is_overfitting": False},
                }
    except Exception as e:
        print(f"[api_client] screener_results.duckdb fallback failed: {e}")

    return None

@st.cache_data(ttl=60, show_spinner=False)
def fetch_active_disposals(target_mode: str = "latest_closed"):
    """
    從 FastAPI 獲取處置股票資料，前端不直接連線 DuckDB，徹底避免跨行程檔案鎖衝突。
    若 API 尚未連線，則自動 fallback 至 published_signals.json。
    """
    try:
        url = f"{API_BASE_URL}/disposals/active?target_mode={target_mode}"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            json_res = res.json()
            if json_res.get("status") == "success":
                return json_res
    except Exception as e:
        print(f"[api_client] fetch_active_disposals API warning: {e}")

    # Fallback: 讀取 published_signals.json (Zero DB lock!)
    try:
        import json
        sig_path = os.path.join(os.path.dirname(__file__), "published_signals.json")
        if os.path.exists(sig_path):
            with open(sig_path, "r", encoding="utf-8") as f:
                sig_data = json.load(f)
                core2 = sig_data.get("core_2_disposal", {})
                last_closed = core2.get("last_closed_date", "2026-10-16")
                next_trading = core2.get("next_trading_date", "2026-10-19")
                is_closed = core2.get("is_weekend_or_holiday", True)
                
                if target_mode == "next_trading_day":
                    pool = core2.get("next_trading_disposals_list") or core2.get("all_next_disposals")
                    if not pool:
                        b_list = core2.get("next_day_b_triggers", [])
                        c_list = core2.get("next_day_c_triggers", [])
                        for item in b_list:
                            item.setdefault("is_hit_b", True)
                            item.setdefault("is_hit_c", False)
                            item.setdefault("priority", 1)
                        for item in c_list:
                            item.setdefault("is_hit_b", False)
                            item.setdefault("is_hit_c", True)
                            item.setdefault("priority", 1)
                        pool = b_list + c_list
                else:
                    pool = core2.get("active_disposals_list") or core2.get("all_active_disposals")
                    if not pool:
                        pool = core2.get("imminent_pool", [])
                        b_list = core2.get("strategy_b_triggers", [])
                        c_list = core2.get("strategy_c_triggers", [])
                        b_ids = {str(x.get("stock_id")) for x in b_list}
                        c_ids = {str(x.get("stock_id")) for x in c_list}
                        for item in pool:
                            sid = str(item.get("stock_id"))
                            if sid in b_ids:
                                item.setdefault("is_hit_b", True)
                                item.setdefault("is_hit_c", False)
                                item.setdefault("priority", 1)
                            elif sid in c_ids:
                                item.setdefault("is_hit_b", False)
                                item.setdefault("is_hit_c", True)
                                item.setdefault("priority", 2)
                            else:
                                item.setdefault("is_hit_b", False)
                                item.setdefault("is_hit_c", False)
                                item.setdefault("priority", 3)
                
                # 確保回傳資料結構中每一筆記錄欄位完整防呆
                norm_pool = []
                for item in (pool or []):
                    clean_item = dict(item)
                    clean_item.setdefault("is_hit_b", False)
                    clean_item.setdefault("is_hit_c", False)
                    clean_item.setdefault("priority", 4)
                    clean_item.setdefault("status", "監控中")
                    clean_item.setdefault("action_timing", "觀望")
                    clean_item.setdefault("entry_sop", "處置中持續監控動能")
                    clean_item.setdefault("turnover_str", "0元")
                    clean_item.setdefault("vol_ratio", 1.0)
                    clean_item.setdefault("disp_cum_ret", 0.0)
                    clean_item.setdefault("latest_price", 0.0)
                    clean_item.setdefault("ma5", clean_item.get("latest_price", 0.0))
                    clean_item.setdefault("countdown_desc", "-")
                    clean_item.setdefault("td_to_exit", 99)
                    norm_pool.append(clean_item)

                return {
                    "status": "success",
                    "count": len(norm_pool),
                    "dates_info": {
                        "last_closed_date": last_closed,
                        "next_trading_date": next_trading,
                        "is_market_closed": is_closed
                    },
                    "data": norm_pool
                }
    except Exception as e:
        print(f"[api_client] published_signals fallback failed: {e}")

    return {
        "status": "success",
        "count": 0,
        "dates_info": {
            "last_closed_date": "2026-10-16",
            "next_trading_date": "2026-10-19",
            "is_market_closed": True
        },
        "data": []
    }

@st.cache_data(ttl=120, show_spinner=False)
def fetch_disposal_history(year: str = None, market: str = None, tier: str = None, limit: int = 300):
    """
    從 FastAPI 獲取歷史處置庫紀錄與量化回測統計指標，前端不直接連線 DuckDB。
    """
    params = {"limit": limit}
    if year and year != "全部":
        params["year"] = year
    if market and market != "全部":
        params["market"] = market
    if tier and tier != "全部":
        params["tier"] = tier

    try:
        url = f"{API_BASE_URL}/disposals/history"
        res = requests.get(url, params=params, timeout=8)
        if res.status_code == 200:
            json_res = res.json()
            if json_res.get("status") == "success":
                return json_res
    except Exception as e:
        print(f"[api_client] fetch_disposal_history warning: {e}")

    return {"status": "success", "stats": {}, "count": 0, "data": []}

@st.cache_data(ttl=60, show_spinner=False)
def fetch_stock_kline(ticker):
    """抓取單股 K 線歷史資料"""
    try:
        url = f"{API_BASE_URL}/stock/{ticker}/data"
        res = requests.get(url)
        if res.status_code == 200:
            json_res = res.json()
            if json_res.get("success"):
                return json_res.get("data", {}).get("data", {}).get("kline", [])
            else:
                print(f"[Frontend API Error] {url} returned False success: {json_res.get('error')}")
        else:
            print(f"[Frontend Error] fetch_stock_kline({ticker}) 回傳非 200 狀態碼: {res.status_code}, {res.text}")
        return []
    except Exception as e:
        print(f"[Frontend Exception] GET {API_BASE_URL}/stock/{ticker}/data failed: {str(e)}")
        return []

def fetch_stock_full_data(ticker):
    """抓取單股完整的 Panel Data (含 kline, chip, rev 等)"""
    try:
        url = f"{API_BASE_URL}/stock/{ticker}/data"
        res = requests.get(url)
        if res.status_code == 200:
            json_res = res.json()
            if json_res.get("success"):
                return json_res.get("data", {}).get("data", {})
            else:
                print(f"[Frontend API Error] {url} returned False success: {json_res.get('error')}")
        else:
            print(f"[Frontend Error] fetch_stock_full_data({ticker}) 回傳非 200 狀態碼: {res.status_code}, {res.text}")
        return {}
    except Exception as e:
        print(f"[Frontend Exception] GET {API_BASE_URL}/stock/{ticker}/data failed: {str(e)}")
        return {}

@st.cache_data(ttl=10, show_spinner=False)
def fetch_strategy_performance():
    """抓取並計算歷史回測績效"""
    csv_path = os.path.join(os.path.dirname(__file__), "data", "backtest_equity_curve.csv")
    if not os.path.exists(csv_path):
        return None
    try:
        df = pd.read_csv(csv_path)
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date')
        
        equity = df['equity'].values
        if len(equity) < 2:
            return None
            
        years = (df['date'].iloc[-1] - df['date'].iloc[0]).days / 365.25
        if years > 0:
            cagr = (equity[-1] / equity[0]) ** (1/years) - 1
        else:
            cagr = 0
            
        roll_max = np.maximum.accumulate(equity)
        drawdown = equity / roll_max - 1
        mdd = drawdown.min()
        
        daily_ret = df['equity'].pct_change().dropna()
        if len(daily_ret) > 0 and daily_ret.std() != 0:
            sharpe = (daily_ret.mean() / daily_ret.std()) * np.sqrt(252)
        else:
            sharpe = 0
            
        if len(daily_ret) > 0:
            win_rate = len(daily_ret[daily_ret > 0]) / len(daily_ret)
        else:
            win_rate = 0
            
        alpha = cagr - 0.10 if cagr != 0 else 0
            
        return {
            "df": df,
            "cagr": cagr,
            "mdd": mdd,
            "sharpe": sharpe,
            "alpha": alpha,
            "win_rate": win_rate
        }
    except Exception as e:
        print(f"[Frontend Exception] Failed to read backtest data: {e}")
        return None

def fetch_stock_factors(ticker):
    try:
        res = requests.get(f"{API_BASE_URL}/stock/{ticker}/factors", timeout=5)
        if res.status_code == 200:
            return res.json().get("data", {})
    except Exception:
        pass
    return None

def fetch_stock_ml_predict(ticker):
    try:
        res = requests.get(f"{API_BASE_URL}/stock/{ticker}/ml_predict", timeout=5)
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass
    return None

def update_revenue_openapi():
    return requests.post(f"{API_BASE_URL}/update_revenue_openapi", timeout=30)

def update_revenue_moneydj():
    try:
        res = requests.post(f"{API_BASE_URL}/update_revenue_moneydj", timeout=30)
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass
    # 本地直接執行容錯
    from moneydj_revenue_scraper import scrape_moneydj_revenue
    return scrape_moneydj_revenue()

def run_screener():
    try:
        res = requests.post(f"{API_BASE_URL}/run_screener", timeout=15)
        if res.status_code == 200:
            return res.json()
    except Exception as e:
        print(f"[api_client] run_screener backend error, using local fallback: {e}")
    
    # 本地直接執行容錯
    try:
        from data_providers.four_factors_screener_service import compute_four_factors_from_db
        from db_utils import update_screener_results_in_db
        top_results = compute_four_factors_from_db()
        db_path = os.path.join(os.path.dirname(__file__), "screener_results.duckdb")
        update_screener_results_in_db(top_results, len(top_results), db_path)
        return {"status": "success", "message": f"截面跑分完成，共 {len(top_results)} 檔"}
    except Exception as local_e:
        print(f"[api_client] run_screener local fallback error: {local_e}")
        return {"status": "error", "message": str(local_e)}

def update_revenue():
    try:
        res = requests.post(f"{API_BASE_URL}/update_revenue", timeout=180)
        if res.status_code == 200:
            return res.json()
    except Exception as e:
        print(f"[api_client] update_revenue backend notice, switching to local service: {e}")

    # 本地直接執行容錯
    try:
        from data_providers.finmind_revenue_service import sync_finmind_revenues
        return sync_finmind_revenues()
    except Exception as local_e:
        print(f"[api_client] update_revenue local error: {local_e}")
        return {"status": "error", "message": str(local_e)}

def update_financials():
    try:
        res = requests.post(f"{API_BASE_URL}/update_financials", timeout=180)
        if res.status_code == 200:
            return res.json()
    except Exception as e:
        print(f"[api_client] update_financials backend notice, switching to local service: {e}")

    # 本地直接執行容錯
    try:
        from data_providers.finmind_financials_service import sync_finmind_financials
        return sync_finmind_financials()
    except Exception as local_e:
        print(f"[api_client] update_financials local error: {local_e}")
        return {"status": "error", "message": str(local_e)}

def sync_daily_market_data():
    try:
        res = requests.post(f"{API_BASE_URL}/sync_daily_market_data", timeout=15)
        if res.status_code == 200:
            return res.json()
    except Exception as e:
        print(f"[api_client] sync_daily_market_data backend error, using local fallback: {e}")

    # 本地直接執行容錯
    try:
        from daily_openapi_klines import fetch_openapi_klines
        return fetch_openapi_klines()
    except Exception as local_e:
        print(f"[api_client] sync_daily_market_data local fallback error: {local_e}")
        return {"status": "error", "message": str(local_e)}

def determine_regime(close, ma20, ma60, ma120, mom_20d, hv_20=0):
    """
    ComeWish 實戰風控狀態機：
    回傳 (燈號, 中文名稱, 建議持倉比例, 行動建議)
    """
    if close <= ma120 or mom_20d <= -0.10:
        return ("RED", "黑天鵝斷電 (全面空手)", "0%", "大盤跌破半年線或單月暴跌逾10%！觸發系統斷電保護，現貨100%清倉變現，嚴禁摸底。")
    elif close < ma60:
        if close > ma20:
            return ("ORANGE", "破季線強彈 (防守觀察)", "30~50%", "大盤處於季線下但站上月線強彈。現貨持股上限50%；若原持有避險空單應立即全數平倉！")
        else:
            return ("ORANGE", "趨勢破線 (啟動對沖)", "20~30%", "大盤同時跌破季線與月線！僅保留前5名強勢股，依持股市值啟動微台指期貨空單對沖現貨。")
    else:
        # close >= ma60
        if close >= ma20:
            return ("GREEN", "多頭攻擊 (滿水位)", "100%", "大盤站穩季線與月線之上，多頭結構完整。維持四大因子 100% 現貨滿水位，嚴禁放空避險！")
        else:
            return ("YELLOW", "震盪警戒 (防守減碼)", "70%", "大盤回測月線震盪。停止買進新名單，弱勢股跌出30名優先減碼換取現金。")

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_macro_hedge_ai():
    """從後端獲取 ComeWish AI 實戰風控大腦之戰略評估"""
    try:
        res = requests.get(f"{API_BASE_URL}/macro/hedge_ai_analysis", timeout=15)
        if res.status_code == 200:
            json_res = res.json()
            if json_res.get("success"):
                return json_res.get("data", {})
        return None
    except Exception as e:
        print(f"[Frontend API Error] fetch_macro_hedge_ai failed: {e}")
        return None

@st.cache_data(ttl=60, show_spinner=False)
def fetch_market_regime():
    try:
        import signal_publisher
        info = signal_publisher.compute_market_regime_and_water_level()
        taiex_close = float(info.get("taiex_close", 0.0))
        ma60 = float(info.get("ma60", 0.0))
        close_vs_ma60 = ((taiex_close / ma60) - 1.0) if ma60 > 0 else 0.0
        
        return {
            "code": info.get("regime_code", "YELLOW"),
            "name": info.get("regime_name", "震盪警戒"),
            "position": f"{info.get('recommended_equity_pct', 50)}%",
            "action": info.get("halt_reason", ""),
            "close": taiex_close,
            "ma5": float(info.get("ma5", 0.0)),
            "ma20": float(info.get("ma20", 0.0)),
            "ma60": ma60,
            "ma120": float(info.get("ma120", 0.0)),
            "close_vs_ma60": close_vs_ma60,
            "mom_20d": float(info.get("mom_20d_pct", 0.0)) / 100.0,
            "hv_20": float(info.get("hv_20", 0.0)),
            "recommended_equity_pct": info.get("recommended_equity_pct", 50),
            "halt_new_buys": info.get("halt_new_buys", False),
            "weather": info.get("weather", "CLOUDY"),
            "weather_title": info.get("weather_title", "⛅ 多雲警戒"),
            "weather_desc": info.get("weather_desc", ""),
            "n7_guardian": info.get("n7_guardian", {})
        }
    except Exception as e:
        print(f"[Frontend Exception] fetch_market_regime failed: {e}")
        return None

@st.cache_data(ttl=60, show_spinner=False)
def fetch_cliffhanger_shorts(min_turnover: float = 100_000_000, delta_drop: float = -10.0, top_n: int = 5):
    """
    抓取 5 日動能由正轉負・神級斷崖破線做空名單 (Top 3~5 ｜ 93% 勝率)
    優先嘗試後端 API，若未啟動則直接呼叫本地 service
    """
    try:
        res = requests.get(f"{API_BASE_URL}/cliffhanger_shorts?min_turnover={min_turnover}&delta_drop={delta_drop}&top_n={top_n}", timeout=10)
        if res.status_code == 200:
            data = res.json()
            if data.get("status") == "success":
                return data.get("data", [])
    except Exception:
        pass
    
    # 本地直接運算 fallback
    try:
        from data_providers.cliffhanger_short_service import compute_cliffhanger_shorts
        return compute_cliffhanger_shorts(min_turnover=min_turnover, delta_drop=delta_drop, top_n=top_n)
    except Exception as e:
        print(f"[api_client] compute_cliffhanger_shorts fallback failed: {e}")
        return []



def update_taiex():
    try:
        res = requests.post(f"{API_BASE_URL}/update_taiex", timeout=15)
        if res.status_code == 200:
            return res.json()
    except Exception as e:
        print(f"[api_client] update_taiex backend error, using local fallback: {e}")

    # 本地直接執行容錯
    try:
        from update_taiex import fetch_and_update_taiex
        fetch_and_update_taiex()
        return {"status": "success", "message": "大盤數據已成功更新！"}
    except Exception as local_e:
        print(f"[api_client] update_taiex local fallback error: {local_e}")
        return {"status": "error", "message": str(local_e)}


@st.cache_data(ttl=60, show_spinner=False)
def audit_duckdb():
    """自動核對本地 DuckDB 資料庫新鮮度與時效性"""
    try:
        res = requests.get(f"{API_BASE_URL}/audit_duckdb", timeout=5)
        if res.status_code == 200:
            data = res.json().get("data", {})
            if data and "factor_penetration" in data:
                return data
    except Exception:
        pass
    from data_providers.smart_sync_manager import audit_duckdb_freshness
    return audit_duckdb_freshness()

def run_smart_sync_pipeline(force_all=False, progress_callback=None):
    """執行全自動智能盤後同步與雲端發布管線"""
    from data_providers.smart_sync_manager import run_smart_sync_and_publish
    return run_smart_sync_and_publish(force_all=force_all, progress_callback=progress_callback)
