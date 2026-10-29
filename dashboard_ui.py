# -*- coding: utf-8 -*-
"""
dashboard_ui.py
================
新科狀元・事件量化作戰中心 & 親友白話投資導航 (一站式整合版)
特點：
1. 頂部雙模式切換膠囊 (⚔️ 老手量化作戰中心 ⇄ 🧭 親友白話投資導航)
2. 頂置 N7 終極本金守護者 (Guardian V7) 即時 HUD (含 08:30 夜盤攔買單 + 09:00 開盤防雙巴)
3. 工具列收斂為「⚡ 一鍵盤後全量同步與發布」+「進階個別工具折疊抽屜」，徹底杜絕橫向擠壓
4. 表格欄位嚴格精簡，100% 容器寬度自適應，保證零橫向捲軸
5. 深色高保定美學：#0a0a0a 背景、#111827 微霧卡片、#10b981 翡翠綠強調色、16px 圓角
"""

import sys
import os
import json
import datetime
import importlib
import pandas as pd
import streamlit as st

_project_root = os.path.dirname(os.path.abspath(__file__))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

st.set_page_config(
    page_title="新科狀元・事件量化作戰中心",
    page_icon="⚔️",
    layout="wide",
    initial_sidebar_state="collapsed"
)

import api_client
import components_weapons as components

def render_html(html_str: str):
    """安全渲染 HTML，去除每行多餘縮排與空行，徹底防止 CommonMark 觸發 4-space 縮排程式碼區塊"""
    cleaned = " ".join(line.strip() for line in html_str.splitlines() if line.strip())
    st.markdown(cleaned, unsafe_allow_html=True)

import disposal_event_scanner

importlib.reload(api_client)
importlib.reload(disposal_event_scanner)
importlib.reload(components)

PROJECT_ROOT = _project_root
SIGNALS_FILE = os.path.join(PROJECT_ROOT, "published_signals.json")

# ============================================================
# Session State Initialize
# ============================================================
if "is_syncing_all" not in st.session_state: st.session_state.is_syncing_all = False
if "is_syncing_score" not in st.session_state: st.session_state.is_syncing_score = False
if "is_syncing_revenue" not in st.session_state: st.session_state.is_syncing_revenue = False
if "is_syncing_revenue_moneydj" not in st.session_state: st.session_state.is_syncing_revenue_moneydj = False
if "is_syncing_financials" not in st.session_state: st.session_state.is_syncing_financials = False
if "is_syncing_disposal" not in st.session_state: st.session_state.is_syncing_disposal = False
if "is_syncing_taiex" not in st.session_state: st.session_state.is_syncing_taiex = False
if "is_syncing_klines" not in st.session_state: st.session_state.is_syncing_klines = False
if "is_exporting_signals" not in st.session_state: st.session_state.is_exporting_signals = False
if "export_signal_result" not in st.session_state: st.session_state.export_signal_result = None
if "sync_success_msg" not in st.session_state: st.session_state.sync_success_msg = None

# ============================================================
# CSS: 深色模式高保定微霧美學 (#0a0a0a + #10b981 + 16px 圓角)
# ============================================================
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Urbanist:wght@400;600;700;900&family=Noto+Sans+TC:wght@400;500;700;900&display=swap');

:root {
    --color-bg:      #0a0a0a;
    --color-card:    #111827;
    --color-up:      #10B981;
    --color-down:    #F43F5E;
    --color-amber:   #F59E0B;
    --color-blue:    #38BDF8;
    --color-panel:   rgba(255, 255, 255, 0.03);
    --color-text:    #E2E8F0;
    --color-muted:   #94A3B8;
    --color-border:  rgba(255, 255, 255, 0.08);
}

.stApp {
    background-color: var(--color-bg) !important;
    color: var(--color-text) !important;
    font-family: 'Urbanist', 'Noto Sans TC', sans-serif !important;
}

[data-testid="stAppViewContainer"] {
    background-color: var(--color-bg) !important;
    color: var(--color-text) !important;
    overflow-x: hidden !important;
}

.main {
    overflow-x: hidden !important;
}

.main .block-container {
    padding-top: 0.6rem !important;
    padding-bottom: 2rem !important;
    padding-left: 1.2rem !important;
    padding-right: 1.2rem !important;
    max-width: 100% !important;
    overflow-x: hidden !important;
}

