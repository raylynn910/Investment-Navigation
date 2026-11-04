# -*- coding: utf-8 -*-
"""
smart_sync_manager.py
======================
全自動盤後智能核對、FinMind API 清洗同步與雲端發布引擎 (Smart Sync & Publish Engine)

職責：
1. 自動判別當前系統時間 (台灣時區 UTC+8)，精確計算各數據模組應有之目標日期/月份/季度：
   - K線 (klines)：盤後 14:30 以前為前一交易日，14:30 以後為當日交易日；週末/休市自動回推前一交易日。
   - 台指期日夜盤 (TXF / TXF_NIGHT)：注意夜盤 (15:00~次日05:00) 跨日與隔日開盤對照，精確對齊主力合約。
   - 月營收 (revenues)：每月 10 日前要求前2個月營收，10 日後全量要求前1個月營收 (如 10/10 後為 9 月營收)。
   - 季財報/ROE (financials)：依主管機關申報時限 (Q1: 5/15, Q2: 8/14, Q3: 11/14, Q4: 3/31) 判定應有季度，並及時納入最新公佈之提早申報股。
2. 核對本地 DuckDB (market_data.duckdb) 是否為最新月份與覆蓋率 (>= 1,500 檔)。
3. 若檢測為舊資料，自動呼叫 FinMind API 進行最新資料串接、正規化清洗 (單位統一為元、ROE 公式驗證、假動能除權息校正) 並寫入 DuckDB。
4. 重新執行全市場截面四大因子 Z-Score 跑分 (screener_results.duckdb)。
5. 匯出親友版/老手版信號 (published_signals.json) 並執行 Git Commit & Push 瞬傳至雲端 Streamlit.app。
"""

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import time
import json
import logging
import datetime
import duckdb
import pandas as pd
from typing import Callable, Optional, Dict, Any

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from db_utils import safe_duckdb_connect
from tools.market_calendar import is_trading_day, get_last_trading_day

logger = logging.getLogger(__name__)
DB_PATH = os.path.join(PROJECT_ROOT, "data", "market_data.duckdb")
SCREENER_DB_PATH = os.path.join(PROJECT_ROOT, "screener_results.duckdb")


def get_taiwan_now() -> datetime.datetime:
    """取得台灣標準時間 (UTC+8)"""
    tz_tw = datetime.timezone(datetime.timedelta(hours=8))
    return datetime.datetime.now(tz_tw)


