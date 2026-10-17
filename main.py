"""Interfaz local. Todos los widgets se actualizan únicamente en el hilo Tk."""
from runtime_setup import configure_runtime
configure_runtime()

import logging
from logging.handlers import RotatingFileHandler
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import queue
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import config

config.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    handlers=[RotatingFileHandler(config.LOG_PATH, maxBytes=2_000_000,
                              backupCount=2, encoding="utf-8")])
log = logging.getLogger(__name__)

try:
    import customtkinter as ctk
    import cv2
    from PIL import Image, ImageDraw, ImageOps, ImageTk
    from services.model_service import ModelService, roi_bounds
    from services.camera_service import CameraService
    from services.decision_service import Decision, TemporalDecision, RECOGNIZED, UNKNOWN, UNCERTAIN, is_other_label
    from services.catalog_service import Catalog
    from services.history_service import HistoryService, capture_filename
    from services.voice_service import VoiceService
    from services.narration_service import NarrationGate
    from services.labels import normalize_label
except Exception as exc:
    log.exception("No se pudieron importar las dependencias")
    try:
        emergency = tk.Tk()
        emergency.withdraw()
        messagebox.showerror("Dependencias no disponibles", f"No se pudo iniciar el agente.\n{exc}\n\nEjecute DIAGNOSTICO.bat y revise README.md.")
        emergency.destroy()
    finally:
        raise SystemExit(1)

import theme
from theme import BG, PANEL, INNER, TEXT, MUTED, ACCENT, GREEN, YELLOW, RED
from ui_layout import UIController


@dataclass(frozen=True)
class ConfirmedDetection:
    label: str
    confidence: float
    top3: tuple
    timestamp: datetime
    category: str
    frame: Image.Image
    decision: Decision
    narration_text: str
    roi_enabled: bool


