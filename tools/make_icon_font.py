"""Builds island_app/assets/IslandIcons.otf: Material Icons Outlined glyphs (Apache-2.0, Google)
placed at the same codepoints as the Segoe Fluent Icons glyphs the UI uses. On Linux (and on
Windows without Segoe Fluent/MDL2) the app loads this font, so the UI code needs no changes.

    python tools/make_icon_font.py <MaterialIconsOutlined-Regular.otf> <....codepoints>

Source: https://github.com/google/material-design-icons/tree/master/font
"""
import os
import sys

from fontTools import subset
from fontTools.ttLib import TTFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "island_app", "assets", "IslandIcons.otf")

# Segoe Fluent codepoint -> Material icon names (first one found wins)
MAP = {
    0xE701: ["wifi"], 0xE702: ["bluetooth"], 0xE706: ["light_mode", "wb_sunny"], 0xE708: ["dark_mode", "nights_stay"],
    0xE70B: ["sticky_note_2", "note"], 0xE70F: ["edit"], 0xE710: ["add"], 0xE711: ["close"], 0xE713: ["settings"],
    0xE718: ["push_pin"], 0xE720: ["mic"], 0xE721: ["search"], 0xE722: ["photo_camera"], 0xE724: ["send"],
    0xE72A: ["arrow_forward"], 0xE72E: ["lock"], 0xE738: ["remove"], 0xE739: ["crop_square"], 0xE73E: ["check"],
    0xE74D: ["delete"], 0xE74E: ["save"], 0xE74F: ["volume_off"], 0xE752: ["keyboard_capslock"],
    0xE765: ["keyboard"], 0xE767: ["volume_up"], 0xE768: ["play_arrow"], 0xE769: ["pause"], 0xE77A: ["push_pin"],
    0xE77F: ["content_paste"], 0xE790: ["palette"], 0xE7A7: ["undo"], 0xE7A8: ["crop"], 0xE7BA: ["warning"],
    0xE7E6: ["border_color", "brush"], 0xE7E7: ["notifications_active"], 0xE7E8: ["power_settings_new"],
    0xE7ED: ["notifications_off"], 0xE7F4: ["desktop_windows", "computer"], 0xE80F: ["home"],
    0xE890: ["blur_on"], 0xE892: ["skip_previous"], 0xE893: ["skip_next"], 0xE896: ["download", "file_download"],
    0xE8A7: ["open_in_new"], 0xE8B7: ["folder"], 0xE8B9: ["image"], 0xE8C1: ["translate"],
    0xE8C8: ["content_copy"], 0xE8D2: ["text_fields"], 0xE8D6: ["music_note"], 0xE8D7: ["key", "vpn_key"],
    0xE8E5: ["description"], 0xE8EF: ["calculate"], 0xE8F1: ["inventory_2", "inbox"], 0xE916: ["timer"],
    0xE99A: ["smart_toy", "android"], 0xE9D9: ["monitor_heart", "show_chart"], 0xE9E9: ["tune"],
    0xEA80: ["lightbulb"], 0xEA8F: ["notifications"], 0xED14: ["qr_code_scanner", "qr_code"],
    0xEF3C: ["colorize"], 0xF0E2: ["apps"],
    # extra glyphs used by the layout editor
    0xE70D: ["expand_more"], 0xE70E: ["expand_less"], 0xE8CB: ["swap_vert"], 0xE7C4: ["dashboard"],
    0xE72C: ["refresh"], 0xE946: ["info"], 0xE787: ["calendar_today", "event"], 0xE700: ["menu"],
    0xE76B: ["chevron_left"], 0xE76C: ["chevron_right"],
}


def main(otf, codepoints):
    names = {}
    with open(codepoints, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                name, cp = line.split()
                names[name] = int(cp, 16)
    font = TTFont(otf)
    cmap = font.getBestCmap()
    chosen = {}
    for fluent, candidates in MAP.items():
        for name in candidates:
            if name in names and names[name] in cmap:
                chosen[fluent] = cmap[names[name]]
                break
        else:
            raise SystemExit(f"no glyph for U+{fluent:04X} {candidates}")
    opts = subset.Options()
    opts.layout_features = []
    opts.name_IDs = ["*"]
    opts.notdef_outline = True
    sub = subset.Subsetter(opts)
    sub.populate(glyphs=sorted(set(chosen.values())))
    sub.subset(font)
    for table in font["cmap"].tables:
        if table.isUnicode():
            table.cmap = dict(chosen)
    name = font["name"]
    for nid, value in ((1, "Island Icons"), (2, "Regular"), (4, "Island Icons"), (6, "IslandIcons-Regular"),
                       (16, "Island Icons"), (17, "Regular")):
        name.setName(value, nid, 3, 1, 0x409)
        name.setName(value, nid, 1, 0, 0)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    font.save(OUT)
    print(f"{OUT}: {len(chosen)} glyphs, {os.path.getsize(OUT)} bytes")


if __name__ == "__main__":
    main(*sys.argv[1:3])
