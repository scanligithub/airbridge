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
            k in title for k in popup_keywords
        ):
          if hwnd not in self.handled_hwnds:
            self.log_callback(f"[Watchdog] 自动穿透拦截弹窗 [{title}]")
            self.handled_hwnds.add(hwnd)

          user32.PostMessageW(hwnd, 0x0100, 0x0D, 0)
          user32.PostMessageW(hwnd, 0x0101, 0x0D, 0)
          user32.PostMessageW(hwnd, 0x0111, 1, 0)
          if any(k in title for k in ["About", "Improvement", "Launch"]):
            user32.PostMessageW(hwnd, 0x0010, 0, 0)
      return True

    while self.running:
      user32.EnumWindows(EnumWindowsProc(check_window), 0)
      time.sleep(0.3)

  def stop(self):
    self.running = False


class EDAExecutor:

  def __init__(self, workspace_dir: str = None):
    if os.path.exists(r"D:\Cadence") or os.path.exists("D:\\"):
      self.workspace_dir = r"D:\_airbridge_temp"
    else:
      self.workspace_dir = r"C:\_airbridge_temp"

    os.makedirs(self.workspace_dir, exist_ok=True)
    self.active_process = None

  def execute_script(
      self,
      script_content: str,
      filename_hint: str,
      log_callback: Callable[[str], None],
      timeout: int = 60,
      version: str = "auto",
      target_file: str = "",
  ) -> Tuple[int, str]:
    lang = detect_script_type(script_content, filename_hint)
    ver_info, reason_msg = CadenceVersionManager.select_best_installation(
        requested_version=version,
        script_content=script_content,
        target_file=target_file,
    )
    log_callback(f"[EDA Dispatcher] 语言判定: [{lang}] | {reason_msg}")

    if lang == EDALanguage.ORCAD_TCL:
      return self._run_orcad_tcl(script_content, log_callback, timeout, ver_info)
    else:
      return self._run_allegro_skill(
          script_content, log_callback, timeout, ver_info
      )

  def _run_orcad_tcl(
      self,
      script_content: str,
      log_callback: Callable[[str], None],
      timeout: int,
      ver_info: Optional[Dict[str, Any]] = None,
  ) -> Tuple[int, str]:
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
        "catch { _raw_exit 0 }",
    ]
    with open(wrapper_file, "w", encoding="utf-8") as f:
      f.write("\n".join(lines) + "\n")

    autoload_dirs = find_cap_autoload_dirs(capture_exe, cdsroot)
    injected_hooks = []
    norm_wrapper = wrapper_file.replace("\\", "/")

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
    isolated_env = CadenceVersionManager.build_isolated_env(cdsroot)
    try:
      return self._spawn_and_monitor(
          cmd,
          log_file,
          log_callback,
          timeout,
          done_flag_path=done_flag,
          custom_env=isolated_env,
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
  ) -> Tuple[int, str]:
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

    if "(exit" not in script_content and "exit(" not in script_content:
      script_content += "\n\n;; AirBridge 自动注入安全退出指令\n(exit)\n"

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
      f.write("exit\n")
      f.write("fillin yes\n")

    allegro_log = os.path.join(self.workspace_dir, "allegro.log")
    if os.path.exists(allegro_log):
      try:
        os.remove(allegro_log)
      except Exception:
        pass

    cmd = [allegro_exe, "-s", scr_file]
    isolated_env = CadenceVersionManager.build_isolated_env(cdsroot)
    return self._spawn_and_monitor(
        cmd, allegro_log, log_callback, timeout, custom_env=isolated_env
    )

  def _spawn_and_monitor(
      self,
      cmd: list,
      watch_log_path: str,
      log_callback: Callable[[str], None],
      timeout: int,
      done_flag_path: str = None,
      custom_env: dict = None,
  ) -> Tuple[int, str]:
    log_callback(f"[Process Runner] 启动: {' '.join(cmd)}")

    run_env = custom_env if custom_env else os.environ.copy()
    run_env["CDS_DONT_SHOW_CEIP"] = "1"
    run_env["CDS_DISABLE_CEIP"] = "1"
    run_env["CDS_DISABLE_STARTPAGE"] = "1"

    try:
      self.active_process = subprocess.Popen(
          cmd,
          cwd=self.workspace_dir,
          env=run_env,
          stdout=subprocess.PIPE,
          stderr=subprocess.STDOUT,
          text=True,
      )
    except Exception as e:
      err_msg = f"[Process Runner 异常] 无法启动进程: {str(e)}"
      log_callback(err_msg)
      return -1, err_msg

    watchdog = DialogKillerWatchdog(self.active_process.pid, log_callback)
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

        ret = self.active_process.poll()
        if ret is not None:
          break

        if done_flag_path and os.path.exists(done_flag_path):
          time.sleep(0.3)
          if self.active_process.poll() is None:
            self.kill_active_task()
          ret = 0
          break

        if time.time() - start_time > timeout:
          log_callback("[Timeout] 运行超时，正在中止...")
          self.kill_active_task()
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
      self.active_process = None

  def kill_active_task(self):
    if self.active_process and self.active_process.poll() is None:
      if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(self.active_process.pid)],
            capture_output=True,
        )
      else:
        self.active_process.kill()