class LabApp(ctk.CTk):
    def __init__(self, *, history_path=config.HISTORY_PATH, capture_dir=config.CAPTURES_DIR,
                 ui_preferences_path=None):
        ctk.set_appearance_mode("light")
        super().__init__()
        # Unidades de diseño en píxeles: caben también en proyectores de 1366x768.
        dpi_scale = self._get_window_scaling()
        ctk.set_widget_scaling(1 / dpi_scale)
        ctk.set_window_scaling(1 / dpi_scale)
        self.title(config.APP_TITLE)
        width = min(1320, self.winfo_screenwidth() - 50)
        height = min(820, self.winfo_screenheight() - 80)
        self.geometry(f"{width}x{height}+{max(0, (self.winfo_screenwidth()-width)//2)}+{max(0, (self.winfo_screenheight()-height)//2)}")
        self.minsize(1000, 640)
        self.configure(fg_color=BG)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<F11>", lambda event: self.toggle_presentation())
        self.bind("<Escape>", lambda event: self.set_presentation(False))
        self.bind("<Configure>", self._resize)
        self.report_callback_exception = self._callback_error
        self.capture_dir = Path(capture_dir)
        self.catalog = Catalog.load(config.CATALOG_PATH)
        self.history = HistoryService(history_path, config.LOG_COOLDOWN_SECONDS)
        self.model = ModelService()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="inferencia")
        self.pending = self.executor.submit(self.model.load)
        self.pending_kind, self.pending_token = "load", 0
        self.camera = CameraService()
        self.ready = False
        self.closing = False
        self.camera_active = False
        self.presenting = False
        self.settings_open = False
        self.source = "none"
        self.source_token = 0
        self.last_sequence = -1
        self.inferred_sequence = -1
        self.camera_generation = -1
        self.camera_request = 0
        self.last_submit = 0.0
        self.current_rgb = None
        self.last_decision = None
        self.is_frozen = False
        self.confirmed_detection = None
        self.frozen_frame = None
        self.confirmation_voice_pending = False
        self.decision_engine = None
        self.display_image = None
        self.needs_render = True
        self.last_capture = None
        self.last_error = None
        self._photo_file = None
        self._image_path = None
        self.voice = VoiceService()
        self.narrator = NarrationGate(config.VOICE_COOLDOWN_SECONDS,
            stable_seconds=config.VOICE_STABLE_SECONDS, absence_seconds=config.VOICE_ABSENCE_SECONDS)
        self.voice_ready = False
        self.voice_busy = False
        self.voice_request = None
        self.voice_manual_active = False
        self.voice_suppressed_class = None
        self.voice_last_text = None
        self.voice_last_label = None
        self.voice_choices = {}
        self.all_voices = []
        self.selected_voice_ids = {"edge": config.EDGE_VOICE}
        self.voice_warning = ""
        self.current_education_label = None
        self._education_view = None
        self.mode_var = tk.StringVar(value="Normal")
        self.voice_volume_var = tk.DoubleVar(value=config.VOICE_VOLUME)
        self.roi_var = tk.BooleanVar(value=config.USE_CENTER_ROI)
        self.threshold_var = tk.DoubleVar(value=config.CONFIDENCE_THRESHOLD * 100)
        self.window_var = tk.IntVar(value=config.SMOOTHING_WINDOW)
        self._build()
        self.ui = UIController(self, ui_preferences_path)
        self._refresh_history()
        warnings = [w for w in (self.catalog.warning, self.history.warning) if w]
        if warnings:
            self._notice(" | ".join(warnings), YELLOW)
        self.camera.scan()
        self.after(config.RENDER_INTERVAL_MS, self._poll)

    def _label(self, master, text, size=14, color=TEXT, weight="normal", **kwargs):
        return ctk.CTkLabel(master, text=text, font=theme.font(size, weight),
                            text_color=color, **kwargs)

    def _button(self, master, text, command, primary=False, danger=False, **kwargs):
        return ctk.CTkButton(master, text=text, command=command, height=34,
                             corner_radius=8, font=theme.font(12, "bold"),
                             **theme.button_style(primary, danger), **kwargs)

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        header = self.header = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=12, border_width=1, border_color=theme.BORDER)
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(12, 10))
        header.grid_columnconfigure(1, weight=1)
        self.ui_settings_button = self._button(header, "⚙ AJUSTES", lambda: self.ui.open_settings(), width=105)
        self.ui_settings_button.grid(row=0, column=0, rowspan=2, padx=(12, 10), pady=9)
        self.app_title_label = self._label(header, config.APP_TITLE, 20, weight="bold", anchor="w", justify="left", wraplength=650)
        self.app_title_label.grid(row=0, column=1, sticky="sw", pady=(6, 0))
        self.app_subtitle_label = self._label(header, "Reconocimiento de materiales y productos mediante Machine Learning", 12, MUTED, height=20, justify="left", wraplength=650)
        self.app_subtitle_label.grid(row=1, column=1, sticky="nw", pady=(0, 6))
        self.presentation_button = self._button(header, "MODO PRESENTACIÓN  ·  F11", self.toggle_presentation, width=210)
        self.presentation_button.grid(row=0, column=2, rowspan=2, padx=(12, 8))
        self._button(header, "SALIR", self.close, width=70, danger=True).grid(row=0, column=3, rowspan=2, padx=(0, 16))

        self.workspace = tk.PanedWindow(self, orient="vertical", sashwidth=9,
            sashrelief="flat", bg=theme.resolve(theme.BORDER), bd=0, opaqueresize=True,
            sashcursor="sb_v_double_arrow")
        self.workspace.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 5))
        self.body = tk.PanedWindow(self.workspace, orient="horizontal", sashwidth=9,
            sashrelief="flat", bg=theme.resolve(theme.BORDER), bd=0, opaqueresize=True,
            sashcursor="sb_h_double_arrow")
        self.workspace.add(self.body, minsize=300, stretch="always")
        left = self.left_panel = ctk.CTkFrame(self.body, fg_color=PANEL,
            corner_radius=10, border_width=1, border_color=theme.BORDER)
        self.body.add(left, minsize=380, stretch="always")
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)
        self.camera_content = ctk.CTkScrollableFrame(left, fg_color=PANEL,
            corner_radius=0, scrollbar_button_color=theme.SCROLL_THUMB,
            scrollbar_button_hover_color=theme.SCROLL_HOVER)
        self.camera_content.grid(row=1, column=0, sticky="nsew", padx=3, pady=(0, 5))
        self.camera_content.grid_columnconfigure(0, weight=1)
        camera_header = self.camera_header = ctk.CTkFrame(left, fg_color="transparent")
        camera_header.grid(row=0, column=0, sticky="ew", padx=16, pady=(10, 4))
        camera_header.grid_columnconfigure(0, weight=1)
        self.source_label = self._label(camera_header, "CÁMARA / IMAGEN", 12, ACCENT, "bold", wraplength=300, justify="left")
        self.source_label.grid(row=0, column=0, sticky="w")
        self.fps_label = self._label(camera_header, "FPS  —", 12, MUTED)
        self.fps_label.grid(row=0, column=1, sticky="e")
        self.learning_controls = ctk.CTkFrame(self.camera_content, fg_color="transparent")
        self.learning_controls.grid(row=0, column=0, sticky="ew", padx=8, pady=(4, 6))
        self.mode_caption = self._label(self.learning_controls, "Modo", 12, MUTED)
        self.mode_caption.grid(row=0, column=0, padx=(0, 8))
        self.mode_selector = ctk.CTkSegmentedButton(self.learning_controls, values=["Normal", "Estudiante"],
            variable=self.mode_var, command=self._mode_changed, font=("Segoe UI", 12),
            selected_color=theme.SELECTED, selected_hover_color=theme.SELECTED_HOVER, height=28)
        self.mode_selector.grid(row=0, column=1, sticky="w")
        self.confirmation_hint = self._label(self.learning_controls,
            "La explicación comienza al confirmar", 12, MUTED)
        self.confirmation_hint.grid(row=0, column=2, sticky="e", padx=(10, 0))
        self.learning_controls.grid_columnconfigure(2, weight=1)
        self.video_host = ctk.CTkFrame(self.camera_content, fg_color=theme.VIDEO_BG, corner_radius=8, border_width=1, border_color=theme.BORDER)
        self.video_host.grid(row=1, column=0, sticky="nsew", padx=12, pady=4)
        self.video_host.grid_rowconfigure(0, weight=1)
        self.video_host.grid_columnconfigure(0, weight=1)
        self.video_host.grid_propagate(False)
        self.video_host.bind("<Configure>", lambda event: setattr(self, "needs_render", True))
        self.video_label = tk.Label(self.video_host, text="Cámara lista para iniciar\n\nTambién puede PROBAR IMAGEN",
                                    font=(theme.FONT, 16), fg=theme.resolve(theme.VIDEO_TEXT), bg=theme.resolve(theme.VIDEO_BG), borderwidth=0)
        self.video_label.grid(row=0, column=0, sticky="nsew")
        self.guide_label = self._label(self.camera_content, "Coloque el objeto dentro del recuadro", 12, MUTED)
        self.guide_label.grid(row=2, column=0, pady=(3, 5))
        self.controls = ctk.CTkFrame(self.camera_content, fg_color="transparent")
        self.controls.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 7))
        for col in range(4):
            self.controls.grid_columnconfigure(col, weight=1, uniform="buttons")
        self.start_button = self._button(self.controls, "INICIAR CÁMARA", self.start_camera, True)
        self.stop_button = self._button(self.controls, "DETENER CÁMARA", self.stop_camera)
        self.capture_button = self._button(self.controls, "CAPTURAR IMAGEN", self.capture)
        self.image_button = self._button(self.controls, "PROBAR IMAGEN", self.choose_image, True, state="disabled")
        for col, button in enumerate((self.start_button, self.stop_button, self.capture_button, self.image_button)):
            button.grid(row=0, column=col, sticky="ew", padx=3)

        # Fuera del área desplazable: accesible también en F11 y paneles pequeños.
        self.confirm_button = self._button(left, "CONGELAR Y CONFIRMAR",
            self.toggle_confirmation, primary=True, state="disabled")
        self.confirm_button.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))

        self.technical = ctk.CTkFrame(self.camera_content, fg_color=INNER, corner_radius=8)
        self.technical.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 12))
        self.technical.grid_columnconfigure(1, weight=1)
        self.camera_caption = self._label(self.technical, "Cámara", 12, MUTED)
        self.camera_caption.grid(row=0, column=0, padx=10, pady=7)
        self.camera_selector = ctk.CTkOptionMenu(self.technical, values=["Cámara 0"], width=120,
                                                 command=self._camera_changed, fg_color=theme.SECONDARY)
        self.camera_selector.grid(row=0, column=1, sticky="w")
        self.search_camera_button = self._button(self.technical, "BUSCAR", self.scan_cameras, width=75)
        self.search_camera_button.grid(row=0, column=2, padx=8)
        self.roi_check = ctk.CTkCheckBox(self.technical, text="Analizar zona central", variable=self.roi_var,
                                       command=self.settings_changed, font=("Segoe UI", 12), width=165)
        self.roi_check.grid(row=0, column=3, padx=(4, 10))
        self.functional_settings_button = self._button(self.technical, "AJUSTES", self.toggle_settings, width=76)
        self.functional_settings_button.grid(row=0, column=4, padx=(0, 8))
        self.settings_window = ctk.CTkToplevel(self)
        self.settings_window.withdraw()
        self.settings_window.title("Ajustes · Laboratorio")
        self.settings_window.geometry("600x350")
        self.settings_window.minsize(530, 330)
        self.settings_window.configure(fg_color=BG)
        self.settings_window.protocol("WM_DELETE_WINDOW", self.toggle_settings)
        self.settings_window.transient(self)
        self.settings_panel = ctk.CTkFrame(self.settings_window, fg_color=PANEL)
        self.settings_panel.pack(fill="both", expand=True, padx=14, pady=14)
        self.settings_panel.grid_columnconfigure(1, weight=1)
        self.threshold_label = self._label(self.settings_panel, "Confianza mín. 80 %", 12, MUTED)
        self.threshold_label.grid(row=0, column=0, padx=10, pady=(14, 5))
        self.threshold_slider = ctk.CTkSlider(self.settings_panel, from_=70, to=99, number_of_steps=29,
                                              variable=self.threshold_var, command=lambda _: self.settings_changed())
        self.threshold_slider.grid(row=0, column=1, sticky="ew", padx=12, pady=(14, 5))
        self.window_label = self._label(self.settings_panel, "Suavizado: 12", 12, MUTED)
        self.window_label.grid(row=1, column=0, padx=10, pady=(3, 10))
        self.window_slider = ctk.CTkSlider(self.settings_panel, from_=1, to=30, number_of_steps=29,
                                          variable=self.window_var, command=lambda _: self.settings_changed())
        self.window_slider.grid(row=1, column=1, sticky="ew", padx=12, pady=(3, 10))
        self.voice_settings = ctk.CTkFrame(self.settings_panel, fg_color="transparent")
        self.voice_settings.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 8))
        self.voice_settings.grid_columnconfigure(1, weight=1)
        self._label(self.voice_settings, "Motor de voz", 12, MUTED).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=3)
        self.voice_engine_selector = ctk.CTkOptionMenu(self.voice_settings,
            values=["Voz neuronal", "Voz local"], command=self._voice_engine_changed,
            font=("Segoe UI", 12), height=27, fg_color=theme.SECONDARY)
        self.voice_engine_selector.set("Voz local" if config.TTS_ENGINE == "sapi" else "Voz neuronal")
        self.voice_engine_selector.grid(row=0, column=1, columnspan=2, sticky="ew", pady=3)
        self._label(self.voice_settings, "Voz", 12, MUTED).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=3)
        self.voice_selector = ctk.CTkOptionMenu(self.voice_settings, values=["Detectando voces…"],
            command=self._voice_selected, font=("Segoe UI", 12), height=27, fg_color=theme.SECONDARY, state="disabled")
        self.voice_selector.grid(row=1, column=1, columnspan=2, sticky="ew", pady=3)
        self._label(self.voice_settings, "Velocidad", 12, MUTED).grid(row=2, column=0, sticky="w")
        self.voice_rate_selector = ctk.CTkSegmentedButton(self.voice_settings,
            values=["Lenta", "Normal", "Rápida"], command=self._voice_settings_changed, height=27,
            font=("Segoe UI", 12), selected_color=theme.SELECTED)
        self.voice_rate_selector.set("Normal")
        self.voice_rate_selector.grid(row=2, column=1, columnspan=2, sticky="w", pady=3)
        self.voice_volume_label = self._label(self.voice_settings, "Volumen 100 %", 12, MUTED)
        self.voice_volume_label.grid(row=3, column=0, sticky="w", padx=(0, 8))
        self.voice_volume_slider = ctk.CTkSlider(self.voice_settings, from_=0, to=100,
            number_of_steps=100, variable=self.voice_volume_var, command=self._voice_settings_changed)
        self.voice_volume_slider.grid(row=3, column=1, columnspan=2, sticky="ew", pady=(4, 2))
        self._label(self.settings_panel, "Los cambios se aplican al instante. La cámara continúa activa.",
            11, MUTED).grid(row=3, column=0, columnspan=2, padx=10, pady=(5, 8))

        self.result_panel = ctk.CTkFrame(self.body, fg_color=PANEL, corner_radius=12, border_width=1, border_color=theme.BORDER)
        self.body.add(self.result_panel, minsize=320, stretch="always")
        self.result_panel.grid_columnconfigure(0, weight=1)
        self.result_panel.grid_rowconfigure(1, weight=1)
        self.result_header = ctk.CTkFrame(self.result_panel, fg_color="transparent")
        self.result_header.grid(row=0, column=0, sticky="ew", padx=12, pady=(7, 2))
        self.result_header.grid_columnconfigure(0, weight=1)
        self.result_content = ctk.CTkScrollableFrame(self.result_panel, fg_color=PANEL, corner_radius=0,
            scrollbar_button_color=theme.SCROLL_THUMB, scrollbar_button_hover_color=theme.SCROLL_HOVER)
        self.result_content.grid(row=1, column=0, sticky="nsew", padx=3, pady=(0, 5))
        self.result_content.grid_columnconfigure(0, weight=1)
        self.status_label = self._label(self.result_header, "CARGANDO MODELO…", 12, YELLOW, "bold", anchor="w", height=18)
        self.status_label.grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=2)
        self.name_label = self._label(self.result_content, "Esperando una imagen", 27, weight="bold",
                                      anchor="w", justify="left", wraplength=420)
        self.name_label.grid(row=1, column=0, sticky="ew", padx=20, pady=(2, 4))
        confidence_box = self.confidence_box = ctk.CTkFrame(self.result_content, fg_color="transparent")
        confidence_box.grid(row=2, column=0, sticky="ew", padx=20)
        confidence_box.grid_columnconfigure(1, weight=1)
        self.confidence_label = self._label(confidence_box, "—", 34, ACCENT, "bold")
        self.confidence_label.grid(row=0, column=0, sticky="w")
        self.confidence_caption = self._label(confidence_box, "  confianza", 13, MUTED)
        self.confidence_caption.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.confidence_bar = ctk.CTkProgressBar(confidence_box, height=8, corner_radius=4,
            progress_color=GREEN, fg_color=theme.TRACK, width=130)
        self.confidence_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 7))
        self.confidence_bar.set(0)
        self.category_label = self._label(self.result_content, "Información del elemento", 13, MUTED,
                                          anchor="w", justify="left", wraplength=420, height=20)
        self.category_label.grid(row=3, column=0, sticky="ew", padx=20, pady=(2, 4))
        self.info_text = ctk.CTkTextbox(self.result_content, fg_color=INNER, corner_radius=8,
                                       font=(theme.FONT, 13), text_color=TEXT, height=75, wrap="word",
                                       border_width=1, border_color=theme.BORDER,
                                       scrollbar_button_color=theme.SCROLL_THUMB, scrollbar_button_hover_color=theme.SCROLL_HOVER)
        self.info_text.grid(row=4, column=0, sticky="nsew", padx=16, pady=(0, 7))
        self._set_info("Centre un solo objeto, use buena iluminación y un fondo con contraste.\n\nLa clase y la confianza procederán del modelo entrenado.")
        self.voice_controls = ctk.CTkFrame(self.result_content, fg_color="transparent")
        self.voice_controls.grid(row=5, column=0, sticky="ew", padx=16, pady=(0, 2))
        self.voice_controls.grid_columnconfigure(0, weight=1)
        self.listen_button = self._button(self.voice_controls, "🔊 ESCUCHAR EXPLICACIÓN", self.listen_explanation,
            primary=True, state="disabled", width=205)
        self.listen_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.voice_stop_button = self._button(self.voice_controls, "⏹ DETENER VOZ", self.stop_voice, width=114, danger=True)
        self.voice_stop_button.grid(row=0, column=1, padx=(0, 4))
        self.repeat_button = self._button(self.voice_controls, "🔁 REPETIR", self.repeat_explanation, width=88, state="disabled")
        self.repeat_button.grid(row=0, column=2)
        self.voice_status = self._label(self.result_content, "Voz: preparando voces españolas…", 11, MUTED, anchor="w",
            wraplength=430, justify="left", height=20)
        self.voice_status.grid(row=6, column=0, sticky="ew", padx=20, pady=(0, 2))
        self.top_header = ctk.CTkFrame(self.result_content, fg_color="transparent")
        self.top_header.grid(row=7, column=0, sticky="ew", padx=12, pady=(3, 2))
        self.top_header.grid_columnconfigure(0, weight=1)
        self.top_title = self._label(self.top_header, "TOP 3 · SALIDAS DEL MODELO", 11, MUTED, "bold", anchor="w", height=18)
        self.top_title.grid(row=0, column=0, sticky="ew")
        self.top_container = ctk.CTkFrame(self.result_content, fg_color="transparent")
        self.top_container.grid(row=8, column=0, sticky="ew")
        self.top_container.grid_columnconfigure(0, weight=1)
        self.top_rows = []
        for i in range(3):
            row = ctk.CTkFrame(self.top_container, fg_color=INNER, corner_radius=5)
            row.grid(row=i, column=0, sticky="ew", padx=16, pady=(0, 3))
            row.grid_columnconfigure(1, weight=1)
            self._label(row, f"{i+1:02}", 11, MUTED, "bold", width=25).grid(row=0, column=0, rowspan=2, padx=(4, 5))
            label = self._label(row, "—", 12, TEXT, anchor="w", justify="left", wraplength=320, height=19)
            label.grid(row=0, column=1, sticky="w")
            value = self._label(row, "—", 12, MUTED, anchor="e", width=65, height=19)
            value.grid(row=0, column=2, sticky="e", padx=(3, 7))
            bar = ctk.CTkProgressBar(row, height=5, progress_color=ACCENT, fg_color=theme.TRACK)
            bar.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(0, 8), pady=(2, 4))
            bar.set(0)
            self.top_rows.append((label, value, bar))
        self.evidence_label = self._label(self.result_content, "Sin predicciones todavía", 11, MUTED, anchor="w", height=18)
        self.evidence_label.grid(row=11, column=0, sticky="ew", padx=20, pady=(1, 5))

        self.history_panel = ctk.CTkFrame(self.workspace, fg_color=PANEL, corner_radius=10, border_width=1, border_color=theme.BORDER)
        self.workspace.add(self.history_panel, minsize=100, stretch="never", height=130)
        self.history_panel.grid_rowconfigure(2, weight=1)
        self.history_header = ctk.CTkFrame(self.history_panel, fg_color="transparent")
        self.history_header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(4, 0))
        self.history_header.grid_columnconfigure(0, weight=1)
        self.history_title = self._label(self.history_header, "HISTORIAL", 12, ACCENT, "bold", anchor="w")
        self.history_title.grid(row=0, column=0, sticky="w")
        self.history_panel.grid_columnconfigure(0, weight=1)
        self.session_label = self._label(self.history_panel, "HISTORIAL", 12, MUTED, anchor="w", height=20)
        self.session_label.grid(row=1, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 2))
        style = ttk.Style(self)
        self.tree = ttk.Treeview(self.history_panel, columns=("hora", "clase", "confianza", "categoria"),
                                 show="headings", height=2, style="Lab.Treeview")
        theme.style_history(style, self.tree)
        for key, title, width in (("hora", "HORA", 100), ("clase", "ELEMENTO", 480),
                                  ("confianza", "CONFIANZA", 100), ("categoria", "CATEGORÍA", 270)):
            self.tree.heading(key, text=title, anchor="center" if key in ("hora", "confianza") else "w")
            self.tree.column(key, width=width, minwidth=70, stretch=key in ("clase", "categoria"),
                             anchor="center" if key in ("hora", "confianza") else "w")
        self.tree.grid(row=2, column=0, sticky="nsew", padx=(12, 0), pady=(0, 8))
        scroll = self.history_scroll = ttk.Scrollbar(self.history_panel, orient="vertical", command=self.tree.yview, style="Lab.Vertical.TScrollbar")
        scroll.grid(row=2, column=1, sticky="ns", padx=(0, 8), pady=(0, 8))
        self.tree.configure(yscrollcommand=scroll.set)
        self.history_hscroll = ttk.Scrollbar(self.history_panel, orient="horizontal", command=self.tree.xview,
                                           style="Lab.Horizontal.TScrollbar")
        self.history_hscroll.grid(row=3, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 5))
        self.tree.configure(xscrollcommand=self.history_hscroll.set)
        self.footer = ctk.CTkFrame(self, fg_color="transparent")
        self.footer.grid(row=3, column=0, sticky="ew", padx=22, pady=(0, 7))
        self.footer.grid_columnconfigure(0, weight=1)
        self.health_label = self._label(self.footer, "Modelo: cargando  ·  Cámara: buscando", 11, MUTED, anchor="w", height=20)
        self.health_label.grid(row=0, column=0, sticky="w")
        self.notice_label = self._label(self.footer, "Procesamiento local · CPU", 11, MUTED, anchor="e", height=20)
        self.notice_label.grid(row=0, column=1, sticky="e")
        self.scientific_label = self._label(self.footer, config.SCIENTIFIC_NOTICE, 11, MUTED, anchor="w", justify="left", height=20)
        self.scientific_label.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        theme.style_controls(self)

    def _set_info(self, text):
        self.info_text.configure(state="normal")
        self.info_text.delete("1.0", "end")
        self.info_text.insert("1.0", text)
        self.info_text.configure(state="disabled")

    def _notice(self, text, color=MUTED):
        self.notice_label.configure(text=text, text_color=color)

    def _resize(self, event):
        if event.widget is self:
            self.needs_render = True
            if hasattr(self, "ui"):
                self.ui.schedule_reflow()

    def _clear_confirmation(self):
        self.is_frozen = False
        self.confirmed_detection = None
        self.frozen_frame = None
        self.confirmation_voice_pending = False
        self.confirm_button.configure(text="CONGELAR Y CONFIRMAR", state="disabled")

    def _can_confirm(self):
        decision = self.last_decision
        return (not self.closing and not self.is_frozen and self.ready
                and self.current_rgb is not None and decision is not None
                and decision.status == RECOGNIZED
                and not is_other_label(self.model.labels[decision.index])
                and (self.source == "image" or (self.source == "camera" and self.camera_active)))

    def toggle_confirmation(self):
        if self.is_frozen:
            self.resume_camera()
        else:
            self.confirm_identification()

    def confirm_identification(self):
        if not self._can_confirm():
            return
        decision = self.last_decision
        label = self.model.labels[decision.index]
        # Copia de la imagen visible y de los resultados mostrados en este clic.
        # La confianza y el Top 3 conservan el suavizado real, sin recalcularlos.
        frame = self.current_rgb.copy()
        confirmed = ConfirmedDetection(label, decision.confidence,
            tuple(self.model.top_n(decision.probabilities)), datetime.now(),
            self.catalog.get(label)["categoria"], frame, decision,
            self.catalog.narration(label, student=self.mode_var.get() == "Estudiante"),
            self.roi_var.get())
        self.is_frozen = True
        self.confirmed_detection = confirmed
        self.frozen_frame = frame
        self.current_rgb = frame
        # Una inferencia ya iniciada puede terminar, pero jamás aplicar su resultado.
        self.source_token += 1
        self._photo_file = None
        self.needs_render = True
        self.status_label.configure(text="✓ IDENTIFICACIÓN CONFIRMADA", text_color=GREEN)
        self.source_label.configure(text="IMAGEN CONGELADA · CONFIRMADA")
        self.fps_label.configure(text="Inferencia en pausa")
        self.confirm_button.configure(text="▶ REANUDAR CÁMARA", state="normal")
        self._render()
        try:
            self.history.record(label, confirmed.confidence, confirmed.category,
                timestamp=confirmed.timestamp, confirmed=True)
        except OSError as exc:
            log.exception("No se pudo guardar el historial confirmado")
            self.history.persistence_enabled = False
            self.history.warning = str(exc)
            self.history.record(label, confirmed.confidence, confirmed.category,
                timestamp=confirmed.timestamp, confirmed=True)
            self._notice("Error de CSV: historial solo en memoria", RED)
        self._refresh_history()
        # El clic solicita voz incluso en modo Normal; no depende de una detección futura.
        self.confirmation_voice_pending = True
        self.voice_last_text, self.voice_last_label = confirmed.narration_text, label
        if not self.voice_ready:
            self.voice_status.configure(text="Identificación confirmada · esperando voz disponible", text_color=MUTED)
        self._narrate_confirmation()

    def _narrate_confirmation(self):
        if not (self.is_frozen and self.confirmation_voice_pending and self.confirmed_detection):
            return
        if not self.voice_ready:
            return
        self.confirmation_voice_pending = False
        confirmed = self.confirmed_detection
        self._speak(confirmed.narration_text, confirmed.label, manual=True)

    def resume_camera(self):
        if not self.is_frozen or self.closing:
            return
        self._clear_confirmation()
        if self.source == "camera" and self.camera_active:
            self.camera_generation = self.camera.snapshot()[3]
            self._reset_decision(stop_narration=False)
            self.last_sequence = -1
            self.current_rgb = None
            self._empty_video("Reanudando cámara…")
            self.source_label.configure(text=f"{self.camera_selector.get().upper()} · EN VIVO")
            self.fps_label.configure(text="FPS  —")
        else:
            self.start_camera(stop_narration=False)

    def _reset_decision(self, *, stop_narration=True):
        if self.is_frozen:
            return
        self.source_token += 1
        self.last_decision = None
        self.current_education_label = None
        self._education_view = None
        self.listen_button.configure(state="disabled")
        self.confirm_button.configure(text="CONGELAR Y CONFIRMAR", state="disabled")
        if stop_narration and self.voice_request is not None:
            self.stop_voice(suppress=False)
        if stop_narration:
            self.narrator.reset()
        self.voice_suppressed_class = None
        self.inferred_sequence = -1
        if self.ready:
            self.decision_engine = TemporalDecision(self.model.labels,
                confidence_threshold=self.threshold_var.get() / 100,
                min_margin=config.MIN_MARGIN, smoothing_window=int(self.window_var.get()),
                stability_ratio=config.STABILITY_RATIO)
        self.status_label.configure(text="ANALIZANDO…" if self.ready else "CARGANDO MODELO…", text_color=YELLOW)
        self.name_label.configure(text="Sin identificación confirmada")
        self.confidence_label.configure(text="—", text_color=ACCENT)
        self.confidence_bar.set(0)
        self.category_label.configure(text="Esperando evidencia nueva")
        self._set_info("Centre el objeto o utilice un fondo con mayor contraste.")
        for label, value, bar in self.top_rows:
            label.configure(text="—")
            value.configure(text="—")
            bar.set(0)
        self.evidence_label.configure(text="Sin predicciones todavía")

    def settings_changed(self):
        self.threshold_label.configure(text=f"Confianza mín. {self.threshold_var.get():.0f} %")
        self.window_label.configure(text=f"Suavizado: {self.window_var.get()}")
        self.guide_label.configure(text="Coloque el objeto dentro del recuadro" if self.roi_var.get()
                                   else "Fotograma completo · ajuste central al formato del modelo")
        if self.is_frozen:
            return
        self._reset_decision()
        self.needs_render = True
        if self.source == "image":
            if self.current_rgb is not None:
                self._photo_file = "reprocess"
            elif self._image_path is not None:
                self._photo_file = self._image_path

    def toggle_settings(self):
        self.settings_open = not self.settings_open
        if self.settings_open:
            self.settings_window.deiconify()
            self.settings_window.lift()
        else:
            self.settings_window.withdraw()

    def _camera_changed(self, value):
        if self.source == "camera" or self.camera_active:
            self.start_camera()
        elif self.source != "image":
            self._reset_decision()

    def scan_cameras(self):
        self.stop_camera()
        self.camera.scan()

    def start_camera(self, *, stop_narration=True):
        if self.closing:
            return
        self._clear_confirmation()
        self._camera_start_preserves_voice = not stop_narration
        index = int(self.camera_selector.get().split()[-1])
        self.source = "camera"
        self._photo_file = None
        self.camera_active = False
        self.current_rgb = None
        self._reset_decision(stop_narration=stop_narration)
        self._empty_video("Abriendo cámara…")
        self.camera_request += 1
        self.camera.start(index, self.camera_request)

    def stop_camera(self):
        self._clear_confirmation()
        self.camera.stop()
        self.camera_request += 1
        self.camera_active = False
        self.source = "none"
        self.current_rgb = None
        self._photo_file = None
        self._reset_decision()
        self._empty_video("Cámara detenida\n\nPuede INICIAR CÁMARA o PROBAR IMAGEN")
        self.fps_label.configure(text="FPS  —")

    def _empty_video(self, text):
        self.video_label.configure(image="", text=text)
        self.display_image = None

    def choose_image(self):
        path = filedialog.askopenfilename(parent=self, title="Probar una imagen del elemento",
            filetypes=[("Imágenes", "*.jpg *.jpeg *.png *.webp"), ("Todos los archivos", "*.*")])
        if path:
            self.load_image(path)

    def load_image(self, path):
        if not self.ready:
            return
        self._clear_confirmation()
        self.camera.stop()
        self.camera_active = False
        self.source = "image"
        self.current_rgb = None
        self._reset_decision()
        self._photo_file = Path(path)
        self._image_path = Path(path)
        self._empty_video("Abriendo y analizando imagen…")
        self.source_label.configure(text="IMAGEN · UNA INFERENCIA")
        self.fps_label.configure(text="Imagen fija")

    def _show_decision(self, decision):
        if self.is_frozen:
            return
        self.last_decision = decision
        color = GREEN if decision.status == RECOGNIZED else RED if decision.status in (UNCERTAIN, UNKNOWN) else YELLOW
        self.status_label.configure(text=decision.status, text_color=color)
        self.confidence_label.configure(text=f"{decision.confidence * 100:.1f} %", text_color=theme.confidence_color(decision.confidence))
        self.confidence_bar.configure(progress_color=theme.confidence_color(decision.confidence))
        self.confidence_bar.set(decision.confidence)
        self.confidence_caption.configure(text="  confianza" if decision.status == RECOGNIZED else "  Top 1")
        label = self.model.labels[decision.index]
        if decision.status == RECOGNIZED:
            self._render_education(label)
        else:
            self.current_education_label = None
            self._education_view = None
            self.listen_button.configure(state="disabled")
            self.name_label.configure(text="Objeto no registrado" if decision.status == UNKNOWN else "Sin identificación confirmada")
            self.category_label.configure(text="No se asigna una ficha educativa sin reconocimiento")
            self._set_info("La clase Otro domina la predicción." if decision.status == UNKNOWN else
                           "No existe evidencia suficiente para confirmar el elemento.\n\nCentre el objeto, manténgalo quieto y utilice un fondo con mayor contraste.")
        for i, (_, name, probability) in enumerate(self.model.top_n(decision.probabilities)):
            text, value, bar = self.top_rows[i]
            text.configure(text=name)
            value.configure(text=f"{probability * 100:.1f} %")
            bar.configure(progress_color=theme.confidence_color(probability))
            bar.set(probability)
        evidence = "Imagen fija · sin validación temporal" if self.source == "image" else (
            f"Media de {decision.frames}/{self.window_var.get()} · estabilidad {decision.agreement * 100:.0f} %")
        self.evidence_label.configure(text=f"{evidence} · margen {decision.margin * 100:.1f} pp")
        self.confirm_button.configure(state="normal" if self._can_confirm() else "disabled")

    def _render_education(self, label):
        if self.is_frozen:
            return
        information = self.catalog.get(label)
        self.current_education_label = label
        chemical = bool(information.get("formula"))
        self.status_label.configure(text="PRODUCTO IDENTIFICADO" if chemical else "ELEMENTO DETECTADO", text_color=GREEN)
        self.listen_button.configure(state="normal" if self.voice_ready else "disabled")
        view = (label, self.mode_var.get())
        if view == self._education_view:
            return
        self._education_view = view
        self.name_label.configure(text=information["nombre"])
        details = [information["categoria"]]
        if chemical:
            details.append("Fórmula: " + information["formula"])
        elif information.get("capacidad"):
            details.append(information["capacidad"])
        self.category_label.configure(text="  ·  ".join(details))
        body = self.catalog.describe(label, student=self.mode_var.get() == "Estudiante")
        # Categoría, fórmula y capacidad ya tienen una línea fija encima del texto.
        for heading, value in (("Categoría", information["categoria"]),
                               ("Fórmula", information.get("formula")),
                               ("Capacidad", information.get("capacidad"))):
            prefix = f"{heading}\n{value}\n\n"
            if value and body.startswith(prefix):
                body = body[len(prefix):]
        self._set_info(body)

    def _mode_changed(self, value):
        if self.current_education_label:
            self._render_education(self.current_education_label)

    def _voice_selected(self, _=None):
        selected = self.voice_choices.get(self.voice_selector.get())
        if selected:
            self.selected_voice_ids[selected["engine"]] = selected["id"]
        self._voice_settings_changed()

    def _voice_engine_changed(self, _=None):
        self.stop_voice(suppress=False)
        self._refresh_voice_choices()

    def _refresh_voice_choices(self):
        engine = "edge" if self.voice_engine_selector.get() == "Voz neuronal" else "sapi"
        voices = [item for item in self.all_voices if item["engine"] == engine]
        self.voice_choices = {item["name"] if engine == "edge" else
            f"{item['name']} · {item['language']}": item for item in voices}
        preferred = self.selected_voice_ids.get(engine)
        selected = next((name for name, item in self.voice_choices.items() if item["id"] == preferred), None)
        if selected is None:
            selected = next((name for name, item in self.voice_choices.items() if item["spanish"]), None)
        placeholder = "Seleccione una voz local" if voices else "No hay voces disponibles"
        self.voice_selector.configure(values=list(self.voice_choices) or [placeholder],
            state="normal" if voices else "disabled")
        self.voice_selector.set(selected or placeholder)
        self._voice_settings_changed()

    def _voice_settings_changed(self, _=None):
        volume = int(self.voice_volume_var.get())
        self.voice_volume_label.configure(text=f"Volumen {volume} %")
        selected = self.voice_choices.get(self.voice_selector.get())
        engine = "edge" if self.voice_engine_selector.get() == "Voz neuronal" else "sapi"
        self.voice_ready = selected is not None
        if selected:
            self.selected_voice_ids[engine] = selected["id"]
            self.voice.configure(engine=engine, voice_id=selected["id"],
                rate={"Lenta": -2, "Normal": 0, "Rápida": 2}[self.voice_rate_selector.get()], volume=volume)
            self.voice_warning = ("" if selected["spanish"] else
                "Voz local no española seleccionada manualmente · puede cambiarla en AJUSTES.")
        else:
            self.voice_warning = ("No hay voz española local instalada. Seleccione una voz o use Voz neuronal."
                if self.voice_choices else "No hay voces locales disponibles. Use Voz neuronal.")
        self.listen_button.configure(state="normal" if self.voice_ready and self.current_education_label else "disabled")
        self.repeat_button.configure(state="normal" if self.voice_ready and self.voice_last_text else "disabled")
        if not self.voice_busy:
            self.voice_status.configure(text=self.voice_warning or
                ("Voz neuronal lista · Dalia y otras voces españolas en AJUSTES" if engine == "edge" else "Voz local lista"),
                text_color=YELLOW if self.voice_warning else MUTED)

    def _speak(self, text, label, *, manual):
        if not self.voice_ready or not text:
            return
        self.narrator.cancel()
        request_id = self.voice.speak(text, label=label)
        self.narrator.reserve(label, request_id)
        self.voice_request = request_id
        self.voice_busy = True
        self.voice_manual_active = manual
        self.voice_last_text, self.voice_last_label = text, label
        self.repeat_button.configure(state="normal")
        self.voice_status.configure(text="Voz: preparando explicación…", text_color=ACCENT)

    def listen_explanation(self):
        if self.is_frozen and self.confirmed_detection:
            self.confirmation_voice_pending = False
            confirmed = self.confirmed_detection
            self._speak(confirmed.narration_text, confirmed.label, manual=True)
            return
        if self.current_education_label:
            label = self.current_education_label
            self._speak(self.catalog.narration(label, student=self.mode_var.get() == "Estudiante"), label, manual=True)

    def repeat_explanation(self):
        if self.is_frozen and self.confirmed_detection:
            self.listen_explanation()
            return
        if self.voice_last_text:
            log.info("Repetición de voz solicitada: %s", self.voice_last_label)
            self._speak(self.voice_last_text, self.voice_last_label, manual=True)

    def stop_voice(self, *, suppress=True):
        self.confirmation_voice_pending = False
        if suppress:
            log.info("Detener voz solicitado")
            label = self.current_education_label or self.voice_last_label
            self.voice_suppressed_class = normalize_label(label) if label else None
        self.voice.stop()
        self.narrator.cancel()
        self.voice_request = None
        self.voice_busy = False
        self.voice_manual_active = False
        if hasattr(self, "voice_status"):
            self.voice_status.configure(text="Voz detenida" if self.voice_ready else "Voz no disponible", text_color=MUTED)

    def _voice_events(self):
        while True:
            try:
                event, payload = self.voice.events.get_nowait()
            except queue.Empty:
                break
            if event == "ready":
                self.all_voices = payload.get("voices", [])
                self._refresh_voice_choices()
                log.info("Voces disponibles: %s; seleccionada: %s", len(self.all_voices), self.voice_selector.get())
            elif event == "error" and payload.get("request_id") is None:
                self.confirmation_voice_pending = False
                self.voice_ready = False
                self.voice_busy = False
                self.voice_manual_active = False
                self.voice_request = None
                self.narrator.cancel()
                self.listen_button.configure(state="disabled")
                self.repeat_button.configure(state="disabled")
                self.voice_status.configure(text="Voz no disponible. El reconocimiento sigue funcionando.", text_color=YELLOW)
                log.warning("Motor de voz: %s", payload.get("message", payload))
            elif payload.get("request_id") == self.voice_request:
                if event in ("generating", "cache_hit", "fallback", "status"):
                    message = payload.get("message") or {
                        "generating": "Voz: generando audio en español…",
                        "cache_hit": "Voz: reutilizando audio guardado…",
                        "fallback": "Voz neuronal no disponible. Usando voz local.",
                    }.get(event, "")
                    if message:
                        self.voice_status.configure(text=message, text_color=YELLOW if event == "fallback" else MUTED)
                        log.info("Voz %s: %s", event, message)
                elif event == "started":
                    self.narrator.started(self.voice_request)
                    using_fallback = payload.get("engine") == "sapi" and self.voice_engine_selector.get() == "Voz neuronal"
                    self.voice_status.configure(text=("Voz neuronal no disponible. Usando voz local." if using_fallback else
                        "🔊 Explicando: " + payload.get("label", self.voice_last_label or "elemento")),
                        text_color=YELLOW if using_fallback else ACCENT)
                    log.info("Voz iniciada: %s", payload.get("label", self.voice_last_label))
                elif event in ("finished", "stopped", "error"):
                    self.narrator.cancel(self.voice_request)
                    self.voice_request = None
                    self.voice_busy = False
                    self.voice_manual_active = False
                    if event == "error":
                        self.voice_status.configure(text=payload.get("message") or
                            "No se pudo reproducir la voz. Revise AJUSTES y la salida de audio.", text_color=YELLOW)
                        log.warning("Error de narración: %s", payload.get("message", payload))
                    else:
                        self.voice_status.configure(text=self.voice_warning or "Voz lista · REPETIR vuelve a narrar", text_color=MUTED)
                        log.info("Voz %s", event)

    def _refresh_history(self):
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        for row_number, record in enumerate(self.history.recent(config.HISTORY_VISIBLE_ROWS)):
            self.tree.insert("", "end", values=(record["hora"], record["clase"], record["confianza"] + " %", record["categoria"]),
                             tags=("even" if row_number % 2 == 0 else "odd",))
        stats = self.history.stats
        self.session_label.configure(text=f"HISTORIAL   |   Identificaciones confirmadas en sesión: {stats['detections']}   ·   "
            f"Confianza media: {stats['mean_confidence'] * 100:.1f} %   ·   "
            f"Materiales: {stats['materials']}   ·   Productos: {stats['products']}")

    def capture(self):
        if self.current_rgb is None:
            self._notice("No hay imagen disponible para capturar", YELLOW)
            return None
        decision = self.last_decision
        label = self.model.labels[decision.index] if decision and decision.status == RECOGNIZED else "Sin_identificacion"
        confidence = decision.confidence if decision else 0.0
        try:
            self.capture_dir.mkdir(parents=True, exist_ok=True)
            path = self.capture_dir / capture_filename(label, confidence)
            self.current_rgb.save(path, "JPEG", quality=95)
            self.last_capture = path
            self._notice("Captura guardada en captures/", GREEN)
            log.info("Captura solicitada por usuario: %s", path.name)
            return path
        except Exception as exc:
            self._error("No se pudo guardar la captura", exc)
            return None

    def toggle_presentation(self):
        self.set_presentation(not self.presenting)

    def set_presentation(self, active):
        self.presenting = bool(active)
        self.attributes("-fullscreen", self.presenting)
        if active and self.settings_open:
            self.toggle_settings()
        self.ui.presentation_changed(self.presenting)
        self.presentation_button.configure(text="SALIR DE PRESENTACIÓN  ·  ESC" if active else "MODO PRESENTACIÓN  ·  F11")
        self.needs_render = True

    def _render(self):
        if self.current_rgb is None:
            return
        width, height = self.video_host.winfo_width() - 12, self.video_host.winfo_height() - 12
        if width < 10 or height < 10:
            return
        display = ImageOps.contain(self.current_rgb, (width, height), Image.Resampling.BILINEAR)
        use_roi = self.confirmed_detection.roi_enabled if self.is_frozen else self.roi_var.get()
        if use_roi:
            draw = ImageDraw.Draw(display)
            draw.rectangle(roi_bounds(*display.size), outline=theme.resolve(theme.ROI_COLOR), width=2)
        photo = ImageTk.PhotoImage(display, master=self)
        self.video_label.configure(image=photo, text="")
        self.display_image = photo
        self.needs_render = False

    def _camera_events(self):
        while True:
            try:
                event, value = self.camera.events.get_nowait()
            except queue.Empty:
                break
            if event == "cameras":
                choices = [f"Cámara {i}" for i in value] or [f"Cámara {i}" for i in config.CAMERA_INDICES]
                self.camera_selector.configure(values=choices)
                self.camera_selector.set(choices[0])
                self._notice(f"Cámaras disponibles: {', '.join(map(str, value))}" if value else "No se pudo abrir la cámara · PROBAR IMAGEN disponible", MUTED if value else YELLOW)
            elif event == "started":
                index, request = value
                if self.source == "camera" and request == self.camera_request:
                    self.camera_active = True
                    if not self.is_frozen:
                        preserve_voice = getattr(self, "_camera_start_preserves_voice", False)
                        self._reset_decision(stop_narration=not preserve_voice)
                        self._camera_start_preserves_voice = False
                        # El próximo snapshot no debe volver a borrar este reinicio.
                        self.camera_generation = self.camera.snapshot()[3]
                        self.source_label.configure(text=f"CÁMARA {index} · EN VIVO")
                    self._notice("Cámara activa", GREEN)
            elif event in ("error", "disconnected"):
                self.camera_active = False
                if self.source == "camera" and not self.is_frozen:
                    self.current_rgb = None
                    self._reset_decision()
                    self._empty_video("No se pudo abrir la cámara\n\nPROBAR IMAGEN sigue disponible")
                self._notice(str(value), YELLOW)
            elif event == "status":
                self._notice(str(value))

    def _poll(self):
        if self.closing:
            return
        try:
            self._camera_events()
            self._voice_events()
            self._narrate_confirmation()
            if self.pending is not None and self.pending.done():
                future, kind, token = self.pending, self.pending_kind, self.pending_token
                self.pending = None
                try:
                    result = future.result()
                    if kind == "load":
                        self.ready = True
                        self._reset_decision()
                        self.image_button.configure(state="normal")
                        self._notice(f"Modelo cargado · {len(self.model.labels)} clases", GREEN)
                        log.info("Modelo cargado: %s -> %s", self.model.input_shape, self.model.output_shape)
                    elif token == self.source_token and not self.is_frozen:
                        if kind == "image":
                            self.current_rgb, probabilities = result
                            self.needs_render = True
                            self._show_decision(self.decision_engine.evaluate_still(probabilities))
                        else:
                            self._show_decision(self.decision_engine.update(result))
                except Exception as exc:
                    if kind == "load" or (token == self.source_token and not self.is_frozen):
                        self._error("ERROR AL CARGAR MODELO" if kind == "load" else "Error de predicción / imagen", exc)
                        if kind == "load":
                            self.status_label.configure(text="ERROR AL CARGAR MODELO", text_color=RED)
                        elif kind == "camera":
                            self.stop_camera()
            if self.source == "camera" and not self.is_frozen:
                frame, sequence, fps, generation = self.camera.snapshot()
                if self.camera_active and frame is not None:
                    if generation != self.camera_generation:
                        self.camera_generation = generation
                        self._reset_decision()
                    if sequence != self.last_sequence:
                        self.last_sequence = sequence
                        self.current_rgb = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                        self.needs_render = True
                        self.fps_label.configure(text=f"FPS  {fps:.0f}")
                    if (self.ready and self.pending is None and sequence != self.inferred_sequence
                            and (time.monotonic() - self.last_submit) * 1000 >= config.INFERENCE_INTERVAL_MS):
                        self.pending = self.executor.submit(self.model.predict_bgr, frame, self.roi_var.get())
                        self.pending_kind, self.pending_token = "camera", self.source_token
                        self.inferred_sequence = sequence
                        self.last_submit = time.monotonic()
            elif (self.source == "image" and not self.is_frozen and self.ready
                  and self.pending is None and self._photo_file is not None):
                path = self._photo_file
                self._photo_file = None
                if path == "reprocess":
                    rgb = self.current_rgb.copy()
                    use_roi = self.roi_var.get()
                    self.pending = self.executor.submit(lambda: (rgb, self.model.predict_rgb(rgb, use_roi)))
                else:
                    self.pending = self.executor.submit(self.model.predict_file, path, self.roi_var.get())
                self.pending_kind, self.pending_token = "image", self.source_token
            if self.needs_render:
                self._render()
            self.health_label.configure(text=f"Modelo: {'CARGADO' if self.ready else 'NO DISPONIBLE / CARGANDO'}  ·  "
                                             f"Cámara: {'ACTIVA' if self.camera_active else 'DETENIDA'}")
        except Exception as exc:
            self._error("Error de aplicación", exc)
        self.after(config.RENDER_INTERVAL_MS, self._poll)

    def _error(self, title, exc):
        self.last_error = f"{title}: {exc}"
        log.error(self.last_error, exc_info=(type(exc), exc, exc.__traceback__))
        self._notice(title + " · consulte logs/agente.log", RED)
        messagebox.showerror(title, f"{exc}\n\nDetalle registrado en logs/agente.log", parent=self)

    def _callback_error(self, kind, value, trace):
        log.error("Error de evento de interfaz", exc_info=(kind, value, trace))
        self._notice("Error de interfaz · consulte logs/agente.log", RED)

    def close(self):
        if self.closing:
            return
        if hasattr(self, "ui"):
            self.ui.close()
        self.closing = True
        self.source_token += 1
        self.camera.close()
        self.voice.close()
        self.narrator.cancel()
        if self.pending is not None:
            self.pending.cancel()
        self._notice("Cerrando: deteniendo voz y liberando cámara…")
        self.after(50, self._finish_close)

    def _finish_close(self):
        # No destruir Tk ni terminar el proceso mientras se utiliza la cámara.
        if self.camera.thread.is_alive() or self.voice.thread.is_alive() or (self.pending is not None and not self.pending.done()):
            self.after(50, self._finish_close)
            return
        self.executor.shutdown(wait=True, cancel_futures=True)
        log.info("Aplicación cerrada; recursos liberados")
        self.destroy()


def main():
    try:
        app = LabApp()
        if "--self-test" in sys.argv:
            from tests.gui_smoke import start_smoke
            start_smoke(app)
        app.mainloop()
        return getattr(app, "test_exit_code", 0)
    except Exception:
        log.exception("Error fatal al iniciar la interfaz")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