h1, h2, h3, h4, h5, h6 {
    color: #FFFFFF !important;
    font-family: 'Urbanist', 'Noto Sans TC', sans-serif !important;
    font-weight: 900 !important;
}

/* 頂部切換膠囊按鈕美化 */
[data-testid="stRadio"] > div {
    background: #111827 !important;
    padding: 2px !important;
    border-radius: 12px !important;
    border: 1px solid rgba(255, 255, 255, 0.1) !important;
    display: flex !important;
    gap: 4px !important;
    justify-content: flex-end !important;
}
[data-testid="stRadio"] label {
    background: transparent !important;
    border-radius: 8px !important;
    padding: 6px 14px !important;
    cursor: pointer !important;
    border: none !important;
    margin: 0 !important;
}
[data-testid="stRadio"] label[data-checked="true"],
[data-testid="stRadio"] div[role="radiogroup"] label:has(input:checked) {
    background: #10B981 !important;
    color: #0a0a0a !important;
    font-weight: 900 !important;
    box-shadow: 0 2px 8px rgba(16, 185, 129, 0.35) !important;
}
[data-testid="stRadio"] label p,
[data-testid="stRadio"] label span {
    font-size: 13px !important;
    font-weight: 800 !important;
    margin: 0 !important;
}

/* 訊息卡片 */
.info-box {
    background-color: var(--color-card);
    padding: 18px 20px;
    border-radius: 16px;
    border: 1px solid var(--color-border);
    box-shadow: 0 4px 16px rgba(0,0,0,0.4);
    margin-bottom: 16px;
}

