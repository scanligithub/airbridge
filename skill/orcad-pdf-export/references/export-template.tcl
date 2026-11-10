# AirBridge OrCAD Capture PDF export template.
#
# AirBridge launches Capture with the selected DSN as a native command-line
# argument (EDA target_file). Capture's capAutoLoad hook executes this script.
# Do not call ab_open_dsn here: the design is already open as the active GUI
# design. AirBridge requests WM_CLOSE after this script and the PDF validation
# finish because the EDA request uses keep_open=false.
#
# The UTF-8 config sidecar contains, one value per line:
#   output directory
#   PDF file name
#   Ghostscript executable
#   OrCAD PostScript printer name

namespace eval ::AirBridgePdfExport {}

set ::AirBridgePdfExport::config_file @CONFIG_FILE@
set ::AirBridgePdfExport::result_file @RESULT_FILE@

proc ::AirBridgePdfExport::single_line {value} {
    return [string map [list "\r" " " "\n" " "] $value]
}

proc ::AirBridgePdfExport::write_result {status pdf_path pdf_size message} {
    variable result_file
    set fp [open $result_file "w"]
    fconfigure $fp -encoding utf-8 -translation lf
    puts $fp "STATUS=$status"
    puts $fp "PDF_PATH=$pdf_path"
    puts $fp "PDF_SIZE=$pdf_size"
    puts $fp "MESSAGE=[::AirBridgePdfExport::single_line $message]"
    close $fp
}

proc ::AirBridgePdfExport::json_quote {value} {
    set escaped [string map [list \
        "\\" "\\\\" \
        "\"" "\\\"" \
        "\b" "\\b" \
        "\f" "\\f" \
        "\n" "\\n" \
        "\r" "\\r" \
        "\t" "\\t" \
    ] $value]
    return "\"$escaped\""
}

