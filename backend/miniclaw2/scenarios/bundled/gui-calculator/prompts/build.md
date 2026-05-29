Build a simple desktop calculator at `calculator.py` in the project root.

Requirements for `calculator.py`:

- Uses only the Python standard library (`tkinter`).
- Importable: `python3 -c "import calculator"` must succeed without
  opening any windows. Wrap the `Tk()` instantiation and `mainloop()`
  call in an `if __name__ == "__main__":` block.
- Running `python3 calculator.py` opens a window titled `Calculator`.
- The window shows a display (label or read-only Entry) and buttons:
  digits `0`–`9`, the four operators `+`, `-`, `*`, `/`, an equals
  button `=`, and a clear button `C`.
- Clicking digits appends to the current input shown in the display.
- Clicking `=` evaluates the current expression and shows the result.
  Operator precedence is fine to delegate to `eval` if you keep the
  input restricted to digits / operators / `.` — otherwise implement
  it yourself, your call.
- Dividing by zero shows a visible error indicator (e.g. the literal
  text `Error` in the display). It must NOT raise a Python traceback
  in the terminal.
- `C` resets the display to an empty / zero state.
- Closing the window exits the process cleanly.

After your work is done you will be followed by a passive review gate
that the human will use to ratify the result. Write the review brief at
the path indicated in your output contract — be specific about how the
human should run the program and exactly what to click.
