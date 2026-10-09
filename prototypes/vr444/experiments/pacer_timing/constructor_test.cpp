// SPDX-License-Identifier: MIT
// Host-only fault injection: no worker is allowed to start in this test.
#include "compositor/pacer.h"
#include "util/u_debug.h"
#include <atomic>
#include <cerrno>
#include <iostream>
#include <pthread.h>
#include <stdexcept>
#include <string>
#include <system_error>

static std::atomic<unsigned> worker_attempts{0};
// Executable symbol interposition provides a deterministic construction fault.
// It also verifies that a diagnostic-path error occurs before worker startup.
extern "C" int pthread_create(pthread_t *, const pthread_attr_t *, void *(*)(void *), void *) noexcept
{
	worker_attempts.fetch_add(1);
	return EAGAIN;
}
extern "C" void u_var_add_root(void *, const char *, bool) {}
extern "C" void u_var_remove_root(void *) {}
extern "C" void u_var_add_draggable_f32(void *, u_var_draggable_f32 *, const char *) {}
extern "C" float debug_get_float_option(const char *, float value) { return value; }

int main(int argc, char ** argv)
{
	if (argc != 2) return 2;
	const bool trace_error = std::string(argv[1]) == "trace-error";
	try
	{
		wivrn::pacer instance(11'111'111);
		std::cerr << "worker fault injection did not run\n";
		return 3;
	}
	catch (const std::system_error &)
	{
		if (trace_error || worker_attempts.load() != 1) return 4;
		std::cout << "worker-start fault reached after diagnostic initialization\n";
	}
	catch (const std::runtime_error & error)
	{
		if (!trace_error || worker_attempts.load() != 0) return 5;
		std::cout << "diagnostic error before worker start: " << error.what() << '\n';
	}
	return 0;
}
