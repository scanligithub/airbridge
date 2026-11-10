# eda_engine.py (v2.4 - 含多版本进程沙箱 + Tcl/SKILL 跨版本 API Polyfill 垫片层)
# -*- coding: utf-8 -*-
import ctypes
import glob
import os
import re
import shutil
import string
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

user32 = ctypes.windll.user32 if sys.platform == "win32" else None


class EDALanguage:
  ORCAD_TCL = "ORCAD_TCL"
  ALLEGRO_SKILL = "ALLEGRO_SKILL"
  UNKNOWN = "UNKNOWN"


# ==============================================================================
# 1. OrCAD Capture Tcl 跨版本 API 垫片库 (兼容 SPB 16.6 / 17.2 / 17.4 / 22.1 / 23.1 / 24.1)
# ==============================================================================
ORCAD_TCL_POLYFILL = r"""
# --- AirBridge Cross-Version OrCAD Tcl Polyfill (Tcl 8.4 ~ 8.6 / SPB 16.6 ~ 24.1) ---
set ::DboState_OK 0

proc is_dbo_null {ptr} {
    return [expr {$ptr == "" || $ptr == "NULL" || $ptr == "0" || $ptr == "0x0"}]
}

proc ab_cstr {str_val} {
    return [DboTclHelper_sMakeCString $str_val]
}

proc ab_get_str {obj method_name} {
    if {[is_dbo_null $obj]} { return "" }
    set cstr [DboTclHelper_sMakeCString]
    catch { $obj $method_name $cstr }
    return [DboTclHelper_sGetConstCharPtr $cstr]
}

proc ab_get_prop {obj prop_name} {
    if {[is_dbo_null $obj]} { return "" }
    set p_cstr [DboTclHelper_sMakeCString $prop_name]
    set v_cstr [DboTclHelper_sMakeCString]
    set st [$obj GetEffectivePropStringValue $p_cstr $v_cstr]
    set val [DboTclHelper_sGetConstCharPtr $v_cstr]
    if {$val == ""} {
        catch {
            set st2 [$obj GetUserPropStringValue $p_cstr $v_cstr]
            set val [DboTclHelper_sGetConstCharPtr $v_cstr]
        }
    }
    return $val
}

proc ab_set_prop {obj prop_name prop_val} {
    if {[is_dbo_null $obj]} { return 0 }
    set p_cstr [DboTclHelper_sMakeCString $prop_name]
    set v_cstr [DboTclHelper_sMakeCString $prop_val]
    set st [$obj SetEffectivePropStringValue $p_cstr $v_cstr]
    catch { $obj MarkModified }
    return 1
}

proc ab_open_dsn {dsn_path} {
    set lSession $::DboSession_s_pDboSession
    DboSession -this $lSession
    set lStatus [DboState]
    set norm_p [string map {"\\" "/"} $dsn_path]
    set lPathStr [DboTclHelper_sMakeCString $norm_p]
    set lDesign [$lSession GetDesignAndSchematics $lPathStr $lStatus]
    if {[is_dbo_null $lDesign]} {
        puts "[ERROR] ab_open_dsn failed to load: $norm_p"
        return "NULL"
    }
    DboDesign -this $lDesign
    return $lDesign
}

proc ab_save_and_close {lDesign} {
    if {[is_dbo_null $lDesign]} { return }
    set lSession $::DboSession_s_pDboSession
    DboSession -this $lSession
    catch { $lDesign MarkModified }
    catch { $lSession SaveDesign $lDesign }
    catch { $lSession RemoveDesign $lDesign }
}
"""

# ==============================================================================
# 2. Allegro SKILL 跨版本 API 垫片库 (兼容 SPB 16.6 / 17.2 / 17.4 / 22.1 / 23.1 / 24.1)
# ==============================================================================
ALLEGRO_SKILL_POLYFILL = r"""
;; --- AirBridge Cross-Version Allegro SKILL Polyfill (SPB 16.6 ~ 24.1) ---

;; 1. 屏蔽各版本交互确认弹窗与高版本 DB 升级询问
axlSetVariable("NOCONFIRM" t)
axlSetVariable("allegro_dynam_timing" "off")

;; 2. 跨版本安全打开 .brd 设计 (兼容 16.6 ~ 24.1)
procedure(abOpenDesign(brdPath)
    let((ok)
        axlSetVariable("NOCONFIRM" t)
        ok = nil
        if(isCallable('axlOpenDesignForBatch) then
            errset(ok = axlOpenDesignForBatch(brdPath "wf") t)
        )
        if(!ok then
            errset(ok = axlOpenDesign(?design brdPath ?mode "wf") t)
        )
        ok
    )
)


;; 在交互式 Allegro PCB Editor 中打开设计（不使用 Batch API）。
;; axlOpenDesign 是 GUI 设计切换/打开入口；Batch API 不会满足用户需要的
;; “在当前 Allegro 窗口中打开并显示 PCB”这一交互目标。
procedure(abOpenDesignInteractive(brdPath)
    let((openResult currentResult currentName)
        printf("AirBridge: requested BRD path = %L\n" brdPath)
        if(!isFile(brdPath) then
            printf("AirBridge ERROR: BRD path is not an existing file: %L\n" brdPath)
            nil
        else
            openResult = errset(
                axlOpenDesign(?design brdPath ?mode "wf")
                t
            )
            printf("AirBridge: axlOpenDesign raw result = %L\n" openResult)

            ;; Do not interpret the API's return value alone as success.
            ;; Verify that the interactive Allegro session now has a design.
            currentResult = errset(axlCurrentDesign() t)
            when(currentResult currentName = car(currentResult))
            printf("AirBridge: axlCurrentDesign after open = %L\n" currentName)

            if(currentName then
                printf("AirBridge: interactive PCB design is now active.\n")
                t
            else
                printf("AirBridge ERROR: no active PCB design after axlOpenDesign.\n")
                nil
            )
        )
    )
)


;; 3. 为大模型高频误调的 axlDBGetDesignExtents 提供兼容 Polyfill 别名
if(!isCallable('axlDBGetDesignExtents) then
    procedure(axlDBGetDesignExtents()
        let((d)
            d = axlDBGetDesign()
            if(d d->bBox nil)
        )
    )
)

procedure(abGetBBox()
    let((d)
        d = axlDBGetDesign()
        if(d d->bBox nil)
    )
)

;; 4. 跨版本获取导电走线叠层 (兼容 16.6 ~ 24.1)
procedure(abGetEtchLayers()
    let((p)
        p = axlGetParam("paramLayerGroup:ETCH")
        if(p p->groupMembers nil)
    )
)

;; 5. 跨版本判断器件所在正反面 (TOP / BOTTOM)
procedure(abGetSymSide(sym)
    if(sym && sym->isMirrored "BOTTOM" "TOP")
)

;; 6. 跨版本安全获取引脚网络名 (无网络统一返回 "" 防止 fprintf %s 崩溃)
procedure(abGetPinNetName(pin)
    let((n)
        n = if(pin pin->net nil)
        if(n && n->name n->name "")
    )
)
"""


