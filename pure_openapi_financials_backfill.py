import os
import duckdb
import requests
import urllib3
import json
from datetime import datetime
from collections import defaultdict
urllib3.disable_warnings()

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "market_data.duckdb")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

def init_db():
    from db_utils import safe_duckdb_connect
    conn = safe_duckdb_connect(DB_PATH, read_only=False)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS financials (
                stock_id TEXT,
                year INTEGER,
                quarter INTEGER,
                eps REAL,
                debt_asset_ratio REAL,
                gross_margin REAL,
                operating_margin REAL,
                net_margin REAL,
                roe REAL,
                PRIMARY KEY (stock_id, year, quarter)
            )
        """)
        conn.commit()
    finally:
        conn.close()

def get_openapi_data(url):
    try:
        res = requests.get(url, headers=HEADERS, verify=False, timeout=30)
        if res.status_code == 200:
            return res.json()
        print(f"  [X] HTTP {res.status_code}: {url}")
    except Exception as e:
        print(f"  [X] 連線失敗 {url}: {e}")
    return []

def safe_float(val):
    if not val:
        return None
    try:
        if isinstance(val, str):
            val = val.replace(",", "")
        return float(val)
    except ValueError:
        return None

def run_pure_openapi_backfill():
    print("=" * 60)
    print("🚀 啟動純 OpenAPI 財報全量更新 (無 API 限制)")
    print("=" * 60)
    init_db()
    
    # 儲存結構: memory[stock_id][(year, quarter)] = {'net_income': x, 'eps': y, 'equity': z, ...}
    memory = defaultdict(lambda: defaultdict(dict))
    
    # -----------------------------------------------------
    # 1. 抓取損益表 (Income Statement: 稅後淨利, EPS)
    # -----------------------------------------------------
    print("\n[1/4] 下載上市損益表 (t187ap14_L)...")
    twse_is = get_openapi_data("https://openapi.twse.com.tw/v1/opendata/t187ap14_L")
    for r in twse_is:
        roc_year_str = str(r.get("年度", "0")).strip()
        roc_year = int(roc_year_str) if roc_year_str else 0
        year = roc_year + 1911 if 0 < roc_year < 1911 else roc_year
        quarter_str = str(r.get("季別", "0")).strip()
        quarter = int(quarter_str) if quarter_str else 0
        stock_id = r.get("公司代號", "").strip()
        if not stock_id:
            stock_id = r.get("SecuritiesCompanyCode", "").strip()
        if stock_id.startswith('0') or len(stock_id) != 4:
            continue
        
        eps_str = r.get("基本每股盈餘(元)", "")
        net_inc_str = r.get("稅後淨利", "")
        rev_str = r.get("營業收入", "")
        op_inc_str = r.get("營業利益", "")
        
        eps = safe_float(eps_str)
        net_inc = safe_float(net_inc_str)
        rev = safe_float(rev_str)
        op_inc = safe_float(op_inc_str)
        
        memory[stock_id][(year, quarter)]["eps"] = eps
        memory[stock_id][(year, quarter)]["net_income"] = net_inc * 1000 if net_inc is not None else None
        memory[stock_id][(year, quarter)]["revenue"] = rev * 1000 if rev is not None else None
        
        if op_inc is not None and rev is not None and rev != 0:
            memory[stock_id][(year, quarter)]["op_margin"] = op_inc / rev * 100
    
    print("\n[2/4] 下載上櫃損益表 (mopsfin_t187ap14_O)...")
    tpex_is = get_openapi_data("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap14_O")
    for r in tpex_is:
        roc_year_str = str(r.get("Year", "0")).strip()
        roc_year = int(roc_year_str) if roc_year_str else 0
        year = roc_year + 1911 if 0 < roc_year < 1911 else roc_year
        quarter_str = str(r.get("季別", "0")).strip()
        quarter = int(quarter_str) if quarter_str else 0
        stock_id = r.get("SecuritiesCompanyCode", "").strip()
        if not stock_id:
            stock_id = r.get("公司代號", "").strip()
        if stock_id.startswith('0') or len(stock_id) != 4:
            continue
        
        eps_str = r.get("基本每股盈餘", "")
        net_inc_str = r.get("稅後淨利", "")
        rev_str = r.get("營業收入", "")
        op_inc_str = r.get("營業利益", "")
        
        eps = safe_float(eps_str)
        net_inc = safe_float(net_inc_str)
        rev = safe_float(rev_str)
        op_inc = safe_float(op_inc_str)
        
        memory[stock_id][(year, quarter)]["eps"] = eps
        memory[stock_id][(year, quarter)]["net_income"] = net_inc * 1000 if net_inc is not None else None
        memory[stock_id][(year, quarter)]["revenue"] = rev * 1000 if rev is not None else None
        
        if op_inc is not None and rev is not None and rev != 0:
            memory[stock_id][(year, quarter)]["op_margin"] = op_inc / rev * 100

    # -----------------------------------------------------
    # 1.5 補充金融/銀行/金控/保險/證券損益表 (t187ap06_L_{suf})
    # 部分金控或金融股若尚未出現於彙總表 t187ap14_L，直接由各業別損益表補齊稅後淨利與 EPS
    # -----------------------------------------------------
    fin_is_suffixes = ['bd', 'fh', 'ins', 'mim', 'basi']
    for suf in fin_is_suffixes:
        data_is = get_openapi_data(f"https://openapi.twse.com.tw/v1/opendata/t187ap06_L_{suf}")
        for r in data_is:
            roc_year_str = str(r.get("年度", "0")).strip()
            roc_year = int(roc_year_str) if roc_year_str.isdigit() else 0
            year = roc_year + 1911 if 0 < roc_year < 1911 else roc_year
            quarter_str = str(r.get("季別", "0")).strip()
            quarter = int(quarter_str) if quarter_str.isdigit() else 0
            stock_id = str(r.get("公司代號") or r.get("SecuritiesCompanyCode") or "").strip()
            if not stock_id or stock_id.startswith('0') or len(stock_id) != 4:
                continue
            ni_raw = (
                r.get("本期稅後淨利（淨損）")
                or r.get("本期淨利（淨損）")
                or r.get("繼續營業單位本期淨利（淨損）")
                or r.get("稅後淨利")
                or ""
            )
            eps_raw = r.get("基本每股盈餘（元）") or r.get("基本每股盈餘(元)") or r.get("基本每股盈餘") or ""
            net_inc = safe_float(ni_raw)
            eps_val = safe_float(eps_raw)
            if net_inc is not None and memory[stock_id][(year, quarter)].get("net_income") is None:
                memory[stock_id][(year, quarter)]["net_income"] = net_inc * 1000
            if eps_val is not None and memory[stock_id][(year, quarter)].get("eps") is None:
                memory[stock_id][(year, quarter)]["eps"] = eps_val

    # -----------------------------------------------------
    # 2. 抓取資產負債表 (Balance Sheet: 母公司股東權益)
    # 嚴格遵守 AGENTS.md 3.1：完整涵蓋 6 大產業後綴！
    # ci(一般業), bd(證券期貨業), fh(金控業), ins(保險業), mim(異業), basi(銀行/金融業)
    # -----------------------------------------------------
    suffixes = ['ci', 'bd', 'fh', 'ins', 'mim', 'basi']

    def _extract_equity(r: dict):
        for key in (
            "歸屬於母公司業主之權益合計",
            "歸屬於母公司業主權益合計",
            "歸屬於母公司業主之權益",
            "權益總額",
            "權益總計",
            "權益合計",
        ):
            val = r.get(key)
            if val is not None and str(val).strip() not in ("", "-", "--"):
                parsed = safe_float(val)
                if parsed is not None:
                    return parsed
        return None

    print("\n[3/4] 下載上市資產負債表 (一般/銀行/金控/保險/證券/異業 全 6 業別)...")
    for suf in suffixes:
        url = f"https://openapi.twse.com.tw/v1/opendata/t187ap07_L_{suf}"
        data = get_openapi_data(url)
        if not data:
            continue
        print(f"      -> {suf}: 取得 {len(data)} 筆")
        for r in data:
            roc_year_str = str(r.get("年度", "0")).strip()
            roc_year = int(roc_year_str) if roc_year_str.isdigit() else 0
            year = roc_year + 1911 if 0 < roc_year < 1911 else roc_year

            quarter_str = str(r.get("季別", "0")).strip()
            quarter = int(quarter_str) if quarter_str.isdigit() else 0
            stock_id = str(r.get("公司代號") or r.get("SecuritiesCompanyCode") or "").strip()
            if not stock_id or stock_id.startswith('0') or len(stock_id) != 4:
                continue

            equity = _extract_equity(r)
            if equity is not None:
                memory[stock_id][(year, quarter)]["equity"] = equity * 1000

    print("\n[4/4] 下載上櫃資產負債表 (一般/銀行/金控/保險/證券/異業 全 6 業別)...")
    for suf in suffixes:
        url = f"https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap07_O_{suf}"
        data = get_openapi_data(url)
        if not data:
            continue
        print(f"      -> {suf}: 取得 {len(data)} 筆")
        for r in data:
            roc_year_str = str(r.get("年度") or r.get("Year") or "0").strip()
            roc_year = int(roc_year_str) if roc_year_str.isdigit() else 0
            year = roc_year + 1911 if 0 < roc_year < 1911 else roc_year

            quarter_str = str(r.get("季別", "0")).strip()
            quarter = int(quarter_str) if quarter_str.isdigit() else 0
            stock_id = str(r.get("SecuritiesCompanyCode") or r.get("公司代號") or "").strip()
            if not stock_id or stock_id.startswith('0') or len(stock_id) != 4:
                continue

            equity = _extract_equity(r)
            if equity is not None:
                memory[stock_id][(year, quarter)]["equity"] = equity * 1000

    # -----------------------------------------------------
    # 3. 計算 ROE 並寫入資料庫 (批量 DataFrame UPSERT，瞬時釋放寫入鎖)
    # -----------------------------------------------------
    print("\n[*] 進行交叉組裝與 ROE 計算...")
    import pandas as pd
    rows_to_upsert = []
    for stock_id, periods in memory.items():
        for (year, quarter), data in periods.items():
            if year <= 0 or quarter not in (1, 2, 3, 4):
                continue
            net_income = data.get("net_income")
            equity = data.get("equity")
            eps = data.get("eps")
            op_margin = data.get("op_margin")

            roe = None
            if net_income is not None and equity is not None and equity != 0:
                roe = (net_income / equity) * 100.0

            if net_income is not None or eps is not None or roe is not None:
                rows_to_upsert.append({
                    "stock_id": stock_id,
                    "year": int(year),
                    "quarter": int(quarter),
                    "operating_margin": op_margin,
                    "eps": eps,
                    "roe": roe,
                })

    upsert_count = 0
    if rows_to_upsert:
        df_fin = pd.DataFrame(rows_to_upsert).drop_duplicates(subset=["stock_id", "year", "quarter"], keep="last")
        from db_utils import safe_duckdb_connect
        conn = safe_duckdb_connect(DB_PATH, read_only=False)
        try:
            conn.register("df_openapi_fin", df_fin)
            conn.execute("""
                INSERT INTO financials (stock_id, year, quarter, operating_margin, eps, roe)
                SELECT stock_id, year, quarter, operating_margin, eps, roe FROM df_openapi_fin
                ON CONFLICT(stock_id, year, quarter) DO UPDATE SET
                    operating_margin = COALESCE(excluded.operating_margin, financials.operating_margin),
                    eps = COALESCE(excluded.eps, financials.eps),
                    roe = COALESCE(excluded.roe, financials.roe)
            """)
            conn.commit()
            upsert_count = len(df_fin)
        finally:
            conn.close()

    roe_valid = sum(1 for r in rows_to_upsert if r["roe"] is not None)
    print(f"[DB Update Log] {datetime.now():%Y-%m-%d %H:%M:%S} financials UPSERT {upsert_count} 筆 (有效 ROE: {roe_valid} 筆，涵蓋全 6 業別)")

if __name__ == "__main__":
    run_pure_openapi_backfill()
