# Capture Dbo Session Design / Library iterator probe
# DBO-V02 — read-only; intended for Capture Start Page with no DSN/OPJ open.
# Submit this file through AirBridge /api/v1/eda/execute.
# Do NOT set target_file in the enclosing HTTP payload.

set report [dict create]
dict set report probe DBO-V02
dict set report pid [pid]
dict set report tcl_version $::tcl_version

set session_exists [info exists ::DboSession_s_pDboSession]
dict set report session_variable_exists $session_exists

if {!$session_exists} {
    dict set report outcome session_variable_missing
} else {
    set lSession $::DboSession_s_pDboSession
    dict set report session_pointer_nonempty [expr {$lSession ne ""}]

    set wrapper_rc [catch {
        DboSession -this $lSession
    } wrapper_value wrapper_options]
    dict set report session_wrapper_rc $wrapper_rc
    dict set report session_wrapper_result $wrapper_value

    if {$wrapper_rc != 0} {
        dict set report outcome session_wrapper_failed
        if {[dict exists $wrapper_options -errorinfo]} {
            dict set report session_wrapper_errorinfo [dict get $wrapper_options -errorinfo]
        }
    } else {
        set state_rc [catch {
            set lStatus [DboState]
        } state_value state_options]
        dict set report dbo_state_create_rc $state_rc
        dict set report dbo_state_create_result $state_value

        if {$state_rc != 0} {
            dict set report outcome dbo_state_create_failed
            if {[dict exists $state_options -errorinfo]} {
                dict set report dbo_state_create_errorinfo [dict get $state_options -errorinfo]
            }
        } else {
            # Enumerate open Designs. NewDesignsIter does not open a project.
            set design_count 0
            set design_names {}
            set lDesignsIter ""
            set design_iter_rc [catch {
                set lDesignsIter [$lSession NewDesignsIter $lStatus]
            } design_iter_value design_iter_options]
            dict set report design_iter_create_rc $design_iter_rc
            dict set report design_iter_create_result $design_iter_value

            if {$design_iter_rc == 0 && $lDesignsIter ne "" && $lDesignsIter ne "NULL"} {
                set lName [DboTclHelper_sMakeCString]
                set lNullObj NULL
                set next_rc [catch {
                    set lDesign [$lDesignsIter NextDesign $lStatus]
                } next_value next_options]
                while {$next_rc == 0 && $lDesign ne $lNullObj} {
                    incr design_count
                    set name_rc [catch {
                        $lDesign GetName $lName
                        DboTclHelper_sGetConstCharPtr $lName
                    } name_value name_options]
                    if {$name_rc == 0} {
                        lappend design_names $name_value
                    } else {
                        dict lappend report design_name_errors $name_value
                    }
                    set next_rc [catch {
                        set lDesign [$lDesignsIter NextDesign $lStatus]
                    } next_value next_options]
                }
                dict set report design_next_rc $next_rc
                if {$next_rc == 0} {
                    dict set report design_end_value $lDesign
                    dict set report design_end_is_null [expr {$lDesign eq $lNullObj}]
                } else {
                    dict set report design_next_error $next_value
                    if {[dict exists $next_options -errorinfo]} {
                        dict set report design_next_errorinfo [dict get $next_options -errorinfo]
                    }
                }
                set design_release_rc [catch {
                    delete_DboSessionDesignsIter $lDesignsIter
                } design_release_value design_release_options]
                dict set report design_iter_release_rc $design_release_rc
                dict set report design_iter_release_result $design_release_value
            } elseif {$design_iter_rc == 0} {
                dict set report design_iter_is_null 1
            } else {
                if {[dict exists $design_iter_options -errorinfo]} {
                    dict set report design_iter_create_errorinfo [dict get $design_iter_options -errorinfo]
                }
            }
            dict set report design_count $design_count
            dict set report design_names $design_names

            # Enumerate open Libraries. This only enumerates libraries already in this Session.
            set lib_count 0
            set lib_names {}
            set lLibsIter ""
            set lib_iter_rc [catch {
                set lLibsIter [$lSession NewLibsIter $lStatus]
            } lib_iter_value lib_iter_options]
            dict set report lib_iter_create_rc $lib_iter_rc
            dict set report lib_iter_create_result $lib_iter_value

            if {$lib_iter_rc == 0 && $lLibsIter ne "" && $lLibsIter ne "NULL"} {
                set lLibName [DboTclHelper_sMakeCString]
                set lNullObj NULL
                set next_lib_rc [catch {
                    set lLib [$lLibsIter NextLib $lStatus]
                } next_lib_value next_lib_options]
                while {$next_lib_rc == 0 && $lLib ne $lNullObj} {
                    incr lib_count
                    set lib_name_rc [catch {
                        $lLib GetName $lLibName
                        DboTclHelper_sGetConstCharPtr $lLibName
                    } lib_name_value lib_name_options]
                    if {$lib_name_rc == 0} {
                        lappend lib_names $lib_name_value
                    } else {
                        dict lappend report lib_name_errors $lib_name_value
                    }
                    set next_lib_rc [catch {
                        set lLib [$lLibsIter NextLib $lStatus]
                    } next_lib_value next_lib_options]
                }
                dict set report lib_next_rc $next_lib_rc
                if {$next_lib_rc == 0} {
                    dict set report lib_end_value $lLib
                    dict set report lib_end_is_null [expr {$lLib eq $lNullObj}]
                } else {
                    dict set report lib_next_error $next_lib_value
                    if {[dict exists $next_lib_options -errorinfo]} {
                        dict set report lib_next_errorinfo [dict get $next_lib_options -errorinfo]
                    }
                }
                set lib_release_rc [catch {
                    delete_DboSessionLibsIter $lLibsIter
                } lib_release_value lib_release_options]
                dict set report lib_iter_release_rc $lib_release_rc
                dict set report lib_iter_release_result $lib_release_value
            } elseif {$lib_iter_rc == 0} {
                dict set report lib_iter_is_null 1
            } else {
                if {[dict exists $lib_iter_options -errorinfo]} {
                    dict set report lib_iter_create_errorinfo [dict get $lib_iter_options -errorinfo]
                }
            }
            dict set report lib_count $lib_count
            dict set report lib_names $lib_names

            set state_release_rc [catch {
                $lStatus -delete
            } state_release_value state_release_options]
            dict set report dbo_state_release_rc $state_release_rc
            dict set report dbo_state_release_result $state_release_value
        }
    }
}

puts "AB_DBO_SESSION_ITERATORS_PROBE=$report"
set report