class CadenceVersionManager:
  """Cadence SPB 多版本探测器与进程级沙箱环境构建器 (支持 16.6 ~ 24.1+)"""

  KNOWN_VERSIONS = ["16.6", "17.2", "17.4", "22.1", "23.1", "24.1", "25.1"]

  @classmethod
  def discover_installations(cls) -> Dict[str, Dict[str, Any]]:
    candidate_roots = []

    def _add_candidate(path_str: str):
      if not path_str:
        return
      norm = os.path.normpath(path_str.strip().strip('"'))
      if os.path.isdir(norm) and norm not in candidate_roots:
        candidate_roots.append(norm)

    if os.environ.get("CDSROOT"):
      _add_candidate(os.environ["CDSROOT"])

    if sys.platform == "win32":
      try:
        import winreg

        reg_paths = [
            r"SOFTWARE\Cadence Design Systems",
            r"SOFTWARE\WOW6432Node\Cadence Design Systems",
        ]
        for hkey in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
          for rpath in reg_paths:
            try:
              with winreg.OpenKey(hkey, rpath) as base_key:
                sub_cnt, _, _ = winreg.QueryInfoKey(base_key)
                for i in range(sub_cnt):
                  sub_name = winreg.EnumKey(base_key, i)
                  with winreg.OpenKey(base_key, sub_name) as sub_key:
                    for val_name in (
                        "CDSROOT",
                        "InstallDir",
                        " Path",
                        "Path",
                        "TargetDir",
                    ):
                      try:
                        val, _ = winreg.QueryValueEx(sub_key, val_name)
                        if val:
                          _add_candidate(str(val))
                      except OSError:
                        pass
            except OSError:
              pass
      except Exception:
        pass

    drives = ["C", "D", "E", "F", "G"]
    if sys.platform == "win32":
      bitmask = ctypes.windll.kernel32.GetLogicalDrives()
      drives = [
          letter
          for i, letter in enumerate(string.ascii_uppercase)
          if bitmask & (1 << i)
      ]

    for drv in drives:
      for base_folder in ["Cadence", "OrCAD", r"Program Files\Cadence"]:
        base_dir = f"{drv}:\\{base_folder}"
        if os.path.isdir(base_dir):
          for pattern in ["SPB_*", "OrCAD_*", "Allegro_*"]:
            for match in glob.glob(os.path.join(base_dir, pattern)):
              _add_candidate(match)

    for exe_name in ("capture.exe", "allegro.exe"):
      found_exe = shutil.which(exe_name)
      if found_exe:
        p = os.path.dirname(os.path.abspath(found_exe))
        while len(p) > 4:
          if os.path.basename(p).lower() == "tools":
            _add_candidate(os.path.dirname(p))
            break
          p = os.path.dirname(p)

    installations: Dict[str, Dict[str, Any]] = {}
    for root in candidate_roots:
      info = cls._inspect_cdsroot(root)
      if info:
        ver = info["version"]
        if ver not in installations or (
            info["capture_exe"] and info["allegro_exe"]
        ):
          installations[ver] = info

    return installations

  @classmethod
  def _inspect_cdsroot(cls, cdsroot: str) -> Optional[Dict[str, Any]]:
    tools_dir = os.path.join(cdsroot, "tools")
    if not os.path.isdir(tools_dir):
      return None

    capture_candidates = [
        os.path.join(tools_dir, "bin", "capture.exe"),
        os.path.join(tools_dir, "capture", "capture.exe"),
    ]
    capture_exe = next(
        (p for p in capture_candidates if os.path.isfile(p)), None
    )

    allegro_candidates = [
        os.path.join(tools_dir, "bin", "allegro.exe"),
        os.path.join(tools_dir, "pcb", "bin", "allegro.exe"),
    ]
    allegro_exe = next(
        (p for p in allegro_candidates if os.path.isfile(p)), None
    )

    if not capture_exe and not allegro_exe:
      return None

    version = cls._detect_version_from_root(cdsroot)

    autoload_dirs = []
    sys_autoload = os.path.join(
        tools_dir, "capture", "tclscripts", "capAutoLoad"
    )
    if os.path.isdir(sys_autoload):
      autoload_dirs.append(sys_autoload)

    home_dir = os.environ.get("HOME") or os.path.expanduser("~")
    if home_dir and os.path.isdir(home_dir):
      cdssetup_cap = os.path.join(home_dir, "cdssetup", "OrCAD_Capture")
      if os.path.isdir(cdssetup_cap):
        for sub in glob.glob(
            os.path.join(cdssetup_cap, "*", "tclscripts", "capAutoLoad")
        ):
          if version in sub and os.path.isdir(sub) and sub not in autoload_dirs:
            autoload_dirs.append(sub)

    return {
        "version": version,
        "cdsroot": cdsroot,
        "capture_exe": capture_exe or "",
        "allegro_exe": allegro_exe or "",
        "autoload_dirs": autoload_dirs,
    }

  @classmethod
  def _detect_version_from_root(cls, cdsroot: str) -> str:
    base_name = os.path.basename(cdsroot)
    m = re.search(r"(\d{2}\.\d)", base_name)
    if m:
      return m.group(1)
    for v in cls.KNOWN_VERSIONS:
      if v in cdsroot:
        return v
    return "17.2"

  @classmethod
  def sniff_design_file_version(cls, file_path: str) -> Optional[str]:
    if not file_path or not os.path.isfile(file_path):
      return None
    try:
      with open(file_path, "rb") as f:
        header = f.read(4096)
      text = header.decode("latin1", errors="ignore")
      for ver in ["24.1", "23.1", "22.1", "17.4", "17.2", "16.6", "16.5"]:
        if (
            f" {ver}" in text
            or f"_{ver}" in text
            or f"v{ver.replace('.', '-')}" in text.lower()
        ):
          return "16.6" if ver == "16.5" else ver
    except Exception:
      pass
    return None

  @classmethod
  def sniff_version_from_script(
      cls, script_content: str, target_file: str = ""
  ) -> Optional[str]:
    candidates = []
    if target_file:
      candidates.append(target_file)

    for m in re.finditer(
        r'["\']([a-zA-Z]:[\\/][^"\']+\.(?:dsn|brd))["\']',
        script_content,
        re.IGNORECASE,
    ):
      raw_p = m.group(1)
      try:
        decoded_p = re.sub(
            r"\\u([0-9a-fA-F]{4})", lambda x: chr(int(x.group(1), 16)), raw_p
        )
      except Exception:
        decoded_p = raw_p
      candidates.append(decoded_p)

    for cand in candidates:
      ver = cls.sniff_design_file_version(cand)
      if ver:
        return ver
    return None

  @classmethod
  def select_best_installation(
      cls,
      requested_version: str = "auto",
      script_content: str = "",
      target_file: str = "",
  ) -> Tuple[Optional[Dict[str, Any]], str]:
    installs = cls.discover_installations()
    if not installs:
      return None, "未在注册表或磁盘中扫描到标准目录，将回退至系统 PATH 查找"

    req = (requested_version or "auto").strip().lower()

    if req != "auto":
      for ver, info in installs.items():
        if req in ver.lower() or ver.lower() in req:
          return (
              info,
              f"按显式指定版本 [{req}] 命中引擎: SPB {ver} ({info['cdsroot']})",
          )
      avail_str = ", ".join(sorted(installs.keys()))
      fallback_ver = "17.2" if "17.2" in installs else sorted(installs.keys())[0]
      return (
          installs[fallback_ver],
          f"请求版本 [{req}] 未安装 (本机可用: {avail_str})，已安全回退至 SPB"
          f" {fallback_ver}",
      )

    sniffed_ver = cls.sniff_version_from_script(script_content, target_file)
    if sniffed_ver and sniffed_ver in installs:
      info = installs[sniffed_ver]
      return (
          info,
          f"根据工程原档文件头特征自动匹配同版本引擎: SPB {sniffed_ver}"
          f" ({info['cdsroot']})",
      )

    if len(installs) == 1:
      only_ver = next(iter(installs.keys()))
      info = installs[only_ver]
      return info, f"自动选用本机唯一安装版本: SPB {only_ver} ({info['cdsroot']})"

    for pref in ["17.2", "17.4", "23.1", "22.1", "24.1", "16.6"]:
      if pref in installs:
        info = installs[pref]
        return (
            info,
            f"多版本共存环境 ({', '.join(sorted(installs.keys()))})，默认优选"
            f" SPB {pref} ({info['cdsroot']})",
        )

    first_ver = sorted(installs.keys())[0]
    return installs[first_ver], f"默认选用 SPB {first_ver}"

  @staticmethod
  def build_isolated_env(cdsroot: str) -> dict:
    env = os.environ.copy()
    env["CDS_DONT_SHOW_CEIP"] = "1"
    env["CDS_DISABLE_CEIP"] = "1"
    env["CDS_DISABLE_STARTPAGE"] = "1"

    if not cdsroot or not os.path.isdir(cdsroot):
      return env

    raw_paths = env.get("PATH", "").split(os.pathsep)
    clean_paths = []
    for p in raw_paths:
      p_low = p.lower()
      if any(
          k in p_low
          for k in [
              "cadence",
              "spb_",
              "orcad",
              "tools\\bin",
              "tools\\pcb",
              "tools\\capture",
              "openaccess",
          ]
      ):
        continue
      clean_paths.append(p)

    target_bins = [
        os.path.join(cdsroot, "tools", "bin"),
        os.path.join(cdsroot, "tools", "capture"),
        os.path.join(cdsroot, "tools", "pcb", "bin"),
        os.path.join(cdsroot, "tools", "fet", "bin"),
        os.path.join(cdsroot, "tools", "jre", "bin"),
        os.path.join(cdsroot, "OpenAccess", "bin", "win32", "opt"),
        os.path.join(cdsroot, "OpenAccess", "bin", "win64", "opt"),
    ]
    valid_target_bins = [b for b in target_bins if os.path.isdir(b)]

    env["CDSROOT"] = cdsroot
    env["CDS_INST_DIR"] = cdsroot
    env["PATH"] = os.pathsep.join(valid_target_bins + clean_paths)
    return env


