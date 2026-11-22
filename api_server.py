from fastapi import FastAPI, HTTPException, Request, Path
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List, Generic, TypeVar
import json
import sys
import os
from datetime import datetime

_project_root = os.path.dirname(os.path.abspath(__file__))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# 導入原本的資料與分析層
from data_providers.finmind_provider import FinMindDataProvider
from data_providers.factor_service import get_stock_factors
from ai_evaluator import DataEvaluator

from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[Startup] FastAPI backend server started cleanly.")
    yield
    print("[Shutdown] FastAPI backend server shutting down.")

app = FastAPI(title="Quant Dashboard API", version="1.1", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8201", "http://127.0.0.1:8201"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# 初始化資料來源與分析引擎
finmind_provider = FinMindDataProvider()
evaluator = DataEvaluator()

# --- 1. 標準化統一回應格式 (Unified Response Format) ---
T = TypeVar("T")

class ApiResponse(BaseModel, Generic[T]):
    success: bool
    data: Optional[T] = None
    error: Optional[str] = None

# --- 全域例外防呆攔截 (Global Exception Handlers) ---
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={"success": False, "data": None, "error": f"參數格式錯誤或無效的請求: {exc.errors()}"}
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "data": None, "error": exc.detail}
    )

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    import traceback
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"success": False, "data": None, "error": f"伺服器內部錯誤: {str(exc)}"}
    )

# --- 2. 強型別契約定義 (Strict Pydantic Schemas) ---

class StockInfoModel(BaseModel):
    id: str
    name: str
    close: float
    diff: float
    pct_change: float
    large_holder_pct: float
    retail_holder_pct: float
    margin_maint_pct: float

class StockDataPayload(BaseModel):
    kline: List[Dict[str, Any]]
    chip: List[Dict[str, Any]]
    rev: List[Dict[str, Any]]
    funds: List[Dict[str, Any]]

class StockDataResponse(BaseModel):
    stock_info: StockInfoModel
    data: StockDataPayload

class FactorScoreResponse(BaseModel):
    momentum_pr: float
    rev_yoy_pr: float
    rev_mom_pr: float
    roe_pr: float
    momentum_hist: float = 0.0
    rev_yoy_hist: float = 0.0
    rev_mom_hist: float = 0.0
    roe_hist: float = 0.0
    total_score: float
    bull_case: float
    base_case: float
    bear_case: float

class AiEvaluationRequest(BaseModel):
    stock_id: str
    stock_name: str
    price: float
    factors: Dict[str, Any]
    chip_data: list
    rev_data: list

# --- ETF Dashboard Schemas ---
class EtfStockModel(BaseModel):
    stock_id: str
    stock_name: str
    total_score: float
    revenue_yoy_rank: float
    revenue_mom_rank: float
    roe_rank: float
    momentum_rank: float
    foreign_fund_flow_20d: float = 0.0
    trust_fund_flow_20d: float = 0.0

class EtfMetadata(BaseModel):
    last_updated_time: str
    total_scanned_stocks: int
    cagr: float = 70.2199
    mdd: float = -0.1738
    sharpe_ratio: float = 12.43
    win_rate: float = 0.758

class EtfChangeModel(BaseModel):
    stock_id: str
    stock_name: str

class EtfChangesPayload(BaseModel):
    added: List[EtfChangeModel]
    removed: List[EtfChangeModel]

class EtfPortfolioPayload(BaseModel):
    metadata: EtfMetadata
    current_portfolio: List[EtfStockModel]
    changes: EtfChangesPayload

# Regex validator for Ticker
TickerPath = Path(..., description="股票代號", pattern="^[a-zA-Z0-9]{2,6}$")

# --- API 路由定義 ---