def audit_duckdb_freshness(now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """
    智能核驗本地 DuckDB 各項數據的新鮮度與合規性。
    回傳詳細診斷報告與各模組同步決策。
    """
    if now is None:
        now = get_taiwan_now()

    conn = safe_duckdb_connect(DB_PATH, read_only=True)
    today_str = now.strftime("%Y-%m-%d")
    today_is_trading = is_trading_day(today_str)

    # -------------------------------------------------------------
    # 1. K 線 (klines) 目標交易日判定
    # -------------------------------------------------------------
    # 台股收盤 13:30，盤後交易與 OpenAPI 匯出約 14:30 完備
    if today_is_trading:
        if now.hour < 14 or (now.hour == 14 and now.minute < 30):
            yesterday = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            expected_kline_date = get_last_trading_day(yesterday)
        else:
            expected_kline_date = today_str
    else:
        expected_kline_date = get_last_trading_day(today_str)

    # -------------------------------------------------------------
    # 2. 台指期 (TXF 日盤 / TXF_NIGHT 夜盤) 目標日期判定
    # -------------------------------------------------------------
    expected_txf_date = expected_kline_date

    # 台指期夜盤：15:00 ~ 次日 05:00
    # FinMind 以夜盤開盤所在之日曆日作為 date 標籤 (例如 10/29 15:00 開打的夜盤，date 為 2026-10-29)
    if now.hour >= 15 and today_is_trading:
        expected_night_date = today_str
    elif now.hour < 5:
        # 05:00 前夜盤仍在進行或剛結束，屬於前一有效交易日
        yesterday = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        expected_night_date = get_last_trading_day(yesterday)
    else:
        # 05:00 ~ 15:00，夜盤已於 05:00 結算完畢，屬於前一交易日
        if today_is_trading:
            yesterday = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            expected_night_date = get_last_trading_day(yesterday)
        else:
            expected_night_date = get_last_trading_day(today_str)

    # -------------------------------------------------------------
    # 3. 月營收 (revenues) 目標月份判定
    # -------------------------------------------------------------
    # 台灣證券法規：每月 10 日前需申報前一月份營收
    if now.day > 10:
        if now.month == 1:
            req_rev_year = now.year - 1
            req_rev_month = 12
        else:
            req_rev_year = now.year
            req_rev_month = now.month - 1
        expected_rev_date = f"{now.year}-{now.month:02d}-10"
        expected_rev_label = f"{req_rev_year}年{req_rev_month}月營收"
    else:
        if now.month <= 2:
            req_rev_year = now.year - 1
            req_rev_month = 10 + now.month
        else:
            req_rev_year = now.year
            req_rev_month = now.month - 2
        prev_m = 12 if now.month == 1 else (now.month - 1)
        prev_y = (now.year - 1) if now.month == 1 else now.year
        expected_rev_date = f"{prev_y}-{prev_m:02d}-10"
        expected_rev_label = f"{req_rev_year}年{req_rev_month}月營收"

    # -------------------------------------------------------------
    # 4. 季財報 / ROE (financials) 目標季度判定
    # -------------------------------------------------------------
    # 法定申報期限：Q1 (5/15 前), Q2 (8/14 前), Q3 (11/14 前), Q4 (次年 3/31 前)
    m, d = now.month, now.day
    if (m == 5 and d > 15) or (m in [6, 7]) or (m == 8 and d <= 14):
        req_fin_year = now.year
        req_fin_quarter = 1
    elif (m == 8 and d > 14) or (m in [9, 10]) or (m == 11 and d <= 14):
        req_fin_year = now.year
        req_fin_quarter = 2
    elif (m == 11 and d > 14) or (m == 12):
        req_fin_year = now.year
        req_fin_quarter = 3
    elif m in [1, 2, 3]:
        req_fin_year = now.year - 1
        req_fin_quarter = 3
    else:
        req_fin_year = now.year - 1
        req_fin_quarter = 4

    expected_fin_label = f"{req_fin_year} Q{req_fin_quarter} 季財報"

    # -------------------------------------------------------------
    # 5. 檢索本地 DuckDB 當前儲存狀態
    # -------------------------------------------------------------
    try:
        db_kline_max = conn.execute("SELECT MAX(date) FROM klines WHERE stock_id NOT IN ('TAIEX', 'TXF', 'TXF_NIGHT')").fetchone()[0]
    except Exception:
        db_kline_max = None
    db_kline_max_str = str(db_kline_max) if db_kline_max else ""

    try:
        db_kline_cnt = conn.execute("SELECT COUNT(DISTINCT stock_id) FROM klines WHERE date = ?", [db_kline_max]).fetchone()[0] if db_kline_max else 0
    except Exception:
        db_kline_cnt = 0

    try:
        db_taiex_max = conn.execute("SELECT MAX(date) FROM klines WHERE stock_id='TAIEX'").fetchone()[0]
    except Exception:
        db_taiex_max = None
    db_taiex_str = str(db_taiex_max) if db_taiex_max else ""

    try:
        db_txf_max = conn.execute("SELECT MAX(date) FROM klines WHERE stock_id='TXF'").fetchone()[0]
    except Exception:
        db_txf_max = None
    db_txf_str = str(db_txf_max) if db_txf_max else ""

    try:
        db_night_max = conn.execute("SELECT MAX(date) FROM klines WHERE stock_id='TXF_NIGHT'").fetchone()[0]
    except Exception:
        db_night_max = None
    db_night_str = str(db_night_max) if db_night_max else ""

    # 月營收覆蓋量
    try:
        db_rev_cnt = conn.execute("SELECT COUNT(*) FROM revenues WHERE date = ?", [expected_rev_date]).fetchone()[0]
    except Exception:
        db_rev_cnt = 0

    try:
        db_rev_latest_date = conn.execute("SELECT MAX(date) FROM revenues").fetchone()[0]
    except Exception:
        db_rev_latest_date = None

    # 財報基準季覆蓋量
    try:
        db_fin_cnt = conn.execute("SELECT COUNT(*) FROM financials WHERE year = ? AND quarter = ?", [req_fin_year, req_fin_quarter]).fetchone()[0]
    except Exception:
        db_fin_cnt = 0

    # 檢測是否有提早公佈之最新季度 (例如在 10月/11月 提早申報的 Q3 財報)
    try:
        db_early_q3_cnt = conn.execute("SELECT COUNT(*) FROM financials WHERE year = ? AND quarter = 3", [now.year]).fetchone()[0]
    except Exception:
        db_early_q3_cnt = 0

    # 統計最近 3 期月營收與季財報的月份分佈 (方便主帥一眼查閱 DuckDB 更新到哪個月份)
    monthly_rev_summary = []
    try:
        rev_rows = conn.execute("""
            SELECT CAST(date AS VARCHAR) AS d, COUNT(DISTINCT stock_id) AS cnt
            FROM revenues
            GROUP BY d
            ORDER BY d DESC
            LIMIT 4
        """).fetchall()
        for r_d, r_cnt in rev_rows:
            # 發布日 YYYY-MM-10 對應上個月營收
            try:
                dt_obj = datetime.datetime.strptime(str(r_d)[:10], "%Y-%m-%d")
                rev_m = 12 if dt_obj.month == 1 else (dt_obj.month - 1)
                rev_y = (dt_obj.year - 1) if dt_obj.month == 1 else dt_obj.year
                m_label = f"{rev_y}年{rev_m}月營收"
            except Exception:
                m_label = str(r_d)
            monthly_rev_summary.append({"publish_date": str(r_d)[:10], "rev_month_label": m_label, "count": int(r_cnt)})
    except Exception:
        pass

    quarterly_fin_summary = []
    try:
        fin_rows = conn.execute("""
            SELECT year, quarter, COUNT(DISTINCT stock_id) AS cnt
            FROM financials
            GROUP BY year, quarter
            ORDER BY year DESC, quarter DESC
            LIMIT 4
        """).fetchall()
        q_month_map = {1: "3月/Q1季報", 2: "6月/Q2季報", 3: "9月/Q3季報", 4: "12月/Q4年報"}
        for fy, fq, fcnt in fin_rows:
            q_label = f"{int(fy)} Q{int(fq)} ({q_month_map.get(int(fq), f'Q{fq}')})"
            quarterly_fin_summary.append({"year": int(fy), "quarter": int(fq), "quarter_label": q_label, "count": int(fcnt)})
    except Exception:
        pass

    # 穿透最近一期跑分 (Top 9 / Top 15) 四大因子所採用的真實日期標籤
    factor_penetration = {
        "screener_updated_at": "未知",
        "data_date": db_kline_max_str,
        "momentum_tag": f"{db_kline_max_str} 收盤 (還原動能)",
        "yoy_mom_tag": expected_rev_label,
        "roe_tag": expected_fin_label,
        "top_stocks": []
    }
    try:
        sig_path = os.path.join(PROJECT_ROOT, "published_signals.json")
        top_ids = []
        top_names = {}
        if os.path.exists(sig_path):
            with open(sig_path, "r", encoding="utf-8") as sf:
                sig_json = json.load(sf)
            factor_penetration["screener_updated_at"] = sig_json.get("generated_at", "未知")
            factor_penetration["data_date"] = sig_json.get("data_date", db_kline_max_str)
            c1 = sig_json.get("core_1_swing", {})
            for item in (c1.get("top_9", []) + c1.get("buffer_10_15", [])):
                sid = str(item.get("stock_id", "")).strip()
                if sid:
                    top_ids.append(sid)
                    top_names[sid] = str(item.get("stock_name") or item.get("name") or "")

        if not top_ids and os.path.exists(SCREENER_DB_PATH):
            s_conn = safe_duckdb_connect(SCREENER_DB_PATH, read_only=True)
            try:
                st_row = s_conn.execute("SELECT last_updated_time FROM system_status LIMIT 1").fetchone()
                if st_row:
                    factor_penetration["screener_updated_at"] = str(st_row[0])
                p_rows = s_conn.execute("SELECT stock_id, stock_name FROM current_portfolio LIMIT 15").fetchall()
                for sid, sname in p_rows:
                    top_ids.append(str(sid))
                    top_names[str(sid)] = str(sname)
            finally:
                s_conn.close()

        if top_ids:
            placeholders = ",".join([f"'{sid}'" for sid in top_ids])
            rev_latest_rows = conn.execute(f"""
                SELECT stock_id, CAST(MAX(date) AS VARCHAR) AS max_rev_date
                FROM revenues
                WHERE stock_id IN ({placeholders})
                GROUP BY stock_id
            """).fetchall()
            rev_map = {str(r[0]): str(r[1])[:10] for r in rev_latest_rows}

            fin_latest_rows = conn.execute(f"""
                SELECT stock_id, year, quarter
                FROM (
                    SELECT stock_id, year, quarter,
                           ROW_NUMBER() OVER(PARTITION BY stock_id ORDER BY year DESC, quarter DESC) AS rn
                    FROM financials
                    WHERE stock_id IN ({placeholders}) AND (roe IS NOT NULL OR eps < 0)
                ) sub
                WHERE rn = 1
            """).fetchall()
            fin_map = {str(r[0]): f"{int(r[1])} Q{int(r[2])}" for r in fin_latest_rows}

            kline_latest_rows = conn.execute(f"""
                SELECT stock_id, CAST(MAX(date) AS VARCHAR) AS max_k_date
                FROM klines
                WHERE stock_id IN ({placeholders})
                GROUP BY stock_id
            """).fetchall()
            k_map = {str(r[0]): str(r[1])[:10] for r in kline_latest_rows}

            rev_dist = {}
            fin_dist = {}
            top_details = []
            for rank_i, sid in enumerate(top_ids[:15], 1):
                r_date = rev_map.get(sid, "")
                try:
                    dt_r = datetime.datetime.strptime(r_date, "%Y-%m-%d")
                    rm = 12 if dt_r.month == 1 else (dt_r.month - 1)
                    ry = (dt_r.year - 1) if dt_r.month == 1 else dt_r.year
                    r_label = f"{ry}年{rm}月"
                except Exception:
                    r_label = r_date or "未知"
                f_label = fin_map.get(sid, "未知")
                k_date = k_map.get(sid, db_kline_max_str)

                if rank_i <= 9:
                    rev_dist[r_label] = rev_dist.get(r_label, 0) + 1
                    fin_dist[f_label] = fin_dist.get(f_label, 0) + 1

                top_details.append({
                    "rank": rank_i,
                    "stock_id": sid,
                    "stock_name": top_names.get(sid, ""),
                    "kline_date": k_date,
                    "rev_month": r_label,
                    "rev_publish_date": r_date,
                    "roe_quarter": f_label
                })

            factor_penetration["top_stocks"] = top_details
            if rev_dist:
                factor_penetration["yoy_mom_tag"] = " ｜ ".join([f"{k}營收 ({v}檔)" for k, v in sorted(rev_dist.items(), reverse=True)])
            if fin_dist:
                factor_penetration["roe_tag"] = " ｜ ".join([f"{k} ({v}檔)" for k, v in sorted(fin_dist.items(), reverse=True)])
            if top_details:
                factor_penetration["momentum_tag"] = f"{top_details[0]['kline_date']} 收盤 (20D/10D還原動能)"
    except Exception as e:
        logger.warning(f"Factor penetration check error: {e}")

    conn.close()

    # -------------------------------------------------------------
    # 5.5 智慧時效提醒文案 (現在應該是幾月份的營收？幾月份的財報 ROE？)
    # -------------------------------------------------------------
    if now.day <= 10:
        cur_filing_m = 12 if now.month == 1 else (now.month - 1)
        cur_filing_y = (now.year - 1) if now.month == 1 else now.year
        early_rev_date = f"{now.year}-{now.month:02d}-10"
        early_rev_cnt = next((x["count"] for x in monthly_rev_summary if x["publish_date"] == early_rev_date), 0)
        warn_suffix = f" ⚠️ 基準月僅 {db_rev_cnt} 檔 (<1500)，建議點選一鍵同步補齊" if db_rev_cnt < 1500 else ""
        rev_smart_tip = (
            f"現在法定基準應為【{expected_rev_label}】（目前已收錄 {db_rev_cnt} 檔{warn_suffix}；"
            f"另 {cur_filing_y}年{cur_filing_m}月營收正處於申報期，{now.month}/10 截止，已搶先收錄 {early_rev_cnt} 檔）"
        )
    else:
        warn_suffix = f" ⚠️ 基準月僅 {db_rev_cnt} 檔 (<1500)，建議點選一鍵同步補齊" if db_rev_cnt < 1500 else ""
        rev_smart_tip = (
            f"現在法定基準應為【{expected_rev_label}】（因今日 {now.month}/{now.day:02d} 已過每月10日申報截止日，"
            f"目前 DuckDB 已收錄 {db_rev_cnt} 檔{warn_suffix}）"
        )

    q_name_map = {1: "第1季 (3月財報)", 2: "第2季 (6月半年報)", 3: "第3季 (9月季報)", 4: "第4季 (12月年報)"}
    req_q_desc = f"{req_fin_year} Q{req_fin_quarter} {q_name_map.get(req_fin_quarter, '')}"
    if (now.month == 10) or (now.month == 11 and now.day <= 14):
        fin_smart_tip = (
            f"現在法定基準應為【{req_q_desc}】（全市場已收錄 {db_fin_cnt} 檔）；"
            f"同時正值【{now.year} Q3 第3季 (9月財報)】密集申報期（11/14 截止，DuckDB 已搶先收錄 {db_early_q3_cnt} 檔）"
        )
    elif (now.month == 7) or (now.month == 8 and now.day <= 14):
        fin_smart_tip = (
            f"現在法定基準應為【{req_q_desc}】（已收錄 {db_fin_cnt} 檔）；"
            f"同時正值【{now.year} Q2 第2季 (6月財報)】申報期（8/14 截止）"
        )
    elif (now.month == 4) or (now.month == 5 and now.day <= 15):
        fin_smart_tip = (
            f"現在法定基準應為【{req_q_desc}】（已收錄 {db_fin_cnt} 檔）；"
            f"同時正值【{now.year} Q1 第1季 (3月財報)】申報期（5/15 截止）"
        )
    else:
        fin_smart_tip = f"現在法定基準應為【{req_q_desc}】（目前已收錄 {db_fin_cnt} 檔）"

    # -------------------------------------------------------------
    # 6. 決策邏輯判定
    # -------------------------------------------------------------
    needs_kline_sync = (db_kline_max_str < expected_kline_date) or (db_kline_cnt < 1500)
    needs_taiex_sync = (db_taiex_str < expected_kline_date) or (db_txf_str < expected_txf_date) or (db_night_str < expected_night_date)
    needs_revenue_sync = (db_rev_cnt < 1500)
    needs_financials_sync = (db_fin_cnt < 1500)

    # 判定處置股名單是否為今日更新
    needs_disposal_sync = True

    is_all_fresh = not (needs_kline_sync or needs_taiex_sync or needs_revenue_sync or needs_financials_sync)

    report = {
        "audit_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "expected_targets": {
            "kline_date": expected_kline_date,
            "txf_date": expected_txf_date,
            "txf_night_date": expected_night_date,
            "revenue_date": expected_rev_date,
            "revenue_label": expected_rev_label,
            "financial_year": req_fin_year,
            "financial_quarter": req_fin_quarter,
            "financial_label": expected_fin_label,
        },
        "smart_reminders": {
            "revenue_tip": rev_smart_tip,
            "financial_tip": fin_smart_tip,
        },
        "monthly_rev_summary": monthly_rev_summary,
        "quarterly_fin_summary": quarterly_fin_summary,
        "factor_penetration": factor_penetration,
        "db_status": {
            "kline_max_date": db_kline_max_str,
            "kline_stocks_count": db_kline_cnt,
            "taiex_max_date": db_taiex_str,
            "txf_max_date": db_txf_str,
            "txf_night_max_date": db_night_str,
            "revenue_latest_date": str(db_rev_latest_date) if db_rev_latest_date else "無",
            "revenue_target_count": db_rev_cnt,
            "financial_target_count": db_fin_cnt,
            "early_q3_count": db_early_q3_cnt,
        },
        "sync_decisions": {
            "needs_kline_sync": needs_kline_sync,
            "needs_taiex_sync": needs_taiex_sync,
            "needs_revenue_sync": needs_revenue_sync,
            "needs_financials_sync": needs_financials_sync,
            "needs_disposal_sync": needs_disposal_sync,
            "is_all_fresh": is_all_fresh,
        }
    }
    return report


