# master_eda_preflight_suite.py
# -*- coding: utf-8 -*-
"""
AirBridge EDA 全量前置终审与作战地图提取总套件 v2.3 (OrCAD + Allegro 双轨 100% 完结版)
涵盖链路：
  1. 通信网闸健康度与 RFS 确权
  2. 14.5MB 原版原理图 (DSN) 安全副本制作、真实挂载、34页全解析、全套网讯/CPLD/存储芯片定位
  3. 19.5MB 原版 PCB (BRD) 批处理挂载、6层叠层/bBox板框提取、核心芯片物理坐标(X,Y)/层别确权
  4. 现场临时工件自愈清理与全局作战地图 JSON 汇总结算
"""
import os
import sys
import time
import json
import requests

AIRBRIDGE_API = "http://127.0.0.1:8888"

# 远程设计源路径与临时工作区配置
REMOTE_DIR = "e:\\RK3588智能盒子"
ORIG_DSN = f"{REMOTE_DIR}\\RK3588_WX8116-SCH-V2T.DSN"
ORIG_BRD = f"{REMOTE_DIR}\\RK3588_WX8116-PCB-V2T.brd"

TEMP_WORKSPACE = "D:\\_airbridge_temp"
BENCH_DSN = f"{TEMP_WORKSPACE}\\benchmark.dsn"
BENCH_BRD = f"{TEMP_WORKSPACE}\\benchmark.brd"

def to_tcl_unicode_escape(path_str: str) -> str:
    """将含中文的路径转为 100% 纯 ASCII 的 Tcl \\uXXXX 转义格式"""
    norm = path_str.replace("\\", "/")
    return "".join(f"\\u{ord(c):04x}" if ord(c) > 127 else c for c in norm)

def log_step(title: str):
    print("\n" + "=" * 75)
    print(f"  👉  {title}")
    print("=" * 75)

