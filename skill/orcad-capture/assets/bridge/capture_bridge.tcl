# Capture Skill Tcl Bridge for Tcl 8.6.
#
# The Bridge owns only the per-session file queue.  All transport and file
# publication is performed by the Capture Skill through generic AirBridge
# Shell/RFS APIs.  No JSON or third-party Tcl package is required.

namespace eval ::AirBridgeSkill {
    variable schema_version 1
    variable session_id ""
    variable startup_nonce ""
    variable session_dir ""
    variable bridge_sha256 ""
    variable runtime_pid ""
    variable tcl_version ""
    variable ready_file ""
    variable request_dir ""
    variable running_dir ""
    variable result_dir ""
    variable poll_interval_ms 250
    variable poll_after_id ""
    variable active_request_id ""
}

proc ::AirBridgeSkill::write_ascii_atomic {final_path text} {
    set temp_path "${final_path}.[pid].[clock clicks].tmp"
    if {[file exists $final_path] || [file exists $temp_path]} {
        error "Capture Skill refuses to overwrite an existing queue file"
    }
    set channel ""
    try {
        set channel [open $temp_path {WRONLY CREAT EXCL}]
        fconfigure $channel -encoding ascii -translation lf
        puts -nonewline $channel $text
        flush $channel
        close $channel
        set channel ""
        file rename -- $temp_path $final_path
    } on error {message options} {
        if {$channel ne ""} {
            catch {close $channel}
        }
        catch {file delete -- $temp_path}
        return -options $options $message
    }
}

proc ::AirBridgeSkill::read_request_file {path filename_request_id} {
    variable session_id
    variable startup_nonce

    set channel [open $path rb]
    try {
        set raw [read $channel]
    } finally {
        close $channel
    }
    if {[string length $raw] > 400000} {
        error "Capture request exceeded the maximum envelope size"
    }

    set lines [split $raw "\n"]
    if {[llength $lines] == 9 && [lindex $lines 8] eq ""} {
        set lines [lrange $lines 0 7]
    }
    if {[llength $lines] != 8 || [lindex $lines 0] ne "AB_CAPTURE_REQUEST_V1"} {
        error "Capture request envelope header or field count is invalid"
    }

    set expected_keys {
        session_id startup_nonce request_id script_sha256
        script_crc32 script_length script_b64
    }
    set fields [dict create]
    set index 1
    foreach key $expected_keys {
        set line [lindex $lines $index]
        set prefix "${key}="
        if {![string match "${prefix}*" $line]} {
            error "Capture request field order is invalid"
        }
        dict set fields $key [string range $line [string length $prefix] end]
        incr index
    }

    set sid [dict get $fields session_id]
    set nonce [dict get $fields startup_nonce]
    set rid [dict get $fields request_id]
    set digest [dict get $fields script_sha256]
    set crc [dict get $fields script_crc32]
    set length_text [dict get $fields script_length]
    set encoded [dict get $fields script_b64]

    if {$sid ne $session_id || $nonce ne $startup_nonce} {
        error "Capture request belongs to another Session or Startup Nonce"
    }
    if {$rid ne $filename_request_id} {
        error "Capture request Request ID does not match its filename"
    }
    if {![regexp {^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$} $rid]} {
        error "Capture request Request ID is invalid"
    }
    if {![regexp {^[0-9a-f]{64}$} $digest]} {
        error "Capture request script SHA-256 is invalid"
    }
    if {![regexp {^[0-9a-f]{8}$} $crc]} {
        error "Capture request CRC-32 is invalid"
    }
    if {![string is integer -strict $length_text] || $length_text < 1 || $length_text > 262144} {
        error "Capture request script length is invalid"
    }
    if {![regexp {^[A-Za-z0-9+/]*={0,2}$} $encoded] || ([string length $encoded] % 4) != 0} {
        error "Capture request Base64 payload is invalid"
    }

    set script_bytes [binary decode base64 $encoded]
    if {[string length $script_bytes] != $length_text} {
        error "Capture request payload length does not match"
    }
    set actual_crc [format %08x [zlib crc32 $script_bytes]]
    if {$actual_crc ne $crc} {
        error "Capture request payload CRC-32 does not match"
    }
    set script [encoding convertfrom utf-8 $script_bytes]
    if {[string trim $script] eq ""} {
        error "Capture request script is empty"
    }

    return [dict create \
        request_id $rid \
        script_sha256 $digest \
        script $script]
}

