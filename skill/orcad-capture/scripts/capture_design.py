# -*- coding: utf-8 -*-
"""Capture Skill-owned OPJ open and Dbo read-only inspection workflows.

All remote interaction uses the generic AirBridge Shell and the M3.2.4 Tcl
request/result queue. This module never saves, edits, removes or closes a design.
"""
from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import uuid
import ntpath
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping, Optional

from capture_requests import (
    CaptureTclOutcomeUnknown,
    CaptureTclQueueError,
    CaptureTclResult,
)
from capture_session import (
    CaptureAuthorizationError,
    CaptureIdentityError,
    CaptureSession,
    CaptureStateTransitionError,
    SessionState,
)


ACTIVE_MAGIC = "AB_CAPTURE_ACTIVE_DESIGN_V1"
INVENTORY_MAGIC = "AB_CAPTURE_DBO_INVENTORY_V1"
OPEN_MAGIC = "AB_CAPTURE_OPEN_RESULT_V1"


class CaptureDesignError(RuntimeError):
    """A Capture Skill design workflow failed safely."""


class CaptureDesignOutcomeUnknown(CaptureDesignError):
    """The OPJ open request may have taken effect and must not be repeated."""

    def __init__(self, message: str, request_id: str):
        self.request_id = request_id
        super().__init__(message)


class CaptureDboProbeError(CaptureDesignError):
    """The read-only Dbo API probe returned incomplete or invalid evidence."""


@dataclass(frozen=True)
class CaptureActiveDesign:
    session_present: bool
    session_wrapper_rc: int
    active_design_rc: Optional[int]
    active_design_present: bool
    active_design_name: Optional[str]
    active_design_name_rc: Optional[int]
    project_manager_rc: Optional[int]
    project_manager_present: bool
    project_manager_name: Optional[str]
    project_manager_name_rc: Optional[int]
    same_design_object: Optional[bool]
    tcl_pid: int
    tcl_version: str

    @property
    def verified(self) -> bool:
        return (
            self.session_present
            and self.session_wrapper_rc == 0
            and self.active_design_rc == 0
            and self.active_design_present
            and bool(self.active_design_name)
            and self.project_manager_rc == 0
            and self.project_manager_present
            and self.same_design_object is True
        )


@dataclass(frozen=True)
class CaptureDboInventory:
    session_present: bool
    session_wrapper_rc: int
    dbo_state_create_rc: Optional[int]
    design_names: tuple[str, ...]
    library_names: tuple[str, ...]
    design_iter_create_rc: Optional[int]
    design_next_rc: Optional[int]
    design_end_is_null: Optional[bool]
    design_iter_release_rc: Optional[int]
    lib_iter_create_rc: Optional[int]
    lib_next_rc: Optional[int]
    lib_end_is_null: Optional[bool]
    lib_iter_release_rc: Optional[int]
    dbo_state_release_rc: Optional[int]
    tcl_pid: int
    tcl_version: str

    @property
    def resources_released(self) -> bool:
        release_codes = (
            self.design_iter_release_rc,
            self.lib_iter_release_rc,
            self.dbo_state_release_rc,
        )
        return all(code == 0 for code in release_codes if code is not None)

    @property
    def complete(self) -> bool:
        return (
            self.session_present
            and self.session_wrapper_rc == 0
            and self.dbo_state_create_rc == 0
            and self.design_iter_create_rc == 0
            and self.design_next_rc == 0
            and self.design_end_is_null is True
            and self.design_iter_release_rc == 0
            and self.lib_iter_create_rc == 0
            and self.lib_next_rc == 0
            and self.lib_end_is_null is True
            and self.lib_iter_release_rc == 0
            and self.dbo_state_release_rc == 0
        )


@dataclass(frozen=True)
class CaptureProjectOpenResult:
    request_id: str
    opj_path: str
    active_design_name: str
    project_manager_name: str
    same_design_object: bool
    open_result: str
    tcl_pid: int
    tcl_version: str


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _b64_utf8(value: str) -> str:
    return base64.b64encode(str(value).encode("utf-8")).decode("ascii")


def _decode_b64_utf8(value: str, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9+/]*={0,2}", value):
        raise CaptureDesignError("{} is not valid Base64".format(field))
    if len(value) % 4:
        raise CaptureDesignError("{} Base64 length is invalid".format(field))
    try:
        return base64.b64decode(value.encode("ascii"), validate=True).decode("utf-8")
    except (ValueError, UnicodeEncodeError, UnicodeDecodeError) as exc:
        raise CaptureDesignError("{} is not valid Base64 UTF-8".format(field)) from exc


def _parse_report(value: str, magic: str) -> dict[str, list[str]]:
    if not isinstance(value, str):
        raise CaptureDboProbeError("Tcl report must be a string")
    lines = value.splitlines()
    if not lines or lines[0] != magic:
        raise CaptureDboProbeError("Tcl report header does not match {}".format(magic))
    fields: dict[str, list[str]] = {}
    for line in lines[1:]:
        if "=" not in line:
            raise CaptureDboProbeError("Tcl report contains a malformed field")
        key, item = line.split("=", 1)
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            raise CaptureDboProbeError("Tcl report contains an invalid field name")
        fields.setdefault(key, []).append(item)
    return fields


