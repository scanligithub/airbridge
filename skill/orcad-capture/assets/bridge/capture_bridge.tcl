# Capture Skill startup handshake only.
#
# This bridge deliberately does not execute Tcl requests yet.  M3.2.4 will add
# the request/result queue after the startup identity handshake is validated.
# All session values are injected by the unique capAutoLoad Hook.

namespace eval ::AirBridgeSkill {
    variable schema_version 1
    variable session_id ""
    variable startup_nonce ""
    variable session_dir ""
    variable bridge_sha256 ""
    variable runtime_pid ""
    variable tcl_version ""
    variable ready_file ""
}

proc ::AirBridgeSkill::bootstrap {sid nonce work_dir bridge_hash} {
    variable schema_version
    variable session_id
    variable startup_nonce
    variable session_dir
    variable bridge_sha256
    variable runtime_pid
    variable tcl_version
    variable ready_file

    if {![regexp {^[0-9a-fA-F-]{36}$} $sid]} {
        error "Capture Skill Bridge rejected invalid Session ID"
    }
    if {![regexp {^[A-Za-z0-9_-]{32,128}$} $nonce]} {
        error "Capture Skill Bridge rejected invalid Startup Nonce"
    }
    if {![regexp {^[0-9a-f]{64}$} $bridge_hash]} {
        error "Capture Skill Bridge rejected invalid Bridge SHA-256"
    }
    if {![file isdirectory $work_dir]} {
        error "Capture Skill session directory does not exist"
    }

    set session_id $sid
    set startup_nonce $nonce
    set session_dir [file normalize $work_dir]
    set bridge_sha256 $bridge_hash
    set runtime_pid [pid]
    set tcl_version [info patchlevel]
    set ready_file [file join $session_dir "bridge.ready"]

    if {[file exists $ready_file]} {
        error "Capture Skill READY file already exists; refusing to overwrite"
    }

    set ready_at [clock format [clock seconds] -gmt 1 -format "%Y-%m-%dT%H:%M:%SZ"]
    set ready_json [format \
        "{\"schema_version\":%d,\"session_id\":\"%s\",\"startup_nonce\":\"%s\",\"runtime_pid\":%d,\"tcl_version\":\"%s\",\"bridge_sha256\":\"%s\",\"ready_at_utc\":\"%s\"}" \
        $schema_version $session_id $startup_nonce $runtime_pid $tcl_version \
        $bridge_sha256 $ready_at]

    # Write a session-unique temporary file, flush it, then atomically rename.
    set temp_file [file join $session_dir "bridge.ready.[pid].tmp"]
    if {[file exists $temp_file]} {
        error "Capture Skill READY temporary file already exists"
    }

    set channel ""
    try {
        set channel [open $temp_file {WRONLY CREAT EXCL}]
        fconfigure $channel -encoding utf-8 -translation lf
        puts -nonewline $channel $ready_json
        flush $channel
        close $channel
        set channel ""
        file rename $temp_file $ready_file
    } on error {message options} {
        if {$channel ne ""} {
            catch {close $channel}
        }
        catch {file delete $temp_file}
        return -options $options $message
    }

    return [dict create \
        state "ready" \
        session_id $session_id \
        runtime_pid $runtime_pid \
        ready_file $ready_file]
}

proc ::AirBridgeSkill::get_status {} {
    variable session_id
    variable runtime_pid
    variable tcl_version
    variable ready_file

    return [dict create \
        session_id $session_id \
        runtime_pid $runtime_pid \
        tcl_version $tcl_version \
        ready_file $ready_file]
}
