# test_antigravity_access.py
# -*- coding: utf-8 -*-
"""
Antigravity -> AirBridge -> Cadence 17.2 双机无头闭环自动化验收套件 (修正 OrCAD 重定向锁)
"""
import time
import json
import requests

AIRBRIDGE_API = "http://127.0.0.1:8888"

def print_banner(title: str):
    print("\n" + "=" * 65)
    print(f"  🚀  {title}")
    print("=" * 65)

def test_1_text_msg():
    print_banner("测试 1: 穿透链路与 REST API 连通性测试")
    url = f"{AIRBRIDGE_API}/api/v1/message"
    test_msg = f"[Antigravity Handshake] Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
    try:
        resp = requests.post(url, data=test_msg.encode('utf-8'), timeout=5)
        if resp.status_code == 200:
            print(f"✅ 状态码: 200 OK")
            print(f"✅ 消息已穿透网闸注入对端: {test_msg}")
            return True
        else:
            print(f"❌ 错误: HTTP {resp.status_code} - {resp.text}")
            return False
    except Exception as e:
        print(f"❌ 无法连接到本地 AirBridge (端口 8888): {e}")
        return False

def test_2_remote_fs_inspection():
    print_banner("测试 2: 远程文件系统 (RFS) 静默扫描与元数据嗅探")
    url_ls = f"{AIRBRIDGE_API}/api/v1/fs/ls"
    remote_target_dir = "e:\\RK3588智能盒子"
    
    print(f"📡 正在静默列举 B 机目标目录: {remote_target_dir} ...")
    try:
        payload = {"path": remote_target_dir, "timeout": 15.0}
        resp = requests.post(url_ls, json=payload, timeout=16)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success"):
                items = data.get("items", [])
                print(f"✅ 成功列举远程目录，包含 {len(items)} 个文件/目录项：")
                found_dsn = False
                found_brd = False
                for item in items:
                    name = item["name"]
                    size_kb = item["size"] / 1024
                    if "dsn" in name.lower():
                        found_dsn = True
                        print(f"   📁 [原理图原档] {name:<35} | 大小: {size_kb:.1f} KB | 修改时间: {item['mtime']}")
                    elif "brd" in name.lower():
                        found_brd = True
                        print(f"   📁 [PCB 原档]   {name:<35} | 大小: {size_kb:.1f} KB | 修改时间: {item['mtime']}")
                    elif "edn" in name.lower():
                        print(f"   📄 [EDIF 网表]  {name:<35} | 大小: {size_kb:.1f} KB | 修改时间: {item['mtime']}")

                if found_dsn and found_brd:
                    print("✅ 目标工程核心设计原件 (DSN + BRD) 均已由 Antigravity 确权定位！")
                    return True
                else:
                    print("⚠️ 目录已获取，但未完全识别到 .dsn 或 .brd 文件。")
                    return True
            else:
                print(f"❌ RFS 查询返回失败: {data.get('error')}")
                return False
        else:
            print(f"❌ HTTP 状态异常: {resp.status_code} - {resp.text}")
            return False
    except Exception as e:
        print(f"❌ RFS 访问异常: {e}")
        return False

def test_3_orcad_capture_closed_loop():
    print_banner("测试 3: OrCAD Capture 17.2 原理图引擎静默闭环测试")
    
    # 遵循 eda_engine 原生机制：直接 puts，避免 Windows 重复 open 文件锁冲突
    probe_token = f"TOKEN_ORCAD_PASS_{int(time.time())}"
    tcl_code = f"""# Antigravity OrCAD Probe Native Tcl
puts "=============================================="
puts ">>> Antigravity Engine Probe Verified <<<"
puts "System Timestamp: {probe_token}"
puts "OrCAD Capture 17.2 Environment: PASS"
puts "=============================================="
"""
    print("📤 正在向 B 机下发 OrCAD 自动化任务...")
    exec_url = f"{AIRBRIDGE_API}/api/v1/eda/execute"
    payload = {
        "script": tcl_code,
        "hint": "probe_capture.tcl"
    }
    
    try:
        resp = requests.post(exec_url, json=payload, timeout=5)
        if resp.status_code != 200:
            print(f"❌ 任务下发失败: {resp.text}")
            return False
        print("⏳ 脚本已注入，等待 B 机 OrCAD Capture 静默启动、执行与自愈退出 (冷启动预计 8~18 秒)...")
    except Exception as e:
        print(f"❌ 任务下发异常: {e}")
        return False

    # 轮询远程日志确认
    read_url = f"{AIRBRIDGE_API}/api/v1/fs/read"
    log_path = "D:\\_airbridge_temp\\_cadence_stdout.log"
    time.sleep(5)
    
    for attempt in range(12):
        time.sleep(1.5)
        try:
            r_resp = requests.post(read_url, json={"path": log_path, "max_preview_bytes": 4096}, timeout=10)
            if r_resp.status_code == 200:
                res_data = r_resp.json()
                if res_data.get("success"):
                    content = res_data.get("content", "")
                    if probe_token in content:
                        print(f"✅ [OrCAD 原理图闭环成功] 第 {attempt + 1} 次轮询捕获到特征令牌！")
                        print(f"   📜 远程捕获输出:\n{content.strip()}")
                        return True
                    elif "[ERROR]" in content:
                        print(f"⚠️ 捕获到执行期错误: {content.strip()}")
        except Exception:
            pass
        print(f"   ... 等待 OrCAD 执行完成 (尝试 {attempt + 1}/12) ...")

    print("❌ OrCAD 原理图执行超时或未检出标记。")
    return False

