# Manual Duplex

Print double-sided on a printer that only prints one side.

1. Drop PDFs on the window (or right-click a PDF → Open with → Manual Duplex).
2. **Print front sides.**
3. Put the printed stack back into the paper tray.
4. **Print back sides.**

Each document is padded to an even number of pages, so several documents and copies share one stack without their pages getting mixed up.

## First-time setup

Click **Print test** once. It prints 3 numbered pages. Page 2 should end up on the back of page 1, the same way up:

- Page 2 is on the back of page 3: switch **Reverse back sides**.
- Page 2 is upside down: switch **Rotate back sides**.

The settings are remembered in `~/.config/manual-duplex/settings.json`.

## Command line

```sh
manual-duplex print a.pdf b.pdf          # fronts, wait for Enter, backs
manual-duplex fronts a.pdf b.pdf -n 2    # step 1 only; the job is remembered
manual-duplex backs                      # step 2, after the stack is back in the tray
manual-duplex status | cancel | printers
manual-duplex split a.pdf -o out/        # only write the two PDFs, don't print
manual-duplex config -p PRINTER --reverse --no-rotate
```

`fronts` and `backs` are separate commands so a script or agent can run step 1, wait for you to turn the stack, and then run step 2. The CLI and the window share the same saved settings. Running `manual-duplex` with no command, optionally followed by PDFs, opens the window.

## Install

```sh
./install.sh
```

This needs Python 3 with GTK 4 and libadwaita (PyGObject), `pypdf`, and CUPS (`lp`).

## License

MIT
