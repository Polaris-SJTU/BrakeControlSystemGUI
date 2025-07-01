import sys
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton, QTextEdit, QLabel, QHBoxLayout
from PyQt5.QtCore import Qt
from modules.up_link import SamTcpClient

class SamClientTestUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SAM Client 测试工具")
        self.resize(700, 450)

        # 状态栏和日志框
        self.status_label = QLabel("连接状态：未连接")
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)

        # 按钮
        self.btn_acq = QPushButton("请求集中控制 (ACQ)")

        # Layout
        layout = QVBoxLayout()
        layout.addWidget(self.status_label)
        layout.addWidget(self.log_box)
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.btn_acq)
        layout.addLayout(btn_row)
        self.setLayout(layout)

        # 创建客户端
        self.client = SamTcpClient("127.0.0.1", 12345, )
        self.client.sam_event.connect(self.on_event_received)

        # 绑定信号
        self.btn_acq.clicked.connect(lambda: self.client.post_command_signal.emit("REQUEST_CENTRAL_CONTROL", {}))

    def on_event_received(self, event: dict):
        event_type = event.get("type")
        data = event.get("data")

        if event_type == "connection":
            connected = data.get("status") == "connected"
            self.status_label.setText(f"连接状态：{'已连接' if connected else '已断开'}")
            self.append_log(f"[连接] {data.get('status')}")
        elif event_type == "handshake":
            self.append_log("[握手] 握手完成，进入通信状态")
        elif event_type == "ack_confirm":
            self.append_log(f"[ACK] 收到ACK确认, 序号: {data['send_seq']}")
        elif event_type == "aca":
            self.append_log(f"[ACA] {'成功' if data['success'] else '失败'} - {data['message']}")
        elif event_type == "time_data":
            ts = data
            self.append_log(f"[TSD] 同步时间: {ts['year']}-{ts['month']:02d}-{ts['day']:02d} {ts['hour']:02d}:{ts['minute']:02d}:{ts['second']:02d}")
        elif event_type == "rsr":
            self.append_log(f"[RSR] SAM主备: {data['sam_master_backup']:02X}, 集中控制许可: {data['sam_allow_central_control']:02X}")
        elif event_type == "bcc":
            self.append_log(f"[BCC] 接收到BCC指令: {data.hex().upper()}")
        elif event_type == "event":
            self.append_log(f"[事件] {data}")
        else:
            self.append_log(f"[事件] 未知事件类型: {event_type}")

        # 自动 TSQ 日志记录
        if event_type == "handshake":
            self.append_log("[定时] 已安排初始TSQ（10秒后）与每日18:00 TSQ请求")
        elif event_type == "tsq_auto":
            self.append_log("[定时] 自动定时器触发：发送TSQ请求")

    def append_log(self, message):
        self.log_box.append(message)
        self.log_box.verticalScrollBar().setValue(self.log_box.verticalScrollBar().maximum())

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SamClientTestUI()
    window.show()
    sys.exit(app.exec_())
