from PyQt5.QtWidgets import QMainWindow
from modules.hot_standby import HotStandby, MachineRole, HeartbeatStatus
from uis.brake_control_system import Ui_Form


class BrakeControlSystemGUI(QMainWindow, Ui_Form):
    def __init__(self, machine_id):
        super(BrakeControlSystemGUI, self).__init__()
        self.setupUi(self)
        self.showFullScreen()

        # 设置设备ID
        self.machine_id = machine_id
        self.update_machine_id()

        # 双机热备模块
        self.current_role = MachineRole.BACKUP
        self.remote_role = MachineRole.BACKUP
        self.remote_status = HeartbeatStatus.OFFLINE
        self.hot_standby = HotStandby(machine_id)
        self.hot_standby.status_updated.connect(self.update_hot_standby_status)

    def update_machine_id(self):
        """更新设备ID"""
        label_server = self.label_serverA if self.machine_id == "A" else self.label_serverB
        label_server.setStyleSheet(
            "background-color: rgb(0, 0, 0);"
            "color: rgb(255, 255, 255);"
            "border: 2px solid gray;"
        )

    def update_hot_standby_status(self, status_data):
        """更新双机热备状态"""
        self.current_role = status_data['local_role']
        self.remote_role = status_data['remote_role']
        self.remote_status = status_data['remote_status']

        # 锁定/解锁按钮
        if self.current_role == MachineRole.BACKUP:
            self.lock_all_buttons()
        else:
            self.unlock_all_buttons()

        if self.machine_id == "A":
            self.label_serverA_state.setText(f"{self.current_role.value}")
            self.label_serverA_state.setStyleSheet(
                f"font-size: 12;"
                f"font-family: 'Microsoft YaHei';"
                f"font-weight: bold;"
                f"background-color: rgb(0, 0, 0);"
                f"color: rgb(0, 255, 0);"
                f"border: 2px solid gray;"
            )
            self.label_serverB_state.setText(f"{self.remote_role.value}")
            self.label_serverB_state.setStyleSheet(
                f"font-size: 12;"
                f"font-family: 'Microsoft YaHei';"
                f"font-weight: bold;"
                f"background-color: rgb(0, 0, 0);"
                f"color: {'rgb(0, 255, 0)' if self.remote_status == HeartbeatStatus.ONLINE else 'rgb(255, 0, 0)'};"
                f"border: 2px solid gray;"
            )
        else:
            self.label_serverA_state.setText(f"{self.remote_role.value}")
            self.label_serverA_state.setStyleSheet(
                f"font-size: 12;"
                f"font-family: 'Microsoft YaHei';"
                f"font-weight: bold;"
                f"background-color: rgb(0, 0, 0);"
                f"color: {'rgb(0, 255, 0)' if self.remote_status == HeartbeatStatus.ONLINE else 'rgb(255, 0, 0)'};"
                f"border: 2px solid gray;"
            )
            self.label_serverB_state.setText(f"{self.current_role.value}")
            self.label_serverB_state.setStyleSheet(
                f"font-size: 12;"
                f"font-family: 'Microsoft YaHei';"
                f"font-weight: bold;"
                f"background-color: rgb(0, 0, 0);"
                f"color: rgb(0, 255, 0);"
                f"border: 2px solid gray;"
            )

    def lock_all_buttons(self):
        """禁用所有按钮"""
        # 禁用 BTN2_1 到 BTN24_5
        for x in range(2, 25):  # x = 2 到 24
            for i in range(1, 6):  # i = 1 到 5
                button_name = f"BTN{x}_{i}"
                button = getattr(self, button_name, None)
                button.setEnabled(False)

        # 禁用 BTN_brake 和 BTN_release
        for name in ["BTN_brake", "BTN_release"]:
            button = getattr(self, name, None)
            button.setEnabled(False)

    def unlock_all_buttons(self):
        """启用所有按钮"""
        # 启用 BTN2_1 到 BTN24_5
        for x in range(2, 25):  # x = 2 到 24
            for i in range(1, 6):  # i = 1 到 5
                button_name = f"BTN{x}_{i}"
                button = getattr(self, button_name, None)
                button.setEnabled(True)

        # 启用 BTN_brake 和 BTN_release
        for name in ["BTN_brake", "BTN_release"]:
            button = getattr(self, name, None)
            button.setEnabled(True)
