"""Desktop entry point shared by source and installed packages."""

import sys


def main():
    if sys.platform == "win32":
        print("This release supports macOS/Linux. Safe file operations are not yet ported to Windows.", file=sys.stderr)
        return 1
    try:
        import tkinter as tk
        from .ui import AIFileSortApp
        root = tk.Tk()
    except ImportError as exc:
        print(f"Missing GUI dependency: {exc}. Install Python with Tkinter; see README.md.", file=sys.stderr)
        return 1
    except tk.TclError as exc:
        print(f"Cannot open a graphical session: {exc}. See README.md.", file=sys.stderr)
        return 1
    AIFileSortApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
