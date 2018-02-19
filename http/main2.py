# Arduino advertises current status once per second.
# The message consists of a series of fields, followed by a nul terminator.
# Each field is represented as 2 hexadecimal characters.
# Fields:
#   Rmin as 2 hex bytes
#   Rmax as 2 hex bytes
#   Gmin as 2 hex bytes
#   Gmax as 2 hex bytes
#   Bmin as 2 hex bytes
#   Bmax as 2 hex bytes
#   Speed as 2 hex bytes
#   FrameStretch as 2 hex bytes
#   CurSecHi as 2 hex bytes
#   CurSecLo as 2 hex bytes
#   TotSecHi as 2 hex bytes
#   TotSecLo as 2 hex bytes
#   Dir as 2 hex bytes
#   Idx as 2 hex bytes

# Wemos sends commands, at a rate of no more than 1 byte / 50ms.
# Each command consists of:
#   1 byte type code
#   4 bytes containing hex, representing 2 different integers
#   1 byte containing a nul terminator
# Command types:
#   'R' Rmin Rmax - Change Red Levels
#   'G' Gmin Gmax - Change Green Levels
#   'B' Bmin Bmax - Change Blue Levels
#   'S' Speed FrameStretch - Change Speeds
#   'V' Dir Idx - Change Video
#   'S' SecHi SecLo - Seek in Video

from machine import UART
from utime import ticks_ms, ticks_diff
import esp
import json
import select
import socket

MESSAGES = [b'Hello World']

HTML = b"""<!DOCTYPE html>
<html>
    <head> <title>Hello World!</title> </head>
    <body> <h1>%s</h1>
    </body>
</html>
"""


def setup_listening_socket():
    addr = ('0.0.0.0', 80)
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(3)
    return s


def setup_serial():
    esp.osdebug(None)
    return UART(0, 9600)


listen_socket = setup_listening_socket()
uart = setup_serial()

arduino_settings = dict(
    r_min=0,
    r_max=255,
    g_min=0,
    g_max=255,
    b_min=0,
    b_max=255,
    frame_skip=0,
    frame_stretch=20,
    directory=0,
    index=0)

arduino_status = dict(
    cur_sec=0,
    tot_sec=1,
    index=0)


class ArduinoStatusParser:
    def __init__(self):
        self._buf = bytearray(28)
        self._msglen = 0

    def _parse_arduino_settings(self):
        if self._msglen == 28:
            try:
                return dict(
                        r_min=int(self._buf[0:2], 16),
                        r_max=int(self._buf[2:4], 16),
                        g_min=int(self._buf[4:6], 16),
                        g_max=int(self._buf[6:8], 16),
                        b_min=int(self._buf[8:10], 16),
                        b_max=int(self._buf[10:12], 16),
                        frame_skip=int(self_buf[12:14], 16),
                        frame_stretch=int(self_buf[14:16], 16),
                        cur_sec=int(self_buf[16:20], 16),
                        tot_sec=int(self_buf[20:24], 16),
                        directory=int(self_buf[24:26], 16),
                        index=int(self_buf[26:28], 16))
            except ValueError:
                pass

    def handle_character(self, c):
        if c == b'\0':
            ret = self._parse_arduino_settings()
            self._msglen = 0
            return ret

        if self._msglen < len(self._buf):
            self._buf[self._msglen] = ord(c)
        self._msglen += 1


class CommandSender:
    def __init__(self, uart, commands):
        self._send_byte = uart.write
        self._next_command_pair = commands.popitem
        self._current_command = bytearray()
        self._current_command_pos = 0
        self._last_sent = ticks_ms()

    def send_next_byte_if_ready(self):
        now = ticks_ms()
        if ticks_diff(now, self._last_sent) < 50:
            return  # too soon

        if self._current_command_pos >= len(self._current_command):
            # nothing left in current-command buffer; send a new command
            try:
                self._current_command[:] = self._next_command_pair()[1]
                self._current_command_pos = 0
            except KeyError:
                pass  # no new command is enqueued yet

        if self._current_command_pos >= len(self._current_command):
            return  # no command to send

        self._send_byte(self._current_command[self._current_command_pos])
        self._current_command_pos += 1
        self._last_sent = now


