# -*- coding: utf-8 -*-
"""
family_app.py
=============
親友與老手專屬・白話文量化投資導航系統 (Family & Veteran Companion App)
特點：
- 純前端架構：僅讀取 published_signals.json，零依賴 DuckDB / 爬蟲 / 後端 API，秒級極速部署於 Streamlit Cloud
- 視覺革命：高鐵時刻表級「巨無霸字體 (視覺放大 3~5 倍)」＋ 全寬手機直式上下堆疊，徹底根絕左右硬擠
- 破解限制：全域 CSS 強制穿透 Streamlit 預設字體限制，Tabs、Input、Button、Toggle 全面高對比放大
- 極簡文案：砍掉 70% 贅字，全站改用「10 字以內的大字口訣」
- 五大模組：股市天氣預報、巨型零股配資機、持股一鍵健檢、直式波段看板、老手處置狙擊口訣專區
"""

import os
import json
import math
import datetime
import pandas as pd
import streamlit as st

# ============================================================
# 1. 頁面設定與自訂巨無霸紙質亮色 CSS
# ============================================================
st.set_page_config(
    page_title="白話投資導航・親友與老手指南",
    page_icon="🧭",
    layout="centered",
    initial_sidebar_state="collapsed"
)

def render_html(html_str: str):
    """
    安全渲染 HTML，去除每行多餘縮排與空行，徹底防止 CommonMark 觸發 4-space 縮排程式碼區塊 (<pre><code>)
    """
    cleaned = " ".join(line.strip() for line in html_str.splitlines() if line.strip())
    st.markdown(cleaned, unsafe_allow_html=True)


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
    max-width: 820px !important;
    padding-top: 1.5rem !important;
    padding-bottom: 4rem !important;
    padding-left: 1rem !important;
    padding-right: 1rem !important;
}

/* ============================================================
   一、 破解 Streamlit 字體鎖死限制 (全域強制 CSS 覆蓋)
   ============================================================ */

/* 1. 強制解除段落限制 */
.stMarkdown p, .stMarkdown span, .stMarkdown div, .stMarkdown li {
    font-size: inherit !important;
    line-height: 1.4 !important;
}

/* 2. 放大分頁籤 (Tabs) */
[data-testid="stTabs"] {
    background-color: transparent !important;
    margin-bottom: 24px !important;
}

[data-testid="stTabs"] [data-baseweb="tab-list"] {
    background-color: #E2E8F0 !important;
    padding: 8px !important;
    border-radius: 16px !important;
    gap: 8px !important;
    border: 2px solid #CBD5E1 !important;
    display: flex !important;
    flex-wrap: nowrap !important;
}

[data-testid="stTabs"] button[data-baseweb="tab"] {
    background-color: #FFFFFF !important;
    border-radius: 12px !important;
    padding: 14px 18px !important;
    border: 1.5px solid #CBD5E1 !important;
    opacity: 1 !important;
    flex: 1 1 auto !important;
    text-align: center !important;
}

[data-testid="stTabs"] button[data-baseweb="tab"] *,
[data-testid="stTabs"] button[data-baseweb="tab"] p,
[data-testid="stTabs"] button[data-baseweb="tab"] span,
[data-testid="stTabs"] button[data-baseweb="tab"] div {
    color: #1E293B !important;
    font-size: 24px !important;
    font-weight: 900 !important;
    opacity: 1 !important;
    line-height: 1.2 !important;
}

[data-testid="stTabs"] button[data-baseweb="tab"][aria-selected="true"] {
    background-color: #0F172A !important;
    border: 1.5px solid #0F172A !important;
    box-shadow: 0 4px 12px rgba(15, 23, 42, 0.25) !important;
    opacity: 1 !important;
}

[data-testid="stTabs"] button[data-baseweb="tab"][aria-selected="true"] *,
[data-testid="stTabs"] button[data-baseweb="tab"][aria-selected="true"] p,
[data-testid="stTabs"] button[data-baseweb="tab"][aria-selected="true"] span,
[data-testid="stTabs"] button[data-baseweb="tab"][aria-selected="true"] div {
    color: #FFFFFF !important;
    font-size: 24px !important;
    font-weight: 900 !important;
    opacity: 1 !important;
}

[data-testid="stTabs"] [data-baseweb="tab-border"],
[data-testid="stTabs"] [data-baseweb="tab-highlight"] {
    display: none !important;
}

/* 3. 放大輸入框、選擇器標題與本體 (Label & Input) */
label,
[data-testid="stWidgetLabel"] p,
[data-testid="stWidgetLabel"] div,
[data-testid="stWidgetLabel"] span {
    color: #1E293B !important;
    font-size: 26px !important;
    font-weight: 900 !important;
    line-height: 1.4 !important;
    margin-bottom: 8px !important;
}