def _one(fields: Mapping[str, list[str]], key: str, *, required: bool = True) -> Optional[str]:
    values = fields.get(key, [])
    if not values:
        if required:
            raise CaptureDboProbeError("Tcl report is missing {}".format(key))
        return None
    if len(values) != 1:
        raise CaptureDboProbeError("Tcl report repeats scalar field {}".format(key))
    return values[0]


def _int_field(fields: Mapping[str, list[str]], key: str, *, required: bool = True) -> Optional[int]:
    value = _one(fields, key, required=required)
    if value is None:
        return None
    if not re.fullmatch(r"-?[0-9]+", value):
        raise CaptureDboProbeError("Tcl report field {} is not an integer".format(key))
    return int(value)


def _bool_field(fields: Mapping[str, list[str]], key: str, *, required: bool = True) -> Optional[bool]:
    value = _one(fields, key, required=required)
    if value is None:
        return None
    if value not in ("0", "1"):
        raise CaptureDboProbeError("Tcl report field {} is not a Tcl boolean".format(key))
    return value == "1"


def build_active_design_probe_script() -> str:
    """Inspect the current Session and active design without opening anything."""
    return r"""
set __lines [list "AB_CAPTURE_ACTIVE_DESIGN_V1" "tcl_pid=[pid]" "tcl_version=[info patchlevel]"]
set __session_exists [info exists ::DboSession_s_pDboSession]
lappend __lines "session_present=$__session_exists"
set __session_wrapper_rc -1
set __active_rc -1
set __active_present 0
set __active_name_rc -1
set __active_name ""
set __pm_rc -1
set __pm_present 0
set __pm_name_rc -1
set __pm_name ""
set __same 0
if {$__session_exists && $::DboSession_s_pDboSession ne "" && $::DboSession_s_pDboSession ne "NULL"} {
    set __session $::DboSession_s_pDboSession
    set __session_wrapper_rc [catch {DboSession -this $__session} __wrapper_value __wrapper_options]
    if {$__session_wrapper_rc == 0} {
        set __active_rc [catch {set __active_design [$__session GetActiveDesign]} __active_value __active_options]
        if {$__active_rc == 0 && $__active_design ne "" && $__active_design ne "NULL"} {
            set __active_present 1
            set __active_name_rc [catch {
                set __active_name_obj [DboTclHelper_sMakeCString]
                $__active_design GetName $__active_name_obj
                set __active_name [DboTclHelper_sGetConstCharPtr $__active_name_obj]
            } __active_name_value __active_name_options]
        }
        # GetActivePMDesign is only queried when a Dbo Active Design exists.
        # Start Page DBO-V01 evidence covers GetActiveDesign returning NULL;
        # avoid calling additional object APIs in that no-design context.
        if {$__active_present} {
            set __pm_rc [catch {set __pm_design [GetActivePMDesign]} __pm_value __pm_options]
            if {$__pm_rc == 0 && $__pm_design ne "" && $__pm_design ne "NULL"} {
                set __pm_present 1
                set __pm_name_rc [catch {
                    set __pm_name_obj [DboTclHelper_sMakeCString]
                    $__pm_design GetName $__pm_name_obj
                    set __pm_name [DboTclHelper_sGetConstCharPtr $__pm_name_obj]
                } __pm_name_value __pm_name_options]
            }
            if {$__pm_present} {
                set __same [expr {$__active_design eq $__pm_design}]
            }
        } else {
            set __pm_rc 0
        }
    }
}
lappend __lines "session_wrapper_rc=$__session_wrapper_rc"
lappend __lines "active_design_rc=$__active_rc"
lappend __lines "active_design_present=$__active_present"
lappend __lines "active_design_name_rc=$__active_name_rc"
lappend __lines "active_design_name_b64=[binary encode base64 -maxlen 0 [encoding convertto utf-8 $__active_name]]"
lappend __lines "project_manager_rc=$__pm_rc"
lappend __lines "project_manager_present=$__pm_present"
lappend __lines "project_manager_name_rc=$__pm_name_rc"
lappend __lines "project_manager_name_b64=[binary encode base64 -maxlen 0 [encoding convertto utf-8 $__pm_name]]"
lappend __lines "same_design_object=$__same"
join $__lines "
"
""".strip()


def parse_active_design_report(value: str) -> CaptureActiveDesign:
    fields = _parse_report(value, ACTIVE_MAGIC)
    active_name_b64 = _one(fields, "active_design_name_b64")
    pm_name_b64 = _one(fields, "project_manager_name_b64")
    return CaptureActiveDesign(
        session_present=bool(_bool_field(fields, "session_present")),
        session_wrapper_rc=int(_int_field(fields, "session_wrapper_rc")),
        active_design_rc=_int_field(fields, "active_design_rc"),
        active_design_present=bool(_bool_field(fields, "active_design_present")),
        active_design_name=(
            _decode_b64_utf8(active_name_b64, "active_design_name")
            if active_name_b64
            else None
        ),
        active_design_name_rc=_int_field(fields, "active_design_name_rc"),
        project_manager_rc=_int_field(fields, "project_manager_rc"),
        project_manager_present=bool(_bool_field(fields, "project_manager_present")),
        project_manager_name=(
            _decode_b64_utf8(pm_name_b64, "project_manager_name")
            if pm_name_b64
            else None
        ),
        project_manager_name_rc=_int_field(fields, "project_manager_name_rc"),
        same_design_object=bool(_bool_field(fields, "same_design_object")),
        tcl_pid=int(_int_field(fields, "tcl_pid")),
        tcl_version=str(_one(fields, "tcl_version")),
    )


