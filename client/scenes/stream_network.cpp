/*
 * WiVRn VR streaming
 * Copyright (C) 2022  Guillaume Meunier <guillaume.meunier@centraliens.net>
 * Copyright (C) 2022  Patrick Nicolas <patricknicolas@laposte.net>
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 */

#include "stream.h"

#include "application.h"
#include "utils/i18n.h"
#include "utils/named_thread.h"

#include <spdlog/spdlog.h>
#include <uni_algo/case.h>

void scenes::stream::process_packets()
{
#ifdef __ANDROID__
	application::instance().setup_jni();
#endif
	while (state_ != state::shutdown)
	{
		try
		{
#ifdef __ANDROID__
			#if !WIVRN_AUDIO_AB_STOCK
			if (audio_handle)
			{
				audio_handle->set_capture_enabled(mic_capture_enabled);
				if (request_mic_restart.exchange(false))
					audio_handle->restart_microphone();
				audio_handle->send_microphone_data();
			}
			#endif
			network_session->poll(*this, std::chrono::milliseconds(audio_handle || headset_capture_id ? 10 : 500));
            poll_headset_capture();
#else
			network_session->poll(*this, std::chrono::milliseconds(500));
#endif
		}
		catch (std::exception & e)
		{
			spdlog::info("Exception in network thread, exiting: {}", e.what());
			exit();
		}
	}
    stop_headset_capture();
}

void scenes::stream::stop_headset_capture() {
#ifdef __ANDROID__
    auto & env = jni::jni_thread::env(); auto activity = application::native_app()->activity->clazz;
    auto cls = env.GetObjectClass(activity); auto method = env.GetMethodID(cls, "stopHeadsetCapture", "()V");
    if (method) env.CallVoidMethod(activity,method);
    if (env.ExceptionCheck()) env.ExceptionClear();
    env.DeleteLocalRef(cls);
#endif
    headset_capture_id=0; headset_capture_status.clear();
}
void scenes::stream::operator()(to_headset::headset_capture_control && value) {
    if (!value.enabled || !value.request_id) { if (headset_capture_id) stop_headset_capture(); return; }
    headset_capture_id=value.request_id; headset_capture_deadline=std::chrono::steady_clock::now()+std::chrono::seconds(2);
#ifdef __ANDROID__
    auto & env = jni::jni_thread::env(); auto activity = application::native_app()->activity->clazz;
    auto cls = env.GetObjectClass(activity); auto method = env.GetMethodID(cls,"renewHeadsetCapture","(JZ)V");
    if (method) env.CallVoidMethod(activity,method,jlong(value.request_id),jboolean(value.frames));
    if (env.ExceptionCheck()) env.ExceptionClear();
    env.DeleteLocalRef(cls);
#else
    network_session->send_control(from_headset::headset_capture{.request_id=value.request_id,.age_ms=0,.status="Headset view requires an Android client",.jpeg={}});
#endif
}
void scenes::stream::poll_headset_capture() {
    auto now=std::chrono::steady_clock::now();
    if (!headset_capture_id) return;
    if (now>headset_capture_deadline) { stop_headset_capture(); return; }
    if (now<headset_capture_poll) return;
    headset_capture_poll=now+std::chrono::milliseconds(100);
#ifdef __ANDROID__
    auto & env = jni::jni_thread::env(); auto activity = application::native_app()->activity->clazz;
    auto cls = env.GetObjectClass(activity);
    auto status_method=env.GetMethodID(cls,"getHeadsetCaptureStatus","()Ljava/lang/String;");
    auto image_method=env.GetMethodID(cls,"pollHeadsetCapture","()[B");
    std::string status;
    if (status_method) {
        auto text=static_cast<jstring>(env.CallObjectMethod(activity,status_method));
        if (text) { const char * chars=env.GetStringUTFChars(text,nullptr); if(chars) { status=chars; env.ReleaseStringUTFChars(text,chars); } env.DeleteLocalRef(text); }
    }
    from_headset::headset_capture packet{.request_id=headset_capture_id,.age_ms=0,.status=status.substr(0,160),.jpeg={}};
    if (image_method) {
        auto data=static_cast<jbyteArray>(env.CallObjectMethod(activity,image_method));
        if(data) {
            auto size=env.GetArrayLength(data);
            if(size>8 && size<=256*1024+8) {
                uint64_t stamp=0; env.GetByteArrayRegion(data,0,8,reinterpret_cast<jbyte *>(&stamp));
                uint64_t clock=std::chrono::duration_cast<std::chrono::nanoseconds>(now.time_since_epoch()).count();
                // The JPEG worker timestamps before compression; drop stale frames.
                if(clock>=stamp && clock-stamp<=250000000) {
                    packet.age_ms=uint32_t((clock-stamp)/1000000); packet.jpeg.resize(size-8);
                    env.GetByteArrayRegion(data,8,size-8,reinterpret_cast<jbyte *>(packet.jpeg.data()));
                }
            }
            env.DeleteLocalRef(data);
        }
    }
    bool failed=env.ExceptionCheck(); if(failed) env.ExceptionClear(); env.DeleteLocalRef(cls);
    if(!failed && (!packet.jpeg.empty() || status!=headset_capture_status)) {
        network_session->send_control(std::move(packet)); headset_capture_status=status;
    }
#endif
}

