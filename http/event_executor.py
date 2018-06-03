import uselect as select


class EventExecutor:
	def __init__(self):
		self._handlers = {}
		self._poll = select.poll()
		self._per_loop_callbacks = []

	def register(self, obj, on_readable, on_writable, on_hup, on_err):
		handlers = self._handlers.setdefault(id(obj), {})
		mask = (select.POLLIN if on_readable else 0) | (
		        select.POLLOUT if on_writable else 0)
		handlers['on_readable'] = on_readable
		handlers['on_writable'] = on_writable
		handlers['on_hup'] = on_hup
		handlers['on_err'] = on_err
		self._poll.register(obj, mask)

	def unregister(self, obj):
		self._poll.unregister(obj)
		del self._handlers[id(obj)]

	def register_per_loop_callback(self, cb):
		self._per_loop_callbacks.append(cb)

	def handle_events(self, timeout_ms=500):
		while True:
			for obj, events, *_ in self._poll.ipoll(timeout_ms):
				for cb in self._per_loop_callbacks:
					cb()
				handlers = self._handlers[id(obj)]
				if events & select.POLLERR:
					handlers['on_err']()
				elif events & select.POLLHUP:
					handlers['on_hup']()
				else:
					if events & select.POLLIN:
						handlers['on_readable']()
					if events & select.POLLOUT:
						handlers['on_writable']()

# vim: set ts=4 sw=4 noet:
