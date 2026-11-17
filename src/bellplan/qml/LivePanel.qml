pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

Item {
    id: panel
    property var library: ({})
    property var playing: ({})
    property int selectedIndex: -1
    readonly property var files: library.files || []
    signal command(string action, var payload)
    readonly property color ink: "#22253f"
    readonly property color muted: "#82869b"
    readonly property color accent: "#6659df"
    function send(action, value) {
        command(action, value || {});
    }
    function save() {
        send("live_save_settings", {
            mode: ["list", "single", "random"][modeInput.currentIndex],
            volume: Math.round(volumeInput.value),
            stop_mode: ["playlist", "tracks", "duration"][stopInput.currentIndex],
            repeat: repeatInput.value,
            track_limit: tracksInput.value,
            duration: durationInput.value
        });
    }
    function clock(seconds) {
        seconds = Math.max(0, Math.floor(seconds || 0));
        return Math.floor(seconds / 60) + ":" + (seconds % 60 < 10 ? "0" : "") + seconds % 60;
    }
    ColumnLayout {
        anchors.fill: parent
        spacing: 12
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Button {
                text: "＋ 添加文件"
                highlighted: true
                onClicked: panel.send("live_add_files")
            }
            Button {
                text: "添加目录"
                flat: true
                onClicked: panel.send("live_add_directory")
            }
            Item {
                Layout.fillWidth: true
            }
            Button {
                text: "名称 ↑"
                flat: true
                onClicked: panel.send("live_sort", {
                    descending: false
                })
            }
            Button {
                text: "名称 ↓"
                flat: true
                onClicked: panel.send("live_sort", {
                    descending: true
                })
            }
            Button {
                text: "打乱"
                flat: true
                onClicked: panel.send("live_shuffle")
            }
        }
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: 14
            color: "white"
            border.color: "#e9eaf3"
            ListView {
                id: fileList
                anchors.fill: parent
                anchors.margins: 14
                clip: true
                model: panel.files
                spacing: 4
                ScrollBar.vertical: ScrollBar {}
                delegate: Rectangle {
                    id: fileRow
                    required property string modelData
                    required property int index
                    width: fileList.width
                    height: 64
                    radius: 8
                    color: panel.selectedIndex === index ? "#f0edff" : fileMouse.containsMouse ? "#f8f8fc" : "white"
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 12
                        Label {
                            text: String(fileRow.index + 1).padStart(2, "0")
                            color: panel.muted
                            Layout.preferredWidth: 28
                            font.pixelSize: 12
                        }
                        Label {
                            text: "♫"
                            color: panel.accent
                            font.pixelSize: 20
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Label {
                                text: fileRow.modelData.replace(/\\/g, "/").split("/").pop()
                                color: panel.ink
                                font.bold: true
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                            Label {
                                text: fileRow.modelData
                                color: panel.muted
                                font.pixelSize: 11
                                elide: Text.ElideMiddle
                                Layout.fillWidth: true
                            }
                        }
                    }
                    MouseArea {
                        id: fileMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: panel.selectedIndex = fileRow.index
                        onDoubleClicked: panel.send("live_play", {
                            index: fileRow.index
                        })
                    }
                }
                ColumnLayout {
                    anchors.centerIn: parent
                    visible: !panel.files.length
                    spacing: 8
                    Label {
                        text: "播放列表为空"
                        font.pixelSize: 21
                        color: panel.ink
                        Layout.alignment: Qt.AlignHCenter
                    }
                    Label {
                        text: "请添加文件或目录。"
                        color: panel.muted
                        font.pixelSize: 12
                        Layout.alignment: Qt.AlignHCenter
                    }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 4
            Label {
                text: "共 " + panel.files.length + " 首"
                color: panel.muted
                font.pixelSize: 12
                Layout.fillWidth: true
            }
            Button {
                text: "上移"
                flat: true
                enabled: panel.selectedIndex > 0
                onClicked: {
                    panel.send("live_move", {
                        index: panel.selectedIndex,
                        delta: -1
                    });
                    panel.selectedIndex--;
                }
            }
            Button {
                text: "下移"
                flat: true
                enabled: panel.selectedIndex >= 0 && panel.selectedIndex < panel.files.length - 1
                onClicked: {
                    panel.send("live_move", {
                        index: panel.selectedIndex,
                        delta: 1
                    });
                    panel.selectedIndex++;
                }
            }
            Button {
                text: "打开目录"
                flat: true
                enabled: panel.selectedIndex >= 0 && panel.selectedIndex < panel.files.length
                onClicked: panel.send("live_open_folder", {
                    index: panel.selectedIndex
                })
            }
            Button {
                text: "移除"
                flat: true
                enabled: panel.selectedIndex >= 0 && panel.selectedIndex < panel.files.length
                onClicked: {
                    panel.send("live_remove", {
                        index: panel.selectedIndex
                    });
                    panel.selectedIndex = Math.min(panel.selectedIndex, panel.files.length - 1);
                }
            }
            Button {
                text: "清空"
                flat: true
                enabled: !!panel.files.length
                onClicked: clearDialog.open()
            }
        }
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: options.implicitHeight + 28
            color: "white"
            radius: 14
            border.color: "#e9eaf3"
            ColumnLayout {
                id: options
                anchors.fill: parent
                anchors.margins: 14
                spacing: 8
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12
                    Label {
                        text: "播放方式"
                        color: panel.muted
                        font.pixelSize: 12
                    }
                    ComboBox {
                        id: modeInput
                        Layout.fillWidth: true
                        model: ["列表播放", "单曲循环", "随机播放"]
                        currentIndex: Math.max(0, ["list", "single", "random"].indexOf(panel.library.mode || "list"))
                        onActivated: panel.save()
                    }
                    Label {
                        text: "结束方式"
                        color: panel.muted
                        font.pixelSize: 12
                    }
                    ComboBox {
                        id: stopInput
                        Layout.fillWidth: true
                        model: ["列表循环遍数", "播放首数", "播放秒数"]
                        currentIndex: Math.max(0, ["playlist", "tracks", "duration"].indexOf(panel.library.stop_mode || "playlist"))
                        onActivated: panel.save()
                    }
                    SpinBox {
                        id: repeatInput
                        visible: stopInput.currentIndex === 0
                        from: 0
                        to: 1000000
                        value: panel.library.repeat === undefined ? 1 : panel.library.repeat
                        editable: true
                        onValueModified: panel.save()
                        ToolTip.visible: hovered
                        ToolTip.text: "列表循环遍数；0 为无限"
                    }
                    SpinBox {
                        id: tracksInput
                        visible: stopInput.currentIndex === 1
                        from: 0
                        to: 1000000
                        value: panel.library.track_limit || 0
                        editable: true
                        onValueModified: panel.save()
                        ToolTip.visible: hovered
                        ToolTip.text: "播放首数；0 为无限"
                    }
                    SpinBox {
                        id: durationInput
                        visible: stopInput.currentIndex === 2
                        from: 0
                        to: 31536000
                        value: panel.library.duration || 0
                        editable: true
                        onValueModified: panel.save()
                        ToolTip.visible: hovered
                        ToolTip.text: "播放秒数；0 为无限"
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Button {
                        text: "立即播放"
                        highlighted: true
                        enabled: !!panel.files.length
                        onClicked: {
                            panel.save();
                            panel.send("live_play", {
                                index: Math.max(0, panel.selectedIndex)
                            });
                        }
                    }
                    Button {
                        text: panel.playing.status === "paused" ? "继续" : "暂停"
                        flat: true
                        enabled: !!panel.playing.name
                        onClicked: panel.send(panel.playing.status === "paused" ? "resume" : "pause")
                    }
                    Button {
                        text: "停止立即播"
                        flat: true
                        enabled: !!panel.playing.name
                        onClicked: panel.send("live_stop")
                    }
                    Item {
                        Layout.fillWidth: true
                    }
                    Label {
                        text: "音量"
                        color: panel.muted
                        font.pixelSize: 12
                    }
                    Slider {
                        id: volumeInput
                        Layout.preferredWidth: 150
                        from: 0
                        to: 100
                        value: panel.library.volume === undefined ? 80 : panel.library.volume
                        onMoved: panel.save()
                    }
                    Label {
                        text: Math.round(volumeInput.value) + "%"
                        color: panel.muted
                        font.pixelSize: 12
                        Layout.preferredWidth: 34
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        text: panel.clock(panel.playing.position)
                        color: panel.muted
                        font.pixelSize: 11
                    }
                    Slider {
                        Layout.fillWidth: true
                        from: 0
                        to: Math.max(1, panel.playing.duration || 0)
                        value: panel.playing.position || 0
                        enabled: !!panel.playing.seekable && (panel.playing.duration || 0) > 0
                        onMoved: panel.send("seek", {
                            position: value
                        })
                    }
                    Label {
                        text: panel.clock(panel.playing.duration)
                        color: panel.muted
                        font.pixelSize: 11
                    }
                }
            }
        }
    }
    Dialog {
        id: clearDialog
        Component.onCompleted: {
            footer.standardButton(Dialog.Yes).text = "确定";
            footer.standardButton(Dialog.Cancel).text = "取消";
        }
        parent: Overlay.overlay
        modal: true
        width: 390
        x: (parent.width - width) / 2
        y: (parent.height - height) / 2
        title: "清空立即播列表"
        standardButtons: Dialog.Yes | Dialog.Cancel
        Label {
            text: "确认移除列表中的全部媒体？原始文件会保留。"
            width: parent.width
            wrapMode: Text.Wrap
            color: panel.ink
        }
        onAccepted: {
            panel.send("live_clear");
            panel.selectedIndex = -1;
        }
    }
}