def format_console_freshness_banner(report: Optional[Dict[str, Any]] = None) -> str:
    """
    產生供 launcher.py (start_ui 捷徑啟動視窗) 顯示的高對比度 DuckDB 月份核驗與四大因子時效提醒看板。
    """
    if report is None:
        report = audit_duckdb_freshness()

    targets = report.get("expected_targets", {})
    db_stat = report.get("db_status", {})
    reminders = report.get("smart_reminders", {})
    fp = report.get("factor_penetration", {})
    rev_sum = report.get("monthly_rev_summary", [])
    fin_sum = report.get("quarterly_fin_summary", [])

    rev_hist_str = " ｜ ".join([f"{x['rev_month_label']}: {x['count']}檔" for x in rev_sum[:3]]) or "無"
    fin_hist_str = " ｜ ".join([f"{x['quarter_label']}: {x['count']}檔" for x in fin_sum[:3]]) or "無"

    lines = [
        "=" * 72,
        "  🦆 [DuckDB 資料庫月份核驗 & 最近一期跑分四大因子日期標籤智慧提醒]",
        "=" * 72,
        f"  🕒 系統核驗時間 : {report.get('audit_time', '')}",
        f"  💡 營收月份提醒 : {reminders.get('revenue_tip', '')}",
        f"  💡 財報ROE提醒  : {reminders.get('financial_tip', '')}",
        "-" * 72,
        "  📦 【本地 DuckDB (market_data.duckdb) 最新庫存月份一覽】",
        f"     • 個股 K 線收盤 : 最新 {db_stat.get('kline_max_date')} (共 {db_stat.get('kline_stocks_count')} 檔) [目標: {targets.get('kline_date')}]",
        f"     • 台指期日/夜盤 : 日盤 {db_stat.get('txf_max_date')} ｜ 夜盤 {db_stat.get('txf_night_max_date')}",
        f"     • 月營收各月庫存: {rev_hist_str}",
        f"     • 季財報各季庫存: {fin_hist_str}",
        "-" * 72,
        f"  🎯 【最近一期跑分 ({fp.get('screener_updated_at')}) 四大因子日期標籤穿透 (Top 9)】",
        f"     1. 動能因子 (70%) : 採用 {fp.get('momentum_tag')} + 5日均量護城河",
        f"     2. 營收YoY  (20%) : 採用 {fp.get('yoy_mom_tag')}",
        f"     3. 營收MoM  (底線): 採用 {fp.get('yoy_mom_tag')} (> -20% 防護)",
        f"     4. 財報ROE  (10%) : 採用 {fp.get('roe_tag')} (單季平均化)",
        "=" * 72,
    ]
    return "\n".join(lines)