class ClientConn:
    def __init__(self, sock, poller):
        self._sock = sock
        self._poller = poller
        self._uri = None
        self._buf = b''

        sock.setblocking(False)
        poller.register(sock, select.POLLIN)

    def _handle_request(self):
        path, _, params = self._uri.partition(b'?')
        params = params.split(b'&')
        params = dict(pair.split(b'=', 1) for pair in params if pair)
        if self._uri.startswith(b'/settings'):
            for k, v in params.items():
                try:
                    k = k.decode('utf-8')
                    v = int(v)
                    if k in arduino_settings:
                        arduino_settings[k] = int(v)
                except UnicodeDecodeError:
                    pass
                except ValueError:
                    pass
            self._response = (b'HTTP/1.0 200 OK\r\n\r\n'
                                                + json.dumps(arduino_settings).encode('utf-8'))
        else:
            self._response = (b'HTTP/1.0 200 OK\r\n\r\n'
                                                + HTML % str(MESSAGES).encode('utf-8'))
        print(self._response)
        self._response_idx = 0
        self._poller.modify(self._sock, select.POLLOUT)

    def handle_readable(self):
        # Request line must fit in 128 bytes
        buf = self._sock.recv(64)
        self._buf = self._buf[-64:] + buf

        if not self._uri and b'\r\n' in self._buf:
            try:
                request_line_end = buf.index(b'\r\n')
                space1 = buf.index(b' ', 0, request_line_end)
                space2 = buf.index(b' ', space1 + 1, request_line_end)
                self._uri = buf[space1 + 1:space2]
            except ValueError:
                self.handle_err_or_hup()

        if not buf or self._buf[-4:] == b'\r\n\r\n':
            if self._uri:
                self._handle_request()
            else:
                self.handle_err_or_hup()
            return

        if self._uri:
            return # Throw away headers

    def handle_writable(self):
        sent = self._sock.send(self._response[self._response_idx:])
        if sent > 0:
            self._response_idx += sent
        else:
            self.handle_err_or_hup()

    def handle_err_or_hup(self):
        self._poller.unregister(self._sock)
        self._sock.close()


class Poller:
    def __init__(self, listen_socket, uart):
        self._listen_socket = listen_socket
        self._uart = uart
        self._client_conn_by_fd = {}
        self._arduino_status_parser = ArduinoIOParser()
        self._arduino_commands = {}
        self._send_next_command_byte_if_ready = CommandSender(
                uart, self._arduino_commands).send_next_byte_if_ready

    def _send_command(self, type_code, byte1, byte2):
        command = b'%s%02X%02X\0' % (type_code, byte1, byte2)
        self._arduino_commands[type_code] = command

    def _handle_new_connection(self):
        cl, _ = self._listen_socket.accept()
        self._client_conn_by_fd[cl.fileno()] = ClientConn(cl, self._poller)

    def _handle_arduino_status(self, actual):
        configured = arduino_settings

        if (configured['r_min'] != actual['r_min'] or
                configured['r_max'] != actual['r_max']):
            self._send_command(b'R', configured['r_min'], configured['r_max'])

        if (configured['g_min'] != actual['g_min'] or
                configured['g_max'] != actual['g_max']):
            self._send_command(b'G', configured['g_min'], configured['g_max'])

        if (configured['b_min'] != actual['b_min'] or
                configured['b_max'] != actual['b_max']):
            self._send_command(b'B', configured['b_min'], configured['b_max'])

        if (configured['frame_skip'] != actual['frame_skip'] or
                configured['frame_stretch'] != actual['frame_stretch']):
            self._send_command(b'S',
                    configured['frame_skip'], configured['frame_stretch'])

        if configured['directory'] != actual['directory']:
            self._send_command(b'V',
                    configured['directory'], configured['index'])

        for key in arduino_status:
            arduino_status[key] = actual[key]

    def _read_from_arduino(self):
        c = self._uart.read(1)
        if c != b'':
            status = self._arduino_status_parser.handle_character(c)
            print(status)
            if status is not None:
                self._handle_arduino_status(status)
        else:
            self._poller.unregister(self._uart)

    def _handle_browser_request(self, fd, event):
        conn = self._client_conn_by_fd[fd]
        if event == select.POLLIN:
            conn.handle_readable()
        elif event == select.POLLOUT:
            conn.handle_writable()
        else:
            conn.handle_err_or_hup()

    def loop_forever(self):
        self._poller = select.poll()
        self._poller.register(self._uart, select.POLLIN)
        self._poller.register(self._listen_socket, select.POLLIN)
        while True:
            fds_and_events = self._poller.poll(50)
            self._send_next_command_byte_if_ready()
            for fd, event, *rest in fds_and_events:
                print(fd, event)
                if fd is self._listen_socket.fileno():
                    self._handle_new_connection()
                elif fd is self._uart.fileno():
                    if event & select.POLLIN:
                        self._read_from_arduino()
                    else:
                        self._poller.unregister(self._uart)
                else:
                    self._handle_browser_request(fd, event)

Poller(listen_socket, uart).loop_forever()

# vim: set ts=8 sts=4 sw=4 et:
