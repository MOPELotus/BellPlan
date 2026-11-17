pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

Item {
    id: library

    property var tracks: []
    property bool editorMode: false
    property var selectedPaths: []
    property string currentPath: ""
    property string category: ""
    property string query: ""
    readonly property color ink: "#22253f"
    readonly property color muted: "#82869b"
    readonly property color accent: "#6659df"
    readonly property var categories: {
        var names = {};
        tracks.forEach(function (track) {
            names[track.category || "其他"] = true;
        });
        return ["全部分类"].concat(Object.keys(names).sort(function (a, b) {
            return a.localeCompare(b);
        }));
    }
    readonly property var filteredTracks: tracks.filter(function (track) {
        return (!library.category || (track.category || "其他") === library.category) && (!library.query || (track.name + " " + track.category).toLowerCase().indexOf(library.query.toLowerCase()) >= 0);
    })
    readonly property bool allVisibleSelected: filteredTracks.length > 0 && filteredTracks.every(function (track) {
        return library.selectedPaths.indexOf(track.path) >= 0;
    })

    signal command(string action, var payload)
    signal chosen(var paths)

    function togglePath(path, checked) {
        var next = selectedPaths.slice();
        var index = next.indexOf(path);
        if (checked && index < 0)
            next.push(path);
        else if (!checked && index >= 0)
            next.splice(index, 1);
        selectedPaths = next;
        currentPath = path;
    }
    function selectVisible(checked) {
        var next = selectedPaths.slice();
        filteredTracks.forEach(function (track) {
            var index = next.indexOf(track.path);
            if (checked && index < 0)
                next.push(track.path);
            else if (!checked && index >= 0)
                next.splice(index, 1);
        });
        selectedPaths = next;
    }
    function sizeLabel(bytes) {
        var value = Number(bytes || 0);
        return value >= 1048576 ? (value / 1048576).toFixed(1) + " MB" : Math.ceil(value / 1024) + " KB";
    }
    function previewPath() {
        return currentPath || (selectedPaths.length ? selectedPaths[0] : "");
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            ComboBox {
                id: categoryInput
                objectName: "builtinCategory"
                Layout.preferredWidth: 180
                model: library.categories
                onActivated: library.category = currentIndex > 0 ? currentText : ""
            }
            TextField {
                objectName: "builtinSearch"
                Layout.fillWidth: true
                placeholderText: "搜索音频名称"
                selectByMouse: true
                text: library.query
                onTextEdited: library.query = text
            }
            Button {
                objectName: "builtinPreview"
                text: "试听"
                flat: true
                enabled: !!library.previewPath()
                onClicked: library.command("preview_builtin", {
                    path: library.previewPath()
                })
            }
        }
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: 14
            color: "white"
            border.color: "#e9eaf3"

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 8
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 14
                    CheckBox {
                        objectName: "builtinSelectAll"
                        Layout.preferredWidth: 42
                        checked: library.allVisibleSelected
                        onToggled: library.selectVisible(checked)
                        ToolTip.visible: hovered
                        ToolTip.text: "选择当前筛选结果"
                    }
                    Label {
                        text: "音频名称"
                        color: library.muted
                        font.pixelSize: 12
                        Layout.fillWidth: true
                    }
                    Label {
                        text: "分类"
                        color: library.muted
                        font.pixelSize: 12
                        Layout.preferredWidth: 145
                    }
                    Label {
                        text: "文件大小"
                        color: library.muted
                        font.pixelSize: 12
                        Layout.preferredWidth: 82
                    }
                    Label {
                        text: "操作"
                        color: library.muted
                        font.pixelSize: 12
                        Layout.preferredWidth: 60
                    }
                }
                Rectangle {
                    Layout.fillWidth: true
                    height: 1
                    color: "#eceef5"
                }
                ListView {
                    id: trackList
                    objectName: "builtinList"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 3
                    model: library.filteredTracks
                    ScrollBar.vertical: ScrollBar {}

                    delegate: Rectangle {
                        id: row
                        required property var modelData
                        width: trackList.width
                        height: 58
                        radius: 8
                        color: library.currentPath === modelData.path ? "#f0edff" : "white"
                        RowLayout {
                            anchors.fill: parent
                            spacing: 14
                            CheckBox {
                                Layout.preferredWidth: 42
                                checked: library.selectedPaths.indexOf(row.modelData.path) >= 0
                                onToggled: library.togglePath(row.modelData.path, checked)
                            }
                            Label {
                                text: row.modelData.name
                                color: library.ink
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: library.currentPath = row.modelData.path
                                    onDoubleClicked: library.command("preview_builtin", {
                                        path: row.modelData.path
                                    })
                                }
                                ToolTip.visible: nameHover.hovered
                                ToolTip.text: row.modelData.relative_path || row.modelData.path
                                HoverHandler {
                                    id: nameHover
                                }
                            }
                            Label {
                                text: row.modelData.category || "其他"
                                color: library.muted
                                font.pixelSize: 12
                                Layout.preferredWidth: 145
                                elide: Text.ElideRight
                            }
                            Label {
                                text: library.sizeLabel(row.modelData.size)
                                color: library.muted
                                font.pixelSize: 12
                                Layout.preferredWidth: 82
                            }
                            Button {
                                text: "试听"
                                flat: true
                                Layout.preferredWidth: 60
                                font.pixelSize: 12
                                onClicked: {
                                    library.currentPath = row.modelData.path;
                                    library.command("preview_builtin", {
                                        path: row.modelData.path
                                    });
                                }
                            }
                        }
                    }
                    Label {
                        anchors.centerIn: parent
                        visible: !library.filteredTracks.length
                        text: library.tracks.length ? "没有匹配的音频" : "暂无内置音频"
                        color: library.muted
                    }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            Label {
                text: "共 " + library.filteredTracks.length + " 首，已选 " + library.selectedPaths.length + " 首"
                color: library.muted
                font.pixelSize: 12
                Layout.fillWidth: true
            }
            Button {
                text: "取消选择"
                flat: true
                enabled: library.selectedPaths.length > 0
                onClicked: library.selectedPaths = []
            }
            Button {
                objectName: "builtinAddLive"
                text: "加入立即播"
                enabled: library.selectedPaths.length > 0
                onClicked: library.command("live_add_builtin", {
                    paths: library.selectedPaths.slice()
                })
            }
            Button {
                objectName: "builtinAdd"
                text: library.editorMode ? "选取音频" : "加入节目"
                highlighted: true
                enabled: library.selectedPaths.length > 0
                onClicked: library.chosen(library.selectedPaths.slice())
            }
        }
    }
}
