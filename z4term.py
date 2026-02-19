#!/usr/bin/env python3
"""z4term — minimal terminal emulator with pane and tab management.

Dependencies: Python 3, GTK 3, VTE 2.91
    sudo apt install python3-gi gir1.2-vte-2.91 libvte-2.91-0
"""

import json
import os
import random
import subprocess
import sys

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")

from gi.repository import Gdk, GLib, Gtk, Pango, Vte  # noqa: E402

# ── Constants ──────────────────────────────────────────────────────────────────

PCRE2_CASELESS = 0x00000008
PCRE2_MULTILINE = 0x00000400
URL_PATTERN = r"(https?://|ftp://|www\.)[^\s)>\]\"']*"

CONFIG_DIR = os.path.expanduser("~/.config/z4term")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
SESSION_FILE = os.path.join(CONFIG_DIR, "session.json")

# ── Themes ─────────────────────────────────────────────────────────────────────

THEMES = {
    "tango-dark": {
        "bg": "#1e1e2e", "fg": "#d0cfcc", "cursor": "#f0f0f0",
        "palette": [
            "#171421", "#c01c28", "#26a269", "#a2734c",
            "#12488b", "#a347ba", "#2aa1b3", "#d0cfcc",
            "#5e5c64", "#f66151", "#33d17a", "#e9ad0c",
            "#2a7bde", "#c061cb", "#33c7de", "#ffffff",
        ],
    },
    "catppuccin-mocha": {
        "bg": "#1e1e2e", "fg": "#cdd6f4", "cursor": "#f5e0dc",
        "palette": [
            "#45475a", "#f38ba8", "#a6e3a1", "#f9e2af",
            "#89b4fa", "#f5c2e7", "#94e2d5", "#bac2de",
            "#585b70", "#f38ba8", "#a6e3a1", "#f9e2af",
            "#89b4fa", "#f5c2e7", "#94e2d5", "#a6adc8",
        ],
    },
    "dracula": {
        "bg": "#282a36", "fg": "#f8f8f2", "cursor": "#f8f8f2",
        "palette": [
            "#21222c", "#ff5555", "#50fa7b", "#f1fa8c",
            "#bd93f9", "#ff79c6", "#8be9fd", "#f8f8f2",
            "#6272a4", "#ff6e6e", "#69ff94", "#ffffa5",
            "#d6acff", "#ff92df", "#a4ffff", "#ffffff",
        ],
    },
    "solarized-dark": {
        "bg": "#002b36", "fg": "#839496", "cursor": "#93a1a1",
        "palette": [
            "#073642", "#dc322f", "#859900", "#b58900",
            "#268bd2", "#d33682", "#2aa198", "#eee8d5",
            "#002b36", "#cb4b16", "#586e75", "#657b83",
            "#839496", "#6c71c4", "#93a1a1", "#fdf6e3",
        ],
    },
    "gruvbox-dark": {
        "bg": "#282828", "fg": "#ebdbb2", "cursor": "#ebdbb2",
        "palette": [
            "#282828", "#cc241d", "#98971a", "#d79921",
            "#458588", "#b16286", "#689d6a", "#a89984",
            "#928374", "#fb4934", "#b8bb26", "#fabd2f",
            "#83a598", "#d3869b", "#8ec07c", "#ebdbb2",
        ],
    },
}

# ── Default Configuration ──────────────────────────────────────────────────────

DEFAULTS = {
    "font_family": "Monospace",
    "font_size": 12,
    "scrollback_lines": 10000,
    "shell": None,
    "theme": "tango-dark",
    "opacity": 1.0,
    "notification_threshold": 5,
    "keybindings": {
        "split_vertical": "Ctrl+Shift+D",
        "split_horizontal": "Ctrl+Shift+E",
        "close_pane": "Ctrl+Shift+W",
        "new_tab": "Ctrl+Shift+T",
        "new_window": "Ctrl+Shift+N",
        "next_pane": "Ctrl+Tab",
        "search": "Ctrl+Shift+F",
        "zoom_in": "Ctrl+plus",
        "zoom_out": "Ctrl+minus",
        "zoom_reset": "Ctrl+0",
        "next_tab": "Ctrl+Shift+Page_Down",
        "prev_tab": "Ctrl+Shift+Page_Up",
        "copy": "Ctrl+Shift+C",
        "paste": "Ctrl+Shift+V",
    },
}


