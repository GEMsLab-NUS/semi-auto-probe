import unittest
from unittest.mock import patch

from semi_auto_probe.protocol import (
    FRAME_TAIL,
    RESPONSE_HEAD,
    Axis,
    build_clear_position_command,
    build_go_home_command,
    build_read_motion_parameters_command,
    build_write_minimum_speed_command,
    build_write_work_speed_and_acceleration_command,
    checksum,
)
from semi_auto_probe.serial_client import ControllerSerialClient


class FakeSerial:
    is_open = True

    def __init__(self, payload: bytes) -> None:
        self.payload = bytearray(payload)
        self.written = bytearray()
        self.reset_count = 0

    def read(self, size: int = 1) -> bytes:
        if not self.payload:
            return b""
        chunk = bytes(self.payload[:size])
        del self.payload[:size]
        return chunk

    def reset_input_buffer(self) -> None:
        self.reset_count += 1

    def write(self, data: bytes) -> int:
        self.written.extend(data)
        return len(data)

    def flush(self) -> None:
        return None


def position_response(axis: Axis, position: int) -> bytes:
    data = bytes((0x00, 0x00, 0x00, 0x00)) + position.to_bytes(2, "big")
    first_nine = bytes((RESPONSE_HEAD, 0xCB, axis)) + data
    return first_nine + bytes((checksum(first_nine),)) + FRAME_TAIL


def reached_response(axis: Axis) -> bytes:
    first_nine = bytes((RESPONSE_HEAD, 0xB5, axis)) + bytes(6)
    return first_nine + bytes((checksum(first_nine),)) + FRAME_TAIL


def multi_axis_completed_response() -> bytes:
    first_nine = bytes((RESPONSE_HEAD, 0xBE, 0x00)) + bytes(6)
    return first_nine + bytes((checksum(first_nine),)) + FRAME_TAIL


def motion_parameters_response(axis: Axis, minimum_speed: int, work_speed: int, acceleration: int) -> bytes:
    frames = []
    for function_code, value in ((0xB2, minimum_speed), (0xB3, work_speed), (0xB4, acceleration)):
        first_nine = bytes((RESPONSE_HEAD, function_code, axis)) + value.to_bytes(4, "big") + bytes(2)
        frames.append(first_nine + bytes((checksum(first_nine),)) + FRAME_TAIL)
    return b"".join(frames)


def home_completed_response(axis: Axis) -> bytes:
    first_nine = bytes((RESPONSE_HEAD, 0xB7, axis)) + bytes(6)
    return first_nine + bytes((checksum(first_nine),)) + FRAME_TAIL


