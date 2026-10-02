# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2008-2026 Andrew Ziem.
#
# This work is licensed under the terms of the GNU GPL, version 3 or
# later.  See the COPYING file in the top-level directory.

import logging
import os
import sys
import threading
import time

import bleachbit
from bleachbit import APP_NAME, Cleaner, FileUtilities, GuiBasic, Language, appicon_path, IS_MAC, IS_WINDOWS
from bleachbit.Cleaner import backends, register_cleaners
from bleachbit.Constant import ABORT_BUTTON_LABEL, REQUIRES_EXPERT_MODE
from bleachbit.GUI import logger
from bleachbit.General import sanitize_surrogates
from bleachbit.GtkShim import GLib, Gdk, Gio, Gtk, require_gtk
from bleachbit.GuiInfoBar import InfoBarMixin
from bleachbit.GuiStartup import get_startup_messages
from bleachbit.GuiTreeModels import TreeDisplayModel, TreeInfoModel
from bleachbit.GuiUtil import (clear_clipboard, get_font_size_from_name,
                               get_window_info, notify, resolve_icon_name, threaded)
from bleachbit.Language import get_app_full_name, get_text as _
from bleachbit.Options import options
from bleachbit.Wipe import detect_orphaned_wipe_files

if IS_WINDOWS:
    from bleachbit import Windows


# TRANSLATORS: Button label on the headerbar and context menu item
# in the treeview.
# 'Preview' is a verb.
PREVIEW_MSG = _('Preview')

# TRANSLATORS: Button label on the headerbar and context menu item
# in the treeview.
# 'Clean' is a verb.
CLEAN_MSG = _('Clean')

# TRANSLATORS: Button in tree view's context menu to open the cookie
# manager.
# Preserve the ellipsis as literal Unicode (…) or as Unicode escape (\u2026).
MANAGE_COOKIES_TO_KEEP = _("Manage cookies to keep\u2026")

# Ensure GTK is available for this GUI module
require_gtk()

bleachbit.log_startup_time('GuiWindow imported')


def _iter_rows(model, parent=None):
    """Yield the tree iter of each row under parent, or of each top-level row"""
    if parent is None:
        tree_iter = model.get_iter_first()
    else:
        tree_iter = model.iter_children(parent)
    while tree_iter:
        yield tree_iter
        tree_iter = model.iter_next(tree_iter)