def load_config():
    cfg = json.loads(json.dumps(DEFAULTS))  # deep copy
    try:
        with open(CONFIG_FILE) as fh:
            user = json.load(fh)
            if "keybindings" in user:
                cfg["keybindings"].update(user.pop("keybindings"))
            cfg.update(user)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    if cfg["shell"] is None:
        cfg["shell"] = os.environ.get("SHELL", "/bin/bash")
    # Validate shell path: must exist and be executable
    if not os.path.isfile(cfg["shell"]) or not os.access(cfg["shell"], os.X_OK):
        cfg["shell"] = "/bin/bash"
    if cfg["theme"] not in THEMES:
        cfg["theme"] = "tango-dark"
    cfg["opacity"] = max(0.1, min(1.0, float(cfg["opacity"])))
    cfg["scrollback_lines"] = max(100, min(1_000_000, int(cfg["scrollback_lines"])))
    return cfg


def ensure_config():
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    if not os.path.exists(CONFIG_FILE):
        fd = os.open(CONFIG_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(DEFAULTS, fh, indent=2)


# ── Keybinding parser ─────────────────────────────────────────────────────────

def _parse_binding(text):
    """Parse 'Ctrl+Shift+D' into (keyval, modifier_mask)."""
    mods = 0
    keyval = None
    for part in text.split("+"):
        p = part.strip()
        low = p.lower()
        if low == "ctrl":
            mods |= Gdk.ModifierType.CONTROL_MASK
        elif low == "shift":
            mods |= Gdk.ModifierType.SHIFT_MASK
        elif low == "alt":
            mods |= Gdk.ModifierType.MOD1_MASK
        elif low == "super":
            mods |= Gdk.ModifierType.SUPER_MASK
        else:
            for name in (p, p.lower(), p.upper(), p.capitalize()):
                kv = Gdk.keyval_from_name(name)
                if kv and kv != Gdk.KEY_VoidSymbol:
                    keyval = kv
                    break
    return (keyval, mods) if keyval else (None, 0)


def _build_keymap(bindings):
    """Build {(keyval, mods): action} from config keybindings dict."""
    km = {}
    for action, binding_str in bindings.items():
        kv, mods = _parse_binding(binding_str)
        if kv:
            km[(kv, mods)] = action
            # Register both letter cases so Shift combos work
            lower = Gdk.keyval_to_lower(kv)
            upper = Gdk.keyval_to_upper(kv)
            if lower != upper:
                km[(lower, mods)] = action
                km[(upper, mods)] = action
    return km


# ── Helpers ────────────────────────────────────────────────────────────────────

def _rgba(hex_color, alpha=1.0):
    c = Gdk.RGBA()
    c.parse(hex_color)
    c.alpha = alpha
    return c


def _theme_colors(config):
    theme = THEMES[config["theme"]]
    a = config["opacity"]
    return {
        "fg": _rgba(theme["fg"]),
        "bg": _rgba(theme["bg"], a),
        "cursor": _rgba(theme["cursor"]),
        "palette": [_rgba(h) for h in theme["palette"]],
    }


# ── CSS ────────────────────────────────────────────────────────────────────────

def _random_bright_color():
    """Generate a random bright color suitable for borders."""
    h = random.random()
    # Convert HSL (h, 0.7 saturation, 0.65 lightness) to RGB
    import colorsys
    r, g, b = colorsys.hls_to_rgb(h, 0.65, 0.7)
    return f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"


def _build_css():
    colors = [_random_bright_color() for _ in range(4)]
    return f"""
window {{
    background-color: #1e1e2e;
}}
notebook header tabs tab {{
    padding: 4px 12px;
}}
paned > separator {{
    min-width: 2px;
    min-height: 2px;
    background-color: #444;
}}
#tip-bar {{
    background-color: #2a2a3e;
    padding: 6px 14px;
}}
#tip-bar-title {{
    font-weight: bold;
    font-size: 14px;
    color: #cdd6f4;
}}
#tip-bar-text {{
    font-size: 12px;
}}
vte-terminal.focused {{
    border-top: 2px solid {colors[0]};
    border-right: 2px solid {colors[1]};
    border-bottom: 2px solid {colors[2]};
    border-left: 2px solid {colors[3]};
}}
vte-terminal.unfocused {{
    border: 2px solid transparent;
}}
.tab-activity label {{
    color: #f9e2af;
}}
#search-bar {{
    background-color: #2a2a3e;
    padding: 4px 8px;
}}
#search-bar entry {{
    min-height: 28px;
}}
""".encode()


def _tip(key, action):
    return (
        f'<span bgcolor="#3b3b5c" fgcolor="#89b4fa"><b> {key} </b></span>'
        f' <span fgcolor="#a6adc8">{action}</span>'
    )


TIP_MARKUP = (
    _tip("Ctrl+Shift+D", "Split \u2194") + "   "
    + _tip("Ctrl+Shift+E", "Split \u2195") + "   "
    + _tip("Ctrl+Shift+W", "Close") + "   "
    + _tip("Ctrl+Shift+T", "Tab") + "   "
    + _tip("Ctrl+Shift+N", "Window") + "   "
    + _tip("Ctrl+Tab", "Next Pane") + "   "
    + _tip("Ctrl+Shift+F", "Search") + "   "
    + _tip("Ctrl+\u00b1", "Zoom") + "   "
    + _tip("Ctrl+C", "Copy/SIGINT") + "   "
    + _tip("Ctrl+V", "Paste") + "   "
    + _tip("Right-Click", "Menu")
)


# ── Terminal Pane ──────────────────────────────────────────────────────────────

class TerminalPane(Vte.Terminal):
    """Single VTE-backed terminal pane."""

    def __init__(self, window, cwd=None):
        super().__init__()
        self._window = window
        self._child_pid = None
        self._unfocused_since = None
        cfg = window.config

        # Font
        self._apply_font(cfg)

        # Colors
        colors = _theme_colors(cfg)
        self.set_colors(colors["fg"], colors["bg"], colors["palette"])
        self.set_color_cursor(colors["cursor"])
        self.set_color_cursor_foreground(colors["bg"])

        # Scrollback
        self.set_scrollback_lines(cfg["scrollback_lines"])

        # Focus tracking
        self.connect("focus-in-event", self._on_focus)
        self.connect("focus-out-event", self._on_blur)

        # Shell exit
        self.connect("child-exited", self._on_exit)

        # Tab title from terminal title
        self.connect("window-title-changed", self._on_title_changed)

        # Activity / notification tracking
        self.connect("bell", self._on_bell)
        self.connect("contents-changed", self._on_contents_changed)

        # Clickable URLs
        self._setup_url_matching()
        self.connect("button-press-event", self._on_button_press)

        # Initial unfocused style
        self.get_style_context().add_class("unfocused")

        # Spawn shell
        spawn_dir = cwd or os.environ.get("HOME", "/")
        self.spawn_async(
            Vte.PtyFlags.DEFAULT,
            spawn_dir,
            [cfg["shell"]],
            None,
            GLib.SpawnFlags.DEFAULT,
            None,
            None,
            -1,
            None,
            self._on_spawn_done,
        )

        self.set_hexpand(True)
        self.set_vexpand(True)
        self.show()

    def _apply_font(self, cfg):
        desc = Pango.FontDescription.from_string(
            f"{cfg['font_family']} {cfg['font_size']}"
        )
        self.set_font(desc)

    def _setup_url_matching(self):
        try:
            regex = Vte.Regex.new_for_match(
                URL_PATTERN, -1, PCRE2_CASELESS | PCRE2_MULTILINE
            )
            tag = self.match_add_regex(regex, 0)
        except (AttributeError, TypeError, GLib.Error):
            try:
                regex = GLib.Regex.new(
                    URL_PATTERN,
                    GLib.RegexCompileFlags.CASELESS,
                    GLib.RegexMatchFlags(0),
                )
                tag = self.match_add_gregex(regex, 0)
            except (AttributeError, GLib.Error):
                return
        self.match_set_cursor_name(tag, "pointer")

    # ── Callbacks ──────────────────────────────────────────────────────────────

    def _on_spawn_done(self, _terminal, pid, error, *_data):
        if error:
            print(f"z4term: spawn failed: {error}", file=sys.stderr)
        else:
            self._child_pid = pid

    def _on_focus(self, _widget, _event):
        prev = self._window.focused_terminal
        if prev and prev is not self:
            prev.get_style_context().remove_class("focused")
            prev.get_style_context().add_class("unfocused")
            prev._unfocused_since = GLib.get_monotonic_time()
        self.get_style_context().remove_class("unfocused")
        self.get_style_context().add_class("focused")
        self._unfocused_since = None
        self._window.focused_terminal = self
        self._window._clear_tab_activity(self)
        self._window.refresh_border_colors()
        return False

    def _on_blur(self, _widget, _event):
        self._unfocused_since = GLib.get_monotonic_time()
        return False

    def _on_exit(self, _terminal, _status):
        self._window.close_pane(self)

    def _on_title_changed(self, _terminal):
        title = self.get_window_title()
        if title:
            self._window._update_tab_title_for(self, title)

    def _on_bell(self, _terminal):
        if self is not self._window.focused_terminal:
            self._window._mark_tab_activity(self)
            if self._unfocused_since:
                elapsed = (
                    GLib.get_monotonic_time() - self._unfocused_since
                ) / 1_000_000
                threshold = self._window.config.get("notification_threshold", 5)
                if elapsed >= threshold:
                    self._window._send_notification(
                        "Command finished",
                        "A background terminal rang the bell.",
                    )

    def _on_contents_changed(self, _terminal):
        if self is not self._window.focused_terminal:
            self._window._mark_tab_activity(self)

    def _on_button_press(self, _widget, event):
        # Right-click → context menu
        if event.button == 3:
            self._window._show_context_menu(self, event)
            return True
        # Ctrl+click → open URL
        if event.button == 1 and (event.state & Gdk.ModifierType.CONTROL_MASK):
            try:
                match = self.match_check_event(event)
                if match and match[0]:
                    Gtk.show_uri_on_window(self._window, match[0], event.time)
                    return True
            except Exception:
                pass
        return False

    def get_cwd(self):
        if self._child_pid:
            try:
                return os.readlink(f"/proc/{self._child_pid}/cwd")
            except (OSError, FileNotFoundError):
                pass
        return None


# ── Main Window ────────────────────────────────────────────────────────────────

class TerminalWindow(Gtk.ApplicationWindow):
    """Window containing a notebook of tabs, each with a tree of panes."""

    def __init__(self, config, **kwargs):
        super().__init__(**kwargs)
        self.config = config
        self.focused_terminal = None
        self._closing = False
        self._tab_counter = 0
        self._keymap = _build_keymap(config["keybindings"])

        self.set_title("z4term")
        self.set_default_size(960, 640)

        # Transparency support
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual and config["opacity"] < 1.0:
            self.set_visual(visual)
        self.set_app_paintable(True)

        # Dark theme
        settings = Gtk.Settings.get_default()
        if settings:
            settings.set_property("gtk-application-prefer-dark-theme", True)

        # CSS
        self._css_provider = Gtk.CssProvider()
        self._css_provider.load_from_data(_build_css())
        Gtk.StyleContext.add_provider_for_screen(
            screen,
            self._css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # ── Tip bar ───────────────────────────────────────────────────────────
        tip_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        tip_bar.set_name("tip-bar")
        title_label = Gtk.Label(label="z4term")
        title_label.set_name("tip-bar-title")
        tip_label = Gtk.Label()
        tip_label.set_markup(TIP_MARKUP)
        tip_label.set_name("tip-bar-text")
        tip_label.set_ellipsize(Pango.EllipsizeMode.END)
        tip_bar.pack_start(title_label, False, False, 0)
        tip_bar.pack_start(tip_label, True, True, 0)

        # ── Notebook ──────────────────────────────────────────────────────────
        self.notebook = Gtk.Notebook()
        self.notebook.set_scrollable(True)
        self.notebook.set_show_tabs(False)
        self.notebook.connect("switch-page", self._on_switch_page)

        # ── Search bar ────────────────────────────────────────────────────────
        self._search_revealer = Gtk.Revealer()
        self._search_revealer.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_UP
        )
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        search_box.set_name("search-bar")
        self._search_entry = Gtk.Entry()
        self._search_entry.set_placeholder_text("Search\u2026")
        self._search_entry.connect("activate", lambda _: self._search_next())
        self._search_entry.connect("changed", self._on_search_changed)
        self._search_entry.connect("key-press-event", self._on_search_key)
        btn_prev = Gtk.Button(label="\u25b2")
        btn_prev.set_tooltip_text("Previous match (Shift+Enter)")
        btn_prev.connect("clicked", lambda _: self._search_prev())
        btn_next = Gtk.Button(label="\u25bc")
        btn_next.set_tooltip_text("Next match (Enter)")
        btn_next.connect("clicked", lambda _: self._search_next())
        btn_close = Gtk.Button(label="\u2715")
        btn_close.connect("clicked", lambda _: self._hide_search())
        search_box.pack_start(self._search_entry, True, True, 0)
        search_box.pack_start(btn_prev, False, False, 0)
        search_box.pack_start(btn_next, False, False, 0)
        search_box.pack_start(btn_close, False, False, 0)
        self._search_revealer.add(search_box)

        # ── Layout ────────────────────────────────────────────────────────────
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        vbox.pack_start(tip_bar, False, False, 0)
        vbox.pack_start(self.notebook, True, True, 0)
        vbox.pack_start(self._search_revealer, False, False, 0)
        self.add(vbox)

        # Keyboard at window level
        self.connect("key-press-event", self._on_key)
        self.connect("delete-event", self._on_delete)

        # Periodic CWD-based tab title fallback
        GLib.timeout_add_seconds(3, self._poll_tab_titles)

    def refresh_border_colors(self):
        """Regenerate random border colors for the focused pane."""
        self._css_provider.load_from_data(_build_css())

    def restore_or_init(self, session_data=None):
        """Restore a previous session or create a default tab."""
        if session_data and session_data.get("tabs"):
            try:
                w = session_data.get("window_width", 960)
                h = session_data.get("window_height", 640)
                self.set_default_size(w, h)
                for tab_data in session_data["tabs"]:
                    self._restore_tab(tab_data)
            except Exception:
                pass
        if self.notebook.get_n_pages() == 0:
            self.add_tab()
        self._update_tab_bar()
        self.show_all()
        self._search_revealer.set_reveal_child(False)

    # ── Tabs ───────────────────────────────────────────────────────────────────

    def add_tab(self, cwd=None):
        self._tab_counter += 1
        term = TerminalPane(self, cwd=cwd)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.pack_start(term, True, True, 0)
        box.show_all()

        # Tab label in EventBox for middle-click close
        label = Gtk.Label(label=f"Terminal {self._tab_counter}")
        ebox = Gtk.EventBox()
        ebox.add(label)
        ebox.connect("button-press-event", self._on_tab_label_click, box)
        ebox.show_all()

        idx = self.notebook.append_page(box, ebox)
        self.notebook.set_current_page(idx)
        self._update_tab_bar()
        term.grab_focus()

    def _update_tab_bar(self):
        self.notebook.set_show_tabs(self.notebook.get_n_pages() > 1)

    def _on_switch_page(self, _nb, page, _num):
        terminals = self._collect_terminals(page)
        if terminals:
            GLib.idle_add(terminals[0].grab_focus)
        # Clear activity indicator
        label_w = self.notebook.get_tab_label(page)
        if label_w:
            label_w.get_style_context().remove_class("tab-activity")

    def _on_tab_label_click(self, _widget, event, tab_box):
        """Middle-click closes the tab."""
        if event.button == 2:
            if self.notebook.get_n_pages() <= 1:
                self._closing = True
                self.destroy()
            else:
                page_idx = self.notebook.page_num(tab_box)
                self.notebook.remove_page(page_idx)
                self._update_tab_bar()
                self._focus_any()
            return True
        return False

    def _switch_tab(self, direction):
        n = self.notebook.get_n_pages()
        if n <= 1:
            return
        cur = self.notebook.get_current_page()
        self.notebook.set_current_page((cur + direction) % n)

    # ── Tab titles ─────────────────────────────────────────────────────────────

    def _update_tab_title_for(self, terminal, title):
        for i in range(self.notebook.get_n_pages()):
            page = self.notebook.get_nth_page(i)
            if terminal in self._collect_terminals(page):
                self._set_tab_label_text(page, title)
                break

    def _set_tab_label_text(self, page, text):
        label_w = self.notebook.get_tab_label(page)
        if isinstance(label_w, Gtk.EventBox):
            children = label_w.get_children()
            if children and isinstance(children[0], Gtk.Label):
                if len(text) > 30:
                    text = "\u2026" + text[-29:]
                children[0].set_text(text)

    def _poll_tab_titles(self):
        for i in range(self.notebook.get_n_pages()):
            page = self.notebook.get_nth_page(i)
            terminals = self._collect_terminals(page)
            if terminals:
                t = terminals[0]
                if not t.get_window_title():
                    cwd = t.get_cwd()
                    if cwd:
                        self._set_tab_label_text(
                            page, os.path.basename(cwd) or cwd
                        )
        return True  # keep timer running

    # ── Tab activity & notifications ───────────────────────────────────────────

    def _mark_tab_activity(self, terminal):
        cur_page = self.notebook.get_nth_page(self.notebook.get_current_page())
        for i in range(self.notebook.get_n_pages()):
            page = self.notebook.get_nth_page(i)
            if page is cur_page:
                continue
            if terminal in self._collect_terminals(page):
                label_w = self.notebook.get_tab_label(page)
                if label_w:
                    label_w.get_style_context().add_class("tab-activity")
                break

    def _clear_tab_activity(self, terminal):
        for i in range(self.notebook.get_n_pages()):
            page = self.notebook.get_nth_page(i)
            if terminal in self._collect_terminals(page):
                label_w = self.notebook.get_tab_label(page)
                if label_w:
                    label_w.get_style_context().remove_class("tab-activity")
                break

    def _send_notification(self, title, body):
        try:
            subprocess.Popen(
                ["notify-send", "-a", "z4term", title, body],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            pass

    # ── Pane tree helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _collect_terminals(widget):
        if isinstance(widget, TerminalPane):
            return [widget]
        result = []
        if isinstance(widget, Gtk.Paned):
            for child in (widget.get_child1(), widget.get_child2()):
                if child:
                    result.extend(TerminalWindow._collect_terminals(child))
        elif isinstance(widget, Gtk.Box):
            for child in widget.get_children():
                result.extend(TerminalWindow._collect_terminals(child))
        return result

    def _current_tab_root(self):
        idx = self.notebook.get_current_page()
        return self.notebook.get_nth_page(idx) if idx >= 0 else None

    # ── Split ──────────────────────────────────────────────────────────────────

    def split_pane(self, orientation):
        term = self.focused_terminal
        if term is None:
            return
        parent = term.get_parent()
        if parent is None:
            return

        paned = Gtk.Paned(orientation=orientation)
        paned.set_wide_handle(True)
        new_term = TerminalPane(self, cwd=term.get_cwd())

        if isinstance(parent, Gtk.Box):
            parent.remove(term)
            paned.pack1(term, resize=True, shrink=True)
            paned.pack2(new_term, resize=True, shrink=True)
            parent.pack_start(paned, True, True, 0)
        elif isinstance(parent, Gtk.Paned):
            is_child1 = parent.get_child1() == term
            parent.remove(term)
            paned.pack1(term, resize=True, shrink=True)
            paned.pack2(new_term, resize=True, shrink=True)
            if is_child1:
                parent.pack1(paned, resize=True, shrink=True)
            else:
                parent.pack2(paned, resize=True, shrink=True)

        paned.show_all()

        def _set_half(*_):
            alloc = (
                paned.get_allocated_width()
                if orientation == Gtk.Orientation.HORIZONTAL
                else paned.get_allocated_height()
            )
            paned.set_position(alloc // 2 if alloc > 1 else 400)
            return False

        GLib.idle_add(_set_half)
        new_term.grab_focus()

    # ── Close ──────────────────────────────────────────────────────────────────

    def close_pane(self, target=None):
        if self._closing:
            return
        term = target or self.focused_terminal
        if term is None:
            return
        parent = term.get_parent()
        if parent is None:
            return

        if isinstance(parent, Gtk.Box):
            if self.notebook.get_n_pages() <= 1:
                self._closing = True
                self.destroy()
                return
            page_idx = self.notebook.page_num(parent)
            self.notebook.remove_page(page_idx)
            self._update_tab_bar()
            self._focus_any()
            return

        if isinstance(parent, Gtk.Paned):
            is_child1 = parent.get_child1() == term
            sibling = parent.get_child2() if is_child1 else parent.get_child1()
            parent.remove(term)
            parent.remove(sibling)

            grandparent = parent.get_parent()
            if isinstance(grandparent, Gtk.Box):
                grandparent.remove(parent)
                grandparent.pack_start(sibling, True, True, 0)
            elif isinstance(grandparent, Gtk.Paned):
                is_gp1 = grandparent.get_child1() == parent
                grandparent.remove(parent)
                if is_gp1:
                    grandparent.pack1(sibling, resize=True, shrink=True)
                else:
                    grandparent.pack2(sibling, resize=True, shrink=True)

            sibling.show_all()
            terminals = self._collect_terminals(sibling)
            if terminals:
                terminals[0].grab_focus()

    # ── Navigate ───────────────────────────────────────────────────────────────

    def navigate_next(self):
        root = self._current_tab_root()
        if root is None:
            return
        terminals = self._collect_terminals(root)
        if not terminals:
            return
        try:
            idx = terminals.index(self.focused_terminal)
            nxt = (idx + 1) % len(terminals)
        except ValueError:
            nxt = 0
        terminals[nxt].grab_focus()

    def _focus_any(self):
        root = self._current_tab_root()
        if root:
            ts = self._collect_terminals(root)
            if ts:
                ts[0].grab_focus()

    # ── Zoom ───────────────────────────────────────────────────────────────────

    def _zoom(self, direction):
        """direction: +1 bigger, -1 smaller, 0 reset."""
        if direction == 0:
            self.config["font_size"] = int(
                json.loads(json.dumps(DEFAULTS))["font_size"]
            )
        else:
            self.config["font_size"] = max(
                6, self.config["font_size"] + direction
            )
        for i in range(self.notebook.get_n_pages()):
            page = self.notebook.get_nth_page(i)
            for t in self._collect_terminals(page):
                t._apply_font(self.config)

    # ── New window ─────────────────────────────────────────────────────────────

    def _new_window(self):
        win = TerminalWindow(self.config, application=self.get_application())
        win.restore_or_init()
        win.present()

    # ── Search ─────────────────────────────────────────────────────────────────

    def _toggle_search(self):
        if self._search_revealer.get_reveal_child():
            self._hide_search()
        else:
            self._search_revealer.set_reveal_child(True)
            self._search_entry.grab_focus()

    def _hide_search(self):
        self._search_revealer.set_reveal_child(False)
        if self.focused_terminal:
            try:
                self.focused_terminal.search_set_regex(None, 0)
            except (AttributeError, TypeError):
                try:
                    self.focused_terminal.search_set_gregex(None, 0)
                except (AttributeError, TypeError):
                    pass
            self.focused_terminal.grab_focus()

    def _on_search_changed(self, entry):
        text = entry.get_text()
        term = self.focused_terminal
        if not term or not text:
            return
        escaped = GLib.Regex.escape_string(text)
        try:
            regex = Vte.Regex.new_for_search(escaped, -1, PCRE2_CASELESS)
            term.search_set_regex(regex, 0)
        except (AttributeError, TypeError, GLib.Error):
            try:
                regex = GLib.Regex.new(
                    escaped,
                    GLib.RegexCompileFlags.CASELESS,
                    GLib.RegexMatchFlags(0),
                )
                term.search_set_gregex(regex, GLib.RegexMatchFlags(0))
            except (AttributeError, GLib.Error):
                return
        term.search_set_wrap_around(True)
        term.search_find_previous()

    def _search_next(self):
        if self.focused_terminal:
            self.focused_terminal.search_find_next()

    def _search_prev(self):
        if self.focused_terminal:
            self.focused_terminal.search_find_previous()

    def _on_search_key(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self._hide_search()
            return True
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if event.state & Gdk.ModifierType.SHIFT_MASK:
                self._search_prev()
            else:
                self._search_next()
            return True
        return False

    # ── Context menu ───────────────────────────────────────────────────────────

    def _show_context_menu(self, terminal, event):
        menu = Gtk.Menu()
        items = [
            ("Copy", lambda _: terminal.copy_clipboard_format(Vte.Format.TEXT)),
            ("Paste", lambda _: terminal.paste_clipboard()),
            None,
            ("Split Side-by-Side",
             lambda _: self.split_pane(Gtk.Orientation.HORIZONTAL)),
            ("Split Top/Bottom",
             lambda _: self.split_pane(Gtk.Orientation.VERTICAL)),
            None,
            ("Search\u2026", lambda _: self._toggle_search()),
            None,
            ("Close Pane", lambda _: self.close_pane(terminal)),
        ]
        for item_data in items:
            if item_data is None:
                menu.append(Gtk.SeparatorMenuItem())
            else:
                label, handler = item_data
                mi = Gtk.MenuItem(label=label)
                mi.connect("activate", handler)
                menu.append(mi)
        menu.show_all()
        try:
            menu.popup_at_pointer(event)
        except AttributeError:
            menu.popup(None, None, None, None, event.button, event.time)

    # ── Session save / restore ─────────────────────────────────────────────────

    def _serialize_tree(self, widget):
        if isinstance(widget, TerminalPane):
            return {
                "type": "terminal",
                "cwd": widget.get_cwd() or os.environ.get("HOME", "/"),
            }
        if isinstance(widget, Gtk.Paned):
            orient = (
                "horizontal"
                if widget.get_orientation() == Gtk.Orientation.HORIZONTAL
                else "vertical"
            )
            c1 = widget.get_child1()
            c2 = widget.get_child2()
            return {
                "type": "paned",
                "orientation": orient,
                "position": widget.get_position(),
                "child1": self._serialize_tree(c1) if c1 else None,
                "child2": self._serialize_tree(c2) if c2 else None,
            }
        if isinstance(widget, Gtk.Box):
            children = widget.get_children()
            if children:
                return self._serialize_tree(children[0])
        return None

    def save_session(self):
        session = {
            "window_width": self.get_allocated_width(),
            "window_height": self.get_allocated_height(),
            "tabs": [],
        }
        for i in range(self.notebook.get_n_pages()):
            page = self.notebook.get_nth_page(i)
            tree = self._serialize_tree(page)
            if tree:
                session["tabs"].append(tree)
        try:
            fd = os.open(SESSION_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                json.dump(session, fh, indent=2)
        except OSError:
            pass

    def _restore_tab(self, tab_data):
        widget = self._restore_tree(tab_data)
        if widget is None:
            return
        self._tab_counter += 1
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.pack_start(widget, True, True, 0)
        box.show_all()

        label = Gtk.Label(label=f"Terminal {self._tab_counter}")
        ebox = Gtk.EventBox()
        ebox.add(label)
        ebox.connect("button-press-event", self._on_tab_label_click, box)
        ebox.show_all()

        idx = self.notebook.append_page(box, ebox)
        self.notebook.set_current_page(idx)

    def _restore_tree(self, data, depth=0):
        if data is None or depth > 20:
            return None
        if data.get("type") == "terminal":
            return TerminalPane(self, cwd=data.get("cwd"))
        if data.get("type") == "paned":
            orient = (
                Gtk.Orientation.HORIZONTAL
                if data.get("orientation") == "horizontal"
                else Gtk.Orientation.VERTICAL
            )
            paned = Gtk.Paned(orientation=orient)
            paned.set_wide_handle(True)
            c1 = self._restore_tree(data.get("child1"), depth + 1)
            c2 = self._restore_tree(data.get("child2"), depth + 1)
            if c1:
                paned.pack1(c1, resize=True, shrink=True)
            if c2:
                paned.pack2(c2, resize=True, shrink=True)
            pos = data.get("position")
            if pos is not None:
                GLib.idle_add(lambda p=pos: paned.set_position(p) or False)
            return paned
        return None

    def _on_delete(self, _widget, _event):
        self.save_session()
        return False

    # ── Keyboard handler ───────────────────────────────────────────────────────

    def _on_key(self, _widget, event):
        kv = event.keyval
        state = event.state & (
            Gdk.ModifierType.CONTROL_MASK
            | Gdk.ModifierType.SHIFT_MASK
            | Gdk.ModifierType.MOD1_MASK
        )

        # Look up action in custom keymap
        action = self._keymap.get((kv, state))
        if action:
            return self._handle_action(action)

        # Smart Ctrl+C: copy if selection, else let VTE send SIGINT
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if ctrl and not shift and kv in (Gdk.KEY_c, Gdk.KEY_C):
            t = self.focused_terminal
            if t and t.get_has_selection():
                t.copy_clipboard_format(Vte.Format.TEXT)
                return True
            return False

        # Smart Ctrl+V: paste
        if ctrl and not shift and kv in (Gdk.KEY_v, Gdk.KEY_V):
            t = self.focused_terminal
            if t:
                t.paste_clipboard()
                return True

        return False

    def _handle_action(self, action):
        t = self.focused_terminal
        dispatch = {
            "split_vertical":
                lambda: self.split_pane(Gtk.Orientation.HORIZONTAL),
            "split_horizontal":
                lambda: self.split_pane(Gtk.Orientation.VERTICAL),
            "close_pane": self.close_pane,
            "new_tab": self.add_tab,
            "new_window": self._new_window,
            "next_pane": self.navigate_next,
            "search": self._toggle_search,
            "zoom_in": lambda: self._zoom(1),
            "zoom_out": lambda: self._zoom(-1),
            "zoom_reset": lambda: self._zoom(0),
            "next_tab": lambda: self._switch_tab(1),
            "prev_tab": lambda: self._switch_tab(-1),
            "copy":
                lambda: t and t.copy_clipboard_format(Vte.Format.TEXT),
            "paste":
                lambda: t and t.paste_clipboard(),
        }
        fn = dispatch.get(action)
        if fn:
            fn()
            return True
        return False


# ── Application ────────────────────────────────────────────────────────────────

class Z4TermApp(Gtk.Application):
    def __init__(self, config):
        from gi.repository import Gio
        super().__init__(
            application_id="com.github.z4term",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        self.config = config

    def do_activate(self):
        win = TerminalWindow(self.config, application=self)
        # Try to restore previous session
        session = None
        try:
            with open(SESSION_FILE) as fh:
                session = json.load(fh)
            os.remove(SESSION_FILE)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        win.restore_or_init(session)
        win.present()


def main():
    ensure_config()
    cfg = load_config()
    app = Z4TermApp(cfg)
    sys.exit(app.run(sys.argv))


if __name__ == "__main__":
    main()
