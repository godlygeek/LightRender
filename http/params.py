import utime as time

class Params:
	def __init__(self, uart_manager):
		uart_manager.set_read_callback(self._update_state)
		self._uart_manager = uart_manager
		self._last_cmd_sent = time.ticks_ms()
		self._first_read = True
		self.A = 255
		self.r = self.g = self.b = 0
		self.R = self.G = self.B = 255
		self.s = self.S = 0
		self.d = self.v = self.V = self.p = 0

	def update(self, params):
		changed = set()
		for k, v in params.items():
			if v != getattr(self, k):
				setattr(self, k, v)
				changed.add(k)

		if changed & set("ArRgGbB"):
			self._send_color_update()
		if changed & set("sS"):
			self._send_speed_update()
		if changed & set("dvp"):
			self._send_video_update()

	def dim(self, c):
		return getattr(self, c) * self.A // 255

	def _send_color_update(self):
		dim = self.dim
		self._send_command(b'C%02x%02x%02x%02x%02x%02x\n' % (
			dim('r'), dim('R'), dim('g'), dim('G'), dim('b'), dim('B')))

	def _send_speed_update(self):
		self._send_command(b'S%02x%02x\n' % (self.s + 128, self.S))

	def _send_video_update(self):
		self._send_command(b'V%02x%02x%04x\n' % (self.d, self.v, self.p))

	def _send_command(self, msg):
		self._uart_manager.write(msg)
		self._last_cmd_sent = time.ticks_ms()

	def _update_state(self, msg):
		try:
			assert msg[26] == 0x0A
			r = int(msg[0:2], 16)
			R = int(msg[2:4], 16)
			g = int(msg[4:6], 16)
			G = int(msg[6:8], 16)
			b = int(msg[8:10], 16)
			B = int(msg[10:12], 16)
			s = int(msg[12:14], 16) - 128
			S = int(msg[14:16], 16)
			p = int(msg[16:20], 16)
			d = int(msg[20:22], 16)
			V = int(msg[22:24], 16)  # curr video
			v = int(msg[24:26], 16)  # last selected video
		except Exception:
			return

		if self._first_read:
			self._first_read = False
			self.r = r
			self.R = R
			self.g = g
			self.G = G
			self.b = b
			self.B = B
			self.s = s
			self.S = S
			self.d = d

		self.p = p
		self.V = V

		dim = self.dim
		dr = dim('r')
		dg = dim('g')
		db = dim('b')
		dR = dim('R')
		dG = dim('G')
		dB = dim('B')

		if time.ticks_diff(time.ticks_ms(), self._last_cmd_sent) > 1000:
			if [r, g, b, R, G, B] != [dim(c) for c in 'rgbRGB']:
				self._send_color_update()
			if s != self.s or S != self.S:
				self._send_speed_update()
			if d != self.d:
				self._send_video_update()

# vim: set ts=4 sw=4 noet:
