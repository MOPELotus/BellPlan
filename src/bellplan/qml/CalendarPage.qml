pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

Item {
    id: calendar
    objectName: "calendarPage"
    property var controller
    property var appState: ({})
    property int tabIndex: 0
    property int selectedHoliday: -1
    property string selectedOverride: ""
    property int sortMode: 0
    readonly property color ink: "#22253f"
    readonly property color muted: "#74798e"
    readonly property color line: "#e6e8f0"
    readonly property var settings: appState.settings || ({})
    readonly property var holidays: settings.exclude_windows || []
    readonly property var overrides: settings.calendar_overrides || ({})
    readonly property var details: settings.calendar_details || ({})
    readonly property var holidayRows: {
        var rows = holidays.map(function(item, i) { return {record:item, sourceIndex:i} })
        rows.sort(function(a,b) {
            var av = sortMode === 1 ? a.record.name || "" : a.record.start + " " + (a.record.time_from || "")
            var bv = sortMode === 1 ? b.record.name || "" : b.record.start + " " + (b.record.time_from || "")
            return av < bv ? -1 : av > bv ? 1 : a.sourceIndex-b.sourceIndex
        })
        return rows
    }
    readonly property var overrideRows: {
        var rows = Object.keys(overrides).map(function(day) { return {day:day, target:overrides[day], name:(details[day] || {}).name || "", enabled:(details[day] || {}).enabled !== false} })
        rows.sort(function(a,b) {
            var av = sortMode === 1 ? a.name : a.day
            var bv = sortMode === 1 ? b.name : b.day
            return av < bv ? -1 : av > bv ? 1 : 0
        })
        return rows
    }
    signal errorOccurred(string message)

    function today() {
        var d = new Date()
        return d.getFullYear() + "-" + ("0"+(d.getMonth()+1)).slice(-2) + "-" + ("0"+d.getDate()).slice(-2)
    }
    function save(payload) {
        try {
            var result = JSON.parse(controller.dispatch("save_settings", JSON.stringify(payload)))
            if (!result.ok) {
                if (!result.cancelled) errorOccurred(result.error || "保存失败")
                return false
            }
            return true
        } catch (e) { errorOccurred(String(e)); return false }
    }
    function dateValid(text) { return /^\d{4}-\d{2}-\d{2}$/.test(text) }
    function targetLabel(target) {
        if (target === "disabled") return "不播出"
        if (typeof target === "number") return "星期" + ["一","二","三","四","五","六","日"][target]
        return String(target)
    }
    function editHoliday(sourceIndex) {
        holidayDialog.sourceIndex = sourceIndex
        var record = sourceIndex >= 0 ? holidays[sourceIndex] : ({})
        holidayName.text = record.name || ""
        holidayStart.text = record.start || today()
        holidayEnd.text = record.end || today()
        holidayAllDay.checked = !record.time_from
        holidayTimeFrom.text = record.time_from || "00:00:00"
        holidayTimeTo.text = record.time_to || "23:59:59"
        holidayEnabled.checked = record.enabled !== false
        holidayDialog.open()
    }
    function saveHoliday() {
        var start = holidayStart.text.trim(), end = holidayEnd.text.trim()
        if (!dateValid(start) || !dateValid(end)) { errorOccurred("日期格式应为 YYYY-MM-DD"); return }
        var record = {name:holidayName.text.trim(), start:start, end:end,
            time_from:holidayAllDay.checked ? "" : holidayTimeFrom.text.trim(),
            time_to:holidayAllDay.checked ? "" : holidayTimeTo.text.trim(), enabled:holidayEnabled.checked}
        var rows = holidays.slice()
        if (holidayDialog.sourceIndex >= 0) rows[holidayDialog.sourceIndex] = record
        else rows.push(record)
        if (save({exclude_windows:rows})) holidayDialog.close()
    }
    function editOverride(day) {
        overrideDialog.originalDay = day
        overrideActual.text = day || today()
        var target = day ? overrides[day] : today()
        overrideMode.currentIndex = typeof target === "number" ? 1 : target === "disabled" ? 2 : 0
        overrideTarget.text = typeof target === "string" && target !== "disabled" ? target : today()
        overrideWeekday.currentIndex = typeof target === "number" ? target : 0
        overrideNote.text = day ? (details[day] || {}).name || "" : ""
        overrideEnabled.checked = day ? (details[day] || {}).enabled !== false : true
        overrideDialog.open()
    }
    function saveOverride() {
        var day = overrideActual.text.trim()
        if (!dateValid(day)) { errorOccurred("调休日期格式应为 YYYY-MM-DD"); return }
        if (day !== overrideDialog.originalDay && Object.prototype.hasOwnProperty.call(overrides, day)) {
            errorOccurred("该日期已有调休记录，请修改原记录"); return
        }
        var target = overrideMode.currentIndex === 1 ? overrideWeekday.currentIndex : overrideMode.currentIndex === 2 ? "disabled" : overrideTarget.text.trim()
        if (overrideMode.currentIndex === 0 && !dateValid(target)) { errorOccurred("执行日期格式应为 YYYY-MM-DD"); return }
        var records = Object.assign({}, overrides), notes = Object.assign({}, details)
        if (overrideDialog.originalDay && overrideDialog.originalDay !== day) {
            delete records[overrideDialog.originalDay]
            delete notes[overrideDialog.originalDay]
        }
        records[day] = target
        notes[day] = {name:overrideNote.text.trim(),enabled:overrideEnabled.checked}
        if (save({calendar_overrides:records, calendar_details:notes})) { selectedOverride=day; overrideDialog.close() }
    }
    function removeSelected() {
        if (tabIndex === 0) {
            if (selectedHoliday < 0 || selectedHoliday >= holidays.length) return
            deleteDialog.kind = 0
            deleteDialog.label = (holidays[selectedHoliday].name || "节假日") + "（" + holidays[selectedHoliday].start + " 至 " + holidays[selectedHoliday].end + "）"
            deleteDialog.holidayIndex = selectedHoliday
        } else {
            if (!selectedOverride || !Object.prototype.hasOwnProperty.call(overrides, selectedOverride)) return
            deleteDialog.kind = 1
            deleteDialog.label = selectedOverride + " 的调休记录"
            deleteDialog.day = selectedOverride
        }
        deleteDialog.open()
    }
    function confirmDelete() {
        if (deleteDialog.kind === 0) {
            var rows = holidays.slice()
            rows.splice(deleteDialog.holidayIndex, 1)
            if (save({exclude_windows:rows})) { selectedHoliday=-1; deleteDialog.close() }
        } else {
            var records = Object.assign({}, overrides), notes = Object.assign({}, details)
            delete records[deleteDialog.day]
            delete notes[deleteDialog.day]
            if (save({calendar_overrides:records,calendar_details:notes})) { selectedOverride=""; deleteDialog.close() }
        }
    }
    component Caption: Label { color: calendar.muted; font.pixelSize: 12 }
    component FieldLabel: Label { color: calendar.ink; Layout.alignment: Qt.AlignVCenter }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 28
        spacing: 16
        TabBar {
            id: tabs
            objectName: "calendarTabs"
            Layout.preferredWidth: 280
            currentIndex: calendar.tabIndex
            onCurrentIndexChanged: calendar.tabIndex=currentIndex
            TabButton { text: "节假日" }
            TabButton { text: "调休" }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Button { objectName: "calendarAdd"; text: "添加"; highlighted: true; onClicked: calendar.tabIndex===0 ? calendar.editHoliday(-1) : calendar.editOverride("") }
            Button { objectName: "calendarEdit"; text: "修改"; enabled: calendar.tabIndex===0 ? calendar.selectedHoliday>=0 && calendar.selectedHoliday<calendar.holidays.length : !!calendar.selectedOverride; onClicked: calendar.tabIndex===0 ? calendar.editHoliday(calendar.selectedHoliday) : calendar.editOverride(calendar.selectedOverride) }
            Button { objectName: "calendarDelete"; text: "删除"; enabled: calendar.tabIndex===0 ? calendar.selectedHoliday>=0 && calendar.selectedHoliday<calendar.holidays.length : !!calendar.selectedOverride; onClicked: calendar.removeSelected() }
            Item { Layout.fillWidth: true }
            Caption { text: "排序" }
            ComboBox { objectName: "calendarSort"; Layout.preferredWidth: 145; model: ["日期", calendar.tabIndex===0 ? "名称" : "备注"]; currentIndex: calendar.sortMode; onActivated: calendar.sortMode=currentIndex }
        }
        Caption { Layout.fillWidth: true; text: calendar.tabIndex===0 ? "节假日时段内停止定时播。未指定时段时，按整日处理。" : "调休日期按指定日期的节目安排播出。日期有效范围和禁播设置仍按实际日期检查。"; wrapMode: Text.Wrap }
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: "white"
            radius: 12
            border.color: calendar.line
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 0
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 42
                    color: "#f6f7fb"
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12; spacing: 18
                        Caption { text: "状态"; Layout.preferredWidth: 62 }
                        Caption { text: calendar.tabIndex===0 ? "名称" : "调休日期"; Layout.preferredWidth: 170 }
                        Caption { text: calendar.tabIndex===0 ? "开始日期" : "按此日期播出"; Layout.preferredWidth: 155 }
                        Caption { visible: calendar.tabIndex===0; text: "结束日期"; Layout.preferredWidth: 140 }
                        Caption { text: calendar.tabIndex===0 ? "每日禁播时段" : "备注"; Layout.fillWidth: true }
                    }
                }
                StackLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    currentIndex: calendar.tabIndex
                    ListView {
                        id: holidayList
                        objectName: "holidayList"
                        clip: true
                        model: calendar.holidayRows
                        ScrollBar.vertical: ScrollBar {}
                        delegate: Rectangle {
                            id: holidayRow
                            required property var modelData
                            width: holidayList.width
                            height: 49
                            color: calendar.selectedHoliday === modelData.sourceIndex ? "#eeebfc" : rowHover.hovered ? "#f8f9fc" : "white"
                            HoverHandler { id: rowHover }
                            TapHandler { onTapped: calendar.selectedHoliday=holidayRow.modelData.sourceIndex; onDoubleTapped: calendar.editHoliday(holidayRow.modelData.sourceIndex) }
                            RowLayout {
                                anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12; spacing: 18
                                Label { text: holidayRow.modelData.record.enabled===false ? "禁用" : "启用"; color: holidayRow.modelData.record.enabled===false ? calendar.muted : "#287b65"; Layout.preferredWidth: 62; font.pixelSize: 13 }
                                Label { text: holidayRow.modelData.record.name || "—"; color: calendar.ink; Layout.preferredWidth: 170; elide: Text.ElideRight }
                                Label { text: holidayRow.modelData.record.start; color: calendar.ink; Layout.preferredWidth: 155 }
                                Label { text: holidayRow.modelData.record.end; color: calendar.ink; Layout.preferredWidth: 140 }
                                Label { text: holidayRow.modelData.record.time_from ? holidayRow.modelData.record.time_from + " – " + holidayRow.modelData.record.time_to : "全天"; color: calendar.ink; Layout.fillWidth: true; elide: Text.ElideRight }
                            }
                            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: "#f0f1f6" }
                        }
                        Caption { anchors.centerIn: parent; visible: !calendar.holidays.length; text: "暂无节假日记录" }
                    }
                    ListView {
                        id: overrideList
                        objectName: "overrideList"
                        clip: true
                        model: calendar.overrideRows
                        ScrollBar.vertical: ScrollBar {}
                        delegate: Rectangle {
                            id: overrideRow
                            required property var modelData
                            width: overrideList.width
                            height: 49
                            color: calendar.selectedOverride===modelData.day ? "#eeebfc" : overrideHover.hovered ? "#f8f9fc" : "white"
                            HoverHandler { id: overrideHover }
                            TapHandler { onTapped: calendar.selectedOverride=overrideRow.modelData.day; onDoubleTapped: calendar.editOverride(overrideRow.modelData.day) }
                            RowLayout {
                                anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12; spacing: 18
                                Label { text: overrideRow.modelData.enabled ? "启用" : "禁用"; color: overrideRow.modelData.enabled ? "#287b65" : calendar.muted; Layout.preferredWidth: 62; font.pixelSize: 13 }
                                Label { text: overrideRow.modelData.day; color: calendar.ink; Layout.preferredWidth: 170 }
                                Label { text: calendar.targetLabel(overrideRow.modelData.target); color: calendar.ink; Layout.preferredWidth: 155 }
                                Label { text: overrideRow.modelData.name || "—"; color: calendar.ink; Layout.fillWidth: true; elide: Text.ElideRight }
                            }
                            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: "#f0f1f6" }
                        }
                        Caption { anchors.centerIn: parent; visible: !calendar.overrideRows.length; text: "暂无调休记录" }
                    }
                }
            }
        }
        Caption { text: "共 " + (calendar.tabIndex===0 ? calendar.holidays.length : calendar.overrideRows.length) + " 条记录" }
    }
    Dialog {
        id: holidayDialog
        objectName: "holidayEditor"
        property int sourceIndex: -1
        parent: Overlay.overlay
        modal: true
        width: 540
        x: (parent.width-width)/2
        y: (parent.height-height)/2
        title: sourceIndex>=0 ? "修改节假日" : "添加节假日"
        footer: DialogButtonBox {
            Button { objectName: "holidaySave"; text: "确定"; highlighted: true; DialogButtonBox.buttonRole: DialogButtonBox.ActionRole; onClicked: calendar.saveHoliday() }
            Button { text: "取消"; DialogButtonBox.buttonRole: DialogButtonBox.RejectRole; onClicked: holidayDialog.close() }
        }
        GridLayout {
            anchors.fill: parent
            columns: 2
            columnSpacing: 18
            rowSpacing: 12
            FieldLabel { text: "名称" }
            TextField { id: holidayName; objectName: "holidayName"; Layout.fillWidth: true; placeholderText: "选填"; selectByMouse: true }
            FieldLabel { text: "开始日期" }
            TextField { id: holidayStart; objectName: "holidayStart"; Layout.fillWidth: true; placeholderText: "YYYY-MM-DD"; selectByMouse: true }
            FieldLabel { text: "结束日期" }
            TextField { id: holidayEnd; objectName: "holidayEnd"; Layout.fillWidth: true; placeholderText: "YYYY-MM-DD"; selectByMouse: true }
            Item { implicitWidth: 1 }
            CheckBox { id: holidayAllDay; text: "全天禁播"; checked: true }
            FieldLabel { text: "每日开始时间"; enabled: !holidayAllDay.checked }
            TextField { id: holidayTimeFrom; objectName: "holidayTimeFrom"; Layout.fillWidth: true; enabled: !holidayAllDay.checked; placeholderText: "HH:MM:SS"; selectByMouse: true }
            FieldLabel { text: "每日结束时间"; enabled: !holidayAllDay.checked }
            TextField { id: holidayTimeTo; objectName: "holidayTimeTo"; Layout.fillWidth: true; enabled: !holidayAllDay.checked; placeholderText: "HH:MM:SS"; selectByMouse: true }
            Item { implicitWidth: 1 }
            CheckBox { id: holidayEnabled; objectName: "holidayEnabled"; text: "启用"; checked: true }
            Caption { Layout.columnSpan: 2; Layout.fillWidth: true; text: "结束时间早于开始时间时，按跨午夜时段处理。"; wrapMode: Text.Wrap }
        }
    }
    Dialog {
        id: overrideDialog
        objectName: "overrideEditor"
        property string originalDay: ""
        parent: Overlay.overlay
        modal: true
        width: 540
        x: (parent.width-width)/2
        y: (parent.height-height)/2
        title: originalDay ? "修改调休" : "添加调休"
        footer: DialogButtonBox {
            Button { objectName: "overrideSave"; text: "确定"; highlighted: true; DialogButtonBox.buttonRole: DialogButtonBox.ActionRole; onClicked: calendar.saveOverride() }
            Button { text: "取消"; DialogButtonBox.buttonRole: DialogButtonBox.RejectRole; onClicked: overrideDialog.close() }
        }
        GridLayout {
            anchors.fill: parent
            columns: 2
            columnSpacing: 18
            rowSpacing: 12
            FieldLabel { text: "调休日期" }
            TextField { id: overrideActual; objectName: "overrideActual"; Layout.fillWidth: true; placeholderText: "YYYY-MM-DD"; selectByMouse: true }
            FieldLabel { text: "执行方式" }
            ComboBox { id: overrideMode; objectName: "overrideMode"; Layout.fillWidth: true; model: ["按指定日期播出", "按指定星期播出", "当日不播出"] }
            FieldLabel { text: "执行日期"; visible: overrideMode.currentIndex===0 }
            TextField { id: overrideTarget; objectName: "overrideTarget"; visible: overrideMode.currentIndex===0; Layout.fillWidth: true; placeholderText: "YYYY-MM-DD"; selectByMouse: true }
            FieldLabel { text: "执行星期"; visible: overrideMode.currentIndex===1 }
            ComboBox { id: overrideWeekday; visible: overrideMode.currentIndex===1; Layout.fillWidth: true; model: ["星期一","星期二","星期三","星期四","星期五","星期六","星期日"] }
            FieldLabel { text: "备注" }
            TextField { id: overrideNote; objectName: "overrideNote"; Layout.fillWidth: true; placeholderText: "选填"; selectByMouse: true }
            Item { implicitWidth: 1 }
            CheckBox { id: overrideEnabled; objectName: "overrideEnabled"; text: "启用"; checked: true }
            Caption { Layout.columnSpan: 2; Layout.fillWidth: true; text: "例如：11 月 21 日按 11 月 23 日的节目安排播出。"; wrapMode: Text.Wrap }
        }
    }
    Dialog {
        id: deleteDialog
        property int kind: 0
        property int holidayIndex: -1
        property string day: ""
        property string label: ""
        parent: Overlay.overlay
        modal: true
        width: 460
        x: (parent.width-width)/2
        y: (parent.height-height)/2
        title: "删除记录"
        Label { width: parent.width; text: "确定删除 " + deleteDialog.label + "？"; wrapMode: Text.Wrap; color: calendar.ink }
        footer: DialogButtonBox {
            Button { text: "删除"; highlighted: true; DialogButtonBox.buttonRole: DialogButtonBox.ActionRole; onClicked: calendar.confirmDelete() }
            Button { text: "取消"; DialogButtonBox.buttonRole: DialogButtonBox.RejectRole; onClicked: deleteDialog.close() }
        }
    }
}