def build_dbo_session_inventory_script() -> str:
    """Enumerate only Designs and Libraries already loaded in the current Session."""
    return r"""
set __lines [list "AB_CAPTURE_DBO_INVENTORY_V1" "tcl_pid=[pid]" "tcl_version=[info patchlevel]"]
set __session_exists [info exists ::DboSession_s_pDboSession]
lappend __lines "session_present=$__session_exists"
set __wrapper_rc -1
set __state_rc -1
set __design_iter_rc -1
set __design_next_rc -1
set __design_end_is_null 0
set __design_release_rc -1
set __lib_iter_rc -1
set __lib_next_rc -1
set __lib_end_is_null 0
set __lib_release_rc -1
set __state_release_rc -1
set __design_count 0
set __lib_count 0
if {$__session_exists && $::DboSession_s_pDboSession ne "" && $::DboSession_s_pDboSession ne "NULL"} {
    set __session $::DboSession_s_pDboSession
    set __wrapper_rc [catch {DboSession -this $__session} __wrapper_value __wrapper_options]
    if {$__wrapper_rc == 0} {
        set __state_rc [catch {set __dbo_state [DboState]} __state_value __state_options]
        if {$__state_rc == 0} {
            set __null_obj NULL

            set __design_iter ""
            set __design_iter_rc [catch {
                set __design_iter [$__session NewDesignsIter $__dbo_state]
            } __design_iter_value __design_iter_options]
            if {$__design_iter_rc == 0 && $__design_iter ne "" && $__design_iter ne "NULL"} {
                set __design_loop_rc [catch {
                    set __design_next_rc [catch {
                        set __design [$__design_iter NextDesign $__dbo_state]
                    } __design_value __design_options]
                    while {$__design_next_rc == 0 && $__design ne $__null_obj && $__design ne ""} {
                        incr __design_count
                        set __name_rc [catch {
                            set __name_obj [DboTclHelper_sMakeCString]
                            $__design GetName $__name_obj
                            set __name [DboTclHelper_sGetConstCharPtr $__name_obj]
                        } __name_value __name_options]
                        if {$__name_rc == 0} {
                            lappend __lines "design_name_b64=[binary encode base64 -maxlen 0 [encoding convertto utf-8 $__name]]"
                        } else {
                            lappend __lines "design_name_error_b64=[binary encode base64 -maxlen 0 [encoding convertto utf-8 $__name_value]]"
                        }
                        set __design_next_rc [catch {
                            set __design [$__design_iter NextDesign $__dbo_state]
                        } __design_value __design_options]
                    }
                    if {$__design_next_rc == 0} {
                        set __design_end_is_null [expr {$__design eq $__null_obj || $__design eq ""}]
                    }
                } __design_loop_value __design_loop_options]
                if {$__design_loop_rc != 0} {
                    lappend __lines "design_loop_error_b64=[binary encode base64 -maxlen 0 [encoding convertto utf-8 $__design_loop_value]]"
                }
                set __design_release_rc [catch {
                    delete_DboSessionDesignsIter $__design_iter
                } __design_release_value __design_release_options]
            } elseif {$__design_iter_rc == 0} {
                set __design_end_is_null 1
            }

            set __lib_iter ""
            set __lib_iter_rc [catch {
                set __lib_iter [$__session NewLibsIter $__dbo_state]
            } __lib_iter_value __lib_iter_options]
            if {$__lib_iter_rc == 0 && $__lib_iter ne "" && $__lib_iter ne "NULL"} {
                set __lib_loop_rc [catch {
                    set __lib_next_rc [catch {
                        set __lib [$__lib_iter NextLib $__dbo_state]
                    } __lib_value __lib_options]
                    while {$__lib_next_rc == 0 && $__lib ne $__null_obj && $__lib ne ""} {
                        incr __lib_count
                        set __name_rc [catch {
                            set __name_obj [DboTclHelper_sMakeCString]
                            $__lib GetName $__name_obj
                            set __name [DboTclHelper_sGetConstCharPtr $__name_obj]
                        } __name_value __name_options]
                        if {$__name_rc == 0} {
                            lappend __lines "library_name_b64=[binary encode base64 -maxlen 0 [encoding convertto utf-8 $__name]]"
                        } else {
                            lappend __lines "library_name_error_b64=[binary encode base64 -maxlen 0 [encoding convertto utf-8 $__name_value]]"
                        }
                        set __lib_next_rc [catch {
                            set __lib [$__lib_iter NextLib $__dbo_state]
                        } __lib_value __lib_options]
                    }
                    if {$__lib_next_rc == 0} {
                        set __lib_end_is_null [expr {$__lib eq $__null_obj || $__lib eq ""}]
                    }
                } __lib_loop_value __lib_loop_options]
                if {$__lib_loop_rc != 0} {
                    lappend __lines "library_loop_error_b64=[binary encode base64 -maxlen 0 [encoding convertto utf-8 $__lib_loop_value]]"
                }
                set __lib_release_rc [catch {
                    delete_DboSessionLibsIter $__lib_iter
                } __lib_release_value __lib_release_options]
            } elseif {$__lib_iter_rc == 0} {
                set __lib_end_is_null 1
            }

            set __state_release_rc [catch {$__dbo_state -delete} __state_release_value __state_release_options]
        }
    }
}
lappend __lines "session_wrapper_rc=$__wrapper_rc"
lappend __lines "dbo_state_create_rc=$__state_rc"
lappend __lines "design_iter_create_rc=$__design_iter_rc"
lappend __lines "design_next_rc=$__design_next_rc"
lappend __lines "design_end_is_null=$__design_end_is_null"
lappend __lines "design_iter_release_rc=$__design_release_rc"
lappend __lines "design_count=$__design_count"
lappend __lines "lib_iter_create_rc=$__lib_iter_rc"
lappend __lines "lib_next_rc=$__lib_next_rc"
lappend __lines "lib_end_is_null=$__lib_end_is_null"
lappend __lines "lib_iter_release_rc=$__lib_release_rc"
lappend __lines "library_count=$__lib_count"
lappend __lines "dbo_state_release_rc=$__state_release_rc"
join $__lines "
"
""".strip()


