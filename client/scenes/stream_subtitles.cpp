// SPDX-License-Identifier: GPL-3.0-or-later
#include "stream.h"
#include "application.h"
#include <imgui.h>

void scenes::stream::operator()(wivrn::subtitles::packet && packet)
{
    if (wivrn::subtitles::valid(packet))
    {
        auto & pending = packet.visual ? pending_ocr : pending_subtitles;
        *pending.lock() = std::pair(std::move(packet), instance.now());
    }
}

void scenes::stream::update_subtitles(XrTime now)
{
    if (subtitle_reference_barrier)
    {
        subtitle_overlay.clear();
        ocr_overlay.clear();
        pending_ocr.lock()->reset();
        pending_subtitles.lock()->reset();
        if (now < subtitle_reference_barrier) return;
        subtitle_reference_barrier = 0;
    }
    auto pose = application::locate_controller(application::space(xr::spaces::view), application::space(xr::spaces::world), now);
    if (pose) { subtitle_overlay.pose_at(now, pose->first, pose->second); ocr_overlay.pose_at(pose->first,pose->second); }
    else { subtitle_overlay.clear(); ocr_overlay.clear(); }
    auto visual = pending_ocr.lock();
    if (*visual)
    {
        ocr_overlay.receive((*visual)->first,(*visual)->second,now);
        visual->reset();
    }
    auto pending = pending_subtitles.lock();
    if (*pending)
    {
        subtitle_overlay.receive((*pending)->first, (*pending)->second, now);
        pending->reset();
    }
}

void scenes::stream::draw_subtitles(XrTime now)
{
    // High contrast anonymous voice colours, reinforced by a textual label.
    static constexpr std::array<ImVec4, 6> colours{{
        {0.45f, 0.88f, 1, 1}, {1, 0.8f, 0.35f, 1}, {0.65f, 1, 0.6f, 1},
        {1, 0.65f, 0.85f, 1}, {0.8f, 0.75f, 1, 1}, {1, 0.65f, 0.4f, 1}}};
    if (!subtitle_overlay.enabled) return;
    for (size_t i = 0; i < subtitle_overlay.captions.size(); ++i)
    {
        const auto & caption = subtitle_overlay.captions[i];
        if (caption.text.empty() || now >= caption.expires) continue;
        auto & layer = imgui_ctx->layers()[3+i];
        layer.position = caption.position;
        layer.orientation = caption.orientation;
        layer.size = glm::vec2(1.5f, .25f) * caption.scale;
        ImGui::SetNextWindowPos(ImVec2(layer.vp_origin.x, layer.vp_origin.y));
        ImGui::SetNextWindowSize(ImVec2(layer.vp_size.x, layer.vp_size.y));
        ImGui::SetNextWindowBgAlpha(.92f);
        ImGui::PushStyleColor(ImGuiCol_Text, caption.speaker ? colours[caption.colour] : ImVec4(1,1,1,1));
        ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(28, 18));
        auto name = "##spoken-subtitle-" + std::to_string(i);
        ImGui::Begin(name.c_str(), nullptr, ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoInputs |
            ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_NoFocusOnAppearing | ImGuiWindowFlags_NoBringToFrontOnFocus);
        auto font_scale = ImGui::GetStyle().FontScaleMain;
        auto font = caption.use_jupiteroid ? imgui_ctx->subtitle_font() : nullptr;
        ImGui::GetStyle().FontScaleMain = 1;
        ImGui::PushFont(font, 28);
        if (caption.speaker) ImGui::Text("Voice %u%s", caption.speaker, caption.spatial ? " · stereo direction estimate" : "");
        else ImGui::TextUnformatted(caption.spatial ? "Speech · stereo direction estimate" : "Speech");
        ImGui::PopFont();
        ImGui::PushFont(font, 44);
        const float text_height = ImGui::CalcTextSize(caption.text.c_str(), nullptr, false, float(layer.vp_size.x)-56).y;
        ImGui::PopFont();
        ImGui::PushFont(font, std::clamp(44.f * 210.f / std::max(210.f, text_height), 22.f, 44.f));
        ImGui::PushTextWrapPos(ImGui::GetWindowContentRegionMax().x);
        ImGui::TextUnformatted(caption.text.c_str());
        ImGui::PopTextWrapPos();
        ImGui::PopFont();
        ImGui::GetStyle().FontScaleMain = font_scale;
        ImGui::End();
        ImGui::PopStyleVar();
        ImGui::PopStyleColor();
    }
}

void scenes::stream::draw_scene_translations(XrTime now)
{
    if (!ocr_overlay.visible(now)) return;
    for (size_t i=0; i<ocr_overlay.regions.size(); ++i)
    {
        const auto & region = ocr_overlay.regions[i];
        if (region.text.empty()) continue;
        auto & layer = imgui_ctx->layers()[7+i];
        layer.position = region.position; layer.orientation = region.orientation; layer.size = region.size;
        // Match the atlas rectangle's aspect ratio to the original text region.
        float aspect = region.size.x/region.size.y;
        layer.vp_size = {int(std::clamp(300.f*aspect,1.f,1800.f)),int(std::clamp(1800.f/aspect,1.f,300.f))};
        const auto & fg = region.foreground; const auto & bg = region.background;
        ImGui::SetNextWindowPos(ImVec2(layer.vp_origin.x,layer.vp_origin.y));
        ImGui::SetNextWindowSize(ImVec2(layer.vp_size.x,layer.vp_size.y));
        ImGui::SetNextWindowBgAlpha(1);
        ImGui::PushStyleColor(ImGuiCol_Text,ImVec4(fg[0],fg[1],fg[2],1));
        ImGui::PushStyleColor(ImGuiCol_WindowBg,ImVec4(bg[0],bg[1],bg[2],1));
        ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding,ImVec2(4,4));
        ImGui::PushStyleVar(ImGuiStyleVar_WindowRounding,0);
        ImGui::PushStyleVar(ImGuiStyleVar_WindowBorderSize,0);
        auto name = "##scene-translation-"+std::to_string(i);
        ImGui::Begin(name.c_str(),nullptr,ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoInputs |
            ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_NoFocusOnAppearing | ImGuiWindowFlags_NoBringToFrontOnFocus);
        auto old_scale = ImGui::GetStyle().FontScaleMain;
        ImGui::GetStyle().FontScaleMain = 1;
        auto font = ocr_overlay.use_jupiteroid ? imgui_ctx->subtitle_font() : nullptr;
        float size = std::min(72.f,std::max(10.f,float(layer.vp_size.y)-8));
        for (; size>10; size-=2)
        {
            ImGui::PushFont(font,size);
            auto measured = ImGui::CalcTextSize(region.text.c_str(),nullptr,false,std::max(1.f,float(layer.vp_size.x)-8));
            ImGui::PopFont();
            if (measured.y<=layer.vp_size.y-8 && measured.x<=layer.vp_size.x-8) break;
        }
        ImGui::PushFont(font,size);
        ImGui::PushTextWrapPos(ImGui::GetWindowContentRegionMax().x);
        ImGui::TextUnformatted(region.text.c_str());
        ImGui::PopTextWrapPos(); ImGui::PopFont();
        ImGui::GetStyle().FontScaleMain = old_scale;
        ImGui::End(); ImGui::PopStyleVar(3); ImGui::PopStyleColor(2);
    }
}