def find_cadence_tool(tool_name: str, cdsroot: str = "") -> str:
  if cdsroot and os.path.isdir(cdsroot):
    info = CadenceVersionManager._inspect_cdsroot(cdsroot)
    if info:
      if tool_name.lower() == "capture.exe" and info["capture_exe"]:
        return info["capture_exe"]
      if tool_name.lower() == "allegro.exe" and info["allegro_exe"]:
        return info["allegro_exe"]

  info, _ = CadenceVersionManager.select_best_installation("auto")
  if info:
    if tool_name.lower() == "capture.exe" and info["capture_exe"]:
      return info["capture_exe"]
    if tool_name.lower() == "allegro.exe" and info["allegro_exe"]:
      return info["allegro_exe"]

  found = shutil.which(tool_name)
  return found if found else tool_name


def find_cap_autoload_dirs(capture_exe: str, cdsroot: str = "") -> List[str]:
  dirs = []
  if cdsroot and os.path.isdir(cdsroot):
    info = CadenceVersionManager._inspect_cdsroot(cdsroot)
    if info and info["autoload_dirs"]:
      for d in info["autoload_dirs"]:
        if d not in dirs:
          dirs.append(d)

  if capture_exe and os.path.exists(capture_exe):
    bin_dir = os.path.dirname(os.path.abspath(capture_exe))
    tools_dir = os.path.dirname(bin_dir)
    cand1 = os.path.join(tools_dir, "capture", "tclscripts", "capAutoLoad")
    if os.path.isdir(cand1) and cand1 not in dirs:
      dirs.append(cand1)
    cand2 = os.path.join(bin_dir, "tclscripts", "capAutoLoad")
    if os.path.isdir(cand2) and cand2 not in dirs:
      dirs.append(cand2)

  return dirs


