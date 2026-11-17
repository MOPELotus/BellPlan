import QtQuick
import QtQuick.Shapes

Item {
    id: icon

    property string name: "clock"
    property color color: "#82869b"
    implicitWidth: 22
    implicitHeight: 22
    Accessible.ignored: true

    // Original 24-unit line drawings keep navigation independent of installed fonts.
    readonly property var paths: ({
        clock: "M12 3 A9 9 0 1 1 11.999 3 Z M12 7 V12 H16",
        schedule: "M5 4 H19 Q21 4 21 6 V18 Q21 20 19 20 H5 Q3 20 3 18 V6 Q3 4 5 4 Z M7 8 H17 M7 12 H12 M7 16 H10 M16 12 V16 L18.5 17.5",
        playlist: "M4 5 H20 M4 10 H20 M4 15 H13 M4 20 H13 M17 15 L21 17.5 L17 20 Z",
        music: "M9 17 V5 L20 3 V15 M9 8 L20 6 M9 17 C9 20 3 20 3 17 C3 14 9 14 9 17 Z M20 15 C20 18 14 18 14 15 C14 12 20 12 20 15 Z",
        logs: "M4 5 H4.01 M8 5 H20 M4 10 H4.01 M8 10 H20 M4 15 H4.01 M8 15 H20 M4 20 H4.01 M8 20 H16",
        settings: "M19.13 9.68 L21.48 10.5 L21.48 13.5 L19.13 14.32 L18.68 15.4 L19.76 17.64 L17.64 19.76 L15.4 18.68 L14.32 19.13 L13.5 21.48 L10.5 21.48 L9.68 19.13 L8.6 18.68 L6.36 19.76 L4.24 17.64 L5.32 15.4 L4.87 14.32 L2.52 13.5 L2.52 10.5 L4.87 9.68 L5.32 8.6 L4.24 6.36 L6.36 4.24 L8.6 5.32 L9.68 4.87 L10.5 2.52 L13.5 2.52 L14.32 4.87 L15.4 5.32 L17.64 4.24 L19.76 6.36 L18.68 8.6 Z M15.1 12 A3.1 3.1 0 1 1 8.9 12 A3.1 3.1 0 1 1 15.1 12 Z",
        calendar: "M5 5 H19 Q21 5 21 7 V19 Q21 21 19 21 H5 Q3 21 3 19 V7 Q3 5 5 5 Z M8 3 V7 M16 3 V7 M3 10 H21 M8 14 H8.01 M12 14 H12.01 M16 14 H16.01 M8 18 H8.01 M12 18 H12.01 M16 18 H16.01",
        check: "M5 12.5 L9.5 17 L19 7"
    })

    Item {
        width: 24
        height: 24
        anchors.centerIn: parent
        scale: Math.min(icon.width, icon.height) / 24

        Shape {
            anchors.fill: parent
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: icon.color
                strokeWidth: 1.8
                fillColor: "transparent"
                capStyle: ShapePath.RoundCap
                joinStyle: ShapePath.RoundJoin
                PathSvg {
                    path: icon.paths[icon.name] || ""
                }
            }
        }
    }
}
