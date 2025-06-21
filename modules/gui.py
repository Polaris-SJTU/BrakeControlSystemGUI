import datetime
import enum

from PyQt5.QtCore import pyqtSlot, QTimer
from PyQt5.QtWidgets import QMainWindow

from modules.hot_standby import HotStandby, MachineRole, HeartbeatStatus
from modules.logger import Logger
from modules.tcp_client import DownlinkTcpClient
from uis.brake_control_system import Ui_Form


class StopperState(enum.IntEnum):
    STATE_INIT = 1,
    STATE_STOP_AT_BRAKE = 2,
    STATE_STOP_AT_RELEASE = 3,
    STATE_MAINTAIN = 4,
    ERROR_VALVE_ANOMALY = 100


stopper_state_map = {
    StopperState.STATE_INIT: "正在初始化",
    StopperState.STATE_STOP_AT_BRAKE: "处于制动状态",
    StopperState.STATE_STOP_AT_RELEASE: "处于缓解状态",
    StopperState.STATE_MAINTAIN: "处于检修状态",
    StopperState.ERROR_VALVE_ANOMALY: "无指令电磁阀异动"
}


class AntiSlipState(enum.IntEnum):
    STATE_INIT = 1,
    STATE_STOP_AT_BRAKE_REMOTE = 2,
    STATE_STOP_AT_RELEASE_REMOTE = 3,
    STATE_STOP_LOCAL = 4,
    STATE_BRAKING_REMOTE = 5,
    STATE_RELEASING_REMOTE = 6,
    STATE_BRAKING_LOCAL = 7,
    STATE_RELEASING_LOCAL = 8,
    STATE_PUSH_AWAY = 9,
    WARNING_NOT_IN_PLACE = 10,
    ERROR_BOTH_SWITCH_ON = 100,
    ERROR_RELEASE_SWITCH_ON = 101,
    ERROR_BRAKE_SWITCH_OFF = 102,
    ERROR_BRAKE_SWITCH_ON = 103,
    ERROR_RELEASE_SWITCH_OFF = 104,
    ERROR_BRAKE_TIMEOUT = 110,
    ERROR_RELEASE_TIMEOUT = 111
    ERROR_NOT_UNIFIED_RELEASE = 120,
    ERROR_NOT_UNIFIED_BRAKE = 121


anti_slip_state_map = {
    AntiSlipState.STATE_INIT: "正在初始化",
    AntiSlipState.STATE_STOP_AT_BRAKE_REMOTE: "处于制动（远程）状态",
    AntiSlipState.STATE_STOP_AT_RELEASE_REMOTE: "处于缓解（远程）状态",
    AntiSlipState.STATE_STOP_LOCAL: "处于停止（检修）状态",
    AntiSlipState.STATE_BRAKING_REMOTE: "制动中（远程）",
    AntiSlipState.STATE_RELEASING_REMOTE: "缓解中（远程）",
    AntiSlipState.STATE_BRAKING_LOCAL: "制动中（检修）",
    AntiSlipState.STATE_RELEASING_LOCAL: "缓解中（检修）",
    AntiSlipState.STATE_PUSH_AWAY: "主鞋推走",
    AntiSlipState.WARNING_NOT_IN_PLACE: "进入远程控制时未正确归位",
    AntiSlipState.ERROR_BOTH_SWITCH_ON: "出现双表示错误",
    AntiSlipState.ERROR_RELEASE_SWITCH_ON: "缓解表示错误出现",
    AntiSlipState.ERROR_BRAKE_SWITCH_OFF: "制动表示错误消失",
    AntiSlipState.ERROR_BRAKE_SWITCH_ON: "制动表示错误出现",
    AntiSlipState.ERROR_RELEASE_SWITCH_OFF: "缓解表示错误消失",
    AntiSlipState.ERROR_BRAKE_TIMEOUT: "制动超时",
    AntiSlipState.ERROR_RELEASE_TIMEOUT: "缓解超时",
    AntiSlipState.ERROR_NOT_UNIFIED_RELEASE: "缓解表示不一致",
    AntiSlipState.ERROR_NOT_UNIFIED_BRAKE: "制动表示不一致"
}


