# -*- coding: utf-8 -*-
"""
launcher.py
============
十方極速・超弦動能殲滅系統 - 統一實戰控制台 (Single Window Bulletproof Launcher)
"""
import os
import sys

# 強制設定 stdout / stderr 為 UTF-8 並對未支援字元進行 safe fallback
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import time
import socket
import subprocess
import webbrowser

def is_port_listening(port, host='127.0.0.1'):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0

def kill_port(port):
    try:
        output = subprocess.check_output(f'netstat -ano | findstr :{port}', shell=True).decode('utf-8', errors='ignore')
        pids = set()
        for line in output.strip().splitlines():
            if 'LISTENING' in line:
                parts = line.strip().split()
                if len(parts) >= 5:
                    pids.add(parts[-1])
        for pid in pids:
            if pid and pid != '0':
                subprocess.run(f'taskkill /PID {pid} /F /T', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    os.environ["PYTHONIOENCODING"] = "utf-8"

    print("=" * 65)
    print("  [十方極速・超弦動能殲滅系統] - 正在啟動後台服務...")
    print("=" * 65)
    print()

    # 1. 清理舊程序
    print("[1/4] 清理歷史殘留程序 (Port 8001 / 8201)...")
    kill_port(8001)
    kill_port(8201)
    time.sleep(1)

    py_exe = os.path.join(script_dir, ".venv", "Scripts", "python.exe")
    if not os.path.exists(py_exe):
        py_exe = sys.executable

    # 2. 啟動 FastAPI 後端 (Port 8001)
    print("[2/4] 啟動後端 FastAPI 服務 (Port 8001)...")
    api_proc = subprocess.Popen(
        [py_exe, "-m", "uvicorn", "api_server:app", "--host", "0.0.0.0", "--port", "8001"],
        cwd=script_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    # 等待 API 就緒
    for _ in range(15):
        if is_port_listening(8001):
            break
        time.sleep(0.5)

    # 3. 啟動 Streamlit 儀表板 (Port 8201)
    print("[3/4] 啟動前端 Streamlit 儀表板 (Port 8201)...")
    ui_proc = subprocess.Popen(
        [py_exe, "-m", "streamlit", "run", "dashboard_ui.py", "--server.port", "8201", "--server.headless", "true"],
        cwd=script_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    # 等待 Streamlit 就緒
    for _ in range(15):
        if is_port_listening(8201):
            break
        time.sleep(0.5)

    # 4. 開啟單一瀏覽器視窗
    print("[4/4] 正在開啟瀏覽器儀表板 (單一視窗)...")
    time.sleep(1)
    webbrowser.open("http://localhost:8201")

    print()
    try:
        from data_providers.smart_sync_manager import format_console_freshness_banner
        print(format_console_freshness_banner())
    except Exception as audit_e:
        print(f"  [提示] DuckDB 月份核驗摘要載入略過: {audit_e}")
    print()
    print("=" * 65)
    print("  [成功] 全系統啟動就緒！(十方極速 1億流動性 0緩衝前10強)")
    print("  - 後端 API:    http://localhost:8001")
    print("  - 前端 UI:     http://localhost:8201")
    print("=" * 65)
    print()
    print("  * 提示：請保持此控制台視窗最小化運作。")
    print("  * 按 [Enter] 鍵或關閉視窗，將自動安全停止所有背景服務...")
    print()

    try:
        if sys.stdin and sys.stdin.isatty():
            input()
        else:
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass

    print()
    print("正在安全停止所有服務...")
    try:
        api_proc.terminate()
        ui_proc.terminate()
    except Exception:
        pass
    kill_port(8001)
    kill_port(8201)
    print("[完成] 所有服務已安全停止！")
    time.sleep(1)

if __name__ == "__main__":
    main()
