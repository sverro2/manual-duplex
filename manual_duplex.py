#!/usr/bin/env python3
"""Manual Duplex: print double-sided on a printer that can only print one side.

Pass 1 prints the front sides of every sheet. You put the printed stack back
into the paper tray, and pass 2 prints the back sides. Each document is padded
to an even number of pages, so several documents can share one stack.

Without a subcommand this opens the window; see `manual-duplex --help` for the
command line.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402
from pypdf import PdfReader, PdfWriter  # noqa: E402

APP_ID = "nl.audiospire.ManualDuplex"
HERE = Path(__file__).resolve().parent
TEST_PDF = HERE / "assets" / "test-page.pdf"
CONFIG_FILE = Path(GLib.get_user_config_dir()) / "manual-duplex" / "settings.json"
JOB_DIR = Path(GLib.get_user_cache_dir()) / "manual-duplex"
PENDING_FILE = JOB_DIR / "pending.json"
DEFAULTS = {"printer": None, "reverse_backs": True, "rotate_backs": False}
C_LOCALE = {**os.environ, "LC_ALL": "C"}


# --- PDF and printing -------------------------------------------------------


def open_pdf(path):
    reader = PdfReader(path)
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("the PDF is password protected")
    return reader


def build_job(paths, copies, reverse_backs, rotate_backs, out_dir):
    """Split documents into a front-sides PDF and a back-sides PDF.

    Returns (fronts_path, backs_path, sheet_count).
    """
    fronts, backs = PdfWriter(), PdfWriter()
    back_pages = []  # source page, or the front page whose size a blank back gets
    for path in paths:
        pages = list(open_pdf(path).pages)
        for _ in range(copies):
            for i in range(0, len(pages), 2):
                fronts.add_page(pages[i])
                if i + 1 < len(pages):
                    back_pages.append((pages[i + 1], None))
                else:
                    back_pages.append((None, pages[i]))

    if reverse_backs:
        back_pages.reverse()
    for page, blank_like in back_pages:
        if page is None:
            box = blank_like.mediabox
            backs.add_blank_page(box.width, box.height)
        else:
            added = backs.add_page(page)
            if rotate_backs:
                added.rotate(180)

    out_dir.mkdir(parents=True, exist_ok=True)
    fronts_path = out_dir / "1-front-sides.pdf"
    backs_path = out_dir / "2-back-sides.pdf"
    with open(fronts_path, "wb") as f:
        fronts.write(f)
    with open(backs_path, "wb") as f:
        backs.write(f)
    return fronts_path, backs_path, len(back_pages)


def list_printers():
    """Return (printer names, default printer or None)."""
    try:
        names = subprocess.run(
            ["lpstat", "-e"], capture_output=True, text=True, env=C_LOCALE
        ).stdout.split()
        default = subprocess.run(
            ["lpstat", "-d"], capture_output=True, text=True, env=C_LOCALE
        ).stdout
    except FileNotFoundError:
        return [], None
    default = default.split(":", 1)[1].strip() if ":" in default else None
    return names, default if default in names else None


def print_pdf(printer, path, title):
    result = subprocess.run(
        ["lp", "-d", printer, "-t", title, "-o", "sides=one-sided", str(path)],
        capture_output=True,
        text=True,
        env=C_LOCALE,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "lp failed")


def load_settings():
    try:
        return {**DEFAULTS, **json.loads(CONFIG_FILE.read_text())}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save_settings(settings):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(settings, indent=2))


# --- User interface ---------------------------------------------------------


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Manual Duplex")
        self.set_default_size(540, 720)
        self.settings = load_settings()
        self.documents = []  # (path, page count, row)
        self.job = None  # dict with the back-sides PDF while waiting for the flip

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        add_button = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add PDFs")
        add_button.connect("clicked", self.on_choose_files)
        header.pack_start(add_button)
        view.add_top_bar(header)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self.build_empty_page(), "empty")
        self.stack.add_named(self.build_main_page(), "main")
        self.stack.add_named(self.build_flip_page(), "flip")
        view.set_content(self.stack)
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self.on_drop)
        self.toasts.add_controller(drop)

        self.refresh()

    # Pages

    def build_empty_page(self):
        button = Gtk.Button(label="Choose PDFs", halign=Gtk.Align.CENTER)
        button.add_css_class("pill")
        button.add_css_class("suggested-action")
        button.connect("clicked", self.on_choose_files)
        return Adw.StatusPage(
            icon_name="printer-symbolic",
            title="Drop PDFs here",
            description="Every document is printed double-sided in two passes:\n"
            "first the fronts, then you put the stack back for the backs.",
            child=button,
        )

    def build_main_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(24)

        self.documents_group = Adw.PreferencesGroup(title="Documents")
        clear_button = Gtk.Button(label="Clear", valign=Gtk.Align.CENTER)
        clear_button.add_css_class("flat")
        clear_button.connect("clicked", lambda _: self.clear_documents())
        self.documents_group.set_header_suffix(clear_button)
        box.append(self.documents_group)

        group = Adw.PreferencesGroup(title="Printing")
        printers, default = list_printers()
        self.printers = printers
        self.printer_row = Adw.ComboRow(title="Printer", model=Gtk.StringList.new(printers))
        wanted = self.settings["printer"] or default
        if wanted in printers:
            self.printer_row.set_selected(printers.index(wanted))
        if not printers:
            self.printer_row.set_subtitle("No printers found")
            self.printer_row.set_sensitive(False)
        self.printer_row.connect("notify::selected", self.on_settings_changed)
        group.add(self.printer_row)

        self.copies_row = Adw.SpinRow.new_with_range(1, 99, 1)
        self.copies_row.set_title("Copies")
        self.copies_row.connect("notify::value", lambda *_: self.refresh())
        group.add(self.copies_row)
        box.append(group)

        group = Adw.PreferencesGroup(
            title="Printer setup",
            description="Depends on how your printer feeds paper. "
            "Print the test page once to find the right combination.",
        )
        self.reverse_row = Adw.SwitchRow(
            title="Reverse back sides",
            subtitle="Print the back sides last sheet first",
            active=self.settings["reverse_backs"],
        )
        self.reverse_row.connect("notify::active", self.on_settings_changed)
        group.add(self.reverse_row)
        self.rotate_row = Adw.SwitchRow(
            title="Rotate back sides",
            subtitle="Turn the back sides upside down",
            active=self.settings["rotate_backs"],
        )
        self.rotate_row.connect("notify::active", self.on_settings_changed)
        group.add(self.rotate_row)
        test_row = Adw.ActionRow(title="Test page", subtitle="3 pages, uses 2 sheets")
        self.test_button = Gtk.Button(label="Print test", valign=Gtk.Align.CENTER)
        self.test_button.connect("clicked", self.on_print_test)
        test_row.add_suffix(self.test_button)
        group.add(test_row)
        box.append(group)

        self.summary = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.summary.add_css_class("dim-label")
        box.append(self.summary)

        self.print_button = Gtk.Button(label="Print front sides", halign=Gtk.Align.CENTER)
        self.print_button.add_css_class("pill")
        self.print_button.add_css_class("suggested-action")
        self.print_button.connect("clicked", self.on_print_fronts)
        box.append(self.print_button)

        clamp = Adw.Clamp(maximum_size=600, child=box)
        return Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER)

    def build_flip_page(self):
        buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        cancel = Gtk.Button(label="Cancel")
        cancel.add_css_class("pill")
        cancel.connect("clicked", self.on_cancel)
        backs = Gtk.Button(label="Print back sides")
        backs.add_css_class("pill")
        backs.add_css_class("suggested-action")
        backs.connect("clicked", self.on_print_backs)
        buttons.append(cancel)
        buttons.append(backs)
        self.flip_page = Adw.StatusPage(
            icon_name="object-flip-vertical-symbolic", title="Now turn the stack", child=buttons
        )
        return self.flip_page

    # State

    def add_files(self, files):
        known = {path for path, _, _ in self.documents}
        for file in files:
            path = file.get_path()
            if not path or path in known:
                continue
            try:
                pages = len(open_pdf(path).pages)
            except Exception as error:
                self.toast(f"Can't read {Path(path).name}: {error}")
                continue
            row = Adw.ActionRow(
                title=GLib.markup_escape_text(Path(path).name),
                subtitle=f"{pages} page{'s' if pages != 1 else ''}",
            )
            remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
            remove.add_css_class("flat")
            remove.set_tooltip_text("Remove")
            remove.connect("clicked", lambda _, p=path: self.remove_document(p))
            row.add_suffix(remove)
            self.documents_group.add(row)
            self.documents.append((path, pages, row))
            known.add(path)
        self.refresh()

    def remove_document(self, path):
        for doc in self.documents:
            if doc[0] == path:
                self.documents_group.remove(doc[2])
                self.documents.remove(doc)
                break
        self.refresh()

    def clear_documents(self):
        for _, _, row in self.documents:
            self.documents_group.remove(row)
        self.documents.clear()
        self.refresh()

    def refresh(self):
        if self.job:
            self.stack.set_visible_child_name("flip")
            return
        self.stack.set_visible_child_name("main" if self.documents else "empty")
        copies = int(self.copies_row.get_value())
        sheets = copies * sum((pages + 1) // 2 for _, pages, _ in self.documents)
        count = len(self.documents)
        self.summary.set_label(
            f"{count} document{'s' if count != 1 else ''} · "
            f"{sheets} sheet{'s' if sheets != 1 else ''} of paper"
        )
        self.print_button.set_sensitive(bool(self.printers) and sheets > 0)
        self.test_button.set_sensitive(bool(self.printers) and TEST_PDF.exists())

    def selected_printer(self):
        index = self.printer_row.get_selected()
        return self.printers[index] if 0 <= index < len(self.printers) else None

    def toast(self, message):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(message), timeout=5))

    def error(self, heading, message):
        dialog = Adw.AlertDialog(heading=heading, body=message)
        dialog.add_response("ok", "OK")
        dialog.present(self)

    # Printing flow

    def start_job(self, paths, test=False):
        printer = self.selected_printer()
        copies = 1 if test else int(self.copies_row.get_value())
        try:
            fronts, backs, sheets = build_job(
                paths,
                copies,
                self.reverse_row.get_active(),
                self.rotate_row.get_active(),
                JOB_DIR,
            )
            print_pdf(printer, fronts, "Manual Duplex – front sides")
        except Exception as error:
            self.error("Printing failed", str(error))
            return
        self.job = {"printer": printer, "backs": backs, "sheets": sheets, "test": test}
        self.flip_page.set_description(
            f"1. Wait until all {sheets} sheet{'s' if sheets != 1 else ''} have been printed.\n"
            "2. Take the stack from the output tray without changing the order.\n"
            "3. Put it back into the paper tray so the blank sides get printed.\n\n"
            "Always turn the stack the same way. The test page tells you\n"
            "whether the printer setup matches the way you do it."
        )
        self.refresh()

    def on_print_fronts(self, _button):
        self.start_job([path for path, _, _ in self.documents])

    def on_print_test(self, _button):
        self.start_job([str(TEST_PDF)], test=True)

    def on_print_backs(self, _button):
        job = self.job
        try:
            print_pdf(job["printer"], job["backs"], "Manual Duplex – back sides")
        except Exception as error:
            self.error("Printing failed", str(error))
            return
        self.job = None
        if job["test"]:
            self.error(
                "Check the test page",
                "Page 2 should be on the back of page 1, the same way up.\n\n"
                "• Page 2 is on the back of page 3? Switch “Reverse back sides”.\n"
                "• Page 2 is upside down? Switch “Rotate back sides”.\n\n"
                "Change the setting and print the test again until it is right.",
            )
        else:
            self.clear_documents()
            self.toast(f"Done: {job['sheets']} sheets printed double-sided")
        self.refresh()

    def on_cancel(self, _button):
        self.job = None
        self.refresh()

    # Events

    def on_choose_files(self, _button):
        pdf_filter = Gtk.FileFilter(name="PDF documents")
        pdf_filter.add_mime_type("application/pdf")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(pdf_filter)
        dialog = Gtk.FileDialog(title="Choose PDFs", filters=filters)
        dialog.open_multiple(self, None, self.on_files_chosen)

    def on_files_chosen(self, dialog, result):
        try:
            files = dialog.open_multiple_finish(result)
        except GLib.Error:
            return  # dialog cancelled
        self.add_files([files.get_item(i) for i in range(files.get_n_items())])

    def on_drop(self, _target, value, _x, _y):
        self.add_files(value.get_files())
        return True

    def on_settings_changed(self, *_):
        self.settings.update(
            printer=self.selected_printer(),
            reverse_backs=self.reverse_row.get_active(),
            rotate_backs=self.rotate_row.get_active(),
        )
        save_settings(self.settings)


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)

    def window(self):
        return self.get_active_window() or Window(self)

    def do_activate(self):
        self.window().present()

    def do_open(self, files, _n_files, _hint):
        window = self.window()
        window.add_files(files)
        window.present()


# --- Command line -----------------------------------------------------------


def resolve_printer(requested, settings):
    printers, default = list_printers()
    printer = requested or settings["printer"] or default or (printers[0] if printers else None)
    if printer not in printers:
        raise SystemExit(f"error: unknown printer {printer!r} (available: {', '.join(printers) or 'none'})")
    return printer


def job_options(args, settings):
    reverse = settings["reverse_backs"] if args.reverse is None else args.reverse
    rotate = settings["rotate_backs"] if args.rotate is None else args.rotate
    return reverse, rotate


def cmd_printers(args):
    printers, default = list_printers()
    for name in printers:
        print(name + (" (default)" if name == default else ""))


def cmd_split(args):
    reverse, rotate = job_options(args, load_settings())
    fronts, backs, sheets = build_job(args.files, args.copies, reverse, rotate, Path(args.output))
    print(f"{sheets} sheets\nfronts: {fronts}\nbacks:  {backs}")


def cmd_fronts(args):
    settings = load_settings()
    printer = resolve_printer(args.printer, settings)
    reverse, rotate = job_options(args, settings)
    fronts, backs, sheets = build_job(args.files, args.copies, reverse, rotate, JOB_DIR)
    print_pdf(printer, fronts, "Manual Duplex – front sides")
    PENDING_FILE.write_text(json.dumps({"printer": printer, "backs": str(backs), "sheets": sheets}))
    print(f"Printing front sides of {sheets} sheets on {printer}.")
    print("When done, put the stack back in the paper tray and run: manual-duplex backs")


def cmd_backs(args):
    try:
        job = json.loads(PENDING_FILE.read_text())
    except (OSError, ValueError):
        raise SystemExit("error: no pending job; run `manual-duplex fronts FILE...` first")
    print_pdf(args.printer or job["printer"], job["backs"], "Manual Duplex – back sides")
    PENDING_FILE.unlink()
    print(f"Printing back sides of {job['sheets']} sheets.")


def cmd_print(args):
    cmd_fronts(args)
    try:
        input("\nPress Enter once the stack is back in the paper tray (Ctrl+C cancels)... ")
    except (KeyboardInterrupt, EOFError):
        PENDING_FILE.unlink(missing_ok=True)
        raise SystemExit("\ncancelled; back sides not printed")
    cmd_backs(args)


def cmd_status(args):
    try:
        job = json.loads(PENDING_FILE.read_text())
        print(f"Waiting for back sides: {job['sheets']} sheets on {job['printer']}")
    except (OSError, ValueError):
        print("No pending job")


def cmd_cancel(args):
    PENDING_FILE.unlink(missing_ok=True)
    print("Pending job cancelled")


def cmd_config(args):
    settings = load_settings()
    if args.printer is not None:
        settings["printer"] = args.printer
    if args.reverse is not None:
        settings["reverse_backs"] = args.reverse
    if args.rotate is not None:
        settings["rotate_backs"] = args.rotate
    save_settings(settings)
    print(json.dumps(settings, indent=2))


def cli(argv):
    parser = argparse.ArgumentParser(
        prog="manual-duplex",
        description="Print double-sided on a single-sided printer. "
        "Run without a command (optionally with PDFs) to open the window.",
    )
    commands = parser.add_subparsers(required=True, metavar="COMMAND")

    def job_parser(name, help, func, printer=True):
        sub = commands.add_parser(name, help=help)
        sub.add_argument("files", nargs="+", metavar="FILE", help="PDF files, in print order")
        sub.add_argument("-n", "--copies", type=int, default=1, help="copies of every document")
        if printer:
            sub.add_argument("-p", "--printer", help="CUPS printer (default: saved or system default)")
        sub.add_argument("--reverse", action=argparse.BooleanOptionalAction, help="reverse back sides (default: saved setting)")
        sub.add_argument("--rotate", action=argparse.BooleanOptionalAction, help="rotate back sides 180° (default: saved setting)")
        sub.set_defaults(func=func)
        return sub

    job_parser("print", "print fronts, wait for Enter, print backs", cmd_print)
    job_parser("fronts", "print front sides and remember the job for `backs`", cmd_fronts)
    sub = commands.add_parser("backs", help="print the back sides of the pending job")
    sub.add_argument("-p", "--printer", help="override the printer used for the fronts")
    sub.set_defaults(func=cmd_backs)
    commands.add_parser("status", help="show the pending job").set_defaults(func=cmd_status)
    commands.add_parser("cancel", help="forget the pending job").set_defaults(func=cmd_cancel)
    commands.add_parser("printers", help="list printers").set_defaults(func=cmd_printers)
    split = job_parser("split", "only write the front and back PDFs", cmd_split, printer=False)
    split.add_argument("-o", "--output", default=".", help="output directory (default: .)")
    sub = commands.add_parser("config", help="show or change saved settings")
    sub.add_argument("-p", "--printer")
    sub.add_argument("--reverse", action=argparse.BooleanOptionalAction)
    sub.add_argument("--rotate", action=argparse.BooleanOptionalAction)
    sub.set_defaults(func=cmd_config)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit(f"error: {error}")


CLI_COMMANDS = {"print", "fronts", "backs", "status", "cancel", "printers", "split", "config", "-h", "--help"}

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in CLI_COMMANDS:
        cli(sys.argv[1:])
    else:
        sys.exit(App().run(sys.argv))
