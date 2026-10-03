// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <exception>

namespace wivrn
{
// Keep consuming submitted slots after a failure. Async stereo packets are
// committed only after both eyes have completed this batch without throwing.
template <typename Encoders, typename Mask, typename Encode, typename Failure>
void encode_batch(Encoders & encoders, const Mask & submitted, Encode encode, Failure failure)
{
	bool keep = submitted[0] && submitted[1];
	for (auto & encoder: encoders)
	{
		if (!submitted[encoder->stream_idx])
			continue;
		try
		{
			encode(*encoder);
		}
		catch (const std::exception & e)
		{
			keep = false;
			failure(*encoder, e);
		}
	}
	for (auto & encoder: encoders)
		encoder->commit_encoded(keep);
}
} // namespace wivrn
