# diag_orcad.py
# -*- coding: utf-8 -*-
"""
OrCAD 远程执行现场深度诊断工具 (利用已打通的 RFS 提取日志真相)
"""
import requests
import json

AIRBRIDGE_API = "http://127.0.0.1:8888"

def inspect_temp_workspace():
    print("=" * 60)
    print("🔍 正在通过 RFS 透视 B 机临时工作区: D:\\_airbridge_temp")
    print("=" * 60)

    # 1. 查看 D:\_airbridge_temp 目录下的文件
    resp = requests.post(f"{AIRBRIDGE_API}/api/v1/fs/ls", json={"path": "D:\\_airbridge_temp", "timeout": 10})
    if resp.status_code == 200:
        data = resp.json()
        if data.get("success"):
            print("📁 B 机临时工作区文件清单:")
            for it in data.get("items", []):
                print(f"   - {it['name']:<25} ({it['size']} 字节)  修改时间: {it['mtime']}")
        else:
            print(f"❌ 无法读取目录: {data.get('error')}")
            return
    else:
        print(f"❌ HTTP 错误: {resp.status_code}")
        return

    # 2. 读取 _cadence_stdout.log 真实内容
    print("\n📄 正在提取 _cadence_stdout.log 真实内容:")
    resp_log = requests.post(f"{AIRBRIDGE_API}/api/v1/fs/read", json={"path": "D:\\_airbridge_temp\\_cadence_stdout.log", "max_preview_bytes": 10000})
    if resp_log.status_code == 200:
        log_data = resp_log.json()
        if log_data.get("success"):
            print("------------------------------------------------------------")
            print(log_data.get("content", "").strip())
            print("------------------------------------------------------------")
        else:
            print(f"⚠️ 文件读取失败或不存在: {log_data.get('error')}")

    # 3. 读取 _bootstrap.tcl
    print("\n📄 正在提取 _bootstrap.tcl 引导脚本:")
    resp_boot = requests.post(f"{AIRBRIDGE_API}/api/v1/fs/read", json={"path": "D:\\_airbridge_temp\\_bootstrap.tcl", "max_preview_bytes": 10000})
    if resp_boot.status_code == 200:
        boot_data = resp_boot.json()
        if boot_data.get("success"):
            print("------------------------------------------------------------")
            print(boot_data.get("content", "").strip())
            print("------------------------------------------------------------")

if __name__ == "__main__":
    inspect_temp_workspace()