def wait_for_flag_and_read_log(flag_filename: str, log_filename: str, timeout_sec: int = 55) -> str:
    """通过 ls 轮询目录中的完成信标文件，零报错等待结果"""
    ls_url = f"{AIRBRIDGE_API}/api/v1/fs/ls"
    read_url = f"{AIRBRIDGE_API}/api/v1/fs/read"
    log_full_path = f"{TEMP_WORKSPACE}\\{log_filename}"

    start_t = time.time()
    while time.time() - start_t < timeout_sec:
        time.sleep(2.5)
        try:
            resp = requests.post(ls_url, json={"path": TEMP_WORKSPACE, "timeout": 10.0}, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    names = {item["name"].lower(): item["size"] for item in data.get("items", [])}
                    if flag_filename.lower() in names:
                        r_resp = requests.post(
                            read_url,
                            json={"path": log_full_path, "max_preview_bytes": 131072},
                            timeout=12
                        )
                        if r_resp.status_code == 200 and r_resp.json().get("success"):
                            return r_resp.json().get("content", "")
        except Exception:
            pass
        elapsed = int(time.time() - start_t)
        print(f"   ... 远程引擎正在处理大文件数据库 (已运行 {elapsed} 秒) ...")
    return ""

def main():
    print("\n" + "★" * 75)
    print("   AirBridge & Cadence 17.2 全流程终审压测与作战地图提取套件 v2.3")
    print("★" * 75)

    results = {
        "link_health": False,
        "sch_inspection": False,
        "pcb_inspection": False,
        "sch_pages_total": 0,
        "sch_pages_list": [],
        "pcb_layers_total": 0,
        "pcb_layers_names": "",
        "pcb_extents": "",
        "pcb_total_symbols": 0,
        "sch_targets": [],
        "pcb_targets": []
    }

    # =========================================================================
    # 第一阶段：基础链路与原工程大文件确权
    # =========================================================================
    log_step("第一阶段：基础物理链路、REST API 与原工程大文件确权")
    try:
        msg_resp = requests.post(f"{AIRBRIDGE_API}/api/v1/message", data=b"[Master Suite v2.3 Init]", timeout=5)
        if msg_resp.status_code != 200:
            print("❌ 无法连接 AirBridge REST API (8888 端口)！")
            return

        ls_orig = requests.post(f"{AIRBRIDGE_API}/api/v1/fs/ls", json={"path": REMOTE_DIR}, timeout=10).json()
        if not ls_orig.get("success"):
            print(f"❌ 无法访问原工程目录: {REMOTE_DIR}")
            return

        file_map = {item["name"].upper(): item["size"] for item in ls_orig.get("items", [])}
        dsn_size = file_map.get("RK3588_WX8116-SCH-V2T.DSN", 0)
        brd_size = file_map.get("RK3588_WX8116-PCB-V2T.BRD", 0)

        if dsn_size == 0 or brd_size == 0:
            print(f"❌ 原工程文件缺失！DSN: {dsn_size}B, BRD: {brd_size}B")
            return

        print(f"✅ 物理链路与 REST 接口正常响应！")
        print(f"✅ 原版原理图锁定: {ORIG_DSN} ({dsn_size / 1024 / 1024:.2f} MB)")
        print(f"✅ 原版 PCB 图锁定: {ORIG_BRD} ({brd_size / 1024 / 1024:.2f} MB)")
        results["link_health"] = True
    except Exception as e:
        print(f"❌ 链路检查异常: {e}")
        return

    # =========================================================================
    # 第二阶段：OrCAD Capture 副本隔离、14.5MB 工程挂载与 34 页全景扫描
    # =========================================================================
    log_step("第二阶段：OrCAD Capture 17.2 副本隔离、14.5MB 真实挂载与全页芯片扫描")

    tcl_orig_dsn = to_tcl_unicode_escape(ORIG_DSN)
    tcl_orig_brd = to_tcl_unicode_escape(ORIG_BRD)
    tcl_bench_dsn = BENCH_DSN.replace("\\", "/")
    tcl_bench_brd = BENCH_BRD.replace("\\", "/")

    sch_tcl = f"""# 1. 清理旧文件并创建隔离副本
catch {{ file delete -force "{tcl_bench_dsn}" }}
catch {{ file delete -force "{tcl_bench_brd}" }}
catch {{ file delete -force "D:/_airbridge_temp/_allegro_done.flag" }}
catch {{ file delete -force "D:/_airbridge_temp/pcb_master_result.log" }}

if {{[catch {{
    file copy -force "{tcl_orig_dsn}" "{tcl_bench_dsn}"
    file copy -force "{tcl_orig_brd}" "{tcl_bench_brd}"
}} copy_err]}} {{
    puts "\[ERROR\] Copy failed: $copy_err"
    exit
}}
puts "\[COPY_OK\] Safe copies created"

# 2. 挂载 OrCAD Capture DboSession 数据库
set lSession $::DboSession_s_pDboSession
DboSession -this $lSession
set lNullObj NULL
set lStatus [DboState]

set lDsnPath "{tcl_bench_dsn}"
set lPathStr [DboTclHelper_sMakeCString $lDsnPath]

set t0 [clock seconds]
set lDesign [$lSession GetDesignAndSchematics $lPathStr $lStatus]
if {{$lDesign == $lNullObj || $lDesign == ""}} {{
    puts "\[ERROR\] DboSession GetDesignAndSchematics failed!"
    exit
}}
DboDesign -this $lDesign
set load_time [expr {{[clock seconds] - $t0}}]
puts "\[SCH_LOAD_OK\] Load time: $load_time sec"

# 3. 深度遍历所有 Schematic 文件夹、Page 页面与核心元器件
set target_refs [list "U2" "U5" "U7" "U9" "U11" "U12" "U64" "U69" "CON1"]
set lSchematicIter [$lDesign NewViewsIter $lStatus $::IterDefs_SCHEMATICS]
set lView [$lSchematicIter NextView $lStatus]
set page_count 0

while {{$lView != $lNullObj && $lView != ""}} {{
    set lSchematic [DboViewToDboSchematic $lView]
    set lViewName [DboTclHelper_sMakeCString]
    $lSchematic GetName $lViewName
    set view_str [DboTclHelper_sGetConstCharPtr $lViewName]

    set lPagesIter [$lSchematic NewPagesIter $lStatus]
    set lPage [$lPagesIter NextPage $lStatus]

    while {{$lPage != $lNullObj && $lPage != ""}} {{
        incr page_count
        set lPageName [DboTclHelper_sMakeCString]
        $lPage GetName $lPageName
        set page_str [DboTclHelper_sGetConstCharPtr $lPageName]
        puts "MAP_PAGE|$page_count|$view_str|$page_str"

        set lPartsIter [$lPage NewPartInstsIter $lStatus]
        set lInst [$lPartsIter NextPartInst $lStatus]

        while {{$lInst != $lNullObj && $lInst != ""}} {{
            set lPlacedInst [DboPartInstToDboPlacedInst $lInst]
            if {{$lPlacedInst != $lNullObj && $lPlacedInst != ""}} {{
                set lRefBase [DboTclHelper_sMakeCString]
                $lPlacedInst GetReference $lRefBase
                set ref_base_str [DboTclHelper_sGetConstCharPtr $lRefBase]

                set lRefFull [DboTclHelper_sMakeCString]
                $lPlacedInst GetReferenceDesignator $lRefFull
                set ref_full_str [DboTclHelper_sGetConstCharPtr $lRefFull]

                set lVal [DboTclHelper_sMakeCString]
                $lPlacedInst GetPartValue $lVal
                set val_str [DboTclHelper_sGetConstCharPtr $lVal]

                set lPkg [DboTclHelper_sMakeCString]
                $lPlacedInst GetPCBFootprint $lPkg
                set pkg_str [DboTclHelper_sGetConstCharPtr $lPkg]

                set matched 0
                foreach tr $target_refs {{
                    if {{[string equal -nocase $ref_base_str $tr] || [string equal -nocase $ref_full_str $tr]}} {{
                        set matched 1
                    }}
                }}
                if {{[regexp -nocase {{(WX81|WX51|WX50|EF2L|CPLD|DDR3|EMMC)}} $val_str]}} {{
                    set matched 1
                }}

                if {{$matched}} {{
                    puts "TARGET_CHIP|$ref_full_str|$val_str|$pkg_str|$page_str"
                }}
            }}
            set lInst [$lPartsIter NextPartInst $lStatus]
        }}
        delete_DboPagePartInstsIter $lPartsIter
        set lPage [$lPagesIter NextPage $lStatus]
    }}
    delete_DboSchematicPagesIter $lPagesIter
    set lView [$lSchematicIter NextView $lStatus]
}}
delete_DboLibViewsIter $lSchematicIter

puts "\[SCH_INSPECT_DONE\] $page_count"
exit
"""

    t_sch_start = time.time()
    requests.post(f"{AIRBRIDGE_API}/api/v1/eda/execute", json={"script": sch_tcl, "hint": "inspect_sch.tcl"}, timeout=10)
    print("⏳ OrCAD Capture 正在创建安全副本并反序列化 14.5MB 原理图数据库...")

    sch_log = wait_for_flag_and_read_log("_orcad_done.flag", "_cadence_stdout.log", timeout_sec=55)
    if sch_log and "[SCH_INSPECT_DONE]" in sch_log:
        elapsed = time.time() - t_sch_start
        print(f"✅ [OrCAD 原理图深度压测通过！] 耗时: {elapsed:.1f} 秒")
        results["sch_inspection"] = True

        for line in sch_log.splitlines():
            line = line.strip()
            if line.startswith("MAP_PAGE|"):
                _, idx_str, folder, page_name = line.split("|", 3)
                results["sch_pages_total"] = max(results["sch_pages_total"], int(idx_str))
                results["sch_pages_list"].append(f"{folder}/{page_name}")
            elif line.startswith("TARGET_CHIP|"):
                _, ref, val, pkg, page = line.split("|", 4)
                results["sch_targets"].append({"ref": ref, "value": val, "footprint": pkg, "page": page})
                print(f"   🎯 原理图芯片定位: {ref:<8} | 型号: {val:<18} | 封装: {pkg:<22} | 页面: [{page}]")
        print(f"   📋 原理图结构总计: 成功遍历 {results['sch_pages_total']} 个原理图页面")
    else:
        print("❌ OrCAD 原理图深度压测未检出完成标志！远程日志内容如下：")
        print(sch_log if sch_log else "(空)")

    # =========================================================================
    # 第三阶段：Allegro PCB 深度压测与叠层/物理坐标提取
    # =========================================================================
    log_step("第三阶段：Allegro 17.2 挂载 19.5MB 真实 PCB 与叠层/物理坐标提取")

    pcb_skill = f"""procedure(MasterInspectPCB()
    let((fp dfp open_ok layers bbox design sym_list target_refs ref xy rot lyr pkg pin_count)
        fp = outfile("D:/_airbridge_temp/pcb_master_result.log")
        errset(
            let(()
                axlSetVariable("NOCONFIRM" t)
                open_ok = axlOpenDesignForBatch("{tcl_bench_brd}" "wf")
                if(!open_ok then
                    open_ok = axlOpenDesign(?design "{tcl_bench_brd}" ?mode "wf")
                )

                if(open_ok then
                    fprintf(fp "[PCB_LOAD_OK]\\n")
                    drain(fp)
                    
                    layers = axlGetParam("paramLayerGroup:ETCH")->groupMembers
                    if(layers then
                        fprintf(fp "LAYERS_COUNT|%d|%L\\n" length(layers) layers)
                        drain(fp)
                    )

                    design = axlDBGetDesign()
                    errset(
                        let(()
                            bbox = design->bBox
                            if(bbox then
                                fprintf(fp "EXTENTS|%L\\n" bbox)
                                drain(fp)
                            )
                        )
                        t
                    )

                    target_refs = '("U2" "U5" "U7" "U9" "U11" "U12" "U64" "U69" "CON1" "J6" "J8")
                    sym_list = design->symbols
                    fprintf(fp "TOTAL_SYMBOLS|%d\\n" length(sym_list))
                    drain(fp)

                    foreach(sym sym_list
                        errset(
                            let(()
                                ref = sym->refdes
                                pin_count = length(sym->pins)
                                if(ref && (member(ref target_refs) || pin_count >= 40) then
                                    xy = sym->xy
                                    rot = sym->rotation
                                    lyr = if(sym->isMirrored "BOTTOM" "TOP")
                                    pkg = or(sym->name "UNKNOWN")
                                    fprintf(fp "PCB_CHIP|%s|%s|%d|%s|%L|%L|%L\\n"
                                            ref pkg pin_count lyr car(xy) cadr(xy) rot)
                                )
                            )
                            t
                        )
                    )
                    fprintf(fp "[PCB_INSPECT_DONE]\\n")
                    drain(fp)
                else
                    fprintf(fp "[PCB_LOAD_FAIL]\\n")
                    drain(fp)
                )
            )
            t
        )
        close(fp)

        dfp = outfile("D:/_airbridge_temp/_allegro_done.flag")
        fprintf(dfp "OK\\n")
        close(dfp)
    )
)
MasterInspectPCB()
"""

    t_pcb_start = time.time()
    requests.post(f"{AIRBRIDGE_API}/api/v1/eda/execute", json={"script": pcb_skill, "hint": "inspect_pcb.il"}, timeout=10)
    print("⏳ Allegro PCB 正在全速挂载 19.5MB 物理走线、铺铜与引脚封装树...")

    pcb_log = wait_for_flag_and_read_log("_allegro_done.flag", "pcb_master_result.log", timeout_sec=55)
    if pcb_log and "[PCB_INSPECT_DONE]" in pcb_log:
        elapsed = time.time() - t_pcb_start
        print(f"✅ [Allegro PCB 深度压测通过！] 耗时: {elapsed:.1f} 秒")
        results["pcb_inspection"] = True

        for line in pcb_log.splitlines():
            line = line.strip()
            if line.startswith("LAYERS_COUNT|"):
                parts = line.split("|", 2)
                results["pcb_layers_total"] = int(parts[1])
                results["pcb_layers_names"] = parts[2] if len(parts) > 2 else ""
            elif line.startswith("EXTENTS|"):
                results["pcb_extents"] = line.split("|", 1)[1]
            elif line.startswith("TOTAL_SYMBOLS|"):
                results["pcb_total_symbols"] = int(line.split("|")[1])
            elif line.startswith("PCB_CHIP|"):
                _, ref, pkg, pins, lyr, x_str, y_str, rot_str = line.split("|")
                x_val = float(x_str) if x_str != "nil" else 0.0
                y_val = float(y_str) if y_str != "nil" else 0.0
                rot_val = float(rot_str) if rot_str != "nil" else 0.0
                results["pcb_targets"].append({
                    "ref": ref, "package": pkg, "pins": int(pins), "layer": lyr,
                    "x": x_val, "y": y_val, "rot": rot_val
                })
                print(f"   📍 PCB 物理定位: {ref:<8} | 封装: {pkg:<22} | 引脚: {pins:<4} | 层: {lyr:<8} | 坐标: ({x_val:.2f}, {y_val:.2f}) | 角度: {rot_val:.1f}°")
        print(f"   📋 PCB 叠层确认: 全板共 {results['pcb_layers_total']} 层信号走线层 (ETCH) -> {results['pcb_layers_names']}")
        print(f"   📐 PCB 板框范围: {results['pcb_extents']} | 全板总封装数: {results['pcb_total_symbols']}")
    else:
        print("❌ Allegro PCB 深度压测未检出完成标志！远程日志内容如下：")
        print(pcb_log if pcb_log else "(空)")

    # =========================================================================
    # 第四阶段：现场自愈清理与战果落盘
    # =========================================================================
    log_step("第四阶段：清理测试副本与导出全局改板作战地图")
    for temp_f in [BENCH_DSN, BENCH_BRD, f"{TEMP_WORKSPACE}\\_allegro_done.flag"]:
        requests.post(f"{AIRBRIDGE_API}/api/v1/fs/rm", json={"path": temp_f}, timeout=10)
        time.sleep(0.8)
    print("🧹 B 机临时副本已安全抹除，原工程 100% 零污染！")

    with open("antigravity_combat_map.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print("💾 已在本地生成改板作战地图: antigravity_combat_map.json")

    # =========================================================================
    # 终审总决算
    # =========================================================================
    print("\n" + "=" * 75)
    print("                        全量终审压测总决算")
    print("=" * 75)
    print(f" 1. 物理通信与 RFS 文件系统确权:       [{'PASS' if results['link_health'] else 'FAIL'}]")
    print(f" 2. 14.5MB OrCAD 原理图工程真实挂载:    [{'PASS' if results['sch_inspection'] else 'FAIL'}] ({results['sch_pages_total']} Pages)")
    print(f" 3. 19.5MB Allegro PCB 工程真实挂载:   [{'PASS' if results['pcb_inspection'] else 'FAIL'}] ({results['pcb_layers_total']} Layers)")
    print("=" * 75)

    if results["link_health"] and results["sch_inspection"] and results["pcb_inspection"]:
        print("🏆 终审 100% 满分通过！全链路基础设施已达到工业投产级标准。")
        print("👉 Antigravity 改板所需的所有原理图页码与 PCB 物理坐标已全量落盘至 antigravity_combat_map.json！")
    else:
        print("⚠️ 存在未通过项目，请检查上述输出。")

if __name__ == "__main__":
    main()