class GUI(InfoBarMixin, Gtk.ApplicationWindow):
    """The main application GUI"""
    _error_tag_color = None
    _showed_startup_messages = False
    _scroll_pending = False
    _scroll_again = False
    _register_generation = 0
    _app_menu_generation = None
    _brand_css_provider = None
    _summary_label = None
    _summary_update_id = None
    _filter_text = ''
    recognized_cleanerml = False

    def __init__(self, auto_exit, *args, **kwargs):
        super().__init__(*args, **kwargs)
        bleachbit.log_startup_time('window created')

        self._show_splash_screen()
        bleachbit.log_startup_time('splash checked')

        self._auto_exit = auto_exit
        self._gui_cleaner_cleanup_pending = None
        self._filter_text = ''

        self.set_property('name', APP_NAME)
        self.set_property('role', APP_NAME)
        self._apply_brand_css()
        self.populate_window()

        # Redirect logging to the GUI.
        bb_logger = logging.getLogger('bleachbit')
        from bleachbit.Log import GtkLoggerHandler
        self.gtklog = GtkLoggerHandler(self.append_text)
        bb_logger.addHandler(self.gtklog)

        # process any delayed logs
        from bleachbit.Log import DelayLog
        if isinstance(sys.stderr, DelayLog):
            for msg in sys.stderr.read():
                self.append_text(msg)
            # if stderr was redirected - keep redirecting it
            sys.stderr = self.gtklog

        if options.is_corrupt():
            logger.error(
                # TRANSLATORS: Error message shown in the log on the main window.
                # %s is the file path.
                _('Resetting the configuration file because it is corrupt: %s'),
                bleachbit.options_file)
            bleachbit.Options.init_configuration()

        GLib.idle_add(self.cb_refresh_operations)

        # Close the application when user presses CTRL+Q or CTRL+W.
        accel = Gtk.AccelGroup()
        self.add_accel_group(accel)
        key, mod = Gtk.accelerator_parse("<Control>Q")
        accel.connect(key, mod, Gtk.AccelFlags.VISIBLE, self.on_quit)
        key, mod = Gtk.accelerator_parse("<Control>W")
        accel.connect(key, mod, Gtk.AccelFlags.VISIBLE, self.on_quit)
        if IS_MAC:
            # Gtk.AccelGroup does not reliably fire for Cmd+Q on this
            # GTK/Quartz build (the key-press-event carries the correct
            # META_MASK|MOD2_MASK combination, but the accelerator group
            # never activates), so handle it directly instead.
            mac_cmd_q_mods = Gdk.ModifierType.META_MASK | Gdk.ModifierType.MOD2_MASK

            def _on_mac_key_press(_widget, event):
                if event.keyval == Gdk.KEY_q and event.state & mac_cmd_q_mods == mac_cmd_q_mods:
                    self.on_quit(None)
                    return True
                return False
            self.connect("key-press-event", _on_mac_key_press)

        # Enable the user to change font size with keyboard or mouse.
        gtk_font_name = None
        try:
            gtk_font_name = Gtk.Settings.get_default().get_property('gtk-font-name')
        except TypeError as e:
            logger.debug("Error getting font name from GTK settings: %s", e)
        self.font_size = get_font_size_from_name(gtk_font_name) or 12
        self.default_font_size = self.font_size
        self.textview.connect("scroll-event", self.on_scroll_event)
        self.connect("key-press-event", self.on_key_press_event)
        self._font_css_provider = None
        if options.has_option("window_font_size"):
            self.set_font_size(absolute_size=options.get("window_font_size"))
        bleachbit.log_startup_time('window init done')

    def populate_window(self):
        """Create the main application window"""
        screen = self.get_screen()
        display = screen.get_display()
        monitor = display.get_primary_monitor()
        if monitor is None:
            # See https://github.com/bleachbit/bleachbit/issues/1793
            if display.get_n_monitors() > 0:
                monitor = display.get_monitor(0)
        if monitor is None:
            self.set_default_size(800, 600)
        else:
            geometry = monitor.get_geometry()
            self.set_default_size(min(geometry.width, 800),
                                  min(geometry.height, 600))
        self.set_position(Gtk.WindowPosition.CENTER)
        self.connect("configure-event", self.on_configure_event)
        self.connect("window-state-event", self.on_window_state_event)
        self.connect("delete-event", self.on_delete_event)
        self.connect("show", self.on_show)

        if appicon_path and os.path.exists(appicon_path):
            self.set_icon_from_file(appicon_path)
        bleachbit.log_startup_time('icon set')

        # add headerbar
        self.headerbar = self.create_headerbar()
        self.set_titlebar(self.headerbar)
        bleachbit.log_startup_time('headerbar built')

        # split main window twice
        # A Gtk.Paned lets the user drag the divider between the cleaner
        # list and the log output.  Its position is remembered between
        # sessions (see on_paned_position_changed()).
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.paned.set_wide_handle(False)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, homogeneous=False)
        self.add(vbox)

        # add InfoBar for non-blocking messages
        self._build_infobar(vbox)

        # add summary bar showing how many operations are selected
        vbox.add(self.create_summary_bar())

        vbox.add(self.paned)

        # add operations to left
        operations = self.create_operations_box()
        self.paned.pack1(operations, resize=False, shrink=False)

        # create the right side of the window
        right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.progressbar = Gtk.ProgressBar()
        right_box.pack_start(self.progressbar, False, True, 0)

        # add output display on right
        self.textbuffer = Gtk.TextBuffer()
        swindow = Gtk.ScrolledWindow()
        swindow.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        swindow.set_property('expand', True)
        swindow.get_style_context().add_class('bb-log')
        self.textview = Gtk.TextView.new_with_buffer(self.textbuffer)
        self.textview.set_editable(False)
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD)
        swindow.add(self.textview)
        right_box.add(swindow)
        self.paned.pack2(right_box, resize=True, shrink=False)

        # restore the divider position from the previous session
        try:
            if options.has_option('window_paned_position'):
                self.paned.set_position(
                    int(options.get('window_paned_position')))
            else:
                self.paned.set_position(320)
        except (TypeError, ValueError):
            logger.warning('Invalid window_paned_position option')
        self.paned.connect('notify::position', self.on_paned_position_changed)

        # add markup tags
        tt = self.textbuffer.get_tag_table()

        style_operation = Gtk.TextTag.new('operation')
        style_operation.set_property('size-points', 14)
        style_operation.set_property('weight', 700)
        style_operation.set_property('pixels-above-lines', 10)
        style_operation.set_property('justification', Gtk.Justification.CENTER)
        tt.add(style_operation)

        style_description = Gtk.TextTag.new('description')
        style_description.set_property(
            'justification', Gtk.Justification.CENTER)
        tt.add(style_description)

        style_option_label = Gtk.TextTag.new('option_label')
        style_option_label.set_property('weight', 700)
        style_option_label.set_property('left-margin', 20)
        tt.add(style_option_label)

        style_operation = Gtk.TextTag.new('error')
        tt.add(style_operation)
        # This event fires when the theme changes, e.g., the system
        # theme changes or the application changes its style.
        self.textview.connect('style-updated', self._update_error_tag_color)
        self._update_error_tag_color()

        self.status_bar = Gtk.Statusbar()
        vbox.add(self.status_bar)
        # setup drag&drop
        self.setup_drag_n_drop()
        bleachbit.log_startup_time('widgets built')
        # done
        self.show_all()
        self.progressbar.hide()
        self.infobar.hide()
        bleachbit.log_startup_time('window shown')

    def _update_error_tag_color(self, *_args):
        """Ensure error messages stay high contrast in current theme"""
        if not self.textbuffer:
            return

        tag_table = self.textbuffer.get_tag_table()
        if not tag_table:
            return

        error_tag = tag_table.lookup('error')
        if not error_tag:
            return

        error_tag.set_property('foreground', '#b00000')
        self._error_tag_color = '#b00000'

    def _apply_brand_css(self):
        """Load the brand stylesheet (share/purgebit.css) for the whole app"""
        css_path = bleachbit.get_share_path('purgebit.css')
        if not css_path:
            return
        try:
            provider = Gtk.CssProvider()
            provider.load_from_path(css_path)
        except GLib.Error:
            logger.exception('Error loading brand stylesheet %s', css_path)
            return
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        self._brand_css_provider = provider
        logger.debug('loaded brand stylesheet %s', css_path)

    def create_summary_bar(self):
        """Create the summary bar shown above the cleaner list"""
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bar.get_style_context().add_class('bb-summary-bar')
        self._summary_label = Gtk.Label()
        self._summary_label.set_xalign(0.0)
        bar.add(self._summary_label)
        return bar

    def update_summary(self):
        """Show how many cleaning operations are selected"""
        if self._summary_label is None or self.tree_store is None:
            return
        try:
            model = self.tree_store.get_model()
        except AttributeError:
            return
        selected = 0
        total = 0
        for parent in _iter_rows(model):
            if model.iter_children(parent) is None:
                # a cleaner without options counts as one operation
                total += 1
                if model[parent][1]:
                    selected += 1
                continue
            for child in _iter_rows(model, parent):
                total += 1
                if model[child][1]:
                    selected += 1
        # TRANSLATORS: Summary above the list of cleaners.  %(selected)s is
        # the number of enabled cleaning options and %(total)s is the number
        # of cleaning options currently listed.
        self._summary_label.set_text(
            _('%(selected)s of %(total)s items selected')
            % {'selected': selected, 'total': total})

    def on_tree_row_changed(self, _model, _path, _iter):
        """Update the summary bar when a checkbox or row changes"""
        if self._summary_update_id is not None:
            return
        self._summary_update_id = GLib.idle_add(self._update_summary_idle)

    def _update_summary_idle(self):
        self._summary_update_id = None
        self.update_summary()
        return False

    def on_search_changed(self, entry):
        """Filter the cleaner list to match the search text"""
        if self.tree_store is None:
            return
        self._filter_text = entry.get_text()
        self.tree_store.set_search_text(self._filter_text)
        if self.view is not None:
            self.view.expand_all()
        self.update_summary()

    def on_paned_position_changed(self, paned, _pspec):
        """Remember the divider position between list and log output"""
        options.set('window_paned_position', paned.get_position())

    def _show_splash_screen(self):
        """Show the splash screen on Windows because startup may be slow"""
        if not IS_WINDOWS:
            return

        # Check if splash screen is forced via environment variable
        splash_delay = os.environ.get('BLEACHBIT_SPLASH_SCREEN_DELAY')
        if splash_delay is not None:
            # pylint: disable-next=possibly-used-before-assignment
            Windows.splash_thread.start()
            return

        font_conf_file = Windows.get_font_conf_file()
        if not os.path.exists(font_conf_file):
            logger.error('No fonts.conf file %s', font_conf_file)
            return

        has_cache = Windows.has_fontconfig_cache(font_conf_file)
        if not has_cache:
            Windows.splash_thread.start()

    def set_font_size(self, absolute_size=None, relative_size=None):
        """Set the font size of the entire application"""
        assert absolute_size is not None or relative_size is not None
        if absolute_size is None:
            absolute_size = self.font_size + relative_size
        absolute_size = max(5, min(25, absolute_size))
        self.font_size = absolute_size
        options.set("window_font_size", absolute_size)
        css = f"* {{ font-size: {absolute_size}pt; }}"
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode())

        # Remove any previous provider to avoid stacking rules.
        screen = self.get_screen()
        if self._font_css_provider is not None:
            Gtk.StyleContext.remove_provider_for_screen(
                screen, self._font_css_provider)

        # Add the new provider globally so it affects all widgets.
        Gtk.StyleContext.add_provider_for_screen(
            screen,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        self._font_css_provider = provider

    def on_key_press_event(self, _widget, event):
        """Handle key press events"""
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK

        if event.keyval == Gdk.KEY_F11 and not ctrl:
            is_fullscreen = self.get_window().get_state() & Gdk.WindowState.FULLSCREEN
            if is_fullscreen:
                self.unfullscreen()
            else:
                self.fullscreen()
            options.set("window_fullscreen", not is_fullscreen)
            return True
        if not ctrl:
            return False
        if event.keyval in (Gdk.KEY_plus, Gdk.KEY_KP_Add):
            self.set_font_size(relative_size=1)
            return True
        if event.keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
            self.set_font_size(relative_size=-1)
            return True
        if event.keyval == Gdk.KEY_0:
            self.set_font_size(absolute_size=self.default_font_size)
            return True

        return False

    def on_scroll_event(self, _widget, event):
        """Handle mouse scroll events

        The first smooth scroll event, whether up or down, has dy=0.
        """
        if not event.get_state() & Gdk.ModifierType.CONTROL_MASK:
            return False

        relative_size = 0
        if event.direction == Gdk.ScrollDirection.UP:
            logger.debug('scroll event ctrl + Gdk.ScrollDirection.UP')
            relative_size = 1
        elif event.direction == Gdk.ScrollDirection.DOWN:
            logger.debug('scroll event ctrl + Gdk.ScrollDirection.DOWN')
            relative_size = -1
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            try:
                finished, dx, dy = event.get_scroll_deltas()
            except TypeError as e:
                logger.warning("Could not unpack scroll deltas: %s", e)
                return False  # event not handled

            if dy < 0:
                relative_size = 1
            elif dy > 0:
                relative_size = -1
            else:
                logger.debug(
                    "Smooth scroll: finished=%s, dx=%s, dy=%s", finished, dx, dy)
        else:
            logger.debug(
                'scroll event ctrl + unknown direction %s', event.direction)

        if relative_size != 0:
            self.set_font_size(relative_size=relative_size)
            return True  # Event handled

        return False

    def on_quit(self, *_args):
        """Quit the application, used with CTRL+Q, CTRL+W, or Cmd+Q on macOS"""
        # Unlike closing via the window's own close button (which fires
        # delete-event -> on_delete_event() -> options.close()), calling
        # Gtk.main_quit() directly here never emits delete-event, so any
        # setting change still waiting on the delayed flush timer
        # (see Options.__schedule_flush) would otherwise be lost if the
        # process exits before that timer fires.
        options.commit()
        if Gtk.main_level() > 0:
            Gtk.main_quit()
        else:
            self.destroy()

    def _confirm_delete(self, mention_preview, shred_settings=False):
        if options.get("delete_confirmation") or not options.get('expert_mode'):
            return GuiBasic.delete_confirmation_dialog(self, mention_preview, shred_settings=shred_settings)
        return True

    def destroy(self, *_args):
        """Prevent textbuffer usage during UI destruction"""
        self._destroyed = True
        self.textbuffer = None
        super().destroy()

    def get_preferences_dialog(self):
        # Keep the dialog and its minidom/cookie deps off the startup path.
        from bleachbit.GuiPreferences import PreferencesDialog  # pylint: disable=import-outside-toplevel
        return PreferencesDialog(
            self,
            self.cb_refresh_operations)

    def show_preferences_dialog(self, page_name=None):
        """Show the preferences dialog.

        Args:
            page_name: The name of the page to open, or None for the default page.
        """
        pref = self.get_preferences_dialog()
        pref.run(page_name)
        self.update_log_level()

    def shred_paths(self, paths, shred_settings=False, should_clear_clipboard=False):
        """Shred file or folders

        This function has several uses:
        1. Shred files or folders from the application menu.
        2. Shred objects in the clipboard, trigger by application menu.
        3. Shred objects in the clipboard, trigger by pasting.
        4. Shred objects by drag and drop.
        5. Shred application settings and quit.
        6. Integration with the Windows Explorer context menu.

        When shred_settings=True, the caller checks if the user confirmed to delete,
        so return True or False, depending on the user's confirmation.

        Otherwise, return False to remove from idle queue.
        """
        # create a temporary cleaner object
        backends['_gui'] = Cleaner.create_simple_cleaner(paths)

        operations = {'_gui': ['files']}

        # If no confirmation is requested, skip the preview.
        if options.get("delete_confirmation"):
            self.preview_or_run_operations(False, operations)
            # Set the pending flag before the confirmation dialog because
            # the dialog runs a nested GTK main loop in which the preview
            # worker may finish and call worker_done().  If the flag is set
            # by then, worker_done() removes _gui from backends.  Otherwise
            # it is removed here or when the preview finishes.
            self._gui_cleaner_cleanup_pending = self.worker
            if not self._confirm_delete(False, shred_settings):
                # User dis-confirmed the deletion.
                return False
            # User confirmed.  If the preview already finished during the
            # confirmation dialog, worker_done() removed _gui from backends.
            # Re-create it so the real delete worker can use it.
            self._gui_cleaner_cleanup_pending = None
            if '_gui' not in backends:
                backends['_gui'] = Cleaner.create_simple_cleaner(paths)

        if should_clear_clipboard:
            clear_clipboard()

        # Either confirmation was not required or user approved, so
        # continue with deletion.
        self.preview_or_run_operations(True, operations)
        if shred_settings:
            return True

        if self._auto_exit:
            GLib.idle_add(self.close,
                          priority=GLib.PRIORITY_LOW)

        # Return False to remove from idle queue.
        return False

    def append_text(self, text, tag=None, __iter=None, scroll=True):
        """Add some text to the main log"""
        if threading.current_thread() is not threading.main_thread():
            # GTK isn't thread-safe, so push work from worker threads back
            # onto the main loop instead of touching the buffer directly.
            GLib.idle_add(self.append_text, text, tag, None, scroll)
            return
        if self.textbuffer is None:
            # textbuffer was destroyed.
            return
        if not __iter:
            __iter = self.textbuffer.get_end_iter()
        text = sanitize_surrogates(text)
        if tag:
            self.textbuffer.insert_with_tags_by_name(__iter, text, tag)
        else:
            self.textbuffer.insert(__iter, text)
        # Scroll to end.  If the command is run directly instead of
        # through the idle loop, it may only scroll most of the way
        # as seen on Ubuntu 9.04 with Italian and Spanish.
        if scroll:
            self._queue_scroll()

    def _queue_scroll(self):
        """Scroll the log to the end from the idle loop"""
        if self._scroll_pending:
            self._scroll_again = True
            return
        self._scroll_pending = True
        GLib.idle_add(self._scroll_to_end)

    def _scroll_to_end(self):
        """Scroll the log to the insert mark"""
        # While text keeps arriving, keep one scroll queued behind
        # whatever appends next, as one idle per line used to. Set the
        # flags first so a failed scroll cannot leave them stuck.
        again = self._scroll_again
        self._scroll_again = False
        self._scroll_pending = again
        if again:
            GLib.idle_add(self._scroll_to_end)
        if self.textbuffer is not None:
            self.textview.scroll_mark_onscreen(self.textbuffer.get_insert())
        return False

    def update_log_level(self):
        """This gets called when the log level might have changed via the preferences."""
        self.gtklog.update_log_level()

    def on_selection_changed(self, selection):
        """When the tree view selection changed"""
        model = self.view.get_model()
        selected_rows = selection.get_selected_rows()
        if not selected_rows[1]:  # empty
            # happens when searching in the tree view
            return
        paths = selected_rows[1][0]
        row = paths[0]
        name = model[row][0]
        cleaner_id = model[row][2]
        self.progressbar.hide()
        description = backends[cleaner_id].get_description()
        self.textbuffer.set_text("")
        self.append_text(name + "\n", 'operation', scroll=False)
        if not description:
            description = ""
        self.append_text(description + "\n\n\n", 'description', scroll=False)
        for (label, description) in backends[cleaner_id].get_option_descriptions():
            self.append_text(label, 'option_label', scroll=False)
            if description:
                self.append_text(': ', 'option_label', scroll=False)
                self.append_text(description, scroll=False)
            self.append_text("\n\n", scroll=False)

    def get_selected_operations(self):
        """Return a list of the IDs of the selected operations in the tree view"""
        model = self.tree_store.get_model()
        return [model[__iter][2]
                for __iter in _iter_rows(model) if model[__iter][1]]

    def get_operation_options(self, operation):
        """For the given operation ID, return a list of the selected option IDs."""
        model = self.tree_store.get_model()
        if model.get_iter_first() is None:
            return []
        for __iter in _iter_rows(model):
            if operation != model[__iter][2]:
                continue
            if not model.iter_children(__iter):
                return None
            # only the enabled options
            return [model[iterc][2]
                    for iterc in _iter_rows(model, __iter) if model[iterc][1]]
        return None

    def set_sensitive(self, is_sensitive):
        """Disable commands while an operation is running"""
        self.view.set_sensitive(is_sensitive)
        self.preview_button.set_sensitive(is_sensitive)
        self.run_button.set_sensitive(is_sensitive)
        self.stop_button.set_sensitive(not is_sensitive)

    def run_button_get_sensitive(self):
        """Return whether commands are enabled

        set_sensitive() leaves the window itself sensitive, so ask the button.
        """
        return self.run_button.get_sensitive()

    def run_operations(self, __widget):
        """Event when the 'delete' toolbar button is clicked."""
        # fixme: should present this dialog after finding operations

        # Disable delete confirmation message.
        # if the option is selected under preference.

        if self._confirm_delete(True):
            self.preview_or_run_operations(True)

    def _filter_operations_for_expert_mode(self, operations, really_delete):
        """Filter out options with warnings when expert mode is disabled.

        When cleaning (really_delete=True) without expert mode, options
        with warnings are removed and a log message is shown for each.
        Preview is always allowed.
        """
        if not really_delete or options.get('expert_mode'):
            return operations
        filtered = {}
        for cleaner_id, option_ids in operations.items():
            if option_ids is None:
                filtered[cleaner_id] = option_ids
                continue
            safe_options = []
            for option_id in option_ids:
                if not backends[cleaner_id].get_warning(option_id):
                    safe_options.append(option_id)
                    continue
                cleaner_name = backends[cleaner_id].get_name()
                option_name = option_id
                # Find the friendly option_name for option_id
                for (oid, oname) in backends[cleaner_id].get_options():
                    if oid == option_id:
                        option_name = oname
                        break
                self.append_text(
                    # TRANSLATORS: Error message shown when a cleaner option
                    # cannot be used because expert mode is disabled.
                    # %(cleaner)s is the cleaner name, %(option)s is the option name.
                    _("%(cleaner)s - %(option)s cannot be cleaned because expert mode is disabled.") % {
                        'cleaner': cleaner_name, 'option': option_name} + "\n",
                    'error')
            if safe_options:
                filtered[cleaner_id] = safe_options
        return filtered

    def preview_or_run_operations(self, really_delete, operations=None):
        """Preview operations or run operations (delete files)"""

        assert isinstance(really_delete, bool)
        from bleachbit import Worker
        self.start_time = None
        if not operations:
            operations = {
                operation: self.get_operation_options(operation)
                for operation in self.get_selected_operations()
            }
        assert isinstance(operations, dict)
        if not operations:  # empty
            self.show_infobar(
                # TRANSLATORS: Error message shown in the infobar when the user clicks
                # the preview or clean button without selecting any cleaner options.
                _("You must select an operation"),
                Gtk.MessageType.ERROR)
            return
        try:
            self.set_sensitive(False)
            self.textbuffer.set_text("")
            self.progressbar.show()
            operations = self._filter_operations_for_expert_mode(
                operations, really_delete)
            if not operations:
                self.set_sensitive(True)
                self.progressbar.hide()
                return
            self.worker = Worker.Worker(self, really_delete, operations)
        except Exception:
            logger.exception('Error in Worker()')
        else:
            self.start_time = time.time()
            worker = self.worker.run()
            GLib.idle_add(worker.__next__)

    def worker_done(self, worker, really_delete):
        """Callback for when Worker is done"""
        # Remove the temporary _gui cleaner used for shred-paths and
        # wipe-empty-space operations, so it does not leak into the tree
        # view on the next refresh. For confirmed deletes, keep it through
        # the preview so the real delete worker can use it. If confirmation
        # is canceled, remove it when that preview worker finishes.
        if really_delete or worker is self._gui_cleaner_cleanup_pending:
            backends.pop('_gui', None)
            self._gui_cleaner_cleanup_pending = None
        # TRANSLATORS: Status message shown on the progress bar and in a popup
        # notification.
        done_msg = _("Done.")
        if self.progressbar is not None:
            self.progressbar.set_text("")
            self.progressbar.set_fraction(1)
            self.progressbar.set_text(done_msg)
        # No scroll here: append_text() has queued one, and scrolling
        # before GTK lays out the new text makes it do that twice.
        self.set_sensitive(True)

        # Close the program after cleaning is completed.
        # if the option is selected under preference.

        if really_delete:
            if options.get("exit_done"):
                sys.exit()

        # notification for long-running process
        if self.start_time is None:
            return
        elapsed = time.time() - self.start_time
        logger.debug('elapsed time: %d seconds', elapsed)
        if elapsed < 10 or self.is_active():
            return
        notify(done_msg)

    def create_operations_box(self):
        """Create and return the operations box (which holds a tree view)"""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.get_style_context().add_class('bb-sidebar')

        # search entry to filter the list of cleaners
        self.search_entry = Gtk.SearchEntry()
        # TRANSLATORS: Placeholder of the search box above the cleaner list.
        self.search_entry.set_placeholder_text(_('Search cleaners'))
        self.search_entry.set_margin_top(6)
        self.search_entry.set_margin_bottom(6)
        self.search_entry.set_margin_start(8)
        self.search_entry.set_margin_end(8)
        self.search_entry.connect('search-changed', self.on_search_changed)
        box.pack_start(self.search_entry, False, False, 0)

        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled_window.set_overlay_scrolling(False)
        self.tree_store = TreeInfoModel()
        display = TreeDisplayModel()
        mdl = self.tree_store.get_model()
        self.view = display.make_view(
            mdl, self, self.context_menu_event)
        self.view.get_selection().connect("changed", self.on_selection_changed)
        scrollbar_width = scrolled_window.get_vscrollbar().get_preferred_width()[
            1]
        # avoid conflict with scrollbar
        self.view.set_margin_end(scrollbar_width)
        scrolled_window.add(self.view)
        box.pack_start(scrolled_window, True, True, 0)

        # keep the summary bar in sync with the checkboxes
        mdl.connect('row-changed', self.on_tree_row_changed)
        return box

    def cb_refresh_operations(self):
        """Callback to refresh the list of cleaners and header bar labels"""
        if getattr(self, '_destroyed', False) or self.in_destruction():
            return False
        bleachbit.log_startup_time('refresh started')
        # Only the newest registration may advance. A refresh can arrive
        # mid-way, e.g. from Preferences, and two would both fill backends.
        self._register_generation += 1
        generation = self._register_generation
        # In case language changed, update the header bar labels.
        self.update_headerbar_labels()
        # Is this the first time in this session?
        allow_local = True
        if not self.recognized_cleanerml and not self._auto_exit:
            from bleachbit import RecognizeCleanerML
            try:
                RecognizeCleanerML.RecognizeCleanerML(self)
            except Exception:
                logger.exception(
                    'Error recognizing CleanerML files, so they will not be loaded')
                allow_local = False
            else:
                self.recognized_cleanerml = True
        # reload cleaners from disk
        self.progressbar.show()
        rc = register_cleaners(self.update_progress_bar,
                               self.cb_register_cleaners_done,
                               allow_local=allow_local)

        def pump():
            if generation != self._register_generation:
                rc.close()
                return False
            return next(rc)

        GLib.idle_add(pump)
        return False

    def cb_register_cleaners_done(self):
        """Called from register_cleaners()"""
        bleachbit.log_startup_time('cleaners registered')
        self.progressbar.hide()
        # update tree view
        self.tree_store.refresh_rows()
        # expand tree view
        self.view.expand_all()
        # update the summary bar
        self.update_summary()
        bleachbit.log_startup_time('tree filled')

        if self._showed_startup_messages:
            # remove from idle loop (see GObject.idle_add)
            return False
        self._showed_startup_messages = True
        # Let the tree paint first: the checks can block on antivirus or a
        # domain controller. Not PRIORITY_LOW, where --exit queues quit().
        GLib.idle_add(self._show_startup_messages)
        return False

    def _show_startup_messages(self):
        """Show startup messages once the cleaner tree is on screen"""
        if self.textbuffer is None:
            # window was destroyed before this callback ran
            return False
        try:
            startup_msgs = get_startup_messages(self._auto_exit)
        except Exception:
            # report the problem and keep going
            logger.exception('Error getting startup messages')
            startup_msgs = []
        for (msg, is_error) in startup_msgs:
            self.append_text(msg + '\n', 'error' if is_error else None)

        bleachbit.log_startup_time('startup messages done')
        return False

    def cb_run_option(self, widget, really_delete, cleaner_id, option_id):
        """Callback from context menu to delete/preview a single option"""
        operations = {cleaner_id: [option_id]}

        # preview
        if not really_delete:
            self.preview_or_run_operations(False, operations)
            return

        # block cleaning of warning options without expert mode
        if not options.get('expert_mode') and backends[cleaner_id].get_warning(option_id):
            self.show_infobar(REQUIRES_EXPERT_MODE)
            return

        # delete
        if self._confirm_delete(False):
            self.preview_or_run_operations(True, operations)
            return

    def cb_stop_operations(self, __widget):
        """Callback to stop the preview/cleaning process"""
        self.worker.abort()

    def cb_manage_cookies(self, widget):
        """Callback to launch the preferences dialog with Cookies page"""
        self.show_preferences_dialog('cookies')

    def cb_manage_custom_paths(self, widget):
        """Callback to launch the preferences dialog with Custom page"""
        self.show_preferences_dialog('custom')

    def _option_has_cookie_command(self, cleaner_id, option_id):
        """Return True if the given option runs a cookie command."""
        cleaner = backends.get(cleaner_id)
        return bool(cleaner) and cleaner.has_action_key(option_id, 'cookie')

    def context_menu_event(self, treeview, event):
        """When user right clicks on the tree view"""
        if event.button != 3:
            return False
        pathinfo = treeview.get_path_at_pos(int(event.x), int(event.y))
        if not pathinfo:
            return False
        path, col, _cellx, _celly = pathinfo
        treeview.grab_focus()
        treeview.set_cursor(path, col, 0)
        # context menu applies only to children, not parents
        if len(path) != 2:
            return False
        # find the selected option
        model = treeview.get_model()
        option_id = model[path][2]
        cleaner_id = model[path[0]][2]
        # make a menu
        menu = Gtk.Menu()
        menu.connect('hide', lambda widget: widget.detach())
        preview_item = Gtk.MenuItem(label=PREVIEW_MSG)
        preview_item.connect('activate', self.cb_run_option,
                             False, cleaner_id, option_id)
        menu.append(preview_item)
        clean_item = Gtk.MenuItem(label=CLEAN_MSG)
        clean_item.connect('activate', self.cb_run_option,
                           True, cleaner_id, option_id)
        menu.append(clean_item)

        # Check if this option has a cookie command
        if self._option_has_cookie_command(cleaner_id, option_id):
            menu.append(Gtk.SeparatorMenuItem())
            cookie_item = Gtk.MenuItem(label=MANAGE_COOKIES_TO_KEEP)
            cookie_item.connect('activate', self.cb_manage_cookies)
            menu.append(cookie_item)

        # Check if this is the system.custom option
        if cleaner_id == 'system' and option_id == 'custom':
            menu.append(Gtk.SeparatorMenuItem())
            # TRANSLATORS: Context menu item in the tree view that opens the preferences dialog.
            # Preserve the ellipsis as literal Unicode (…) or as Unicode escape (\u2026).
            custom_paths_label = _("Manage custom paths\u2026")
            custom_paths_item = Gtk.MenuItem(label=custom_paths_label)
            custom_paths_item.connect('activate', self.cb_manage_custom_paths)
            menu.append(custom_paths_item)

        # show the context menu
        menu.attach_to_widget(treeview)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def setup_drag_n_drop(self):
        def cb_drag_data_received(widget, _context, _x, _y, data, info, _time):
            if info == 80:
                uris = data.get_uris()
                paths = FileUtilities.uris_to_paths(uris)
                self.shred_paths(paths)
            # GtkTextView installs its own ::drag-data-received handler that
            # calls gtk_drag_finish(FALSE) when the view is not editable. On
            # Wayland that tears down the data offer in addition to the
            # GTK_DEST_DEFAULT_DROP handler, so stop emission and let the
            # default DROP handler finalize the drag exactly once.
            widget.stop_emission('drag-data-received')

        def setup_widget(widget):
            widget.drag_dest_set(Gtk.DestDefaults.MOTION | Gtk.DestDefaults.HIGHLIGHT | Gtk.DestDefaults.DROP,
                                 [Gtk.TargetEntry.new("text/uri-list", 0, 80)], Gdk.DragAction.COPY)
            widget.connect('drag_data_received', cb_drag_data_received)

        setup_widget(self)
        setup_widget(self.textview)
        # The text view is not editable, so GtkTextView's own ::drag-motion
        # and ::drag-drop handlers reject drops (calling gtk_drag_finish(FALSE))
        # and, on Wayland, cancel the in-flight selection read with
        # "error reading selection buffer: Operation was cancelled". Returning
        # True stops those handlers via the boolean accumulator; the
        # GTK_DEST_DEFAULT_DROP handling then drives the data transfer.
        self.textview.connect('drag_motion', lambda *_: True)
        self.textview.connect('drag_drop', lambda *_: True)

    def update_progress_bar(self, status):
        """Callback to update the progress bar with number or text"""
        if self.progressbar is None:
            return
        if isinstance(status, float):
            self.progressbar.set_fraction(status)
        elif isinstance(status, str):
            self.progressbar.set_show_text(True)
            self.progressbar.set_text(status)
        else:
            raise RuntimeError('unexpected type: ' + str(type(status)))

    def update_item_size(self, option, option_id, bytes_removed):
        """Update size in tree control"""
        model = self.view.get_model()

        if model is None:
            # The tree view has been destroyed (e.g. window is closing).
            return

        text = FileUtilities.bytes_to_human(bytes_removed)
        if bytes_removed == 0:
            text = ""

        treepath = Gtk.TreePath(0)
        # get_iter() raises ValueError when the tree is empty
        try:
            model.get_iter(treepath)
        except ValueError:
            logger.warning(
                'ValueError in get_iter() when updating file size for tree path=%s', treepath)
            return
        for __iter in _iter_rows(model):
            if model[__iter][2] != option:
                continue
            if option_id == -1:
                model[__iter][3] = text
            else:
                for child in _iter_rows(model, __iter):
                    if model[child][2] == option_id:
                        model[child][3] = text

    def update_total_size(self, bytes_removed):
        """Callback to update the total size cleaned"""
        if self.status_bar is None:
            return
        context_id = self.status_bar.get_context_id('size')
        text = FileUtilities.bytes_to_human(bytes_removed)
        if bytes_removed == 0:
            text = ""
        self.status_bar.push(context_id, text)

    def update_headerbar_labels(self):
        """Update the labels and tooltips in the headerbar buttons"""
        # The hamburger menu is not just a label -- it's a whole
        # Gtk.Builder-loaded Gio.MenuModel that only picks up a
        # language change if explicitly reloaded (see the comment in
        # _reload_app_menu()).
        if hasattr(self, 'menu_button'):
            self._reload_app_menu()

        # Preview button
        self.preview_button.set_label(PREVIEW_MSG)
        self.preview_button.set_tooltip_text(
            # TRANSLATORS: Tooltip for the preview button on the headerbar.
            # 'Preview' is a verb, and 'selected operations' refers to
            # the cleaning options (e.g., Firefox - Cache).
            _("Preview files in the selected operations (without deleting any files)"))

        # Clean button
        self.run_button.set_label(CLEAN_MSG)
        self.run_button.set_tooltip_text(
            # TRANSLATORS: Tooltip for the clean button on the headerbar.
            # 'Clean' is a verb, and 'operations' are cleaning options (e.g.,
            #  Firefox - Cache).
            _("Clean files in the selected operations"))

        self.stop_button.set_label(ABORT_BUTTON_LABEL)
        self.stop_button.set_tooltip_text(
            # TRANSLATORS: Tooltip for the abort button on the headerbar,
            # and 'abort' ia a verb.
            _('Abort the preview or cleaning process'))

    def create_headerbar(self):
        """Create the headerbar"""
        hbar = Gtk.HeaderBar()
        hbar.props.show_close_button = True
        hbar.props.title = get_app_full_name()

        box = Gtk.Box()
        Gtk.StyleContext.add_class(box.get_style_context(), "linked")

        if IS_WINDOWS:
            icon_size = Gtk.IconSize.BUTTON
        else:
            icon_size = Gtk.IconSize.LARGE_TOOLBAR

        # create the preview button
        self.preview_button = Gtk.Button.new_from_icon_name(
            resolve_icon_name('edit-find'), icon_size)
        self.preview_button.set_always_show_image(True)
        self.preview_button.connect(
            'clicked', lambda *dummy: self.preview_or_run_operations(False))
        box.add(self.preview_button)

        # create the delete button
        self.run_button = Gtk.Button.new_from_icon_name(
            resolve_icon_name('edit-clear-all'), icon_size)
        self.run_button.set_always_show_image(True)
        self.run_button.connect("clicked", self.run_operations)
        box.add(self.run_button)

        # stop cleaning
        self.stop_button = Gtk.Button.new_from_icon_name(
            resolve_icon_name('process-stop'), icon_size)
        self.stop_button.set_always_show_image(True)
        self.stop_button.set_sensitive(False)
        self.stop_button.connect('clicked', self.cb_stop_operations)
        box.add(self.stop_button)

        hbar.pack_start(box)

        # Add hamburger menu on the right
        self.menu_button = Gtk.MenuButton()
        app_menu_path = bleachbit.get_share_path('app-menu.ui')
        if app_menu_path:
            icon = Gio.ThemedIcon(name="open-menu-symbolic")
            image = Gtk.Image.new_from_gicon(icon, Gtk.IconSize.BUTTON)
            self.menu_button.add(image)
            hbar.pack_end(self.menu_button)
        else:
            hbar.pack_end(Gtk.Label('error: app-menu.ui not found'))

        # Update all labels and tooltips
        self.update_headerbar_labels()
        return hbar

    def _reload_app_menu(self, app_menu_path=None):
        """(Re)load app-menu.ui into self.menu_button.

        Gtk.Builder translates the .ui file's own translatable strings
        via the C locale set by locale.setlocale() (see the comment in
        Language.setup_translation()), baked in at load time -- unlike
        the rest of the headerbar, which re-reads its labels through
        Python's own gettext calls on every call to
        update_headerbar_labels() and so picks up a later language
        change immediately, a Gtk.Builder-loaded menu stays frozen in
        whatever language was active the one time it was originally
        loaded unless it is explicitly reloaded like this.
        """
        # Keyed on setup_translation() runs rather than the language code:
        # a rerun with the same code can still change the C locale.
        generation = Language.translation_generation
        if generation == self._app_menu_generation:
            return
        if app_menu_path is None:
            app_menu_path = bleachbit.get_share_path('app-menu.ui')
        if not app_menu_path:
            return
        builder = Gtk.Builder()
        builder.add_from_file(app_menu_path)
        self.menu_button.set_menu_model(builder.get_object('app-menu'))
        self._app_menu_generation = generation

    def on_configure_event(self, _widget, _event):
        (x, y) = self.get_position()
        (width, height) = self.get_size()

        # fixup maximized window position:
        # on Windows if a window is maximized on a secondary monitor it is moved off the screen
        if IS_WINDOWS:
            window = self.get_window()
            if window.get_state() & Gdk.WindowState.MAXIMIZED != 0:
                g = get_window_info(self)
                if x < g.x or x >= g.x + g.width or y < g.y or y >= g.y + g.height:
                    logger.debug("Maximized window %s+%s: %s",
                                 (x, y), (width, height), str(g))
                    self.move(g.x, g.y)
                    return True

        # save window position and size
        options.set("window_x", x)
        options.set("window_y", y)
        options.set("window_width", width)
        options.set("window_height", height)
        return False

    def on_window_state_event(self, _widget, event):
        """Save window state

        GTK version 3.24.34 on Windows 11 behaves strangely:
        * It reports maximized only when application starts.
        * Later, it reports window is fullscreen when neither
          full screen nor maximized.

        Because of this issue, we check the tiling state.
        """
        tiling_states = (Gdk.WindowState.TILED |
                         Gdk.WindowState.TOP_TILED |
                         Gdk.WindowState.RIGHT_TILED |
                         Gdk.WindowState.BOTTOM_TILED |
                         Gdk.WindowState.LEFT_TILED)

        is_tiled = event.new_window_state & tiling_states != 0
        fullscreen = (event.new_window_state &
                      Gdk.WindowState.FULLSCREEN != 0) and not is_tiled
        options.set("window_fullscreen", fullscreen)
        maximized = event.new_window_state & Gdk.WindowState.MAXIMIZED != 0
        options.set("window_maximized", maximized)
        if IS_WINDOWS:
            logger.debug(
                'window state = %s, full screen = %s, maximized = %s', event.new_window_state, fullscreen, maximized)
        return False

    def on_delete_event(self, _widget, _event):
        # commit options to disk
        options.close()
        return False

    def on_show(self, _widget):
        """Handle the show event.

        The event is triggered when the window is first shown.
        It is not emitted when the window is moved or unminimized.
        """
        # "show" is RUN_FIRST, so the window is already realized and mapped
        bleachbit.log_startup_time('show handler')
        if IS_WINDOWS and Windows.splash_thread.is_alive():
            Windows.splash_thread.join(0)

        # restore window position, size and state
        if not options.get('remember_geometry'):
            return
        if options.has_option("window_x") and options.has_option("window_y") and \
           options.has_option("window_width") and options.has_option("window_height"):
            r = Gdk.Rectangle()
            (r.x, r.y) = (options.get("window_x"), options.get("window_y"))
            (r.width, r.height) = (options.get(
                "window_width"), options.get("window_height"))

            g = get_window_info(self)

            # only restore position and size if window left corner
            # is within the closest monitor
            if r.x >= g.x and r.x < g.x + g.width and \
               r.y >= g.y and r.y < g.y + g.height:
                logger.debug("closest monitor %s, prior window geometry = %s+%s",
                             str(g), (r.x, r.y), (r.width, r.height))
                self.move(r.x, r.y)
                self.resize(r.width, r.height)
        if options.get("window_fullscreen"):
            self.fullscreen()
            self.append_text(
                # TRANSLATORS: Hint shown when starting in fullscreen mode.
                _("Press F11 to exit fullscreen mode.") + '\n')
        elif options.get("window_maximized"):
            self.maximize()

    def check_orphaned_wipe_files(self):
        """Check for orphaned wipe files and offer to delete them.

        These files are created by wipe_path() to fill empty disk space."""
        # Scan off the main loop: a sleeping drive can block it for seconds.
        # Read the option here, since Options.get_list() takes no lock.
        shred_drives = options.get_list('shred_drives')

        def _worker():
            try:
                orphaned_files = detect_orphaned_wipe_files(shred_drives)
            except Exception:
                logger.exception('Error detecting orphaned wipe files')
                return
            if orphaned_files:
                GLib.idle_add(self._prompt_orphaned_wipe_files, orphaned_files)

        threading.Thread(target=_worker, daemon=True).start()
        return False

    def _prompt_orphaned_wipe_files(self, orphaned_files):
        """Ask whether to preview orphaned wipe files, on the main thread"""
        if self.textbuffer is None:
            # window was destroyed while the scan was running
            return False
        if not self.run_button_get_sensitive():
            # an operation is running, and shred_paths() would replace it
            logger.debug(
                'skipping orphaned wipe file prompt: operation running')
            return False

        # TRANSLATORS: This message is shown when orphaned temporary files
        # from an interrupted disk wipe operation are detected.
        msg = _("PurgeBit detected leftover files from an interrupted "
                "disk wipe operation. Would you like to preview them with an option to delete them?")

        # TRANSLATORS: Title of confirmation dialog for deleting orphaned wipe files.
        title = _("Confirm")
        resp = GuiBasic.message_dialog(self,
                                       msg,
                                       Gtk.MessageType.WARNING,
                                       Gtk.ButtonsType.YES_NO,
                                       title)

        if resp == Gtk.ResponseType.YES:
            self.shred_paths(orphaned_files)
        return False