proc ::AirBridgePdfExport::run {} {
    variable config_file
    variable result_file

    set out_dir ""
    set pdf_name ""
    set gs_exe ""
    set ps_driver ""
    set pdf_path ""
    set pdf_size 0

    # Read non-ASCII paths from a UTF-8 sidecar instead of embedding them
    # directly in the Tcl source file (Capture 17.2 may use a legacy system
    # encoding when sourcing scripts).
    if {[catch {open $config_file "r"} config_fp]} {
        set message "Cannot open PDF configuration sidecar: $config_fp"
        catch {::AirBridgePdfExport::write_result "ERROR" "" 0 $message}
        puts "\[PDF_EXPORT_ERROR\] $message"
        return 1
    }
    fconfigure $config_fp -encoding utf-8 -translation auto
    set config_values {}
    while {[gets $config_fp config_line] >= 0} {
        lappend config_values $config_line
    }
    close $config_fp

    if {[llength $config_values] != 4} {
        set message "PDF configuration sidecar must contain exactly four lines."
        catch {::AirBridgePdfExport::write_result "ERROR" "" 0 $message}
        puts "\[PDF_EXPORT_ERROR\] $message"
        return 1
    }

    set out_dir [lindex $config_values 0]
    set pdf_name [lindex $config_values 1]
    set gs_exe [lindex $config_values 2]
    set ps_driver [lindex $config_values 3]

    if {$out_dir eq "" || $pdf_name eq "" || $gs_exe eq "" || $ps_driver eq ""} {
        set message "PDF configuration contains an empty required value."
        catch {::AirBridgePdfExport::write_result "ERROR" "" 0 $message}
        puts "\[PDF_EXPORT_ERROR\] $message"
        return 1
    }

    if {[catch {file mkdir $out_dir} err]} {
        set message "Cannot create PDF output directory: $err"
        catch {::AirBridgePdfExport::write_result "ERROR" "" 0 $message}
        puts "\[PDF_EXPORT_ERROR\] $message"
        return 1
    }

    set pdf_path [file join $out_dir $pdf_name]
    catch {file delete -force $pdf_path}

    # Capture's native command-line argument has already opened the DSN.
    # GetActiveDesign retrieves that GUI-opened design; do not open it again
    # with GetDesignAndSchematics / ab_open_dsn.
    if {[catch {
        set lSession $::DboSession_s_pDboSession
        DboSession -this $lSession
        set design [$lSession GetActiveDesign]
    } err]} {
        set message "Cannot retrieve Capture active design: $err"
        catch {::AirBridgePdfExport::write_result "ERROR" $pdf_path 0 $message}
        puts "\[PDF_EXPORT_ERROR\] $message"
        return 1
    }

    if {[is_dbo_null $design]} {
        set message "Capture has no active DboDesign. Native DSN startup may have failed."
        catch {::AirBridgePdfExport::write_result "ERROR" $pdf_path 0 $message}
        puts "\[PDF_EXPORT_ERROR\] $message"
        return 1
    }
    if {[catch {DboDesign -this $design} err]} {
        set message "Cannot bind active DboDesign object: $err"
        catch {::AirBridgePdfExport::write_result "ERROR" "" 0 $message}
        puts "\[PDF_EXPORT_ERROR\] $message"
        return 1
    }
    puts "\[PDF_EXPORT\] Active Capture design is available."

    if {[catch {package require OrHandlerPDFExport} err]} {
        set message "package require OrHandlerPDFExport failed: $err"
        catch {::AirBridgePdfExport::write_result "ERROR" $pdf_path 0 $message}
        puts "\[PDF_EXPORT_ERROR\] $message"
        return 1
    }

    if {![file exists $gs_exe]} {
        set message "Ghostscript executable does not exist: $gs_exe"
        catch {::AirBridgePdfExport::write_result "ERROR" $pdf_path 0 $message}
        puts "\[PDF_EXPORT_ERROR\] $message"
        return 1
    }

    # The PDF utility fills these two global paths later, while processing
    # printPDF. Preserve their Tcl variable references literally in the
    # configured converter command; do not expand them while building cfg.
    set gs_output_arg "-sOutputFile=\$::capPdfUtil::mPdfFilePath"
    set gs_ps_arg "\$::capPdfUtil::mPSFilePath"

    # Tcl list quoting keeps the executable and file arguments intact even
    # when their paths contain spaces.
    set gs_cmd [list \
        $gs_exe \
        -sDEVICE=pdfwrite \
        $gs_output_arg \
        -dBATCH \
        -dNOPAUSE \
        -dSAFER \
        $gs_ps_arg]

    set cfg_items [list \
        [format "\"outputDirectory\":%s" [::AirBridgePdfExport::json_quote $out_dir]] \
        [format "\"outputPdfFile\":%s" [::AirBridgePdfExport::json_quote $pdf_name]] \
        [format "\"postscriptFilePath\":%s" [::AirBridgePdfExport::json_quote $ps_driver]] \
        [format "\"PStoPdfConverter\":%s" [::AirBridgePdfExport::json_quote "Ghostscript 64 bit / equivalent"]] \
        [format "\"PStoPdfCommand\":%s" [::AirBridgePdfExport::json_quote $gs_cmd]] \
        "\"commandIndex\":2" \
        "\"printingMode\":\"0\"" \
        "\"orientation\":\"1\"" \
        "\"createPdfPropertiesFile\":\"0\"" \
        "\"createNetAndPartBookmarks\":\"1\"" \
        "\"PaperSizeIndex\":0" \
    ]
    set cfg [join $cfg_items ","]
    set cfg_wrapped "{{$cfg}}"

    puts "\[PDF_EXPORT\] Calling OrHandlerPDFExport::printPDF ..."
    if {[catch {eval "::OrHandlerPDFExport::printPDF $cfg_wrapped"} err]} {
        set message "OrHandlerPDFExport::printPDF failed: $err"
        catch {::AirBridgePdfExport::write_result "ERROR" $pdf_path 0 $message}
        puts "\[PDF_EXPORT_ERROR\] $message"
        return 1
    }

    if {![file exists $pdf_path]} {
        set message "PDF export returned, but the output file was not created: $pdf_path"
        catch {::AirBridgePdfExport::write_result "ERROR" $pdf_path 0 $message}
        puts "\[PDF_EXPORT_ERROR\] $message"
        return 1
    }

    set pdf_size [file size $pdf_path]
    if {$pdf_size <= 0} {
        set message "PDF output file is empty: $pdf_path"
        catch {::AirBridgePdfExport::write_result "ERROR" $pdf_path 0 $message}
        puts "\[PDF_EXPORT_ERROR\] $message"
        return 1
    }

    if {[catch {
        set pdf_fp [open $pdf_path "rb"]
        fconfigure $pdf_fp -translation binary -encoding binary
        set pdf_header [read $pdf_fp 5]
        close $pdf_fp
    } err]} {
        set message "Cannot read PDF header: $err"
        catch {::AirBridgePdfExport::write_result "ERROR" $pdf_path $pdf_size $message}
        puts "\[PDF_EXPORT_ERROR\] $message"
        return 1
    }

    if {$pdf_header ne "%PDF-"} {
        set message "Output file does not begin with the PDF signature: $pdf_path"
        catch {::AirBridgePdfExport::write_result "ERROR" $pdf_path $pdf_size $message}
        puts "\[PDF_EXPORT_ERROR\] $message"
        return 1
    }

    # Parse/render the generated PDF once with Ghostscript's nullpage device.
    # This catches truncated or structurally invalid PDFs without creating
    # another output file.
    if {[catch {
        exec -- $gs_exe -sDEVICE=nullpage -dBATCH -dNOPAUSE -dSAFER $pdf_path 2>@1
    } gs_validation]} {
        set message "Ghostscript PDF validation failed: $gs_validation"
        catch {::AirBridgePdfExport::write_result "ERROR" $pdf_path $pdf_size $message}
        puts "\[PDF_EXPORT_ERROR\] $message"
        return 1
    }

    set message "PDF export and Ghostscript validation completed."
    if {[catch {
        ::AirBridgePdfExport::write_result "OK" $pdf_path $pdf_size $message
    } err]} {
        puts "\[PDF_EXPORT_ERROR\] PDF was created, but the result marker could not be written: $err"
        return 1
    }

    puts "\[PDF_EXPORT_OK\] $pdf_path ($pdf_size bytes)"
    return 0
}

set ::AirBridgePdfExport::run_status [::AirBridgePdfExport::run]
if {$::AirBridgePdfExport::run_status != 0} {
    puts "\[PDF_EXPORT_ERROR\] Export workflow finished with errors."
}
