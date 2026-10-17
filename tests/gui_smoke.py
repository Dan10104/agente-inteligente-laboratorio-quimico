"""Prueba visible de la GUI real; no registra datos de prueba en el historial real."""
from pathlib import Path
import json
import tempfile
import time
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageGrab
import config
from services.history_service import HistoryService


def start_smoke(app):
    output = config.BASE_DIR / "logs" / "pruebas_gui"
    output.mkdir(parents=True, exist_ok=True)
    app._test_temp = tempfile.TemporaryDirectory(prefix="agente_gui_")
    root = Path(app._test_temp.name)
    app.history = HistoryService(root / "historial.csv")
    app._refresh_history()
    app.capture_dir = root / "captures"
    # Patrón diagnóstico RGB, no fotografía de un elemento ni evidencia de precisión.
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    image[:, :, 0] = np.linspace(0, 255, 640, dtype=np.uint8)
    image[:, :, 1] = np.linspace(0, 255, 480, dtype=np.uint8)[:, None]
    image[:, :, 2] = 100
    source = root / "prueba_áéñ.png"
    Image.fromarray(image).save(source)
    webp = root / "prueba.webp"
    Image.fromarray(image).save(webp)
    corrupt = root / "corrupta.png"
    corrupt.write_bytes(b"not a picture")
    report = {"tipo_imagen": "Patrón sintético para verificar ejecución; no mide precisión", "checks": [], "errors": []}
    app.test_exit_code = 1
    started = time.monotonic()
    state = {"step": 0}

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        report["checks"].append(name)

    def screenshot(name, window=None):
        app.update_idletasks()
        x, y = app.winfo_rootx(), app.winfo_rooty()
        try:
            ImageGrab.grab(window=(window or app).winfo_id()).save(output / name)
        except OSError as exc:
            report.setdefault("captura_ventana", []).append(str(exc))
        report.setdefault("geometrias", []).append({"ventana": app.geometry(),
            "video": [app.video_host.winfo_width(), app.video_host.winfo_height()],
            "panel": [app.result_panel.winfo_width(), app.result_panel.winfo_height()]})

    original_error = app._error
    def test_error(title, exc):
        app.last_error = str(exc)
        report.setdefault("errores_esperados", []).append(f"{title}: {exc}")
    app._error = test_error

    def finish(success):
        app._error = original_error
        app.test_exit_code = 0 if success else 1
        report["estado"] = "OK" if success else "ERROR"
        report["segundos"] = round(time.monotonic() - started, 2)
        (output / "resultado.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        app.close()

    def tick():
        try:
            if time.monotonic() - started > 180:
                raise TimeoutError("La prueba GUI superó 180 segundos")
            step = state["step"]
            if step == 0 and app.ready:
                check("Modelo real cargado en GUI", app.model.output_shape[-1] == len(app.model.labels) == 9)
                check("Modelo input shape", app.model.input_shape == (None, 224, 224, 3))
                app.geometry("1320x688")
                with patch("main.filedialog.askopenfilename", return_value=str(source)):
                    app.choose_image()
                state["step"] = 1
            elif step == 1 and app.last_decision is not None and app.pending is None:
                check("Selector de imagen Unicode y predict real", app.source == "image")
                check("Top 3 visibles", all(label.cget("text") != "—" for label, _, _ in app.top_rows))
                check("Controles y pie caben en 1366x768", app.footer.winfo_y() + app.footer.winfo_height() <= app.winfo_height())
                check("Salida real suma 1", np.isclose(app.last_decision.probabilities.sum(), 1))
                report["probabilidades_reales"] = app.last_decision.probabilities.tolist()
                path = app.capture()
                check("Captura solicitada desde control", path is not None and path.is_file())
                with Image.open(path) as captured:
                    check("Captura conserva dimensiones", captured.size == (640, 480))
                screenshot("interfaz.png")
                app.set_presentation(True)
                state["wait"] = time.monotonic()
                state["step"] = 2
            elif step == 2 and time.monotonic() - state["wait"] > 1:
                check("Presentación y controles técnicos ocultos", app.presenting and not app.technical.winfo_ismapped())
                check("Top 3 en presentación", app.top_rows[0][0].winfo_ismapped())
                screenshot("presentacion.png")
                app.event_generate("<Escape>")
                app.set_presentation(False)
                app.roi_var.set(False)
                app.settings_changed()
                state["step"] = 3
            elif step == 3 and app.last_decision is not None and app.pending is None:
                check("ROI desactivada y recalculada", not app.roi_var.get())
                check("Restauración de controles", not app.presenting and app.technical.winfo_ismapped())
                app.threshold_var.set(99)
                app.window_var.set(30)
                app.toggle_settings()
                app.settings_changed()
                app.load_image(webp)
                state["step"] = 4
            elif step == 4 and app.last_decision is not None and app.pending is None:
                check("WebP y ajustes de umbral/suavizado", app.decision_engine.smoothing_window == 30 and app.decision_engine.confidence_threshold == .99)
                check("Ajustes de voz visibles", app.voice_selector.winfo_ismapped() and app.voice_volume_slider.winfo_ismapped())
                check("Ajustes no reducen la cámara", app.video_host.winfo_height() >= 150)
                screenshot("ajustes_voz.png", app.settings_window)
                app.toggle_settings()
                # Fixture de presentación de ficha, NO reconocimiento del patrón.
                app.mode_var.set("Estudiante")
                app._render_education("Acetona")
                app.confidence_label.configure(text="—")
                app.confidence_caption.configure(text="  vista de ficha")
                app.status_label.configure(text="FICHA EDUCATIVA · PRUEBA DE INTERFAZ")
                check("Ficha educativa completa", "Usos generales" in app.info_text.get("1.0", "end"))
                report["ficha_educativa"] = "Vista de ficha con etiqueta de prueba; no es resultado de reconocimiento de la imagen sintética."
                state["step"] = 4.5
                state["wait"] = time.monotonic()
            elif step == 4.5 and time.monotonic() - state["wait"] > 0.8:
                screenshot("ficha_educativa.png")
                app.mode_var.set("Normal")
                app._show_decision(app.last_decision)
                app.load_image(corrupt)
                state["step"] = 5
            elif step == 5 and app.last_error is not None and app.pending is None:
                check("Imagen corrupta informa error sin cerrar", bool(report.get("errores_esperados")))
                app.stop_camera()
                app.start_camera()
                state["step"] = 6
                state["wait"] = time.monotonic()
            elif step == 6 and time.monotonic() - state["wait"] > 3:
                app.stop_camera()
                check("Botones detener/iniciar no bloquean GUI", app.winfo_exists())
                report["camara"] = "Activación real limitada por sandbox; no se declara verificada"
                check("CSV de prueba creado con cabecera", (root / "historial.csv").read_text(encoding="utf-8-sig").startswith("fecha,hora,clase,confianza,categoria"))
                app.load_image(source)
                state["step"] = 7
            elif step == 7 and app.last_decision is not None:
                check("Recuperación después de error y cámara", app.source == "image")
                finish(True)
                return
            app.after(180, tick)
        except Exception as exc:
            report["errors"].append(f"{type(exc).__name__}: {exc}")
            finish(False)
    app.after(100, tick)
