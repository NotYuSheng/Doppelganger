"""ASCII startup banner: a parrot in a mirror (it mimics your voice; the
mirror is the doppelganger) beside the wordmark, in a gold-to-coral gradient.
Regenerate via demo/build_final.py. DOPPELGANGER_NO_BANNER=1 silences it."""

import os

_GRAD_TOP, _GRAD_BOTTOM = (255, 196, 84), (233, 84, 64)  # gold -> coral, top to bottom
_RESET = "\x1b[0m"

_BANNER = r"""
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⡀
⠀⠀⠀⠀⠀⠀⠀⠀⣀⣤⠶⠖⠛⠛⠋⠉⣿⣿⣿⣿⣷⣶⣤⣄⡀
⠀⠀⠀⠀⠀⣠⡶⠛⠉⠀⠀⠀⠀⠀⠀⠀⣿⣿⣿⣿⣿⣿⣿⣿⣿⣷⣄⡀       <C>██████╗  ██████╗ ██████╗ ██████╗ ███████╗██╗<R>
⠀⠀⠀⣠⡾⠋⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣆      <C>██╔══██╗██╔═══██╗██╔══██╗██╔══██╗██╔════╝██║<R>
⠀⠀⣴⢋⣤⣴⣶⣦⣤⡀⠀⠀⠀⠀⠀⠀⣿⣿⣿⣿⣿⣿⡿⠟⠋⠉⠉⠙⠻⢷⡀    <C>██║  ██║██║   ██║██████╔╝██████╔╝█████╗  ██║<R>
⠀⣸⣿⣿⣿⠿⠛⠻⣿⣿⣦⠀⠀⢀⣀⣤⣉⡙⠻⢿⣿⠏⠀⣠⠶⠚⠓⢶⣄⠀⠁    <C>██║  ██║██║   ██║██╔═══╝ ██╔═══╝ ██╔══╝  ██║<R>
⠀⣿⣿⣿⠁⠀⠸⠿⠊⣿⣿⠂⣴⠟⠁⠀⣿⣿⣷⣄⠙⠀⢸⠇⠀⠐⠿⠇⢹⡆     <C>██████╔╝╚██████╔╝██║     ██║     ███████╗███████╗<R>
⢸⣿⣿⣿⡀⠄⠀⠀⢀⣿⠇⣼⠃⠸⠁⠀⣿⣿⣄⣿⣇⠀⠸⣇⠠⠀⠀⠀⣼⠇     <C>╚═════╝  ╚═════╝ ╚═╝     ╚═╝     ╚══════╝╚══════╝<R>
⢸⡿⢿⣿⣿⣷⣶⣾⣿⣿⢰⡏⠀⠀⠀⠀⣿⣿⣿⣿⣿⡀⠀⠈⠛⠶⠶⠛⠁⠀⢠⡇   <C> ██████╗  █████╗ ███╗   ██╗ ██████╗ ███████╗██████╗<R>
⢸⡇⠈⠛⠿⣿⣿⣿⡿⠟⢸⣇⠀⠀⠀⠀⣿⣿⣿⣿⡿⠇⣀⡀⠀⠀⠀⢀⣠⣴⣿⡇   <C>██╔════╝ ██╔══██╗████╗  ██║██╔════╝ ██╔════╝██╔══██╗<R>
⢸⡇⠀⠀⠀⠀⠀⠀⠀⠀⢰⡌⢳⣄⠀⠀⣿⣿⣿⠋⣠⠀⣿⣿⣿⣿⣿⣿⣿⣿⣿⡇   <C>██║  ███╗███████║██╔██╗ ██║██║  ███╗█████╗  ██████╔╝<R>
⢸⡇⠀⠀⠀⠀⠀⠀⠀⠀⠈⢷⡀⢹⡆⠀⣿⣿⠃⣼⠏⣰⣿⣿⣿⣿⣿⣿⣿⣿⣿⡇   <C>██║   ██║██╔══██║██║╚██╗██║██║   ██║██╔══╝  ██╔══██╗<R>
⢸⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠳⣄⢻⡄⣿⡏⠐⢋⣴⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡇   <C>╚██████╔╝██║  ██║██║ ╚████║╚██████╔╝███████╗██║  ██║<R>
⢸⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⢷⡟⢠⣾⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡇   <C> ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═══╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝<R>
⢸⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣴⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡇
⢸⣇⣀⣀⣀⣀⣀⣀⣀⣀⣀⣀⣀⣀⣀⣀⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡇
⠈⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠁
"""


def _colour(i: int, n: int) -> str:
    """Truecolor escape for wordmark line ``i`` of ``n`` along the gradient."""
    t = i / max(1, n - 1)
    r, g, b = (round(a + (z - a) * t) for a, z in zip(_GRAD_TOP, _GRAD_BOTTOM))
    return f"\x1b[1;38;2;{r};{g};{b}m"


def print_banner() -> None:
    if os.environ.get("DOPPELGANGER_NO_BANNER"):
        return
    lines = _BANNER.split("\n")
    marked = [i for i, line in enumerate(lines) if "<C>" in line]
    for k, i in enumerate(marked):
        lines[i] = lines[i].replace("<C>", _colour(k, len(marked))).replace("<R>", _RESET)
    print("\n".join(lines) + "\n")
