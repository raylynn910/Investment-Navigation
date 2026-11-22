"""
處置王歷史資料庫建置與量化特徵提取模組
涵蓋 2022~2026 臺灣證券交易所 (TWSE) 與 證券櫃檯買賣中心 (TPEx) 所有處置股票。
自動關聯本地 DuckDB klines 資料表，計算回測所需之入處置價、出關價、處置報酬率、期間最大拉升與最大跌幅。
"""

import os
import sys
import re
import datetime
import urllib3
import requests
import pandas as pd
import duckdb

# 避免 Windows 終端機 cp950 編碼錯誤
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

urllib3.disable_warnings()

try:
    from tools.market_calendar import is_trading_day
except Exception:
    def is_trading_day(d_str):
        try:
            return pd.to_datetime(d_str).weekday() < 5
        except Exception:
            return False

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'market_data.duckdb')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

def parse_roc_date(roc_str):
    """解析民國日期 (例: '115/10/01', '1151001') -> datetime.date"""
    if not roc_str:
        return None
    s = str(roc_str).strip().replace('/', '').replace('-', '').replace('.', '')
    if len(s) >= 7 and s[:3].isdigit():
        try:
            y = int(s[:3]) + 1911
            m = int(s[3:5])
            d = int(s[5:7])
            return datetime.date(y, m, d)
        except:
            pass
    return None

def parse_period(period_str):
    """解析處置起訖日字串 (例: '115/10/02～115/10/08' 或 '115/10/02~115/10/08')"""
    if not period_str:
        return None, None
    delims = ['～', '~', '-', '至']
    for d in delims:
        if d in period_str:
            parts = period_str.split(d)
            if len(parts) >= 2:
                s_d = parse_roc_date(parts[0])
                e_d = parse_roc_date(parts[1])
                return s_d, e_d
    return None, None

def get_next_trading_day(d):
    """次一交易日 (跳過週六、週日與已知休假日/颱風假/補假)"""
    if not d:
        return None
    if isinstance(d, str):
        d = pd.to_datetime(d).date()
    next_day = d + datetime.timedelta(days=1)
    while not is_trading_day(str(next_day)):
        next_day += datetime.timedelta(days=1)
    return next_day

NEW_REGIME_DATE = datetime.date(2026, 8, 10)
FRIDAY_BEFORE_NEW_REGIME = datetime.date(2026, 8, 7)

def parse_disposal_features(text, type_code='', start_d=None):
    """解析處置等級 (2分/5分/20分盤)、預收款券強度、處置期別 (依 2026/8/10 處置新制調整)"""
    text_str = str(text or '')
    type_str = str(type_code or '')
    
    # 撮合間隔 (Tier) - 依 2026/8/10 處置新制：一般處置改為約每2分鐘撮合 (2分盤)
    if start_d and start_d >= NEW_REGIME_DATE:
        tier = '2分盤'
    else:
        tier = '5分盤'

    if re.search(r'20\s*分|二十分', text_str):
        tier = '20分盤'
    elif re.search(r'2\s*分|二分|每二分鐘|每2分鐘|二分鐘', text_str):
        tier = '2分盤'
    elif re.search(r'25\s*分|二十五分', text_str):
        tier = '25分盤'
    elif re.search(r'45\s*分|四十五分', text_str):
        tier = '45分盤'
    elif re.search(r'60\s*分|一小時|60\s*分鐘', text_str):
        tier = '60分盤'
    elif re.search(r'10\s*分|十分', text_str):
        tier = '10分盤'
    elif re.search(r'5\s*分|五分', text_str):
        tier = '5分盤'

    # 預收款券模式 (Prepay)
    prepay = '門檻預收(單筆10張/累計30張)'
    if re.search(r'向委託人收取全部|全部之買進價金|收足全部|逐筆|無論買賣數量多寡', text_str):
        prepay = '逐筆全額預收'
    elif re.search(r'全額交割|變更交易方法', text_str):
        prepay = '變更交易(全額交割)'

    # 處置期別 (Stage)
    if '20分' in tier or '第二' in type_str or '2' in type_str or '第二次處置' in text_str:
        stage = '第二次處置'
    else:
        stage = '第一次處置'

    return tier, prepay, stage

