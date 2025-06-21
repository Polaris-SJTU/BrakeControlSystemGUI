import enum
import struct
from collections import deque
from PyQt5.QtCore import pyqtSignal, QTimer, QDateTime, QTime
# 确保tcp_client.py文件位于一个名为modules的文件夹中
# 如果实际路径不同，请在此处修改import语句
from modules.tcp_client import TcpClient


class SamFrameType(enum.IntEnum):
    """协议帧类型枚举"""
    DC2 = 0x12
    DC3 = 0x13
    ACK = 0x06
    NACK = 0x15
    SDI = 0x85
    BCC = 0x95
    TSQ = 0x9a
    TSD = 0xa5
    ACQ = 0x75
    ACA = 0x7a
    RSR = 0xaa


class SamTcpClient(TcpClient):
    """
    负责与SAM系统进行TCP通信的客户端。
    实现了带ACK/NACK确认、超时重传和网络复位的可靠传输机制。
    """
    sam_event = pyqtSignal(dict)
    post_command_signal = pyqtSignal(str, dict)

    # --- 协议常量 ---
    RETRANSMISSION_TIMEOUT = 40000
    MAX_RETRIES = 2
    MAX_CONSECUTIVE_CRC_ERRORS = 5
    ACA_RESPONSE_TIMEOUT = 5000
    PERIODIC_REPORT_INTERVAL_MS = 10000  # 周期性上报SDI和RSR的时间间隔 (ms)

    def __init__(self, host: str, port: int):
        super().__init__(host, port)

        self._buffer = bytearray()
        self.data_received.connect(self._on_sam_data_received)
        self.socket.connected.connect(lambda: self.on_connection_status_changed(True))
        self.socket.disconnected.connect(lambda: self.on_connection_status_changed(False))
        self.post_command_signal.connect(self._queue_command)

        # --- 状态和序号变量 ---
        self.handshake_complete = False
        self.send_sequence = 0
        self.ack_sequence = 0
        self.my_master_backup_status = 0x55
        self.my_control_mode = 0xaa

        # --- 指令队列 ---
        self._command_queue = deque()

        # --- 用于可靠传输的变量 ---
        self._in_flight_frame = None
        self._retry_count = 0
        self._consecutive_crc_errors = 0
        self.sdi_data_callback = None

        self._retransmission_timer = QTimer(self)
        self._retransmission_timer.setSingleShot(True)
        self._retransmission_timer.timeout.connect(self._on_retransmission_timeout)

        # --- 用于ACQ/ACA流程的变量 ---
        self._is_waiting_for_aca = False
        self._aca_timeout_timer = QTimer(self)
        self._aca_timeout_timer.setSingleShot(True)
        self._aca_timeout_timer.timeout.connect(self._on_aca_timeout)

        # --- 用于TSQ/TSD流程的变量 ---
        self._initial_tsq_timer = QTimer(self)
        self._initial_tsq_timer.setSingleShot(True)
        self._initial_tsq_timer.timeout.connect(self._send_initial_tsq)

        self._daily_tsq_timer = QTimer(self)
        self._daily_tsq_timer.setSingleShot(True)
        self._daily_tsq_timer.timeout.connect(self._on_daily_tsq_trigger)

        # 周期性报告定时器
        self._periodic_report_timer = QTimer(self)
        self._periodic_report_timer.timeout.connect(self._send_periodic_reports)

        print(f"SamTcpClient: Initialized as CLIENT for SAM Server at {host}:{port}.")

    def set_own_status(self, is_master: bool, is_central_control: bool):
        self.my_master_backup_status = 0x55 if is_master else 0xaa
        self.my_control_mode = 0x55 if is_central_control else 0xaa

    def set_sdi_data_callback(self, callback):
        """设置一个回调函数，用于在需要时获取SDI帧的数据内容。"""
        if callable(callback):
            self.sdi_data_callback = callback
            print("  [Callback] SDI data callback has been set.")
        else:
            print("  [Error] Provided SDI data callback is not callable.")

    def _queue_command(self, command: str, data: dict = None):
        """将命令放入队列，并尝试处理队列"""
        print(f"  [Queue] Command '{command}' added to queue.")
        self._command_queue.append((command, data))
        self._process_command_queue()

    def _process_command_queue(self):
        """处理指令队列中的下一个指令"""
        if self._in_flight_frame is not None or not self._command_queue:
            return

        if not self._command_queue:
            return

        command, data = self._command_queue.popleft()
        print(f"  [Queue] Processing command '{command}' from queue.")
        self._execute_command(command, data)

    def _execute_command(self, command: str, data: dict = None):
        """实际执行指令的内部方法"""
        if command == 'REQUEST_CENTRAL_CONTROL':
            self._handle_acq_request()
        elif command == 'REQUEST_TIME_SYNC':
            self._handle_tsq_request()
        elif command == 'SEND_RSR':
            self._send_data_frame(SamFrameType.RSR,
                                  struct.pack('BB', self.my_master_backup_status, self.my_control_mode))
        elif command == 'SEND_SDI':
            if self.sdi_data_callback:
                sdi_data_content = self.sdi_data_callback()
                if isinstance(sdi_data_content, bytes):
                    self._send_data_frame(SamFrameType.SDI, sdi_data_content)
                else:
                    print("  [Error] SDI data callback did not return bytes.")
                    self._process_command_queue()
            else:
                print("  [Warning] SDI data callback not set. Cannot send SDI.")
                self._process_command_queue()
        else:
            print(f"  [Warning] Unknown command '{command}' executed.")

    def on_connection_status_changed(self, is_connected: bool):
        if is_connected:
            self.sam_event.emit({'type': 'connection', 'data': {'status': 'connected'}})
        else:
            self._reset_protocol_layer()

    def _reset_protocol_layer(self):
        was_connected = self.handshake_complete
        self.handshake_complete = False
        self.send_sequence = 0
        self.ack_sequence = 0
        self._retransmission_timer.stop()
        self._aca_timeout_timer.stop()
        self._initial_tsq_timer.stop()
        self._daily_tsq_timer.stop()
        self._periodic_report_timer.stop()
        self._in_flight_frame = None
        self._is_waiting_for_aca = False
        self._retry_count = 0
        self._consecutive_crc_errors = 0
        self._command_queue.clear()
        if was_connected:
            self.sam_event.emit({'type': 'connection', 'data': {'status': 'lost'}})

    def _on_sam_data_received(self, data: bytes):
        self._buffer.extend(data)
        while True:
            start_index = self._buffer.find(0x7D)
            if start_index == -1: return
            if start_index > 0: self._buffer = self._buffer[start_index:]
            end_index = self._buffer.find(0x7E, 1)
            if end_index == -1: return
            raw_frame_with_escape = self._buffer[:end_index + 1]
            self._buffer = self._buffer[end_index + 1:]
            self._process_frame(raw_frame_with_escape)

    def _process_frame(self, raw_frame_with_escape: bytes):
        try:
            payload_with_escape = raw_frame_with_escape[1:-1]
            unescaped_payload = self._deescape_payload(payload_with_escape)
            data_to_check = unescaped_payload[:-2]
            received_crc = struct.unpack('<H', unescaped_payload[-2:])[0]
            calculated_crc = self._calculate_crc(data_to_check)
            if received_crc != calculated_crc:
                self._consecutive_crc_errors += 1
                if self._consecutive_crc_errors >= self.MAX_CONSECUTIVE_CRC_ERRORS:
                    self._reset_protocol_layer()
                else:
                    self._send_nack_frame()
                return
            self._consecutive_crc_errors = 0
            self._parse_and_dispatch(data_to_check)
        except Exception as e:
            print(f"  [Error] Frame processing failed: {e}")

    def _parse_and_dispatch(self, payload: bytearray):
        send_seq, ack_seq, frame_type_val = payload[2], payload[3], payload[4]
        try:
            frame_type = SamFrameType(frame_type_val)

            if self.handshake_complete and frame_type not in [SamFrameType.DC2, SamFrameType.DC3]:
                # 丢帧检查
                if self.ack_sequence != 0 and send_seq != self.ack_sequence and send_seq >= self.ack_sequence + 2:
                    print(
                        f"  [Error] 帧丢失! 期望序号: {self.ack_sequence + 1}, 收到序号: {send_seq}. 正在重新初始化通讯...")
                    self._reset_protocol_layer()
                    return

                self.ack_sequence = send_seq

            handler_map = {
                SamFrameType.DC2: lambda: self._handle_dc2(send_seq, ack_seq),
                SamFrameType.DC3: self._handle_dc3,
                SamFrameType.ACK: lambda: self._handle_ack(ack_seq),
                SamFrameType.NACK: self._handle_nack,
                SamFrameType.RSR: lambda: self._handle_rsr(payload),
                SamFrameType.ACA: lambda: self._handle_aca(payload),
                SamFrameType.TSD: lambda: self._handle_tsd(payload),
            }
            if handler := handler_map.get(frame_type):
                handler()
            else:
                self.sam_event.emit({'type': f'unhandled_{frame_type.name.lower()}', 'data': payload[7:]})
        except Exception as e:
            print(f"  [Error] Dispatching failed: {e}")

    def _send_data_frame(self, frame_type: SamFrameType, data_content: bytes = b''):
        frame_to_send = self._build_frame(frame_type, data_content, self.send_sequence, self.ack_sequence)
        self._in_flight_frame = {"frame_bytes": frame_to_send, "send_seq": self.send_sequence, "type": frame_type}
        self._retry_count = 0
        super().send_data(frame_to_send)
        self._retransmission_timer.start(self.RETRANSMISSION_TIMEOUT)

    def _send_nack_frame(self):
        frame_to_send = self._build_frame(SamFrameType.NACK, b'', self.send_sequence, self.ack_sequence)
        super().send_data(frame_to_send)

    def _handle_dc2(self, send_seq: int, ack_seq: int):
        if send_seq == 0 and ack_seq == 0: self._send_dc3_frame()

    def _send_dc3_frame(self):
        frame_to_send = self._build_frame(SamFrameType.DC3, b'', 0, 0)
        super().send_data(frame_to_send)
        self.handshake_complete = True
        self.send_sequence = 1
        self.ack_sequence = 0
        self.sam_event.emit({'type': 'handshake', 'data': {'status': 'success'}})
        self._initial_tsq_timer.start(10000)
        self._schedule_daily_tsq()
        self._periodic_report_timer.start(self.PERIODIC_REPORT_INTERVAL_MS)

    def _handle_dc3(self):
        pass

    def _handle_ack(self, received_ack_seq: int):
        if not self._in_flight_frame: return
        if self._in_flight_frame['type'] in [SamFrameType.RSR, SamFrameType.SDI]:
            if received_ack_seq == self._in_flight_frame['send_seq']:
                self._retransmission_timer.stop()
                self._in_flight_frame = None
                self.send_sequence = (self.send_sequence % 255) + 1 if self.send_sequence != 0 else 1
                self.sam_event.emit({'type': 'ack_confirm', 'data': {'send_seq': received_ack_seq}})
                self._process_command_queue()

    def _handle_nack(self):
        if self._in_flight_frame:
            self._retransmission_timer.stop()
            self._on_retransmission_timeout(is_nack=True)

    def _handle_rsr(self, payload: bytes):
        data_content = payload[7:]
        if len(data_content) == 2:
            sam_mb, sam_ac = data_content[0], data_content[1]
            status_dict = {'sam_master_backup': sam_mb, 'sam_allow_central_control': sam_ac}
            self.sam_event.emit({'type': 'rsr', 'data': status_dict})
            self._queue_command('SEND_RSR')
        self._process_command_queue()

    def _handle_acq_request(self):
        self._send_data_frame(SamFrameType.ACQ, b'')
        self._is_waiting_for_aca = True
        self._aca_timeout_timer.start(self.ACA_RESPONSE_TIMEOUT)

    def _handle_aca(self, payload: bytes):
        if not (self._is_waiting_for_aca and self._in_flight_frame and self._in_flight_frame[
            'type'] == SamFrameType.ACQ): return
        self._aca_timeout_timer.stop()
        self._retransmission_timer.stop()
        self._is_waiting_for_aca = False
        self._in_flight_frame = None
        self.send_sequence = (self.send_sequence % 255) + 1 if self.send_sequence != 0 else 1
        data_content = payload[7:]
        if len(data_content) == 1:
            code = data_content[0]
            if code == 0x55:
                self.my_control_mode = 0x55
                self.sam_event.emit({'type': 'aca', 'data': {'success': True, 'message': '切换到集中控制模式成功'}})
            else:
                self.sam_event.emit({'type': 'aca', 'data': {'success': False, 'message': '请求被拒绝'}})
        else:
            self.sam_event.emit({'type': 'aca', 'data': {'success': False, 'message': 'ACA帧格式错误'}})
        self._process_command_queue()

    def _handle_tsq_request(self):
        self._send_data_frame(SamFrameType.TSQ, b'')

    def _bcd_to_int(self, bcd_byte: int) -> int:
        return ((bcd_byte & 0xF0) >> 4) * 10 + (bcd_byte & 0x0F)

    def _handle_tsd(self, payload: bytes):
        if not (self._in_flight_frame and self._in_flight_frame['type'] == SamFrameType.TSQ): return
        self._retransmission_timer.stop()
        self._in_flight_frame = None
        self.send_sequence = (self.send_sequence % 255) + 1 if self.send_sequence != 0 else 1
        data_content = payload[7:]
        if len(data_content) == 7:
            try:
                year_decade = self._bcd_to_int(data_content[0])
                year_century = self._bcd_to_int(data_content[1])
                year = year_century * 100 + year_decade
                month, day, hour, minute, second = map(self._bcd_to_int, data_content[2:])
                time_data = {'year': year, 'month': month, 'day': day, 'hour': hour, 'minute': minute, 'second': second}
                self.sam_event.emit({'type': 'time_data', 'data': time_data})
            except Exception as e:
                print(f"  [Error] Parsing TSD data failed: {e}")
        self._process_command_queue()

    def _on_retransmission_timeout(self, is_nack=False):
        if not self._in_flight_frame: return
        if not is_nack: self._retry_count += 1
        if self._retry_count > self.MAX_RETRIES:
            self._in_flight_frame = None
            self._reset_protocol_layer()
            self._process_command_queue()
            return
        super().send_data(self._in_flight_frame['frame_bytes'])
        self._retransmission_timer.start(self.RETRANSMISSION_TIMEOUT)

    def _on_aca_timeout(self):
        if not self._is_waiting_for_aca: return
        self._is_waiting_for_aca = False
        self._in_flight_frame = None
        self.sam_event.emit({'type': 'aca', 'data': {'success': False, 'message': '请求集中控制超时'}})
        self._process_command_queue()

    def _send_initial_tsq(self):
        self._queue_command('REQUEST_TIME_SYNC')

    def _send_periodic_reports(self):
        """周期性地将RSR和SDI指令放入队列"""
        print("  [Periodic] Queuing RSR and SDI.")
        self._queue_command('SEND_RSR')
        self._queue_command('SEND_SDI')

    def _schedule_daily_tsq(self):
        now = QDateTime.currentDateTime()
        target_time = QTime(18, 0, 0)
        next_sync_dt = QDateTime(now.date(), target_time)
        if now.time() >= target_time:
            next_sync_dt = next_sync_dt.addDays(1)
        msecs_to_next_sync = now.msecsTo(next_sync_dt)
        self._daily_tsq_timer.start(msecs_to_next_sync)

    def _on_daily_tsq_trigger(self):
        self._queue_command('REQUEST_TIME_SYNC')
        self._schedule_daily_tsq()

    def _calculate_crc(self, data: bytes) -> int:
        crc, poly = 0x0000, 0x1021
        for byte in data:
            crc ^= (byte << 8)
            for _ in range(8):
                if (crc & 0x8000):
                    crc = (crc << 1) ^ poly
                else:
                    crc <<= 1
            crc &= 0xFFFF
        return crc

    def _deescape_payload(self, payload_with_escape: bytes) -> bytearray:
        data, i = bytearray(), 0
        while i < len(payload_with_escape):
            if payload_with_escape[i] == 0x7F:
                i += 1
                if i >= len(payload_with_escape): raise ValueError("Invalid escape sequence")
                if payload_with_escape[i] == 0xFD:
                    data.append(0x7D)
                elif payload_with_escape[i] == 0xFE:
                    data.append(0x7E)
                elif payload_with_escape[i] == 0xFF:
                    data.append(0x7F)
                else:
                    raise ValueError("Invalid escape sequence")
            else:
                data.append(payload_with_escape[i])
            i += 1
        return data

    def _build_frame(self, frame_type: SamFrameType, data_content: bytes, send_seq: int, ack_seq: int) -> bytes:
        is_data_frame = frame_type.value >= 0x20 and frame_type.value != 0x85
        header_content = struct.pack('BBBB', 0x10, send_seq, ack_seq, frame_type.value)
        crc_data = bytearray([0x04]) + header_content
        if is_data_frame: crc_data.extend(struct.pack('<H', len(data_content)))
        crc_data.extend(data_content)
        crc = self._calculate_crc(crc_data)
        unescaped_payload = crc_data + struct.pack('<H', crc)
        escaped_frame = bytearray([0x7D])
        for byte_val in unescaped_payload:
            if byte_val == 0x7D:
                escaped_frame.extend(b'\x7F\xFD')
            elif byte_val == 0x7E:
                escaped_frame.extend(b'\x7F\xFE')
            elif byte_val == 0x7F:
                escaped_frame.extend(b'\x7F\xFF')
            else:
                escaped_frame.append(byte_val)
        escaped_frame.append(0x7E)
        return bytes(escaped_frame)
