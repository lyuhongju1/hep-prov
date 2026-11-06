"""HEP-Prov L2 capture: loaded automatically by every Python process started under ``hepprov exec``.

``hepprov exec`` prepends this directory to PYTHONPATH, so Python imports this
file at startup with no change to the agent's code. It records, per process:

- a ``process`` record (pid, argv, executable), so the recorder knows which
  Python processes were instrumented;
- a ``read`` record (absolute path, sha256 at read time) for every file read
  under the run root. Sources: the ``open`` audit event (builtins.open,
  io.open, os.open) plus wrappers for readers that bypass Python file objects
  (pyarrow.parquet.read_table, pandas.read_parquet/read_csv/read_json,
  numpy.load).

It never changes what is read or returned. Hashing errors are logged as such.
Self-contained on purpose: the child interpreter may not have hepprov installed.
Not covered: ``python -I``/``-E`` (PYTHONPATH ignored) and non-Python
binaries. The recorder reports those processes as uninstrumented.
"""

import os as _os
import sys as _sys


def _hepprov_install():
    import functools
    import hashlib
    import json
    import threading
    import uuid

    log_dir = _os.environ.get("HEPPROV_LOG_DIR")
    root = _os.environ.get("HEPPROV_ROOT")
    if not log_dir or not root:
        return
    root = _os.path.abspath(root)
    state = _os.path.join(root, ".hepprov")
    log_path = _os.path.join(log_dir, f"reads.{_os.getpid()}.{uuid.uuid4().hex[:8]}.jsonl")
    tls = threading.local()
    seen = {}
    lock = threading.Lock()
    _open = open

    def _write(rec):
        try:
            with _open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception:
            pass

    def _inside(ap):
        try:
            return _os.path.commonpath([ap, root]) == root and _os.path.commonpath([ap, state]) != state
        except ValueError:
            return False

    def _record(path, via):
        if getattr(tls, "busy", False):
            return
        try:
            p = _os.fspath(path)
        except TypeError:
            return
        if isinstance(p, bytes):
            try:
                p = p.decode()
            except Exception:
                return
        if not isinstance(p, str):
            return
        ap = _os.path.abspath(p)
        if not _inside(ap) or not _os.path.isfile(ap):
            return
        tls.busy = True
        try:
            st = _os.stat(ap)
            sig = (st.st_size, st.st_mtime_ns)
            with lock:
                if seen.get(ap) == sig:
                    return
                seen[ap] = sig
            rec = {"type": "read", "path": ap, "size": st.st_size, "via": via}
            try:
                h = hashlib.sha256()
                with _open(ap, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
                rec["sha256"] = h.hexdigest()
            except Exception as e:
                rec["sha256"] = None
                rec["error"] = repr(e)
            _write(rec)
        finally:
            tls.busy = False

    _WRITE_FLAGS = getattr(_os, "O_WRONLY", 1) | getattr(_os, "O_RDWR", 2)

    def _hook(event, args):
        if event != "open" or getattr(tls, "busy", False):
            return
        try:
            path, mode, flags = args
        except ValueError:
            return
        if isinstance(path, int):
            return
        if isinstance(mode, str):
            if any(c in mode for c in "wax+"):
                return
        elif isinstance(flags, int) and flags & _WRITE_FLAGS:
            return
        _record(path, "open")

    _sys.addaudithook(_hook)

    targets = {"pandas": ("read_parquet", "read_csv", "read_json"),
               "numpy": ("load", "loadtxt", "genfromtxt"),
               "pyarrow.parquet": ("read_table", "ParquetFile"),
               "uproot": ("open",)}

    def _wrap(mod, name, modname):
        fn = getattr(mod, name, None)
        if fn is None or getattr(fn, "_hepprov", False):
            return
        if isinstance(fn, type):  # classes (ParquetFile): wrap construction
            orig_init = fn.__init__
            if getattr(orig_init, "_hepprov", False):
                return

            @functools.wraps(orig_init)
            def init(self, source, *a, **k):
                _record(source, f"{modname}.{name}")
                return orig_init(self, source, *a, **k)
            init._hepprov = True
            try:
                fn.__init__ = init
            except (TypeError, AttributeError):
                pass
            return

        @functools.wraps(fn)
        def w(src, *a, **k):
            for s in (src if isinstance(src, (list, tuple)) else [src]):
                _record(s, f"{modname}.{name}")
            return fn(src, *a, **k)
        w._hepprov = True
        try:
            setattr(mod, name, w)
        except (TypeError, AttributeError):
            pass

    import importlib.abc
    import importlib.machinery
    import importlib.util
    machinery, util = importlib.machinery, importlib.util

    class _Finder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path, target=None):
            if fullname not in targets:
                return None
            _sys.meta_path.remove(self)
            try:
                spec = machinery.PathFinder.find_spec(fullname, path) or util.find_spec(fullname)
            finally:
                _sys.meta_path.insert(0, self)
            if spec is None or spec.loader is None or not hasattr(spec.loader, "exec_module"):
                return spec
            orig = spec.loader.exec_module

            def exec_module(module, _o=orig, _n=fullname):
                _o(module)
                for n in targets[_n]:
                    _wrap(module, n, _n)
            spec.loader.exec_module = exec_module
            return spec

    for modname, names in targets.items():
        mod = _sys.modules.get(modname)
        if mod is not None:
            for n in names:
                _wrap(mod, n, modname)
    _sys.meta_path.insert(0, _Finder())

    _write({"type": "process", "pid": _os.getpid(), "argv": list(_sys.argv) if hasattr(_sys, "argv") else [],
            "executable": _sys.executable, "version": _sys.version.split()[0]})


try:
    _hepprov_install()
except Exception:  # capture must never break the agent's program
    pass

# Chain to any sitecustomize this one shadows (e.g. a venv's).
_here = _os.path.dirname(_os.path.abspath(__file__))
_me = _sys.modules.get(__name__)
_saved = list(_sys.path)
try:
    _sys.path = [p for p in _sys.path if _os.path.abspath(p or ".") != _here]
    _sys.modules.pop("sitecustomize", None)
    import importlib as _il
    try:
        _il.import_module("sitecustomize")
    except Exception:
        pass
finally:
    _sys.path = _saved
    if _me is not None:
        _sys.modules["sitecustomize"] = _me
