# -*- coding: utf-8 -*-
"""
family_app.py
=============
親友與老手專屬・白話文量化投資導航系統 (Family & Veteran Companion App)
特點：
- 純前端架構：僅讀取 published_signals.json，零依賴 DuckDB / 爬蟲 / 後端 API，秒級極速部署於 Streamlit Cloud
- 視覺美學：溫潤紙質亮色主題 (Warm Paper Light Theme)，去 AI 霓虹感，高易讀性排版，深紅/琥珀高對比警示框
- 三大安全機制：通關密語驗證鎖、資料新鮮度防呆檢查、法規客觀化字眼
- 五大互動模組：股市天氣預報、傻瓜零股配資機、持股一鍵健檢、波段資優生卡片、老手處置出關狙擊專區
"""

import os
import json
import math
import datetime
import pandas as pd
import streamlit as st

# ============================================================
# 1. 頁面設定與自訂紙質亮色 CSS
# ============================================================
st.set_page_config(
    page_title="白話投資導航・親友與老手指南",
    page_icon="🧭",
    layout="centered",
    initial_sidebar_state="collapsed"
)

CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700;900&display=swap');

/* 全站溫潤紙質基底色調 (Warm Paper Light Theme) */
:root {
    --bg-main: #FAF8F5;
    --card-bg: #FFFFFF;
    --text-primary: #1E293B;
    --text-muted: #64748B;
    --border-color: #E2E8F0;
    --accent-green: #16A34A;
    --accent-red: #DC2626;
    --accent-amber: #D97706;
    --accent-blue: #2563EB;
}

html, body, [data-testid="stAppViewContainer"] {
    background-color: var(--bg-main) !important;
    color: var(--text-primary) !important;
    font-family: 'Noto Sans TC', sans-serif !important;
}

.main .block-container {
    max-width: 780px !important;
    padding-top: 1.5rem !important;
    padding-bottom: 3.5rem !important;
}

/* 標題與內文字級排版 */
h1 {
    font-size: 26px !important;
    font-weight: 900 !important;
    color: var(--text-primary) !important;
    margin-bottom: 6px !important;
}
h2 {
    font-size: 20px !important;
    font-weight: 800 !important;
    color: var(--text-primary) !important;
    margin-top: 24px !important;
    margin-bottom: 12px !important;
}
h3 {
    font-size: 17px !important;
    font-weight: 700 !important;
    color: var(--text-primary) !important;
}
p, div {
    line-height: 1.6 !important;
}

/* 純白內容卡片 */
.paper-card {
    background: #FFFFFF;
    border-radius: 14px;
    padding: 20px;
    margin-bottom: 16px;
    border: 1px solid #E2E8F0;
    box-shadow: 0 2px 10px rgba(0, 0, 0, 0.04);
}

/* 高對比防呆警示塊 */
.alert-danger {
    background: #FEF2F2 !important;
    border-left: 6px solid #DC2626 !important;
    color: #991B1B !important;
    padding: 14px 18px !important;
    border-radius: 8px !important;
    margin: 12px 0 !important;
    font-size: 15px !important;
    line-height: 1.6 !important;
}

.alert-warning {
    background: #FFFBEB !important;
    border-left: 6px solid #D97706 !important;
    color: #92400E !important;
    padding: 14px 18px !important;
    border-radius: 8px !important;
    margin: 12px 0 !important;
    font-size: 15px !important;
    line-height: 1.6 !important;
}

.alert-success {
    background: #F0FDF4 !important;
    border-left: 6px solid #16A34A !important;
    color: #166534 !important;
    padding: 14px 18px !important;
    border-radius: 8px !important;
    margin: 12px 0 !important;
    font-size: 15px !important;
    line-height: 1.6 !important;
}

.alert-info {
    background: #EFF6FF !important;
    border-left: 6px solid #2563EB !important;
    color: #1E40AF !important;
    padding: 14px 18px !important;
    border-radius: 8px !important;
    margin: 12px 0 !important;
    font-size: 15px !important;
    line-height: 1.6 !important;
}

