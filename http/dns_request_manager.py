import usocket as socket

DNS_DOMAINS = [
	b'\x11connectivitycheck\7gstatic\3com', # Google
	b'\x11connectivitycheck\7android\3com', # Google
	b'\x08clients3\6google\3com',           # Google
	b'\x08clients1\6google\3com',           # Google
	b'\7clients\1l\6google\3com',           # Google
	b'\x07captive\5apple\3com',             # An IOS one
	b'\x04camp',                            # The .camp TLD
]


class DnsRequestManager:
	def __init__(self):
		addr = socket.getaddrinfo(b'0.0.0.0', 53)[0][-1]
		s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		s.bind(addr)
		self._sock = s

	def _handle_new_connection(self):
		req, addr = self._sock.recvfrom(512)
		if not 16 < len(req):
			return  # too short

		name_end = req.find(b'\0', 12)
		if name_end == -1:
			return  # no terminator for domain query string

		q_end = name_end + 5
		if len(req) < q_end:
			return  # too short

		resp = bytearray(req[:q_end])

		resp[2] = 0b10000001     # response, recursion desired
		resp[3] = 0b10000000     # recursion available
		resp[6] = resp[7] = 0    # no answer
		resp[8] = resp[9] = 0    # no nameservers
		resp[10] = resp[11] = 0  # no additional

		qr = req[2] & 0x80
		opcode = (req[2] >> 3) & 0x15
		qcount = req[4] * 256 + req[5]
		qtype = req[name_end + 1] * 256 + req[name_end + 2]
		qclass = req[name_end + 3] * 256 + req[name_end + 4]

		if qr != 0 or opcode != 0 or qcount != 1 or qtype != 1 or qclass != 1:
			# Not a query or not a standard query or not looking up 1 address
			# or type A or not class IN
			resp[3] |= 4  # Not Implemented
			self._sock.sendto(resp, addr)
			return

		for domain in DNS_DOMAINS:
			if req.find(domain) + len(domain) == name_end:
				break
		else:
			# Not for a domain we'll serve
			resp[3] |= 9  # Server Not Authoritative for zone
			self._sock.sendto(resp, addr)
			return

		resp[7] = 1  # one answer
		resp.extend(
			b'\xc0\x0c'           # pointer to question url
			b'\x00\x01'           # type A
			b'\x00\x01'           # class IN
			b'\x00\x00\x08\x00'   # TTL 2048 seconds
			b'\x00\x04'           # response is 4 bytes (an IP address)
			b'\xc0\xa8\x04\x01')  # 192.168.4.1 as octets
		self._sock.sendto(resp, addr)

	def _on_err_or_hup(self):
		raise RuntimeError("POLLERR or POLLHUP on DNS Server")

	def register(self, executor):
		self._executor = executor
		executor.register(
			self._sock,
			on_readable=self._handle_new_connection,
			on_writable=None,
			on_err=self._on_err_or_hup,
			on_hup=self._on_err_or_hup)

# vim: set ts=4 sw=4 noet:
