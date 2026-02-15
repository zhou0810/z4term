# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

z4term is a native Linux terminal emulator built with Python, GTK 3, and VTE 2.91. It features pane management, tab support, session restore, search, clickable URLs, theming, transparency, and a minimal dark UI.

## Tech Stack

- **Language:** Python 3
- **UI Framework:** GTK 3 (via PyGObject / `gi.repository`)
- **Terminal Widget:** VTE 2.91 (`gi.repository.Vte`)
- **Platform:** Linux only
- **Dependencies:** `sudo apt install python3-gi gir1.2-vte-2.91 libvte-2.91-0`

## Running

```bash
python3 z4term.py
```

## Architecture

Single-file application (`z4term.py`) with three classes:

- **`TerminalPane(Vte.Terminal)`** — wraps VTE with font/color/URL config, shell spawning, focus/blur tracking, bell/activity signals, right-click handling, and child-exit handling
- **`TerminalWindow(Gtk.ApplicationWindow)`** — main window managing a `Gtk.Notebook` (tabs), each tab containing a pane tree of `Gtk.Paned` widgets; includes search bar (`Gtk.Revealer`), context menu, session save/restore, zoom, and a configurable keymap
- **`Z4TermApp(Gtk.Application)`** — GTK application entry point; loads session from `~/.config/z4term/session.json` on start

**Pane tree structure:** each tab root is a `Gtk.Box` holding either a single `TerminalPane` or a nested `Gtk.Paned` tree. Splitting replaces a terminal with a Paned containing the original + a new terminal. Closing collapses the Paned by promoting the sibling.

**Keybinding system:** shortcuts are parsed from config strings (e.g. `"Ctrl+Shift+D"`) into `(keyval, modifier_mask)` tuples via `_parse_binding()` and stored in a keymap dict. The window's `_on_key` handler does a dict lookup then dispatches to `_handle_action()`.

## Configuration

JSON config at `~/.config/z4term/config.json`:

- `font_family`, `font_size`, `scrollback_lines`, `shell`
- `theme` — one of: `tango-dark`, `catppuccin-mocha`, `dracula`, `solarized-dark`, `gruvbox-dark`
- `opacity` — float 0.1–1.0 for window transparency
- `notification_threshold` — seconds before bell triggers desktop notification
- `keybindings` — map of action names to key combo strings (all remappable)

Session state saved to `~/.config/z4term/session.json` on window close.

## Keyboard Shortcuts (defaults, all remappable)

| Shortcut | Action |
|---|---|
| Ctrl+Shift+D | Split pane side-by-side |
| Ctrl+Shift+E | Split pane top/bottom |
| Ctrl+Shift+W | Close current pane |
| Ctrl+Shift+T | New tab |
| Ctrl+Shift+N | New window |
| Ctrl+Tab | Navigate to next pane |
| Ctrl+Shift+F | Toggle search bar |
| Ctrl+Plus / Ctrl+Minus | Zoom in/out |
| Ctrl+0 | Reset zoom |
| Ctrl+Shift+PageUp/Down | Switch tabs |
| Ctrl+Shift+C / Ctrl+Shift+V | Copy / Paste |
| Ctrl+C | Copy (if selected) / SIGINT (if not) |
| Ctrl+V | Paste from clipboard |
| Right-click | Context menu |
| Middle-click tab | Close tab |
| Ctrl+click URL | Open in browser |