def parse_dbo_session_inventory(value: str) -> CaptureDboInventory:
    fields = _parse_report(value, INVENTORY_MAGIC)
    if any(
        fields.get(key)
        for key in (
            "design_name_error_b64",
            "library_name_error_b64",
            "design_loop_error_b64",
            "library_loop_error_b64",
        )
    ):
        raise CaptureDboProbeError("Dbo iterator encountered an object-name or traversal error")
    design_names = tuple(
        _decode_b64_utf8(item, "design_name")
        for item in fields.get("design_name_b64", [])
    )
    library_names = tuple(
        _decode_b64_utf8(item, "library_name")
        for item in fields.get("library_name_b64", [])
    )
    design_count = int(_int_field(fields, "design_count"))
    library_count = int(_int_field(fields, "library_count"))
    if design_count != len(design_names) or library_count != len(library_names):
        raise CaptureDboProbeError("Dbo iterator counts do not match the returned names")
    result = CaptureDboInventory(
        session_present=bool(_bool_field(fields, "session_present")),
        session_wrapper_rc=int(_int_field(fields, "session_wrapper_rc")),
        dbo_state_create_rc=_int_field(fields, "dbo_state_create_rc"),
        design_names=design_names,
        library_names=library_names,
        design_iter_create_rc=_int_field(fields, "design_iter_create_rc"),
        design_next_rc=_int_field(fields, "design_next_rc"),
        design_end_is_null=_bool_field(fields, "design_end_is_null"),
        design_iter_release_rc=_int_field(fields, "design_iter_release_rc"),
        lib_iter_create_rc=_int_field(fields, "lib_iter_create_rc"),
        lib_next_rc=_int_field(fields, "lib_next_rc"),
        lib_end_is_null=_bool_field(fields, "lib_end_is_null"),
        lib_iter_release_rc=_int_field(fields, "lib_iter_release_rc"),
        dbo_state_release_rc=_int_field(fields, "dbo_state_release_rc"),
        tcl_pid=int(_int_field(fields, "tcl_pid")),
        tcl_version=str(_one(fields, "tcl_version")),
    )
    if len(result.design_names) > 10000 or len(result.library_names) > 10000:
        raise CaptureDboProbeError("Dbo inventory returned an implausible object count")
    return result


