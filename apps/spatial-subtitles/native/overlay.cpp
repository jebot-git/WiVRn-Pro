// SPDX-License-Identifier: GPL-3.0-or-later
#define XR_USE_PLATFORM_XLIB
#define XR_USE_GRAPHICS_API_OPENGL
#include <SDL2/SDL.h>
#include <SDL2/SDL_syswm.h>
#include <X11/Xlib.h>
#include <algorithm>
#include <array>
#include <cairo/cairo.h>
#include <chrono>
#include <cmath>
#include <csignal>
#include <deque>
#include <epoxy/gl.h>
#include <epoxy/glx.h>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <nlohmann/json.hpp>
#include <pango/pangocairo.h>
#include <unistd.h>
#include <vector>
#include <openxr/openxr.h>
#include <openxr/openxr_platform.h>
using Json = nlohmann::json;
static volatile sig_atomic_t stopping = 0;
static double now()
{
	return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
static void check(XrResult r, const char * what)
{
	if (XR_FAILED(r))
		throw std::runtime_error(std::string(what) + ": " + std::to_string(r));
}
#define XR(call) check(call, #call)
static Json read(const std::string & path)
{
	std::ifstream f(path);
	return Json::parse(f);
}
static XrVector3f rotate(XrQuaternionf q, XrVector3f v)
{
	XrVector3f t{2 * (q.y * v.z - q.z * v.y), 2 * (q.z * v.x - q.x * v.z), 2 * (q.x * v.y - q.y * v.x)};
	return {v.x + q.w * t.x + q.y * t.z - q.z * t.y, v.y + q.w * t.y + q.z * t.x - q.x * t.z, v.z + q.w * t.z + q.x * t.y - q.y * t.x};
}
static XrPosef offset(XrPosef p, float x, float y, float z)
{
	auto v = rotate(p.orientation, {x, y, z});
	p.position.x += v.x;
	p.position.y += v.y;
	p.position.z += v.z;
	return p;
}
struct Canvas
{
	XrSwapchain swap = XR_NULL_HANDLE;
	std::vector<XrSwapchainImageOpenGLKHR> images;
	std::string previous;
	XrCompositionLayerQuad quad{XR_TYPE_COMPOSITION_LAYER_QUAD};
	static constexpr int width = 1024, height = 256;
	void init(XrSession session, int64_t format)
	{
		XrSwapchainCreateInfo info{XR_TYPE_SWAPCHAIN_CREATE_INFO};
		info.usageFlags = XR_SWAPCHAIN_USAGE_COLOR_ATTACHMENT_BIT | XR_SWAPCHAIN_USAGE_SAMPLED_BIT;
		info.format = format;
		info.sampleCount = 1;
		info.width = width;
		info.height = height;
		info.faceCount = 1;
		info.arraySize = 1;
		info.mipCount = 1;
		XR(xrCreateSwapchain(session, &info, &swap));
		uint32_t count = 0;
		XR(xrEnumerateSwapchainImages(swap, 0, &count, nullptr));
		images.resize(count, {XR_TYPE_SWAPCHAIN_IMAGE_OPENGL_KHR});
		XR(xrEnumerateSwapchainImages(swap, count, &count, reinterpret_cast<XrSwapchainImageBaseHeader *>(images.data())));
		quad.layerFlags = XR_COMPOSITION_LAYER_BLEND_TEXTURE_SOURCE_ALPHA_BIT;
		quad.eyeVisibility = XR_EYE_VISIBILITY_BOTH;
		quad.subImage.swapchain = swap;
		quad.subImage.imageRect = {{0, 0}, {width, height}};
	}
	void draw(const Json & line, const std::string & font)
	{
		auto key = line.dump() + font;
		if (key == previous)
			return;
		previous = key;
		auto * surface = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, width, height);
		auto * cr = cairo_create(surface);
		cairo_set_operator(cr, CAIRO_OPERATOR_SOURCE);
		cairo_set_source_rgba(cr, 0, 0, 0, 0);
		cairo_paint(cr);
		cairo_set_operator(cr, CAIRO_OPERATOR_OVER);
		std::array<double, 3> fg{1, 1, 1}, bg{.035, .045, .065};
		if (line.contains("foreground") && line["foreground"].size() == 3)
			for (int i = 0; i < 3; i++)
				fg[i] = std::clamp(line["foreground"][i].get<double>(), 0., 1.);
		else
		{
			const std::array<std::array<double, 3>, 5> colours{{{1, 1, 1}, {.3, .9, 1}, {1, .7, .3}, {.7, 1, .4}, {1, .5, .85}}};
			fg = colours[std::clamp(line.value("speaker", 0), 0, 4)];
		}
		if (line.contains("background") && line["background"].size() == 3)
			for (int i = 0; i < 3; i++)
				bg[i] = std::clamp(line["background"][i].get<double>(), 0., 1.);
		cairo_set_source_rgba(cr, bg[0], bg[1], bg[2], line.contains("box") ? 1 : .82);
		cairo_rectangle(cr, 0, 0, width, height);
		cairo_fill(cr);
		auto * layout = pango_cairo_create_layout(cr);
		auto * desc = pango_font_description_from_string((font + " 42").c_str());
		pango_layout_set_font_description(layout, desc);
		pango_layout_set_width(layout, (width - 48) * PANGO_SCALE);
		pango_layout_set_height(layout, (height - 20) * PANGO_SCALE);
		pango_layout_set_wrap(layout, PANGO_WRAP_WORD_CHAR);
		pango_layout_set_ellipsize(layout, PANGO_ELLIPSIZE_END);
		pango_layout_set_alignment(layout, PANGO_ALIGN_CENTER);
		auto text = line.value("text", std::string());
		if (text.size() > 4096)
			text.resize(4096);
		pango_layout_set_text(layout, text.c_str(), -1);
		int w, h;
		pango_layout_get_pixel_size(layout, &w, &h);
		cairo_move_to(cr, 24, std::max(10, (height - h) / 2));
		cairo_set_source_rgb(cr, fg[0], fg[1], fg[2]);
		pango_cairo_show_layout(cr, layout);
		cairo_surface_flush(surface);
		// Cairo stores the top row first; OpenGL swapchains start at the bottom.
		// Reverse rows only, preserving the left-to-right order of glyphs.
		std::vector<unsigned char> pixels(width * height * 4);
		const auto * source = cairo_image_surface_get_data(surface);
		const auto stride = cairo_image_surface_get_stride(surface);
		for (int y = 0; y < height; ++y)
			std::copy_n(source + (height - 1 - y) * stride, width * 4, pixels.data() + y * width * 4);
		uint32_t index;
		XrSwapchainImageAcquireInfo acquire{XR_TYPE_SWAPCHAIN_IMAGE_ACQUIRE_INFO};
		XR(xrAcquireSwapchainImage(swap, &acquire, &index));
		XrSwapchainImageWaitInfo wait{XR_TYPE_SWAPCHAIN_IMAGE_WAIT_INFO};
		wait.timeout = XR_INFINITE_DURATION;
		XR(xrWaitSwapchainImage(swap, &wait));
		glBindTexture(GL_TEXTURE_2D, images[index].image);
		glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, width, height, GL_BGRA, GL_UNSIGNED_BYTE, pixels.data());
		glFinish();
		if (glGetError() != GL_NO_ERROR)
			throw std::runtime_error("Subtitle texture upload failed");
		XrSwapchainImageReleaseInfo release{XR_TYPE_SWAPCHAIN_IMAGE_RELEASE_INFO};
		XR(xrReleaseSwapchainImage(swap, &release));
		g_object_unref(layout);
		pango_font_description_free(desc);
		cairo_destroy(cr);
		cairo_surface_destroy(surface);
	}
};
int main()
{
	try
	{
		signal(SIGINT, [](int) { stopping = 1; });
		signal(SIGTERM, [](int) { stopping = 1; });
		std::string directory = std::string(getenv("XDG_RUNTIME_DIR") ? getenv("XDG_RUNTIME_DIR") : "/tmp") + "/spatial-subtitles";
		std::filesystem::create_directories(directory);
		std::filesystem::permissions(directory, std::filesystem::perms::owner_all);
		uint32_t count;
		XR(xrEnumerateInstanceExtensionProperties(nullptr, 0, &count, nullptr));
		std::vector<XrExtensionProperties> extensions(count, {XR_TYPE_EXTENSION_PROPERTIES});
		XR(xrEnumerateInstanceExtensionProperties(nullptr, count, &count, extensions.data()));
		bool overlay = false;
		for (auto & e: extensions)
			if (std::string(e.extensionName) == XR_EXTX_OVERLAY_EXTENSION_NAME)
				overlay = true;
		if (!overlay)
			throw std::runtime_error("Runtime does not support XR_EXTX_overlay");
		const char * names[] = {XR_KHR_OPENGL_ENABLE_EXTENSION_NAME, XR_EXTX_OVERLAY_EXTENSION_NAME};
		XrInstanceCreateInfo ici{XR_TYPE_INSTANCE_CREATE_INFO};
		strcpy(ici.applicationInfo.applicationName, "Spatial Subtitles");
		ici.applicationInfo.applicationVersion = 1;
		ici.applicationInfo.apiVersion = XR_CURRENT_API_VERSION;
		ici.enabledExtensionCount = 2;
		ici.enabledExtensionNames = names;
		XrInstance instance;
		XR(xrCreateInstance(&ici, &instance));
		XrSystemGetInfo systemInfo{XR_TYPE_SYSTEM_GET_INFO};
		systemInfo.formFactor = XR_FORM_FACTOR_HEAD_MOUNTED_DISPLAY;
		XrSystemId system;
		XR(xrGetSystem(instance, &systemInfo, &system));
		PFN_xrGetOpenGLGraphicsRequirementsKHR requirements;
		XR(xrGetInstanceProcAddr(instance, "xrGetOpenGLGraphicsRequirementsKHR", reinterpret_cast<PFN_xrVoidFunction *>(&requirements)));
		XrGraphicsRequirementsOpenGLKHR req{XR_TYPE_GRAPHICS_REQUIREMENTS_OPENGL_KHR};
		XR(requirements(instance, system, &req));
		SDL_SetHint(SDL_HINT_VIDEODRIVER, "x11");
		if (SDL_Init(SDL_INIT_VIDEO) != 0)
			throw std::runtime_error(SDL_GetError());
		SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION, 4);
		SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION, 5);
		SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK, SDL_GL_CONTEXT_PROFILE_CORE);
		auto * window = SDL_CreateWindow("Spatial Subtitles", 0, 0, 32, 32, SDL_WINDOW_OPENGL | SDL_WINDOW_HIDDEN);
		if (!window)
			throw std::runtime_error(SDL_GetError());
		auto context = SDL_GL_CreateContext(window);
		if (!context)
			throw std::runtime_error(SDL_GetError());
		SDL_SysWMinfo wm{};
		SDL_VERSION(&wm.version);
		if (!SDL_GetWindowWMInfo(window, &wm))
			throw std::runtime_error(SDL_GetError());
		auto display = wm.info.x11.display;
		int configId;
		glXQueryContext(display, glXGetCurrentContext(), GLX_FBCONFIG_ID, &configId);
		int attrs[] = {GLX_FBCONFIG_ID, configId, None};
		int configsCount;
		auto * configs = glXChooseFBConfig(display, DefaultScreen(display), attrs, &configsCount);
		if (!configsCount)
			throw std::runtime_error("No GLX framebuffer configuration");
		auto * visual = glXGetVisualFromFBConfig(display, configs[0]);
		XrGraphicsBindingOpenGLXlibKHR binding{XR_TYPE_GRAPHICS_BINDING_OPENGL_XLIB_KHR};
		binding.xDisplay = display;
		binding.visualid = visual->visualid;
		binding.glxFBConfig = configs[0];
		binding.glxDrawable = glXGetCurrentDrawable();
		binding.glxContext = glXGetCurrentContext();
		XFree(visual);
		XFree(configs);
		XrSessionCreateInfoOverlayEXTX overlayInfo{XR_TYPE_SESSION_CREATE_INFO_OVERLAY_EXTX};
		overlayInfo.next = &binding;
		overlayInfo.sessionLayersPlacement = 10;
		XrSessionCreateInfo sci{XR_TYPE_SESSION_CREATE_INFO};
		sci.next = &overlayInfo;
		sci.systemId = system;
		XrSession session;
		XR(xrCreateSession(instance, &sci, &session));
		XrReferenceSpaceCreateInfo spaceInfo{XR_TYPE_REFERENCE_SPACE_CREATE_INFO};
		spaceInfo.poseInReferenceSpace.orientation.w = 1;
		spaceInfo.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_LOCAL;
		XrSpace local;
		XR(xrCreateReferenceSpace(session, &spaceInfo, &local));
		spaceInfo.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_VIEW;
		XrSpace view;
		XR(xrCreateReferenceSpace(session, &spaceInfo, &view));
		XR(xrEnumerateSwapchainFormats(session, 0, &count, nullptr));
		std::vector<int64_t> formats(count);
		XR(xrEnumerateSwapchainFormats(session, count, &count, formats.data()));
		int64_t format = GL_SRGB8_ALPHA8;
		if (std::find(formats.begin(), formats.end(), format) == formats.end())
			format = GL_RGBA8;
		if (std::find(formats.begin(), formats.end(), format) == formats.end())
			throw std::runtime_error("No RGBA swapchain format");
		std::array<Canvas, 12> canvases;
		for (auto & c: canvases)
			c.init(session, format);
		bool running = false;
		Json state;
		std::deque<Json> poseHistory;
		unsigned referenceGeneration = 0;
		size_t lastLayers = 0;
		double nextRead = 0, nextPose = 0;
		std::cerr << "Spatial Subtitles overlay session created\n";
		while (!stopping)
		{
			XrEventDataBuffer event{XR_TYPE_EVENT_DATA_BUFFER};
			while (xrPollEvent(instance, &event) == XR_SUCCESS)
			{
				if (event.type == XR_TYPE_EVENT_DATA_SESSION_STATE_CHANGED)
				{
					auto & e = *reinterpret_cast<XrEventDataSessionStateChanged *>(&event);
					if (e.state == XR_SESSION_STATE_READY)
					{
						XrSessionBeginInfo begin{XR_TYPE_SESSION_BEGIN_INFO};
						begin.primaryViewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;
						XR(xrBeginSession(session, &begin));
						running = true;
					}
					else if (e.state == XR_SESSION_STATE_STOPPING)
					{
						XR(xrEndSession(session));
						running = false;
					}
					else if (e.state == XR_SESSION_STATE_EXITING || e.state == XR_SESSION_STATE_LOSS_PENDING)
						stopping = 1;
				}
				if (event.type == XR_TYPE_EVENT_DATA_REFERENCE_SPACE_CHANGE_PENDING)
				{
					state = Json();
					poseHistory.clear();
					++referenceGeneration;
				}
				event = {XR_TYPE_EVENT_DATA_BUFFER};
			}
			if (!running)
			{
				SDL_Delay(25);
				continue;
			}
			XrFrameWaitInfo wi{XR_TYPE_FRAME_WAIT_INFO};
			XrFrameState fs{XR_TYPE_FRAME_STATE};
			XR(xrWaitFrame(session, &wi, &fs));
			XrFrameBeginInfo bi{XR_TYPE_FRAME_BEGIN_INFO};
			XR(xrBeginFrame(session, &bi));
			XrSpaceLocation location{XR_TYPE_SPACE_LOCATION};
			XR(xrLocateSpace(view, local, fs.predictedDisplayTime, &location));
			double t = now();
			bool tracked = (location.locationFlags & (XR_SPACE_LOCATION_ORIENTATION_VALID_BIT | XR_SPACE_LOCATION_POSITION_VALID_BIT)) == (XR_SPACE_LOCATION_ORIENTATION_VALID_BIT | XR_SPACE_LOCATION_POSITION_VALID_BIT);
			if (t > nextPose && tracked)
			{
				const auto & p = location.pose;
				Json j{{"time", t}, {"position", {p.position.x, p.position.y, p.position.z}}, {"orientation", {p.orientation.x, p.orientation.y, p.orientation.z, p.orientation.w}}};
				j["generation"] = referenceGeneration;
				poseHistory.push_back(j);
				while (poseHistory.size() > 240)
					poseHistory.pop_front();
				j["history"] = poseHistory;
				j["rendered_layers"] = lastLayers;
				std::ofstream out(directory + "/pose.tmp");
				out << j;
				out.close();
				std::filesystem::rename(directory + "/pose.tmp", directory + "/pose.json");
				nextPose = t + .05;
			}
			if (t > nextRead)
			{
				try
				{
					state = read(directory + "/overlay.json");
				}
				catch (...)
				{
					state = Json();
				}
				nextRead = t + .05;
			}
			std::vector<const XrCompositionLayerBaseHeader *> layers;
			if (fs.shouldRender && tracked && state.is_object() && t - state.value("updated", 0.) < 2)
			{
				auto add = [&](const Json & line, bool scene, size_t row) {
					if (layers.size() >= canvases.size() || !line.is_object() || line.value("expires", 0.) < t)
						return;
					auto & c = canvases[layers.size()];
					float scale = std::clamp(state.value("scale", 1.f), .6f, 1.5f);
					c.quad.space = local;
					c.quad.pose = location.pose;
					c.quad.size = {1.15f * scale, .2875f * scale};
					if (scene)
					{
						if (!line.contains("anchor") || !line["anchor"].is_object())
							return;
						auto a = line["anchor"];
						if (t - a.value("time", 0.) > 10 || a.value("generation", 0u) != referenceGeneration)
							return;
						auto pos = a.at("position").get<std::array<float, 3>>();
						auto q = a.at("orientation").get<std::array<float, 4>>();
						XrPosef anchor{{q[0], q[1], q[2], q[3]}, {pos[0], pos[1], pos[2]}};
						auto current = location.pose.orientation;
						float dot = std::abs(current.x * q[0] + current.y * q[1] + current.z * q[2] + current.w * q[3]);
						float distance = std::hypot(location.pose.position.x - pos[0], location.pose.position.z - pos[2]);
						if (dot < .995f || distance > .15f)
							return;
						auto box = line.at("box").get<std::array<float, 4>>();
						auto fov = line.at("fov").get<std::array<float, 4>>();
						float d = std::clamp(line.value("distance", 2.f), .5f, 10.f);
						float l = std::tan(fov[0]), r = std::tan(fov[1]), u = std::tan(fov[2]), b = std::tan(fov[3]);
						float x = l + (r - l) * (box[0] + box[2] / 2), y = u + (b - u) * (box[1] + box[3] / 2);
						c.quad.pose = offset(anchor, x * d, y * d, -d);
						c.quad.size = {std::max(.05f, box[2] * (r - l) * d), std::max(.025f, box[3] * (u - b) * d)};
					}
					else
					{
						float bearing = std::clamp(line.value("bearing", 0.f), -1.047f, 1.047f);
						auto anchor = location.pose;
						if (line.contains("anchor") && line["anchor"].is_object())
						{
							const auto & a = line["anchor"];
							if (a.value("generation", 0u) != referenceGeneration)
								return;
							auto pos = a.at("position").get<std::array<float, 3>>();
							auto q = a.at("orientation").get<std::array<float, 4>>();
							anchor = {{q[0], q[1], q[2], q[3]}, {pos[0], pos[1], pos[2]}};
						}
						c.quad.pose = offset(anchor, std::sin(bearing) * 2, -.4f - float(row) * .32f, -std::cos(bearing) * 2);
					}
					c.draw(line, state.value("font", std::string("Sans")));
					layers.push_back(reinterpret_cast<const XrCompositionLayerBaseHeader *>(&c.quad));
				};
				try
				{
					size_t row = 0;
					for (auto & line: state.value("speech", Json::array()))
						add(line, false, row++);
					for (auto & line: state.value("ocr", Json::array()))
						add(line, true, 0);
				}
				catch (const std::exception & e)
				{
					std::cerr << "Invalid overlay data: " << e.what() << "\n";
					layers.clear();
				}
			}
			XrFrameEndInfo end{XR_TYPE_FRAME_END_INFO};
			end.displayTime = fs.predictedDisplayTime;
			end.environmentBlendMode = XR_ENVIRONMENT_BLEND_MODE_OPAQUE;
			end.layerCount = layers.size();
			end.layers = layers.data();
			XR(xrEndFrame(session, &end));
			lastLayers = layers.size();
		}
		for (auto & c: canvases)
			xrDestroySwapchain(c.swap);
		xrDestroySpace(view);
		xrDestroySpace(local);
		xrDestroySession(session);
		xrDestroyInstance(instance);
		SDL_GL_DeleteContext(context);
		SDL_DestroyWindow(window);
		SDL_Quit();
		return 0;
	}
	catch (const std::exception & e)
	{
		std::cerr << "Spatial Subtitles: " << e.what() << "\n";
		return 1;
	}
}
