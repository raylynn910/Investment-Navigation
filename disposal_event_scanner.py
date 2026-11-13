import requests
import urllib3
import pandas as pd
import numpy as np
import datetime
import duckdb
from db_utils import safe_duckdb_connect
from download_disposals_history import parse_disposal_features, NEW_REGIME_DATE

try:
    from tools.market_calendar import is_trading_day, KNOWN_HOLIDAYS
except Exception:
    def is_trading_day(date_str: str) -> bool:
        try:
            dt = datetime.datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
            return dt.weekday() < 5
        except Exception:
            return False
    KNOWN_HOLIDAYS = set()

urllib3.disable_warnings()


class DisposalEventScanner:
    """
    台股處置股票 (關狗籠) 爆發事件掃描與即將出關狙擊引擎 (符合 2026/8/10 處置新制)
    """
    def __init__(self, db_path='data/market_data.duckdb'):
        self.headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        self.db_path = db_path
        
    def _parse_roc_date(self, roc_str):
        """解析民國日期字串 (例: '1150821', '115/08/21', '1150824~1150828')"""
        if not roc_str: return None
        clean_str = str(roc_str).strip().replace('/', '').replace('-', '')
        if len(clean_str) >= 7:
            try:
                y = int(clean_str[:3]) + 1911
                m = int(clean_str[3:5])
                d = int(clean_str[5:7])
                return datetime.date(y, m, d)
            except:
                pass
        return None

    def fetch_active_disposals(self):
        """從證交所 (RWD歷史區間+OpenAPI) 與櫃買中心 OpenAPI 取得最新生效中的處置股票清單"""
        disposals = []
        today = datetime.date.today()
        start_q = (today - datetime.timedelta(days=35)).strftime('%Y%m%d')
        end_q = (today + datetime.timedelta(days=25)).strftime('%Y%m%d')
        
        # 1. 上市處置股票 - 優先查詢 TWSE RWD 帶日期區間 (解決官方OpenAPI週末將週五剛結束處置下架的問題)
        try:
            url_twse_rwd = f'https://www.twse.com.tw/rwd/zh/announcement/punish?response=json&startDate={start_q}&endDate={end_q}'
            res_rwd = requests.get(url_twse_rwd, headers=self.headers, verify=False, timeout=10)
            if res_rwd.status_code == 200:
                data_rwd = res_rwd.json().get('data', [])
                for row in data_rwd:
                    code = str(row[2]).strip()
                    name = str(row[3]).strip()
                    period_str = str(row[6]).strip()
                    if len(code) == 4 and code.isdigit():
                        dates = period_str.split('～') if '～' in period_str else period_str.split('~')
                        start_d = self._parse_roc_date(dates[0]) if len(dates) > 0 else None
                        end_d = self._parse_roc_date(dates[1]) if len(dates) > 1 else None
                        measures = str(row[8]) if len(row) > 8 else ''
                        type_str = str(row[7]) if len(row) > 7 else '處置'
                        tier, prepay, stage = parse_disposal_features(measures, type_str, start_d)
                        regime = '新制 (5日/2分盤)' if (start_d and start_d >= NEW_REGIME_DATE) else '舊制 (10日/5分盤)'
                        disposals.append({
                            'market': '上市',
                            'stock_id': code,
                            'name': name,
                            'disposal_tier': tier,
                            'prepay_type': prepay,
                            'disposal_stage': stage,
                            'regulatory_regime': regime,
                            'disposal_type': type_str,
                            'reason': str(row[5]) if len(row) > 5 else '',
                            'start_date': start_d,
                            'end_date': end_d,
                            'raw_period': period_str
                        })
        except Exception as e:
            print(f"Error fetching TWSE rwd with range: {e}")

        # 2. 上市處置股票 - 補充 TWSE OpenAPI
        try:
            url_twse = 'https://openapi.twse.com.tw/v1/announcement/punish'
            res = requests.get(url_twse, headers=self.headers, verify=False, timeout=5)
            if res.status_code == 200:
                data = res.json()
                if isinstance(data, list):
                    for item in data:
                        code = str(item.get('Code', '')).strip()
                        name = str(item.get('Name', '')).strip()
                        period_str = str(item.get('DispositionPeriod', ''))
                        if len(code) == 4 and code.isdigit():
                            dates = period_str.split('～') if '～' in period_str else period_str.split('~')
                            start_d = self._parse_roc_date(dates[0]) if len(dates) > 0 else None
                            end_d = self._parse_roc_date(dates[1]) if len(dates) > 1 else None
                            disposals.append({
                                'market': '上市',
                                'stock_id': code,
                                'name': name,
                                'disposal_type': item.get('DispositionMeasures', '處置'),
                                'reason': item.get('ReasonsOfDisposition', ''),
                                'start_date': start_d,
                                'end_date': end_d,
                                'raw_period': period_str
                            })
        except Exception as e:
            print(f"Error fetching TWSE openapi: {e}")
            
        # 3. 上櫃處置股票 (TPEx OpenAPI) - 帶重試與 15 秒 timeout
        for attempt in range(2):
            try:
                url_tpex = 'https://www.tpex.org.tw/openapi/v1/tpex_disposal_information'
                res = requests.get(url_tpex, headers=self.headers, verify=False, timeout=15)
                if res.status_code == 200:
                    data = res.json()
                    for item in data:
                        code = str(item.get('SecuritiesCompanyCode', '')).strip()
                        name = str(item.get('CompanyName', '')).strip()
                        period_str = str(item.get('DispositionPeriod', ''))
                        if len(code) == 4 and code.isdigit():
                            dates = period_str.split('～') if '～' in period_str else period_str.split('~')
                            start_d = self._parse_roc_date(dates[0]) if len(dates) > 0 else None
                            end_d = self._parse_roc_date(dates[1]) if len(dates) > 1 else None
                            
                            tier, prepay, stage = parse_disposal_features(item.get('DispositionContent', ''), item.get('DispositionReasons', ''), start_d)
                            regime = '新制 (5日/2分盤)' if (start_d and start_d >= NEW_REGIME_DATE) else '舊制 (10日/5分盤)'
                            disposals.append({
                                'market': '上櫃',
                                'stock_id': code,
                                'name': name,
                                'disposal_tier': tier,
                                'prepay_type': prepay,
                                'disposal_stage': stage,
                                'regulatory_regime': regime,
                                'disposal_type': '處置',
                                'reason': item.get('DispositionReasons', ''),
                                'start_date': start_d,
                                'end_date': end_d,
                                'raw_period': period_str
                            })
                    break
            except Exception as e:
                if attempt == 1:
                    print(f"Error fetching TPEx disposal: {e}")
            
        df_live = pd.DataFrame(disposals) if disposals else pd.DataFrame()
        
        # 4. 本地 DuckDB 處置資料庫智能雙引擎防護
        # 不論網路狀態如何，皆從 DuckDB disposals 表讀取最新處置名單，防範任何單一交易所 API 連線失敗造成標的遺漏
        df_db = pd.DataFrame()
        try:
            conn = safe_duckdb_connect(self.db_path, read_only=True)
            max_d = conn.execute("""
                SELECT MAX(date) FROM klines 
                WHERE LENGTH(stock_id) = 4 AND regexp_matches(stock_id, '^[0-9]{4}$')
            """).fetchone()[0]
            ref_d = str(max_d)[:10] if max_d else str(datetime.date.today())
            df_db = conn.execute(f"""
                SELECT stock_id, name, market, disposal_tier, prepay_type, disposal_stage, regulatory_regime,
                       start_date, end_date, exit_date, reason
                FROM disposals
                WHERE end_date >= (CAST('{ref_d}' AS DATE) - INTERVAL 25 DAY)
                ORDER BY end_date ASC
            """).df()
            conn.close()
        except Exception as e:
            print(f"[DisposalEventScanner] DuckDB fallback error: {e}")

        # 雙引擎整合：以 live API 與 DuckDB 交叉合併補齊上市與上櫃標的
        if not df_live.empty and not df_db.empty:
            df = pd.concat([df_live, df_db], ignore_index=True)
        elif not df_live.empty:
            df = df_live
        elif not df_db.empty:
            df = df_db
        else:
            return pd.DataFrame()

        # 去除重複項，以 stock_id 為準，優先保留 end_date 較晚之有效記錄
        df['end_date_parsed'] = pd.to_datetime(df['end_date'], errors='coerce')
        df = df.sort_values(by=['stock_id', 'end_date_parsed'], ascending=[True, False])
        df = df.drop_duplicates(subset=['stock_id'], keep='first').drop(columns=['end_date_parsed'], errors='ignore')
        return df

    @classmethod
    def get_next_trading_day(cls, d):
        """計算下一個交易日 (跳過週六、週日與已知國定休假日)"""
        if not d:
            return None
        if isinstance(d, str):
            d = pd.to_datetime(d).date()
        next_day = d + datetime.timedelta(days=1)
        while not is_trading_day(next_day.strftime('%Y-%m-%d')):
            next_day += datetime.timedelta(days=1)
        return next_day

    @classmethod
    def get_prev_trading_day(cls, d):
        """計算前一個交易日 (跳過週六、週日與已知國定休假日)"""
        if not d:
            return None
        if isinstance(d, str):
            d = pd.to_datetime(d).date()
        prev_day = d - datetime.timedelta(days=1)
        while not is_trading_day(prev_day.strftime('%Y-%m-%d')):
            prev_day -= datetime.timedelta(days=1)
        return prev_day

    @classmethod
    def count_trading_days(cls, from_d, to_d):
        """計算兩日期相差之交易日天數 (若 to_d 在未來為正，過去為負，嚴格跳過非交易日)"""
        if isinstance(from_d, str):
            from_d = pd.to_datetime(from_d).date()
        if isinstance(to_d, str):
            to_d = pd.to_datetime(to_d).date()
        if from_d == to_d:
            return 0
        if from_d > to_d:
            cur = from_d
            days = 0
            while cur > to_d:
                cur -= datetime.timedelta(days=1)
                if is_trading_day(cur.strftime('%Y-%m-%d')):
                    days -= 1
            return days
        cur = from_d
        days = 0
        while cur < to_d:
            cur += datetime.timedelta(days=1)
            if is_trading_day(cur.strftime('%Y-%m-%d')):
                days += 1
        return days

    @classmethod
    def extract_duration_days(cls, text, start_d=None, end_d=None):
        """解析處置規定之法定營業日數 (例如 5 個營業日、7 個營業日、10 個營業日等)"""
        import re
        text_str = str(text or "")
        # 1. 阿拉伯數字
        m = re.search(r'(?:起|﹝|〔)\s*(\d+)\s*個?營業日', text_str)
        if m:
            return int(m.group(1))
        # 2. 中文數字
        m_zh = re.search(r'(?:起|﹝|〔)\s*([一二三四五六七八九十百]+)\s*個?營業日', text_str)
        if m_zh:
            zh_map = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10, '十二': 12, '三十': 30}
            zh_val = m_zh.group(1)
            if zh_val in zh_map:
                return zh_map[zh_val]
        # 3. 2026/8/10 處置新制預設為 5 個營業日
        if start_d and pd.to_datetime(start_d).date() >= datetime.date(2026, 8, 10):
            return 5
        return 10

    @classmethod
    def calculate_adjusted_disposal_dates(cls, start_d, end_d, measures_text=''):
        """
        依據台股法規計算天然災害停止上班或國定休假日順延後的真實處置起訖日與出關日。
        處置期間遇休市暫停計算，依休市日數向後順延足額交易日數。
        """
        if isinstance(start_d, str):
            start_d = pd.to_datetime(start_d).date()
        if isinstance(end_d, str):
            end_d = pd.to_datetime(end_d).date()
        if not start_d or not end_d:
            return start_d, end_d, None, 0

        n_days = cls.extract_duration_days(measures_text, start_d, end_d)

        # 1. 真實處置起算日 (若原起算日遇休市，順延至下一個交易日開市)
        cur = start_d
        while not is_trading_day(cur.strftime('%Y-%m-%d')):
            cur += datetime.timedelta(days=1)
        actual_start_d = cur

        # 2. 累積足額 n_days 個真實交易日
        cur_t = actual_start_d
        accum = 1
        while accum < n_days:
            cur_t = cls.get_next_trading_day(cur_t)
            accum += 1

        actual_end_d = cur_t
        actual_exit_d = cls.get_next_trading_day(actual_end_d)

        # 3. 歷史既成事實保護：若在休市前已結束全部處置天數者 (例如 10/22 含以前已滿 5 天)
        # 其處置迄日維持 10/22，但其出關日 (次一交易日) 順延至開市日
        if end_d <= datetime.date(2026, 10, 22):
            actual_start_d = start_d
            actual_end_d = end_d
            actual_exit_d = cls.get_next_trading_day(actual_end_d)

        return actual_start_d, actual_end_d, actual_exit_d, n_days

    def get_market_dates_info(self):
        """
        取得資料庫最新個股收盤日期、次一交易日、以及今日市場是否休市
        """
        conn = safe_duckdb_connect(self.db_path, read_only=True)
        try:
            # 嚴格排除期貨夜盤 (TXF_NIGHT) 與大盤指數，只取真實 4 碼台股之最新收盤日
            max_d = conn.execute("""
                SELECT MAX(date) FROM klines 
                WHERE LENGTH(stock_id) = 4 AND regexp_matches(stock_id, '^[0-9]{4}$')
            """).fetchone()[0]
            last_closed_equity_date = pd.to_datetime(max_d).date() if max_d else datetime.date.today()
        finally:
            conn.close()
            
        today = datetime.date.today()
        is_today_trading = is_trading_day(today.strftime('%Y-%m-%d'))
        next_trading_date = self.get_next_trading_day(last_closed_equity_date)
        
        # 判斷市場目前是否休市 (週末、國定休假日、或尚未進入下一個交易日)
        is_market_closed = (not is_today_trading) or (today > last_closed_equity_date)
        
        return {
            'today': today,
            'is_today_trading': is_today_trading,
            'is_market_closed': is_market_closed,
            'last_closed_date': last_closed_equity_date,
            'next_trading_date': next_trading_date,
        }

    def scan_disposal_events(self, current_date=None, target_mode='latest_closed'):
        """
        掃描處置股，計算出關倒數天數，並精確篩選出【策略 B (T-2搶跑)】與【策略 C (T-1/T日出關空)】命中名單
        target_mode:
          - 'latest_closed': 以資料庫最新個股收盤日為基準 (週五收盤結算視角)
          - 'next_trading_day': 往下推演次一開盤交易日 (下週一開盤/收盤作戰推演視角)
        """
        dates_info = self.get_market_dates_info()
        last_closed_date = dates_info['last_closed_date']
        next_trading_date = dates_info['next_trading_date']
        is_market_closed = dates_info['is_market_closed']

        if isinstance(current_date, str) and current_date in ['latest_closed', 'next_trading_day']:
            target_mode = current_date
            current_date = None

        conn = safe_duckdb_connect(self.db_path, read_only=True)
        if current_date is None:
            if target_mode == 'next_trading_day':
                current_date = next_trading_date
            else:
                current_date = last_closed_date
        elif isinstance(current_date, str):
            current_date = pd.to_datetime(current_date).date()
            
        # 假日休市防呆：若傳入的 current_date 落在週末/休假日，自動校正其交易日基準以避免非交易日浮動
        if not is_trading_day(current_date.strftime('%Y-%m-%d')):
            if target_mode == 'next_trading_day':
                current_date = self.get_next_trading_day(current_date)
            else:
                current_date = last_closed_date

        df = self.fetch_active_disposals()
        if df.empty:
            conn.close()
            return df
            
        results = []
        
        WEEKDAY_NAMES = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
        last_closed_w = WEEKDAY_NAMES[last_closed_date.weekday()]
        next_trading_w = WEEKDAY_NAMES[next_trading_date.weekday()]

        # 取得大盤/台指期基準日漲跌幅 (供策略 B* 大盤順風濾網 >= 0.0% 檢核)
        tx_ret_pct = 0.0
        try:
            df_tx = conn.execute(f"""
                SELECT date, close FROM klines
                WHERE stock_id = 'TXF' AND date <= '{current_date}'
                ORDER BY date DESC LIMIT 2
            """).df()
            if len(df_tx) < 2:
                df_tx = conn.execute(f"""
                    SELECT date, close FROM klines
                    WHERE stock_id = 'TAIEX' AND date <= '{current_date}'
                    ORDER BY date DESC LIMIT 2
                """).df()
            if len(df_tx) >= 2 and float(df_tx.iloc[1]['close']) > 0:
                tx_ret_pct = ((float(df_tx.iloc[0]['close']) / float(df_tx.iloc[1]['close'])) - 1.0) * 100.0
        except Exception:
            tx_ret_pct = 0.0

        for idx, row in df.iterrows():
            sid = str(row['stock_id']).strip()
            name = str(row['name']).strip()
            end_d = pd.to_datetime(row['end_date']).date() if row['end_date'] else None
            start_d = pd.to_datetime(row['start_date']).date() if row['start_date'] else None
            if not end_d or not start_d:
                continue

            # 依休市日（颱風假/國定假）自動順延足額處置營業日
            meas_text = str(row.get('measures', '')) + " " + str(row.get('reason', '')) + " " + str(row.get('regulatory_regime', ''))
            start_d, end_d, exit_d, n_days = self.calculate_adjusted_disposal_dates(start_d, end_d, meas_text)
            exit_w = WEEKDAY_NAMES[exit_d.weekday()]

            # 距離正式出關日 (T日) 的剩餘交易日天數
            td_to_exit = self.count_trading_days(current_date, exit_d)
            
            # 若已經出關超過 3 個交易日則過濾不顯示
            if td_to_exit < -3:
                continue
            
            # 撈取該股最近 30 日歷史 K 線
            df_k = conn.execute(f"""
                SELECT date, open, high, low, close, volume 
                FROM klines 
                WHERE stock_id = '{sid}' AND date <= '{current_date}'
                ORDER BY date DESC LIMIT 30
            """).df()
            
            if df_k.empty:
                continue
                
            latest_close = float(df_k.iloc[0]['close'])
            latest_vol = float(df_k.iloc[0]['volume'])
            turnover = latest_close * latest_vol
            
            prev_vol = float(df_k.iloc[1]['volume']) if len(df_k) > 1 else latest_vol
            vol_ratio = (latest_vol / prev_vol) if prev_vol > 0 else 1.0
            ma5 = float(df_k.iloc[:5]['close'].mean()) if len(df_k) >= 5 else latest_close
            ma5_bias_pct = ((latest_close / ma5) - 1.0) * 100.0 if ma5 > 0 else 0.0
            
            mom_20 = 0.0
            if len(df_k) >= 20:
                p_20 = float(df_k.iloc[19]['close'])
                mom_20 = ((latest_close / p_20) - 1.0) * 100 if p_20 > 0 else 0.0
                
            # 處置前一交易日收盤價 (基準價)
            pre_k = conn.execute(f"""
                SELECT close FROM klines 
                WHERE stock_id = '{sid}' AND date < '{start_d}'
                ORDER BY date DESC LIMIT 1
            """).fetchone() if start_d else None
            
            pre_close = float(pre_k[0]) if (pre_k and pre_k[0] is not None) else latest_close
            disp_cum_ret = ((latest_close / pre_close) - 1.0) * 100.0 if pre_close > 0 else 0.0
            
            # 判斷最佳化策略 B* 與 最佳化策略 C* 命中狀態 (蒙地卡羅 1,000 次參數高原穩健解)
            strat_tag = "⚪ 處置觀察中"
            action_timing = "觀望"
            sop = f"處置中持續監控動能，預計 {exit_d.strftime('%m/%d')} 出關"
            priority = 5
            is_hit_b = False
            is_hit_c = False
            
            if td_to_exit == 2:
                # 最佳化策略 B* 檢驗 (出關前 T-2 搶跑買入：處置累積 -5.0%~+4.5%、5MA正乖離 +1.0%~+4.5%、成交 >= 3,500萬、大盤 >= 0%)
                cond_b_ret = (-5.0 <= disp_cum_ret <= 4.5)
                cond_b_ma5 = (1.0 <= ma5_bias_pct <= 4.5)
                cond_b_vol = (turnover >= 35_000_000)
                cond_b_tx = True if (is_market_closed and current_date == next_trading_date) else (tx_ret_pct >= 0.0)
                
                if cond_b_ret and cond_b_ma5 and cond_b_vol and cond_b_tx:
                    if is_market_closed and current_date == last_closed_date:
                        strat_tag = "🔥 策略 B* (T-2 搶跑買入)"
                        action_timing = f"{last_closed_w} ({last_closed_date.strftime('%m/%d')}) 收盤已進場 (續抱中)"
                        sop = f"{last_closed_w}收盤已觸發買進 ｜ 持有續抱 ｜ {next_trading_w}(T-1)收盤賣50% ｜ 出關日(T)09:00開盤賣50% ｜ 破5MA停損"
                    elif is_market_closed and current_date == next_trading_date:
                        strat_tag = f"🔥 策略 B* 預警 ({next_trading_w}收盤搶跑買入)"
                        action_timing = f"{next_trading_w} ({next_trading_date.strftime('%m/%d')}) 13:25 尾盤進場"
                        sop = f"{next_trading_w} 13:25 若大盤>=平盤且維持5MA正乖離+1%~+4.5%則買進 ｜ 次日(T-1)收盤賣50% ｜ 出關日(T)09:00開盤賣50% ｜ 破5MA停損"
                    else:
                        strat_tag = "🔥 策略 B* (T-2 搶跑買入)"
                        action_timing = "今日 13:25 收盤進場"
                        sop = "今日13:25收盤買進 ｜ 明日(T-1)收盤賣50% ｜ 出關日(T)09:00開盤賣50% ｜ 破5MA停損"
                    priority = 1
                    is_hit_b = True
                else:
                    reasons = []
                    if not cond_b_ret: reasons.append(f"漲跌{disp_cum_ret:+.1f}%非-5.0%~+4.5%")
                    if not cond_b_ma5: reasons.append(f"5MA乖離{ma5_bias_pct:+.2f}%非+1.0%~+4.5%")
                    if not cond_b_vol: reasons.append(f"成交{turnover/1e4:.0f}萬<3500萬")
                    if not cond_b_tx: reasons.append(f"大盤{tx_ret_pct:+.2f}%<0%")
                    strat_tag = "⏳ 處置蓄勢 (T-2)"
                    action_timing = "觀望"
                    sop = f"B*未達標 ({'、'.join(reasons)})"
                    if is_market_closed and current_date == last_closed_date and disp_cum_ret >= 7.8:
                        c_pre = []
                        if turnover < 40_000_000: c_pre.append(f"成交{turnover/1e4:.0f}萬<4000萬")
                        if not (1.05 <= vol_ratio <= 1.80): c_pre.append(f"量比{vol_ratio:.2f}非1.05~1.80")
                        if c_pre:
                            sop += f" ｜ {next_trading_w}T-1 C*預檢亦未達標 ({'、'.join(c_pre)})"
                    priority = 3
                    
            elif td_to_exit == 1:
                # 最佳化策略 C* 檢驗 (出關前 T-1 警戒，出關日 T0 09:00 開高 +1.2%~+6.0% 做空：累積 >= +7.8%、量比 1.05~1.80、成交 >= 4,000萬)
                cond_c_ret = (disp_cum_ret >= 7.8)
                cond_c_vol = (turnover >= 40_000_000)
                cond_c_ratio = (1.05 <= vol_ratio <= 1.80)
                
                if cond_c_ret and cond_c_vol and cond_c_ratio:
                    if is_market_closed and current_date == last_closed_date:
                        strat_tag = "🎯 策略 C* 警戒 (明晨開高空)"
                        action_timing = f"{exit_w} ({exit_d.strftime('%m/%d')}) 09:00 MOO 券賣"
                        sop = f"{exit_w} ({exit_d.strftime('%m/%d')}) 09:00 若開高 +1.2%~+6.0% 且台指期漲幅 <= +1.0%，MOO 券賣放空 ｜ 停損+4.8% ｜ 13:25 全平"
                    elif is_market_closed and current_date == next_trading_date:
                        strat_tag = f"🎯 策略 C* 預警 ({next_trading_w}T-1收盤確認)"
                        action_timing = f"{exit_w} ({exit_d.strftime('%m/%d')}) 09:00 MOO 券賣"
                        sop = f"待 {next_trading_w} ({next_trading_date.strftime('%m/%d')}) T-1 收盤確認量比 1.05~1.80 後，於 {exit_w} ({exit_d.strftime('%m/%d')}) 09:00 開高 +1.2%~+6.0% MOO 券賣 ｜ 停損+4.8% ｜ 13:25 全平"
                    else:
                        strat_tag = "🎯 策略 C* 警戒 (明晨開高空)"
                        action_timing = f"{exit_w} ({exit_d.strftime('%m/%d')}) 09:00 MOO 券賣"
                        sop = f"{exit_w} ({exit_d.strftime('%m/%d')}) 09:00 若開高 +1.2%~+6.0% 且台指期漲幅 <= +1.0%，MOO 券賣放空 ｜ 停損+4.8% ｜ 13:25 全平"
                    priority = 1
                    is_hit_c = True
                else:
                    reasons = []
                    if not cond_c_ret: reasons.append(f"處置累積{disp_cum_ret:+.1f}%<+7.8%")
                    if not cond_c_vol: reasons.append(f"成交{turnover/1e4:.0f}萬<4000萬")
                    if not cond_c_ratio: reasons.append(f"量比{vol_ratio:.2f}非1.05~1.80")
                    strat_tag = "⏳ 處置出關前夕 (T-1)"
                    action_timing = "觀望"
                    sop = f"未達標 ({'、'.join(reasons)})"
                    priority = 3
                    
            elif td_to_exit == 0:
                # 今日出關日 (T 日)：必須錨定 T-1 (出關前一交易日) 完整檢核三項條件 (包含量比 1.05~1.80)
                df_k_t1 = df_k[pd.to_datetime(df_k['date']).dt.date < exit_d]
                if df_k_t1.empty:
                    df_k_t1 = df_k
                t1_close = float(df_k_t1.iloc[0]['close'])
                t1_vol = float(df_k_t1.iloc[0]['volume'])
                t1_prev_vol = float(df_k_t1.iloc[1]['volume']) if len(df_k_t1) > 1 else t1_vol
                t1_vol_ratio = (t1_vol / t1_prev_vol) if t1_prev_vol > 0 else 1.0
                t1_turnover = t1_close * t1_vol
                t1_cum_ret = ((t1_close / pre_close) - 1.0) * 100.0 if pre_close > 0 else 0.0

                cond_c_ret = (t1_cum_ret >= 7.8)
                cond_c_vol = (t1_turnover >= 40_000_000)
                cond_c_ratio = (1.05 <= t1_vol_ratio <= 1.80)

                if cond_c_ret and cond_c_vol and cond_c_ratio:
                    strat_tag = "🚨 策略 C* 執行 (T日出關空)"
                    action_timing = f"{exit_w} ({exit_d.strftime('%m/%d')}) 09:00 MOO 券賣"
                    sop = "09:00 若開高 +1.2%~+6.0% 且台指期漲幅 <= +1.0%，MOO 券賣放空 ｜ 停損+4.8% ｜ 開低或開平嚴禁追空 ｜ 13:25 全數平倉"
                    priority = 2
                    is_hit_c = True
                else:
                    reasons = []
                    if not cond_c_ret: reasons.append(f"處置累積{t1_cum_ret:+.1f}%<+7.8%")
                    if not cond_c_vol: reasons.append(f"成交{t1_turnover/1e4:.0f}萬<4000萬")
                    if not cond_c_ratio: reasons.append(f"量比{t1_vol_ratio:.2f}非1.05~1.80")
                    strat_tag = "⚪ 今日出關 (觀望)"
                    action_timing = "觀望"
                    sop = f"未達標 ({'、'.join(reasons)}) ｜ 嚴禁盲目放空或接刀"
                    priority = 4
                    
            elif td_to_exit > 2:
                strat_tag = f"關狗籠中 (倒數{td_to_exit}天)"
                action_timing = "觀望"
                if td_to_exit == 3 and is_market_closed:
                    b_pre = []
                    if not (-5.0 <= disp_cum_ret <= 4.5): b_pre.append(f"漲跌{disp_cum_ret:+.1f}%非-5.0%~+4.5%")
                    if not (1.0 <= ma5_bias_pct <= 4.5): b_pre.append(f"5MA乖離{ma5_bias_pct:+.2f}%非+1.0%~+4.5%")
                    if turnover < 35_000_000: b_pre.append(f"成交{turnover/1e4:.0f}萬<3500萬")
                    pre_note = f"目前B*預檢未達標 ({'、'.join(b_pre)})" if b_pre else "目前符合B*預檢條件"
                    sop = f"{next_trading_w} ({next_trading_date.strftime('%m/%d')}) 將進入 T-2 搶跑檢核點 ｜ {pre_note}"
                else:
                    sop = f"處置中持續觀察，預計 {exit_d.strftime('%m/%d')} 出關"
                priority = 5
            else:
                strat_tag = f"已出關 (T+{abs(td_to_exit)})"
                action_timing = "觀望"
                sop = f"已於 {exit_d.strftime('%m/%d')} 正式出關"
                priority = 6
                
            tier = row.get('disposal_tier', '2分盤') if 'disposal_tier' in row else '2分盤'
            prepay = row.get('prepay_type', '門檻預收') if 'prepay_type' in row else '門檻預收'
            regime = row.get('regulatory_regime', '新制 (5日/2分盤)') if 'regulatory_regime' in row else '新制 (5日/2分盤)'
            
            t_str = f"{turnover/1e8:.2f}億" if turnover >= 1e8 else f"{turnover/1e4:.0f}萬"
            c_desc = f"T-{td_to_exit} ({exit_d.strftime('%m/%d')})" if td_to_exit >= 0 else f"出關T+{abs(td_to_exit)}"
            
            results.append({
                'stock_id': sid,
                'name': name,
                'market': row.get('market', '上市'),
                'disposal_tier': tier,
                'prepay_type': prepay,
                'regulatory_regime': regime,
                'latest_price': latest_close,
                'disp_cum_ret': disp_cum_ret,
                'turnover': turnover,
                'turnover_str': t_str,
                'vol_ratio': vol_ratio,
                'ma5': ma5,
                'ma5_bias_pct': ma5_bias_pct,
                'tx_ret_pct': tx_ret_pct,
                'mom_20_pct': mom_20,
                'start_date': str(start_d),
                'end_date': str(end_d),
                'exit_date': str(exit_d),
                'td_to_exit': td_to_exit,
                'countdown_desc': c_desc,
                'countdown_days': td_to_exit,
                'status': strat_tag,
                'priority': priority,
                'is_hit_b': is_hit_b,
                'is_hit_c': is_hit_c,
                'action_timing': action_timing,
                'entry_sop': sop,
                'exit_sop': sop,
                'base_date': str(current_date),
                'last_closed_date': str(last_closed_date),
                'next_trading_date': str(next_trading_date),
                'is_market_closed': is_market_closed
            })
            
        conn.close()
        
        if not results:
            return pd.DataFrame()
            
        df_res = pd.DataFrame(results).sort_values(by=['priority', 'td_to_exit', 'turnover'], ascending=[True, True, False]).reset_index(drop=True)
        return df_res

    def get_weekend_tactical_briefing(self):
        """
        週末/假日休市作戰雙向簡報：
        1. 週五收盤結算已觸發名單 (latest_closed)
        2. 下週一開盤/收盤作戰推演名單 (next_trading_day)
        3. 關鍵標的 (如 3597 映興、3441 聯一光) 的精確作戰節奏指引
        """
        dates_info = self.get_market_dates_info()
        last_closed = dates_info['last_closed_date']
        next_date = dates_info['next_trading_date']
        is_closed = dates_info['is_market_closed']

        df_closed = self.scan_disposal_events(target_mode='latest_closed')
        df_next = self.scan_disposal_events(target_mode='next_trading_day')

        return {
            'is_weekend_or_holiday': is_closed,
            'last_closed_date': str(last_closed),
            'next_trading_date': str(next_date),
            'df_closed': df_closed,
            'df_next': df_next,
        }


    def sync_disposals(self, year=None):
        """將最新處置名單同步寫入 DuckDB disposals 表 (增量更新)"""
        try:
            from download_disposals_history import (
                fetch_twse_year, fetch_tpex_year,
                compute_backtest_metrics_for_records, save_disposals_to_duckdb
            )
            target_year = year or datetime.date.today().year
            tw_list = fetch_twse_year(target_year)
            tp_list = fetch_tpex_year(target_year)
            all_raw = tw_list + tp_list
            if not all_raw:
                return 0
            df_raw = pd.DataFrame(all_raw).drop_duplicates(subset=['stock_id', 'start_date', 'end_date'])
            unique_records = df_raw.to_dict('records')
            
            conn = safe_duckdb_connect(self.db_path, read_only=True)
            try:
                enriched = compute_backtest_metrics_for_records(conn, unique_records)
            finally:
                conn.close()
                
            return save_disposals_to_duckdb(enriched, self.db_path)
        except Exception as e:
            print(f"[DisposalEventScanner] Sync error: {e}")
            raise e

    def get_historical_disposals(self, year=None, market=None, tier=None, limit=200):
        """查詢 DuckDB 歷史處置資料庫與回測指標 (含 regulatory_regime 制度欄位)"""
        conn = safe_duckdb_connect(self.db_path, read_only=True)
        try:
            where_clauses = ["1=1"]
            params = []
            if year and year != '全部':
                where_clauses.append("EXTRACT(year FROM start_date) = ?")
                params.append(int(year))
            if market and market != '全部':
                where_clauses.append("market = ?")
                params.append(market)
            if tier and tier != '全部':
                clean_tier = tier.split(' ')[0]
                where_clauses.append("disposal_tier = ?")
                params.append(clean_tier)
            where_sql = " AND ".join(where_clauses)
            
            df = conn.execute(f"""
                SELECT 
                    stock_id, name, market, disposal_tier, prepay_type, disposal_stage, regulatory_regime,
                    start_date, end_date, exit_date,
                    t_minus_1_close, exit_close, disposal_return_pct, t0_to_exit_return_pct,
                    max_runup_pct, max_drawdown_pct, is_active, days_to_exit, reason
                FROM disposals
                WHERE {where_sql}
                ORDER BY start_date DESC
                LIMIT ?
            """, params + [limit]).df()
            
            # 轉換日期與去除 NaN，保證 JSON 序列化合法
            for col in ['start_date', 'end_date', 'exit_date']:
                if col in df.columns:
                    df[col] = df[col].astype(str)
            df = df.where(pd.notnull(df), None)
            return df
        finally:
            conn.close()

    def get_historical_records_clean(self, year=None, market=None, tier=None, limit=200):
        """回傳 100% 符合 JSON 規範之歷史處置清單 (dicts)"""
        import math
        df = self.get_historical_disposals(year=year, market=market, tier=tier, limit=limit)
        clean_records = []
        for row in df.to_dict('records'):
            clean_row = {}
            for k, v in row.items():
                if pd.isna(v) or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
                    clean_row[k] = None
                elif isinstance(v, (pd.Timestamp, datetime.date, datetime.datetime)):
                    clean_row[k] = str(v)[:10]
                else:
                    clean_row[k] = v
            clean_records.append(clean_row)
        return clean_records

    def get_disposal_statistics(self, year=None, market=None, tier=None):
        """統計歷史勝率、平均報酬率、最大拉升與最大跌幅"""
        conn = safe_duckdb_connect(self.db_path, read_only=True)
        try:
            where_clauses = ["disposal_return_pct IS NOT NULL"]
            params = []
            if year and year != '全部':
                where_clauses.append("EXTRACT(year FROM start_date) = ?")
                params.append(int(year))
            if market and market != '全部':
                where_clauses.append("market = ?")
                params.append(market)
            if tier and tier != '全部':
                clean_tier = tier.split(' ')[0]
                where_clauses.append("disposal_tier = ?")
                params.append(clean_tier)
            where_sql = " AND ".join(where_clauses)
            
            row = conn.execute(f"""
                SELECT 
                    count(*) as total_events,
                    avg(disposal_return_pct) as avg_return,
                    avg(CASE WHEN disposal_return_pct > 0 THEN 1.0 ELSE 0.0 END) * 100 as win_rate,
                    avg(t0_to_exit_return_pct) as avg_t0_return,
                    avg(max_runup_pct) as avg_runup,
                    avg(max_drawdown_pct) as avg_drawdown,
                    max(disposal_return_pct) as max_return,
                    min(disposal_return_pct) as min_return
                FROM disposals
                WHERE {where_sql}
            """, params).fetchone()
            
            if not row or row[0] == 0:
                return {
                    'total_events': 0, 'avg_return': 0.0, 'win_rate': 0.0,
                    'avg_t0_return': 0.0, 'avg_runup': 0.0, 'avg_drawdown': 0.0,
                    'max_return': 0.0, 'min_return': 0.0
                }
            return {
                'total_events': int(row[0]),
                'avg_return': round(row[1] or 0.0, 2),
                'win_rate': round(row[2] or 0.0, 1),
                'avg_t0_return': round(row[3] or 0.0, 2),
                'avg_runup': round(row[4] or 0.0, 2),
                'avg_drawdown': round(row[5] or 0.0, 2),
                'max_return': round(row[6] or 0.0, 2),
                'min_return': round(row[7] or 0.0, 2),
            }
        finally:
            conn.close()


if __name__ == '__main__':
    scanner = DisposalEventScanner()
    print("=== 正在掃描台股處置股票與出關爆發事件 ===")
    df_disposal = scanner.scan_disposal_events()
    print(f"目前生效中處置標的總數: {len(df_disposal)} 檔\n")
    for idx, row in df_disposal.iterrows():
        print(f"{row['status']} {row['stock_id']} {row['name']} ({row['market']}) | 現價: {row['latest_price']} 元 | 20日動能: +{row['mom_20_pct']:.1f}%")
        print(f"   -> 處置區間: {row['start_date']} ~ {row['end_date']} | 距出關倒數: {row['countdown_days']} 天\n")