def build_open_project_script(opj_path: str) -> str:
    """Open one explicitly selected OPJ and return Dbo/Project Manager identity."""
    validated = validate_remote_opj_path(opj_path)
    path_b64 = _b64_utf8(validated)
    return (r"""
set __path [encoding convertfrom utf-8 [binary decode base64 {%s}]]
set __open_rc [catch {Open $__path} __open_value __open_options]
set __open_value_b64 [binary encode base64 -maxlen 0 [encoding convertto utf-8 $__open_value]]
set __lines [list "AB_CAPTURE_OPEN_RESULT_V1" "requested_path_b64=%s" "open_rc=$__open_rc" "open_result_b64=$__open_value_b64" "tcl_pid=[pid]" "tcl_version=[info patchlevel]"]
set __session_exists [info exists ::DboSession_s_pDboSession]
lappend __lines "session_present=$__session_exists"
set __wrapper_rc -1
set __active_rc -1
set __active_present 0
set __active_name_rc -1
set __active_name ""
set __pm_rc -1
set __pm_present 0
set __pm_name_rc -1
set __pm_name ""
set __same 0
if {$__session_exists && $::DboSession_s_pDboSession ne "" && $::DboSession_s_pDboSession ne "NULL"} {
    set __session $::DboSession_s_pDboSession
    set __wrapper_rc [catch {DboSession -this $__session} __wrapper_value __wrapper_options]
    if {$__wrapper_rc == 0} {
        set __active_rc [catch {set __active_design [$__session GetActiveDesign]} __active_value __active_options]
        if {$__active_rc == 0 && $__active_design ne "" && $__active_design ne "NULL"} {
            set __active_present 1
            set __active_name_rc [catch {
                set __active_name_obj [DboTclHelper_sMakeCString]
                $__active_design GetName $__active_name_obj
                set __active_name [DboTclHelper_sGetConstCharPtr $__active_name_obj]
            } __active_name_value __active_name_options]
        }
        if {$__active_present} {
            set __pm_rc [catch {set __pm_design [GetActivePMDesign]} __pm_value __pm_options]
            if {$__pm_rc == 0 && $__pm_design ne "" && $__pm_design ne "NULL"} {
                set __pm_present 1
                set __pm_name_rc [catch {
                    set __pm_name_obj [DboTclHelper_sMakeCString]
                    $__pm_design GetName $__pm_name_obj
                    set __pm_name [DboTclHelper_sGetConstCharPtr $__pm_name_obj]
                } __pm_name_value __pm_name_options]
            }
            if {$__pm_present} {
                set __same [expr {$__active_design eq $__pm_design}]
            }
        } else {
            set __pm_rc 0
        }
    }
}
lappend __lines "session_wrapper_rc=$__wrapper_rc"
lappend __lines "active_design_rc=$__active_rc"
lappend __lines "active_design_present=$__active_present"
lappend __lines "active_design_name_rc=$__active_name_rc"
lappend __lines "active_design_name_b64=[binary encode base64 -maxlen 0 [encoding convertto utf-8 $__active_name]]"
lappend __lines "project_manager_rc=$__pm_rc"
lappend __lines "project_manager_present=$__pm_present"
lappend __lines "project_manager_name_rc=$__pm_name_rc"
lappend __lines "project_manager_name_b64=[binary encode base64 -maxlen 0 [encoding convertto utf-8 $__pm_name]]"
lappend __lines "same_design_object=$__same"
join $__lines "
"
""" % (path_b64, path_b64)
    ).strip()


def parse_open_project_report(value: str) -> tuple[CaptureActiveDesign, int, str, str]:
    fields = _parse_report(value, OPEN_MAGIC)
    active_value = "\n".join(
        [
            ACTIVE_MAGIC,
            "tcl_pid=" + str(_one(fields, "tcl_pid")),
            "tcl_version=" + str(_one(fields, "tcl_version")),
            "session_present=" + str(_one(fields, "session_present")),
            "session_wrapper_rc=" + str(_one(fields, "session_wrapper_rc")),
            "active_design_rc=" + str(_one(fields, "active_design_rc")),
            "active_design_present=" + str(_one(fields, "active_design_present")),
            "active_design_name_rc=" + str(_one(fields, "active_design_name_rc")),
            "active_design_name_b64=" + str(_one(fields, "active_design_name_b64")),
            "project_manager_rc=" + str(_one(fields, "project_manager_rc")),
            "project_manager_present=" + str(_one(fields, "project_manager_present")),
            "project_manager_name_rc=" + str(_one(fields, "project_manager_name_rc")),
            "project_manager_name_b64=" + str(_one(fields, "project_manager_name_b64")),
            "same_design_object=" + str(_one(fields, "same_design_object")),
        ]
    )
    active = parse_active_design_report(active_value)
    open_rc = int(_int_field(fields, "open_rc"))
    open_result = _decode_b64_utf8(str(_one(fields, "open_result_b64")), "open_result")
    requested_path = _decode_b64_utf8(str(_one(fields, "requested_path_b64")), "requested_path")
    return active, open_rc, open_result, requested_path


def validate_remote_opj_path(value: str) -> str:
    raw = str(value or "").strip()
    if not raw or "\x00" in raw or len(raw) > 32767:
        raise CaptureDesignError("OPJ path is empty or invalid")
    path = PureWindowsPath(raw)
    if not path.is_absolute():
        raise CaptureDesignError("OPJ path must be an absolute Host-B Windows path")
    if path.suffix.casefold() != ".opj":
        raise CaptureDesignError("only a Capture .opj project path is supported in this phase")
    return str(path)


def build_remote_opj_preflight_command(opj_path: str) -> str:
    validated = validate_remote_opj_path(opj_path)
    path_b64 = _b64_utf8(validated)
    return (
        "$ErrorActionPreference = 'Stop'; "
        "$path = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"
        + path_b64
        + "')); "
        "$item = Get-Item -LiteralPath $path -ErrorAction Stop; "
        "if ($item.PSIsContainer) { throw 'OPJ path refers to a directory' }; "
        "if ($item.Extension -ine '.opj') { throw 'OPJ extension mismatch' }; "
        "if ($item.Length -le 0) { throw 'OPJ file is empty' }; "
        "[pscustomobject]@{exists=$true; path=$item.FullName; length=[int64]$item.Length; "
        "last_write_utc=$item.LastWriteTimeUtc.ToString('o')} | ConvertTo-Json -Compress"
    )


