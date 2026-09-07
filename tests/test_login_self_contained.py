import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main


LOGIN_HTML = (ROOT / "static" / "login.html").read_text(encoding="utf-8")

# External asset references: src/href attributes (quoted, with or without ?v=/#) and @import url(...)
# The `(?<!\.)` guard skips JS property assignments like location.href='/' / el.src='...'.
_EXTERNAL_RE = re.compile(
    r'(?<!\.)(?:src|href)=["\']([^"\']+)["\']|@import\s+(?:url\()?["\']?([^"\')]+)["\']?\)?',
    re.IGNORECASE,
)


def _external_asset_paths(html):
    """Return the de-versioned paths (e.g. /static/css/x.css) referenced by the page."""
    paths = []
    for match in _EXTERNAL_RE.finditer(html):
        url = match.group(1) or match.group(2)
        if not url:
            continue
        path = url.split("?")[0].split("#")[0]
        if path:
            paths.append(path)
    return sorted(set(paths))


class LoginSelfContainedTests(unittest.TestCase):
    def test_login_page_assets_are_publicly_reachable(self):
        """Every asset login.html references must be anonymously reachable.

        Guards the regression class where an unauthenticated first-login loads
        login.html but its own CSS/JS return 401 (not being in the public
        allowlist), which leaves the --ui-* variables undefined and collapses the
        form styling (invisible inputs, unstyled button).
        """
        public_paths = main.AUTH_PUBLIC_PATHS
        public_prefixes = main.AUTH_PUBLIC_PREFIXES
        external = _external_asset_paths(LOGIN_HTML)
        for path in external:
            allow = path in public_paths or any(
                path.startswith(prefix) for prefix in public_prefixes
            )
            self.assertTrue(
                allow,
                f"login.html references {path!r}, which is not in AUTH_PUBLIC_PATHS/PREFIXES; "
                f"an anonymous first login would receive 401 for it.",
            )

    def test_login_page_styles_define_every_theme_var_they_use(self):
        """login.html is self-contained: every --ui-* var it consumes must be defined inline.

        Because the login page no longer links an external theme token stylesheet,
        any var used but not defined inline resolves to an undefined value, which
        again silently drops the input border/background and the button fill.
        """
        inline_styles = re.findall(r"<style[^>]*>(.*?)</style>", LOGIN_HTML, re.DOTALL)
        css = "\n".join(inline_styles)
        used = set(re.findall(r"var\(\s*(--[a-zA-Z0-9_-]+)", css))
        defined = set(re.findall(r"(--[a-zA-Z0-9_-]+)\s*:", css))
        missing = used - defined
        self.assertFalse(
            missing,
            f"login.html uses theme vars not defined inline: {sorted(missing)}",
        )


if __name__ == "__main__":
    unittest.main()