class SerialClientTest(unittest.TestCase):
    @patch("serial.serial_for_url")
    @patch("serial.Serial")
    def test_socket_url_uses_pyserial_url_handler(self, serial_constructor, serial_for_url) -> None:
        serial_for_url.return_value = FakeSerial(b"")
        client = ControllerSerialClient("socket://127.0.0.1:9500", timeout=0.25)

        client.open()

        serial_constructor.assert_not_called()
        serial_for_url.assert_called_once_with(
            "socket://127.0.0.1:9500",
            baudrate=115200,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=0.25,
            write_timeout=0.25,
        )

    @patch("serial.serial_for_url")
    @patch("serial.Serial")
    def test_local_port_keeps_standard_serial_handler(self, serial_constructor, serial_for_url) -> None:
        serial_constructor.return_value = FakeSerial(b"")
        client = ControllerSerialClient("COM_TEST", timeout=0.25)

        client.open()

        serial_for_url.assert_not_called()
        serial_constructor.assert_called_once_with(
            "COM_TEST",
            baudrate=115200,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=0.25,
            write_timeout=0.25,
        )

    def test_position_reader_resynchronizes_after_fragment(self) -> None:
        client = ControllerSerialClient("COM_TEST", timeout=0.05)
        expected = position_response(Axis.Y, 20)
        client._serial = FakeSerial(bytes.fromhex("0A 7A 0D 0A") + expected)

        self.assertEqual(client._read_position_response(Axis.Y), expected)

    def test_reached_reader_waits_for_axis_b5(self) -> None:
        client = ControllerSerialClient("COM_TEST", timeout=0.05)
        expected = reached_response(Axis.Z)
        client._serial = FakeSerial(position_response(Axis.X, 10) + expected)

        self.assertEqual(client._read_axis_reached_response(Axis.Z, timeout=0.05), expected)

    def test_multi_axis_reader_waits_for_a5_completion(self) -> None:
        client = ControllerSerialClient("COM_TEST", timeout=0.05)
        client._serial = FakeSerial(bytes.fromhex("00 FF A5"))

        self.assertEqual(client._read_multi_axis_completed_response(timeout=0.05), b"\xA5")

    def test_multi_axis_reader_ignores_a5_ack_when_axes_are_expected(self) -> None:
        client = ControllerSerialClient("COM_TEST", timeout=0.05)
        expected = reached_response(Axis.X)
        client._serial = FakeSerial(b"\xA5" + expected)

        completed = client._read_multi_axis_completed_response(timeout=0.05, moving_axes={Axis.X})

        self.assertEqual(completed, expected)

    def test_multi_axis_reader_accepts_b5_for_all_moving_axes(self) -> None:
        client = ControllerSerialClient("COM_TEST", timeout=0.05)
        expected = reached_response(Axis.X) + reached_response(Axis.Y)
        client._serial = FakeSerial(expected)

        completed = client._read_multi_axis_completed_response(timeout=0.05, moving_axes={Axis.X, Axis.Y})

        self.assertEqual(completed, expected)

    def test_multi_axis_reader_accepts_b5_axis_mask(self) -> None:
        client = ControllerSerialClient("COM_TEST", timeout=0.05)
        first_nine = bytes((RESPONSE_HEAD, 0xB5, int(Axis.X) | int(Axis.Y))) + bytes(6)
        expected = first_nine + bytes((checksum(first_nine),)) + FRAME_TAIL
        client._serial = FakeSerial(expected)

        completed = client._read_multi_axis_completed_response(timeout=0.05, moving_axes={Axis.X, Axis.Y})

        self.assertEqual(completed, expected)

    def test_multi_axis_reader_accepts_be_completion_frame(self) -> None:
        client = ControllerSerialClient("COM_TEST", timeout=0.05)
        expected = multi_axis_completed_response()
        client._serial = FakeSerial(expected)

        completed = client._read_multi_axis_completed_response(timeout=0.05, moving_axes={Axis.X, Axis.Y})

        self.assertEqual(completed, expected)

    def test_multi_axis_move_resets_input_writes_and_waits(self) -> None:
        client = ControllerSerialClient("COM_TEST", timeout=0.05)
        fake = FakeSerial(multi_axis_completed_response())
        client._serial = fake

        command, completed = client.move_multi_axis_relative_and_wait({Axis.X: (False, 10, 100, 0)}, timeout=0.05)

        self.assertEqual(completed, multi_axis_completed_response())
        self.assertEqual(bytes(fake.written), command)
        self.assertEqual(fake.reset_count, 1)

    def test_read_motion_parameters_writes_d5_and_parses_response(self) -> None:
        client = ControllerSerialClient("COM_TEST", timeout=0.05)
        fake = FakeSerial(position_response(Axis.Y, 20) + motion_parameters_response(Axis.X, 0, 10, 0))
        client._serial = fake

        command, response, parameters = client.read_motion_parameters(Axis.X)

        self.assertEqual(command, build_read_motion_parameters_command(Axis.X))
        self.assertEqual(bytes(fake.written), command)
        self.assertEqual(response, motion_parameters_response(Axis.X, 0, 10, 0))
        self.assertEqual(parameters.axis, Axis.X)
        self.assertEqual(parameters.minimum_speed, 0)
        self.assertEqual(parameters.work_speed, 10)
        self.assertEqual(parameters.acceleration, 0)
        self.assertEqual(fake.reset_count, 1)

    def test_read_xyz_motion_parameters_reads_each_axis(self) -> None:
        client = ControllerSerialClient("COM_TEST", timeout=0.05)
        fake = FakeSerial(
            motion_parameters_response(Axis.X, 5, 100, 10)
            + motion_parameters_response(Axis.Y, 6, 90, 11)
            + motion_parameters_response(Axis.Z, 7, 80, 12)
        )
        client._serial = fake

        entries = client.read_xyz_motion_parameters()

        self.assertEqual([entry[2].axis for entry in entries], [Axis.X, Axis.Y, Axis.Z])
        self.assertEqual([entry[2].minimum_speed for entry in entries], [5, 6, 7])
        self.assertEqual(bytes(fake.written), b"".join(build_read_motion_parameters_command(axis) for axis in (Axis.X, Axis.Y, Axis.Z)))
        self.assertEqual(fake.reset_count, 3)

    def test_clear_position_is_blocked_without_admin_mode(self) -> None:
        client = ControllerSerialClient("COM_TEST", timeout=0.05)
        client._serial = FakeSerial(b"")

        with self.assertRaises(PermissionError):
            client.clear_position(Axis.Z)
        with self.assertRaises(PermissionError):
            client.send_raw(build_clear_position_command(Axis.ALL), read_length=0)

    def test_clear_position_is_allowed_with_admin_mode(self) -> None:
        client = ControllerSerialClient("COM_TEST", timeout=0.05)
        fake = FakeSerial(b"")
        client._serial = fake
        client.set_admin_mode_enabled(True)

        command = client.clear_position(Axis.Z)

        self.assertEqual(command, build_clear_position_command(Axis.Z))
        self.assertEqual(bytes(fake.written), command)

    def test_go_home_is_blocked_without_admin_mode(self) -> None:
        client = ControllerSerialClient("COM_TEST", timeout=0.05)
        client._serial = FakeSerial(b"")

        with self.assertRaises(PermissionError):
            client.go_home_and_wait(Axis.ALL, timeout=0.05)
        with self.assertRaises(PermissionError):
            client.send_raw(build_go_home_command(Axis.ALL), read_length=0)

    def test_go_home_all_waits_for_xyz_completion(self) -> None:
        client = ControllerSerialClient("COM_TEST", timeout=0.05)
        fake = FakeSerial(
            home_completed_response(Axis.X)
            + home_completed_response(Axis.Y)
            + home_completed_response(Axis.Z)
        )
        client._serial = fake
        client.set_admin_mode_enabled(True)

        command, response = client.go_home_and_wait(Axis.ALL, timeout=0.05)

        self.assertEqual(command, build_go_home_command(Axis.ALL))
        self.assertEqual(bytes(fake.written), command)
        self.assertEqual(len(response), 3 * 12)

    def test_write_motion_parameters_uses_temporary_or_persistent_commands(self) -> None:
        client = ControllerSerialClient("COM_TEST", timeout=0.05)
        fake = FakeSerial(b"")
        client._serial = fake

        temporary = client.write_motion_parameters(Axis.X, 5, 100, 10, persist=False)
        persistent = client.write_motion_parameters(Axis.Y, 6, 90, 11, persist=True)

        expected = (
            build_write_minimum_speed_command(Axis.X, 5)
            + build_write_work_speed_and_acceleration_command(Axis.X, 100, 10)
            + build_write_minimum_speed_command(Axis.Y, 6, persist=True)
            + build_write_work_speed_and_acceleration_command(Axis.Y, 90, 11, persist=True)
        )
        self.assertEqual(bytes(fake.written), expected)
        self.assertEqual(temporary, (expected[:12], expected[12:24]))
        self.assertEqual(persistent, (expected[24:36], expected[36:48]))


if __name__ == "__main__":
    unittest.main()