def fetch_twse_year(year):
    """抓取臺灣證券交易所 (TWSE) 指定西元年處置公告"""
    records = []
    s_date = f"{year}0101"
    e_date = f"{year}1231"
    url = f"https://www.twse.com.tw/rwd/zh/announcement/punish?startDate={s_date}&endDate={e_date}&response=json"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code == 200:
            data = r.json().get('data', [])
            for row in data:
                if len(row) < 9:
                    continue
                code = str(row[2]).strip()
                if not (len(code) == 4 and code.isdigit()):
                    continue
                name = str(row[3]).strip()
                ann_date = parse_roc_date(row[1])
                type_code = str(row[4]).strip()
                period_str = str(row[6]).strip()
                start_d, end_d = parse_period(period_str)
                if not (start_d and end_d):
                    continue
                exit_d = get_next_trading_day(end_d)
                measures = str(row[8]).strip()
                reason = str(row[7]).strip() + ' ' + str(row[5]).strip()
                tier, prepay, stage = parse_disposal_features(measures, type_code, start_d)
                records.append({
                    'stock_id': code,
                    'name': name,
                    'market': '上市',
                    'announcement_date': ann_date,
                    'start_date': start_d,
                    'end_date': end_d,
                    'exit_date': exit_d,
                    'disposal_tier': tier,
                    'prepay_type': prepay,
                    'disposal_stage': stage,
                    'reason': reason,
                    'measures': measures
                })
    except Exception as e:
        print(f"[TWSE {year}] Error: {e}")
    return records

def fetch_tpex_year(year):
    """抓取證券櫃檯買賣中心 (TPEx) 指定年份處置公告"""
    records = []
    roc_year = year - 1911
    s_date = f"{roc_year}/01/01"
    e_date = f"{roc_year}/12/31"
    url = f"https://www.tpex.org.tw/www/zh-tw/bulletin/disposal?startDate={s_date}&endDate={e_date}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code == 200:
            tables = r.json().get('tables', [])
            if tables:
                data = tables[0].get('data', [])
                for row in data:
                    if len(row) < 9:
                        continue
                    code = str(row[2]).strip()
                    if not (len(code) == 4 and code.isdigit()):
                        continue
                    # 清理名稱中可能附帶的 HTML 連結
                    raw_name = str(row[3]).strip()
                    name = raw_name.split('(')[0].strip()
                    ann_date = parse_roc_date(row[1])
                    type_code = str(row[4]).strip()
                    period_str = str(row[5]).strip()
                    start_d, end_d = parse_period(period_str)
                    if not (start_d and end_d):
                        continue
                    exit_d = get_next_trading_day(end_d)
                    reason = str(row[6]).strip()
                    stage_hint = str(row[7]).strip()
                    measures = str(row[8]).strip()
                    tier, prepay, stage = parse_disposal_features(measures, stage_hint or type_code, start_d)
                    records.append({
                        'stock_id': code,
                        'name': name,
                        'market': '上櫃',
                        'announcement_date': ann_date,
                        'start_date': start_d,
                        'end_date': end_d,
                        'exit_date': exit_d,
                        'disposal_tier': tier,
                        'prepay_type': prepay,
                        'disposal_stage': stage,
                        'reason': f"{stage_hint} {reason}".strip(),
                        'measures': measures
                    })
    except Exception as e:
        print(f"[TPEx {year}] Error: {e}")
    return records

def init_disposals_table(conn):
    """建立 disposals 資料表結構 (支援 2026/8/10 處置新制 regulatory_regime)"""
    conn.execute("""
    CREATE TABLE IF NOT EXISTS disposals (
        stock_id VARCHAR,
        name VARCHAR,
        market VARCHAR,
        announcement_date DATE,
        start_date DATE,
        end_date DATE,
        exit_date DATE,
        disposal_tier VARCHAR,
        prepay_type VARCHAR,
        disposal_stage VARCHAR,
        regulatory_regime VARCHAR,
        reason VARCHAR,
        measures VARCHAR,
        is_active BOOLEAN,
        days_to_exit INTEGER,
        t_minus_1_date DATE,
        t_minus_1_close DOUBLE,
        t0_date DATE,
        t0_open DOUBLE,
        t0_close DOUBLE,
        period_high DOUBLE,
        period_low DOUBLE,
        exit_date_actual DATE,
        exit_open DOUBLE,
        exit_close DOUBLE,
        disposal_return_pct DOUBLE,
        t0_to_exit_return_pct DOUBLE,
        max_runup_pct DOUBLE,
        max_drawdown_pct DOUBLE,
        created_at TIMESTAMP,
        PRIMARY KEY (stock_id, start_date, end_date)
    )
    """)