def run_smart_sync_and_publish(
    force_all: bool = False,
    progress_callback: Optional[Callable[[int, str], None]] = None
) -> Dict[str, Any]:
    """
    執行端對端全自動智能核驗、清洗同步、跑分與雲端發布管線。
    支援進度回調函數 progress_callback(percentage, message)。
    """
    def update_progress(pct: int, msg: str):
        if progress_callback:
            try:
                progress_callback(pct, msg)
            except Exception as e:
                logger.warning(f"Progress callback exception: {e}")
        logger.info(f"[{pct}%] {msg}")

    result_summary = {
        "start_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "success": False,
        "audit_report": None,
        "actions_taken": [],
        "errors": [],
        "git_status": {},
        "screener_summary": {}
    }

    try:
        # Step 0: 智能核對 DuckDB
        update_progress(5, "🔍 正在核對 DuckDB 本地資料庫新鮮度與法定時效性...")
        audit_rep = audit_duckdb_freshness()
        result_summary["audit_report"] = audit_rep
        decisions = audit_rep["sync_decisions"]
        targets = audit_rep["expected_targets"]
        db_stat = audit_rep["db_status"]

        logger.info(f"DuckDB 智能診斷結果: kline={decisions['needs_kline_sync']}, taiex={decisions['needs_taiex_sync']}, rev={decisions['needs_revenue_sync']}, fin={decisions['needs_financials_sync']}")

        # Step 1: 大盤與台指期日夜盤
        if decisions["needs_taiex_sync"] or force_all:
            update_progress(20, f"1/6 正在使用 FinMind API 更新大盤與台指期日夜盤 ({targets['txf_date']} / {targets['txf_night_date']})...")
            try:
                from update_taiex import fetch_and_update_taiex
                fetch_and_update_taiex()
                result_summary["actions_taken"].append("大盤指數 (TAIEX) 與台指期日夜盤 (TXF / TXF_NIGHT) 已同步至最新")
            except Exception as e:
                err_msg = f"大盤與期貨同步例外: {e}"
                logger.error(err_msg)
                result_summary["errors"].append(err_msg)
        else:
            update_progress(20, f"1/6 大盤與台指期日夜盤已為最新 ({db_stat['txf_max_date']})，略過更新。")
            result_summary["actions_taken"].append(f"大盤與期貨已為最新 ({db_stat['txf_max_date']})")

        # Step 2: 全市場 K 線歷史與除權息跳空校正
        if decisions["needs_kline_sync"] or force_all:
            update_progress(35, f"2/6 正在校正全市場 K 線歷史與除權息跳空 (目標: {targets['kline_date']})...")
            try:
                from daily_openapi_klines import fetch_openapi_klines
                kline_res = fetch_openapi_klines()
                # 執行 Auto-Adjuster 消除除權息與減資造成的假動能
                from data_providers.price_adjuster import auto_adjust_historical_prices
                auto_adjust_historical_prices()
                result_summary["actions_taken"].append(f"全市場 K 線與除權息校正完成 (覆蓋日期: {targets['kline_date']})")
            except Exception as e:
                err_msg = f"K線同步例外: {e}"
                logger.error(err_msg)
                result_summary["errors"].append(err_msg)
        else:
            update_progress(35, f"2/6 全市場 K 線已為最新 ({db_stat['kline_max_date']}，{db_stat['kline_stocks_count']}檔)，略過更新。")
            result_summary["actions_taken"].append(f"K線已為最新 ({db_stat['kline_max_date']})")

        # Step 3: 上市櫃處置股票名單
        if decisions["needs_disposal_sync"] or force_all:
            update_progress(50, "3/6 正在同步上市櫃處置出關雷達名單...")
            try:
                from disposal_event_scanner import DisposalEventScanner
                cnt = DisposalEventScanner().sync_disposals()
                result_summary["actions_taken"].append(f"處置股票雷達同步完成 (共更新 {cnt} 筆)")
            except Exception as e:
                err_msg = f"處置雷達例外: {e}"
                logger.warning(err_msg)
                result_summary["errors"].append(err_msg)

        # Step 4: 全量月營收 (FinMind API 下載、單位元清洗、重構 YoY/MoM)
        if decisions["needs_revenue_sync"] or force_all:
            update_progress(65, f"4/6 正在使用 FinMind API 全量同步 {targets['revenue_label']} 並重構 YoY/MoM...")
            try:
                from data_providers.finmind_revenue_service import sync_finmind_revenues
                rev_res = sync_finmind_revenues()
                if rev_res.get("status") == "success":
                    result_summary["actions_taken"].append(f"FinMind 全市場月營收全量同步成功 ({rev_res.get('count', 0)} 筆)")
                else:
                    result_summary["actions_taken"].append(f"月營收同步狀態: {rev_res.get('message', '完成')}")
            except Exception as e:
                err_msg = f"月營收同步例外: {e}"
                logger.error(err_msg)
                result_summary["errors"].append(err_msg)
        else:
            update_progress(65, f"4/6 月營收已為最新 ({targets['revenue_label']} 已具備 {db_stat['revenue_target_count']} 檔)，略過全量抓取。")
            result_summary["actions_taken"].append(f"月營收已為最新 ({targets['revenue_label']})")

        # Step 5: 全量季財報與 ROE (FinMind API 下載、單季平均化)
        if decisions["needs_financials_sync"] or force_all:
            update_progress(78, f"5/6 正在使用 FinMind API 全量同步 {targets['financial_label']} 與 ROE...")
            try:
                from data_providers.finmind_financials_service import sync_finmind_financials
                fin_res = sync_finmind_financials()
                result_summary["actions_taken"].append(f"FinMind 季財報與 ROE 同步完成 ({fin_res.get('count', 0)} 筆)")
            except Exception as e:
                err_msg = f"財報同步例外: {e}"
                logger.error(err_msg)
                result_summary["errors"].append(err_msg)
        else:
            update_progress(78, f"5/6 季財報已為最新 ({targets['financial_label']} 已具備 {db_stat['financial_target_count']} 檔，含最新 Q3)，略過全量抓取。")
            result_summary["actions_taken"].append(f"季財報已為最新 ({targets['financial_label']})")

        # Step 6: 全市場四大因子重新跑分
        update_progress(88, "6/7 正在執行 1,900+ 檔截面四大因子 Z-Score 動能跑分...")
        try:
            from data_providers.four_factors_screener_service import compute_four_factors_from_db
            from db_utils import update_screener_results_in_db
            top_results = compute_four_factors_from_db()
            update_screener_results_in_db(top_results, len(top_results), SCREENER_DB_PATH)
            result_summary["screener_summary"] = {
                "top_count": len(top_results),
                "top_sample": [f"{r.get('stock_id')} {r.get('stock_name')}" for r in top_results[:5]] if top_results else []
            }
            result_summary["actions_taken"].append(f"全市場四大因子截面跑分完成 (Top {len(top_results)} 檔)")
        except Exception as e:
            err_msg = f"跑分模組例外: {e}"
            logger.error(err_msg)
            result_summary["errors"].append(err_msg)

        # Step 7: 發布最新訊號與 Git 推送至雲端 Streamlit.app
        update_progress(95, "7/7 正在產出親友版訊號快照並推送至雲端 Streamlit.app...")
        try:
            from signal_publisher import export_and_publish_signals
            pub_res = export_and_publish_signals()
            result_summary["git_status"] = pub_res.get("git_status", {})
            result_summary["data_date"] = pub_res.get("data_date", targets["kline_date"])
            result_summary["actions_taken"].append(f"已產出最新信號快照 (基準日: {result_summary['data_date']})")
            if pub_res.get("git_status", {}).get("git_pushed"):
                result_summary["actions_taken"].append("Git Push 成功，雲端 Streamlit.app 自動重載中")
            result_summary["success"] = True
        except Exception as e:
            err_msg = f"雲端發布與推送例外: {e}"
            logger.error(err_msg)
            result_summary["errors"].append(err_msg)

        update_progress(100, "✅ 全自動盤後智能核對、數據同步與雲端發布全部完成！")

    except Exception as overall_e:
        logger.error(f"智能同步管線重大異常: {overall_e}")
        result_summary["errors"].append(str(overall_e))
        update_progress(100, f"❌ 同步終止: {overall_e}")

    result_summary["end_time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return result_summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print("=" * 60)
    print("🚀 啟動本地 DuckDB 數據核驗測試...")
    print("=" * 60)
    report = audit_duckdb_freshness()
    print(json.dumps(report, ensure_ascii=False, indent=2))
