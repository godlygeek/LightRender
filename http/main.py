# From Arduino to Wemos:
#   if MSB == 1:
#     Control packet.
#     2nd highest bit is lit to ack a received byte, cleared for no data to ack
#     Low 6 bits are a 6 bit integer for number of data packets to follow
#   else:
#     Data packet.
#     Low 7 bits are a 7 bit integer

# Arduino sends acks and advertisements.
# Wemos sends commands.

#     control message if C == 1 else data message
# Arduino settings - Wemos is master, Arduino is slave
#   rMin rMax gMin gMax bMin bMax frameSkip FPS directory
# Arduino state - Arduino is master, Wemos is slave
#   index frame

# Settings (Wemos is master, Arduino is replicant)
#   rMin rMax gMin gMax bMin bMax frameSkip FPS directory
# Playback state (Arduino is master, Wemos is replicant)
#   index frame
# Commands (sent from Wemos to Arduino)
#   1. change arduino settings
#   2. seek forward by/backward by/exactly to offset

import socket
import select
import json

try:
  import esp
  from machine import UART
  EMBEDDED = True
except ImportError:
  import sys
  import serial
  EMBEDDED = False

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
  if not EMBEDDED:
    return serial.Serial(sys.argv[1], 9600, rtscts=True, dsrdtr=True)

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
    fps=20,
    directory=0,
    index=0,
    frame=0)


class ArduinoIOParser:
  def __init__(self):
    self._buf = bytearray(18)
    self._buflen = 0
    self._terminators = 0
    self._message = None

  def _append_character_to_buf(self, c):
    if self._buflen < len(self._buf):
      self._buf[self._buflen] = ord(c)
      self._buflen += 1

  def handle_character(self, c):
    if c == b'\0':
      self._terminators += 1
      if self._terminators == 3:
        self._terminators = 0
        ret = self._buf[:self._buflen]
        self._buflen = 0
        return ret
    else:
      for i in range(self._terminators):
        self._append_character_to_buf(b'\0')
      self._append_character_to_buf(c)
      self._terminators = 0


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
    self._arduino_io_parser = ArduinoIOParser()
    self._arduino_settings_need_update = True

  def _handle_new_connection(self):
    cl, _ = self._listen_socket.accept()
    self._client_conn_by_fd[cl.fileno()] = ClientConn(cl, self._poller)

  def _handle_arduino_message(self, msg):
    if (len(msg) == 18
        and chr(msg[0]) == 'R'
        and chr(msg[3]) == 'G'
        and chr(msg[6]) == 'B'
        and chr(msg[9]) == 'F'
        and chr(msg[12]) == 'V'
        and chr(msg[15]) == '='):
      arduino_settings.update(
          r_min=msg[1],
          r_max=msg[2],
          g_min=msg[4],
          g_max=msg[5],
          b_min=msg[7],
          b_max=msg[8],
          frame_skip=msg[10] - 128,
          fps=msg[11],
          directory=msg[13],
          index=msg[14],
          frame=(msg[16] << 8) + msg[17])
      self._arduino_settings_need_update = False
      print(arduino_settings)
    elif chr(msg[0]) == 'X':
      del MESSAGES[:-4]
      MESSAGES.append(msg[1:])
    else:
      self._arduino_settings_need_update = True

  def _read_from_arduino(self):
    c = self._uart.read(1)
    if c != b'':
      msg = self._arduino_io_parser.handle_character(c)
      print(msg)
      if msg is not None:
        self._handle_arduino_message(msg)
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

  def query_arduino_for_settings(self):
    self._uart.write(b'??????????????????\0\0\0')

  def loop_forever(self):
    self._poller = select.poll()
    self._poller.register(self._uart, select.POLLIN)
    self._poller.register(self._listen_socket, select.POLLIN)
    while True:
      fds_and_events = self._poller.poll(1000)
      for fd, event in fds_and_events:
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

      if not fds_and_events:
        if self._arduino_settings_need_update:
          self.query_arduino_for_settings()

Poller(listen_socket, uart).loop_forever()
# vim: set ts=8 sts=4 sw=2 et:
