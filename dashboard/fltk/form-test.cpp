// SPDX-License-Identifier: GPL-3.0-or-later
#include "form.h"
#include <FL/Fl_Group.H>
#include <iostream>
#include <stdexcept>
using namespace wivrn::dashboard;

void require(bool ok, const char * message)
{
    if (!ok) throw std::runtime_error(message);
}

int main()
try
{
    Fl_Group widgets(0, 0, 600, 600);
    Form form(0, 0, 450);
    form.choice("Face output", "/face/output", {{"Auto / OSCQuery", "auto"},
        {"VRChat native", "native"}, {"UniFT", "unift"}, {"VRChat JSON", "json"}, {"Both", "both"}});
    form.choice("Face source", "/face/source", {{"Babble / EyeTrackVR", "babble"}, {"OpenXR", "openxr"}});
    form.choice("Tracking bridge", "/tracking-mode", {{"SlimeVR", "slimevr"}, {"Motoc / Lighthouse", "motoc"}});
    form.choice("Headset profile", "/headset-profile", {{"Auto", "auto"}, {"Quest Pro", "quest-pro"}, {"Steam Frame", "steam-frame"}});
    form.choice("Bit depth", "/bit-depth", {{"Default", nullptr}, {"8", 8}, {"10", 10}});
    form.choice("HaritoraX model", "/haritorax/model", {{"HaritoraX 2", "x2"}, {"HaritoraX Wireless", "wireless"}});
    form.choice("Motoc calibration", "/mode", {{"One-time", "once"}, {"Rigid headset tracker", "rigid"}, {"Room anchors (experimental)", "anchors"}});
    widgets.end();
    const char * output_labels[] = {"Auto / OSCQuery", "VRChat native", "UniFT", "VRChat JSON", "Both"};
    for (const auto & field: form.fields)
    {
        auto choice = static_cast<Fl_Choice *>(field.widget);
        require(choice->size() == int(field.choices.size()) + 1, "Choice labels created extra submenu entries");
        for (size_t i = 0; i < field.choices.size(); ++i)
        {
            const auto & item = choice->menu()[i];
            require(!item.submenu() && item.active(), "Choice is not directly selectable");
            if (field.path == "/face/output")
                require(std::string(item.label()) == output_labels[i], "Face-output label changed");
            Json profile = {{"face", {{"enabled", true}, {"output", "both"}, {"source", "openxr"}}},
                {"unrelated", "preserve"}};
            profile[Json::json_pointer(field.path)] = field.choices.back();
            form.load(profile);
            choice->value(int(i));
            auto saved = form.apply(profile);
            if (field.choices[i].is_null())
                require(!saved.contains(Json::json_pointer(field.path)), "Default choice did not remove the override");
            else
                require(saved.at(Json::json_pointer(field.path)) == field.choices[i], "Selection saved a different value");
            require(saved["unrelated"] == "preserve", "Saving changed unrelated settings");
            form.load(saved);
            require(choice->value() == int(i), "Saved selection did not reload");
            require(form.clean(), "Reloaded selection appears unsaved");
        }
    }
    // Dynamic names must survive temporary input strings, repeated labels and all
    // menu metacharacters without changing count, indices, flags or ownership.
    LiteralChoice names(0, 0, 450, 34);
    for (int pass = 0; pass < 2; ++pass)
    {
        names.setLabels({"Quest / Pro", "Custom | preset", "_Headset", "Duplicate", "Duplicate", "Tom & Bob", "Back\\slash"});
        require(names.size() == 8, "Dynamic labels changed the menu size");
        const char * expected[] = {"Quest / Pro", "Custom | preset", "_Headset", "Duplicate", "Duplicate", "Tom && Bob", "Back\\slash"};
        for (int i = 0; i < 7; ++i)
        {
            require(std::string(names.text(i)) == expected[i], "Dynamic label text changed or expired");
            require(names.menu()[i].flags == 0, "Dynamic text became menu formatting");
            names.value(i);
            require(names.value() == i, "Dynamic index changed");
        }
    }
    names.setLabels({});
    require(names.size() == 1, "Empty choice list did not clear");
    names.setLabels({"Full standing reset", "Yaw alignment", "Mounting reset", "Save standing pose",
        "Save seated pose", "Save floor / lying pose", "Save high kneeling pose", "Save sitting-on-heels pose",
        "Save belly-down pose", "Save bent-knees belly pose", "Walking in place setup"});
    require(names.size() == 12, "Calibration label shifted command indices");
    for (int i = 0; i < 11; ++i) require(names.menu()[i].flags == 0, "Calibration action is not selectable");
    Form face(0, 0, 450);
    face.string("Mapping", "/face/translation_sheet");
    Json old = {{"face", {{"translation_sheet", "old.json"}, {"output", "auto"}}}};
    face.load(old);
    static_cast<Fl_Input *>(face.fields.front().widget)->value("");
    require(!face.apply(old)["face"].contains("translation_sheet"), "Clearing a nested optional mapping failed");
    face.load({{"face", {{"translation_sheet", nullptr}}}});
    require(std::string(static_cast<Fl_Input *>(face.fields.front().widget)->value()).empty(), "Null mapping did not clear its input");
    Json active = {{"output", "native"}, {"source", "openxr"}, {"destination", "127.0.0.1:9000"}};
    auto unchanged = faceDraft(active, old, old, face);
    require(unchanged["output"] == "native", "Unchanged startup data undid active face output");
    auto draft = old; draft["face"]["destination"] = "127.0.0.1:9999";
    auto merged = faceDraft(active, draft, old, face);
    require(merged["destination"] == "127.0.0.1:9999" && merged["source"] == "openxr", "Explicit advanced face edits did not merge");
    std::cout << "PASS: all seven form selectors save/reload; dynamic names and all eleven calibration choices preserve indices\n";
    return 0;
}
catch (const std::exception & e)
{
    std::cerr << e.what() << '\n';
    return 1;
}
