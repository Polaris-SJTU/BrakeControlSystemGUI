import datetime
import ipaddress
import json
import socket
import threading
import time
from enum import Enum

from PyQt5.QtCore import QObject, pyqtSignal


class MachineRole(Enum):
    MASTER = "主用"
    BACKUP = "备用"


class HeartbeatStatus(Enum):
    ONLINE = "在线"
    OFFLINE = "离线"


class HotStandby(QObject):
    # 定义状态更新信号
    status_updated = pyqtSignal(dict)

    def __init__(self, machine_id):
        super().__init__()
        self.machine_id = machine_id

        self.local_ip = "192.168.1.106" if machine_id == "A" else "192.168.1.110"
        self.remote_ip = "192.168.1.110" if machine_id == "A" else "192.168.1.106"
        self.heartbeat_port = 8888
        self.heartbeat_interval = 0.5  # 心跳间隔秒数
        self.timeout_threshold = 2  # 超时阈值秒数

        # 状态变量
        self.local_role = MachineRole.BACKUP
        self.local_status = HeartbeatStatus.OFFLINE
        self.remote_role = MachineRole.BACKUP
        self.remote_status = HeartbeatStatus.OFFLINE
        self.last_heartbeat_time = None
        self.heartbeat_received = False
        self.dual_master_check_time = None  # 双主机检测时间
        self.dual_master_resolve_delay = 2  # 双主机解决延迟秒数

        # 更新设备状态
        self.update_status()

        # 网络组件
        self.udp_socket = None
        self.heartbeat_timer = None
        self.monitor_timer = None
        self.running = False
        self.stop_event = threading.Event()

        self.start_service()

    def update_status(self):
        """更新设备状态"""
        status_data = {
            'local_role': self.local_role,
            'local_status': self.local_status,
            'remote_role': self.remote_role,
            'remote_status': self.remote_status
        }
        self.status_updated.emit(status_data)

    def start_service(self):
        """启动心跳服务"""
        try:
            # 创建UDP套接字
            self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp_socket.bind(('', self.heartbeat_port))
            self.udp_socket.settimeout(1.0)

            self.running = True
            self.stop_event.clear()

            # 启动监听线程
            self.listen_thread = threading.Thread(target=self.listen_heartbeat, daemon=True)
            self.listen_thread.start()

            self.local_status = HeartbeatStatus.ONLINE

            print(f"服务已启动，本机IP: {self.local_ip}, 对方IP: {self.remote_ip}, 端口: {self.heartbeat_port}")

            # 初始角色判断
            self.determine_initial_role()

            # 启动心跳定时器
            self.start_heartbeat_timer()
            # 启动状态监控定时器
            self.start_monitor_timer()

        except Exception as e:
            print(f"错误：启动服务失败: {str(e)}")
            self.stop_service()

    def stop_service(self):
        """停止心跳服务"""
        self.running = False
        self.stop_event.set()

        # 取消定时器
        if self.heartbeat_timer:
            self.heartbeat_timer.cancel()
            self.heartbeat_timer = None

        if self.monitor_timer:
            self.monitor_timer.cancel()
            self.monitor_timer = None

        if self.udp_socket:
            self.udp_socket.close()
            self.udp_socket = None

        # 重置状态
        self.local_role = MachineRole.BACKUP
        self.local_status = HeartbeatStatus.OFFLINE
        self.remote_role = MachineRole.BACKUP
        self.remote_status = HeartbeatStatus.OFFLINE
        self.last_heartbeat_time = None
        self.dual_master_check_time = None

        self.update_status()
        print("服务已停止")

    def start_heartbeat_timer(self):
        """启动心跳定时器"""
        if not self.running:
            return

        if self.heartbeat_timer:
            self.heartbeat_timer.cancel()

        self.heartbeat_timer = threading.Timer(self.heartbeat_interval, self.send_heartbeat_task)
        self.heartbeat_timer.daemon = True
        self.heartbeat_timer.start()

    def start_monitor_timer(self):
        """启动状态监控定时器"""
        if not self.running:
            return

        if self.monitor_timer:
            self.monitor_timer.cancel()

        self.monitor_timer = threading.Timer(1.0, self.monitor_task)
        self.monitor_timer.daemon = True
        self.monitor_timer.start()

    def send_heartbeat_task(self):
        """发送心跳任务"""
        if not self.running:
            return

        try:
            heartbeat_data = {
                'type': 'heartbeat',
                'role': self.local_role.name,
                'timestamp': time.time(),
                'ip': self.local_ip
            }

            data = json.dumps(heartbeat_data).encode('utf-8')
            self.udp_socket.sendto(data, (self.remote_ip, self.heartbeat_port))

        except Exception as e:
            if self.running:
                print(f"发送心跳失败: {str(e)}")

        # 重新启动定时器
        self.start_heartbeat_timer()

    def monitor_task(self):
        """监控状态任务"""
        if not self.running:
            return

        try:
            # 检查心跳超时
            if self.last_heartbeat_time:
                time_diff = (datetime.datetime.now() - self.last_heartbeat_time).total_seconds()
                if time_diff > self.timeout_threshold:
                    if self.remote_status != HeartbeatStatus.OFFLINE:
                        self.remote_status = HeartbeatStatus.OFFLINE
                        self.remote_role = MachineRole.BACKUP

                        # 如果本机是备机且对方离线，升级为主机
                        if self.local_role == MachineRole.BACKUP:
                            self.local_role = MachineRole.MASTER
                            print("检测到主机离线，备机自动升级为主机")

                        print("对方心跳超时，标记为离线")
        except Exception as e:
            if self.running:
                print(f"状态监控错误: {str(e)}")

        # 重新启动定时器
        self.start_monitor_timer()
        self.update_status()

    def determine_initial_role(self):
        """确定初始角色"""

        # 使用定时器替代 sleep
        def initial_role_check():
            if not self.running:
                return

            if self.remote_status == HeartbeatStatus.OFFLINE:
                # 对方不在线，自己成为主机
                self.local_role = MachineRole.MASTER
                self.remote_role = MachineRole.BACKUP
                print("对方离线，本机自动成为主机")
            else:
                # 对方在线，比较IP地址
                try:
                    local_ip_int = int(ipaddress.ip_address(self.local_ip))
                    remote_ip_int = int(ipaddress.ip_address(self.remote_ip))

                    if local_ip_int < remote_ip_int:
                        self.local_role = MachineRole.MASTER
                        self.remote_role = MachineRole.BACKUP
                        print(f"IP比较：本机({self.local_ip}) < 对方({self.remote_ip})，本机成为主机")
                    else:
                        self.local_role = MachineRole.BACKUP
                        self.remote_role = MachineRole.MASTER
                        print(f"IP比较：本机({self.local_ip}) > 对方({self.remote_ip})，本机成为备机")
                except:
                    self.local_role = MachineRole.BACKUP
                    print("IP比较失败，本机默认成为备机")
            self.update_status()

        # 设置3秒后执行初始角色判断
        threading.Timer(3.0, initial_role_check).start()

    def listen_heartbeat(self):
        """监听心跳包"""
        while self.running and not self.stop_event.is_set():
            try:
                data, addr = self.udp_socket.recvfrom(1024)

                if addr[0] == self.remote_ip:
                    try:
                        heartbeat_data = json.loads(data.decode('utf-8'))

                        if heartbeat_data.get('type') == 'heartbeat':
                            self.last_heartbeat_time = datetime.datetime.now()
                            self.heartbeat_received = True
                            self.remote_status = HeartbeatStatus.ONLINE

                            # 更新对方角色
                            remote_role_name = heartbeat_data.get('role', 'BACKUP')
                            if remote_role_name == 'MASTER':
                                self.remote_role = MachineRole.MASTER
                            elif remote_role_name == 'BACKUP':
                                self.remote_role = MachineRole.BACKUP
                            else:
                                self.remote_role = MachineRole.BACKUP

                            # 检查双主机情况
                            self.check_dual_master()


                        elif heartbeat_data.get('type') == 'demotion_notification':
                            self.handle_demotion_notification(heartbeat_data)

                    except json.JSONDecodeError:
                        pass

            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"监听心跳失败: {str(e)}")
            self.update_status()

    def check_dual_master(self):
        """检查并解决双主机情况"""
        if (self.local_role == MachineRole.MASTER and
                self.remote_role == MachineRole.MASTER and
                self.remote_status == HeartbeatStatus.ONLINE):

            # 首次检测到双主机
            if self.dual_master_check_time is None:
                self.dual_master_check_time = time.time()
                print("警告：检测到双主机状态！开始解决程序...")
                return

            # 等待一段时间后执行解决方案
            if time.time() - self.dual_master_check_time >= self.dual_master_resolve_delay:
                self.resolve_dual_master()
        else:
            # 重置双主机检测时间
            if self.dual_master_check_time is not None:
                self.dual_master_check_time = None
        self.update_status()

    def resolve_dual_master(self):
        """解决双主机冲突：IP大的降为备机"""
        try:
            local_ip_int = int(ipaddress.ip_address(self.local_ip))
            remote_ip_int = int(ipaddress.ip_address(self.remote_ip))

            if local_ip_int > remote_ip_int:
                # 本机IP较大，降级为备机
                self.local_role = MachineRole.BACKUP
                self.remote_role = MachineRole.MASTER
                print(f"双主机冲突解决：本机IP({self.local_ip}) > 对方IP({self.remote_ip})，本机降级为备机")

                # 发送降级通知
                self.send_demotion_notification()

            elif local_ip_int < remote_ip_int:
                # 本机IP较小，保持主机状态，等待对方降级
                print(f"双主机冲突解决：本机IP({self.local_ip}) < 对方IP({self.remote_ip})，本机保持主机状态")
            else:
                # IP相同的情况（理论上不应该发生）
                print("警告：检测到相同IP地址，使用随机方式解决冲突")
                import random
                if random.choice([True, False]):
                    self.local_role = MachineRole.BACKUP
                    print("随机选择：本机降级为备机")

            self.dual_master_check_time = None

        except Exception as e:
            print(f"解决双主机冲突失败: {str(e)}")
            self.dual_master_check_time = None
        self.update_status()

    def send_demotion_notification(self):
        """发送降级通知"""
        try:
            notification_data = {
                'type': 'demotion_notification',
                'message': 'dual_master_resolved',
                'timestamp': time.time(),
                'from_ip': self.local_ip
            }

            data = json.dumps(notification_data).encode('utf-8')
            self.udp_socket.sendto(data, (self.remote_ip, self.heartbeat_port))

        except Exception as e:
            print(f"发送降级通知失败: {str(e)}")

    def handle_demotion_notification(self, notification_data):
        """处理降级通知"""
        if notification_data.get('message') == 'dual_master_resolved':
            print("收到对方降级通知，双主机冲突已解决")
            # 重置双主机检测时间
            self.dual_master_check_time = None
        self.update_status()