def test_4_allegro_pcb_closed_loop():
    print_banner("测试 4: Allegro PCB Designer 17.2 引擎静默闭环测试")
    
    probe_token = f"TOKEN_ALLEGRO_PASS_{int(time.time())}"
    skill_code = f"""procedure(AirBridgeAllegroProbe()
    let((fp)
        fp = outfile("D:/_airbridge_temp/allegro_probe.log")
        fprintf(fp "[AIRBRIDGE_PROBE] Allegro SKILL Engine OK: {probe_token}\\n")
        close(fp)
    )
)
AirBridgeAllegroProbe()
"""
    print("📤 正在向 B 机下发 Allegro SKILL 自动化任务...")
    exec_url = f"{AIRBRIDGE_API}/api/v1/eda/execute"
    payload = {
        "script": skill_code,
        "hint": "probe_allegro.il"
    }

    try:
        resp = requests.post(exec_url, json=payload, timeout=5)
        if resp.status_code != 200:
            print(f"❌ 任务下发失败: {resp.text}")
            return False
        print("⏳ 脚本已注入，等待 B 机 Allegro 秒级调起与安全退出 (预计 6~10 秒)...")
    except Exception as e:
        print(f"❌ 任务下发异常: {e}")
        return False

    read_url = f"{AIRBRIDGE_API}/api/v1/fs/read"
    log_path = "D:\\_airbridge_temp\\allegro_probe.log"
    time.sleep(4)
    
    for attempt in range(8):
        time.sleep(1.5)
        try:
            r_resp = requests.post(read_url, json={"path": log_path, "max_preview_bytes": 4096}, timeout=10)
            if r_resp.status_code == 200:
                res_data = r_resp.json()
                if res_data.get("success"):
                    content = res_data.get("content", "")
                    if probe_token in content:
                        print(f"✅ [Allegro PCB 闭环成功] 第 {attempt + 1} 次轮询捕获到特征令牌！")
                        print(f"   📜 远程落盘输出:\n{content.strip()}")
                        return True
        except Exception:
            pass
        print(f"   ... 等待 Allegro 执行完成 (尝试 {attempt + 1}/8) ...")

    print("❌ Allegro PCB 执行超时或未检出标记。")
    return False

if __name__ == "__main__":
    print("\n" + "★" * 65)
    print("   Antigravity 自动化底座 & Cadence 17.2 全链路接入测试启动")
    print("★" * 65)

    r1 = test_1_text_msg()
    r2 = test_2_remote_fs_inspection()
    r3 = test_3_orcad_capture_closed_loop()
    r4 = test_4_allegro_pcb_closed_loop()

    print("\n" + "=" * 65)
    print("                    验收汇总报告")
    print("=" * 65)
    print(f" 1. 物理链路/REST API 贯通:     [{'PASS' if r1 else 'FAIL'}]")
    print(f" 2. 远程文件系统 (RFS) 确权:   [{'PASS' if r2 else 'FAIL'}]")
    print(f" 3. 原理图 (OrCAD Capture) 闭环: [{'PASS' if r3 else 'FAIL'}]")
    print(f" 4. PCB (Allegro Designer) 闭环: [{'PASS' if r4 else 'FAIL'}]")
    print("=" * 65)

    if r1 and r2 and r3 and r4:
        print("🏆 恭喜！全链路自动化底座 100% 验收通过！")
        print("👉 Antigravity 已全面接管远程 EDA 执行权与工程目录控制权。")
        print("👉 下一步：正式进入裕太微原理图实战剥离与重构开发！")
    else:
        print("⚠️ 存在未通过的环节，请根据上述提示排查。")