class BrakeControlSystemGUI(QMainWindow, Ui_Form):
    def __init__(self, machine_id):
        super(BrakeControlSystemGUI, self).__init__()
        self.setupUi(self)
        self.showFullScreen()

        # 设置设备ID
        self.machine_id = machine_id
        self.update_machine_id()

        # 双机热备模块
        self.local_role = None
        self.local_status = None
        self.remote_role = None
        self.remote_status = None
        self.hot_standby = HotStandby()
        self.hot_standby.status_updated.connect(self.update_hot_standby_status)

        # 日志模块
        self.logger = Logger()
        self.BTN_search.clicked.connect(self.show_log_window)

        self.log(f"{self.machine_id}机启动")

        self.downlink_host = "192.168.1.253"

        self.update_datetime()
        self.time_update_timer = QTimer(self)
        self.time_update_timer.timeout.connect(self.update_datetime)
        self.time_update_timer.start(1000)

        # 设置设备状态
        self.track_statuses = {}
        self.tcp_clients = {}
        self._initialize_track_statuses()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.broadcast_query_command)
        self.timer.start(1000)

        self.selected_devices = set()
        self.selection_timers = {}  # 存储活跃的计时器 {(track, function, device): timer}

        self.last_report_time = {}
        self._initialize_last_report_time()
        self.timeout_threshold = 5
        self.timeout_timer = QTimer(self)
        self.timeout_timer.timeout.connect(self.check_report_timeout)
        self.timeout_timer.start(1000)

    def update_datetime(self):
        """更新日期时间显示"""
        current_time = datetime.datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")
        self.label_5.setText(
            f"<html><head/><body><p><span style='font-size:16pt; font-weight:600;'>{current_time}</span></p></body></html>")

    def check_report_timeout(self):
        """超时检测核心方法"""
        now = datetime.datetime.now()

        for track_id in range(2, 2 + 23):
            for function, device_id in zip(["STOPPER", "STOPPER", "STOPPER", "ANTI_SLIP"], [1, 2, 3, 1]):
                key = (track_id, function, device_id)
                last_time = self.last_report_time.get(key, datetime.datetime.min)

                # 计算时间差
                time_diff = (now - last_time).total_seconds()

                if time_diff > self.timeout_threshold:
                    # 只有当设备当前不是error状态时才更新
                    if self.track_statuses[track_id][function][device_id]["STATE"] != 200:
                        self.track_statuses[track_id][function][device_id]["STATE"] = 200
                        self.log(
                            f"第{track_id}道第{device_id}台{'停车器' if function == 'STOPPER' else '防溜器'}通信超时")
                self.update_device_button(track_id, function, device_id)

    def broadcast_query_command(self):
        if self.local_role == MachineRole.BACKUP:
            return
        for track_id in range(2, 2 + 23):
            query_command = {
                "FUN": "ALL_TYPES",
                "MODE": "REMOTE_CONTROL",
                "DEVICE": 0,
                "TRACK": 0,
                "CMD": "QUERY"
            }
            self.tcp_clients[track_id].send_downlink_command.emit(query_command)

    def _initialize_last_report_time(self):
        for track_id in range(2, 2 + 23):
            for device_id in range(1, 1 + 3):
                self.last_report_time[(track_id, "STOPPER", device_id)] = datetime.datetime.now()
            self.last_report_time[(track_id, "ANTI_SLIP", 1)] = datetime.datetime.now()

    def _initialize_track_statuses(self):
        """Initializes the device status dictionary with default values."""
        for track_id, port in zip(range(2, 2 + 23), range(1031, 1031 + 23)):
            self.track_statuses[track_id] = {
                "STOPPER": {
                    1: {
                        "MODE": None,
                        "STATE": StopperState.STATE_INIT,
                        "IO_16_9": 0xFF,
                        "IO_8_1": 0xFF
                    },
                    2: {
                        "MODE": None,
                        "STATE": StopperState.STATE_INIT,
                        "IO_16_9": 0xFF,
                        "IO_8_1": 0xFF
                    },
                    3: {
                        "MODE": None,
                        "STATE": StopperState.STATE_INIT,
                        "IO_16_9": 0xFF,
                        "IO_8_1": 0xFF
                    }
                },
                "ANTI_SLIP": {
                    1: {
                        "MODE": None,
                        "STATE": AntiSlipState.STATE_INIT,
                        "IO_16_9": 0xFF,
                        "IO_8_1": 0xFF
                    }
                }
            }
            self.tcp_clients[track_id] = DownlinkTcpClient(self.downlink_host, port)
            for device_id in range(1, 1 + 3):
                self.update_device_button(track_id, "STOPPER", device_id)
                button = getattr(self, f"BTN{track_id}_{device_id}")
                button.clicked.connect(self.create_device_handler(track_id, "STOPPER", device_id))
            self.update_device_button(track_id, "ANTI_SLIP", 1)
            button = getattr(self, f"BTN{track_id}_{4}")
            button.clicked.connect(self.create_device_handler(track_id, "ANTI_SLIP", 1))
            button = getattr(self, f"BTN{track_id}_{5}")
            button.clicked.connect(self.create_track_handler(track_id))
            self.tcp_clients[track_id].parsed_uplink_packet.connect(self._update_device_status)

            self.BTN_brake.clicked.connect(self.send_brake_command)
            self.BTN_release.clicked.connect(self.send_release_command)

    def send_brake_command(self):
        self.send_control_command("BRAKE")

    def send_release_command(self):
        self.send_control_command("RELEASE")

    def send_control_command(self, cmd):
        """发送控制命令"""
        if not self.selected_devices:
            return

        try:
            for (track_id, function, device_id) in self.selected_devices:
                command = {
                    "FUN": function,
                    "MODE": "REMOTE_CONTROL",
                    "DEVICE": device_id,
                    "TRACK": track_id,
                    "CMD": cmd
                }
                self.tcp_clients[track_id].send_downlink_command.emit(command)
                self.log(
                    f"上位机发送{'制动' if cmd == 'BRAKE' else '缓解'}指令"
                    f"到第{track_id}道"
                    f"第{device_id}台"
                    f"{'停车器' if function == 'STOPPER' else '防溜器'}"
                )
        finally:
            self.deselect_all_devices()

    def deselect_all_devices(self):
        """取消所有设备选择"""
        # 先取消所有计时器
        self.cancel_all_timers()

        # 清除设备选择状态
        for track_id in range(2, 2 + 23):
            # 处理单个设备按钮
            for device_id in range(1, 1 + 4):
                button = getattr(self, f"BTN{track_id}_{device_id}")
                if button.isChecked():
                    button.setChecked(False)

        # 清空选中集合
        self.selected_devices.clear()

    def cancel_all_timers(self):
        """取消所有激活的计时器"""
        # 取消设备级计时器
        for timer in self.selection_timers.values():
            timer.stop()
        self.selection_timers.clear()

    def create_track_handler(self, track_id):
        def handler():
            checked = 0
            enabled = 0
            for device_id in range(1, 1 + 4):
                button = getattr(self, f"BTN{track_id}_{device_id}")
                if button.isChecked():
                    checked += 1
                if button.isEnabled():
                    enabled += 1
            for device_id in range(1, 1 + 4):
                button = getattr(self, f"BTN{track_id}_{device_id}")
                if checked != enabled and button.isEnabled():
                    self.select_device(track_id, "ANTI_SLIP" if device_id == 4 else "STOPPER",
                                       1 if device_id == 4 else device_id)
                else:
                    self.deselect_device(track_id, "ANTI_SLIP" if device_id == 4 else "STOPPER",
                                         1 if device_id == 4 else device_id)

        return handler

    def create_device_handler(self, track_id, function, device_id):
        def handler():
            button = getattr(self, f"BTN{track_id}_{device_id if function == 'STOPPER' else 4}")
            if button.isChecked():
                self.select_device(track_id, function, device_id)
            else:
                self.deselect_device(track_id, function, device_id)

        return handler

    def select_device(self, track_id, function, device_id):
        """选择单个设备"""
        self.set_device_selection(track_id, function, device_id, True)

        # 取消旧计时器（如果存在）
        self.cancel_device_timer(track_id, function, device_id)

        # 启动新计时器
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self.auto_deselect_device(track_id, function, device_id))
        timer.start(5000)
        self.selection_timers[(track_id, function, device_id)] = timer

    def deselect_device(self, track_id, function, device_id):
        """主动取消设备选择"""
        self.cancel_device_timer(track_id, function, device_id)
        self.set_device_selection(track_id, function, device_id, False)

    def cancel_device_timer(self, track_id, function, device_id):
        """取消设备计时器"""
        key = (track_id, function, device_id)
        if key in self.selection_timers:
            self.selection_timers[key].stop()
            del self.selection_timers[key]

    def set_device_selection(self, track_id, function, device_id, selected):
        """统一更新设备选择状态"""
        button = getattr(self, f"BTN{track_id}_{device_id if function == 'STOPPER' else 4}")
        button.setChecked(selected)

        # 同步选中集合
        if selected:
            self.selected_devices.add((track_id, function, device_id))
        else:
            self.selected_devices.discard((track_id, function, device_id))

    def auto_deselect_device(self, track_id, function, device_id):
        """自动取消设备选择"""
        self.set_device_selection(track_id, function, device_id, False)
        del self.selection_timers[(track_id, function, device_id)]

    def log(self, message):
        if self.local_role == MachineRole.MASTER:
            self.logger.log_signal.emit(message)

    @pyqtSlot(dict)
    def _update_device_status(self, parsed_data):
        track_id = parsed_data["TRACK"]
        function = parsed_data["FUN"]
        device_id = parsed_data["DEVICE"]
        self.last_report_time[(track_id, function, device_id)] = datetime.datetime.now()
        if self.track_statuses[track_id][function][device_id]["MODE"] != parsed_data["MODE"]:
            self.track_statuses[track_id][function][device_id]["MODE"] = parsed_data["MODE"]
            self.log(
                f"第{parsed_data['TRACK']}道"
                f"第{parsed_data['DEVICE']}台"
                f"{'停车器' if parsed_data['FUN'] == 'STOPPER' else '防溜器'}"
                f"进入{'运行' if parsed_data['MODE'] == 'REMOTE_CONTROL' else '检修'}模式"
            )

        if self.track_statuses[track_id][function][device_id]["STATE"] != parsed_data["STATE"]:
            self.track_statuses[track_id][function][device_id]["STATE"] = parsed_data["STATE"]
            if function == "STOPPER":
                if parsed_data["STATE"] > StopperState.ERROR_VALVE_ANOMALY:
                    errors = parsed_data["STATE"] - StopperState.ERROR_VALVE_ANOMALY
                    faulty_valves = []
                    for i in range(5):
                        if errors & (1 << i):
                            faulty_valves.append(str(i + 1))
                    self.log(
                        f"第{parsed_data['TRACK']}道"
                        f"第{parsed_data['DEVICE']}台停车器"
                        f"第{'、'.join(faulty_valves)}台电磁阀故障"
                    )
                else:
                    self.log(
                        f"第{parsed_data['TRACK']}道"
                        f"第{parsed_data['DEVICE']}台停车器"
                        f"{stopper_state_map[parsed_data['STATE']]}"
                    )
            else:
                self.log(
                    f"第{parsed_data['TRACK']}道"
                    f"第{parsed_data['DEVICE']}台防溜器"
                    f"{anti_slip_state_map[parsed_data['STATE']]}"
                )
        if self.track_statuses[track_id][function][device_id]["IO_16_9"] != parsed_data["IO_16_9"]:
            self.track_statuses[track_id][function][device_id]["IO_16_9"] = parsed_data["IO_16_9"]
        if self.track_statuses[track_id][function][device_id]["IO_8_1"] != parsed_data["IO_8_1"]:
            if function == "ANTI_SLIP":
                io_state_low = self.track_statuses[track_id][function][device_id]["IO_8_1"]
                if (io_state_low & 0b00001000) != (parsed_data["IO_8_1"] & 0b00001000):
                    self.log(
                        f"第{parsed_data['TRACK']}道"
                        f"第{parsed_data['DEVICE']}台防溜器"
                        f"副鞋1{'走鞋' if (parsed_data['IO_8_1'] & 0b00001000) == 0 else '缓解'}"
                    )
                if (io_state_low & 0b00010000) != (parsed_data["IO_8_1"] & 0b00010000):
                    self.log(
                        f"第{parsed_data['TRACK']}道"
                        f"第{parsed_data['DEVICE']}台防溜器"
                        f"副鞋2{'走鞋' if (parsed_data['IO_8_1'] & 0b00010000) == 0 else '缓解'}"
                    )
            self.track_statuses[track_id][function][device_id]["IO_8_1"] = parsed_data["IO_8_1"]

        self.update_device_button(track_id, function, device_id)

    def update_device_button(self, track_id, function, device_id):
        button = getattr(self, f"BTN{track_id}_{device_id if function == 'STOPPER' else 4}")
        state = self.track_statuses[track_id][function][device_id]["STATE"]
        mode = self.track_statuses[track_id][function][device_id]["MODE"]
        if function == "STOPPER":
            if state > StopperState.ERROR_VALVE_ANOMALY or state == StopperState.STATE_INIT:
                button.setProperty("state", "error")
                button.setCheckable(False)
                button.setEnabled(False)
            elif mode == "LOCAL_CONTROL":
                button.setProperty("state", "maintain")
                button.setCheckable(False)
                button.setEnabled(False)
            elif state == StopperState.STATE_STOP_AT_BRAKE:
                button.setProperty("state", "brake")
                button.setCheckable(True)
                button.setEnabled(True)
            elif state == StopperState.STATE_STOP_AT_RELEASE:
                button.setProperty("state", "release")
                button.setCheckable(True)
                button.setEnabled(True)
            button.style().unpolish(button)
            button.style().polish(button)
        else:
            label = getattr(self, f"label_anti_slip_{track_id}")
            io_state_low = self.track_statuses[track_id][function][device_id]["IO_8_1"]
            push_away_deputy1 = (io_state_low & 0b00001000) == 0
            push_away_deputy2 = (io_state_low & 0b00010000) == 0
            if state >= AntiSlipState.WARNING_NOT_IN_PLACE or state in [AntiSlipState.STATE_INIT, AntiSlipState.STATE_PUSH_AWAY] or push_away_deputy1 or push_away_deputy2:
                button.setProperty("state", "error")
                button.setCheckable(False)
                button.setEnabled(False)
                label.setProperty("state", "error")
            elif mode == "LOCAL_CONTROL":
                button.setProperty("state", "maintain")
                button.setCheckable(False)
                button.setEnabled(False)
                label.setProperty("state", "maintain")
            elif state == AntiSlipState.STATE_BRAKING_REMOTE:
                button.setProperty("state", "braking")
                button.setCheckable(False)
                button.setEnabled(False)
                label.setProperty("state", "release")
            elif state == AntiSlipState.STATE_RELEASING_REMOTE:
                button.setProperty("state", "releasing")
                button.setCheckable(False)
                button.setEnabled(False)
                label.setProperty("state", "release")
            elif state == AntiSlipState.STATE_STOP_AT_BRAKE_REMOTE:
                button.setProperty("state", "brake")
                button.setCheckable(True)
                button.setEnabled(True)
                label.setProperty("state", "release")
            elif state == AntiSlipState.STATE_STOP_AT_RELEASE_REMOTE:
                button.setProperty("state", "release")
                button.setCheckable(True)
                button.setEnabled(True)
                label.setProperty("state", "release")
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()
            label.style().unpolish(label)
            label.style().polish(label)

    @pyqtSlot()
    def show_log_window(self):
        self.logger.show()

    @pyqtSlot()
    def close_log_window(self):
        self.logger.close()

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
        if self.local_role != status_data["local_role"]:
            self.local_role = status_data["local_role"]
            self.log(f"{self.machine_id}机进入{self.local_role.value}状态")

        if self.local_status != status_data["local_status"]:
            self.local_status = status_data["local_status"]
            self.log(f"{self.machine_id}机{self.local_status.value}")

        if self.remote_role != status_data["remote_role"]:
            self.remote_role = status_data["remote_role"]
            self.log(f"{'B' if self.machine_id == 'A' else 'A'}机进入{self.remote_role.value}状态")

        if self.remote_status != status_data["remote_status"]:
            self.remote_status = status_data["remote_status"]
            self.log(f"{'B' if self.machine_id == 'A' else 'A'}机{self.remote_status.value}")

        # 锁定/解锁按钮
        if self.local_role == MachineRole.BACKUP or self.local_status == HeartbeatStatus.OFFLINE:
            self.lock_all_buttons()
        else:
            self.unlock_all_buttons()

        if self.machine_id == "A":
            self.label_serverA_state.setText(f"{self.local_role.value}")
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
            self.label_serverB_state.setText(f"{self.local_role.value}")
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
        for x in range(2, 2 + 23):  # x = 2 到 24
            for i in range(1, 1 + 5):  # i = 1 到 5
                button = getattr(self, f"BTN{x}_{i}")
                button.setEnabled(False)

        # 禁用 BTN_brake 和 BTN_release
        for name in ["BTN_brake", "BTN_release"]:
            button = getattr(self, name)
            button.setEnabled(False)

    def unlock_all_buttons(self):
        """启用所有按钮"""
        # 启用 BTN2_1 到 BTN24_5
        for x in range(2, 2 + 23):  # x = 2 到 24
            for i in range(1, 1 + 5):  # i = 1 到 5
                button = getattr(self, f"BTN{x}_{i}")
                button.setEnabled(True)

        # 启用 BTN_brake 和 BTN_release
        for name in ["BTN_brake", "BTN_release"]:
            button = getattr(self, name)
            button.setEnabled(True)

    def closeEvent(self, event):
        """程序退出事件"""
        self.logger.close()
        self.hot_standby.stop_service()

        # 删除窗口资源
        self.logger.window.deleteLater()
        self.logger.deleteLater()

        super().closeEvent(event)
