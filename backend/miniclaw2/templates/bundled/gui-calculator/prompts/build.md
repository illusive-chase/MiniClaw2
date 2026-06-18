Build a simple desktop calculator at `calculator.py` in the project root.
Also create a `requirements.txt` in the project root.

Requirements for `calculator.py`:

- Uses PySide6 / Qt Widgets for the GUI. Do not use `tkinter` or
  `customtkinter`.
- `requirements.txt` must include a PySide6 dependency, for example
  `PySide6>=6.7,<7`.
- Importable: `python3 -c "import calculator"` must succeed without
  opening any windows, even before PySide6 is installed. Keep top-level
  code dependency-light: only standard-library imports and pure helper
  functions/classes. Import PySide6 and create the `QApplication` only
  inside a `main()` function or another function called from
  `if __name__ == "__main__":`.
- Running `python3 calculator.py` opens a window titled `Calculator`.
- If PySide6 is missing when the script is run, print a clear message
  telling the user to run `python3 -m pip install -r requirements.txt`
  and exit cleanly instead of showing a traceback.
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

After your work is done you will be followed by a human-interact review
agent. Put any verification notes the reviewer should see in your
`preview.json` summary or next_implications; do not write a separate
brief file.
