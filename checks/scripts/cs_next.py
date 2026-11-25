"""Stand-in for the old single file, for the checks: every name is looked up in (and set on) the
package's modules, so `cs.SETTINGS_FILE = ...` and `cs._Demo._wait = ...` still reach the right place."""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from coolscan_app import app, keepawake, rolls, server, settings  # noqa: E402
from coolscan_app.imaging import colour, develop, dust, enhance, files  # noqa: E402
from coolscan_app.scanner import demo, driver, scanning  # noqa: E402

imaging = files  # the old name of imaging/files.py
PACKAGE = ROOT / "coolscan_app"
_MODULES = [app, server, scanning, rolls, settings, demo, driver, enhance, dust, colour, files, develop, keepawake]
try:
    from coolscan_app.imaging import autocrop  # noqa: E402
    _MODULES.append(autocrop)
except ImportError:
    autocrop = None
if app.HAVE_TESTMODE:  # test mode is optional (kept out of git)
    from coolscan_app.extras import testmode  # noqa: E402
    _MODULES.append(testmode)
else:
    testmode = None
if app.notifier is not None:  # notify.py is optional (kept out of git)
    _MODULES.append(app.notifier)


class _Shim(types.ModuleType):
    def __getattr__(self, name):
        for m in _MODULES:
            if name in vars(m):
                return getattr(m, name)
        raise AttributeError(name)

    def __setattr__(self, name, value):
        hit = False
        for m in _MODULES:
            if name in vars(m):
                setattr(m, name, value)
                hit = True
        if not hit:
            super().__setattr__(name, value)


# The older checks were written when Archive mode started off: they keep starting that way.
# (archive_default_test.py checks the real defaults.)
_real_defaults = settings.default_settings
app.default_settings = lambda: {**_real_defaults(), "archive": False, "archive_default": False}

sys.modules[__name__].__class__ = _Shim

if __name__ == "__main__":
    server.main()