@app.get("/api/v1/stock/{ticker}/data", response_model=ApiResponse[StockDataResponse])
def get_stock_data(ticker: str = TickerPath):
    """
    獲取個股的基礎 Panel Data (K線、籌碼、營收、財報)
    """
    from db_utils import get_stock_panel_data_from_db
    
    db_data = get_stock_panel_data_from_db(ticker)

    payload = StockDataResponse(
        stock_info=StockInfoModel(
            id=ticker,
            name=ticker, # Temporary name
            close=db_data["close_price"],
            diff=0.0,
            pct_change=0.0,
            large_holder_pct=0.0,
            retail_holder_pct=0.0,
            margin_maint_pct=0.0
        ),
        data=StockDataPayload(
            kline=db_data["kline_data"],
            chip=db_data["chip_data"],
            rev=db_data["rev_data"],
            funds=db_data["funds_data"]
        )
    )

    return ApiResponse(success=True, data=payload)

@app.get("/api/v1/stock/{ticker}/factors", response_model=ApiResponse[FactorScoreResponse])
def api_get_stock_factors(ticker: str = TickerPath):
    """
    計算並取得個股的四大因子 PR 值與戰力總分
    """
    factor_scores = get_stock_factors(ticker)
    
    total_score_val = float(factor_scores.get('total_score', 50.0))
    atr_pct = 2.0

    payload = FactorScoreResponse(
        momentum_pr=float(factor_scores.get('momentum', 0)),
        rev_yoy_pr=float(factor_scores.get('yoy', 0)),
        rev_mom_pr=float(factor_scores.get('mom', 0)),
        roe_pr=float(factor_scores.get('roe', 0)),
        momentum_hist=float(factor_scores.get('momentum_hist', 0)),
        rev_yoy_hist=float(factor_scores.get('yoy_hist', 0)),
        rev_mom_hist=float(factor_scores.get('mom_hist', 0)),
        roe_hist=float(factor_scores.get('roe_hist', 0)),
        total_score=total_score_val,
        bull_case=float(atr_pct * 2.5 * (total_score_val / 50.0)),
        base_case=float(atr_pct * 0.5),
        bear_case=float(-atr_pct * 1.5 * ((100 - total_score_val) / 50.0))
    )
        
    return ApiResponse(success=True, data=payload)