void scenes::stream::operator()(to_headset::server_message && message)
{
	switch (message.kind)
	{
		case to_headset::server_message::kind::toast:
		case to_headset::server_message::kind::toast_urgent: {
			auto toast = gui_toast.lock();
			toast->emplace(message.msg, message.kind == to_headset::server_message::kind::toast_urgent);

			gui_status_last_change = instance.now();
			break;
		}

		case to_headset::server_message::kind::error: {
			auto queue = stream_error_queue.lock();
			queue->emplace(std::move(message.msg));

			break;
		}
	}
}

void scenes::stream::operator()(to_headset::video_stream_data_shard && shard)
{
	std::shared_lock lock(decoder_mutex);
	uint8_t idx = shard.stream_item_idx;
	if (idx >= decoders.size() || !decoders[idx].decoder || !video_stream_description || shard.frame_idx < video_stream_description->first_frame)
	{
		// We don't know (yet?) about this stream, ignore packet
		return;
	}
	decoders[idx].decoder->push_shard(std::move(shard));
}

void scenes::stream::operator()(to_headset::feature_control && control)
{
	switch (control.f)
	{
		case wivrn::to_headset::feature_control::hid_input:
			hid_forwarding = control.state;
			if (not control.state and (application::get_config().forward_keyboard or application::get_config().forward_mouse or application::get_config().forward_gamepad))
			{
				auto toast = gui_toast.lock();
				toast->emplace(_("The server does not allow forwarded input devices"), true);
				gui_status_last_change = instance.now();
			}
			return;
		case wivrn::to_headset::feature_control::microphone:
			if (audio_handle)
				audio_handle->set_mic_state(control.state);
			return;
	}
}

void scenes::stream::operator()(to_headset::audio_stream_description && desc)
{
#if defined(__ANDROID__) && WIVRN_AUDIO_AB_STOCK
	spdlog::info("Audio backend: demand-driven unprocessed capture with optional software noise suppression");
#else
	spdlog::info("Audio backend: experimental session keep-alive");
#endif
	audio_handle.emplace(desc, *network_session, instance);
}

void scenes::stream::operator()(to_headset::video_stream_description && desc)
{
	setup(desc);

	if (not tracking_thread)
	{
		tracking_thread = utils::named_thread("tracking_thread", &stream::tracking, this);
	}
}

void scenes::stream::operator()(to_headset::refresh_rate_change && rate)
{
	spdlog::info("refresh rate change request: {}", rate.hz);
	initial_refresh_rate_applied = true;
	session.set_refresh_rate(rate.hz);
	std::unique_lock lock(decoder_mutex);
	if (video_stream_description)
		video_stream_description->refresh_rate = rate.hz;
}

void scenes::stream::operator()(to_headset::stream_tab_change && tab)
{
	next_gui_status = tab.tab;
}

void scenes::stream::operator()(to_headset::timesync_query && query)
{
	network_session->send_stream(from_headset::timesync_response{
	        .query = query.query,
	        .response = instance.now(),
	});
}

void scenes::stream::operator()(audio_data && data)
{
	if (audio_handle)
		(*audio_handle)(std::move(data));
}

void scenes::stream::send_feedback(const wivrn::from_headset::feedback & feedback)
{
	try
	{
		network_session->send_control(wivrn::from_headset::feedback{feedback});
	}
	catch (std::exception & e)
	{
		spdlog::warn("Exception while sending feedback packet: {}", e.what());
	}
}

void scenes::stream::operator()(to_headset::application_list && l)
{
	apps(std::move(l));
}

void scenes::stream::operator()(to_headset::application_icon && icon)
{
	apps(std::move(icon));
}

void scenes::stream::operator()(to_headset::running_applications && apps)
{
	*running_applications.lock() = std::move(apps);
}

void scenes::stream::start_application(std::string appid)
{
	network_session->send_control(wivrn::from_headset::start_app{
	        .app_id = std::move(appid),
	});
}
