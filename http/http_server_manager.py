from http_request_manager import HttpRequestManager
import usocket as socket


class HttpServerManager:
	def __init__(self, params, port=80):
		self._params = params
		addr = socket.getaddrinfo(b'0.0.0.0', port)[0][-1]
		s = socket.socket()
		s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		s.bind(addr)
		s.listen(3)
		self._sock = s

	def _handle_new_connection(self):
		client_sock, addr = self._sock.accept()
		HttpRequestManager(client_sock, self._params).register(self._executor)

	def _on_err_or_hup(self):
		raise RuntimeError("POLLERR or POLLHUP on HTTP Server")

	def register(self, executor):
		self._executor = executor
		executor.register(
			self._sock,
			on_readable=self._handle_new_connection,
			on_writable=None,
			on_err=self._on_err_or_hup,
			on_hup=self._on_err_or_hup)

# vim: set ts=4 sw=4 noet:
