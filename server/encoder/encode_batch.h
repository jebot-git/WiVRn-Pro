// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "batch_executor.h"
#include <array>
#include <exception>

namespace wivrn
{
// Keep consuming submitted slots after a failure. Async stereo packets are
// committed only after both eyes have completed this batch without throwing.
template <typename Encoders, typename Mask, typename Encode, typename Failure>
void encode_batch(Encoders & encoders, const Mask & submitted, Encode encode, Failure failure, batch_executor * executor = nullptr)
{
	std::array<std::exception_ptr, 3> errors{};
	auto work = [&](size_t i) {
		if (i >= encoders.size() || !submitted[encoders[i]->stream_idx])
			return;
		try
		{
			encode(*encoders[i]);
		}
		catch (...)
		{
			errors[i] = std::current_exception();
		}
	};
	if (executor)
		executor->run(work);
	else
		for (size_t i = 0; i < encoders.size(); ++i)
			work(i);
	bool keep = submitted[0] && submitted[1];
	for (const auto & error: errors)
		keep &= !error;
	// Commit only after all workers have completed. A failed eye discards the
	// entire stereo batch; error reporting/recovery is serialized afterwards.
	for (auto & encoder: encoders)
		encoder->commit_encoded(keep);
	for (size_t i = 0; i < encoders.size(); ++i)
		if (errors[i])
			try
			{
				std::rethrow_exception(errors[i]);
			}
			catch (const std::exception & e)
			{
				failure(*encoders[i], e);
			}
}
} // namespace wivrn