def compute_backtest_metrics_for_records(conn, records):
    """利用 DuckDB 中的 klines 表批次計算回測量化指標 (含 2026/8/10 處置新制重算)"""
    today = datetime.date.today()
    enriched = []
    
    # 建立股票代號集合，減少不必要的查詢
    stock_ids = list(set(r['stock_id'] for r in records))
    print(f"[*] Computing K-line backtest metrics for {len(records)} events across {len(stock_ids)} stocks...")
    
    for r in records:
        sid = r['stock_id']
        s_date_obj = r['start_date']
        e_date_obj = r['end_date']

        # 依 2026/8/10 新制處理過渡期與制度標記
        if s_date_obj >= NEW_REGIME_DATE:
            regime = '新制 (5日/2分盤)'
        elif s_date_obj < NEW_REGIME_DATE and e_date_obj >= NEW_REGIME_DATE:
            regime = '過渡調整 (8/10新制規整)'
            # 8/10 處置中股票：重算出關日與撮合間隔
            k_pre = conn.execute("""
                SELECT date FROM klines 
                WHERE stock_id = ? AND date >= ? AND date <= ?
                ORDER BY date ASC
            """, [sid, str(s_date_obj), str(FRIDAY_BEFORE_NEW_REGIME)]).fetchall()
            cnt_pre = len(k_pre)
            if cnt_pre >= 5:
                # 8/10 已滿 5 日者，8/10 當日解除處置
                r['exit_date'] = NEW_REGIME_DATE
                r['end_date'] = datetime.date.fromisoformat(k_pre[4][0])
            else:
                needed = 5 - cnt_pre
                k_post = conn.execute("""
                    SELECT date FROM klines 
                    WHERE stock_id = ? AND date >= ?
                    ORDER BY date ASC LIMIT ?
                """, [sid, str(NEW_REGIME_DATE), needed]).fetchall()
                if len(k_post) == needed:
                    r['end_date'] = datetime.date.fromisoformat(k_post[-1][0])
                    k_nxt = conn.execute("""
                        SELECT date FROM klines 
                        WHERE stock_id = ? AND date > ?
                        ORDER BY date ASC LIMIT 1
                    """, [sid, str(r['end_date'])]).fetchone()
                    if k_nxt:
                        r['exit_date'] = datetime.date.fromisoformat(k_nxt[0])
                    else:
                        r['exit_date'] = get_next_trading_day(r['end_date'])
            # 8/10 起撮合改為 2分盤
            r['disposal_tier'] = '2分盤'
        else:
            regime = '舊制 (10日/5分盤)'

        start_d = str(r['start_date'])
        end_d = str(r['end_date'])
        exit_d = str(r['exit_date'])
        
        # 1. 判斷是否活躍與出關倒數
        is_act = (today <= r['end_date'])
        # 計算交易日倒數 (簡化為工作日)
        cur = today
        days_left = 0
        if is_act:
            while cur < r['exit_date']:
                cur += datetime.timedelta(days=1)
                if cur.weekday() < 5:
                    days_left += 1
        
        # 2. T-1 日 (入處置前1交易日)
        t_m1 = conn.execute("""
            SELECT date, close FROM klines
            WHERE stock_id = ? AND date < ?
            ORDER BY date DESC LIMIT 1
        """, [sid, start_d]).fetchone()
        
        t_minus_1_date = t_m1[0] if t_m1 else None
        t_minus_1_close = float(t_m1[1]) if t_m1 else None
        
        # 3. T0 (處置首日實際交易日)
        t0 = conn.execute("""
            SELECT date, open, close, high, low FROM klines
            WHERE stock_id = ? AND date >= ? AND date <= ?
            ORDER BY date ASC LIMIT 1
        """, [sid, start_d, end_d]).fetchone()
        
        t0_date = t0[0] if t0 else None
        t0_open = float(t0[1]) if t0 else None
        t0_close = float(t0[2]) if t0 else None
        
        # 4. 處置期間最高與最低價
        period_k = conn.execute("""
            SELECT max(high), min(low) FROM klines
            WHERE stock_id = ? AND date >= ? AND date <= ?
        """, [sid, start_d, end_d]).fetchone()
        
        period_high = float(period_k[0]) if (period_k and period_k[0] is not None) else None
        period_low = float(period_k[1]) if (period_k and period_k[1] is not None) else None
        
        # 5. 出關日 (Exit K)
        exit_k = conn.execute("""
            SELECT date, open, close FROM klines
            WHERE stock_id = ? AND date >= ?
            ORDER BY date ASC LIMIT 1
        """, [sid, exit_d]).fetchone()
        
        exit_date_actual = exit_k[0] if exit_k else None
        exit_open = float(exit_k[1]) if exit_k else None
        exit_close = float(exit_k[2]) if exit_k else None
        
        # 6. 回測報酬率計算
        disposal_return_pct = None
        t0_to_exit_return_pct = None
        max_runup_pct = None
        max_drawdown_pct = None
        
        if t_minus_1_close and t_minus_1_close > 0:
            if exit_close is not None:
                disposal_return_pct = round(((exit_close - t_minus_1_close) / t_minus_1_close) * 100.0, 2)
            if period_high is not None:
                max_runup_pct = round(((period_high - t_minus_1_close) / t_minus_1_close) * 100.0, 2)
            if period_low is not None:
                max_drawdown_pct = round(((period_low - t_minus_1_close) / t_minus_1_close) * 100.0, 2)
                
        if t0_open and t0_open > 0 and exit_close is not None:
            t0_to_exit_return_pct = round(((exit_close - t0_open) / t0_open) * 100.0, 2)
            
        enriched.append({
            **r,
            'regulatory_regime': regime,
            'is_active': is_act,
            'days_to_exit': days_left,
            't_minus_1_date': t_minus_1_date,
            't_minus_1_close': t_minus_1_close,
            't0_date': t0_date,
            't0_open': t0_open,
            't0_close': t0_close,
            'period_high': period_high,
            'period_low': period_low,
            'exit_date_actual': exit_date_actual,
            'exit_open': exit_open,
            'exit_close': exit_close,
            'disposal_return_pct': disposal_return_pct,
            't0_to_exit_return_pct': t0_to_exit_return_pct,
            'max_runup_pct': max_runup_pct,
            'max_drawdown_pct': max_drawdown_pct,
            'created_at': datetime.datetime.now()
        })
        
    return enriched

