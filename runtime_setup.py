"""Compatibilidad local: Keras 2 y rutas Tcl explícitas para Windows."""
import os
import sys
import site
from pathlib import Path


def configure_runtime():
    project = Path(__file__).resolve().parent
    # El intérprete portable utiliza exclusivamente las dependencias del proyecto.
    packages = project / ".venv" / "Lib" / "site-packages"
    if packages.is_dir() and str(packages) not in sys.path:
        site.addsitedir(str(packages))
    # TensorFlow 2.15 ya incluye Keras 2; no requiere el paquete tf-keras.
    os.environ.pop("TF_USE_LEGACY_KERAS", None)
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")
    if sys.platform == "win32":
        base = Path(sys.base_prefix)
        for variable, directory in (("TCL_LIBRARY", "tcl8.6"), ("TK_LIBRARY", "tk8.6")):
            library = base / "tcl" / directory
            if library.is_dir():
                os.environ[variable] = "//?/" + library.as_posix()