@app.post("/api/v1/stock/{ticker}/ai_evaluate")
def evaluate_stock_ai(ticker: str, req: AiEvaluationRequest):
    """
    呼叫 AI 模型進行綜合診斷
    """
    try:
        # 重組 data_dict 供 evaluator 使用
        data_dict = {
            "stock_id": req.stock_id,
            "stock_name": req.stock_name,
            "price": req.price,
            "factors": req.factors,
            "recent_chip": req.chip_data[-3:] if req.chip_data else [],
            "recent_rev": req.rev_data[-3:] if req.rev_data else []
        }
        res = evaluator.evaluate(data_dict)
        return {
            "status": "success",
            "result": res
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/stock/{ticker}/ml_predict")
def get_ml_predict(ticker: str):
    """
    後端專屬：純邏輯運算、防呆、高結構穩定性的 ML 模型預測端點。
    """
    try:
        import nn_stock_predictor
        import math
        
        result = nn_stock_predictor.predict_stock(ticker)
        
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
            
        def _sanitize_dict(d):
            clean = {}
            for k, v in d.items():
                if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    clean[k] = 0.0
                elif isinstance(v, dict):
                    clean[k] = _sanitize_dict(v)
                else:
                    clean[k] = v
            return clean
            
        clean_result = _sanitize_dict(result)
        
        return {
            "status": "success",
            "data": clean_result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=str(e))
_macro_hedge_cache = {}

@app.get("/api/v1/macro/hedge_ai_analysis")
def get_macro_hedge_ai_analysis():
    """
    獲取 ComeWish 實戰風控大腦對大盤的即時戰略避險評估
    """
    global _macro_hedge_cache
    try:
        from db_utils import get_stock_panel_data_from_db
        import pandas as pd
        
        db_data = get_stock_panel_data_from_db("TAIEX")
        klines = db_data.get("kline_data", [])
        if not klines or len(klines) < 60:
            return ApiResponse(success=False, error="大盤 K 線資料不足，無法進行 ComeWish AI 研判。")

        df = pd.DataFrame(klines)
        df['Date'] = pd.to_datetime(df['date'])
        df = df.sort_values('Date').reset_index(drop=True)
        df['Close'] = pd.to_numeric(df['close'])
        df['ma5'] = df['Close'].rolling(5).mean()
        df['ma20'] = df['Close'].rolling(20).mean()
        df['ma60'] = df['Close'].rolling(60).mean()
        df['ma120'] = df['Close'].rolling(120).mean()
        df['mom_20d'] = (df['Close'] / df['Close'].shift(20)) - 1
        df['daily_return'] = df['Close'].pct_change()
        df['hv_20'] = df['daily_return'].rolling(20).std() * (252 ** 0.5) * 100

        latest = df.iloc[-1]
        latest_date = str(latest['date'])

        # 快取機制：同日期直接回傳快取
        if latest_date in _macro_hedge_cache:
            return ApiResponse(success=True, data=_macro_hedge_cache[latest_date])

        macro_data = {
            "date": latest_date,
            "close": float(latest['Close']),
            "ma5": float(latest['ma5']) if pd.notnull(latest['ma5']) else 0.0,
            "ma20": float(latest['ma20']) if pd.notnull(latest['ma20']) else 0.0,
            "ma60": float(latest['ma60']) if pd.notnull(latest['ma60']) else 0.0,
            "ma120": float(latest['ma120']) if pd.notnull(latest['ma120']) else 0.0,
            "mom_20d": float(latest['mom_20d']) if pd.notnull(latest['mom_20d']) else 0.0,
            "hv_20": float(latest['hv_20']) if pd.notnull(latest['hv_20']) else 0.0,
        }

        ai_result = evaluator.evaluate_macro_hedge(macro_data)
        ai_result["macro_data"] = macro_data
        _macro_hedge_cache[latest_date] = ai_result

        return ApiResponse(success=True, data=ai_result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return ApiResponse(success=False, error=str(e))

@app.get("/api/v1/portfolio", response_model=ApiResponse[EtfPortfolioPayload])
def get_etf_portfolio():
    """
    從 SQLite 資料庫撈取自組 ETF 面板所需的數據 (Top 10 十方極速 與增刪名單)
    """
    from db_utils import get_etf_portfolio_from_db
    import os
    db_path = os.path.join(os.path.dirname(__file__), "screener_results.duckdb")
    
    db_data = get_etf_portfolio_from_db(db_path)

    payload = EtfPortfolioPayload(
        metadata=EtfMetadata(
            last_updated_time=db_data["metadata_info"]["last_updated_time"],
            total_scanned_stocks=db_data["metadata_info"]["total_scanned_stocks"],
            cagr=52.48, mdd=-34.35, sharpe_ratio=1.47, win_rate=0.5628
        ),
        current_portfolio=[EtfStockModel(**p) for p in db_data["portfolio_list"]],
        changes=EtfChangesPayload(
            added=[EtfChangeModel(**c) for c in db_data["added_list"]],
            removed=[EtfChangeModel(**c) for c in db_data["removed_list"]]
        )
    )
    return ApiResponse(success=True, data=payload)

# --- 新版四大因子選股 API (100% 基於本地 market_data.db) ---

class ScreenerStockModel(BaseModel):
    stock_id: str
    stock_name: str
    total_score: float
    revenue_yoy_rank: float
    revenue_mom_rank: float
    roe_rank: float
    momentum_rank: float
    foreign_fund_flow_20d: float = 0.0
    trust_fund_flow_20d: float = 0.0
    revenue_yoy_value: float = 0.0
    revenue_mom_value: float = 0.0
    roe_value: float = 0.0
    momentum_value: float = 0.0
    latest_date: str = ""
    latest_close: float = 0.0

class ScreenerPayload(BaseModel):
    metadata: EtfMetadata
    current_portfolio: List[ScreenerStockModel]
    changes: EtfChangesPayload

@app.get("/api/v1/four_factors_screener", response_model=ApiResponse[ScreenerPayload])
def get_four_factors_screener(top_n: int = 10):
    """
    十方極速四大因子選股 API (Top 9 / 15 微緩衝) — 100% 讀取本地 data/market_data.duckdb
    """
    try:
        import importlib
        import data_providers.four_factors_screener_service as _ffs
        importlib.reload(_ffs)
        from data_providers.four_factors_screener_service import compute_four_factors_from_db
        from db_utils import get_portfolio_changes_from_db
        
        all_results = compute_four_factors_from_db()
        total_scanned = len(all_results)
        top_results = all_results[:top_n]

        db_path = os.path.join(os.path.dirname(__file__), "screener_results.duckdb")
        added_list, removed_list = get_portfolio_changes_from_db(all_results, db_path)

        portfolio_list = []
        for item in top_results:
            portfolio_list.append(ScreenerStockModel(
                stock_id=item["stock_id"],
                stock_name=item["stock_name"],
                total_score=item["total_score"],
                revenue_yoy_rank=item["revenue_yoy_rank"],
                revenue_mom_rank=item["revenue_mom_rank"],
                roe_rank=item["roe_rank"],
                momentum_rank=item["momentum_rank"],
                foreign_fund_flow_20d=item.get("foreign_fund_flow_20d", 0.0),
                trust_fund_flow_20d=item.get("trust_fund_flow_20d", 0.0),
                revenue_yoy_value=item.get("revenue_yoy_value", 0.0),
                revenue_mom_value=item.get("revenue_mom_value", 0.0),
                roe_value=item.get("roe_value", 0.0),
                momentum_value=item.get("momentum_value", 0.0),
                latest_date=item.get("latest_date", ""),
                latest_close=item.get("latest_close", 0.0),
            ))

        payload = ScreenerPayload(
            metadata=EtfMetadata(
                last_updated_time=datetime.now().strftime("%m-%d %H:%M"),
                total_scanned_stocks=total_scanned,
                cagr=52.48,
                mdd=-34.35,
                sharpe_ratio=1.47,
                win_rate=0.5628
            ),
            current_portfolio=portfolio_list,
            changes=EtfChangesPayload(
                added=[EtfChangeModel(**c) for c in added_list],
                removed=[EtfChangeModel(**c) for c in removed_list]
            )
        )

        return ApiResponse(success=True, data=payload)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return ApiResponse(
            success=False,
            data=ScreenerPayload(
                metadata=EtfMetadata(last_updated_time="", total_scanned_stocks=0),
                current_portfolio=[],
                changes=EtfChangesPayload(added=[], removed=[])
            ),
            error=str(e)
        )

def _update_screener_results_db():
    import importlib
    import data_providers.four_factors_screener_service as _ffs
    importlib.reload(_ffs)
    from data_providers.four_factors_screener_service import compute_four_factors_from_db
    from db_utils import update_screener_results_in_db
    import duckdb

    all_results = compute_four_factors_from_db()
    top_results = all_results[:30] # 預設 Top 30
    
    try:
        from db_utils import safe_duckdb_connect
        conn = safe_duckdb_connect(os.path.join(os.path.dirname(__file__), 'data/market_data.duckdb'), read_only=True)
        try:
            total_count = conn.execute("SELECT COUNT(DISTINCT stock_id) FROM klines WHERE LENGTH(stock_id) = 4").fetchone()[0]
        finally:
            conn.close()
    except Exception as e:
        print(f"Error fetching total count: {e}")
        total_count = len(all_results)
    
    db_path = os.path.join(os.path.dirname(__file__), "screener_results.duckdb")
    update_screener_results_in_db(top_results, total_count, db_path)

@app.post("/api/v1/sync_daily_market_data")
def api_sync_daily_market_data():
    """
    手動觸發資料同步機制：統一改由 daily_openapi_klines + update_taiex 寫入 DuckDB，避免雙軌分裂與跨行程搶鎖。
    """
    try:
        from daily_openapi_klines import fetch_openapi_klines
        result = fetch_openapi_klines(run_adjuster=True)

        try:
            from update_taiex import fetch_and_update_taiex
            fetch_and_update_taiex()
        except Exception as taiex_e:
            print(f"[Sync] Failed to update TAIEX/TXF: {taiex_e}")

        try:
            _update_screener_results_db()
        except Exception as inner_e:
            print(f"[Sync] Failed to update screener: {inner_e}")

        if isinstance(result, dict) and result.get("status") == "success":
            return {
                "status": "success",
                "source": "openapi",
                "updated_records": result.get("updated_records", 0),
                "latest_date": result.get("twse_date") or result.get("tpex_date") or result.get("expected_date", "")
            }
        else:
            return {
                "status": "error",
                "source": "openapi",
                "updated_records": 0,
                "latest_date": result.get("message", "無新報價") if isinstance(result, dict) else "同步異常"
            }

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/cliffhanger_shorts")
def api_get_cliffhanger_shorts(min_turnover: float = 100_000_000, delta_drop: float = -10.0, top_n: int = 5):
    """取得最新 5 日動能由正轉負・神級斷崖做空精選名單 (Top 3~5 ｜ 93% 勝率)"""
    try:
        from data_providers.cliffhanger_short_service import compute_cliffhanger_shorts
        shorts = compute_cliffhanger_shorts(min_turnover=min_turnover, delta_drop=delta_drop, top_n=top_n)
        return {
            "status": "success",
            "count": len(shorts),
            "data": shorts
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/run_screener")
def api_run_screener():
    """重新跑分 data/market_data.duckdb 並更新最強 30 名單"""
    try:
        _update_screener_results_db()
        return {"status": "success", "message": "已更新最強 30 名單並寫入 screener_results.duckdb！"}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/update_revenue")
def api_update_revenue():
    """使用 FinMind API 全量下載、清洗並存入 DuckDB 最新月營收"""
    try:
        from data_providers.finmind_revenue_service import sync_finmind_revenues
        res = sync_finmind_revenues()
        if res.get("status") == "success":
            _update_screener_results_db()
            return {"status": "success", "message": res.get("message"), "count": res.get("count")}
        else:
            raise HTTPException(status_code=500, detail=res.get("message"))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/update_revenue_openapi")
def api_update_revenue_openapi():
    """全面改由 FinMind API 全量抓取月營收並更新 DuckDB"""
    return api_update_revenue()

@app.post("/api/v1/update_revenue_moneydj")
def api_update_revenue_moneydj():
    """使用 MoneyDJ 爬蟲抓取今日最新營收快報 (每日即時公告)"""
    try:
        from moneydj_revenue_scraper import scrape_moneydj_revenue
        result = scrape_moneydj_revenue()
        if result.get("status") == "success":
            _update_screener_results_db()
            return {"status": "success", "message": f"MoneyDJ 今日營收快報爬取完成！成功更新 {result.get('count')} 筆個股 (目標歸屬月: {result.get('target_date')})", "data": result}
        else:
            raise HTTPException(status_code=500, detail=result.get("message"))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/update_financials")
def api_update_financials():
    """使用 FinMind API 全量下載、清洗並存入 DuckDB 最新財報與 ROE (含最新 Q3)，並支援 OpenAPI 雙向容錯"""
    try:
        from data_providers.finmind_financials_service import sync_finmind_financials
        res = sync_finmind_financials()
        if res.get("status") == "success":
            _update_screener_results_db()
            return {"status": "success", "message": res.get("message"), "q2_count": res.get("q2_count"), "q3_count": res.get("q3_count")}
        else:
            print(f"[API Notice] FinMind 暫時受限，切換至純 OpenAPI 財報備用管線: {res.get('message')}")
            from pure_openapi_financials_backfill import run_pure_openapi_backfill
            run_pure_openapi_backfill()
            _update_screener_results_db()
            return {"status": "success", "message": "已成功執行全市場財報與 ROE 備用管線更新！"}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
@app.get("/api/v1/disposals/active")
def api_get_active_disposals(target_mode: str = "latest_closed"):
    """取得當前生效中處置股票與出關狙擊標的 (支援 latest_closed 與 next_trading_day 模式)"""
    try:
        import datetime as dt_mod
        import pandas as pd
        from disposal_event_scanner import DisposalEventScanner
        scanner = DisposalEventScanner()
        dates_info = scanner.get_market_dates_info()
        df = scanner.scan_disposal_events(target_mode=target_mode)
        if df.empty:
            return {
                "status": "success", 
                "count": 0, 
                "dates_info": {
                    "last_closed_date": str(dates_info['last_closed_date']),
                    "next_trading_date": str(dates_info['next_trading_date']),
                    "is_market_closed": bool(dates_info['is_market_closed'])
                },
                "data": []
            }
        records = []
        for row in df.to_dict("records"):
            clean_row = {}
            for k, v in row.items():
                if pd.isna(v):
                    clean_row[k] = None
                elif isinstance(v, (pd.Timestamp, dt_mod.date, dt_mod.datetime)):
                    clean_row[k] = str(v)[:10]
                else:
                    clean_row[k] = v
            records.append(clean_row)
        return {
            "status": "success", 
            "count": len(records), 
            "dates_info": {
                "last_closed_date": str(dates_info['last_closed_date']),
                "next_trading_date": str(dates_info['next_trading_date']),
                "is_market_closed": bool(dates_info['is_market_closed'])
            },
            "data": records
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/disposals/history")
def api_get_disposal_history(
    year: Optional[str] = None,
    market: Optional[str] = None,
    tier: Optional[str] = None,
    limit: int = 300
):
    """取得歷史處置庫紀錄與量化回測統計指標"""
    try:
        from disposal_event_scanner import DisposalEventScanner
        scanner = DisposalEventScanner()
        records = scanner.get_historical_records_clean(year=year, market=market, tier=tier, limit=limit)
        stats = scanner.get_disposal_statistics(year=year, market=market, tier=tier)
        return {
            "status": "success",
            "stats": stats,
            "count": len(records),
            "data": records
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/sync_disposals")
def api_sync_disposals():
    """同步最新處置名單至 DuckDB disposals 表"""
    try:
        from disposal_event_scanner import DisposalEventScanner
        scanner = DisposalEventScanner()
        cnt = scanner.sync_disposals()
        return {"status": "success", "message": f"處置資料庫同步完成，共更新/儲存 {cnt} 筆記錄！"}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/update_taiex")
def api_update_taiex():
    try:
        from update_taiex import fetch_and_update_taiex
        fetch_and_update_taiex()
        return {"status": "success", "message": "大盤數據已更新！"}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/audit_duckdb")
def api_audit_duckdb():
    """自動核對 DuckDB 資料庫各數據新鮮度與時效性"""
    try:
        from data_providers.smart_sync_manager import audit_duckdb_freshness
        report = audit_duckdb_freshness()
        return {"status": "success", "data": report}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/smart_sync_pipeline")
def api_smart_sync_pipeline(force_all: bool = False):
    """全自動智能盤後同步與發布管線"""
    try:
        from data_providers.smart_sync_manager import run_smart_sync_and_publish
        res = run_smart_sync_and_publish(force_all=force_all)
        return {"status": "success", "data": res}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)