proc ::AirBridgeSkill::write_result {request_id script_sha256 status result_value error_info} {
    variable session_id
    variable startup_nonce
    variable result_dir

    set result_text [string range $result_value 0 99999]
    set error_text [string range $error_info 0 99999]
    set result_bytes [encoding convertto utf-8 $result_text]
    set error_bytes [encoding convertto utf-8 $error_text]
    set result_b64 [binary encode base64 -maxlen 0 $result_bytes]
    set error_b64 [binary encode base64 -maxlen 0 $error_bytes]
    set result_crc [format %08x [zlib crc32 $result_bytes]]
    set error_crc [format %08x [zlib crc32 $error_bytes]]
    # The Python Skill parser expects literal true/false tokens, not Tcl's
    # numeric boolean representation.
    set result_truncated "false"
    if {[string length $result_value] > 100000} {
        set result_truncated "true"
    }
    set error_truncated "false"
    if {[string length $error_info] > 100000} {
        set error_truncated "true"
    }

    set envelope [join [list \
        "AB_CAPTURE_RESULT_V1" \
        "session_id=$session_id" \
        "startup_nonce=$startup_nonce" \
        "request_id=$request_id" \
        "script_sha256=$script_sha256" \
        "status=$status" \
        "result_length=[string length $result_bytes]" \
        "result_crc32=$result_crc" \
        "result_truncated=$result_truncated" \
        "result_b64=$result_b64" \
        "error_info_length=[string length $error_bytes]" \
        "error_info_crc32=$error_crc" \
        "error_info_truncated=$error_truncated" \
        "error_info_b64=$error_b64" \
    ] "\n"]
    set result_path [file join $result_dir "${request_id}.result"]
    write_ascii_atomic $result_path $envelope
    return $result_path
}

proc ::AirBridgeSkill::process_request {request_path running_path request_id} {
    set result_path ""
    set script_sha256 ""
    set status "error"
    set result_value ""
    set error_info ""

    if {[file exists [file join [file dirname $running_path] ".." "results" "${request_id}.result"]]} {
        catch {file delete -- $running_path}
        return
    }

    if {[catch {
        set request [read_request_file $running_path $request_id]
        set script_sha256 [dict get $request script_sha256]
        set script [dict get $request script]
        set code [catch {uplevel #0 $script} result_value options]
        if {$code == 0} {
            set status "ok"
        } else {
            set status "error"
            if {[dict exists $options -errorinfo]} {
                set error_info [dict get $options -errorinfo]
            } else {
                set error_info $result_value
            }
        }
    } failure options]} {
        set status "error"
        set result_value "Capture request rejected before script execution: $failure"
        set error_info $failure
    }

    if {[catch {
        set result_path [write_result $request_id $script_sha256 $status $result_value $error_info]
        file delete -- $running_path
    } write_error]} {
        puts stderr "AirBridge Capture Skill could not persist result for $request_id: $write_error"
        # Keep the .running marker.  The Host-A caller must treat this request
        # as UNKNOWN and must never publish or execute it a second time.
    }
}

proc ::AirBridgeSkill::poll {} {
    variable request_dir
    variable running_dir
    variable result_dir
    variable active_request_id
    variable poll_after_id
    variable poll_interval_ms

    set poll_after_id ""
    if {$active_request_id eq ""} {
        set candidates [lsort [glob -nocomplain -directory $request_dir -- *.req]]
        foreach request_path $candidates {
            set filename [file tail $request_path]
            if {![regexp {^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.req$} $filename -> request_id]} {
                continue
            }
            set result_path [file join $result_dir "${request_id}.result"]
            set running_path [file join $running_dir "${request_id}.running"]
            if {[file exists $result_path]} {
                catch {file delete -- $request_path}
                continue
            }
            if {[file exists $running_path]} {
                continue
            }
            if {[catch {file rename -- $request_path $running_path} move_error]} {
                puts stderr "AirBridge Capture Skill could not claim request $request_id: $move_error"
                continue
            }

            set active_request_id $request_id
            process_request $request_path $running_path $request_id
            set active_request_id ""
            break
        }
    }

    set poll_after_id [after $poll_interval_ms ::AirBridgeSkill::poll]
}

proc ::AirBridgeSkill::schedule_poll {} {
    variable poll_after_id
    variable poll_interval_ms
    if {$poll_after_id eq ""} {
        set poll_after_id [after $poll_interval_ms ::AirBridgeSkill::poll]
    }
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
    variable request_dir
    variable running_dir
    variable result_dir
    variable poll_interval_ms

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

    set session_id [string tolower $sid]
    set startup_nonce $nonce
    set session_dir [file normalize $work_dir]
    set bridge_sha256 $bridge_hash
    set runtime_pid [pid]
    set tcl_version [info patchlevel]
    set ready_file [file join $session_dir "bridge.ready"]
    set request_dir [file join $session_dir "requests"]
    set running_dir [file join $session_dir "running"]
    set result_dir [file join $session_dir "results"]

    foreach directory [list $request_dir $running_dir $result_dir] {
        if {![file isdirectory $directory]} {
            error "Capture Skill queue directory is missing: $directory"
        }
    }
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
        file rename -- $temp_file $ready_file
    } on error {message options} {
        if {$channel ne ""} {
            catch {close $channel}
        }
        catch {file delete -- $temp_file}
        return -options $options $message
    }

    schedule_poll
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
    variable active_request_id

    return [dict create \
        session_id $session_id \
        runtime_pid $runtime_pid \
        tcl_version $tcl_version \
        active_request_id $active_request_id \
        ready_file $ready_file]
}
