"""Write ``email signature.html`` — the page somebody opens to copy their signature.

The signature itself is not written here. It comes from
``email_branding.signature_block``, the one module that decides what a Hilal Markets
email looks like, so the signature uses the same palette, the same two typefaces and the
same logo as every email the product sends.

What this script adds is the wrapper: the instructions, the Copy button, and the logo
turned into a ``data:`` URI so the file works with nothing online. It rebuilds the logo
pictures first, so the picture inside the page can never be an older logo than the site's.

    .venv/Scripts/python scripts/build_email_signature.py
    .venv/Scripts/python scripts/build_email_signature.py --name "Sara" --role "Analyst"
"""

from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_email_logo import main as build_pictures  # noqa: E402

from ai_market_monitor.services.email_branding import (  # noqa: E402
    APPLE,
    APPLE_DEEP,
    BODY_FONT,
    CANVAS,
    COPY,
    DISPLAY_FONT,
    HAIRLINE,
    HAIRLINE_STRONG,
    INK_STRONG,
    SIGNATURE_LOGO_PATH,
    SIGNATURE_WIDTH,
    SURFACE,
    signature_block,
)

STATIC = REPOSITORY / "src" / "ai_market_monitor" / "static"
LOGO = STATIC / SIGNATURE_LOGO_PATH.removeprefix("/static/")
TARGET = REPOSITORY / "email signature.html"

#: How much white space the preview box puts around the signature. The box is then
#: exactly as wide as the signature plus this on each side, so the hairlines inside it
#: reach both edges. A box wider than what it holds looks like something is missing.
PREVIEW_PADDING = 28

STEPS = (
    "Open <strong>Settings</strong> (the gear), then <strong>See all settings</strong>.",
    "Stay on the <strong>General</strong> tab and scroll down to "
    "<strong>Signature</strong>.",
    "Press <strong>Create new</strong>, give it a name, then click inside the big box.",
    "Paste with <strong>Ctrl + V</strong> (<strong>Cmd + V</strong> on a Mac).",
    "Scroll to the bottom of the page and press <strong>Save Changes</strong>.",
)

#: Copying the *selection* is what carries the logo across. Handing the clipboard the
#: markup as a string would paste the angle brackets themselves, and Gmail would show
#: the code instead of the signature.
SCRIPT = """
(() => {
  const button = document.getElementById("copy-signature");
  const block = document.getElementById("signature");
  const result = document.getElementById("copy-result");
  const DONE = "Copied. Now paste it into your mail settings.";

  const say = (message, good) => {
    result.textContent = message;
    result.style.color = good ? "%(good)s" : "%(plain)s";
  };

  /* A page opened straight from a file is not always granted the modern clipboard, so
   * `execCommand` stays as the fallback: it is old, and it is still the one thing every
   * browser agrees on for copying formatted content. */
  const copyBySelection = () => {
    const range = document.createRange();
    range.selectNodeContents(block);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    const copied = document.execCommand("copy");
    selection.removeAllRanges();
    return copied;
  };

  button.addEventListener("click", async () => {
    try {
      if (navigator.clipboard && window.ClipboardItem) {
        await navigator.clipboard.write([
          new ClipboardItem({
            "text/html": new Blob([block.innerHTML], { type: "text/html" }),
            "text/plain": new Blob([block.innerText], { type: "text/plain" }),
          }),
        ]);
        say(DONE, true);
        return;
      }
    } catch (error) {
      /* Falls through to the selection copy below. */
    }
    const copied = copyBySelection();
    say(
      copied ? DONE : "Could not copy. Select the box above and press Ctrl + C.",
      copied,
    );
  });
})();
"""


def data_uri() -> str:
    """The built logo, carried inside the page so nothing has to be online."""

    if not LOGO.is_file():
        raise SystemExit(
            f"{LOGO} is missing. Run: .venv/Scripts/python scripts/build_email_logo.py"
        )
    encoded = base64.b64encode(LOGO.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def page(*, name: str, role: str) -> str:
    """The whole file: instructions, the signature, and the button that copies it.

    Everything above the signature box is instructions. The button selects only what is
    inside the box, so the instructions can never end up in somebody's signature.
    """

    steps = "".join(
        f'<li style="margin:0 0 8px">{step}</li>' for step in STEPS
    )
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        "<title>Email signature — Hilal Markets</title>\n"
        "</head>\n"
        f'<body style="margin:0;padding:48px 20px;background:{CANVAS};'
        f'font-family:{BODY_FONT};color:{INK_STRONG}">\n'
        "\n"
        f'<main style="max-width:720px;margin:0 auto;padding:40px;background:{SURFACE};'
        f'border:1px solid {HAIRLINE};border-radius:24px">\n'
        "\n"
        f'  <h1 style="margin:0 0 8px;font-family:{DISPLAY_FONT};font-size:28px;'
        f'font-weight:500;letter-spacing:-.02em;line-height:1.2;color:{INK_STRONG}">'
        "Your email signature</h1>\n"
        f'  <p style="margin:0 0 28px;font-size:16px;line-height:1.65;color:{COPY}">\n'
        "    Copy it once and paste it into your mail settings. Every email you send "
        "then carries it.\n"
        "  </p>\n"
        "\n"
        f'  <p style="margin:0 0 10px;font-size:13px;font-weight:700;'
        f'letter-spacing:.06em;text-transform:uppercase;color:{COPY}">'
        "This is what people will see</p>\n"
        "\n"
        "  <!-- The Copy button selects only what is inside this box. -->\n"
        f'  <div id="signature" style="max-width:{SIGNATURE_WIDTH + PREVIEW_PADDING * 2}px;'
        f"padding:{PREVIEW_PADDING}px;background:{SURFACE};"
        f'border:1px solid {HAIRLINE_STRONG};border-radius:18px">\n'
        f"{signature_block(name=name, role=role, logo_src=data_uri())}\n"
        "  </div>\n"
        "\n"
        f'  <div style="margin:24px 0 0">\n'
        '    <button id="copy-signature" type="button"\n'
        f'            style="appearance:none;border:1px solid {APPLE};'
        f"border-radius:999px;background:{APPLE};color:{INK_STRONG};"
        f"font-family:{BODY_FONT};font-size:15px;font-weight:700;padding:14px 28px;"
        'cursor:pointer">\n'
        "      Copy signature\n"
        "    </button>\n"
        '    <span id="copy-result" role="status"\n'
        f'          style="margin-left:14px;font-size:14px;color:{COPY}"></span>\n'
        "  </div>\n"
        "\n"
        f'  <div style="margin:32px 0 0;padding:24px 0 0;border-top:1px solid {HAIRLINE}">\n'
        f'    <h2 style="margin:0 0 12px;font-family:{DISPLAY_FONT};font-size:18px;'
        f'font-weight:500;line-height:1.3;color:{INK_STRONG}">Then, in Gmail</h2>\n'
        f'    <ol style="margin:0;padding-left:20px;font-size:15px;line-height:1.7;'
        f'color:{COPY}">{steps}</ol>\n'
        f'    <p style="margin:16px 0 0;font-size:13px;line-height:1.7;color:{COPY}">\n'
        "      The logo is stored inside this file, so nothing needs to be online for "
        "this to work.\n"
        "      Gmail uploads it for you when you paste. Outlook and Apple Mail take the "
        "same paste.\n"
        "      If the button does not work, select everything inside the box above and "
        "copy it with Ctrl + C.\n"
        "    </p>\n"
        "  </div>\n"
        "\n"
        "</main>\n"
        "\n"
        f"<script>{SCRIPT % {'good': APPLE_DEEP, 'plain': INK_STRONG}}</script>\n"
        "\n"
        "</body>\n"
        "</html>\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="Amr", help="the person the signature is for")
    parser.add_argument("--role", default="Founder & CEO", help="their job title")
    parser.add_argument(
        "--keep-pictures",
        action="store_true",
        help="do not rebuild the logo pictures first",
    )
    arguments = parser.parse_args()

    if not arguments.keep_pictures:
        build_pictures()
    # Written with "\n" on every platform: this file is compared against what
    # `signature_block` renders, and Windows line endings would make that comparison
    # depend on which machine last ran the script.
    TARGET.write_text(
        page(name=arguments.name, role=arguments.role), encoding="utf-8", newline="\n"
    )
    print(
        f"{TARGET.relative_to(REPOSITORY)} written for {arguments.name} "
        f"({TARGET.stat().st_size} bytes, one picture)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
