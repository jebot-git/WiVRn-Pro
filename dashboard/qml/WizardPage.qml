import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

Kirigami.ScrollablePage {
    title: i18n("WiVRn Pro setup")
    ColumnLayout {
        Controls.Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            text: i18n("Install the matching WiVRn Pro APK on your Quest Pro. Connect the headset to the same Wi-Fi network as this Intel Arc PC, open WiVRn Pro and select this server. Enter the pairing PIN shown here when prompted.")
        }
        Controls.Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            text: i18n("Use Room capture in the headset's streaming settings to capture your playspace. Spatial data and Shora tracking use the same WiVRn stage automatically.")
        }
    }
}
