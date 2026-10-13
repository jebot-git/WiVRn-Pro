find_package(Python3 3.11 REQUIRED COMPONENTS Interpreter)
execute_process(COMMAND "${Python3_EXECUTABLE}" -c "from PySide6 import QtCore, QtWidgets, QtNetwork, QtDBus; assert QtCore.qVersion().split('.')[0] == '6'"
    RESULT_VARIABLE qt_status ERROR_VARIABLE qt_error)
if(NOT qt_status EQUAL 0)
    message(FATAL_ERROR "The unified Shora dashboard requires PySide6 with QtDBus: ${qt_error}")
endif()
configure_file(wivrn-pro-dashboard.in wivrn-pro-dashboard @ONLY)
add_custom_target(wivrn-dashboard ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/wivrn-pro-dashboard")
install(PROGRAMS "${CMAKE_CURRENT_BINARY_DIR}/wivrn-pro-dashboard" DESTINATION ${CMAKE_INSTALL_BINDIR} COMPONENT Dashboard)
install(DIRECTORY shora/ DESTINATION ${CMAKE_INSTALL_DATADIR}/wivrn-pro/dashboard COMPONENT Dashboard FILES_MATCHING PATTERN "*.py" PATTERN "__pycache__" EXCLUDE)
configure_file(wivrn-pro.desktop wivrn-pro.desktop @ONLY)
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/wivrn-pro.desktop" DESTINATION ${CMAKE_INSTALL_DATADIR}/applications COMPONENT Dashboard)
foreach(icon_size 16 22 24 32 48 64 128 256 512 1024)
    install(FILES "../images/wivrn-pro-menu/${icon_size}.png"
        DESTINATION "${CMAKE_INSTALL_DATADIR}/icons/hicolor/${icon_size}x${icon_size}/apps"
        RENAME wivrn-pro-menu.png COMPONENT Dashboard)
endforeach()

configure_file(wivrn-pro-dashboard.service.in wivrn-pro-dashboard.service @ONLY)
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/wivrn-pro-dashboard.service" DESTINATION lib/systemd/user COMPONENT Dashboard)

install(DIRECTORY "${CMAKE_SOURCE_DIR}/fork/encoder-presets/profiles" DESTINATION "${CMAKE_INSTALL_DATADIR}/wivrn-pro/encoder-presets")
