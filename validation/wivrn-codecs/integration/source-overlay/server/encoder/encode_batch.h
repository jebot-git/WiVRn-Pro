// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "batch_executor.h"
#include <array>
#include <exception>
#include <limits>

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
#ifdef WIVRN_CODEC_TESTS
    bool capped=false;
    for(const auto& encoder:encoders)capped|=encoder->test_pair_capped();
    if(capped){
        size_t bytes=0;bool valid=encoders.size()>=2&&encoders.size()<=3;std::array<bool,2>seen{};
        for(const auto&encoder:encoders){
            auto n=encoder->staged_output_bytes();auto eye=encoder->stream_idx;
            if(eye<2){
                valid&=encoder->test_pair_capped()&&!seen[eye]&&n>48&&n<=262144;seen[eye]=true;
                if(n>std::numeric_limits<size_t>::max()-bytes){valid=false;bytes=std::numeric_limits<size_t>::max();}
                else bytes+=n;
                valid&=bytes<=262144;
            }else{
                // Native construction retains an inactive alpha encoder. It may
                // not submit or carry staged bytes in this opaque stereo profile.
                valid&=eye==2&&!submitted[2]&&!encoder->test_pair_capped()&&n==0;
            }
        }
        valid&=seen[0]&&seen[1];
        if(!valid||!keep){
            const char*reason=bytes>262144?"cap_exceeded":!valid?"incomplete_or_invalid_pair":"batch_failed_or_unsubmitted";
            keep=false;for(auto&encoder:encoders)if(encoder->test_pair_capped())encoder->test_pair_rejected(bytes,reason);
        }
    }
#endif
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