def _normal_windows_path(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(str(value)))


class CaptureDesignCoordinator:
    """Own OPJ opening and Dbo read-only probes for a single Capture Session."""

    def __init__(
        self,
        client: Any,
        queue: Any,
        session: CaptureSession,
        manifest_path: str | Path,
    ):
        if client is None or not callable(getattr(client, "shell_execute", None)):
            raise TypeError("client must provide shell_execute()")
        if queue is None or not callable(getattr(queue, "execute", None)):
            raise TypeError("queue must provide execute()")
        if not isinstance(session, CaptureSession):
            raise TypeError("session must be a CaptureSession")
        if not str(manifest_path or "").strip():
            raise ValueError("manifest_path is required")
        self.client = client
        self.queue = queue
        self.session = session
        self.manifest_path = Path(manifest_path)
        self.context_path = self.manifest_path.with_name(
            self.manifest_path.name + ".design.json"
        )

    def inspect_active_design(
        self, *, user_authorized: bool = False, timeout_seconds: float = 60.0
    ) -> CaptureActiveDesign:
        self._require_ready()
        self._require_authorized(user_authorized)
        result = self.queue.execute(
            build_active_design_probe_script(),
            user_authorized=True,
            timeout_seconds=timeout_seconds,
        )
        if not result.success:
            raise CaptureDboProbeError(
                "Capture active-design probe failed: {}".format(result.error_info)
            )
        report = parse_active_design_report(result.value)
        if not report.session_present or report.session_wrapper_rc != 0:
            raise CaptureDboProbeError("Capture Dbo Session is unavailable or failed to bind")
        if report.tcl_pid != self.session.manifest.pid:
            raise CaptureIdentityError("Tcl PID does not match the owned Capture process")
        if self.session.manifest.tcl_version and report.tcl_version != self.session.manifest.tcl_version:
            raise CaptureIdentityError("Tcl version does not match the READY Session evidence")
        if report.active_design_rc != 0:
            raise CaptureDboProbeError("DboSession.GetActiveDesign failed")
        if report.project_manager_rc != 0:
            raise CaptureDboProbeError("GetActivePMDesign failed")
        if report.active_design_present and report.active_design_name_rc != 0:
            raise CaptureDboProbeError("active Dbo Design name could not be read")
        if report.project_manager_present and report.project_manager_name_rc != 0:
            raise CaptureDboProbeError("Project Manager Design name could not be read")
        return report

    def inspect_session_objects(
        self, *, user_authorized: bool = False, timeout_seconds: float = 60.0
    ) -> CaptureDboInventory:
        self._require_ready()
        self._require_authorized(user_authorized)
        result = self.queue.execute(
            build_dbo_session_inventory_script(),
            user_authorized=True,
            timeout_seconds=timeout_seconds,
        )
        if not result.success:
            raise CaptureDboProbeError(
                "Capture Dbo Session inventory failed: {}".format(result.error_info)
            )
        inventory = parse_dbo_session_inventory(result.value)
        if not inventory.session_present or inventory.session_wrapper_rc != 0:
            raise CaptureDboProbeError("Capture Dbo Session is unavailable or failed to bind")
        if inventory.tcl_pid != self.session.manifest.pid:
            raise CaptureIdentityError("Tcl PID does not match the owned Capture process")
        if self.session.manifest.tcl_version and inventory.tcl_version != self.session.manifest.tcl_version:
            raise CaptureIdentityError("Tcl version does not match the READY Session evidence")
        if not inventory.complete:
            raise CaptureDboProbeError(
                "Capture Dbo inventory is incomplete; inspect iterator and cleanup return codes"
            )
        return inventory

    def open_project(
        self,
        opj_path: str,
        *,
        user_authorized: bool = False,
        timeout_seconds: float = 120.0,
    ) -> CaptureProjectOpenResult:
        self._require_ready()
        self._require_authorized(user_authorized)
        validated = validate_remote_opj_path(opj_path)
        if self.context_path.exists():
            existing = self._load_context()
            raise CaptureDesignError(
                "this Capture Session already has a design context (state={}); do not open another project"
                .format(existing["state"])
            )

        active_before = self.inspect_active_design(
            user_authorized=True, timeout_seconds=timeout_seconds
        )
        if active_before.active_design_present:
            raise CaptureDesignError(
                "Capture already has an active Design; this Skill will not switch or adopt it"
            )
        # Keep the read-only Host-B filesystem check immediately before the
        # authorized Tcl Open request; no OPJ bytes are transferred to Host A.
        preflight = self._remote_preflight(validated)

        request_id = str(uuid.uuid4())
        context = {
            "schema_version": 1,
            "session_id": self.session.manifest.session_id,
            "request_id": request_id,
            "opj_path": validated,
            "remote_file_length": preflight["length"],
            "remote_last_write_utc": preflight["last_write_utc"],
            "state": "opening",
            "active_design_name": None,
            "created_at_utc": _utc_now(),
            "updated_at_utc": _utc_now(),
        }
        self._create_context(context)
        try:
            result = self.queue.execute(
                build_open_project_script(validated),
                user_authorized=True,
                request_id=request_id,
                timeout_seconds=timeout_seconds,
            )
        except CaptureTclOutcomeUnknown as exc:
            context["state"] = "unknown"
            context["updated_at_utc"] = _utc_now()
            context["error"] = str(exc)[:512]
            self._save_context(context)
            raise CaptureDesignOutcomeUnknown(
                "OPJ open outcome is UNKNOWN; do not issue Open again. Reconcile Request ID {}".format(request_id),
                request_id,
            ) from exc
        except Exception as exc:
            context["state"] = "failed"
            context["updated_at_utc"] = _utc_now()
            context["error"] = str(exc)[:512]
            self._save_context(context)
            raise

        if not result.success:
            context["state"] = "open_failed"
            context["updated_at_utc"] = _utc_now()
            context["error"] = (result.error_info or result.value)[:512]
            self._save_context(context)
            raise CaptureDesignError("Capture Tcl Open command failed: {}".format(context["error"]))

        try:
            active, open_rc, open_result, returned_path = parse_open_project_report(result.value)
        except Exception as exc:
            context["state"] = "verification_failed"
            context["updated_at_utc"] = _utc_now()
            context["error"] = "Open returned but its Design identity report was invalid"
            self._save_context(context)
            raise CaptureDesignOutcomeUnknown(
                "Capture returned from Open but active Design identity could not be verified; do not repeat Open",
                request_id,
            ) from exc

        if _normal_windows_path(returned_path) != _normal_windows_path(validated):
            context["state"] = "verification_failed"
            context["updated_at_utc"] = _utc_now()
            context["error"] = "Tcl report returned a different OPJ path"
            self._save_context(context)
            raise CaptureDesignOutcomeUnknown(
                "Capture Open report path did not match the requested OPJ; do not repeat Open",
                request_id,
            )

        runtime_identity_matches = (
            active.tcl_pid == self.session.manifest.pid
            and (
                not self.session.manifest.tcl_version
                or active.tcl_version == self.session.manifest.tcl_version
            )
        )
        if (
            open_rc != 0
            or not active.verified
            or not runtime_identity_matches
        ):
            context["state"] = "verification_failed" if open_rc == 0 else "open_failed"
            context["updated_at_utc"] = _utc_now()
            context["active_design_name"] = active.active_design_name
            context["error"] = (
                "Open return code={}, active design present={}, PM/Dbo object match={}, Tcl PID match={}"
                .format(open_rc, active.active_design_present, active.same_design_object, runtime_identity_matches)
            )
            self._save_context(context)
            raise CaptureDesignError(
                "Capture OPJ open did not pass active Design verification: {}".format(context["error"])
            )

        context["state"] = "opened"
        context["active_design_name"] = active.active_design_name
        context["project_manager_name"] = active.project_manager_name
        context["tcl_pid"] = active.tcl_pid
        context["tcl_version"] = active.tcl_version
        context["same_design_object"] = active.same_design_object
        context["open_result"] = open_result[:512]
        context["updated_at_utc"] = _utc_now()
        self._save_context(context)
        return CaptureProjectOpenResult(
            request_id=request_id,
            opj_path=validated,
            active_design_name=str(active.active_design_name),
            project_manager_name=str(active.project_manager_name),
            same_design_object=bool(active.same_design_object),
            open_result=open_result,
            tcl_pid=active.tcl_pid,
            tcl_version=active.tcl_version,
        )

    def reconcile_open_project(
        self, request_id: str
    ) -> Optional[CaptureProjectOpenResult]:
        """Resolve an indeterminate Open only by its original Tcl Request ID."""
        context = self._load_context()
        if context["state"] != "unknown" or context["request_id"] != str(uuid.UUID(request_id)):
            raise CaptureStateTransitionError(
                "no matching UNKNOWN OPJ open request is available for reconciliation"
            )
        if not callable(getattr(self.queue, "reconcile", None)):
            raise CaptureDesignError("Tcl queue does not support read-only reconciliation")
        result = self.queue.reconcile(request_id)
        if result is None:
            return None
        if not result.success:
            context["state"] = "open_failed"
            context["error"] = (result.error_info or result.value)[:512]
            context["updated_at_utc"] = _utc_now()
            self._save_context(context)
            return None
        try:
            active, open_rc, open_result, returned_path = parse_open_project_report(result.value)
        except Exception as exc:
            context["state"] = "verification_failed"
            context["error"] = "original Open result could not be parsed"
            context["updated_at_utc"] = _utc_now()
            self._save_context(context)
            raise CaptureDesignOutcomeUnknown(
                "original Open result is not verifiable; do not repeat Open",
                request_id,
            ) from exc
        runtime_identity_matches = (
            active.tcl_pid == self.session.manifest.pid
            and (
                not self.session.manifest.tcl_version
                or active.tcl_version == self.session.manifest.tcl_version
            )
        )
        if (
            _normal_windows_path(returned_path) != _normal_windows_path(context["opj_path"])
            or open_rc != 0
            or not active.verified
            or not runtime_identity_matches
        ):
            context["state"] = "verification_failed"
            context["active_design_name"] = active.active_design_name
            context["error"] = "original Open result did not prove the expected active Design"
            context["updated_at_utc"] = _utc_now()
            self._save_context(context)
            return None
        context["state"] = "opened"
        context["active_design_name"] = active.active_design_name
        context["project_manager_name"] = active.project_manager_name
        context["tcl_pid"] = active.tcl_pid
        context["tcl_version"] = active.tcl_version
        context["same_design_object"] = active.same_design_object
        context["open_result"] = open_result[:512]
        context["updated_at_utc"] = _utc_now()
        self._save_context(context)
        return CaptureProjectOpenResult(
            request_id=request_id,
            opj_path=context["opj_path"],
            active_design_name=str(active.active_design_name),
            project_manager_name=str(active.project_manager_name),
            same_design_object=bool(active.same_design_object),
            open_result=open_result,
            tcl_pid=active.tcl_pid,
            tcl_version=active.tcl_version,
        )

    def _remote_preflight(self, opj_path: str) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        response = self.client.shell_execute(
            build_remote_opj_preflight_command(opj_path),
            shell="powershell",
            task_timeout_seconds=self.session.config.shell_timeout_seconds if self.session.config else 60,
            async_mode=False,
            request_id=request_id,
        )
        if not isinstance(response, Mapping):
            raise CaptureDesignError("Host-B OPJ preflight returned a non-object response")
        if response.get("request_id") != request_id or response.get("state") != "completed":
            raise CaptureDesignError("Host-B OPJ preflight was not confirmed complete")
        result = response.get("result")
        if (
            not isinstance(result, Mapping)
            or result.get("success") is not True
            or result.get("exit_code") != 0
        ):
            raise CaptureDesignError("Host-B OPJ file preflight failed")
        try:
            evidence = json.loads(str(result.get("stdout") or ""))
        except json.JSONDecodeError as exc:
            raise CaptureDesignError("Host-B OPJ preflight JSON is invalid") from exc
        if (
            not isinstance(evidence, Mapping)
            or evidence.get("exists") is not True
            or not isinstance(evidence.get("path"), str)
            or _normal_windows_path(evidence["path"]) != _normal_windows_path(opj_path)
            or isinstance(evidence.get("length"), bool)
            or not isinstance(evidence.get("length"), int)
            or evidence["length"] <= 0
            or not isinstance(evidence.get("last_write_utc"), str)
            or not evidence["last_write_utc"]
        ):
            raise CaptureDesignError("Host-B OPJ preflight evidence does not match the requested path")
        return dict(evidence)

    def _require_ready(self) -> None:
        if self.session.state is not SessionState.READY:
            raise CaptureStateTransitionError("Capture design operation requires a READY Session")
        if self.session.manifest.reconciliation_required or self.session.manifest.active_request_id:
            raise CaptureStateTransitionError("resolve the existing UNKNOWN request before design operations")

    @staticmethod
    def _require_authorized(user_authorized: bool) -> None:
        if user_authorized is not True:
            raise CaptureAuthorizationError(
                "running a Tcl operation or opening a Capture design requires explicit authorization"
            )

    def _create_context(self, value: Mapping[str, Any]) -> None:
        self.context_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.context_path.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(dict(value), stream, ensure_ascii=True, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError as exc:
            raise CaptureDesignError(
                "a Capture Design context already exists; refusing to replace it"
            ) from exc

    def _save_context(self, value: Mapping[str, Any]) -> None:
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=str(self.context_path.parent),
                prefix=self.context_path.name + ".",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temp_name = stream.name
                json.dump(dict(value), stream, ensure_ascii=True, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.context_path)
        finally:
            if temp_name and os.path.exists(temp_name):
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass

    def _load_context(self) -> dict[str, Any]:
        try:
            value = json.loads(self.context_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise CaptureDesignError("Capture Design context is unavailable") from exc
        except json.JSONDecodeError as exc:
            raise CaptureDesignError("Capture Design context is malformed") from exc
        required = {
            "schema_version", "session_id", "request_id", "opj_path",
            "remote_file_length", "remote_last_write_utc", "state",
            "active_design_name", "created_at_utc", "updated_at_utc",
        }
        allowed = required | {
            "error", "project_manager_name", "tcl_pid", "tcl_version",
            "same_design_object", "open_result",
        }
        if (
            not isinstance(value, Mapping)
            or not required.issubset(value)
            or not set(value).issubset(allowed)
            or value.get("schema_version") != 1
            or value.get("session_id") != self.session.manifest.session_id
            or not isinstance(value.get("request_id"), str)
            or not re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                value["request_id"],
            )
            or not isinstance(value.get("opj_path"), str)
            or value.get("state") not in (
                "opening", "unknown", "opened", "open_failed", "verification_failed", "failed"
            )
        ):
            raise CaptureDesignError("Capture Design context identity or schema is invalid")
        return dict(value)
