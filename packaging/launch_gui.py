import sys

from dj_director.app import main, main_gui

# Windowed executable: GUI by default, CLI sub-commands (e.g. "selftest --out x.txt") still work.
sys.exit(main(sys.argv[1:]) if len(sys.argv) > 1 else main_gui())
