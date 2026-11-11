import sys

import tempstore

from . import _selftest, _selftest_cleanup

tempstore.autosession()
if "--cleanup-test" in sys.argv:
    print(_selftest_cleanup())
else:
    print(_selftest()[0])