def save_disposals_to_duckdb(enriched_records, db_path=DB_PATH):
    """將強化後的處置紀錄寫入 DuckDB (UPSERT)"""
    if not enriched_records:
        print("[!] No records to save.")
        return 0
        
    from db_utils import safe_duckdb_connect
    conn = safe_duckdb_connect(db_path, read_only=False)
    try:
        init_disposals_table(conn)
        df = pd.DataFrame(enriched_records)
        
        # 註冊為臨時 View 並依欄位名稱明確執行 INSERT OR REPLACE
        conn.register("temp_disposals", df)
        cols = [
            'stock_id', 'name', 'market', 'announcement_date', 'start_date', 'end_date', 'exit_date',
            'disposal_tier', 'prepay_type', 'disposal_stage', 'regulatory_regime', 'reason', 'measures',
            'is_active', 'days_to_exit', 't_minus_1_date', 't_minus_1_close', 't0_date', 't0_open', 't0_close',
            'period_high', 'period_low', 'exit_date_actual', 'exit_open', 'exit_close',
            'disposal_return_pct', 't0_to_exit_return_pct', 'max_runup_pct', 'max_drawdown_pct', 'created_at'
        ]
        cols_str = ', '.join(cols)
        conn.execute(f"""
            INSERT OR REPLACE INTO disposals ({cols_str})
            SELECT {cols_str} FROM temp_disposals
        """)
        saved_count = conn.execute("SELECT count(*) FROM disposals").fetchone()[0]
        print(f"[✓] Successfully upserted {len(df)} records. Total in disposals table: {saved_count}")
        return saved_count
    finally:
        conn.close()

def backfill_disposals_5_years(db_path=DB_PATH):
    """一鍵回補 2022~2026 近 5 年所有處置股票名單與回測指標"""
    years = [2022, 2023, 2024, 2025, 2026]
    all_raw = []
    
    print("="*60)
    print("🚀 啟動 2022~2026 全市場處置股回補引擎 (TWSE & TPEx)")
    print("="*60)
    
    for y in years:
        tw_list = fetch_twse_year(y)
        tp_list = fetch_tpex_year(y)
        print(f"[*] Year {y}: TWSE={len(tw_list)}, TPEx={len(tp_list)}")
        all_raw.extend(tw_list)
        all_raw.extend(tp_list)
        
    # 去重 (依 stock_id, start_date, end_date)
    df_raw = pd.DataFrame(all_raw)
    if df_raw.empty:
        print("[!] No records retrieved.")
        return
    df_raw = df_raw.drop_duplicates(subset=['stock_id', 'start_date', 'end_date'])
    unique_records = df_raw.to_dict('records')
    print(f"[*] Total unique disposal events: {len(unique_records)}")
    
    # 連結 DuckDB 計算回測指標
    from db_utils import safe_duckdb_connect
    conn = safe_duckdb_connect(db_path, read_only=True)
    try:
        enriched = compute_backtest_metrics_for_records(conn, unique_records)
    finally:
        conn.close()
        
    # 儲存
    save_disposals_to_duckdb(enriched, db_path)
    print("[✓] 處置資料庫回補完畢！")

if __name__ == '__main__':
    backfill_disposals_5_years()