[data-baseweb="input"],
[data-testid="stTextInput"] div[data-baseweb="input"],
[data-testid="stNumberInput"] div[data-baseweb="input"] {
    background-color: #FFFFFF !important;
    border: 2.5px solid #CBD5E1 !important;
    border-radius: 14px !important;
    min-height: 64px !important;
    padding: 6px 14px !important;
}

[data-baseweb="input"]:focus-within {
    border-color: #16A34A !important;
    box-shadow: 0 0 0 3px rgba(22, 163, 74, 0.25) !important;
}

input, [data-baseweb="input"] input {
    background-color: #FFFFFF !important;
    color: #0F172A !important;
    font-weight: 900 !important;
    font-size: 28px !important;
    height: 52px !important;
}

input::placeholder {
    color: #94A3B8 !important;
    font-size: 24px !important;
}

/* 數字輸入框加減按鈕 */
[data-testid="stNumberInput"] button {
    background-color: #F1F5F9 !important;
    color: #1E293B !important;
    min-width: 52px !important;
    min-height: 52px !important;
    font-size: 28px !important;
    border-radius: 10px !important;
}

/* 按鈕 (st.button) */
.stButton > button {
    font-size: 28px !important;
    font-weight: 900 !important;
    padding: 16px 28px !important;
    border-radius: 14px !important;
    min-height: 64px !important;
    width: 100% !important;
}

/* Toggle 與 Checkbox 標題與圖示 */
[data-testid="stCheckbox"] label span,
[data-testid="stToggle"] label span,
[data-testid="stToggle"] label p {
    color: #1E293B !important;
    font-weight: 900 !important;
    font-size: 26px !important;
    line-height: 1.4 !important;
}

[data-testid="stToggle"] [data-baseweb="toggle"] {
    transform: scale(1.35) !important;
    margin-right: 14px !important;
}

/* 4. 純白內容卡片與警示框 */
.paper-card {
    background: #FFFFFF;
    border-radius: 18px;
    padding: 24px 22px;
    margin-bottom: 20px;
    border: 1.5px solid #E2E8F0;
    box-shadow: 0 3px 12px rgba(0, 0, 0, 0.05);
}

.alert-danger {
    background: #FEF2F2 !important;
    border-left: 8px solid #DC2626 !important;
    color: #991B1B !important;
    padding: 20px 22px !important;
    border-radius: 14px !important;
    margin: 14px 0 !important;
    line-height: 1.5 !important;
}

.alert-warning {
    background: #FFFBEB !important;
    border-left: 8px solid #D97706 !important;
    color: #92400E !important;
    padding: 20px 22px !important;
    border-radius: 14px !important;
    margin: 14px 0 !important;
    line-height: 1.5 !important;
}

.alert-success {
    background: #F0FDF4 !important;
    border-left: 8px solid #16A34A !important;
    color: #166534 !important;
    padding: 20px 22px !important;
    border-radius: 14px !important;
    margin: 14px 0 !important;
    line-height: 1.5 !important;
}

.alert-info {
    background: #EFF6FF !important;
    border-left: 8px solid #2563EB !important;
    color: #1E40AF !important;
    padding: 20px 22px !important;
    border-radius: 14px !important;
    margin: 14px 0 !important;
    line-height: 1.5 !important;
}

