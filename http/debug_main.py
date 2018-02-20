from ucollections import OrderedDict
from machine import UART
from utime import ticks_ms, ticks_diff
import esp
import json
import machine
import select
import socket
import socket
import sys
import time

DISABLE_UART = 1

LAST_BYTE_READ = b''
LAST_POLL = 0

HTML = b"""<!DOCTYPE html>
<html>
    <head> <title>Hello World!</title> </head>
    <body> <h1>Read: %s</h1>
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
    if not DISABLE_UART:
        esp.osdebug(None)
        return UART(0, 9600)


class ClientConn:
    def __init__(self, sock, poller):
        self._sock = sock
        self._poller = poller
        self._uri = None
        self._buf = b''

        sock.setblocking(False)
        poller.register(sock, select.POLLIN)

    def _handle_request(self):
        global LAST_POLL
        LAST_POLL += 1
        self._response = (b'HTTP/1.0 200 OK\r\n\r\n'
                + HTML % ("%s (%s)" % (LAST_BYTE_READ, LAST_POLL)))
        self._response_idx = 0
        self._poller.modify(self._sock, select.POLLOUT)

    def handle_readable(self):
        # Request line must fit in 128 bytes
        buf = self._sock.recv(64)
        self._buf = self._buf[-64:] + buf

        if not buf or self._buf[-4:] == b'\r\n\r\n':
            self._handle_request()
            return

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
        self._client_conn_by_socket = {}

    def _handle_new_connection(self):
        cl, _ = self._listen_socket.accept()
        self._client_conn_by_socket[id(cl)] = ClientConn(cl, self._poller)

    def _read_from_arduino(self):
        global LAST_BYTE_READ
        LAST_BYTE_READ = self._uart.read(1)

    def _handle_browser_request(self, client, event):
        conn = self._client_conn_by_socket[id(client)]
        if event == select.POLLIN:
            conn.handle_readable()
        elif event == select.POLLOUT:
            conn.handle_writable()
        else:
            conn.handle_err_or_hup()

    def loop_forever(self):
        self._poller = select.poll()
        if not DISABLE_UART:
            self._poller.register(self._uart, select.POLLIN)
        self._poller.register(self._listen_socket, select.POLLIN)
        while True:
            sources_and_events = self._poller.poll(50)
            #self._send_next_command_byte_if_ready()
            for source, event, *rest in sources_and_events:
                if source is self._listen_socket:
                    self._handle_new_connection()
                elif source is self._uart:
                    self._read_from_arduino()
                else:
                    self._handle_browser_request(source, event)


def main():
    listen_socket = setup_listening_socket()
    uart = setup_serial()
    Poller(listen_socket, uart).loop_forever()


def meta_main():
    addr = ('0.0.0.0', 8080)
    debug_socket = socket.socket()
    debug_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    debug_socket.bind(addr)

    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        try:
            debug_socket.listen(0)
            client, _ = debug_socket.accept()
            while True:
                data = client.readline()
                if not data or data == b'\r\n':
                    break
            client.send('HTTP/1.0 200 OK\r\n\r\n')
            sys.print_exception(exc, client)
            client.close()
        finally:
            machine.reset()

meta_main()