.weapon-title {
    font-size: 20px;
    font-weight: 900;
    margin-bottom: 12px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--color-border);
}
.weapon-1 { color: #38bdf8; border-bottom-color: rgba(56,189,248,0.3); }
.weapon-3 { color: #f59e0b; border-bottom-color: rgba(245,158,11,0.3); }

/* 標籤小標記 */
.tag {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 800;
    margin-right: 6px;
}
.tag-moo { background: rgba(16,185,129,0.15); border: 1px solid #10b981; color: #10b981; }
.tag-moc { background: rgba(56,189,248,0.15); border: 1px solid #38bdf8; color: #38bdf8; }
.tag-night { background: rgba(99,102,241,0.15); border: 1px solid #818cf8; color: #a5b4fc; }

/* 消除水平捲軸與邊距自適應 */
.stDataFrame {
    width: 100% !important;
}
[data-testid="stDataFrame"] > div {
    max-width: 100% !important;
}

/* 親友白話版專用巨無霸字體與直式卡片 */
.companion-card {
    background: #111827;
    border-radius: 16px;
    padding: 24px 22px;
    margin-bottom: 18px;
    border: 1px solid rgba(255, 255, 255, 0.08);
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
}
.companion-hero-title {
    font-size: 36px;
    font-weight: 900;
    line-height: 1.25;
    color: #F8FAFC;
    margin-bottom: 8px;
}
.companion-hero-sub {
    font-size: 20px;
    font-weight: 700;
    color: #94A3B8;
    line-height: 1.4;
}
.companion-badge {
    display: inline-block;
    font-size: 18px;
    font-weight: 900;
    padding: 6px 14px;
    border-radius: 10px;
    margin-right: 8px;
    margin-bottom: 6px;
}
</style>
"""

st.markdown(CSS, unsafe_allow_html=True)


# ============================================================
# 執行一鍵全自動盤後同步管線 (Smart Pipeline - 智能時效核驗與精準同步)
# ============================================================
def execute_smart_sync_pipeline():
    """自動核對 DuckDB 新鮮度 -> FinMind API 按需下載/清洗/寫入 -> 跑分 -> 雲端發布"""
    progress_bar = st.progress(0, text="🔍 正在自動核驗本地 DuckDB 資料時效性...")
    try:
        from data_providers.smart_sync_manager import run_smart_sync_and_publish

        def on_progress(pct: int, msg: str):
            progress_bar.progress(pct, text=msg)

        res = run_smart_sync_and_publish(force_all=False, progress_callback=on_progress)
        st.session_state.smart_sync_result = res

        # 讀取發布結果供 UI 狀態卡片展示
        from signal_publisher import SIGNALS_FILE
        sig_data = {}
        if os.path.exists(SIGNALS_FILE):
            with open(SIGNALS_FILE, "r", encoding="utf-8") as f:
                sig_data = json.load(f)

        c1 = sig_data.get("core_1_swing", {})
        c3 = sig_data.get("core_3_disposal", {})

        export_res = {
            "success": res.get("success", True),
            "data_date": res.get("data_date", sig_data.get("data_date", "")),
            "top_9_count": len(c1.get("top_9", [])),
            "buffer_count": len(c1.get("buffer_10_15", [])),
            "hits_b_count": len(c3.get("strategy_b_preemptive", [])),
            "git_status": res.get("git_status", {}),
            "actions_taken": res.get("actions_taken", []),
            "audit_report": res.get("audit_report", {})
        }
        st.session_state.export_signal_result = export_res
        st.session_state.sync_success_msg = f"🎉 盤後智能核對與同步完成！資料基準: {export_res.get('data_date')}，Top 9 名單與處置狙擊已全面推送雲端！"
        st.cache_data.clear()

    except Exception as e:
        st.session_state.sync_success_msg = f"❌ 智能同步管線異常: {str(e)}"
    finally:
        st.session_state.is_syncing_all = False


# ============================================================
# 親友白話投資導航視圖 (Companion View)
# ============================================================
def render_companion_view():
    render_html("""
    <div style='text-align: center; margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid rgba(255,255,255,0.08);'>
        <div style='font-size: 34px; font-weight: 900; background: linear-gradient(135deg, #ffffff 0%, #10b981 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent;'>
            🧭 白話投資導航・親友與老手指南
        </div>
        <div style='font-size: 16px; color: #94a3b8; margin-top: 6px;'>
            高鐵時刻表級大字看板 ｜ 零股配資試算 ｜ 一鍵健檢持股 ｜ 10字口訣避雷
        </div>
    </div>
    """)

    if not os.path.exists(SIGNALS_FILE):
        st.warning("🚧 尚未找到 published_signals.json 訊號檔。請於老手作戰室點擊「⚡ 一鍵全自動盤後同步與發布」！")
        return

    try:
        with open(SIGNALS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        st.error(f"讀取訊號檔失敗: {e}")
        return

    # 最上方發布休市快報卡片 (如有生效中休市公告)
    holiday_notice = data.get("holiday_notice", {})
    if holiday_notice and holiday_notice.get("is_active") is True:
        notice_title = holiday_notice.get("title", "")
        notice_msg = holiday_notice.get("message", "")
        if notice_title:
            render_html(f"""
            <div style='background: rgba(245, 158, 11, 0.12); border: 2px solid #f59e0b; border-left: 8px solid #f59e0b; border-radius: 16px; padding: 20px 24px; margin-bottom: 22px;'>
                <div style='display: flex; align-items: flex-start; gap: 16px;'>
                    <div style='font-size: 40px; line-height: 1;'>🏖️</div>
                    <div style='flex: 1;'>
                        <div style='font-size: 26px; font-weight: 900; color: #fbbf24; line-height: 1.3; margin-bottom: 6px;'>
                            【重要休市公告】{notice_title}
                        </div>
                        <div style='font-size: 16px; font-weight: 500; color: #fde68a; line-height: 1.6;'>
                            {notice_msg}
                        </div>
                    </div>
                </div>
            </div>
            """)

    data_date = data.get("data_date", "2026-01-01")
    market = data.get("market_regime", {})
    core1 = data.get("core_1_swing", {})
    core2 = data.get("core_2_disposal", {})
    all_stocks = data.get("all_searchable_stocks", {})

    n7 = market.get("n7_guardian", {})
    rec_equity = int(market.get("recommended_equity_pct", 50))
    rec_cash = int(market.get("recommended_cash_pct", 50))
    weather_title = market.get("weather_title", "⛅ 多雲警戒")
    weather_desc = market.get("weather_desc", "")
    taiex_c = market.get("taiex_close", 0)

    # 1. 股市天氣預報 (大字看板 + N7 夜盤攔截巨幅警告)
    weather_border = "#10b981" if rec_equity >= 80 else ("#f59e0b" if rec_equity >= 50 else "#f43f5e")
    weather_icon = "☀️" if rec_equity >= 80 else ("⛅" if rec_equity >= 50 else "⛈️")

    render_html(f"""
    <div class='companion-card' style='border-top: 6px solid {weather_border}; text-align: center;'>
        <div style='font-size: 64px; line-height: 1.1;'>{weather_icon}</div>
        <div class='companion-hero-title' style='color: {weather_border};'>{weather_title}（建議持股上限：{rec_equity}%）</div>
        <div style='font-size: 15px; color: #94a3b8; margin-top: 6px;'>
            最新大盤: <b>{taiex_c:,.0f} 點</b> ｜ 資料基準日: <b>{data_date}</b>
        </div>
        <div style='font-size: 18px; font-weight: 700; color: #cbd5e1; margin-top: 14px; line-height: 1.5;'>
            {weather_desc}
        </div>
    </div>
    """)

    # N7 夜盤攔截專屬警告卡
    if n7.get("night_intercept_active"):
        render_html(f"""
        <div class='companion-card' style='border: 2px solid #f43f5e; background: rgba(244,63,94,0.08);'>
            <div style='font-size: 22px; font-weight: 900; color: #f43f5e; margin-bottom: 6px;'>
                🛑 【N7 守護者警告：夜盤重挫摜破季線！】
            </div>
            <div style='font-size: 16px; color: #fecdd3; line-height: 1.6;'>
                <b>{n7.get('night_intercept_reason')}</b><br>
                👉 <b>親友操盤口訣</b>：今天開盤「絕對不要買任何新股票」！手中持股若跌出15名優先賣出換現金！
            </div>
        </div>
        """)
    elif n7.get("mod_c_triggered"):
        render_html(f"""
        <div class='companion-card' style='border: 2px solid #f59e0b; background: rgba(245,158,11,0.08);'>
            <div style='font-size: 22px; font-weight: 900; color: #f59e0b; margin-bottom: 6px;'>
                ⚡ 【09:00 開盤防雙巴：極度超賣防錯殺！】
            </div>
            <div style='font-size: 16px; color: #fde68a; line-height: 1.6;'>
                <b>{n7.get('mod_c_reason')}</b><br>
                👉 <b>親友操盤口訣</b>：開盤不要慌張全砍在地板！減碼單分兩批，若盤中反彈站回即可保住獲利！
            </div>
        </div>
        """)

    # 2. 巨型零股配資試算機
    st.markdown("<div class='companion-hero-title' style='font-size: 24px;'>💰 巨型零股配資試算機</div>", unsafe_allow_html=True)
    user_capital = st.number_input(
        "輸入預計投資總本金 (新台幣元)",
        min_value=10000,
        max_value=100000000,
        value=300000,
        step=50000,
        key="companion_cap"
    )

    investable_total = user_capital * (rec_equity / 100.0)
    cash_reserve = user_capital - investable_total
    single_stock_budget = investable_total / 9.0 if rec_equity > 0 else 0.0

    c_c1, c_c2, c_c3 = st.columns(3)
    with c_c1:
        render_html(f"""
        <div class='companion-card' style='border-left: 6px solid #10b981; text-align: center;'>
            <div style='font-size: 14px; color: #94a3b8;'>📈 可買股票總額 ({rec_equity}%)</div>
            <div style='font-size: 28px; font-weight: 900; color: #10b981; margin-top: 4px;'>${investable_total:,.0f}</div>
        </div>
        """)
    with c_c2:
        render_html(f"""
        <div class='companion-card' style='border-left: 6px solid #f43f5e; text-align: center;'>
            <div style='font-size: 14px; color: #94a3b8;'>🏦 必留守防守現金 ({rec_cash}%)</div>
            <div style='font-size: 28px; font-weight: 900; color: #f43f5e; margin-top: 4px;'>${cash_reserve:,.0f}</div>
        </div>
        """)
    with c_c3:
        render_html(f"""
        <div class='companion-card' style='border-left: 6px solid #38bdf8; text-align: center;'>
            <div style='font-size: 14px; color: #94a3b8;'>🎯 前9強每檔預算</div>
            <div style='font-size: 28px; font-weight: 900; color: #38bdf8; margin-top: 4px;'>${single_stock_budget:,.0f}</div>
        </div>
        """)

    # 3. 持股一鍵健檢
    st.markdown("<div class='companion-hero-title' style='font-size: 24px; margin-top: 20px;'>🔍 「我手上的股票怎麼辦？」一鍵健檢</div>", unsafe_allow_html=True)
    search_query = st.text_input("輸入 4 碼股票代號 (例如: 6672, 3287, 2330, 2454)", placeholder="請輸入代號", key="companion_search").strip()
    if search_query:
        target_info = all_stocks.get(search_query)
        if target_info:
            t_name = target_info.get("name", "")
            t_rank = target_info.get("rank", 999)
            t_close = target_info.get("close", 0.0)

            if isinstance(t_rank, int) and t_rank <= 9:
                render_html(f"""
                <div class='companion-card' style='border-left: 6px solid #10b981; background: rgba(16,185,129,0.1);'>
                    <div style='font-size: 22px; font-weight: 900; color: #10b981;'>
                        🟢 {search_query} {t_name}（目前第 {t_rank} 名 ｜ 現價: {t_close:.2f}）
                    </div>
                    <div style='font-size: 16px; color: #a7f3d0; margin-top: 8px;'>
                        👉 <b>行動口訣</b>：動能霸榜前 9 強！安心抱牢，享受波段飆升！
                    </div>
                </div>
                """)
            elif isinstance(t_rank, int) and t_rank <= 15:
                render_html(f"""
                <div class='companion-card' style='border-left: 6px solid #f59e0b; background: rgba(245,158,11,0.1);'>
                    <div style='font-size: 22px; font-weight: 900; color: #f59e0b;'>
                        🟡 {search_query} {t_name}（目前第 {t_rank} 名 ｜ 現價: {t_close:.2f}）
                    </div>
                    <div style='font-size: 16px; color: #fde68a; margin-top: 8px;'>
                        👉 <b>行動口訣</b>：處於安全緩衝區！續抱不賣，亦無須追高加碼！
                    </div>
                </div>
                """)
            else:
                render_html(f"""
                <div class='companion-card' style='border-left: 6px solid #f43f5e; background: rgba(244,63,94,0.1);'>
                    <div style='font-size: 22px; font-weight: 900; color: #f43f5e;'>
                        🔴 {search_query} {t_name}（已跌出 15 名外 ｜ 現價: {t_close:.2f}）
                    </div>
                    <div style='font-size: 16px; color: #fecdd3; margin-top: 8px;'>
                        👉 <b>行動口訣</b>：動能衰退！明日開盤 09:00 嚴守紀律市價賣出換現金！
                    </div>
                </div>
                """)
        else:
            st.info(f"股票 {search_query} 未進入今日全市場動能篩選名單。")

    # 4. 直式波段輪動看板 (十方極速陣 Top 9 / 10~15 緩衝)
    st.markdown("<div class='companion-hero-title' style='font-size: 24px; margin-top: 24px;'>📈 直式波段輪動前 9 強</div>", unsafe_allow_html=True)
    top_9_cards = core1.get("top_9", [])
    for s in top_9_cards:
        tags_html = " ".join([f"<span class='companion-badge' style='background:rgba(16,185,129,0.15); color:#10b981; border:1px solid #10b981;'>{t}</span>" for t in s.get("highlights", [])])
        warn_html = f"<div style='font-size:13px; color:#fbbf24; margin-top:6px;'>{s['warning_tag']}</div>" if s.get("warning_tag") else ""
        
        render_html(f"""
        <div class='companion-card' style='border-left: 6px solid #10b981; margin-bottom: 12px;'>
            <div style='display: flex; justify-content: space-between; align-items: center;'>
                <div>
                    <span style='font-size: 24px; font-weight: 900; color: #fff;'>第 {s['rank']} 名：{s['stock_id']} {s['name']}</span>
                    <span style='font-size: 16px; color: #94a3b8; margin-left: 12px;'>現價: {s['close']:.2f} ｜ 10日動能: {s['momentum_pct']:+.1f}% ｜ 營收年增: {s['revenue_yoy_pct']:+.1f}%</span>
                </div>
                <span style='background:#10b981; color:#0a0a0a; font-weight:900; padding:4px 10px; border-radius:6px; font-size:13px;'>🎯 前9買進</span>
            </div>
            <div style='margin-top: 8px;'>{tags_html}</div>
            {warn_html}
        </div>
        """)


# ============================================================
# 老手量化實戰作戰室視圖 (Veteran War Room View)
# ============================================================
def render_veteran_war_room(portfolio_data, market_regime):
    # 1. 頂部 N7 終極本金守護者 HUD
    components.render_n7_guardian_hud(market_regime)

    # 2. 智能極簡盤後同步列 (收斂為一鍵全自動 + 即時推送雲端 + 個別抽屜)
    col_main_sync, col_pub_btn, col_adv_btn = st.columns([0.44, 0.38, 0.18])
    with col_main_sync:
        if st.session_state.is_syncing_all:
            st.button("⏳ 全自動盤後管線執行中...", disabled=True, use_container_width=True)
            execute_smart_sync_pipeline()
            st.rerun()
        else:
            if st.button("⚡ 一鍵盤後全自動同步與發布", type="primary", use_container_width=True):
                st.session_state.is_syncing_all = True
                st.rerun()

    with col_pub_btn:
        if st.session_state.is_exporting_signals:
            st.button("⏳ 正在推播至雲端 Streamlit.app...", disabled=True, use_container_width=True)
            try:
                from signal_publisher import export_and_publish_signals
                res = export_and_publish_signals()
                st.session_state.export_signal_result = res
                if res.get("success") and res.get("git_status", {}).get("git_pushed"):
                    st.toast("🚀 雲端親友版已成功推播！Streamlit.app 正自動熱重載中...", icon="✅")
            except Exception as e:
                st.session_state.export_signal_result = {"success": False, "message": str(e)}
            st.session_state.is_exporting_signals = False
            st.rerun()
        else:
            if st.button("🚀 一鍵即時推送雲端親友版 (Streamlit.app)", use_container_width=True):
                st.session_state.is_exporting_signals = True
                st.rerun()

    with col_adv_btn:
        show_adv = st.toggle("🛠️ 個別抽屜", value=False, key="toggle_adv_sync")

    if show_adv:
        with st.container():
            c1, c2, c3, c4, c5, c6 = st.columns(6)
            with c1:
                if st.button("🚀 單獨跑分", use_container_width=True):
                    try:
                        api_client.run_screener()
                        st.success("跑分完成！")
                        st.cache_data.clear()
                    except Exception as e:
                        st.error(f"錯誤: {e}")
            with c2:
                if st.button("🚨 同步處置", use_container_width=True):
                    try:
                        from disposal_event_scanner import DisposalEventScanner
                        cnt = DisposalEventScanner().sync_disposals()
                        st.success(f"處置更新 {cnt} 筆！")
                        st.cache_data.clear()
                    except Exception as e:
                        st.error(f"錯誤: {e}")
            with c3:
                if st.button("📰 MoneyDJ營收", use_container_width=True):
                    try:
                        from moneydj_revenue_scraper import scrape_moneydj_revenue
                        res = scrape_moneydj_revenue()
                        st.success(f"今日快報已更新 {res.get('count', 0)} 檔！")
                        st.cache_data.clear()
                    except Exception as e:
                        st.error(f"錯誤: {e}")
            with c4:
                if st.button("⚡ FinMind營收", use_container_width=True):
                    try:
                        res = api_client.update_revenue()
                        if isinstance(res, dict) and res.get("status") == "success":
                            st.success(res.get("message", "全市場月營收同步成功！"))
                        else:
                            st.info("全市場月營收同步完成！")
                        st.cache_data.clear()
                    except Exception as e:
                        st.error(f"錯誤: {e}")
            with c5:
                if st.button("📊 FinMind財報", use_container_width=True):
                    try:
                        res = api_client.update_financials()
                        if isinstance(res, dict) and res.get("status") == "success":
                            st.success(res.get("message", "全市場財報與ROE同步成功！"))
                        else:
                            st.info("全市場季財報同步完成！")
                        st.cache_data.clear()
                    except Exception as e:
                        st.error(f"錯誤: {e}")
            with c6:
                if st.button("📈 同步大盤", use_container_width=True):
                    try:
                        api_client.update_taiex()
                        st.success("大盤夜盤已更新")
                        st.cache_data.clear()
                    except Exception as e:
                        st.error(f"錯誤: {e}")

    # 雲端親友版連動狀態卡片 (含直達 Streamlit.app 按鈕)
    if st.session_state.export_signal_result:
        res = st.session_state.export_signal_result
        if res.get("success"):
            git_info = res.get("git_status", {})
            pushed_at = git_info.get("pushed_at", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            commit_hash = git_info.get("commit_hash", "")
            cloud_url = git_info.get("cloud_url", "https://investment-navigation.streamlit.app")
            
            actions = res.get("actions_taken", [])
            actions_html = f"<div style='font-size: 12px; color: #94a3b8; margin-top: 5px; border-top: 1px dashed rgba(255,255,255,0.12); padding-top: 4px;'>🔍 <b>智能核驗行動:</b> {' ｜ '.join(actions)}</div>" if actions else ""
            c_msg, c_btn, c_close = st.columns([0.68, 0.22, 0.10])
            with c_msg:
                st.markdown(f"""
                <div style='background: rgba(16, 185, 129, 0.10); border-left: 5px solid #10b981; border-radius: 8px; padding: 10px 16px;'>
                    <div style='font-size: 15px; font-weight: bold; color: #10b981;'>
                        🚀 雲端親友版 (Streamlit.app) 連動推送成功！
                    </div>
                    <div style='font-size: 13px; color: #cbd5e1; margin-top: 4px;'>
                        📅 資料基準: <b>{res.get('data_date')}</b> ｜ ⏱️ 推送時間: <b>{pushed_at}</b> ｜ 🔀 Commit: <code>{commit_hash or 'latest'}</code><br>
                        📊 前 9 強 <b>{res.get('top_9_count')}</b> 檔 ｜ 緩衝 <b>{res.get('buffer_count')}</b> 檔 ｜ 處置策略B <b>{res.get('hits_b_count')}</b> 檔 ｜ 休市公告已全面同步
                    </div>
                    {actions_html}
                </div>
                """, unsafe_allow_html=True)
            with c_btn:
                st.link_button("🔗 開啟 Streamlit.app", cloud_url, use_container_width=True)
            with c_close:
                if st.button("✖ 關閉", key="dismiss_export_msg", use_container_width=True):
                    st.session_state.export_signal_result = None
                    st.rerun()
        else:
            st.error(f"❌ 雲端推送失敗: {res.get('message')}")
            if st.button("✖ 關閉提示", key="dismiss_export_msg"):
                st.session_state.export_signal_result = None
                st.rerun()

    # 4. 核心一：波段輪動 (十方極速陣)
    components.render_weapon1_swing(portfolio_data, market_regime)

    st.markdown("<div style='margin: 18px 0;'><hr style='border: none; border-top: 1px solid rgba(255,255,255,0.08);'></div>", unsafe_allow_html=True)

    # 5. 核心二：處置出關狙擊 (策略 B 搶跑 ｜ 策略 C 開高走低做空)
    components.render_weapon3_disposal()

    st.markdown("<div style='margin: 18px 0;'><hr style='border: none; border-top: 1px solid rgba(255,255,255,0.08);'></div>", unsafe_allow_html=True)

    # 6. 全市場 Top 30 觀測站
    components.render_super_grid_v2(portfolio_data)


# ============================================================
# Main Entry Point
# ============================================================
def main():
    if st.session_state.sync_success_msg:
        st.info(st.session_state.sync_success_msg, icon="ℹ️")
        st.session_state.sync_success_msg = None

    # 頂部模式切換膠囊
    col_header, col_mode = st.columns([0.66, 0.34])
    with col_header:
        render_html("""
        <div>
            <h1 style='margin: 0; font-size: 22px; background: linear-gradient(135deg, #fff 0%, #10b981 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent;'>
                ⚔️ 新科狀元・事件量化作戰中心
            </h1>
            <div style='margin-top: 3px; font-size: 12px; color: #94a3b8;'>
                <span class='tag tag-night'>🛡️ N7 終極守護</span>
                <span class='tag tag-moo'>五大濾網 + 5000萬甜區</span>
                <span class='tag tag-moc'>前 9 買 ｜ 15 賣</span>
            </div>
        </div>
        """)

    with col_mode:
        selected_mode = st.radio(
            "系統導航模式",
            ["⚔️ 老手量化作戰", "🧭 親友白話導航"],
            index=0,
            horizontal=True,
            label_visibility="collapsed"
        )

    st.markdown("<div style='margin-bottom: 8px;'></div>", unsafe_allow_html=True)

    if "親友" in selected_mode:
        render_companion_view()
    else:
        with st.spinner("載入全市場作戰數據..."):
            portfolio_data = api_client.fetch_portfolio_data()
            market_regime = api_client.fetch_market_regime()

        render_veteran_war_room(portfolio_data, market_regime)


if __name__ == "__main__":
    main()