def detect_script_type(content: str, filename_hint: str = "") -> str:
  skill_keywords = [
      "procedure(",
      "defun(",
      "let(",
      "axl",
      "printf(",
      "abOpenDesign(",
  ]
  tcl_keywords = [
      "proc ",
      "$::DboSession",
      "DboState",
      "IterateDesigns",
      "ab_open_dsn",
  ]

  skill_score = sum(1 for kw in skill_keywords if kw in content)
  tcl_score = sum(1 for kw in tcl_keywords if kw in content)

  if skill_score > 0 and skill_score >= tcl_score:
    return EDALanguage.ALLEGRO_SKILL
  if tcl_score > 0 and tcl_score > skill_score:
    return EDALanguage.ORCAD_TCL

  lower_hint = filename_hint.lower()
  if lower_hint.endswith((".il", ".skill", ".scr")):
    return EDALanguage.ALLEGRO_SKILL
  if lower_hint.endswith((".tcl", ".tclproc")):
    return EDALanguage.ORCAD_TCL

  return EDALanguage.ORCAD_TCL


class DialogKillerWatchdog(threading.Thread):

  def __init__(self, target_pid: int, log_callback: Callable[[str], None]):
    super().__init__(daemon=True)
    self.target_pid = target_pid
    self.log_callback = log_callback
    self.running = True
    self.handled_hwnds = set()

  def run(self):
    if not user32:
      return

    EnumWindowsProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
    )

    def check_window(hwnd, lParam):
      if not user32.IsWindowVisible(hwnd):
        return True
      pid = ctypes.c_ulong()
      user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
      if pid.value == self.target_pid:
        class_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_buf, 256)
        title_buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title_buf, 512)
        title = title_buf.value

        popup_keywords = [
            "About",
            "Choices",
            "Launch",
            "Improvement",
            "Product Choices",
            "License",
            "CadenceOrCAD",
            "OrCAD X",
        ]
        if class_buf.value == "#32770" or any(
            k.lower() in title.lower() for k in popup_keywords
        ):
          if hwnd not in self.handled_hwnds:
            # Observe and report only. Never send Enter/IDOK/close messages:
            # unknown dialogs may be license, migration, missing-library, or save prompts.
            safe_title = title if title else "<无标题>"
            self.log_callback(
                f"[Watchdog] 检测到弹窗 [{safe_title}]；未自动操作，请人工检查"
            )
            self.handled_hwnds.add(hwnd)
      return True

    while self.running:
      user32.EnumWindows(EnumWindowsProc(check_window), 0)
      time.sleep(0.3)

  def stop(self):
    self.running = False