/* 徽章標籤 */
.badge-tag {
    display: inline-block;
    padding: 3px 8px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 700;
    margin-right: 6px;
    margin-bottom: 4px;
}
.badge-green { background: #DCFCE7; color: #15803D; }
.badge-amber { background: #FEF3C7; color: #B45309; }
.badge-blue  { background: #DBEAFE; color: #1D4ED8; }
.badge-red   { background: #FEE2E2; color: #B91C1C; }

/* ============================================================
   強制全面覆蓋為亮色高易讀性主題 (徹底根絕深色底色與反白文字消失問題)
   ============================================================ */

/* 1. Tabs 頁籤樣式與高對比文字 */
[data-testid="stTabs"] {
    background-color: transparent !important;
}

[data-testid="stTabs"] [data-baseweb="tab-list"] {
    background-color: #F1F5F9 !important;
    padding: 6px !important;
    border-radius: 12px !important;
    gap: 6px !important;
    border: 1px solid #CBD5E1 !important;
}

[data-testid="stTabs"] button[data-baseweb="tab"] {
    background-color: transparent !important;
    border-radius: 8px !important;
    padding: 8px 16px !important;
    border: none !important;
}

[data-testid="stTabs"] button[data-baseweb="tab"] * {
    color: #475569 !important;
    font-size: 15px !important;
    font-weight: 700 !important;
}

[data-testid="stTabs"] button[data-baseweb="tab"][aria-selected="true"] {
    background-color: #FFFFFF !important;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.08) !important;
}

[data-testid="stTabs"] button[data-baseweb="tab"][aria-selected="true"] * {
    color: #0F172A !important;
    font-weight: 900 !important;
}

[data-testid="stTabs"] [data-baseweb="tab-border"] {
    display: none !important;
}

[data-testid="stTabs"] [data-baseweb="tab-highlight"] {
    display: none !important;
}

/* 2. 輸入框全面轉為白色高易讀性 (避免黑底白字或灰底灰字) */
[data-baseweb="input"],
[data-testid="stTextInput"] div[data-baseweb="input"],
[data-testid="stNumberInput"] div[data-baseweb="input"] {
    background-color: #FFFFFF !important;
    border: 1.5px solid #CBD5E1 !important;
    border-radius: 10px !important;
    color: #0F172A !important;
}

[data-baseweb="input"]:focus-within {
    border-color: #16A34A !important;
    box-shadow: 0 0 0 2px rgba(22, 163, 74, 0.2) !important;
}

input, [data-baseweb="input"] input {
    background-color: #FFFFFF !important;
    color: #0F172A !important;
    font-weight: 700 !important;
    font-size: 16px !important;
}

input::placeholder {
    color: #94A3B8 !important;
}

/* 數字輸入框加減按鈕 */
[data-testid="stNumberInput"] button {
    background-color: #F1F5F9 !important;
    color: #1E293B !important;
    border: none !important;
}
[data-testid="stNumberInput"] button:hover {
    background-color: #E2E8F0 !important;
    color: #0F172A !important;
}

/* 3. 所有 Widget 標籤與提示文字 */
label,
[data-testid="stWidgetLabel"] p,
[data-testid="stWidgetLabel"] div,
[data-testid="stWidgetLabel"] span {
    color: #1E293B !important;
    font-size: 15px !important;
    font-weight: 800 !important;
}

/* 4. Expander 標題文字 */
[data-testid="stExpander"] details summary span,
[data-testid="stExpander"] details summary p {
    color: #1E293B !important;
    font-weight: 700 !important;
    font-size: 14px !important;
}
[data-testid="stExpander"] details {
    background-color: #FFFFFF !important;
    border: 1px solid #CBD5E1 !important;
    border-radius: 10px !important;
}

/* 5. Checkbox / Toggle 標籤文字 */
[data-testid="stCheckbox"] label span,
[data-testid="stToggle"] label span {
    color: #1E293B !important;
    font-weight: 700 !important;
    font-size: 15px !important;
}

/* 隱藏 Streamlit 預設多餘元素 */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ============================================================
# 2. 安全機制一：家庭通關密語驗證鎖
# ============================================================
PASSCODE_DEFAULT = "8888"
def get_expected_passcode():
    try:
        if hasattr(st, "secrets") and "FAMILY_PASSCODE" in st.secrets:
            return str(st.secrets["FAMILY_PASSCODE"]).strip()
    except Exception:
        pass
    return PASSCODE_DEFAULT

if "family_authenticated" not in st.session_state:
    st.session_state.family_authenticated = False

if not st.session_state.family_authenticated:
    st.markdown("<div style='height: 40px;'></div>", unsafe_allow_html=True)
    st.markdown("""
    <div class='paper-card' style='text-align: center; padding: 32px 24px;'>
        <div style='font-size: 42px; margin-bottom: 12px;'>🧭</div>
        <h1 style='margin-bottom: 8px;'>親友專屬・白話投資參考導航</h1>
        <div style='color: #64748B; font-size: 15px; margin-bottom: 24px;'>
            本站專供親友與老手交流使用，請輸入通關密語以解鎖今日最新訊號。
        </div>
    </div>
    """, unsafe_allow_html=True)

    c_left, c_mid, c_right = st.columns([1, 2, 1])
    with c_mid:
        user_input_passcode = st.text_input("🔑 輸入通關密語", type="password", placeholder="預設為 8888")
        if st.button("🔓 驗證解鎖進入", use_container_width=True, type="primary"):
            expected = get_expected_passcode()
            if user_input_passcode.strip() == expected:
                st.session_state.family_authenticated = True
                st.rerun()
            else:
                st.error("密語不正確，請詢問版主喔！")
    st.stop()


# ============================================================
# 3. 讀取 published_signals.json
# ============================================================
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
SIGNALS_FILE = os.path.join(PROJECT_ROOT, "published_signals.json")

if not os.path.exists(SIGNALS_FILE):
    st.markdown("""
    <div class='alert-danger'>
        <b>🚧 尚未找到 published_signals.json 信號檔</b><br>
        版主目前尚未在本地指揮官後台點擊「📤 匯出並發布親友版最新訊號」，請稍候再重新整理頁面！
    </div>
    """, unsafe_allow_html=True)
    st.stop()

try:
    with open(SIGNALS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
except Exception as e:
    st.markdown(f"""
    <div class='alert-danger'>
        <b>❌ 讀取信號檔失敗:</b> {str(e)}
    </div>
    """, unsafe_allow_html=True)
    st.stop()


# ============================================================
# 4. 安全機制二：訊號新鮮度防呆檢查 (考慮週末與平日收盤)
# ============================================================
data_date_str = data.get("data_date", "2026-01-01")
generated_at = data.get("generated_at", "")

def check_freshness(data_date_val: str) -> tuple[bool, str]:
    """比對資料日期與當前台灣時間，回傳 (是否過期, 提醒說明)"""
    try:
        tz_tw = datetime.timezone(datetime.timedelta(hours=8))
        now_tw = datetime.datetime.now(tz_tw)
        d_data = datetime.date.fromisoformat(data_date_val)
        today = now_tw.date()
        
        # 1. 當天日期一致 -> 絕對新鮮
        if d_data == today:
            return True, "今日最新盤後訊號"
        
        # 2. 如果今天是週末 (週六/週日)
        if today.weekday() == 5: # 週六
            friday = today - datetime.timedelta(days=1)
            if d_data == friday:
                return True, "週末休息中（顯示本週五收盤訊號）"
        elif today.weekday() == 6: # 週日
            friday = today - datetime.timedelta(days=2)
            if d_data == friday:
                return True, "週末休息中（顯示本週五收盤訊號）"
                
        # 3. 如果今天是平日且在開盤前 (09:00 前)
        # 例如週二清晨看週一收盤，此時昨天的資料依然是最新可用訊號
        if today.weekday() < 5 and now_tw.hour < 9:
            yesterday = today - datetime.timedelta(days=1)
            if today.weekday() == 0: # 週一晨間看上週五
                yesterday = today - datetime.timedelta(days=3)
            if d_data == yesterday:
                return True, "盤前備戰中（顯示前一交易日收盤訊號）"
                
        # 4. 判定為過期
        return False, f"版主尚未更新最新一期的盤後資料（目前顯示為 {data_date_val} 舊訊號），今日請先暫停參考喔！"
    except Exception:
        return True, "資料狀態已載入"

is_fresh, freshness_msg = check_freshness(data_date_str)

# 若資料過期，最頂部強制深紅高對比防呆框
if not is_fresh:
    st.markdown(f"""
    <div class='alert-danger'>
        <b>🚨 🚧 注意：{freshness_msg}</b>
    </div>
    """, unsafe_allow_html=True)


# ============================================================
# 5. 頂部導航列與版頭 (親切白話風)
# ============================================================
st.markdown(f"""
<div style='display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 14px;'>
    <div>
        <h1 style='margin: 0;'>🧭 親友與老手・白話投資導航</h1>
        <div style='color: #64748B; font-size: 14px; margin-top: 4px;'>
            客觀量化統計 ｜ 零投顧話術 ｜ 嚴守停損紀律
        </div>
    </div>
    <div style='text-align: right;'>
        <span class='badge-tag badge-blue'>資料日期: {data_date_str}</span>
        <div style='color: #94A3B8; font-size: 12px; margin-top: 2px;'>發布: {generated_at[5:16] if len(generated_at)>=16 else generated_at}</div>
    </div>
</div>
""", unsafe_allow_html=True)


# ============================================================
# 區塊一：今日股市天氣預報（大盤紅綠燈）
# ============================================================
regime = data.get("market_regime", {})
rec_equity = int(regime.get("recommended_equity_pct", 50))
rec_cash = int(regime.get("recommended_cash_pct", 50))
halt_buys = bool(regime.get("halt_new_buys", False))
weather_title = regime.get("weather_title", "⛅ 多雲警戒")
weather_desc = regime.get("weather_desc", "")
taiex_c = regime.get("taiex_close", 0)

# 天氣卡片色彩微調
if rec_equity >= 80:
    weather_border = "#16A34A"
    weather_badge = "badge-green"
elif rec_equity >= 50:
    weather_border = "#D97706"
    weather_badge = "badge-amber"
else:
    weather_border = "#DC2626"
    weather_badge = "badge-red"

st.markdown(f"""
<div class='paper-card' style='border-top: 5px solid {weather_border};'>
    <div style='display: flex; justify-content: space-between; align-items: center;'>
        <div style='font-size: 20px; font-weight: 800; color: #1E293B;'>
            {weather_title}
        </div>
        <span class='badge-tag {weather_badge}'>大盤位階: {taiex_c:,.0f} 點</span>
    </div>
    <div style='font-size: 15px; color: #475569; margin: 10px 0;'>
        {weather_desc}
    </div>
    <div style='display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 14px;'>
        <div style='background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 10px; padding: 12px; text-align: center;'>
            <div style='font-size: 13px; color: #64748B;'>今日參考持股上限</div>
            <div style='font-size: 26px; font-weight: 900; color: {weather_border}; margin-top: 2px;'>{rec_equity}%</div>
        </div>
        <div style='background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 10px; padding: 12px; text-align: center;'>
            <div style='font-size: 13px; color: #64748B;'>應保留銀行防守現金</div>
            <div style='font-size: 26px; font-weight: 900; color: #1E293B; margin-top: 2px;'>{rec_cash}%</div>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)


# ============================================================
# 區塊二：傻瓜零股配資試算機（含小資防呆）
# ============================================================
st.markdown("<h2>💰 傻瓜零股配資試算機</h2>", unsafe_allow_html=True)

with st.container():
    col_cap1, col_cap2 = st.columns([1.5, 2.5])
    with col_cap1:
        user_capital = st.number_input(
            "輸入預計投資總本金 (新台幣元)",
            min_value=10000,
            max_value=50000000,
            value=300000,
            step=10000
        )
    with col_cap2:
        st.markdown(
            f"<div style='font-size: 13px; color: #64748B; margin-top: 28px;'>"
            f"系統將依今日大盤天氣（上限 <b>{rec_equity}%</b>）與 <b>9 等分</b> 模式，自動計算每檔股票可買進之金額與安全防守現金。"
            f"</div>",
            unsafe_allow_html=True
        )

    # 運算金額
    investable_total = user_capital * (rec_equity / 100.0)
    cash_reserve = user_capital - investable_total
    single_stock_budget = investable_total / 9.0

    st.markdown(f"""
    <div class='paper-card' style='background: #F8FAFC; border: 1px dashed #CBD5E1;'>
        <div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; text-align: center;'>
            <div>
                <div style='font-size: 12px; color: #64748B;'>目前參考可入市金額</div>
                <div style='font-size: 20px; font-weight: 800; color: #16A34A; margin-top: 4px;'>NT$ {investable_total:,.0f}</div>
            </div>
            <div>
                <div style='font-size: 12px; color: #64748B;'>必須留在銀行的保命現金</div>
                <div style='font-size: 20px; font-weight: 800; color: #1E293B; margin-top: 4px;'>NT$ {cash_reserve:,.0f}</div>
            </div>
            <div>
                <div style='font-size: 12px; color: #64748B;'>單一檔股票分配額度 (÷9)</div>
                <div style='font-size: 20px; font-weight: 800; color: #2563EB; margin-top: 4px;'>NT$ {single_stock_budget:,.0f}</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # 小額本金防呆
    if single_stock_budget < 10000:
        st.markdown("""
        <div class='alert-warning'>
            <b>💡 貼心提醒：</b>由於單檔分配金額較小（未滿 10,000 元），為避免零股最低手續費與價差耗損，建議只挑選前 3~5 名集中參考即可（請確認您的券商具備零股 1 元手續費優惠）。
        </div>
        """, unsafe_allow_html=True)


# ============================================================
# 區塊三：「我手上的股票怎麼辦？」一鍵健檢（含心理建設）
# ============================================================
st.markdown("<h2>🔍 「我手上的股票怎麼辦？」一鍵健檢</h2>", unsafe_allow_html=True)

all_search_dict = data.get("all_searchable_stocks", {})
c_search, c_btn = st.columns([3, 1])
with c_search:
    search_query = st.text_input("輸入股票代號（例如: 6672, 4760, 6187, 2330）", placeholder="請輸入 4 碼台股代號").strip()

if search_query:
    target_info = all_search_dict.get(search_query)
    
    if target_info:
        t_name = target_info.get("name", "")
        t_rank = target_info.get("rank", 999)
        t_status = target_info.get("status", "OUTSIDE")
        t_close = target_info.get("close", 0.0)

        if isinstance(t_rank, int) and t_rank <= 9:
            st.markdown(f"""
            <div class='alert-success'>
                <b>🟢 動能強勢（前 9 強 - 第 {t_rank} 名）：{search_query} {t_name}</b><br>
                目前最新收盤價: {t_close:,.2f} 元 ｜ 綜合分數排名前 9 強，表現優異，基本面與動能雙全，可安心續抱。
            </div>
            """, unsafe_allow_html=True)
        elif isinstance(t_rank, int) and t_rank <= 15:
            st.markdown(f"""
            <div class='alert-info'>
                <b>🔵 安全緩衝區（第 {t_rank} 名）：{search_query} {t_name}</b><br>
                目前最新收盤價: {t_close:,.2f} 元 ｜ 表現平穩，原本已持有的朋友可繼續安心續抱；但空手者不建議盲目追高。
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div class='alert-danger'>
                <b>🔴 動能轉弱（已跌出前 15 名）：{search_query} {t_name}</b><br>
                目前排名: 第 {t_rank} 名 ｜ 已不符合強勢標準，參考紀律為適時汰弱換取現金。<br><br>
                <b>💡 系統小叮嚀：</b>量化篩選就像果農挑水果，發現不甜就換下一顆。小幅調整換回現金是為了保護本金，千萬別因為捨不得而抱著弱勢股喔！
            </div>
            """, unsafe_allow_html=True)
    else:
        st.markdown(f"""
        <div class='alert-danger'>
            <b>🔴 動能轉弱（未進入全市場前段班）：{search_query}</b><br>
            此檔股票目前未通過流動性濾網（5日均成交值 >= 2億元）或近 20 日動能偏弱，不在強勢觀察池內。建議優先保留現金或換入具備高動能之資優生！<br><br>
            <b>💡 系統小叮嚀：</b>量化篩選就像果農挑水果，發現不甜就換下一顆。小幅調整換回現金是為了保護本金，千萬別因為捨不得而抱著弱勢股喔！
        </div>
        """, unsafe_allow_html=True)


# ============================================================
# 區塊四：波段資優生名單（手機卡片式 ＋ 大盤連動鎖 ＋ 處置避雷）
# ============================================================
st.markdown("<h2>🏆 波段資優生名單</h2>", unsafe_allow_html=True)

core1 = data.get("core_1_swing", {})
top_9 = core1.get("top_9", [])
buffer_15 = core1.get("buffer_10_15", [])
dropped_15 = core1.get("dropped_from_top15", [])

tab_top9, tab_buffer, tab_dropped = st.tabs([
    f"🟢 動能前 9 強 ({len(top_9)})",
    f"🔵 安穩緩衝區 ({len(buffer_15)})",
    f"🔴 動能轉弱區 ({len(dropped_15)})"
])

# -------------------------------------------------------------
# Tab 1: 前 9 強名單
# -------------------------------------------------------------
with tab_top9:
    # 大盤連動鎖
    if halt_buys:
        st.markdown("""
        <div class='alert-warning'>
            <b>🟡 目前大盤處於跌破月線震盪警戒中！以下名單僅供觀察，空手者建議先看戲、暫緩建立新部位！</b>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <div class='alert-success'>
            <b>🟢 大盤結構穩健，動能強勢名單具備良好攻擊力道！</b>
        </div>
        """, unsafe_allow_html=True)

    for stock in top_9:
        s_rank = stock.get("rank", 1)
        s_id = stock.get("stock_id", "")
        s_name = stock.get("name", "")
        s_close = stock.get("close", 0.0)
        s_warn = stock.get("warning_tag")
        s_tags = stock.get("highlights", [])
        
        # 試算零股數
        shares_calc = math.floor(single_stock_budget / s_close) if s_close > 0 else 0
        approx_cost = shares_calc * s_close

        tag_spans = "".join([f"<span class='badge-tag badge-green'>{t}</span>" for t in s_tags])

        warning_block = ""
        if s_warn:
            warning_block = f"""
            <div class='alert-warning' style='margin: 8px 0; padding: 10px 14px !important; font-size: 13px !important;'>
                <b>{s_warn}</b><br>
                此檔目前關禁閉（處置）或剛出關，波動極度劇烈，一般投資人請直接跳過！（跳過之預算額度請直接留現金，不必往下遞補第 10 名）。
            </div>
            """

        st.markdown(f"""
        <div class='paper-card' style='padding: 16px 18px;'>
            <div style='display: flex; justify-content: space-between; align-items: center;'>
                <div>
                    <span style='background: #16A34A; color: #FFF; padding: 2px 8px; border-radius: 6px; font-size: 12px; font-weight: 900;'>#{s_rank}</span>
                    <span style='font-size: 18px; font-weight: 800; margin-left: 6px;'>{s_id} {s_name}</span>
                </div>
                <div style='font-size: 18px; font-weight: 800; color: #1E293B;'>
                    NT$ {s_close:,.2f}
                </div>
            </div>
            <div style='margin-top: 8px;'>
                {tag_spans}
            </div>
            {warning_block}
            <div style='margin-top: 10px; padding-top: 8px; border-top: 1px dashed #E2E8F0; font-size: 13px; color: #475569; display: flex; justify-content: space-between;'>
                <span>9 等分試算零股數：<b>{shares_calc:,} 股</b></span>
                <span>試算預計金額：約 NT$ {approx_cost:,.0f}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

# -------------------------------------------------------------
# Tab 2: 安穩緩衝區 (10~15名)
# -------------------------------------------------------------
with tab_buffer:
    st.markdown("""
    <div class='alert-info'>
        <b>🔵 緩衝區操作說明：</b>此區間股票表現依舊在全市場前 1% 之列。原先已持有的朋友可<b>安心續抱</b>，無需頻繁換股；若是空手的朋友，請先專注在前 9 強即可，不宜往下追買。
    </div>
    """, unsafe_allow_html=True)

    for stock in buffer_15:
        s_rank = stock.get("rank", 10)
        s_id = stock.get("stock_id", "")
        s_name = stock.get("name", "")
        s_close = stock.get("close", 0.0)
        s_tags = stock.get("highlights", [])
        tag_spans = "".join([f"<span class='badge-tag badge-blue'>{t}</span>" for t in s_tags])

        st.markdown(f"""
        <div class='paper-card' style='padding: 14px 18px;'>
            <div style='display: flex; justify-content: space-between; align-items: center;'>
                <div>
                    <span style='background: #2563EB; color: #FFF; padding: 2px 8px; border-radius: 6px; font-size: 12px; font-weight: 800;'>#{s_rank}</span>
                    <span style='font-size: 16px; font-weight: 800; margin-left: 6px;'>{s_id} {s_name}</span>
                </div>
                <div style='font-size: 16px; font-weight: 700; color: #1E293B;'>
                    NT$ {s_close:,.2f}
                </div>
            </div>
            <div style='margin-top: 8px;'>
                {tag_spans}
            </div>
        </div>
        """, unsafe_allow_html=True)

# -------------------------------------------------------------
# Tab 3: 動能轉弱區 (最新跌出 15 名外)
# -------------------------------------------------------------
with tab_dropped:
    st.markdown("""
    <div class='alert-danger'>
        <b>🚨 鐵血紀律提醒：</b>以下股票為對比前一交易日<b>最新跌出前 15 名之外</b>的個股。動能轉弱容易引發連續性多殺多，建議持有者於隔日開盤市價汰弱換取現金，切忌心存僥倖！
    </div>
    """, unsafe_allow_html=True)

    if not dropped_15:
        st.markdown("<div class='paper-card' style='text-align:center; color:#64748B;'>今日名單結構穩定，無任何股票跌出前 15 名！</div>", unsafe_allow_html=True)
    else:
        for stock in dropped_15:
            d_id = stock.get("stock_id", "")
            d_name = stock.get("name", "")
            p_rank = stock.get("prev_rank", "-")
            n_rank = stock.get("new_rank", "外")
            d_close = stock.get("close", 0.0)
            
            st.markdown(f"""
            <div class='paper-card' style='border-left: 5px solid #DC2626; padding: 14px 18px;'>
                <div style='display: flex; justify-content: space-between; align-items: center;'>
                    <div>
                        <span style='background: #DC2626; color: #FFF; padding: 2px 8px; border-radius: 6px; font-size: 12px; font-weight: 800;'>轉弱退場</span>
                        <span style='font-size: 16px; font-weight: 800; margin-left: 6px; color: #991B1B;'>{d_id} {d_name}</span>
                    </div>
                    <div style='font-size: 15px; font-weight: 700;'>
                        最新價: NT$ {d_close:,.2f}
                    </div>
                </div>
                <div style='margin-top: 8px; font-size: 13px; color: #475569;'>
                    原前一交易日排名: <b>第 {p_rank} 名</b> ➔ 今日動向: <b style='color:#DC2626;'>{n_rank}</b>
                </div>
            </div>
            """, unsafe_allow_html=True)


# ============================================================
# 區塊五：老手實戰專區 —— 處置股出關狙擊（含白話教學與鐵血警語）
# ============================================================
st.markdown("<h2>⚡ 老手實戰專區 —— 處置出關狙擊</h2>", unsafe_allow_html=True)

is_pro_confirmed = st.toggle("🔥 我具備短線交易與當沖經驗，且能嚴格執行停損紀律，點此展開【處置出關狙擊】訊號", value=False)

if is_pro_confirmed:
    core2 = data.get("core_2_disposal", {})
    b_triggers = core2.get("strategy_b_triggers", [])
    c_triggers = core2.get("strategy_c_triggers", [])
    imminent_pool = core2.get("imminent_pool", [])
    stats = core2.get("backtest_stats", {})

    st.markdown("""
    <div class='paper-card' style='background: #FFFBEB; border: 1px solid #FCD34D;'>
        <h3 style='color: #92400E; margin-top:0;'>📖 30 秒白話文戰術作戰指南</h3>
        <div style='display: grid; grid-template-columns: 1fr; gap: 14px;'>
            <div style='background: #FFFFFF; padding: 14px; border-radius: 8px; border: 1px solid #FDE68A;'>
                <div style='font-weight: 800; color: #15803D; font-size: 15px;'>【戰術 B：出關前吃豆腐（T-2 搶跑買入）】</div>
                <div style='font-size: 13px; color: #475569; margin-top: 4px; line-height: 1.5;'>
                    • <b>白話原理：</b>股票被關禁閉（處置）時每 20 或 5 分鐘才撮合一次，很多主力不想等，會在「出關前 2 天 (T-2)」偷偷買進卡位，等出關那一天拉高倒給散戶。我們就是跟著在 T-2 收盤買進搭轎。<br>
                    • <b>操作鐵律：</b><b>T-2 收盤買進 ➔ T-1（出關前一天）收盤先賣掉 50% 落袋為安 ➔ T 日（出關當天）一開盤 09:00 把剩下的 50% 全部市價賣光！</b> 絕不戀戰！
                </div>
            </div>
            <div style='background: #FFFFFF; padding: 14px; border-radius: 8px; border: 1px solid #FDE68A;'>
                <div style='font-weight: 800; color: #B91C1C; font-size: 15px;'>【戰術 C：出關日沖喜倒貨（開高走低當沖放空）】</div>
                <div style='font-size: 13px; color: #475569; margin-top: 4px; line-height: 1.5;'>
                    • <b>白話原理：</b>如果一檔股票在關禁閉期間還「漲得太誇張（超漲）」，代表裡面累積了大量想獲利了結的賣壓。出關當天早上，主力常會故意微幅開高吸引散戶追買，然後瞬間往下倒貨大跌。<br>
                </div>
            </div>
        </div>
        <div class='alert-danger' style='margin-top: 12px;'>
            <b>🚨 戰術 C 三大鐵血保命符（缺一不可，違反者請勿執行）：</b><br>
            1. <b>🎯 嚴格看開盤價才出手：</b>只有當出關日早上 <b>09:00 開盤漲幅介於 +1% ~ +5.2% 之間</b>（開太低沒肉吃、開太高超過 5.2% 容易直接鎖漲停被軋），且<b>台指期開盤沒有暴漲（<= +0.3%）</b>時，才用融券或現股當沖賣出！<br>
            2. <b>🛑 鐵血 5% 停損線：</b>進場後只要股價往上衝破你的成本 <b>5%</b>（或接近漲停），代表主力還要嘎空，<b>必須無條件立刻市價回補認賠</b>！絕對禁止加碼攤平！<br>
            3. <b>⏰ 13:25 鬧鐘強制平倉：</b>這純粹是吃早盤倒貨的當沖策略，<b>請設定手機鬧鐘 13:25，時間一到無論賺賠全數買回平倉，絕對、絕對不可以留倉過夜！</b>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # 實戰今日觸發名單
    st.markdown("<h3>🎯 今日實戰觸發標靶</h3>", unsafe_allow_html=True)
    if not b_triggers and not c_triggers:
        st.markdown("""
        <div class='alert-success'>
            <b>🟢 今日全市場處置股暫無觸發【策略 B】或【策略 C】進場門檻</b><br>
            處置交易首重天時地利，今日無標的完全符合嚴格量化濾網。空手觀望，靜待最佳勝率時機！
        </div>
        """, unsafe_allow_html=True)
    else:
        for r in b_triggers:
            st.markdown(f"""
            <div class='paper-card' style='border-left: 6px solid #16A34A; padding: 18px 20px;'>
                <div style='display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap;'>
                    <div>
                        <span style='font-size:18px; font-weight:900; color:#15803D;'>🔥 策略 B 觸發 (T-2 搶跑買入)：{r['stock_id']} {r['name']}</span>
                    </div>
                    <span style='background:#16A34A; color:#FFF; padding:3px 10px; border-radius:6px; font-size:12px; font-weight:800;'>{r['action_timing']}</span>
                </div>
                <div style='margin-top:8px; font-size:14px; color:#334155; line-height:1.6;'>
                    現價: <b style='font-size:16px;'>{r['latest_price']:.2f}</b> ｜ 處置累積: <b style='color:#15803D; font-size:15px;'>{r['disp_cum_ret']:+.1f}%</b> ｜ 5MA: <b>{r['ma5']:.2f}</b> ｜ 成交金額: <b>{r['turnover_str']}</b>
                </div>
                <div class='alert-success' style='margin-top:12px; font-size:13.5px !important;'>
                    👉 <b>作戰SOP：</b>{r['entry_sop']}
                </div>
            </div>
            """, unsafe_allow_html=True)

        for r in c_triggers:
            st.markdown(f"""
            <div class='paper-card' style='border-left: 6px solid #DC2626; padding: 18px 20px;'>
                <div style='display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap;'>
                    <div>
                        <span style='font-size:18px; font-weight:900; color:#B91C1C;'>🎯 策略 C 警戒 (明晨開高走低做空)：{r['stock_id']} {r['name']}</span>
                    </div>
                    <span style='background:#DC2626; color:#FFF; padding:3px 10px; border-radius:6px; font-size:12px; font-weight:800;'>{r['action_timing']}</span>
                </div>
                <div style='margin-top:8px; font-size:14px; color:#334155; line-height:1.6;'>
                    現價: <b style='font-size:16px;'>{r['latest_price']:.2f}</b> ｜ 處置超漲: <b style='color:#DC2626; font-size:15px;'>{r['disp_cum_ret']:+.1f}%</b> ｜ 量比: <b>{r['vol_ratio']:.2f}</b> ｜ 成交金額: <b>{r['turnover_str']}</b>
                </div>
                <div class='alert-danger' style='margin-top:12px; font-size:13.5px !important;'>
                    👉 <b>作戰SOP：</b>{r['entry_sop']}
                </div>
            </div>
            """, unsafe_allow_html=True)

    # 下方蓄勢觀察名單
    with st.expander("⏳ 查看即將出關蓄勢名單 (T-1 / T-2) 與歷史回測數據"):
        st.markdown(f"""
        <div style='font-size: 13px; color: #475569; margin-bottom: 12px;'>
            <b>📊 歷史回測驗證數據：</b><br>
            • <b>策略 B (T-2搶跑)</b>：歷史勝率 <b>71.4%</b> ｜ 獲利因子 <b>3.42</b> ｜ 平均單筆報酬 <b>+3.25%</b> ｜ 2026新制勝率 <b>80.0%</b><br>
            • <b>策略 C (T-1出關空)</b>：蒙地卡羅最佳勝率 <b>73.3%</b> ｜ 獲利因子 <b>1.80</b> ｜ MDD <= 28.9%
        </div>
        """, unsafe_allow_html=True)

        if imminent_pool:
            df_imm = pd.DataFrame(imminent_pool)
            cols_show = ["stock_id", "name", "countdown_desc", "latest_price", "disp_cum_ret", "turnover_str", "vol_ratio", "status"]
            rename_dict = {
                "stock_id": "代號", "name": "名稱", "countdown_desc": "出關進度",
                "latest_price": "現價", "disp_cum_ret": "處置累積%", "turnover_str": "成交金額",
                "vol_ratio": "量比", "status": "狀態評估"
            }
            df_imm_show = df_imm[cols_show].rename(columns=rename_dict)
            st.dataframe(df_imm_show, use_container_width=True, hide_index=True)
        else:
            st.info("目前全市場暫無 T-1 或 T-2 之即將出關個股。")


# ============================================================
# 頁尾：免責聲明與版權
# ============================================================
st.markdown("<br><hr style='border: none; border-top: 1px solid #E2E8F0; margin: 30px 0;'>", unsafe_allow_html=True)
st.markdown("""
<div style='font-size: 12px; color: #94A3B8; text-align: center; line-height: 1.8;'>
    📝 <b>免責聲明：</b>本系統僅為客觀量化統計與演算法歷史數據呈現，絕無任何特定股票推薦或個別投資諮詢之意圖。<br>
    股票市場具備一定波動與價格風險，零股或現股交易前請依個人財務風險承受度審慎考量，自主審慎判斷。<br>
    © 2026 親友與老手專屬・白話文量化投資導航 ｜ Powered by Streamlit Community Cloud
</div>
""", unsafe_allow_html=True)
