# OrCAD Capture schematic → PDF (no GUI dialogs)
# Substitute: DSN_PATH OUT_DIR PDF_NAME GS_EXE PS_DRIVER
# Requires AirBridge polyfill helpers: ab_open_dsn is_dbo_null

set dsn_path   {DSN_PATH}
set out_dir    {OUT_DIR}
set pdf_name   {PDF_NAME}
set gs_exe     {GS_EXE}
set ps_driver  {PS_DRIVER}

file mkdir $out_dir

puts "Opening $dsn_path"
set design [ab_open_dsn $dsn_path]
if {[is_dbo_null $design]} {
    puts {[ERROR] ab_open_dsn failed}
    return
}
puts {[OK] design opened}

if {[catch {package require OrHandlerPDFExport} err]} {
    puts "\[ERROR\] package require OrHandlerPDFExport: $err"
    return
}

# Capture PDF util fills ::capPdfUtil::mPdfFilePath and ::capPdfUtil::mPSFilePath at runtime
set gs_cmd "{$gs_exe} -sDEVICE=pdfwrite -sOutputFile=$::capPdfUtil::mPdfFilePath -dBATCH -dNOPAUSE -dSAFER $::capPdfUtil::mPSFilePath"

set cfg [join [list \
    "\"outputDirectory\":\"$out_dir\"" \
    "\"outputPdfFile\":\"$pdf_name\"" \
    "\"postscriptFilePath\":\"$ps_driver\"" \
    "\"PStoPdfConverter\":\"Ghostscript 64 bit / equivalent\"" \
    "\"PStoPdfCommand\":\"$gs_cmd\"" \
    "\"commandIndex\":2" \
    "\"printingMode\":\"0\"" \
    "\"orientation\":\"1\"" \
    "\"createPdfPropertiesFile\":\"0\"" \
    "\"createNetAndPartBookmarks\":\"1\"" \
    "\"PaperSizeIndex\":0" \
] ","]

set cfg_wrapped "{{$cfg}}"
puts "PDF config ready"

if {[catch {eval "::OrHandlerPDFExport::printPDF $cfg_wrapped"} err]} {
    puts "\[ERROR\] printPDF: $err"
} else {
    puts "\[OK\] PDF export requested → $out_dir/$pdf_name"
}

# Optional close without saving UI prompts when design was only opened for export
# ab_save_and_close $design
