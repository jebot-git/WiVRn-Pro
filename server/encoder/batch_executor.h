// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <array>
#include <condition_variable>
#include <functional>
#include <mutex>
#include <thread>

namespace wivrn
{
// One persistent worker per stream. run() is synchronous: source images and
// codec instances remain owned until every worker finishes, including failures.
class batch_executor
{
	std::mutex mutex;
	std::condition_variable wake, done;
	std::function<void(size_t)> job;
	size_t generation = 0, remaining = 0;
	bool stopping = false;
	std::array<std::jthread, 3> workers;

public:
	batch_executor()
	{
		try
		{
			for (size_t i = 0; i < workers.size(); ++i)
				workers[i] = std::jthread([this, i] {
					size_t observed = 0;
					std::unique_lock lock(mutex);
					while (true)
					{
						wake.wait(lock, [&] { return stopping || generation != observed; });
						if (stopping)
							return;
						observed = generation;
						lock.unlock();
						job(i); // caller catches exceptions inside the job
						lock.lock();
						if (--remaining == 0)
							done.notify_one();
					}
				});
		}
		catch (...)
		{
			{
				std::lock_guard lock(mutex);
				stopping = true;
			}
			wake.notify_all();
			for (auto & worker: workers)
				if (worker.joinable())
					worker.join();
			throw;
		}
	}
	~batch_executor()
	{
		{
			std::lock_guard lock(mutex);
			stopping = true;
		}
		wake.notify_all();
		for (auto & worker: workers)
			if (worker.joinable())
				worker.join();
	}
	void run(std::function<void(size_t)> function)
	{
		std::unique_lock lock(mutex);
		job = std::move(function);
		remaining = workers.size();
		++generation;
		wake.notify_all();
		done.wait(lock, [&] { return remaining == 0; });
		job = {};
	}
};
} // namespace wivrn
