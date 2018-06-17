import ujson as json
import utime as time
import uos as os

class HttpRequestManager:
	last_notify = time.ticks_ms()
	waiters = []
	stats = {'http_conns': 0, 'http_reqs': 0}

	def __init__(self, sock, params):
		self.stats['http_conns'] += 1
		self._sock = sock
		self._params = params
		self._executor = None

		self._buf = None
		self._request_body = None
		self._response = None
		self._response_file = None
		self._keep_alive = True

		self._read_handler = self._parse_method
		self._method = None
		self._uri = None
		self._content_length = None

	def close(self):
		if self in self.waiters:
			self.waiters.remove(self)
		self._executor.unregister(self._sock)
		self._sock.close()
		if self._response_file:
			self._response_file.close()

		del self._sock
		del self._executor
		del self._buf
		del self._request_body
		del self._response
		del self._response_file
		del self._keep_alive
		del self._read_handler
		del self._method
		del self._uri
		del self._content_length

	def register(self, executor):
		self._executor = executor
		self._await_socket_readability()

	def _await_socket_readability(self):
		self._executor.register(
			self._sock,
			on_readable=self._read,
			on_writable=None,
			on_err=self.close,
			on_hup=self.close)

	def _await_socket_writability(self):
		self._executor.register(
			self._sock,
			on_readable=None,
			on_writable=self._write,
			on_err=self.close,
			on_hup=self.close)

	def _await_notification(self):
		# Long poll waiting for a settings update
		self.waiters.append(self)
		self._executor.register(
			self._sock,
			on_readable=None,
			on_writable=None,
			on_err=self.close,
			on_hup=self.close)

	@classmethod
	def _notify_waiters(cls):
		cls.last_notify = time.ticks_ms()
		waiters = cls.waiters
		cls.waiters = []
		for waiter in waiters:
			waiter._serve_params()

	@classmethod
	def _notify_waiters_if_timer_elapsed(cls):
		if time.ticks_diff(time.ticks_ms(), cls.last_notify) > 1000:
			cls._notify_waiters()

	def _serve_params(self):
		p = self._params
		pd = dict(r=p.r, R=p.R, g=p.g, G=p.G, b=p.b, B=p.B,
		          A=p.A, s=p.s, S=p.S, d=p.d, p=p.p, v=p.V)
		body = json.dumps(pd).encode('ascii')
		self._send_response(200, b'OK', body=body)

	def _set_params(self):
		try:
			params = json.loads(bytes(self._request_body))
		except Exception:
			self._send_response(400, b'Bad Request')
			return

		for k, v in params.items():
			if not isinstance(v, int) or not k in "ArRgGbBsSdpv":
				self._send_response(400, b'Bad Request')
				return

		self._params.update(params)
		self._notify_waiters()
		self._serve_params()

	def _serve_index(self):
		self._send_response(200, b'OK', stream=open('index.html', 'rb'))

	def _send_response(self, code, status, *, body=None, stream=None):
		conn_hdr = b'keep-alive'
		if code != 200:
			self._keep_alive = False
			conn_hdr = b'close'

		if stream:
			length = stream.seek(0, 2)
			stream.seek(0)
			self._response_file = stream
			body = b''
		else:
			if body is None:
				body = status
			length = len(body)

		resp = (b'HTTP/1.1 %(code)d %(status)s\r\n'
		        b'Content-Length: %(length)d\r\n'
				b'Connection: %(conn_hdr)s\r\n'
		        b'\r\n%(body)s' % dict(
		            code=code, status=status, length=length, body=body,
					conn_hdr=conn_hdr))
		self._response = memoryview(resp)
		self._await_socket_writability()

	def _write(self):
		try:
			sent = self._sock.send(self._response)
		except OSError:
			sent = -1
		if sent > 0:
			self._response = self._response[sent:]
		else:
			self.close()
			return

		if not self._response and self._response_file:
			self._response = memoryview(self._response_file.read(512))
			if not self._response:
				self._response_file.close()
				self._response_file = None

		if not self._response:
			self._response = None

			if self._keep_alive:
				self._await_socket_readability()
			else:
				self.close()

	def _report_parse_error(self):
		self._send_response(400, b'Bad Request')

	def _parse_method(self, buf, idx, length):
		space_idx = buf.find(b' ', idx)
		if space_idx == -1:
			if length >= 4:
				# Max supported method length is 3
				# "GET"
				self._report_parse_error()
				return idx + length
			return  # More bytes needed

		method = buf[idx:space_idx].upper()
		if method == b'GET' or method == b'PUT':
			self._method = method
		else:
			self._send_response(501, b'Not Implemented')
			return idx + length

		self._read_handler = self._parse_uri
		return space_idx + 1

	def _parse_uri(self, buf, idx, length):
		space_idx = buf.find(b' ', idx)
		if space_idx == -1:
			if length >= 13:
				# Max supported uri length is 12
				# "/next_params"
				self._uri = b'/'
				self._read_handler = self._ignore_til_eol
				return idx + length
			return  # More bytes needed

		self._uri = buf[idx:space_idx]
		self._read_handler = self._ignore_til_eol
		return space_idx + 1

	def _ignore_til_eol(self, buf, idx, length):
		eol_idx = buf.find(b'\n', idx)
		if eol_idx == -1:
			return idx + length
		self._read_handler = self._parse_header
		return eol_idx + 1

	def _parse_header(self, buf, idx, length):
		eol_idx = buf.find(b'\n', idx)
		if eol_idx == -1:
			if length >= 23:
				# Max supported header line length is 22
				# "Content-Length: 1234\r\n"
				self._read_handler = self._ignore_til_eol
				return idx + length
			return  # More bytes needed

		if eol_idx == idx or buf[eol_idx - 1] != 13:
			# Missing CR before NL
			self._report_parse_error()
			return idx + length

		if eol_idx == idx + 1:
			# End of headers (got an empty header line)
			if self._content_length is None:
				if self._method == b'GET':
					# GETs don't require a Content-Length
					self._route_request()
					self._read_handler = self._parse_method
				else:
					# PUTs do
					self._send_response(411, b'Length Required')
			else:
				self._request_body = bytearray()
				self._read_handler = self._read_body
			return eol_idx + 1

		colon_idx = buf.find(b':', idx)
		if colon_idx - idx == 14:
			if buf[idx:colon_idx].lower() == b'content-length':
				try:
					self._content_length = int(buf[colon_idx + 1:eol_idx])
				except ValueError:
					self._report_parse_error()
					return idx + length

		return eol_idx + 1

	def _read_body(self, buf, idx, length):
		if self._content_length is not None:
			if self._content_length < length:
				length = self._content_length
			self._content_length -= length

		self._request_body.extend(buf[idx:idx + length])

		if self._content_length == 0:
			self._route_request()
			self._read_handler = self._parse_method

		return idx + length

	def _read(self):
		try:
			buf = self._sock.recv(64)
		except OSError:
			buf = None
		if not buf:
			if self._buf or self._read_handler != self._parse_method:
				# More bytes were required, but never came.
				self._report_parse_error()
			return

		if self._buf:
			buf = self._buf + buf
			self._buf = None

		idx = 0
		size = len(buf)
		while idx < size:
			new_idx = self._read_handler(buf, idx, size - idx)
			if new_idx is None:
				self._buf = buf[idx:]
				break
			idx = new_idx

	def _route_request(self):
		self.stats['http_reqs'] += 1
		if self._uri == b'/params':
			if self._method == b'PUT':
				return self._set_params()
			elif self._method == b'GET':
				return self._serve_params()
		elif self._uri == b'/next_params':
			if self._method == b'GET':
				return self._await_notification()
		elif self._uri == b'/err.log':
			if self._method == b'GET':
				try:
					body = open('err.log', 'rb').read()
					os.remove('err.log')
					return self._send_response(200, b'OK', body=body)
				except OSError:
					return self._send_response(200, b'OK', body=b'<none>\n')
		elif self._uri == b'/debug':
			if self._method == b'GET':
				body = json.dumps(self.stats)
				return self._send_response(200, b'OK', body=body)
		else:
			if self._method == b'GET':
				return self._serve_index()
			return self._send_response(404, b'Not Found')
		self._send_response(405, b'Method Not Allowed')

# vim: set ts=4 sw=4 noet:
