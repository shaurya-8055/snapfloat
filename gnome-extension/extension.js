// SnapFloat thumbnails title themselves "snapfloat-thumb <corner> <offset>".
// Wayland gives apps no say over placement, so the shell does it for them.
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const TITLE = /^snapfloat-thumb (\S+) (\d+)$/;
const FRESH_US = 500_000; // a thumb focused this soon after appearing was auto-focused, not clicked

export default class SnapFloatExtension extends Extension {
    enable() {
        this._signals = new Map(); // window -> signal ids
        this._fresh = new Map(); // window -> {at, prev}
        this._placed = new Set();
        this._created = global.display.connect('window-created', (_d, win) => this._track(win));
        this._focus = global.display.connect('notify::focus-window', () => this._keepFocus());
        for (const actor of global.get_window_actors())
            this._track(actor.meta_window);
    }

    disable() {
        global.display.disconnect(this._created);
        global.display.disconnect(this._focus);
        for (const [win, ids] of this._signals)
            ids.forEach(id => win.disconnect(id));
        this._signals.clear();
        this._fresh.clear();
        this._placed.clear();
    }

    _track(win) {
        // app_id/wm_class isn't known yet at creation, so watch every window and let the title decide
        if (this._signals.has(win))
            return;
        this._signals.set(win, [
            win.connect('notify::title', () => this._place(win)), // re-stack after a thumb closes
            win.connect('shown', () => this._place(win)), // after mutter's own placement, so ours sticks
            win.connect('size-changed', () => this._place(win)), // a crop in markup resizes it: re-anchor to the corner
        ]);
        this._signals.get(win).push(win.connect('unmanaged', () => {
            this._signals.delete(win);
            this._fresh.delete(win);
            this._placed.delete(win);
        }));
        this._fresh.set(win, {at: GLib.get_monotonic_time(), prev: global.display.focus_window});
    }

    _place(win) {
        const m = TITLE.exec(win.get_title() ?? '');
        if (!m)
            return; // settings dialog and friends
        const [, corner, offset] = m;
        if (!win.is_above())
            win.make_above();
        if (!win.is_on_all_workspaces())
            win.stick();
        const area = Main.layoutManager.getWorkAreaForMonitor(global.display.get_current_monitor());
        const r = win.get_frame_rect();
        if (!r.width)
            return; // not mapped yet: 'shown' will call us again
        const x = corner.endsWith('right') ? area.x + area.width - r.width : area.x;
        const y = corner.startsWith('bottom') ? area.y + area.height - r.height - Number(offset) : area.y + Number(offset);
        const restack = this._placed.has(win);
        this._placed.add(win);
        if (r.x === x && r.y === y)
            return;
        win.move_frame(false, x, y);
        const actor = win.get_compositor_private();
        if (restack && actor) { // glide from the old slot instead of jumping
            actor.translation_x = r.x - x;
            actor.translation_y = r.y - y;
            actor.ease({translation_x: 0, translation_y: 0, duration: 220, mode: Clutter.AnimationMode.EASE_OUT_CUBIC});
        }
    }

    _keepFocus() {
        const win = global.display.focus_window;
        const fresh = this._fresh.get(win);
        if (!fresh)
            return;
        this._fresh.delete(win);
        if (TITLE.test(win.get_title() ?? '') && GLib.get_monotonic_time() - fresh.at < FRESH_US && fresh.prev && fresh.prev !== win)
            fresh.prev.activate(global.get_current_time());
    }
}
