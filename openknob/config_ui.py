#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable, Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

from openknob.actions import execute_action
from openknob.settings import load_settings, save_settings, PROFILES_DIR

BUILTIN_ACTIONS = [
    ("Volume Up",       "volume:up"),
    ("Volume Down",     "volume:down"),
    ("Brightness Up",   "brightness:up"),
    ("Brightness Down", "brightness:down"),
    ("Scroll Up",       "scroll:up"),
    ("Scroll Down",     "scroll:down"),
    ("Next Function",   "next_function"),
]

_COLOR_DEFAULTS = {
    "ring_track": [255, 255, 255,  18],
    "ring_fill":  [ 40, 190, 255, 255],
    "background": [ 10,  10,  16, 210],
    "text":       [255, 255, 255, 255],
}


# ── Position picker ───────────────────────────────────────────────────────────

class PositionPicker(Gtk.Grid):
    _POSITIONS = [
        ("top-left",     0, 0, "↖"), ("top-center",    0, 1, "↑"), ("top-right",    0, 2, "↗"),
        ("middle-left",  1, 0, "←"), ("center",         1, 1, "⊕"), ("middle-right", 1, 2, "→"),
        ("bottom-left",  2, 0, "↙"), ("bottom-center",  2, 1, "↓"), ("bottom-right", 2, 2, "↘"),
    ]

    def __init__(self, current: str = "bottom-right"):
        super().__init__(row_spacing=4, column_spacing=4, halign=Gtk.Align.CENTER)
        self._value = current
        self._buttons: dict[str, Gtk.ToggleButton] = {}
        leader = None
        for pos, row, col, icon in self._POSITIONS:
            btn = Gtk.ToggleButton(label=icon)
            btn.set_size_request(44, 44)
            if leader is None:
                leader = btn
            else:
                btn.set_group(leader)
            btn.set_active(pos == current)
            btn.connect("toggled", self._on_toggled, pos)
            self.attach(btn, col, row, 1, 1)
            self._buttons[pos] = btn

    def _on_toggled(self, btn: Gtk.ToggleButton, pos: str) -> None:
        if btn.get_active():
            self._value = pos

    def get_value(self) -> str:
        return self._value

    def set_value(self, pos: str) -> None:
        self._value = pos
        if pos in self._buttons:
            self._buttons[pos].set_active(True)


# ── Config window ─────────────────────────────────────────────────────────────