class EDAExecutor:

  def __init__(self, workspace_dir: str = None):
    env_ws = os.environ.get("AIRBRIDGE_WORKSPACE_DIR", "").strip()
    if workspace_dir:
      self.workspace_dir = workspace_dir
    elif env_ws:
      self.workspace_dir = env_ws
    elif os.path.exists("E:\\"):
      self.workspace_dir = r"E:\_airbridge_temp"
    elif os.path.exists("D:\\"):
      self.workspace_dir = r"D:\_airbridge_temp"
    else:
      self.workspace_dir = r"C:\_airbridge_temp"

    os.makedirs(self.workspace_dir, exist_ok=True)
    self.active_process = None
    # 仅跟踪由 AirBridge 启动的 GUI；关闭命令不会影响用户手动打开的其他实例。
    self.managed_processes: Dict[str, subprocess.Popen] = {}
    self.process_lock = threading.Lock()

  def get_application_process(self, application: str):
    """Return the live Cadence process started and tracked by AirBridge."""
    with self.process_lock:
      proc = self.managed_processes.get(application)
      if proc and proc.poll() is not None:
        self.managed_processes.pop(application, None)
        return None
      return proc

  def execute_script(
      self,
      script_content: str,
      filename_hint: str,
      log_callback: Callable[[str], None],
      timeout: int = 60,
      version: str = "auto",
      target_file: str = "",
      keep_open: bool = False,
  ) -> Tuple[int, str]:
    lang = detect_script_type(script_content, filename_hint)
    application = "capture" if lang == EDALanguage.ORCAD_TCL else "allegro"
    if keep_open:
      existing = self.get_application_process(application)
      if existing:
        msg = (
            f"[EDA Engine] {application} 已由 AirBridge 启动且仍在运行 "
            f"(PID={existing.pid})；为避免重复实例，本次不再启动"
        )
        log_callback(msg)
        return 0, msg

    ver_info, reason_msg = CadenceVersionManager.select_best_installation(
        requested_version=version,
        script_content=script_content,
        target_file=target_file,
    )
    log_callback(f"[EDA Dispatcher] 语言判定: [{lang}] | {reason_msg}")

    if lang == EDALanguage.ORCAD_TCL:
      return self._run_orcad_tcl(
          script_content,
          log_callback,
          timeout,
          ver_info,
          target_file=target_file,
          keep_open=keep_open,
      )
    else:
      return self._run_allegro_skill(
          script_content,
          log_callback,
          timeout,
          ver_info,
          target_file=target_file,
          keep_open=keep_open,
      )

  def _run_orcad_tcl(
      self,
      script_content: str,
      log_callback: Callable[[str], None],
      timeout: int,
      ver_info: Optional[Dict[str, Any]] = None,
      target_file: str = "",
      keep_open: bool = False,
  ) -> Tuple[int, str]:
    if target_file:
      if os.path.splitext(target_file)[1].lower() != ".dsn":
        msg = f"[EDA Engine] Capture 目标文件必须是 .dsn 文件: {target_file}"
        log_callback(msg)
        return -1, msg
      if not os.path.isfile(target_file):
        msg = f"[EDA Engine] 主机 B 上找不到所选原理图文件: {target_file}"
        log_callback(msg)
        return -1, msg

    cdsroot = ver_info["cdsroot"] if ver_info else ""
    ver_str = ver_info["version"] if ver_info else "unknown"
    capture_exe = (
        ver_info["capture_exe"]
        if (ver_info and ver_info.get("capture_exe"))
        else find_cadence_tool("capture.exe", cdsroot)
    )
    log_callback(f"[EDA Engine] 定位 OrCAD ({ver_str}) 路径: {capture_exe}")

    log_file = os.path.join(self.workspace_dir, "_cadence_stdout.log")
    done_flag = os.path.join(self.workspace_dir, "_orcad_done.flag")
    for fpath in [log_file, done_flag]:
      if os.path.exists(fpath):
        try:
          os.remove(fpath)
        except Exception:
          pass

    user_script = os.path.join(self.workspace_dir, "_user_task.tcl")
    with open(user_script, "w", encoding="utf-8") as f:
      f.write(script_content)

    wrapper_file = os.path.join(self.workspace_dir, "_bootstrap.tcl")
    norm_log = log_file.replace("\\", "/")
    norm_done = done_flag.replace("\\", "/")
    norm_user = user_script.replace("\\", "/")

    lines = [
        'set _fp [open "' + norm_log + '" "w"]',
        "catch { fconfigure $_fp -encoding utf-8 }",
        'set ::AIRBRIDGE_SPB_VER "' + ver_str + '"',
        "set ::AIRBRIDGE_TCL_VER $::tcl_version",
        ORCAD_TCL_POLYFILL.strip(),
        "rename puts _raw_puts",
        "proc puts {args} {",
        "    global _fp",
        "    catch { eval _raw_puts $args }",
        "    if {[info exists _fp]} {",
        "        catch { eval _raw_puts [list $_fp] $args; flush $_fp }",
        "    }",
        "}",
        "rename exit _raw_exit",
        "proc exit {args} { return }",
        'catch { source "' + norm_user + '" } _err',
        (
            'if {$_err != ""} { catch { _raw_puts $_fp "[ERROR] $_err"; flush'
            " $_fp } }"
        ),
        "catch { flush $_fp; close $_fp }",
        (
            'catch { set _df [open "'
            + norm_done
            + '" "w"]; _raw_puts $_df "OK"; close $_df }'
        ),
        # Keep Capture's event loop alive after the probe. In keep-open mode,
        # closure is initiated later by the independent close command.
    ]
    with open(wrapper_file, "w", encoding="utf-8") as f:
      f.write("\n".join(lines) + "\n")

    autoload_dirs = find_cap_autoload_dirs(capture_exe, cdsroot)
    injected_hooks = []
    norm_wrapper = wrapper_file.replace("\\", "/")
    if not autoload_dirs:
      log_callback(
          "[EDA Engine] 警告：未找到 Capture capAutoLoad 目录；"
          "Capture 仍会尝试启动，但本次 Tcl 脚本不会自动注入执行"
      )

    for adir in autoload_dirs:
      hook_path = os.path.join(adir, "zz_airbridge_autorun.tcl")
      norm_hook = hook_path.replace("\\", "/")
      hook_lines = [
          "if {[info exists ::_airbridge_autoload_ran]} { return }",
          "set ::_airbridge_autoload_ran 1",
          'catch { file delete -force "' + norm_hook + '" }',
          'catch { source "' + norm_wrapper + '" }',
      ]
      try:
        with open(hook_path, "w", encoding="utf-8") as hf:
          hf.write("\n".join(hook_lines) + "\n")
        injected_hooks.append(hook_path)
        log_callback(f"[EDA Engine] 已注入一次性自毁引导钩子: {hook_path}")
      except Exception as e:
        log_callback(f"[EDA Engine] 写入引导钩子异常 ({adir}): {e}")

    cmd = [capture_exe]
    # 将目标设计文件作为命令行参数传给 capture.exe，使 Capture 在启动时直接打开该设计。
    # 只有在 Capture GUI 中打开设计后，Dbo 对象方法（CreateDesign/GetDesignAndSchematics/PlacePart 等）
    # 才不会挂起，因为 Dbo 方法依赖 Capture 的 GUI 消息泵与活动设计会话。
    # 注意：target_file 是主机 B 上的路径，此处不做本地存在性检查，由 Capture 端自行处理。
    if target_file:
      cmd.append(target_file)
      log_callback(f"[EDA Engine] 以设计文件启动 Capture: {target_file}")
    isolated_env = CadenceVersionManager.build_isolated_env(cdsroot)
    try:
      return self._spawn_and_monitor(
          cmd,
          log_file,
          log_callback,
          timeout,
          done_flag_path=done_flag,
          custom_env=isolated_env,
          keep_open=keep_open,
          application_key="capture",
      )
    finally:
      for hp in injected_hooks:
        if os.path.exists(hp):
          try:
            os.remove(hp)
          except Exception:
            pass

  def _run_allegro_skill(
      self,
      script_content: str,
      log_callback: Callable[[str], None],
      timeout: int,
      ver_info: Optional[Dict[str, Any]] = None,
      target_file: str = "",
      keep_open: bool = False,
  ) -> Tuple[int, str]:
    if target_file:
      if os.path.splitext(target_file)[1].lower() != ".brd":
        msg = (
            f"[EDA Engine] Allegro 目标文件必须是 .brd 文件: {target_file}"
        )
        log_callback(msg)
        return -1, msg
      if not os.path.isfile(target_file):
        msg = (
            f"[EDA Engine] 主机 B 上找不到所选 PCB 文件: {target_file}"
        )
        log_callback(msg)
        return -1, msg

    cdsroot = ver_info["cdsroot"] if ver_info else ""
    ver_str = ver_info["version"] if ver_info else "unknown"
    allegro_exe = (
        ver_info["allegro_exe"]
        if (ver_info and ver_info.get("allegro_exe"))
        else find_cadence_tool("allegro.exe", cdsroot)
    )
    log_callback(f"[EDA Engine] 定位 Allegro ({ver_str}) 路径: {allegro_exe}")

    # 写入跨版本 SKILL Polyfill 垫片文件
    polyfill_file = os.path.join(self.workspace_dir, "_polyfill.il")
    with open(polyfill_file, "w", encoding="utf-8") as pf:
      pf.write(ALLEGRO_SKILL_POLYFILL.strip() + "\n")

    done_flag = os.path.join(self.workspace_dir, "_allegro_done.flag")
    if os.path.exists(done_flag):
      try:
        os.remove(done_flag)
      except Exception:
        pass

    # The marker means the initialization script reached the board-open stage.
    # Write it before the interactive command because axlShell fallback may
    # terminate the active SKILL function by design.
    norm_done = done_flag.replace("\\", "/")
    script_content += (
        "\n\n;; AirBridge task completion marker\n"
        'let((ab_fp) '
        'ab_fp = outfile("' + norm_done + '") '
        'when(ab_fp fprintf(ab_fp "OK") close(ab_fp)) '
        ')\n'
    )

    if target_file:
      # Keep non-ASCII project paths out of the generated UTF-8 SKILL source.
      # The path sidecar uses the Windows ANSI code page expected by Allegro
      # 17.2; the generated SKILL source therefore remains ASCII.
      brd_path_file = os.path.join(
          self.workspace_dir, "_allegro_target_brd_path.txt"
      )
      path_encoding = "mbcs" if sys.platform == "win32" else "utf-8"
      try:
        with open(brd_path_file, "w", encoding=path_encoding) as path_fp:
          path_fp.write(target_file)
      except UnicodeEncodeError as exc:
        msg = (
            "[EDA Engine] BRD 路径包含当前 Windows ANSI 代码页无法表示的字符；"
            "请将设计放到可由系统代码页表示的目录，或调整系统区域设置。"
        )
        log_callback(f"{msg} ({exc})")
        return -1, msg

      norm_brd_path_file = brd_path_file.replace("\\", "/")
      skill_path_file = norm_brd_path_file.replace('"', '\\"')
      script_content += (
          "\n\n;; AirBridge interactive PCB open\n"
          "let((ab_path_fp ab_target_file ab_read_ok ab_open_result)\n"
          '    ab_path_fp = infile("' + skill_path_file + '")\n'
          '    ab_target_file = ""\n'
          "    when(ab_path_fp ab_read_ok = gets(ab_target_file ab_path_fp))\n"
          "    when(ab_path_fp close(ab_path_fp))\n"
          '    printf("AirBridge: BRD sidecar opened = %L, line read = %L\\n" '
          "if(ab_path_fp t nil) if(ab_read_ok then t else nil))\n"
          '    printf("AirBridge: BRD path read from sidecar = %L\\n" ab_target_file)\n'
          "    when(ab_read_ok && ab_target_file != \"\" "
          "ab_open_result = abOpenDesignInteractive(ab_target_file))\n"
          "    unless(ab_open_result\n"
          '        printf("AirBridge ERROR: selected PCB did not become the active Allegro design.\\n")\n'
          "    )\n"
          ")\n"
      )

    skill_file = os.path.join(self.workspace_dir, "_task.il")
    with open(skill_file, "w", encoding="utf-8") as f:
      f.write(script_content)

    scr_file = os.path.join(self.workspace_dir, "_run.scr")
    norm_poly = polyfill_file.replace("\\", "/")
    norm_skill = skill_file.replace("\\", "/")
    with open(scr_file, "w", encoding="utf-8") as f:
      f.write("set window message\n")
      f.write('skill load("' + norm_poly + '")\n')
      f.write('skill load("' + norm_skill + '")\n')
      # Do not send the unsupported fillin command; Allegro 17.2
      # reports "Command not found" and pollutes the replay log.

    allegro_log = os.path.join(self.workspace_dir, "allegro.log")
    if os.path.exists(allegro_log):
      try:
        os.remove(allegro_log)
      except Exception:
        pass

    cmd = [allegro_exe, "-s", scr_file]
    isolated_env = CadenceVersionManager.build_isolated_env(cdsroot)
    return self._spawn_and_monitor(
        cmd,
        allegro_log,
        log_callback,
        timeout,
        done_flag_path=done_flag,
        custom_env=isolated_env,
        keep_open=keep_open,
        application_key="allegro",
    )

  def _spawn_and_monitor(
      self,
      cmd: list,
      watch_log_path: str,
      log_callback: Callable[[str], None],
      timeout: int,
      done_flag_path: str = None,
      custom_env: dict = None,
      keep_open: bool = False,
      application_key: str = "",
  ) -> Tuple[int, str]:
    log_callback(f"[Process Runner] 启动: {' '.join(cmd)}")

    run_env = custom_env if custom_env else os.environ.copy()
    run_env["CDS_DONT_SHOW_CEIP"] = "1"
    run_env["CDS_DISABLE_CEIP"] = "1"
    run_env["CDS_DISABLE_STARTPAGE"] = "1"

    try:
      proc = subprocess.Popen(
          cmd,
          cwd=self.workspace_dir,
          env=run_env,
          # The GUI remains alive after the launch probe completes. Do not
          # leave an unread PIPE attached to a long-running Cadence process.
          stdout=subprocess.DEVNULL,
          stderr=subprocess.STDOUT,
      )
      self.active_process = proc
      if keep_open and application_key:
        with self.process_lock:
          self.managed_processes[application_key] = proc
    except Exception as e:
      err_msg = f"[Process Runner 异常] 无法启动进程: {str(e)}"
      log_callback(err_msg)
      return -1, err_msg

    watchdog = DialogKillerWatchdog(proc.pid, log_callback)
    watchdog.start()

    output_collected = []
    start_time = time.time()
    offset = 0

    try:
      while True:
        if watch_log_path and os.path.exists(watch_log_path):
          try:
            with open(
                watch_log_path, "r", encoding="utf-8", errors="ignore"
            ) as lf:
              lf.seek(offset)
              new_lines = lf.readlines()
              offset = lf.tell()
              for nl in new_lines:
                line_str = nl.rstrip()
                if line_str:
                  output_collected.append(line_str)
                  log_callback(line_str)
          except Exception:
            pass

        ret = proc.poll()
        if ret is not None:
          break

        if done_flag_path and os.path.exists(done_flag_path):
          time.sleep(0.3)
          if keep_open:
            # 任务标记代表启动探针/脚本执行完成，不代表要关闭 GUI。
            # 进程句柄保留在 managed_processes，等待独立的关闭命令。
            ret = proc.poll()
            if ret is None:
              log_callback(
                  f"[Process Runner] 脚本执行完成；{application_key} GUI 保持打开 "
                  f"(PID={proc.pid})"
              )
              ret = 0
            else:
              log_callback(
                  f"[Process Runner] {application_key} 进程已自行退出，退出码: {ret}"
              )
          else:
            if proc.poll() is None:
              self.request_graceful_close(
                  log_callback, reason="Cadence 脚本执行完成", process=proc
              )
              self._wait_for_exit(15, process=proc)
            ret = proc.poll()
            if ret is None:
              log_callback(
                  "[Process Runner] 已请求正常关闭，但程序仍在运行；"
                  "可能存在保存/确认弹窗，保留现场，请人工检查"
              )
              ret = 0
            else:
              log_callback("[Process Runner] Cadence 程序已正常退出")
          break

        if time.time() - start_time > timeout:
          if keep_open:
            log_callback(
                f"[Timeout] {application_key} 启动后等待脚本完成超时；"
                "按“保持打开”模式保留 GUI，不自动关闭"
            )
          else:
            log_callback("[Timeout] 执行超时，先请求 Cadence 正常关闭...")
            self.request_graceful_close(
                log_callback, reason="任务超时", process=proc
            )
            if not self._wait_for_exit(15, process=proc):
              log_callback(
                  "[Process Runner] 正常关闭等待超时；为避免丢失未保存设计，"
                  "不自动强制结束，请人工检查并关闭"
              )
          return -2, "Execution Timeout"

        time.sleep(0.15)

      time.sleep(0.15)
      if watch_log_path and os.path.exists(watch_log_path):
        try:
          with open(
              watch_log_path, "r", encoding="utf-8", errors="ignore"
          ) as lf:
            lf.seek(offset)
            tail_lines = lf.readlines()
            for tl in tail_lines:
              line_str = tl.rstrip()
              if line_str:
                output_collected.append(line_str)
                log_callback(line_str)
        except Exception:
          pass

      log_callback(f"[Process Runner] 任务完成，退出码: {ret}")
      return ret, "\n".join(output_collected)
    finally:
      watchdog.stop()
      if keep_open and proc.poll() is not None and application_key:
        with self.process_lock:
          if self.managed_processes.get(application_key) is proc:
            self.managed_processes.pop(application_key, None)
      if self.active_process is proc:
        self.active_process = None

  def _wait_for_exit(
      self,
      grace_seconds: float,
      process: Optional[subprocess.Popen] = None,
  ) -> bool:
    proc = process or self.active_process
    if not proc:
      return True
    deadline = time.time() + max(0.0, grace_seconds)
    while proc.poll() is None and time.time() < deadline:
      time.sleep(0.2)
    return proc.poll() is not None

  def request_graceful_close(
      self,
      log_callback: Callable[[str], None],
      reason: str = "任务结束",
      process: Optional[subprocess.Popen] = None,
  ) -> bool:
    """Ask Cadence windows to close through the normal Windows close path."""
    proc = process or self.active_process
    if not proc or proc.poll() is not None:
      return True
    if sys.platform != "win32" or not user32:
      log_callback("[Process Runner] 无法发送 GUI 关闭消息；不强制终止")
      return False

    sent = []
    EnumWindowsProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
    )

    def close_window(hwnd, _lparam):
      if not user32.IsWindowVisible(hwnd):
        return True
      pid = ctypes.c_ulong()
      user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
      if pid.value == proc.pid:
        class_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_buf, 256)
        # Never close a modal dialog directly: it may be an unknown save,
        # license, migration, or missing-library prompt.
        if class_buf.value == "#32770":
          return True
        title_buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title_buf, 512)
        title = title_buf.value or "<无标题>"
        if user32.PostMessageW(hwnd, 0x0010, 0, 0):  # WM_CLOSE
          sent.append(title)
      return True

    try:
      user32.EnumWindows(EnumWindowsProc(close_window), 0)
    except Exception as exc:
      log_callback(f"[Process Runner] 正常关闭请求失败: {exc}")
      return False

    if sent:
      log_callback(
          f"[Process Runner] {reason}：已发送 WM_CLOSE："
          + "；".join(sent)
      )
      return True

    log_callback(
        f"[Process Runner] {reason}：未找到可关闭的顶层窗口；"
        "等待程序自行退出"
    )
    return False

  def close_application(
      self,
      application: str,
      log_callback: Callable[[str], None],
      grace_seconds: float = 15.0,
  ) -> Tuple[bool, str]:
    """Gracefully close only the Cadence process started by AirBridge."""
    app_key = (application or "").strip().lower()
    if app_key not in ("capture", "allegro"):
      msg = f"[EDA Close] 不支持的应用类型: {application}"
      log_callback(msg)
      return False, msg

    proc = self.get_application_process(app_key)
    if not proc:
      msg = (
          f"[EDA Close] 没有由 AirBridge 跟踪的 {app_key} 实例；"
          "不会关闭用户手动启动的 Cadence 进程"
      )
      log_callback(msg)
      return False, msg

    log_callback(
        f"[EDA Close] 正在关闭 {app_key} (PID={proc.pid})；"
        "通过 Windows WM_CLOSE 请求正常退出"
    )
    self.request_graceful_close(
        log_callback, reason=f"用户请求关闭 {app_key}", process=proc
    )
    if self._wait_for_exit(grace_seconds, process=proc):
      with self.process_lock:
        if self.managed_processes.get(app_key) is proc:
          self.managed_processes.pop(app_key, None)
      msg = f"[EDA Close] {app_key} 已正常退出 (PID={proc.pid})"
      log_callback(msg)
      return True, msg

    msg = (
        f"[EDA Close] {app_key} 在 {grace_seconds:.0f}s 内未退出；"
        "可能存在保存/确认对话框。未强制结束，请在主机 B 上人工处理"
    )
    log_callback(msg)
    return False, msg

  def kill_active_task(self):
    """Explicit last-resort termination; never used for normal completion."""
    if self.active_process and self.active_process.poll() is None:
      if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(self.active_process.pid)],
            capture_output=True,
        )
      else:
        self.active_process.kill()
