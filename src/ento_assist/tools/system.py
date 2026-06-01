# Copyright 2026 The ento-assist Authors
# SPDX-License-Identifier: MIT

"""System-level MCP tools for ento-assist.

Currently exposes a single tool, ``pick_file``, which opens a native
OS file-chooser dialog so the user can select a file interactively
instead of typing an absolute path.
"""

from __future__ import annotations


def register_system_tools(mcp) -> None:  # type: ignore[type-arg]
    """Register system/utility tools on the given FastMCP server instance."""

    from mcp.server.fastmcp import FastMCP  # noqa: F401 — imported for type context

    @mcp.tool(
        description=(
            "Open a native OS file-chooser dialog and return the absolute path of the "
            "file selected by the user. Call this whenever the user wants to provide a "
            "file but has not typed a path. Optionally restrict the dialog to specific "
            "file extensions (e.g. ['pdf'] or ['pdf', 'txt']). "
            "Raises an error if the user cancels without selecting a file."
        )
    )
    def util_pick_file(
        title: str = "Select a file",
        extensions: list[str] | None = None,
    ) -> str:
        """Open a native file-chooser dialog and return the selected path.

        Parameters
        ----------
        title:
            Window title shown on the dialog.
        extensions:
            Optional list of file extensions to filter (without leading dots),
            e.g. ``["pdf"]`` or ``["pdf", "txt"]``.  When *None* all files are
            shown.

        Returns
        -------
        str
            Absolute path to the selected file.

        Raises
        ------
        RuntimeError
            If the dialog cannot be opened (e.g. no display available).
        ValueError
            If the user closes the dialog without selecting a file.
        """
        try:
            import tkinter as tk
            from tkinter import filedialog
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "tkinter is not available in this Python installation. "
                "Install it via your system package manager (e.g. `brew install python-tk`)."
            ) from exc

        import sys

        if sys.platform == "darwin":
            # Use osascript on macOS to avoid the lingering Tk Dock entry / TclError window.
            import subprocess

            ext_list = extensions or []
            if ext_list:
                # Build AppleScript type list, e.g. {"PDF", "pdf"}
                types = ", ".join(f'"{e.lstrip(".").upper()}"' for e in ext_list)
                of_type_clause = f" of type {{{types}}}"
            else:
                of_type_clause = ""

            script = f'POSIX path of (choose file with prompt "{title}"{of_type_clause})'
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
            )
            path = result.stdout.strip()
            if not path:
                raise ValueError("No file was selected.")
            return path

        try:
            root = tk.Tk()
        except tk.TclError as exc:  # pragma: no cover
            raise RuntimeError(
                f"Could not open a file dialog (no display available?): {exc}"
            ) from exc

        root.withdraw()
        # Bring the dialog to the front of all windows (important on macOS).
        root.attributes("-topmost", True)
        root.update()

        if extensions:
            joined = " ".join(f"*.{ext.lstrip('.')}" for ext in extensions)
            label = "/".join(ext.upper() for ext in extensions) + " files"
            filetypes = [(label, joined), ("All files", "*")]
        else:
            filetypes = [("All files", "*")]

        path = filedialog.askopenfilename(
            title=title,
            filetypes=filetypes,
            parent=root,
        )
        root.destroy()

        if not path:
            raise ValueError("No file was selected.")

        return path