class ConfigWindow(Adw.ApplicationWindow):

    def __init__(self, **kw):
        super().__init__(title="openknob", default_width=960, default_height=640, **kw)
        self._profiles: list[dict] = []
        self._profile_paths: list[Optional[Path]] = []
        self._cur_prof: int = -1
        self._updating: bool = False
        self._settings: dict = load_settings()
        self._func_rows: list[Adw.ExpanderRow] = []
        self._color_btns: dict[str, Gtk.ColorDialogButton] = {}

        # ── Root layout ───────────────────────────────────────────────────────
        overlay = Adw.ToastOverlay()
        self._toast = overlay

        toolbar_view = Adw.ToolbarView()
        overlay.set_child(toolbar_view)
        self.set_content(overlay)

        header = Adw.HeaderBar()
        self._stack = Adw.ViewStack()

        switcher = Adw.ViewSwitcher()
        switcher.set_stack(self._stack)
        switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)

        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", self._on_save)
        header.pack_end(save_btn)

        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(self._stack)

        self._stack.add_titled_with_icon(
            self._build_profiles_page(), "profiles", "Profiles",
            "system-users-symbolic",
        )
        self._stack.add_titled_with_icon(
            self._build_appearance_page(), "appearance", "Appearance",
            "preferences-desktop-color-symbolic",
        )

        self._load_profiles()

    # ── Profiles page ─────────────────────────────────────────────────────────

    def _build_profiles_page(self) -> Gtk.Widget:
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(210)
        paned.set_shrink_start_child(False)
        paned.set_shrink_end_child(False)

        # Sidebar
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar.set_size_request(200, -1)

        sw = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self._profile_list = Gtk.ListBox()
        self._profile_list.add_css_class("navigation-sidebar")
        self._profile_list.set_selection_mode(Gtk.SelectionMode.BROWSE)
        self._profile_list.connect("row-selected", self._on_profile_selected)
        sw.set_child(self._profile_list)
        sidebar.append(sw)

        action_bar = Gtk.ActionBar()
        add_btn = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add profile")
        add_btn.add_css_class("flat")
        add_btn.connect("clicked", lambda _: self._add_profile())
        del_btn = Gtk.Button(icon_name="list-remove-symbolic", tooltip_text="Remove profile")
        del_btn.add_css_class("flat")
        del_btn.connect("clicked", lambda _: self._del_profile())
        action_bar.pack_start(add_btn)
        action_bar.pack_start(del_btn)
        sidebar.append(action_bar)

        paned.set_start_child(sidebar)

        # Editor area
        editor_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self._editor_page = Adw.PreferencesPage()
        editor_scroll.set_child(self._editor_page)

        # Profile meta group
        meta_group = Adw.PreferencesGroup()
        meta_group.set_title("Profile")

        self._name_row = Adw.EntryRow()
        self._name_row.set_title("Name")
        self._name_row.connect("changed", self._sync_profile_meta)
        meta_group.add(self._name_row)

        self._match_row = Adw.EntryRow()
        self._match_row.set_title("App match")
        self._match_row.set_tooltip_text(
            "Regex matched against active window class (e.g. blender|krita)"
        )
        self._match_row.connect("changed", self._sync_profile_meta)
        meta_group.add(self._match_row)

        self._editor_page.add(meta_group)

        # Functions group
        self._func_group = Adw.PreferencesGroup()
        self._func_group.set_title("Functions")
        add_f = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add function")
        add_f.add_css_class("flat")
        add_f.connect("clicked", lambda _: self._add_function())
        self._func_group.set_header_suffix(add_f)
        self._editor_page.add(self._func_group)

        paned.set_end_child(editor_scroll)
        return paned

    def _make_action_row(self, title: str, value: str,
                         on_change: Callable[[str], None]) -> Adw.EntryRow:
        row = Adw.EntryRow()
        row.set_title(title)
        row.set_text(value)
        row.connect("changed", lambda r: on_change(r.get_text()))

        # Built-in picker popover
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                      margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        pop = Gtk.Popover()
        pop.set_child(box)
        for label, key in BUILTIN_ACTIONS:
            btn = Gtk.Button(label=label)
            btn.add_css_class("flat")
            btn.connect("clicked", lambda b, k=key, r=row, p=pop: (r.set_text(k), p.popdown()))
            box.append(btn)

        menu_btn = Gtk.MenuButton(icon_name="pan-down-symbolic",
                                   tooltip_text="Choose built-in action")
        menu_btn.add_css_class("flat")
        menu_btn.set_valign(Gtk.Align.CENTER)
        menu_btn.set_popover(pop)
        row.add_suffix(menu_btn)
        return row

    def _make_func_expander(self, func: dict, idx: int) -> Adw.ExpanderRow:
        def subtitle(f: dict) -> str:
            cw  = f.get("rotate_cw",  "—") or "—"
            ccw = f.get("rotate_ccw", "—") or "—"
            return f"CW: {cw}   ·   CCW: {ccw}"

        exp = Adw.ExpanderRow()
        exp.set_title(func.get("label", "Function"))
        exp.set_subtitle(subtitle(func))

        # Label
        lbl_row = Adw.EntryRow()
        lbl_row.set_title("Label")
        lbl_row.set_text(func.get("label", ""))

        def on_label(text: str, f=func, e=exp) -> None:
            if self._updating:
                return
            f["label"] = text
            e.set_title(text or "Function")

        lbl_row.connect("changed", lambda r: on_label(r.get_text()))
        exp.add_row(lbl_row)

        # CW / CCW / Press
        for field, title in [
            ("rotate_cw",  "Rotate clockwise"),
            ("rotate_ccw", "Rotate counter-clockwise"),
            ("press",      "Button press"),
        ]:
            def make_cb(f=func, k=field, e=exp) -> Callable[[str], None]:
                def cb(v: str) -> None:
                    if self._updating:
                        return
                    f[k] = v
                    e.set_subtitle(subtitle(f))
                return cb

            action_row = self._make_action_row(title, func.get(field, ""), make_cb())
            exp.add_row(action_row)

        # Display flags
        show_pct_row = Adw.SwitchRow()
        show_pct_row.set_title("Display percentage")
        show_pct_row.set_subtitle("Show value gauge in overlay (disable when using system OSD actions)")
        show_pct_row.set_active(func.get("show_percentage", True))
        def on_show_pct(r, _p, f=func) -> None:
            if not self._updating:
                f["show_percentage"] = r.get_active()
        show_pct_row.connect("notify::active", on_show_pct)
        exp.add_row(show_pct_row)

        show_ring_row = Adw.SwitchRow()
        show_ring_row.set_title("Display circle")
        show_ring_row.set_subtitle("Show ring arc in overlay")
        show_ring_row.set_active(func.get("show_ring", True))
        def on_show_ring(r, _p, f=func) -> None:
            if not self._updating:
                f["show_ring"] = r.get_active()
        show_ring_row.connect("notify::active", on_show_ring)
        exp.add_row(show_ring_row)

        # Test row
        test_row = Adw.ActionRow()
        test_row.set_title("Test")
        test_box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        for btn_lbl, field in [("▶ CW", "rotate_cw"), ("▶ CCW", "rotate_ccw"), ("▶ Press", "press")]:
            tb = Gtk.Button(label=btn_lbl)
            tb.add_css_class("flat")
            tb.connect("clicked", lambda b, f=func, k=field: execute_action(f.get(k, "")))
            test_box.append(tb)
        test_row.add_suffix(test_box)
        exp.add_row(test_row)

        # Delete button
        del_btn = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Remove function")
        del_btn.add_css_class("flat")
        del_btn.add_css_class("destructive-action")
        del_btn.set_valign(Gtk.Align.CENTER)
        del_btn.connect("clicked", lambda b, i=idx: self._del_function(i))
        exp.add_prefix(del_btn)

        return exp

    # ── Appearance page ───────────────────────────────────────────────────────

    def _build_appearance_page(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()
        ov     = self._settings.get("overlay", {})
        colors = ov.get("colors", {})

        # Position group
        pos_group = Adw.PreferencesGroup()
        pos_group.set_title("Position")
        pos_group.set_description("Where the dial overlay appears on screen")

        pos_row = Adw.ActionRow()
        pos_row.set_title("Anchor point")
        self._pos_picker = PositionPicker(ov.get("position", "bottom-right"))
        self._pos_picker.set_valign(Gtk.Align.CENTER)
        self._pos_picker.set_margin_top(8)
        self._pos_picker.set_margin_bottom(8)
        for btn in self._pos_picker._buttons.values():
            btn.connect("toggled", lambda *_: GLib.idle_add(self._auto_save_appearance))
        pos_row.add_suffix(self._pos_picker)
        pos_group.add(pos_row)

        margin_adj = Gtk.Adjustment(value=ov.get("margin", 20),
                                     lower=0, upper=200, step_increment=1)
        margin_adj.connect("value-changed", lambda *_: self._auto_save_appearance())
        self._margin_adj = margin_adj
        margin_spin = Gtk.SpinButton(adjustment=margin_adj, valign=Gtk.Align.CENTER)
        margin_row = Adw.ActionRow()
        margin_row.set_title("Edge margin")
        margin_row.set_subtitle("Distance from screen edge (px)")
        margin_row.add_suffix(margin_spin)
        margin_row.set_activatable_widget(margin_spin)
        pos_group.add(margin_row)

        page.add(pos_group)

        # Size group
        size_group = Adw.PreferencesGroup()
        size_group.set_title("Size")
        size_adj = Gtk.Adjustment(value=ov.get("size", 220),
                                   lower=150, upper=340, step_increment=2)
        size_adj.connect("value-changed", lambda *_: self._auto_save_appearance())
        self._size_adj = size_adj
        scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                          adjustment=size_adj, hexpand=True, valign=Gtk.Align.CENTER)
        scale.set_draw_value(True)
        scale.set_value_pos(Gtk.PositionType.RIGHT)
        for mark in (150, 220, 280, 340):
            scale.add_mark(mark, Gtk.PositionType.BOTTOM, str(mark))
        size_row = Adw.ActionRow()
        size_row.set_title("Overlay diameter")
        size_row.set_subtitle("Pixels")
        size_row.add_suffix(scale)
        size_group.add(size_row)
        page.add(size_group)

        # Colors group
        col_group = Adw.PreferencesGroup()
        col_group.set_title("Colors")
        col_group.set_description("Changes apply to the overlay within 1 second")

        for key, title, desc in [
            ("ring_track", "Ring track",       "Background arc"),
            ("ring_fill",  "Ring fill",         "Active arc and accent color"),
            ("background", "Background",        "Overlay background fill"),
            ("text",       "Text",              "Labels and value"),
        ]:
            rgba = colors.get(key, _COLOR_DEFAULTS[key])
            gdk_c = Gdk.RGBA()
            gdk_c.red, gdk_c.green, gdk_c.blue, gdk_c.alpha = (
                rgba[0] / 255, rgba[1] / 255, rgba[2] / 255, rgba[3] / 255
            )
            dialog = Gtk.ColorDialog()
            dialog.set_with_alpha(True)
            color_btn = Gtk.ColorDialogButton(dialog=dialog)
            color_btn.set_rgba(gdk_c)
            color_btn.set_valign(Gtk.Align.CENTER)
            color_btn.connect("notify::rgba", lambda b, _p, k=key: self._on_color(b, k))
            self._color_btns[key] = color_btn

            row = Adw.ActionRow()
            row.set_title(title)
            row.set_subtitle(desc)
            row.add_suffix(color_btn)
            row.set_activatable_widget(color_btn)
            col_group.add(row)

        page.add(col_group)
        return page

    # ── Profile data ──────────────────────────────────────────────────────────

    def _load_profiles(self) -> None:
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        self._profiles.clear()
        self._profile_paths.clear()
        for path in sorted(PROFILES_DIR.glob("*.json")):
            try:
                self._profiles.append(json.loads(path.read_text()))
                self._profile_paths.append(path)
            except Exception:
                pass
        if not self._profiles:
            self._profiles.append({
                "name": "default", "match": ".*",
                "functions": [
                    {"label": "Volume",     "icon": "", "rotate_cw": "volume:up",
                     "rotate_ccw": "volume:down",     "press": "next_function",
                     "show_percentage": False, "show_ring": False},
                    {"label": "Brightness", "icon": "", "rotate_cw": "brightness:up",
                     "rotate_ccw": "brightness:down", "press": "next_function",
                     "show_percentage": False, "show_ring": False},
                ],
            })
            self._profile_paths.append(None)
        self._rebuild_sidebar()

    def _rebuild_sidebar(self) -> None:
        while (row := self._profile_list.get_row_at_index(0)):
            self._profile_list.remove(row)
        for prof in self._profiles:
            row = Adw.ActionRow()
            row.set_title(prof.get("name", "unnamed"))
            row.set_activatable(True)
            self._profile_list.append(row)
        first = self._profile_list.get_row_at_index(0)
        if first:
            self._profile_list.select_row(first)

    def _on_profile_selected(self, _lb: Gtk.ListBox, row: Optional[Gtk.ListBoxRow]) -> None:
        if row is None:
            return
        self._cur_prof = row.get_index()
        prof = self._profiles[self._cur_prof]
        self._updating = True
        self._name_row.set_text(prof.get("name", ""))
        self._match_row.set_text(prof.get("match", ".*"))
        self._updating = False
        self._rebuild_func_rows()

    def _sync_profile_meta(self, *_) -> None:
        if self._updating or self._cur_prof < 0:
            return
        prof = self._profiles[self._cur_prof]
        prof["name"]  = self._name_row.get_text()
        prof["match"] = self._match_row.get_text()
        row = self._profile_list.get_row_at_index(self._cur_prof)
        if isinstance(row, Adw.ActionRow):
            row.set_title(prof["name"])

    def _add_profile(self) -> None:
        new = {"name": "new_profile", "match": ".*", "functions": []}
        self._profiles.append(new)
        self._profile_paths.append(None)
        row = Adw.ActionRow()
        row.set_title(new["name"])
        row.set_activatable(True)
        self._profile_list.append(row)
        self._profile_list.select_row(row)

    def _del_profile(self) -> None:
        idx = self._cur_prof
        if idx < 0 or len(self._profiles) <= 1:
            return
        path = self._profile_paths[idx]
        if path and path.exists():
            path.unlink()
        self._profiles.pop(idx)
        self._profile_paths.pop(idx)
        row = self._profile_list.get_row_at_index(idx)
        if row:
            self._profile_list.remove(row)
        self._cur_prof = -1
        first = self._profile_list.get_row_at_index(0)
        if first:
            self._profile_list.select_row(first)

    # ── Function data ─────────────────────────────────────────────────────────

    def _rebuild_func_rows(self) -> None:
        for old in self._func_rows:
            self._func_group.remove(old)
        self._func_rows.clear()
        if self._cur_prof < 0:
            return
        for i, func in enumerate(self._profiles[self._cur_prof].get("functions", [])):
            row = self._make_func_expander(func, i)
            self._func_group.add(row)
            self._func_rows.append(row)

    def _add_function(self) -> None:
        if self._cur_prof < 0:
            return
        new_f = {"label": "New", "icon": "", "rotate_cw": "",
                 "rotate_ccw": "", "press": "next_function",
                 "show_percentage": True, "show_ring": True}
        funcs = self._profiles[self._cur_prof].setdefault("functions", [])
        funcs.append(new_f)
        row = self._make_func_expander(new_f, len(funcs) - 1)
        self._func_group.add(row)
        self._func_rows.append(row)
        row.set_expanded(True)

    def _del_function(self, idx: int) -> None:
        if self._cur_prof < 0:
            return
        funcs = self._profiles[self._cur_prof].get("functions", [])
        if idx < len(funcs):
            funcs.pop(idx)
        self._rebuild_func_rows()

    # ── Appearance auto-apply ─────────────────────────────────────────────────

    def _on_color(self, btn: Gtk.ColorDialogButton, key: str) -> None:
        c = btn.get_rgba()
        self._settings.setdefault("overlay", {}).setdefault("colors", {})[key] = [
            int(c.red * 255), int(c.green * 255),
            int(c.blue * 255), int(c.alpha * 255),
        ]
        self._auto_save_appearance()

    def _auto_save_appearance(self) -> None:
        ov = self._settings.setdefault("overlay", {})
        ov["position"] = self._pos_picker.get_value()
        ov["margin"]   = int(self._margin_adj.get_value())
        ov["size"]     = int(self._size_adj.get_value())
        save_settings(self._settings)

    # ── Save profiles ─────────────────────────────────────────────────────────

    def _on_save(self, *_) -> None:
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        for i, prof in enumerate(self._profiles):
            name = prof.get("name", f"profile_{i}")
            path = self._profile_paths[i] or PROFILES_DIR / f"{name}.json"
            self._profile_paths[i] = path
            path.write_text(json.dumps(prof, indent=2, ensure_ascii=False))
        toast = Adw.Toast(title=f"Saved {len(self._profiles)} profile(s)")
        toast.set_timeout(2)
        self._toast.add_toast(toast)


# ── App entry point ───────────────────────────────────────────────────────────

class OpenKnobApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="io.openknob.Config")
        self.connect("activate", self._on_activate)

    def _on_activate(self, app: Adw.Application) -> None:
        win = ConfigWindow(application=app)
        win.present()


def main() -> int:
    return OpenKnobApp().run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
