# Windows console launcher provenance

`t64.exe` is the unmodified Windows x64 console launcher from the official
[PyPA distlib 0.4.0 source](https://github.com/pypa/distlib/tree/0.4.0), commit
`aff2cbbc4e8a87b9440cb93472789a213813dbd6`.

| File | Official source | SHA-256 |
| --- | --- | --- |
| `t64.exe` (108,032 bytes) | [distlib/t64.exe](https://raw.githubusercontent.com/pypa/distlib/aff2cbbc4e8a87b9440cb93472789a213813dbd6/distlib/t64.exe) | `81a618f21cb87db9076134e70388b6e9cb7c2106739011b6a51772d22cae06b7` |
| `LICENSE.txt` | [LICENSE.txt](https://raw.githubusercontent.com/pypa/distlib/aff2cbbc4e8a87b9440cb93472789a213813dbd6/LICENSE.txt) | `808e10c8a6ab8deb149ff9b3fb19f447a808094606d712a9ca57fead3552599d` |

The copies were downloaded from the official repository and checked byte for
byte against distlib 0.4.0 bundled in the local pip 26.1.2 installation. No pip
installation, download, compiler or additional package is needed when Crew
installs its entry point. `LAUNCHER-LICENSE.txt` reproduces the launcher-specific
copyright and BSD license from the header of the official
[PC/launcher.c](https://raw.githubusercontent.com/pypa/distlib/aff2cbbc4e8a87b9440cb93472789a213813dbd6/PC/launcher.c).
The original launcher C source has SHA-256
`a0e60a3e9717a2e519120ae19d9c81564f934f38aa322062c26dc21e6c10c0fd`.

`crew/install.py` uses distlib's documented console-script layout: this native
stub, a UTF-8 shebang naming the already selected Python interpreter, and a ZIP
containing `__main__.py`. Crew's appended Python code runs `HOME/crew.py`, which
continues reading the selected source from `HOME/runtime.json`. The stub's code
is unchanged; generated archives use a fixed timestamp. The native launcher
inherits stdin, stdout and stderr and returns the child exit code. The existing
cmd and POSIX shell launchers remain available. No same-name PowerShell script
is installed, so Restricted execution policy does not block normal discovery.

PowerShell 5.1 has its own native argument binding limitations: embedded ASCII
double quotes, empty arguments, and trailing backslashes in space-containing
arguments can change before a native executable receives them. Crew cannot
reconstruct information already removed by that shell. Use `crew send --text-file`
for exact arbitrary message text on that shell. Modern pwsh's native argument
binding and direct native process calls preserve those arguments. The regression
suite records the legacy limitation and tests Restricted and Bypass policies
without changing any persistent execution policy or PATH setting.