/* 膠囊亮點大標籤 (22px ~ 24px 粗體，padding: 10px 16px，圓角 12px) */
.badge-tag {
    display: inline-block;
    padding: 10px 16px !important;
    border-radius: 12px !important;
    font-size: 22px !important;
    font-weight: 900 !important;
    margin-right: 8px;
    margin-bottom: 8px;
    line-height: 1.2 !important;
}
.badge-green { background: #DCFCE7 !important; color: #15803D !important; border: 1.5px solid #BBF7D0 !important; }
.badge-amber { background: #FEF3C7 !important; color: #B45309 !important; border: 1.5px solid #FDE68A !important; }
.badge-blue  { background: #DBEAFE !important; color: #1D4ED8 !important; border: 1.5px solid #BFDBFE !important; }
.badge-red   { background: #FEE2E2 !important; color: #B91C1C !important; border: 1.5px solid #FECACA !important; }

/* 隱藏 Streamlit 多餘元素 */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ============================================================
# 2. 安全機制一：家庭通關密語驗證鎖 (巨無霸化)
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
    render_html("<div style='height: 30px;'></div>")
    render_html("""
    <div class='paper-card' style='text-align: center; padding: 36px 20px; border-top: 8px solid #2563EB;'>
        <div style='font-size: 90px !important; line-height: 1 !important; margin-bottom: 16px;'>🧭</div>
        <div style='font-size: 38px !important; font-weight: 900 !important; color: #1E293B; margin-bottom: 12px;'>親友專屬・白話投資導航</div>
        <div style='font-size: 24px !important; font-weight: 800 !important; color: #64748B; margin-bottom: 24px;'>請輸入通關密語解鎖今日訊號</div>
    </div>
    """)

    c_left, c_mid, c_right = st.columns([1, 4, 1])
    with c_mid:
        user_input_passcode = st.text_input("🔑 輸入通關密語", type="password", placeholder="預設為 8888")
        if st.button("🔓 驗證解鎖進入", use_container_width=True, type="primary"):
            expected = get_expected_passcode()
            if user_input_passcode.strip() == expected:
                st.session_state.family_authenticated = True
                st.rerun()
            else:
                render_html("<div class='alert-danger' style='font-size: 24px !important; font-weight: 900 !important; text-align: center;'>密語不正確，請詢問版主喔！</div>")
    st.stop()


# ============================================================
# 3. 讀取 published_signals.json
# ============================================================
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
SIGNALS_FILE = os.path.join(PROJECT_ROOT, "published_signals.json")

if not os.path.exists(SIGNALS_FILE):
    render_html("""
    <div class='alert-danger' style='font-size: 26px !important; font-weight: 900 !important;'>
        🚧 尚未找到 published_signals.json 訊號檔<br>
        版主目前尚未在本地後台發布最新訊號，請稍候再重新整理！
    </div>
    """)
    st.stop()

try:
    with open(SIGNALS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
except Exception as e:
    render_html(f"""
    <div class='alert-danger' style='font-size: 26px !important; font-weight: 900 !important;'>
        ❌ 讀取訊號檔失敗: {str(e)}
    </div>
    """)
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
        if today.weekday() < 5 and now_tw.hour < 9:
            yesterday = today - datetime.timedelta(days=1)
            if today.weekday() == 0: # 週一晨間看上週五
                yesterday = today - datetime.timedelta(days=3)
            if d_data == yesterday:
                return True, "盤前備戰中（顯示前一交易日收盤訊號）"
                
        # 4. 判定為過期
        return False, f"版主尚未更新最新盤後資料（目前為 {data_date_val}），今日暫停參考！"
    except Exception:
        return True, "資料狀態已載入"

is_fresh, freshness_msg = check_freshness(data_date_str)

# 若資料過期，最頂部強制深紅高對比大防呆框 (28px)
if not is_fresh:
    render_html(f"""
    <div class='alert-danger' style='font-size: 28px !important; font-weight: 900 !important; padding: 22px !important;'>
        🚨 🚧 {freshness_msg}
    </div>
    """)


# ============================================================
# 5. 頂部導航列與版頭 (巨型親切看板)
# ============================================================
render_html(f"""
<div style='margin-bottom: 24px; padding-bottom: 16px; border-bottom: 2px solid #E2E8F0;'>
    <div style='font-size: 40px !important; font-weight: 900 !important; color: #1E293B; line-height: 1.2;'>
        🧭 親友與老手・白話投資導航
    </div>
    <div style='display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; margin-top: 10px; gap: 8px;'>
        <div style='font-size: 22px !important; font-weight: 800 !important; color: #64748B;'>
            客觀量化 ｜ 嚴守停損 ｜ 絕不凹單
        </div>
        <div>
            <span style='background: #DBEAFE; color: #1D4ED8; font-size: 20px !important; font-weight: 900 !important; padding: 6px 14px; border-radius: 8px;'>
                資料日期: {data_date_str}
            </span>
        </div>
    </div>
</div>
""")


# ============================================================
# 區塊一：今日股市天氣預報（大盤紅綠燈）
# ============================================================
render_html("<div style='font-size: 38px !important; font-weight: 900 !important; color: #1E293B; margin: 28px 0 16px 0;'>🌤️ 今日股市天氣預報</div>")

regime = data.get("market_regime", {})
rec_equity = int(regime.get("recommended_equity_pct", 50))
rec_cash = int(regime.get("recommended_cash_pct", 50))
halt_buys = bool(regime.get("halt_new_buys", False))
weather_title = regime.get("weather_title", "⛅ 多雲警戒")
taiex_c = regime.get("taiex_close", 0)

# 天氣與狀態主圖示 (90px)
if rec_equity >= 80:
    weather_icon = "☀️"
    weather_border = "#16A34A"
    weather_desc_short = "大盤晴朗多頭！照表配置好股票！"
elif rec_equity >= 50:
    weather_icon = "⛅"
    weather_border = "#D97706"
    weather_desc_short = "大盤月線震盪中！保留一半現金防守！"
else:
    weather_icon = "⛈️"
    weather_border = "#DC2626"
    weather_desc_short = "大盤急跌暴風雨！全數空手保留現金！"

render_html(f"""
<div class='paper-card' style='border-top: 8px solid {weather_border}; padding: 26px 20px; text-align: center; border-radius: 18px;'>
    <div style='font-size: 90px !important; line-height: 1 !important; margin: 6px 0;'>{weather_icon}</div>
    <div style='font-size: 40px !important; font-weight: 900 !important; color: #1E293B; margin-top: 8px;'>{weather_title}</div>
    <div style='font-size: 24px !important; font-weight: 800 !important; color: #64748B; margin-top: 6px;'>大盤指數: {taiex_c:,.0f} 點</div>
    <div style='font-size: 26px !important; font-weight: 900 !important; color: {weather_border}; margin-top: 14px;'>{weather_desc_short}</div>
</div>
""")


# ============================================================
# 區塊二：傻瓜零股配資試算機（上下堆疊巨型數字看板）
# ============================================================
render_html("<div style='font-size: 38px !important; font-weight: 900 !important; color: #1E293B; margin: 36px 0 16px 0;'>💰 傻瓜零股配資試算機</div>")

user_capital = st.number_input(
    "輸入預計投資總本金 (新台幣元)",
    min_value=10000,
    max_value=50000000,
    value=300000,
    step=10000
)

# 運算金額
investable_total = user_capital * (rec_equity / 100.0)
cash_reserve = user_capital - investable_total
single_stock_budget = investable_total / 9.0

# 資金比例直接做成三張上下堆疊的巨型數字卡 (38px 超粗體)
render_html(f"""
<div style='margin-top: 16px;'>
    <div class='paper-card' style='border-left: 8px solid #16A34A; padding: 22px 20px; margin-bottom: 14px;'>
        <div style='font-size: 38px !important; font-weight: 900 !important; color: #16A34A; line-height: 1.2;'>
            📈 最多買股：{rec_equity}%（${investable_total:,.0f}）
        </div>
    </div>
    <div class='paper-card' style='border-left: 8px solid #1E293B; padding: 22px 20px; margin-bottom: 14px;'>
        <div style='font-size: 38px !important; font-weight: 900 !important; color: #1E293B; line-height: 1.2;'>
            🏦 必留現金：{rec_cash}%（${cash_reserve:,.0f}）
        </div>
    </div>
    <div class='paper-card' style='border-left: 8px solid #2563EB; padding: 22px 20px; margin-bottom: 14px;'>
        <div style='font-size: 38px !important; font-weight: 900 !important; color: #2563EB; line-height: 1.2;'>
            🎯 每檔分配：${single_stock_budget:,.0f} 元
        </div>
    </div>
</div>
""")

# 小額本金防呆 (26px 粗體)
if single_stock_budget < 10000:
    render_html("""
    <div class='alert-warning' style='font-size: 26px !important; font-weight: 900 !important; padding: 18px 20px !important;'>
        💡 資金較小，建議集中買前 3~5 檔即可！
    </div>
    """)


# ============================================================
# 區塊三：「我手上的股票怎麼辦？」一鍵健檢
# ============================================================
render_html("<div style='font-size: 38px !important; font-weight: 900 !important; color: #1E293B; margin: 38px 0 16px 0;'>🔍 「我手上的股票怎麼辦？」一鍵健檢</div>")

all_search_dict = data.get("all_searchable_stocks", {})
search_query = st.text_input("輸入股票代號（例如: 6672, 4760, 6187, 2330）", placeholder="請輸入 4 碼台股代號").strip()

if search_query:
    target_info = all_search_dict.get(search_query)
    
    if target_info:
        t_name = target_info.get("name", "")
        t_rank = target_info.get("rank", 999)
        t_close = target_info.get("close", 0.0)

        if isinstance(t_rank, int) and t_rank <= 9:
            render_html(f"""
            <div class='alert-success' style='padding: 22px 20px !important; border-radius: 14px;'>
                <div style='font-size: 30px !important; font-weight: 900 !important; color: #166534;'>
                    🟢 動能超強（第 {t_rank} 名）！安心抱牢！
                </div>
                <div style='font-size: 28px !important; font-weight: 900 !important; color: #0F172A; margin-top: 10px;'>
                    {search_query} {t_name} ｜ 現價: {t_close:,.2f} 元
                </div>
            </div>
            """)
        elif isinstance(t_rank, int) and t_rank <= 15:
            render_html(f"""
            <div class='alert-info' style='padding: 22px 20px !important; border-radius: 14px;'>
                <div style='font-size: 30px !important; font-weight: 900 !important; color: #1E40AF;'>
                    🔵 安全緩衝（第 {t_rank} 名）！續抱、不加碼！
                </div>
                <div style='font-size: 28px !important; font-weight: 900 !important; color: #0F172A; margin-top: 10px;'>
                    {search_query} {t_name} ｜ 現價: {t_close:,.2f} 元
                </div>
            </div>
            """)
        else:
            render_html(f"""
            <div class='alert-danger' style='padding: 22px 20px !important; border-radius: 14px;'>
                <div style='font-size: 30px !important; font-weight: 900 !important; color: #991B1B;'>
                    🔴 跌出榜外！動能轉弱，找機會賣出換現金！
                </div>
                <div style='font-size: 28px !important; font-weight: 900 !important; color: #0F172A; margin-top: 10px;'>
                    {search_query} {t_name} ｜ 現價: {t_close:,.2f} 元
                </div>
                <div style='font-size: 24px !important; font-weight: 900 !important; color: #991B1B; margin-top: 14px; padding-top: 12px; border-top: 1px dashed #FCA5A5;'>
                    💡 果農挑水果，不甜就換！小賠換現金才能抓下一檔飆股。
                </div>
            </div>
            """)
    else:
        render_html(f"""
        <div class='alert-danger' style='padding: 22px 20px !important; border-radius: 14px;'>
            <div style='font-size: 30px !important; font-weight: 900 !important; color: #991B1B;'>
                🔴 未進強勢榜！動能偏弱，建議保留現金！
            </div>
            <div style='font-size: 28px !important; font-weight: 900 !important; color: #0F172A; margin-top: 10px;'>
                股票代號: {search_query}
            </div>
            <div style='font-size: 24px !important; font-weight: 900 !important; color: #991B1B; margin-top: 14px; padding-top: 12px; border-top: 1px dashed #FCA5A5;'>
                💡 果農挑水果，不甜就換！小賠換現金才能抓下一檔飆股。
            </div>
        </div>
        """)


# ============================================================
# 區塊四：波段資優生名單（手機直式 5 層全寬大看板結構）
# ============================================================
render_html("<div style='font-size: 40px !important; font-weight: 900 !important; color: #1E293B; margin: 42px 0 20px 0;'>🏆 波段資優生名單</div>")

core1 = data.get("core_1_swing", {})
top_9 = core1.get("top_9", [])
buffer_15 = core1.get("buffer_10_15", [])
dropped_15 = core1.get("dropped_from_top15", [])

tab_top9, tab_buffer, tab_dropped = st.tabs([
    f"🟢 前 9 強 ({len(top_9)})",
    f"🔵 緩衝區 ({len(buffer_15)})",
    f"🔴 轉弱 ({len(dropped_15)})"
])

# -------------------------------------------------------------
# Tab 1: 前 9 強名單
# -------------------------------------------------------------
with tab_top9:
    # 大盤連動大字黃框 (28px ~ 30px 粗體)
    if halt_buys:
        render_html("""
        <div class='alert-warning' style='padding: 20px 22px !important; border-radius: 14px;'>
            <div style='font-size: 30px !important; font-weight: 900 !important; color: #92400E; line-height: 1.3;'>🟡 大盤跌破月線警戒！</div>
            <div style='font-size: 28px !important; font-weight: 900 !important; color: #92400E; margin-top: 6px; line-height: 1.3;'>👉 空手者先看戲，暫停買新股！</div>
        </div>
        """)
    else:
        render_html("""
        <div class='alert-success' style='padding: 20px 22px !important; border-radius: 14px;'>
            <div style='font-size: 30px !important; font-weight: 900 !important; color: #166534; line-height: 1.3;'>🟢 大盤穩健多頭！</div>
            <div style='font-size: 28px !important; font-weight: 900 !important; color: #166534; margin-top: 6px; line-height: 1.3;'>👉 照表配置，動能強勁！</div>
        </div>
        """)

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
            <div class='alert-warning' style='border-left: 8px solid #D97706 !important; padding: 18px 20px !important; margin: 14px 0 !important; border-radius: 12px;'>
                <div style='font-size: 28px !important; font-weight: 900 !important; color: #92400E; line-height: 1.3;'>⚠️ 關禁閉劇烈波動！新手直接跳過！</div>
                <div style='font-size: 26px !important; font-weight: 900 !important; color: #92400E; margin-top: 6px; line-height: 1.3;'>👉 這筆錢請直接留現金，別買第 10 名</div>
            </div>
            """

        card_html = f"""
        <div class='paper-card' style='padding: 24px 22px; margin-bottom: 24px; border-radius: 18px;'>
            <!-- 第 1 層：超大股名 -->
            <div style='display: flex; align-items: center; margin-bottom: 12px;'>
                <span style='background: #16A34A; color: #FFF; padding: 4px 14px; border-radius: 8px; font-size: 28px !important; font-weight: 900 !important; margin-right: 12px;'>#{s_rank}</span>
                <span style='font-size: 38px !important; font-weight: 900 !important; color: #0F172A; line-height: 1.2;'>{s_id} {s_name}</span>
            </div>
            <!-- 第 2 層：超大現價 -->
            <div style='font-size: 32px !important; font-weight: 900 !important; color: #334155; margin-bottom: 14px;'>
                💰 現價：{s_close:,.2f} 元
            </div>
            <!-- 第 3 層：大膠囊亮點 -->
            <div style='display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 14px;'>
                {tag_spans}
            </div>
            <!-- 第 4 層：處置避雷大黃框（若有） -->
            {warning_block}
            <!-- 第 5 層：巨型買進試算看板 -->
            <div style='background: #0F172A; border-radius: 14px; padding: 18px 16px; text-align: center; margin-top: 14px;'>
                <div style='font-size: 38px !important; font-weight: 900 !important; color: #22C55E; line-height: 1.2;'>
                    🛒 試算：{shares_calc:,} 股
                </div>
                <div style='font-size: 24px !important; font-weight: 800 !important; color: #94A3B8; margin-top: 4px;'>
                    （約 ${approx_cost:,.0f} 元）
                </div>
            </div>
        </div>
        """
        render_html(card_html)

# -------------------------------------------------------------
# Tab 2: 安穩緩衝區 (10~15名)
# -------------------------------------------------------------
with tab_buffer:
    render_html("""
    <div class='alert-info' style='padding: 20px 22px !important; border-radius: 14px; margin-bottom: 20px;'>
        <div style='font-size: 28px !important; font-weight: 900 !important; color: #1E40AF; line-height: 1.3;'>🔵 緩衝區操作說明</div>
        <div style='font-size: 26px !important; font-weight: 900 !important; color: #1E40AF; margin-top: 6px; line-height: 1.3;'>原持有者安心續抱！空手者專注前 9 強，不盲目追買！</div>
    </div>
    """)

    for stock in buffer_15:
        s_rank = stock.get("rank", 10)
        s_id = stock.get("stock_id", "")
        s_name = stock.get("name", "")
        s_close = stock.get("close", 0.0)
        s_tags = stock.get("highlights", [])
        tag_spans = "".join([f"<span class='badge-tag badge-blue'>{t}</span>" for t in s_tags])

        render_html(f"""
        <div class='paper-card' style='padding: 24px 22px; margin-bottom: 24px; border-radius: 18px;'>
            <!-- 第 1 層：超大股名 -->
            <div style='display: flex; align-items: center; margin-bottom: 12px;'>
                <span style='background: #2563EB; color: #FFF; padding: 4px 14px; border-radius: 8px; font-size: 28px !important; font-weight: 900 !important; margin-right: 12px;'>#{s_rank}</span>
                <span style='font-size: 36px !important; font-weight: 900 !important; color: #0F172A; line-height: 1.2;'>{s_id} {s_name}</span>
            </div>
            <!-- 第 2 層：超大現價 -->
            <div style='font-size: 32px !important; font-weight: 900 !important; color: #334155; margin-bottom: 14px;'>
                💰 現價：{s_close:,.2f} 元
            </div>
            <!-- 第 3 層：大膠囊亮點 -->
            <div style='display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 14px;'>
                {tag_spans}
            </div>
            <!-- 第 4 層：說明底框 -->
            <div style='background: #EFF6FF; border: 1.5px solid #BFDBFE; border-radius: 12px; padding: 14px; text-align: center;'>
                <div style='font-size: 26px !important; font-weight: 900 !important; color: #1D4ED8;'>
                    🔵 續抱即可！空手者不追！
                </div>
            </div>
        </div>
        """)

# -------------------------------------------------------------
# Tab 3: 動能轉弱區 (最新跌出 15 名外)
# -------------------------------------------------------------
with tab_dropped:
    render_html("""
    <div class='alert-danger' style='padding: 20px 22px !important; border-radius: 14px; margin-bottom: 20px;'>
        <div style='font-size: 30px !important; font-weight: 900 !important; color: #991B1B; line-height: 1.3;'>🚨 鐵血紀律提醒</div>
        <div style='font-size: 26px !important; font-weight: 900 !important; color: #991B1B; margin-top: 6px; line-height: 1.3;'>最新跌出前 15 名！建議於開盤市價汰弱換取現金！</div>
    </div>
    """)

    if not dropped_15:
        render_html("<div class='paper-card' style='text-align: center; padding: 28px;'><div style='font-size: 28px !important; font-weight: 900 !important; color: #64748B;'>今日名單結構穩定，無任何股票跌出前 15 名！</div></div>")
    else:
        for stock in dropped_15:
            d_id = stock.get("stock_id", "")
            d_name = stock.get("name", "")
            p_rank = stock.get("prev_rank", "-")
            n_rank = stock.get("new_rank", "外")
            d_close = stock.get("close", 0.0)
            
            render_html(f"""
            <div class='paper-card' style='border-left: 8px solid #DC2626; padding: 24px 22px; margin-bottom: 24px; border-radius: 18px;'>
                <div style='display: flex; align-items: center; margin-bottom: 12px;'>
                    <span style='background: #DC2626; color: #FFF; padding: 4px 14px; border-radius: 8px; font-size: 24px !important; font-weight: 900 !important; margin-right: 12px;'>轉弱</span>
                    <span style='font-size: 36px !important; font-weight: 900 !important; color: #991B1B; line-height: 1.2;'>{d_id} {d_name}</span>
                </div>
                <div style='font-size: 32px !important; font-weight: 900 !important; color: #334155; margin-bottom: 10px;'>
                    💰 最新價：{d_close:,.2f} 元
                </div>
                <div style='font-size: 26px !important; font-weight: 900 !important; color: #64748B; margin-bottom: 14px;'>
                    原前一交易日排名: 第 <b>{p_rank}</b> 名 ➔ 今日動向: <b style='color:#DC2626;'>{n_rank}</b>
                </div>
                <div style='background: #FEF2F2; border: 1.5px solid #F87171; border-radius: 12px; padding: 14px; text-align: center;'>
                    <div style='font-size: 26px !important; font-weight: 900 !important; color: #991B1B;'>
                        🔴 跌出榜外！找機會賣出換現金！
                    </div>
                </div>
            </div>
            """)


# ============================================================
# 區塊五：老手實戰專區 —— 處置出關狙擊（巨型大字口訣卡）
# ============================================================
render_html("<div style='font-size: 40px !important; font-weight: 900 !important; color: #1E293B; margin: 44px 0 20px 0;'>⚡ 老手實戰專區 —— 處置出關狙擊</div>")

is_pro_confirmed = st.toggle("🔥 展開【處置出關狙擊】實戰訊號", value=False)

if is_pro_confirmed:
    core2 = data.get("core_2_disposal", {})
    b_triggers = core2.get("strategy_b_triggers", [])
    c_triggers = core2.get("strategy_c_triggers", [])
    imminent_pool = core2.get("imminent_pool", [])
    stats = core2.get("backtest_stats", {})

    # 徹底刪除段落式說明，改為巨型 30 秒口訣卡
    render_html("""
    <div class='paper-card' style='background: #FFFBEB; border: 2.5px solid #FCD34D; padding: 26px 20px; border-radius: 18px;'>
        <div style='font-size: 36px !important; font-weight: 900 !important; color: #92400E; text-align: center; margin-bottom: 20px;'>
            📖 30 秒實戰大字口訣
        </div>
        
        <!-- 戰術 B -->
        <div style='background: #FFFFFF; padding: 20px 22px; border-radius: 14px; border: 2px solid #BBF7D0; margin-bottom: 18px;'>
            <div style='font-size: 30px !important; font-weight: 900 !important; color: #15803D; margin-bottom: 10px;'>
                🟢 戰術 B：出關前搭轎（勝率 71%）
            </div>
            <div style='font-size: 26px !important; font-weight: 900 !important; color: #1E293B; line-height: 1.7;'>
                ① 出關前 2 天 (T-2)：收盤買進<br>
                ② 出關前 1 天 (T-1)：收盤賣一半<br>
                ③ 出關當天 (T日)：09:00 開盤全賣光！
            </div>
        </div>

        <!-- 戰術 C -->
        <div style='background: #FFFFFF; padding: 20px 22px; border-radius: 14px; border: 2px solid #FECACA; margin-bottom: 18px;'>
            <div style='font-size: 30px !important; font-weight: 900 !important; color: #B91C1C; margin-bottom: 10px;'>
                🔴 戰術 C：出關日沖喜放空（勝率 73%）
            </div>
            <div style='font-size: 26px !important; font-weight: 900 !important; color: #1E293B; line-height: 1.7;'>
                ① 超漲處置股，出關日主力常開高倒貨！<br>
                ② 專打早盤衝喜，當沖放空吃下跌段！
            </div>
        </div>

        <!-- 戰術 C 三大保命鐵律 -->
        <div class='alert-danger' style='border-left: 8px solid #DC2626 !important; padding: 20px 22px !important; border-radius: 12px;'>
            <div style='font-size: 30px !important; font-weight: 900 !important; color: #991B1B; margin-bottom: 12px;'>
                🚨 戰術 C 三大鐵血保命符：
            </div>
            <div style='font-size: 28px !important; font-weight: 900 !important; color: #991B1B; line-height: 1.7;'>
                🎯 1. 09:00 開高 +1% ~ +5.2% 才放空！<br>
                🛑 2. 漲破 +5% 立刻認賠回補！絕不攤平！<br>
                ⏰ 3. 鬧鐘設 13:25 強制平倉！絕不留倉！
            </div>
        </div>
    </div>
    """)

    # 實戰今日觸發標靶 (36px 粗體)
    render_html("<div style='font-size: 36px !important; font-weight: 900 !important; color: #1E293B; margin: 34px 0 16px 0;'>🎯 今日實戰觸發標靶</div>")
    
    if not b_triggers and not c_triggers:
        render_html("""
        <div class='alert-success' style='padding: 24px !important; border-radius: 14px;'>
            <div style='font-size: 28px !important; font-weight: 900 !important; color: #166534;'>
                🟢 今日處置股無觸發進場門檻！空手看戲！
            </div>
        </div>
        """)
    else:
        for r in b_triggers:
            render_html(f"""
            <div class='paper-card' style='border-left: 8px solid #16A34A; padding: 24px 22px; margin-bottom: 20px; border-radius: 16px;'>
                <div style='font-size: 32px !important; font-weight: 900 !important; color: #15803D; line-height: 1.3;'>
                    🔥 策略 B 觸發（T-2 搶跑買入）：{r['stock_id']} {r['name']}
                </div>
                <div style='font-size: 26px !important; font-weight: 900 !important; color: #334155; margin: 12px 0; line-height: 1.6;'>
                    現價: <b>{r['latest_price']:.2f}</b> ｜ 累積: <b style='color:#15803D;'>{r['disp_cum_ret']:+.1f}%</b> ｜ 金額: <b>{r['turnover_str']}</b><br>
                    執行時機: <b>{r['action_timing']}</b>
                </div>
                <div class='alert-success' style='font-size: 26px !important; font-weight: 900 !important; padding: 18px !important; border-radius: 10px;'>
                    👉 <b>SOP：</b>{r['entry_sop']}
                </div>
            </div>
            """)

        for r in c_triggers:
            render_html(f"""
            <div class='paper-card' style='border-left: 8px solid #DC2626; padding: 24px 22px; margin-bottom: 20px; border-radius: 16px;'>
                <div style='font-size: 32px !important; font-weight: 900 !important; color: #B91C1C; line-height: 1.3;'>
                    🎯 策略 C 警戒（明晨開高走低做空）：{r['stock_id']} {r['name']}
                </div>
                <div style='font-size: 26px !important; font-weight: 900 !important; color: #334155; margin: 12px 0; line-height: 1.6;'>
                    現價: <b>{r['latest_price']:.2f}</b> ｜ 超漲: <b style='color:#DC2626;'>{r['disp_cum_ret']:+.1f}%</b> ｜ 量比: <b>{r['vol_ratio']:.2f}</b> ｜ 金額: <b>{r['turnover_str']}</b><br>
                    執行時機: <b>{r['action_timing']}</b>
                </div>
                <div class='alert-danger' style='font-size: 26px !important; font-weight: 900 !important; padding: 18px !important; border-radius: 10px;'>
                    👉 <b>SOP：</b>{r['entry_sop']}
                </div>
            </div>
            """)

    # 下方蓄勢觀察名單
    with st.expander("⏳ 查看即將出關蓄勢名單 (T-1 / T-2) 與歷史回測數據"):
        render_html("""
        <div style='font-size: 24px !important; font-weight: 900 !important; color: #475569; margin-bottom: 14px; line-height: 1.6;'>
            <b>📊 歷史回測驗證數據：</b><br>
            • <b>策略 B (T-2搶跑)</b>：歷史勝率 <b>71.4%</b> ｜ 獲利因子 <b>3.42</b> ｜ 平均單筆 <b>+3.25%</b><br>
            • <b>策略 C (T-1出關空)</b>：最佳勝率 <b>73.3%</b> ｜ 獲利因子 <b>1.80</b> ｜ MDD <= 28.9%
        </div>
        """)

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
render_html("<br><hr style='border: none; border-top: 1.5px solid #E2E8F0; margin: 36px 0;'>")
render_html("""
<div style='font-size: 18px !important; font-weight: 700 !important; color: #94A3B8; text-align: center; line-height: 1.8;'>
    📝 <b>免責聲明：</b>本系統僅為客觀量化統計與演算法歷史數據呈現，絕無任何特定股票推薦或個別投資諮詢之意圖。<br>
    股票市場具備一定價格波動風險，交易前請依個人財務風險承受度審慎考量，自主獨立判斷。<br>
    © 2026 親友與老手專屬・白話文量化投資導航 ｜ Powered by Streamlit Community Cloud
</div>
""")
