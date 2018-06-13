try:
	import machine
except:
	import umachine as machine


class UartManager:
	def __init__(self):
		self._uart = machine.UART(0, 1200)
		self._read_callback = None
		self._tx_buf = b''
		self._rx_buf = b''

	def set_read_callback(self, read_callback):
		self._read_callback = read_callback

	def write(self, message):
		self._tx_buf += message
		self._rx_or_tx()

	def _read(self):
		self._rx_buf += self._uart.read(1)
		if self._rx_buf[-1] == 10:
			if self._read_callback:
				self._read_callback(self._rx_buf)
			self._rx_buf = b''
		if len(self._rx_buf) > 26:
			self._rx_buf = b''

	def _write(self):
		self._uart.write(self._tx_buf[0:1])
		self._tx_buf = self._tx_buf[1:]
		if not self._tx_buf:
			self._rx_only()

	def _handle_err_or_hup(self):
		pass

	def _rx_only(self):
		self._executor.register(
			self._uart,
			on_readable=self._read,
			on_writable=None,
			on_err=self._handle_err_or_hup,
			on_hup=self._handle_err_or_hup)

	def _rx_or_tx(self):
		self._executor.register(
			self._uart,
			on_readable=self._read,
			on_writable=self._write,
			on_err=self._handle_err_or_hup,
			on_hup=self._handle_err_or_hup)

	def register(self, executor):
		self._executor = executor
		self._rx_only()

# vim: set ts=4 sw=4 noet:
