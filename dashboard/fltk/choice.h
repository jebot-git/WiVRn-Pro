// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <FL/Fl_Choice.H>
#include <string>
#include <vector>

namespace wivrn::dashboard
{
// Choice labels are data, not Fl_Menu_::add's menu-description language.
// Own the strings: Fl_Menu_::copy copies the array but borrows its labels.
class LiteralChoice : public Fl_Choice
{
    std::vector<std::string> labels;
public:
    using Fl_Choice::Fl_Choice;
    ~LiteralChoice() override { clear(); }
    void setLabels(const std::vector<std::string> & text)
    {
        clear();
        labels.clear();
        labels.reserve(text.size());
        for (const auto & original: text)
        {
            std::string label;
            for (char c: original)
            {
                label += c;
                if (c == '&') label += '&'; // Literal ampersands, not accelerators.
            }
            labels.push_back(std::move(label));
        }
        std::vector<Fl_Menu_Item> items(labels.size() + 1);
        for (size_t i = 0; i < labels.size(); ++i)
            items[i].label(labels[i].c_str());
        copy(items.data());
    }
};
} // namespace wivrn::dashboard
