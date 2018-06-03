from http_server_manager import HttpServerManager
from http_request_manager import HttpRequestManager
from dns_request_manager import DnsRequestManager
from event_executor import EventExecutor
from uart_manager import UartManager
from params import Params

import gc
import sys
try:
	import machine
except:
	import umachine as machine
import uos as os


def main():
	executor = EventExecutor()
	periodic_broadcast = HttpRequestManager._notify_waiters_if_timer_elapsed
	executor.register_per_loop_callback(periodic_broadcast)

	uart_manager = UartManager()
	params = Params(uart_manager)
	HttpServerManager(params).register(executor)
	DnsRequestManager().register(executor)
	uart_manager.register(executor)
	executor.handle_events()


def main_wrapper():
	try:
		main()
	except Exception as exc:
		gc.collect()
		machine.UART(0).init(115200)
		try:
			os.stat('err.log')
		except OSError:
			with open('err.log', 'w') as errlog:
				sys.print_exception(exc, errlog)
		machine.reset()

main_wrapper()

# vim: set ts=4 sw=4 noet:
