import sys
from PyQt5.QtCore import QDateTime, pyqtSlot
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QGroupBox, QLabel, QLineEdit, \
    QTextEdit

# 从模块中导入SamTcpClient
# 确保 sam_client.py 位于 modules 文件夹中
try:
    from modules.up_link import SamTcpClient
except ImportError:
    print("错误: 无法找到 'modules/sam_client.py'。请确保文件路径正确。")
    sys.exit(1)


class TestUI(QWidget):
    """
    一个用于测试SamTcpClient的图形界面。
    它作为一个纯客户端，连接到外部的SAM服务器。
    """

    def __init__(self):
        super().__init__()
        self.sam_client = None
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("SAM 客户端测试工具")
        self.setGeometry(200, 200, 600, 500)

        main_layout = QVBoxLayout(self)

        # --- 连接设置 ---
        conn_group = QGroupBox("连接设置")
        conn_layout = QHBoxLayout()
        self.host_input = QLineEdit("127.0.0.1")  # 在此输入SAM服务器的IP地址
        self.port_input = QLineEdit("12345")  # 在此输入SAM服务器的端口
        self.connect_button = QPushButton("连接到SAM")
        self.disconnect_button = QPushButton("断开连接")
        self.status_label = QLabel("状态: 未连接")

        conn_layout.addWidget(QLabel("SAM服务器主机:"))
        conn_layout.addWidget(self.host_input)
        conn_layout.addWidget(QLabel("端口:"))
        conn_layout.addWidget(self.port_input)
        conn_layout.addWidget(self.connect_button)
        conn_layout.addWidget(self.disconnect_button)
        conn_group.setLayout(conn_layout)

        # --- 客户端命令控制 ---
        client_cmd_group = QGroupBox("客户端控制 (发送给SAM)")
        client_cmd_layout = QHBoxLayout()
        self.acq_button = QPushButton("请求集中控制 (ACQ)")
        self.tsq_button = QPushButton("手动请求时间同步 (TSQ)")
        client_cmd_layout.addWidget(self.acq_button)
        client_cmd_layout.addWidget(self.tsq_button)
        client_cmd_group.setLayout(client_cmd_layout)

        # --- 日志输出 ---
        log_group = QGroupBox("事件与通信日志")
        log_layout = QVBoxLayout()
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        log_layout.addWidget(self.log_view)
        log_group.setLayout(log_layout)

        main_layout.addWidget(conn_group)
        main_layout.addWidget(self.status_label)
        main_layout.addWidget(client_cmd_group)
        main_layout.addWidget(log_group)

        # --- 连接信号和槽 ---
        self.connect_button.clicked.connect(self.start_client)
        self.disconnect_button.clicked.connect(self.stop_client)
        self.acq_button.clicked.connect(self.send_acq_command)
        self.tsq_button.clicked.connect(self.send_tsq_command)

        # --- 初始状态 ---
        self.set_controls_enabled(False)

    def set_controls_enabled(self, connected):
        """根据连接状态统一设置控件的可用性"""
        self.disconnect_button.setEnabled(connected)
        self.acq_button.setEnabled(connected)
        self.tsq_button.setEnabled(connected)
        self.connect_button.setEnabled(not connected)
        self.host_input.setEnabled(not connected)
        self.port_input.setEnabled(not connected)

    def log_event(self, message):
        """向日志窗口添加一条带时间戳的记录"""
        timestamp = QDateTime.currentDateTime().toString("yyyy-MM-dd hh:mm:ss.zzz")
        self.log_view.append(f"[{timestamp}] {message}")

    @pyqtSlot()
    def start_client(self):
        host = self.host_input.text()
        port = int(self.port_input.text())

        # 创建SamTcpClient实例时，将 self (TestUI窗口) 作为其父对象，以确保正确的内存管理
        self.sam_client = SamTcpClient(host, port, parent=self)

        # 连接统一的事件信号
        self.sam_client.sam_event.connect(self.on_sam_event)

        self.log_event(f"客户端: 正在尝试连接到SAM服务器 {host}:{port}...")
        self.sam_client.connect_to_server()

        self.set_controls_enabled(True)

    @pyqtSlot()
    def stop_client(self):
        if self.sam_client:
            self.sam_client.shutdown()
            self.sam_client.deleteLater()  # 请求安全地删除对象
            self.sam_client = None

        self.set_controls_enabled(False)
        self.status_label.setText("状态: 未连接")
        self.log_event("客户端已断开。")

    @pyqtSlot()
    def send_acq_command(self):
        if self.sam_client:
            self.log_event("UI -> 客户端: 发送 'REQUEST_CENTRAL_CONTROL' 命令。")
            self.sam_client.post_command_signal.emit('REQUEST_CENTRAL_CONTROL', {})

    @pyqtSlot()
    def send_tsq_command(self):
        if self.sam_client:
            self.log_event("UI -> 客户端: 发送 'REQUEST_TIME_SYNC' 命令。")
            self.sam_client.post_command_signal.emit('REQUEST_TIME_SYNC', {})

    @pyqtSlot(dict)
    def on_sam_event(self, event: dict):
        """处理来自SamTcpClient的所有事件"""
        event_type = event.get('type')
        event_data = event.get('data', {})

        self.log_event(f"客户端 -> UI: 收到事件 - 类型: '{event_type}', 数据: {event_data}")

        # 根据核心事件类型更新UI状态
        if event_type == 'connection':
            status = event_data.get('status')
            if status == 'connected':
                self.status_label.setText("状态: TCP已连接，等待服务器发送DC2握手...")
            elif status == 'lost':
                self.status_label.setText("状态: 通信丢失/已重置")
                self.set_controls_enabled(False)

        elif event_type == 'handshake':
            if event_data.get('status') == 'success':
                self.status_label.setText("状态: 握手成功，通信已激活")

        elif event_type == 'aca':
            success = event_data.get('success')
            message = event_data.get('message')
            mode_str = "集中控制" if success else "场控"
            self.status_label.setText(f"状态: {message} (当前: {mode_str})")

    def closeEvent(self, event):
        """关闭窗口时确保客户端断开连接"""
        self.stop_client()
        super().closeEvent(event)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = TestUI()
    window.show()
    sys.exit(app.exec_())
