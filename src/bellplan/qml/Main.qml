pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Controls.Material
import QtQuick.Layouts

ApplicationWindow {
    id: win
    objectName: "mainWindow"
    width: 1360
    height: 900
    minimumWidth: 1050
    minimumHeight: 720
    visible: true
    title: "铃序 · Evolved"
    color: "#f5f6fb"
    font.family: "Noto Sans CJK SC"
    font.pixelSize: 14
    Material.theme: Material.Light
    Material.primary: "#6659df"
    Material.accent: "#6659df"
    property var state: JSON.parse(backend ? backend.stateJson || "{}" : "{}")
    property int page: 0
    property string search: ""
    property string planFilter: ""
    property string sortMode: "manual"
    property bool sortDescending: false
    property var editing: ({})
    property string selectedPlan: ""
    property string selectedProgramId: ""
    readonly property color ink: "#22253f"
    readonly property color muted: "#82869b"
    readonly property color accent: "#6659df"
    readonly property var programs: win.state.programs || []
    readonly property var plans: win.state.plans || []
    readonly property var playing: (win.state.playing || {}).immediate || ({})
    readonly property var filtered: {
        var result = programs.filter(function (p) {
            return (!planFilter || p.plan_id === planFilter) && (!search || (p.name + " " + p.time + " " + (p.files || []).join(" ")).toLowerCase().indexOf(search.toLowerCase()) >= 0);
        });
        if (sortMode !== "manual")
            result.sort(function (a, b) {
                var left = sortMode === "plan" ? planName(a.plan_id) : String(a[sortMode] || "");
                var right = sortMode === "plan" ? planName(b.plan_id) : String(b[sortMode] || "");
                var cmp = left.localeCompare(right);
                return sortDescending ? -cmp : cmp;
            });
        return result;
    }
    readonly property var media: {
        var seen = {}, out = [];
        programs.forEach(function (p) {
            (p.files || []).forEach(function (f) {
                if (!seen[f]) {
                    seen[f] = true;
                    out.push(f);
                }
            });
        });
        return out;
    }
    function act(action, payload) {
        try {
            var result = JSON.parse(backend.dispatch(action, JSON.stringify(payload || {})) || "{}");
            if (result.ok === false && !result.cancelled)
                showError(result.error || "操作未完成");
            return result.ok !== false;
        } catch (e) {
            showError(String(e));
            return false;
        }
    }
    function showError(message) {
        errorText.text = message;
        errorDialog.open();
    }
    function planName(id) {
        for (var i = 0; i < plans.length; i++)
            if (plans[i].id === id)
                return plans[i].name;
        return "未分组";
    }
    function kindLabel(k) {
        return ({
                audio: "音频播放",
                tts: "语音朗读",
                command: "运行程序",
                stop: "停止播放",
                volume: "调整音量",
                shutdown: "关机",
                reboot: "重启"
            })[k] || k;
    }
    function weekLabel(p) {
        if ((p.dates || []).length)
            return "指定日期（" + p.dates.length + "天）";
        if ((p.month_days || []).length)
            return "每月 " + p.month_days.join("、") + " 日";
        var ds = p.weekdays || [];
        if (ds.length === 7)
            return "每天";
        if (JSON.stringify(ds) === "[0,1,2,3,4]")
            return "周一至周五";
        return ds.map(function (d) {
            return ["一", "二", "三", "四", "五", "六", "日"][d];
        }).join(" / ") || "无星期";
    }
    function edit(p) {
        editing = p || ({});
        nameInput.text = p ? p.name : "";
        timesInput.text = p ? (p.times && p.times.length ? p.times.join("\n") : p.time) : "08:00:00";
        var pi = 0;
        for (var i = 0; i < plans.length; i++)
            if (p && plans[i].id === p.plan_id)
                pi = i;
        planInput.currentIndex = pi;
        kindInput.currentIndex = Math.max(0, kindValues.indexOf(p ? p.kind : "audio"));
        filesInput.text = p ? (p.files || []).join("\n") : "";
        textInput.text = p ? (p.kind === "command" && !p.text ? JSON.stringify(p.command || []) : p.text || "") : "";
        datesInput.text = p ? (p.dates || []).join("\n") : "";
        excludedDatesInput.text = p ? (p.excluded_dates || []).join("\n") : "";
        programExcludeInput.text = JSON.stringify(p ? p.exclude_windows || [] : [], null, 2);
        for (i = 0; i < 7; i++)
            days.itemAt(i).checked = p ? (p.weekdays || []).indexOf(i) >= 0 : i < 5;
        monthDays.text = p ? (p.month_days || []).join(",") : "";
        startDate.text = p ? p.start_date || (p.datetime_from || "").split("T")[0] || "" : "";
        startClock.text = p ? (p.datetime_from || "").split("T")[1] || "" : "";
        endDate.text = p ? p.end_date || (p.datetime_to || "").split("T")[0] || "" : "";
        endClock.text = p ? (p.datetime_to || "").split("T")[1] || "" : "";
        volumeInput.value = p && p.volume !== undefined ? p.volume : 80;
        stopModeInput.currentIndex = Math.max(0, stopModes.indexOf(p ? p.stop_mode || "playlist" : "playlist"));
        trackLimitInput.value = p ? p.track_limit || 0 : 0;
        repeatInput.value = p && p.repeat !== undefined ? p.repeat : 1;
        durationInput.value = p ? p.duration || 0 : 0;
        fadeIn.value = p ? p.fade_in || 0 : 0;
        fadeOut.value = p ? p.fade_out || 0 : 0;
        priorityInput.value = p ? p.priority || 0 : 0;
        shuffleInput.checked = p ? !!p.shuffle : false;
        enabledInput.checked = p ? p.enabled !== false : true;
        queueInput.currentIndex = p && p.playback_mode === "queue" ? 1 : 0;
        deviceInput.currentIndex = 0;
        var devices = win.state.devices || [];
        for (i = 0; i < devices.length; i++)
            if (p && devices[i].id === p.output_device)
                deviceInput.currentIndex = i + 1;
        editor.open();
        Qt.callLater(function () {
            editorScroll.contentItem.contentY = 0;
        });
    }
    function acceptBuiltins(paths) {
        if (!editor.visible)
            edit(null);
        var existing = filesInput.text.split("\n").map(function (p) {
            return p.trim();
        }).filter(Boolean);
        paths.forEach(function (p) {
            if (existing.indexOf(p) < 0)
                existing.push(p);
        });
        filesInput.text = existing.join("\n");
        kindInput.currentIndex = 0;
        builtinDialog.close();
    }
    function openBackup(operation) {
        backupDialog.operation = operation;
        backupPrograms.checked = true;
        backupHolidays.checked = true;
        backupCalendar.checked = true;
        backupLive.checked = true;
        backupSettings.checked = true;
        backupDialog.open();
    }
    function textEditing() {
        var focused = win.activeFocusItem;
        return focused && (focused instanceof TextInput || focused instanceof TextEdit);
    }
    readonly property var stopModes: ["playlist", "tracks", "duration"]
    readonly property var kindValues: ["audio", "tts", "command", "stop", "volume", "shutdown", "reboot"]
    Shortcut {
        sequence: "Ctrl+N"
        enabled: !editor.visible && !plansDialog.visible
        onActivated: win.edit(null)
    }
    Shortcut {
        sequence: "Ctrl+F"
        enabled: !editor.visible && !plansDialog.visible
        onActivated: {
            win.page = 0;
            overviewSearch.forceActiveFocus();
        }
    }
    Shortcut {
        sequence: "Delete"
        enabled: !!win.selectedProgramId && (win.page === 0 || win.page === 1) && !editor.visible && !plansDialog.visible && !win.textEditing() && !errorDialog.visible && !deleteDialog.visible
        onActivated: {
            var p = programs.find(function (x) {
                return x.id === win.selectedProgramId;
            });
            if (p) {
                deleteDialog.programId = p.id;
                deleteDialog.programName = p.name;
                deleteDialog.open();
            }
        }
    }
    function localize(dialog) {
        var values = [[Dialog.Ok, "确定"], [Dialog.Save, "保存"], [Dialog.Cancel, "取消"], [Dialog.Close, "关闭"], [Dialog.Yes, "确定"], [Dialog.No, "否"]];
        for (var i = 0; i < values.length; i++) {
            var button = dialog.footer.standardButton(values[i][0]);
            if (button)
                button.text = values[i][1];
        }
    }
    Connections {
        target: backend
        ignoreUnknownSignals: true
        function onErrorOccurred(message) {
            win.showError(message);
        }
        function onFileSelectionJsonChanged() {
            try {
                var selected = JSON.parse(backend.fileSelectionJson || "[]");
                if (selected.length)
                    filesInput.text = selected.join("\n");
            } catch (e) {}
        }
    }
    component SmallText: Label {
        color: win.muted
        font.pixelSize: 12
    }
    component Panel: Rectangle {
        radius: 16
        color: "white"
        border.color: "#e9eaf3"
    }
    component QuietButton: Button {
        flat: true
        Material.foreground: win.muted
        font.pixelSize: 12
    }
    component SectionLabel: Label {
        font.pixelSize: 13
        font.bold: true
        color: win.ink
        topPadding: 12
    }
    component Stat: Panel {
        id: stat
        property string caption
        property string value
        property string detail
        property string symbol
        property color symbolColor: win.accent
        Layout.fillWidth: true
        Layout.preferredHeight: 120
        RowLayout {
            anchors.fill: parent
            anchors.margins: 21
            spacing: 17
            Rectangle {
                width: 48
                height: 48
                radius: 15
                color: Qt.rgba(stat.symbolColor.r, stat.symbolColor.g, stat.symbolColor.b, 0.09)
                Label {
                    anchors.centerIn: parent
                    text: stat.symbol
                    font.pixelSize: 22
                    color: stat.symbolColor
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 3
                SmallText {
                    text: stat.caption
                }
                Label {
                    text: stat.value
                    color: win.ink
                    font.pixelSize: 24
                    font.bold: true
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
                SmallText {
                    text: stat.detail
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
            }
        }
    }
    RowLayout {
        anchors.fill: parent
        spacing: 0
        Rectangle {
            Layout.fillHeight: true
            Layout.preferredWidth: 218
            color: "#ffffff"
            border.color: "#e9eaf3"
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 8
                RowLayout {
                    Layout.topMargin: 12
                    Layout.bottomMargin: 28
                    spacing: 12
                    Rectangle {
                        width: 42
                        height: 42
                        radius: 13
                        color: win.accent
                        Label {
                            anchors.centerIn: parent
                            text: "◷"
                            font.pixelSize: 30
                            color: "white"
                        }
                    }
                    Column {
                        Label {
                            text: "铃序"
                            color: win.ink
                            font.bold: true
                            font.pixelSize: 19
                        }
                        Label {
                            text: "E V O L V E D"
                            color: win.muted
                            font.pixelSize: 10
                            topPadding: 2
                        }
                    }
                }
                SmallText {
                    text: "功 能"
                    leftPadding: 12
                    bottomPadding: 8
                }
                Repeater {
                    model: [
                        {
                            icon: "▦",
                            name: "定时播"
                        },
                        {
                            icon: "☷",
                            name: "节目单"
                        },
                        {
                            icon: "♫",
                            name: "立即播"
                        },
                        {
                            icon: "≡",
                            name: "运行日志"
                        },
                        {
                            icon: "⚙",
                            name: "设置"
                        },
                        {
                            icon: "▦",
                            name: "节假日与调休"
                        }
                    ]
                    delegate: Rectangle {
                        required property int index
                        objectName: "nav" + index
                        required property var modelData
                        Layout.fillWidth: true
                        height: 48
                        radius: 10
                        color: win.page === index ? "#f0edff" : navMouse.containsMouse ? "#f7f7fc" : "transparent"
                        Row {
                            anchors.verticalCenter: parent.verticalCenter
                            leftPadding: 15
                            spacing: 14
                            Label {
                                text: modelData.icon
                                width: 20
                                font.pixelSize: 22
                                color: win.page === index ? win.accent : win.muted
                            }
                            Label {
                                text: modelData.name
                                font.bold: win.page === index
                                color: win.page === index ? win.accent : win.ink
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }
                        MouseArea {
                            id: navMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: win.page = index
                        }
                    }
                }
                Item {
                    Layout.fillHeight: true
                }
                Rectangle {
                    Layout.fillWidth: true
                    height: 120
                    radius: 12
                    color: "#f7f7fc"
                    Column {
                        anchors.fill: parent
                        anchors.margins: 14
                        spacing: 6
                        Label {
                            text: "定时播状态"
                            color: win.ink
                            font.pixelSize: 12
                            font.bold: true
                        }
                        SmallText {
                            text: "当前程序版本 0.1"
                        }
                        SmallText {
                            text: "节目总数：" + programs.length
                            color: win.accent
                        }
                    }
                }
                SmallText {
                    text: "BellPlan · 0.1"
                    Layout.topMargin: 12
                    Layout.alignment: Qt.AlignHCenter
                }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 94
                color: "white"
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 30
                    anchors.rightMargin: 30
                    spacing: 15
                    ColumnLayout {
                        spacing: 4
                        Layout.fillWidth: true
                        Label {
                            text: ["定时播", "节目单", "立即播", "运行日志", "设置", "节假日与调休"][win.page]
                            font.pixelSize: 24
                            font.bold: true
                            color: win.ink
                        }
                        SmallText {
                            text: win.state.now || "正在启动…"
                        }
                    }
                    Rectangle {
                        width: 8
                        height: 8
                        radius: 4
                        color: win.state.schedulerEnabled ? "#38ad83" : "#b4b7c7"
                    }
                    Label {
                        text: win.state.schedulerEnabled ? "启用定时播" : "启用定时播"
                        font.pixelSize: 12
                        color: win.muted
                    }
                    Switch {
                        checked: !!win.state.schedulerEnabled
                        onToggled: act("set_scheduler", {
                            enabled: checked
                        })
                    }
                }
                Rectangle {
                    anchors.bottom: parent.bottom
                    width: parent.width
                    height: 1
                    color: "#e9eaf3"
                }
            }
            StackLayout {
                currentIndex: win.page
                Layout.fillWidth: true
                Layout.fillHeight: true
                // Overview and program manager share the same live program panel.
                Item {
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 28
                        spacing: 22
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 16
                            Stat {
                                caption: "全部节目"
                                value: String(programs.length)
                                detail: "包括全部节目单"
                                symbol: "☷"
                            }
                            Stat {
                                caption: "已启用节目"
                                value: String(programs.filter(function (p) {
                                    return p.enabled;
                                }).length)
                                detail: plans.length + " 个节目单"
                                symbol: "✓"
                                symbolColor: "#31a785"
                            }
                            Stat {
                                caption: "下一次播出"
                                value: win.width < 1200 ? (win.state.next || "暂无安排").split(" · ")[0].split(" ").pop() : (win.state.next || "暂无安排").split(" · ")[0]
                                detail: (win.state.next || "").split(" · ")[1] || "按本机时间执行"
                                symbol: "◷"
                                symbolColor: "#e5a24b"
                            }
                        }
                        Panel {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 20
                                spacing: 12
                                RowLayout {
                                    Layout.fillWidth: true
                                    ColumnLayout {
                                        spacing: 4
                                        Layout.fillWidth: true
                                        Label {
                                            text: "定时节目"
                                            font.pixelSize: 18
                                            font.bold: true
                                            color: win.ink
                                        }
                                        SmallText {
                                            text: "节目按时间和重复规则自动执行。"
                                        }
                                    }
                                    Item {
                                        Layout.fillWidth: true
                                    }
                                    QuietButton {
                                        text: "管理节目单"
                                        objectName: "planManage"
                                        onClicked: plansDialog.open()
                                    }
                                    Button {
                                        text: "＋ 添加节目"
                                        objectName: "newProgram"
                                        highlighted: true
                                        onClicked: edit(null)
                                    }
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 10
                                    TextField {
                                        id: overviewSearch
                                        Layout.fillWidth: true
                                        objectName: "searchInput"
                                        placeholderText: "搜索节目名称、时间或媒体路径"
                                        text: win.search
                                        onTextEdited: win.search = text
                                        selectByMouse: true
                                    }
                                    ComboBox {
                                        Layout.preferredWidth: 180
                                        model: ["全部节目单"].concat(plans.map(function (p) {
                                            return p.name;
                                        }))
                                        onActivated: win.planFilter = currentIndex > 0 ? plans[currentIndex - 1].id : ""
                                    }
                                    ComboBox {
                                        Layout.preferredWidth: 135
                                        model: ["手动顺序", "按时间排序", "按名称排序", "按节目单排序"]
                                        currentIndex: ["manual", "time", "name", "plan"].indexOf(win.sortMode)
                                        onActivated: win.sortMode = ["manual", "time", "name", "plan"][currentIndex]
                                    }
                                    QuietButton {
                                        text: win.sortDescending ? "↓" : "↑"
                                        enabled: win.sortMode !== "manual"
                                        onClicked: win.sortDescending = !win.sortDescending
                                        ToolTip.visible: hovered
                                        ToolTip.text: "切换升序 / 降序"
                                    }
                                    QuietButton {
                                        text: "导入"
                                        onClicked: win.openBackup("import")
                                    }
                                    QuietButton {
                                        text: "导出"
                                        onClicked: win.openBackup("export")
                                    }
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 34
                                    SmallText {
                                        text: "启用"
                                        Layout.preferredWidth: 58
                                    }
                                    SmallText {
                                        text: "播出时间"
                                        Layout.preferredWidth: 95
                                    }
                                    SmallText {
                                        text: "节目 / 所属节目单"
                                        Layout.fillWidth: true
                                    }
                                    SmallText {
                                        text: "重复规则"
                                        Layout.preferredWidth: 125
                                    }
                                    SmallText {
                                        text: "操作"
                                        Layout.preferredWidth: 276
                                    }
                                }
                                Rectangle {
                                    Layout.fillWidth: true
                                    height: 1
                                    color: "#eceef5"
                                }
                                ListView {
                                    id: overviewList
                                    Layout.fillWidth: true
                                    Layout.fillHeight: true
                                    clip: true
                                    model: filtered
                                    spacing: 2
                                    ScrollBar.vertical: ScrollBar {}
                                    delegate: Rectangle {
                                        required property var modelData
                                        required property int index
                                        width: overviewList.width
                                        TapHandler {
                                            onTapped: win.selectedProgramId = modelData.id
                                        }
                                        height: 72
                                        color: win.selectedProgramId === modelData.id ? "#f0edff" : index % 2 ? "#fbfbfe" : "white"
                                        radius: 8
                                        RowLayout {
                                            anchors.fill: parent
                                            spacing: 0
                                            Switch {
                                                Layout.preferredWidth: 58
                                                checked: modelData.enabled
                                                onToggled: act("toggle_program", {
                                                    id: modelData.id,
                                                    enabled: checked
                                                })
                                            }
                                            ColumnLayout {
                                                Layout.preferredWidth: 95
                                                spacing: 2
                                                Label {
                                                    text: (modelData.times && modelData.times.length ? modelData.times[0] : modelData.time) || "—"
                                                    color: win.ink
                                                    font.bold: true
                                                    font.pixelSize: 15
                                                }
                                                SmallText {
                                                    text: (modelData.times || []).length > 1 ? "共 " + modelData.times.length + " 个时点" : kindLabel(modelData.kind)
                                                }
                                            }
                                            ColumnLayout {
                                                Layout.fillWidth: true
                                                Layout.minimumWidth: 0
                                                spacing: 3
                                                Label {
                                                    text: modelData.name
                                                    Layout.minimumWidth: 0
                                                    color: win.ink
                                                    font.bold: true
                                                    elide: Text.ElideRight
                                                    Layout.fillWidth: true
                                                }
                                                SmallText {
                                                    text: planName(modelData.plan_id)
                                                    elide: Text.ElideRight
                                                    Layout.fillWidth: true
                                                }
                                            }
                                            SmallText {
                                                text: weekLabel(modelData)
                                                Layout.preferredWidth: 125
                                                elide: Text.ElideRight
                                            }
                                            RowLayout {
                                                Layout.preferredWidth: 276
                                                spacing: 0
                                                QuietButton {
                                                    text: "↑"
                                                    Layout.preferredWidth: 30
                                                    enabled: win.sortMode === "manual"
                                                    onClicked: act("move_program", {
                                                        id: modelData.id,
                                                        delta: -1
                                                    })
                                                }
                                                QuietButton {
                                                    text: "↓"
                                                    Layout.preferredWidth: 30
                                                    enabled: win.sortMode === "manual"
                                                    onClicked: act("move_program", {
                                                        id: modelData.id,
                                                        delta: 1
                                                    })
                                                }
                                                QuietButton {
                                                    text: "▶"
                                                    Layout.preferredWidth: 38
                                                    ToolTip.visible: hovered
                                                    ToolTip.text: modelData.kind === "audio" || modelData.kind === "tts" ? "立即播放" : "立即执行"
                                                    onClicked: act("play_program", {
                                                        id: modelData.id
                                                    })
                                                }
                                                QuietButton {
                                                    text: "修改"
                                                    Layout.preferredWidth: 52
                                                    onClicked: edit(modelData)
                                                }
                                                QuietButton {
                                                    text: "复制"
                                                    Layout.preferredWidth: 52
                                                    onClicked: act("duplicate_program", {
                                                        id: modelData.id
                                                    })
                                                }
                                                QuietButton {
                                                    text: "删除"
                                                    Layout.preferredWidth: 52
                                                    onClicked: {
                                                        deleteDialog.programId = modelData.id;
                                                        deleteDialog.programName = modelData.name;
                                                        deleteDialog.open();
                                                    }
                                                }
                                            }
                                        }
                                    }
                                    ColumnLayout {
                                        anchors.centerIn: parent
                                        visible: filtered.length === 0
                                        spacing: 10
                                        Label {
                                            text: programs.length ? "没有匹配的节目" : "暂无节目"
                                            color: win.ink
                                            font.pixelSize: 20
                                            Layout.alignment: Qt.AlignHCenter
                                        }
                                        SmallText {
                                            text: programs.length ? "请调整搜索条件或选择其他节目单。" : "请选择“添加节目”或导入节目单。"
                                            Layout.alignment: Qt.AlignHCenter
                                        }
                                        RowLayout {
                                            Layout.alignment: Qt.AlignHCenter
                                            Button {
                                                text: "＋ 添加节目"
                                                objectName: "newProgram"
                                                highlighted: true
                                                onClicked: edit(null)
                                            }
                                            QuietButton {
                                                text: "添加示例节目单"
                                                visible: !programs.length
                                                onClicked: act("create_examples")
                                            }
                                        }
                                    }
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    SmallText {
                                        text: "共 " + filtered.length + " 个节目"
                                    }
                                    Item {
                                        Layout.fillWidth: true
                                    }
                                    SmallText {
                                        text: "修改自动保存"
                                    }
                                }
                            }
                        }
                    }
                }
                Item {
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 28
                        spacing: 16
                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                text: "全部节目"
                                font.bold: true
                                font.pixelSize: 20
                                color: win.ink
                                Layout.fillWidth: true
                            }
                            QuietButton {
                                text: "管理节目单"
                                objectName: "planManage"
                                onClicked: plansDialog.open()
                            }
                            Button {
                                text: "＋ 添加节目"
                                objectName: "newProgram"
                                highlighted: true
                                onClicked: edit(null)
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            TextField {
                                id: programSearch
                                Layout.fillWidth: true
                                text: win.search
                                placeholderText: "搜索节目名称、时间或路径"
                                onTextEdited: win.search = text
                            }
                            ComboBox {
                                Layout.preferredWidth: 180
                                model: ["全部节目单"].concat(plans.map(function (p) {
                                    return p.name;
                                }))
                                onActivated: win.planFilter = currentIndex > 0 ? plans[currentIndex - 1].id : ""
                            }
                            ComboBox {
                                Layout.preferredWidth: 135
                                model: ["手动顺序", "按时间排序", "按名称排序", "按节目单排序"]
                                currentIndex: ["manual", "time", "name", "plan"].indexOf(win.sortMode)
                                onActivated: win.sortMode = ["manual", "time", "name", "plan"][currentIndex]
                            }
                            QuietButton {
                                text: win.sortDescending ? "↓" : "↑"
                                enabled: win.sortMode !== "manual"
                                onClicked: win.sortDescending = !win.sortDescending
                            }
                            QuietButton {
                                text: "导入"
                                onClicked: win.openBackup("import")
                            }
                            QuietButton {
                                text: "导出"
                                onClicked: win.openBackup("export")
                            }
                        }
                        ListView {
                            id: allPrograms
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true
                            model: filtered
                            spacing: 10
                            ScrollBar.vertical: ScrollBar {}
                            delegate: Panel {
                                required property var modelData
                                width: allPrograms.width
                                color: win.selectedProgramId === modelData.id ? "#f0edff" : "white"
                                TapHandler {
                                    onTapped: win.selectedProgramId = modelData.id
                                }
                                height: 94
                                RowLayout {
                                    anchors.fill: parent
                                    anchors.margins: 16
                                    spacing: 16
                                    Switch {
                                        checked: modelData.enabled
                                        onToggled: act("toggle_program", {
                                            id: modelData.id,
                                            enabled: checked
                                        })
                                    }
                                    ColumnLayout {
                                        Layout.preferredWidth: 115
                                        spacing: 4
                                        Label {
                                            text: modelData.time
                                            font.pixelSize: 20
                                            font.bold: true
                                            color: win.accent
                                        }
                                        SmallText {
                                            text: weekLabel(modelData)
                                        }
                                    }
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        Layout.minimumWidth: 0
                                        spacing: 5
                                        Label {
                                            text: modelData.name
                                            font.bold: true
                                            color: win.ink
                                            elide: Text.ElideRight
                                            Layout.fillWidth: true
                                        }
                                        SmallText {
                                            text: planName(modelData.plan_id) + "  ·  " + kindLabel(modelData.kind) + "  ·  音量 " + modelData.volume + "%"
                                            Layout.fillWidth: true
                                            elide: Text.ElideRight
                                        }
                                    }
                                    QuietButton {
                                        text: "↑"
                                        Layout.preferredWidth: 30
                                        enabled: win.sortMode === "manual"
                                        onClicked: act("move_program", {
                                            id: modelData.id,
                                            delta: -1
                                        })
                                    }
                                    QuietButton {
                                        text: "↓"
                                        Layout.preferredWidth: 30
                                        enabled: win.sortMode === "manual"
                                        onClicked: act("move_program", {
                                            id: modelData.id,
                                            delta: 1
                                        })
                                    }
                                    QuietButton {
                                        text: modelData.kind === "audio" || modelData.kind === "tts" ? "立即播放" : "立即执行"
                                        onClicked: act("play_program", {
                                            id: modelData.id
                                        })
                                    }
                                    QuietButton {
                                        text: "修改"
                                        onClicked: edit(modelData)
                                    }
                                    QuietButton {
                                        text: "复制"
                                        onClicked: act("duplicate_program", {
                                            id: modelData.id
                                        })
                                    }
                                    QuietButton {
                                        text: "删除"
                                        onClicked: {
                                            deleteDialog.programId = modelData.id;
                                            deleteDialog.programName = modelData.name;
                                            deleteDialog.open();
                                        }
                                    }
                                }
                            }
                            Label {
                                anchors.centerIn: parent
                                visible: !filtered.length
                                text: "暂无节目"
                                color: win.muted
                            }
                        }
                    }
                }
                Item {
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 28
                        spacing: 14
                        TabBar {
                            id: mediaTabs
                            Layout.fillWidth: true
                            TabButton {
                                text: "立即播"
                            }
                            TabButton {
                                text: "节目媒体"
                            }
                            TabButton {
                                text: "内置音频"
                            }
                        }
                        StackLayout {
                            currentIndex: mediaTabs.currentIndex
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            LivePanel {
                                library: win.state.live || ({})
                                playing: (win.state.playing || {}).immediate || ({})
                                onCommand: function (action, payload) {
                                    win.act(action, payload);
                                }
                            }
                            Item {
                                ColumnLayout {
                                    anchors.fill: parent
                                    anchors.margins: 28
                                    spacing: 18
                                    Label {
                                        text: "节目使用的媒体"
                                        font.pixelSize: 20
                                        font.bold: true
                                        color: win.ink
                                    }
                                    SmallText {
                                        text: "节目使用的媒体文件与目录。在“修改节目”中更换媒体。"
                                    }
                                    ListView {
                                        id: mediaList
                                        Layout.fillWidth: true
                                        Layout.fillHeight: true
                                        clip: true
                                        model: media
                                        spacing: 10
                                        ScrollBar.vertical: ScrollBar {}
                                        delegate: Panel {
                                            required property string modelData
                                            width: mediaList.width
                                            height: 78
                                            RowLayout {
                                                anchors.fill: parent
                                                anchors.margins: 16
                                                spacing: 16
                                                Label {
                                                    text: "♫"
                                                    color: win.accent
                                                    font.pixelSize: 26
                                                }
                                                ColumnLayout {
                                                    Layout.fillWidth: true
                                                    spacing: 4
                                                    Label {
                                                        text: modelData.replace(/\\/g, "/").split("/").pop()
                                                        color: win.ink
                                                        font.bold: true
                                                        elide: Text.ElideRight
                                                        Layout.fillWidth: true
                                                    }
                                                    SmallText {
                                                        text: modelData
                                                        elide: Text.ElideMiddle
                                                        Layout.fillWidth: true
                                                    }
                                                }
                                            }
                                        }
                                        Label {
                                            anchors.centerIn: parent
                                            visible: !media.length
                                            text: "添加音频节目后，媒体将在这里显示"
                                            color: win.muted
                                        }
                                    }
                                }
                            }
                            BuiltinLibrary {
                                tracks: win.state.builtinMedia || []
                                onCommand: function (action, payload) {
                                    win.act(action, payload);
                                }
                                onChosen: function (paths) {
                                    win.acceptBuiltins(paths);
                                }
                            }
                        }
                    }
                }
                Item {
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 28
                        spacing: 18
                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                text: "调度与播放记录"
                                font.pixelSize: 20
                                font.bold: true
                                color: win.ink
                                Layout.fillWidth: true
                            }
                            SmallText {
                                text: "共 " + (win.state.logs || []).length + " 条"
                            }
                            QuietButton {
                                text: "打开日志文件"
                                onClicked: act("open_log")
                            }
                        }
                        Panel {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            ListView {
                                id: logList
                                anchors.fill: parent
                                anchors.margins: 18
                                clip: true
                                model: win.state.logs || []
                                spacing: 12
                                ScrollBar.vertical: ScrollBar {}
                                delegate: RowLayout {
                                    required property var modelData
                                    width: logList.width
                                    spacing: 16
                                    SmallText {
                                        text: modelData.time
                                        Layout.preferredWidth: 170
                                    }
                                    Label {
                                        text: modelData.level.toUpperCase()
                                        color: modelData.level.toLowerCase() === "error" ? "#d66370" : win.accent
                                        font.pixelSize: 11
                                        Layout.preferredWidth: 55
                                    }
                                    Label {
                                        text: modelData.message
                                        wrapMode: Text.Wrap
                                        Layout.fillWidth: true
                                        color: win.ink
                                        font.pixelSize: 12
                                    }
                                }
                                Label {
                                    anchors.centerIn: parent
                                    visible: !(win.state.logs || []).length
                                    text: "暂无运行记录"
                                    color: win.muted
                                }
                            }
                        }
                    }
                }
                ScrollView {
                    clip: true
                    contentWidth: availableWidth
                    ColumnLayout {
                        width: parent.width
                        spacing: 18
                        Item {
                            height: 10
                        }
                        Panel {
                            Layout.fillWidth: true
                            Layout.leftMargin: 28
                            Layout.rightMargin: 28
                            implicitHeight: settingsColumn.implicitHeight + 40
                            ColumnLayout {
                                id: settingsColumn
                                anchors.fill: parent
                                anchors.margins: 20
                                spacing: 12
                                Label {
                                    text: "运行偏好"
                                    font.pixelSize: 18
                                    font.bold: true
                                    color: win.ink
                                }
                                CheckBox {
                                    id: traySetting
                                    objectName: "traySetting"
                                    text: "关闭窗口后继续在托盘运行"
                                    checked: !!(win.state.settings || {}).closeToTray
                                }
                                ComboBox {
                                    id: defaultDeviceSetting
                                    Layout.fillWidth: true
                                    model: ["系统默认音频设备"].concat((win.state.devices || []).map(function (d) {
                                        return d.name;
                                    }))
                                    currentIndex: {
                                        var id = (win.state.settings || {}).defaultDevice || "";
                                        var ds = win.state.devices || [];
                                        for (var i = 0; i < ds.length; i++)
                                            if (ds[i].id === id)
                                                return i + 1;
                                        return 0;
                                    }
                                }
                                SectionLabel {
                                    text: "登录自启动"
                                }
                                CheckBox {
                                    id: startupSetting
                                    objectName: "startupSetting"
                                    text: "登录 Windows 后启动"
                                    checked: !!(win.state.settings || {}).startAtLogin
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    Label {
                                        objectName: "startupActualStatus"
                                        Layout.fillWidth: true
                                        font.pixelSize: 13
                                        text: {
                                            var status = win.state.startupStatus || {};
                                            var actual = "无法读取";
                                            if (status.supported && status.blockedByWindows === true)
                                                actual = "Windows 已禁用";
                                            else if (status.supported && !status.error && status.blockedByWindows === false) {
                                                if (status.enabled === true && status.matchesCurrentPath === true)
                                                    actual = "已启用";
                                                else if ((win.state.settings || {}).startAtLogin && status.matchesCurrentPath === false)
                                                    actual = "启动路径需要修复";
                                                else
                                                    actual = "未启用";
                                            }
                                            return "系统实际状态：" + actual;
                                        }
                                        color: (win.state.startupStatus || {}).blockedByWindows === true ? "#b26d2b" : win.ink
                                    }
                                    QuietButton {
                                        objectName: "startupRefresh"
                                        text: "刷新"
                                        onClicked: act("get_startup_status")
                                    }
                                    QuietButton {
                                        objectName: "startupRepair"
                                        text: "修复启动路径"
                                        visible: !!(win.state.settings || {}).startAtLogin && !!(win.state.startupStatus || {}).supported && (win.state.startupStatus || {}).blockedByWindows === false
                                        enabled: !(win.state.startupStatus || {}).error && !(win.state.startupStatus || {}).matchesCurrentPath
                                        onClicked: act("repair_startup")
                                    }
                                }
                                SmallText {
                                    text: "启用后检查启动路径；系统禁用状态不会自动更改。"
                                    Layout.fillWidth: true
                                    wrapMode: Text.Wrap
                                }
                                SmallText {
                                    visible: !!(win.state.startupStatus || {}).error
                                    text: (win.state.startupStatus || {}).error || ""
                                    Layout.fillWidth: true
                                    wrapMode: Text.Wrap
                                }
                                RowLayout {
                                    Label {
                                        text: "插播时定时播音量（%）"
                                        color: win.ink
                                    }
                                    SpinBox {
                                        id: duckSetting
                                        objectName: "duckSetting"
                                        from: 0
                                        to: 100
                                        value: (win.state.settings || {}).duckVolume === undefined ? 30 : win.state.settings.duckVolume
                                        editable: true
                                    }
                                }
                                SectionLabel {
                                    text: "系统主音量"
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    visible: !!(win.state.systemVolume || {}).available
                                    Slider {
                                        Layout.fillWidth: true
                                        from: 0
                                        to: 100
                                        value: (win.state.systemVolume || {}).volume || 0
                                        onMoved: act("set_system_volume", {
                                            volume: Math.round(value)
                                        })
                                    }
                                    SmallText {
                                        text: String(Math.round((win.state.systemVolume || {}).volume || 0)) + "%"
                                    }
                                    CheckBox {
                                        text: "静音"
                                        checked: !!(win.state.systemVolume || {}).muted
                                        onToggled: act("set_system_mute", {
                                            muted: checked
                                        })
                                    }
                                }
                                SmallText {
                                    visible: !(win.state.systemVolume || {}).available
                                    text: (win.state.systemVolume || {}).error || "当前系统不提供主音量控制"
                                    wrapMode: Text.Wrap
                                    Layout.fillWidth: true
                                }
                                QuietButton {
                                    text: "刷新系统音量"
                                    onClicked: act("get_system_volume")
                                }
                                QuietButton {
                                    text: "立即锁屏"
                                    visible: !!(win.state.systemVolume || {}).available
                                    onClicked: act("lock_workstation")
                                }
                                Button {
                                    text: "管理节假日与调休"
                                    flat: true
                                    onClicked: win.page = 5
                                }
                                Button {
                                    text: "保存设置"
                                    objectName: "settingsSave"
                                    highlighted: true
                                    onClicked: {
                                        try {
                                            act("save_settings", {
                                                closeToTray: traySetting.checked,
                                                startAtLogin: startupSetting.checked,
                                                defaultDevice: defaultDeviceSetting.currentIndex > 0 ? (win.state.devices || [])[defaultDeviceSetting.currentIndex - 1].id : "",
                                                duckVolume: duckSetting.value,
                                                exclude_windows: (win.state.settings || {}).exclude_windows || [],
                                                calendar_overrides: (win.state.settings || {}).calendar_overrides || {}
                                            });
                                        } catch (e) {
                                            showError("设置保存失败：" + e);
                                        }
                                    }
                                }
                            }
                        }
                        Panel {
                            Layout.fillWidth: true
                            Layout.leftMargin: 28
                            Layout.rightMargin: 28
                            implicitHeight: aboutColumn.implicitHeight + 40
                            ColumnLayout {
                                id: aboutColumn
                                anchors.fill: parent
                                anchors.margins: 20
                                spacing: 12
                                Label {
                                    text: "本地数据与运行环境"
                                    font.pixelSize: 18
                                    font.bold: true
                                    color: win.ink
                                }
                                SmallText {
                                    text: "数据目录"
                                }
                                Label {
                                    text: win.state.dataPath || "—"
                                    color: win.ink
                                    wrapMode: Text.Wrap
                                    Layout.fillWidth: true
                                    font.pixelSize: 12
                                }
                                SmallText {
                                    text: "图形设备"
                                }
                                Label {
                                    text: win.state.renderer || "设备信息由运行环境提供"
                                    color: win.ink
                                    wrapMode: Text.Wrap
                                    Layout.fillWidth: true
                                    font.pixelSize: 12
                                }
                                SmallText {
                                    text: "节目数量无固定上限，受可用存储与系统资源约束。"
                                }
                                SmallText {
                                    objectName: "schedulerServiceInfo"
                                    Layout.fillWidth: true
                                    wrapMode: Text.Wrap
                                    text: {
                                        var service = win.state.schedulerService || {};
                                        if (service.intervalMs === undefined)
                                            return "调度检查间隔尚未读取；实际延迟记录于日志。";
                                        return "调度检查间隔" + service.intervalMs + "毫秒；实际延迟记录于日志。";
                                    }
                                }
                                SmallText {
                                    text: "单文件便携程序"
                                }
                                Button {
                                    text: "打开数据目录"
                                    flat: true
                                    onClicked: act("open_data")
                                }
                                Button {
                                    text: "使用说明"
                                    flat: true
                                    onClicked: act("open_help")
                                }
                            }
                        }
                        Item {
                            height: 12
                        }
                    }
                }
                Loader {
                    id: calendarLoader
                    active: win.page === 5
                    source: active ? "CalendarPage.qml" : ""
                    onLoaded: {
                        item.controller = backend;
                        item.appState = Qt.binding(function () {
                            return win.state;
                        });
                    }
                    Connections {
                        target: calendarLoader.item
                        ignoreUnknownSignals: true
                        function onErrorOccurred(message) {
                            win.showError(message);
                        }
                    }
                }
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: win.state.powerPending ? 52 : 0
                visible: !!win.state.powerPending
                color: "#fff0df"
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 28
                    anchors.rightMargin: 28
                    Label {
                        text: {
                            var tick = win.state.now;
                            if (!win.state.powerPending)
                                return "";
                            var p = win.state.powerPending;
                            var seconds = Math.max(0, Math.ceil((new Date(p.at) - new Date()) / 1000));
                            return (p.name || "系统节目") + " · " + (p.action === "restart" ? "重启" : "关机") + "将在 " + seconds + " 秒后执行";
                        }
                        Layout.fillWidth: true
                        color: "#a36a20"
                    }
                    Button {
                        text: "取消系统动作"
                        onClicked: act("cancel_power")
                    }
                }
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 89
                color: "white"
                Rectangle {
                    width: parent.width
                    height: 1
                    color: "#e9eaf3"
                }
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 28
                    anchors.rightMargin: 28
                    spacing: 16
                    Rectangle {
                        width: 44
                        height: 44
                        radius: 13
                        color: "#f0edff"
                        Label {
                            anchors.centerIn: parent
                            text: "♫"
                            font.pixelSize: 25
                            color: win.accent
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4
                        Label {
                            text: playing.name || "当前未播放"
                            font.bold: true
                            color: win.ink
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                        }
                        SmallText {
                            text: ({
                                    playing: "正在播放",
                                    paused: "已暂停",
                                    stopped: "已停止"
                                })[playing.status] || "已停止"
                        }
                    }
                    QuietButton {
                        text: playing.status === "paused" ? "▶ 继续" : "Ⅱ 暂停"
                        enabled: !!playing.name
                        onClicked: act(playing.status === "paused" ? "resume" : "pause")
                    }
                    QuietButton {
                        text: "■ 停止"
                        enabled: !!playing.name
                        onClicked: act("stop")
                    }
                    SmallText {
                        text: "音量"
                    }
                    Slider {
                        Layout.preferredWidth: 130
                        from: 0
                        to: 100
                        value: playing.volume === undefined ? 80 : playing.volume
                        onMoved: act("set_volume", {
                            volume: Math.round(value)
                        })
                    }
                    SmallText {
                        text: Math.round(playing.volume === undefined ? 80 : playing.volume) + "%"
                        Layout.preferredWidth: 34
                    }
                }
            }
        }
    }
    Dialog {
        id: editor
        objectName: "editor"
        parent: Overlay.overlay
        modal: true
        width: Math.min(850, win.width - 70)
        height: Math.min(790, win.height - 50)
        x: (parent.width - width) / 2
        y: (parent.height - height) / 2
        title: editing.id ? "修改节目" : "添加节目"
        standardButtons: Dialog.NoButton
        footer: DialogButtonBox {
            Button {
                text: "保存"
                objectName: "editorSave"
                highlighted: true
                DialogButtonBox.buttonRole: DialogButtonBox.ActionRole
                onClicked: {
                    if (editor.submit())
                        editor.close();
                }
            }
            Button {
                text: "取消"
                objectName: "editorCancel"
                flat: true
                DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
            }
            onRejected: editor.reject()
        }
        function submit() {
            var ds = [];
            for (var i = 0; i < 7; i++)
                if (days.itemAt(i).checked)
                    ds.push(i);
            var times = timesInput.text.split(/[\n,，]+/).map(function (s) {
                return s.trim();
            }).filter(Boolean);
            var md = monthDays.text.trim() ? monthDays.text.split(/[,，\s]+/).map(Number) : [];
            var exclusions = [];
            try {
                exclusions = JSON.parse(programExcludeInput.text || "[]");
            } catch (e) {
                showError("节目禁播时段 JSON 格式错误：" + e);
                return false;
            }
            var value = {
                dates: datesInput.text.split(/[\n,，]+/).map(function (s) {
                    return s.trim();
                }).filter(Boolean),
                excluded_dates: excludedDatesInput.text.split(/[\n,，]+/).map(function (s) {
                    return s.trim();
                }).filter(Boolean),
                exclude_windows: exclusions,
                id: editing.id,
                plan_id: plans[planInput.currentIndex] ? plans[planInput.currentIndex].id : "",
                name: nameInput.text.trim(),
                time: times[0] || "",
                times: times,
                weekdays: ds,
                month_days: md,
                kind: kindValues[kindInput.currentIndex],
                files: filesInput.text.split("\n").map(function (s) {
                    return s.trim();
                }).filter(Boolean),
                text: textInput.text,
                volume: volumeInput.value,
                repeat: repeatInput.value,
                stop_mode: stopModes[stopModeInput.currentIndex],
                track_limit: trackLimitInput.value,
                duration: durationInput.value,
                fade_in: fadeIn.value,
                fade_out: fadeOut.value,
                priority: priorityInput.value,
                shuffle: shuffleInput.checked,
                enabled: enabledInput.checked,
                start_date: startDate.text.trim(),
                datetime_from: startClock.text.trim() ? startDate.text.trim() + "T" + startClock.text.trim() : "",
                end_date: endDate.text.trim(),
                datetime_to: endClock.text.trim() ? endDate.text.trim() + "T" + endClock.text.trim() : "",
                playback_mode: queueInput.currentIndex === 1 ? "queue" : "interrupt",
                output_device: deviceInput.currentIndex > 0 ? (win.state.devices || [])[deviceInput.currentIndex - 1].id : ""
            };
            return act(editing.id ? "update_program" : "add_program", value);
        }
        contentItem: ScrollView {
            id: editorScroll
            clip: true
            contentWidth: availableWidth
            ColumnLayout {
                width: parent.width
                spacing: 12
                RowLayout {
                    Layout.fillWidth: true
                    ColumnLayout {
                        Layout.fillWidth: true
                        SmallText {
                            text: "节目名称"
                        }
                        TextField {
                            id: nameInput
                            objectName: "nameInput"
                            Layout.fillWidth: true
                            placeholderText: "例如：晨间广播"
                            selectByMouse: true
                        }
                    }
                    ColumnLayout {
                        Layout.preferredWidth: 200
                        SmallText {
                            text: "所属节目单"
                        }
                        ComboBox {
                            id: planInput
                            Layout.fillWidth: true
                            model: plans.map(function (p) {
                                return p.name;
                            })
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    ColumnLayout {
                        Layout.fillWidth: true
                        SmallText {
                            text: "节目类型"
                        }
                        ComboBox {
                            id: kindInput
                            objectName: "kindInput"
                            Layout.fillWidth: true
                            model: ["音频播放", "语音朗读", "运行程序", "停止播放", "调整音量", "关机", "重启"]
                        }
                    }
                    CheckBox {
                        id: enabledInput
                        text: "启用节目"
                        checked: true
                    }
                }
                SectionLabel {
                    text: "播出安排"
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 14
                    ColumnLayout {
                        Layout.fillWidth: true
                        RowLayout {
                            Layout.fillWidth: true
                            SmallText {
                                text: "开始时间 · 每行一个，格式 HH:MM:SS"
                                Layout.fillWidth: true
                            }
                            QuietButton {
                                text: "批量生成…"
                                onClicked: timeGenerator.open()
                            }
                        }
                        Basic.TextArea {
                            id: timesInput
                            padding: 12
                            topInset: 0
                            bottomInset: 0
                            objectName: "timesInput"
                            Layout.fillWidth: true
                            Layout.preferredHeight: 80
                            selectByMouse: true
                            wrapMode: Text.Wrap
                            background: Rectangle {
                                color: "#f7f8fc"
                                radius: 8
                                border.color: "#e6e7f1"
                            }
                        }
                    }
                    ColumnLayout {
                        Layout.preferredWidth: 276
                        SmallText {
                            text: "遇到正在播放的节目"
                        }
                        ComboBox {
                            id: queueInput
                            Layout.fillWidth: true
                            model: ["准点播出（抢占）", "排队，等当前节目结束"]
                        }
                    }
                }
                Flow {
                    Layout.fillWidth: true
                    spacing: 1
                    Repeater {
                        id: days
                        model: ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
                        delegate: CheckBox {
                            required property string modelData
                            text: modelData
                            enabled: !monthDays.text.trim() && !datesInput.text.trim()
                            font.pixelSize: 12
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12
                    ColumnLayout {
                        Layout.fillWidth: true
                        SmallText {
                            text: "每月几号 · 填写后按月执行，忽略星期"
                        }
                        TextField {
                            id: monthDays
                            Layout.fillWidth: true
                            placeholderText: "留空按星期执行"
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        SmallText {
                            text: "开始日期 · 可选"
                        }
                        TextField {
                            id: startDate
                            Layout.fillWidth: true
                            placeholderText: "YYYY-MM-DD"
                        }
                        TextField {
                            id: startClock
                            Layout.fillWidth: true
                            placeholderText: "起始时刻，可选 HH:MM:SS"
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        SmallText {
                            text: "结束日期 · 可选"
                        }
                        TextField {
                            id: endDate
                            Layout.fillWidth: true
                            placeholderText: "YYYY-MM-DD"
                        }
                        TextField {
                            id: endClock
                            Layout.fillWidth: true
                            placeholderText: "结束时刻，可选 HH:MM:SS"
                        }
                    }
                }
                SectionLabel {
                    text: "播放内容"
                    visible: kindInput.currentIndex < 3
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: kindInput.currentIndex === 0
                    spacing: 8
                    RowLayout {
                        Layout.fillWidth: true
                        SmallText {
                            text: "媒体文件或目录 · 每行一条路径，目录递归扫描"
                            Layout.fillWidth: true
                        }
                        QuietButton {
                            text: "内置音频…"
                            onClicked: builtinDialog.open()
                        }
                        QuietButton {
                            text: "选择目录…"
                            onClicked: act("open_directory")
                        }
                        QuietButton {
                            text: "选择文件…"
                            onClicked: act("open_files")
                        }
                    }
                    Basic.TextArea {
                        id: filesInput
                        padding: 12
                        topInset: 0
                        bottomInset: 0
                        objectName: "filesInput"
                        Layout.fillWidth: true
                        Layout.preferredHeight: 100
                        placeholderText: "C:/Music/铃声.mp3\nC:/Music/晨间广播"
                        selectByMouse: true
                        wrapMode: Text.Wrap
                        background: Rectangle {
                            color: "#f7f8fc"
                            radius: 8
                            border.color: "#e6e7f1"
                        }
                    }
                    ComboBox {
                        id: deviceInput
                        Layout.fillWidth: true
                        model: ["系统默认音频设备"].concat((win.state.devices || []).map(function (d) {
                            return d.name;
                        }))
                    }
                }
                Basic.TextArea {
                    id: textInput
                    padding: 12
                    topInset: 0
                    bottomInset: 0
                    objectName: "textInput"
                    visible: kindInput.currentIndex === 1 || kindInput.currentIndex === 2
                    Layout.fillWidth: true
                    Layout.preferredHeight: 100
                    placeholderText: kindInput.currentIndex === 1 ? "输入要朗读的内容" : 'JSON 参数数组，例如 ["notepad.exe", "C:/notes.txt"]'
                    selectByMouse: true
                    wrapMode: Text.Wrap
                    background: Rectangle {
                        color: "#f7f8fc"
                        radius: 8
                        border.color: "#e6e7f1"
                    }
                }
                Label {
                    visible: kindInput.currentIndex >= 5
                    text: "该动作会影响系统运行，请确认时间及日期安排。"
                    color: "#b77b25"
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }
                SectionLabel {
                    text: "播放参数"
                }
                RowLayout {
                    Layout.fillWidth: true
                    ComboBox {
                        id: stopModeInput
                        objectName: "stopModeInput"
                        Layout.fillWidth: true
                        model: ["列表循环遍数", "播放首数", "播放秒数"]
                    }
                    ColumnLayout {
                        visible: stopModeInput.currentIndex === 1
                        Layout.fillWidth: true
                        SmallText {
                            text: "播放首数 · 0 无限"
                        }
                        SpinBox {
                            id: trackLimitInput
                            from: 0
                            to: 100000
                            editable: true
                            Layout.fillWidth: true
                        }
                    }
                }
                GridLayout {
                    columns: 3
                    Layout.fillWidth: true
                    columnSpacing: 16
                    rowSpacing: 10
                    ColumnLayout {
                        Layout.fillWidth: true
                        SmallText {
                            text: "音量（%）"
                        }
                        SpinBox {
                            id: volumeInput
                            from: 0
                            to: 100
                            value: 80
                            editable: true
                            Layout.fillWidth: true
                        }
                    }
                    ColumnLayout {
                        visible: stopModeInput.currentIndex === 0
                        Layout.fillWidth: true
                        SmallText {
                            text: "列表循环遍数 · 0 无限"
                        }
                        SpinBox {
                            id: repeatInput
                            from: 0
                            to: 100000
                            value: 1
                            editable: true
                            Layout.fillWidth: true
                        }
                    }
                    ColumnLayout {
                        visible: stopModeInput.currentIndex === 2
                        Layout.fillWidth: true
                        SmallText {
                            text: "限制时长（秒）· 0 不限"
                        }
                        SpinBox {
                            id: durationInput
                            from: 0
                            to: 864000
                            editable: true
                            Layout.fillWidth: true
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        SmallText {
                            text: "淡入（秒）"
                        }
                        SpinBox {
                            id: fadeIn
                            from: 0
                            to: 3600
                            editable: true
                            Layout.fillWidth: true
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        SmallText {
                            text: "淡出（秒）"
                        }
                        SpinBox {
                            id: fadeOut
                            from: 0
                            to: 3600
                            editable: true
                            Layout.fillWidth: true
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        SmallText {
                            text: "优先级"
                        }
                        SpinBox {
                            id: priorityInput
                            from: -100
                            to: 100
                            editable: true
                            Layout.fillWidth: true
                        }
                    }
                }
                CheckBox {
                    id: shuffleInput
                    text: "随机播放媒体列表"
                }
                SectionLabel {
                    text: "日期例外 · 可选"
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 14
                    ColumnLayout {
                        Layout.fillWidth: true
                        SmallText {
                            text: "仅指定日期 · 每行 YYYY-MM-DD"
                        }
                        Basic.TextArea {
                            id: datesInput
                            padding: 12
                            topInset: 0
                            bottomInset: 0
                            Layout.fillWidth: true
                            Layout.preferredHeight: 80
                            placeholderText: "留空按星期执行"
                            selectByMouse: true
                            wrapMode: Text.Wrap
                            background: Rectangle {
                                color: "#f7f8fc"
                                radius: 8
                                border.color: "#e6e7f1"
                            }
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        SmallText {
                            text: "排除日期 · 每行 YYYY-MM-DD"
                        }
                        Basic.TextArea {
                            id: excludedDatesInput
                            padding: 12
                            topInset: 0
                            bottomInset: 0
                            Layout.fillWidth: true
                            Layout.preferredHeight: 80
                            placeholderText: "这些日期不自动执行"
                            selectByMouse: true
                            wrapMode: Text.Wrap
                            background: Rectangle {
                                color: "#f7f8fc"
                                radius: 8
                                border.color: "#e6e7f1"
                            }
                        }
                    }
                }
                SmallText {
                    text: "本节目禁播时段 · 高级 JSON，字段 start / end / time_from / time_to"
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }
                Basic.TextArea {
                    id: programExcludeInput
                    padding: 12
                    topInset: 0
                    bottomInset: 0
                    Layout.fillWidth: true
                    Layout.preferredHeight: 80
                    text: "[]"
                    selectByMouse: true
                    wrapMode: Text.Wrap
                    background: Rectangle {
                        color: "#f7f8fc"
                        radius: 8
                        border.color: "#e6e7f1"
                    }
                }
            }
        }
    }
    Dialog {
        id: builtinDialog
        parent: Overlay.overlay
        modal: true
        width: Math.min(1040, win.width - 60)
        height: Math.min(730, win.height - 60)
        x: (parent.width - width) / 2
        y: (parent.height - height) / 2
        title: "选择内置音频"
        standardButtons: Dialog.Close
        Component.onCompleted: win.localize(builtinDialog)
        BuiltinLibrary {
            anchors.fill: parent
            tracks: win.state.builtinMedia || []
            editorMode: true
            onCommand: function (action, payload) {
                win.act(action, payload);
            }
            onChosen: function (paths) {
                win.acceptBuiltins(paths);
            }
        }
    }
    Dialog {
        id: backupDialog
        property string operation: "export"
        parent: Overlay.overlay
        modal: true
        width: 430
        x: (parent.width - width) / 2
        y: (parent.height - height) / 2
        title: operation === "import" ? "导入范围" : "导出范围"
        ColumnLayout {
            anchors.fill: parent
            spacing: 5
            SmallText {
                text: "请选择需要处理的内容。"
            }
            CheckBox {
                id: backupPrograms
                text: "节目单"
                checked: true
            }
            CheckBox {
                id: backupHolidays
                text: "节假日"
                checked: true
            }
            CheckBox {
                id: backupCalendar
                text: "调休"
                checked: true
            }
            CheckBox {
                id: backupLive
                text: "立即播列表"
                checked: true
            }
            CheckBox {
                id: backupSettings
                text: "设置"
                checked: true
            }
        }
        footer: DialogButtonBox {
            Button {
                text: "下一步"
                DialogButtonBox.buttonRole: DialogButtonBox.ActionRole
                onClicked: {
                    var sections = [];
                    if (backupPrograms.checked)
                        sections.push("programs");
                    if (backupHolidays.checked)
                        sections.push("holidays");
                    if (backupCalendar.checked)
                        sections.push("calendar");
                    if (backupLive.checked)
                        sections.push("live");
                    if (backupSettings.checked)
                        sections.push("settings");
                    if (!sections.length) {
                        showError("请至少选择一项内容。");
                        return;
                    }
                    backupDialog.close();
                    act(backupDialog.operation, {
                        sections: sections
                    });
                }
            }
            Button {
                text: "取消"
                DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
            }
            onRejected: backupDialog.reject()
        }
    }
    Dialog {
        id: timeGenerator
        parent: Overlay.overlay
        modal: true
        title: "批量生成播出时间"
        width: 440
        x: (parent.width - width) / 2
        y: (parent.height - height) / 2
        standardButtons: Dialog.NoButton
        footer: DialogButtonBox {
            Button {
                text: "生成"
                objectName: "generateTimes"
                highlighted: true
                DialogButtonBox.buttonRole: DialogButtonBox.ActionRole
                onClicked: {
                    if (timeGenerator.generate())
                        timeGenerator.close();
                }
            }
            Button {
                text: "取消"
                DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
            }
            onRejected: timeGenerator.reject()
        }
        ColumnLayout {
            anchors.fill: parent
            spacing: 12
            SmallText {
                text: "同一天内按固定间隔生成，替换当前时间列表。"
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
            SmallText {
                text: "开始时间"
            }
            TextField {
                id: batchStart
                Layout.fillWidth: true
                text: "08:00:00"
                placeholderText: "开始时间 HH:MM:SS"
            }
            SmallText {
                text: "结束时间"
            }
            TextField {
                id: batchEnd
                Layout.fillWidth: true
                text: "18:00:00"
                placeholderText: "结束时间 HH:MM:SS"
            }
            SmallText {
                text: "间隔（分钟，最小0.2）"
            }
            TextField {
                id: batchInterval
                Layout.fillWidth: true
                text: "60"
                placeholderText: "间隔分钟，最少 0.2 分钟"
            }
        }
        function generate() {
            function seconds(value) {
                var parts = value.trim().split(":");
                if (parts.length < 2 || parts.length > 3)
                    return -1;
                var h = Number(parts[0]), m = Number(parts[1]), s = parts.length === 3 ? Number(parts[2]) : 0;
                if (!Number.isInteger(h) || !Number.isInteger(m) || !Number.isInteger(s) || h < 0 || h > 23 || m < 0 || m > 59 || s < 0 || s > 59)
                    return -1;
                return h * 3600 + m * 60 + s;
            }
            var start = seconds(batchStart.text), end = seconds(batchEnd.text), interval = Number(batchInterval.text);
            if (start < 0 || end < start || !isFinite(interval) || interval < 0.2) {
                showError("请填写有效的开始和结束时间（同一天，开始不晚于结束），间隔至少 0.2 分钟。");
                return false;
            }
            var step = Math.round(interval * 60), result = [];
            for (var t = start; t <= end; t += step)
                result.push(String(Math.floor(t / 3600)).padStart(2, "0") + ":" + String(Math.floor(t % 3600 / 60)).padStart(2, "0") + ":" + String(t % 60).padStart(2, "0"));
            timesInput.text = result.join("\n");
            return true;
        }
    }
    Dialog {
        id: plansDialog
        Component.onCompleted: win.localize(plansDialog)
        objectName: "planEditor"
        parent: Overlay.overlay
        modal: true
        width: 560
        height: Math.min(550, win.height - 80)
        x: (parent.width - width) / 2
        y: (parent.height - height) / 2
        title: "节目单"
        standardButtons: Dialog.Close
        ColumnLayout {
            anchors.fill: parent
            spacing: 14
            RowLayout {
                Layout.fillWidth: true
                TextField {
                    id: newPlanName
                    objectName: "planNameInput"
                    Layout.fillWidth: true
                    placeholderText: "新节目单名称"
                }
                Button {
                    text: "添加节目单"
                    objectName: "planSave"
                    highlighted: true
                    onClicked: {
                        if (act("add_plan", {
                            name: newPlanName.text.trim()
                        }))
                            newPlanName.text = "";
                    }
                }
            }
            ListView {
                id: plansList
                Layout.fillWidth: true
                Layout.fillHeight: true
                model: plans
                spacing: 8
                clip: true
                delegate: RowLayout {
                    required property var modelData
                    width: plansList.width
                    Switch {
                        checked: modelData.enabled
                        onToggled: act("toggle_plan", {
                            id: modelData.id,
                            enabled: checked
                        })
                    }
                    TextField {
                        id: planRename
                        text: modelData.name
                        Layout.fillWidth: true
                        onEditingFinished: {
                            if (text !== modelData.name)
                                act("update_plan", {
                                    id: modelData.id,
                                    name: text
                                });
                        }
                    }
                    QuietButton {
                        text: "删除"
                        onClicked: {
                            planDeleteDialog.planId = modelData.id;
                            planDeleteDialog.planName = modelData.name;
                            planDeleteDialog.open();
                        }
                    }
                }
            }
            SmallText {
                text: "禁用节目单会同时暂停该节目单下所有节目的定时播。"
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
        }
    }
    Dialog {
        id: deleteDialog
        Component.onCompleted: {
            win.localize(deleteDialog);
            footer.standardButton(Dialog.Yes).objectName = "programDeleteConfirm";
        }
        property string programId
        property string programName
        parent: Overlay.overlay
        modal: true
        width: 420
        x: (parent.width - width) / 2
        y: (parent.height - height) / 2
        title: "删除节目"
        standardButtons: Dialog.Yes | Dialog.Cancel
        Label {
            width: parent.width
            text: "确认删除“" + deleteDialog.programName + "”？"
            wrapMode: Text.Wrap
            color: win.ink
        }
        onAccepted: {
            act("delete_program", {
                id: programId
            });
            win.selectedProgramId = "";
        }
    }
    Dialog {
        id: planDeleteDialog
        Component.onCompleted: {
            win.localize(planDeleteDialog);
            footer.standardButton(Dialog.Yes).objectName = "planDeleteConfirm";
        }
        property string planId
        property string planName
        parent: Overlay.overlay
        modal: true
        width: 430
        x: (parent.width - width) / 2
        y: (parent.height - height) / 2
        title: "删除节目单"
        standardButtons: Dialog.Yes | Dialog.Cancel
        Label {
            width: parent.width
            text: "确认删除节目单“" + planDeleteDialog.planName + "”？请先移动或删除该节目单下的节目。"
            wrapMode: Text.Wrap
            color: win.ink
        }
        onAccepted: act("delete_plan", {
            id: planId
        })
    }
    Dialog {
        id: errorDialog
        Component.onCompleted: win.localize(errorDialog)
        parent: Overlay.overlay
        modal: true
        width: Math.min(520, win.width - 60)
        x: (parent.width - width) / 2
        y: (parent.height - height) / 2
        title: "操作提示"
        standardButtons: Dialog.Ok
        Label {
            id: errorText
            width: parent.width
            wrapMode: Text.Wrap
            color: win.ink
        }
    }
}
