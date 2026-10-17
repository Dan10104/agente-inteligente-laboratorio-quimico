"""Personalización de presentación. No accede a servicios ni altera sus estados."""
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter import font as tkfont
import customtkinter as ctk
import theme
import ui_preferences


class Tooltip:
    def __init__(self, widget, text):
        self.widget, self.text, self.window, self.job = widget, text, None, None
        widget.bind("<Enter>", self.enter, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def enter(self, _=None):
        self.hide()
        self.job = self.widget.after(450, self.show)

    def show(self):
        self.job = None
        if not self.widget.winfo_ismapped():
            return
        self.window = tk.Toplevel(self.widget)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        label = tk.Label(self.window, text=self.text() if callable(self.text) else self.text,
                         font=theme.font(11), bg=theme.resolve(theme.INNER),
                         fg=theme.resolve(theme.TEXT), padx=9, pady=6, relief="solid", bd=1)
        label.pack()
        self.window.geometry(f"+{self.widget.winfo_rootx()}+{self.widget.winfo_rooty()+self.widget.winfo_height()+4}")

    def hide(self, _=None):
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.window:
            self.window.destroy()
            self.window = None


class UIController:
    def __init__(self, app, preferences_path=None):
        self.app = app
        self.path = Path(preferences_path) if preferences_path is not None else Path(__file__).with_name("ui_preferences.json")
        self.prefs = ui_preferences.load(self.path)
        self.maximized = None
        self.window = None
        self.reflow_job = None
        self.ratio_job = None
        self.system_job = None
        self.ready = False
        self.applying = False
        self.last_root_size = None
        self.tooltips = []
        self.section_buttons = {}
        self.section_headers = {"camera": app.camera_header, "info": app.result_header,
                                "top3": app.top_header, "history": app.history_header}
        for key, header in self.section_headers.items():
            tools = ctk.CTkFrame(header, fg_color="transparent")
            tools.grid(row=0, column=3, sticky="e", padx=(6, 0))
            buttons = []
            if key != "top3":
                button = self.small_button(tools, "□", lambda name=key: self.maximize(name))
                button.pack(side="left", padx=(0, 3))
                self.tooltips.append(Tooltip(button, lambda name=key: "Restaurar panel" if self.maximized == name else "Maximizar panel"))
                buttons.append(button)
            button = self.small_button(tools, "−", lambda name=key: self.toggle_section(name))
            button.pack(side="left")
            self.tooltips.append(Tooltip(button, lambda name=key: "Expandir sección" if self.prefs["collapsed"][name] else "Contraer sección"))
            buttons.append(button)
            self.section_buttons[key] = buttons
        self.tooltips.append(Tooltip(app.ui_settings_button, "Ajustes de interfaz · Ctrl+,"))
        self.tooltips.append(Tooltip(app.functional_settings_button, "Ajustes de reconocimiento y voz"))
        for pane in (app.body, app.workspace):
            pane.bind("<ButtonRelease-1>", self.sash_released, add="+")
        app.left_panel.bind("<Configure>", lambda _: self.schedule_reflow(), add="+")
        app.result_panel.bind("<Configure>", lambda _: self.schedule_reflow(), add="+")
        app.bind("<Control-comma>", lambda _: self.open_settings(), add="+")
        for key in ("<Control-plus>", "<Control-equal>", "<Control-KP_Add>"):
            app.bind(key, lambda _: self.step_scale(1), add="+")
        for key in ("<Control-minus>", "<Control-KP_Subtract>"):
            app.bind(key, lambda _: self.step_scale(-1), add="+")
        app.bind("<Control-0>", lambda _: self.set_scale(1.0), add="+")
        self.apply_preferences()
        app.after(180, self.start_layout)

    def small_button(self, parent, text, command):
        return ctk.CTkButton(parent, text=text, command=command, width=27, height=25,
            corner_radius=5, font=theme.font(15), **theme.button_style())

    def start_layout(self):
        self.ready = True
        self.restore_ratios()
        self.schedule_reflow()

    def save(self):
        result = ui_preferences.save(self.path, self.prefs)
        if hasattr(self, "save_notice"):
            self.save_notice.configure(text="Preferencias guardadas automáticamente" if result else
                                       "No se pudo guardar. Los ajustes se mantienen durante esta sesión.")
        return result

    def capture_ratios(self):
        if not self.ready or self.maximized or self.app.presenting or self.applying:
            return
        a = self.app
        if not self.prefs["collapsed"]["camera"] and not self.prefs["collapsed"]["info"] and len(a.body.panes()) == 2:
            self.prefs["ratios"]["camera"] = min(.85, max(.15, a.body.sash_coord(0)[0] / max(1, a.body.winfo_width())))
        if self.prefs["visible"]["history"] and not self.prefs["collapsed"]["history"] and len(a.workspace.panes()) == 2:
            self.prefs["ratios"]["workspace"] = min(.95, max(.4, a.workspace.sash_coord(0)[1] / max(1, a.workspace.winfo_height())))

    def sash_released(self, _=None):
        def finish():
            if not self.app.closing:
                self.capture_ratios()
                self.save()
                self.schedule_reflow()
        self.app.after_idle(finish)

    def restore_ratios(self):
        self.ratio_job = None
        if self.app.closing:
            return
        a = self.app
        if len(a.body.panes()) == 2:
            width = a.body.winfo_width()
            collapsed = self.prefs["collapsed"] if not a.presenting else {}
            pos = 190 if collapsed.get("camera") else width - 190 if collapsed.get("info") else int(width*self.prefs["ratios"]["camera"])
            a.body.sash_place(0, pos, 0)
        if len(a.workspace.panes()) == 2:
            height = a.workspace.winfo_height()
            pos = height - 44 if self.prefs["collapsed"]["history"] else int(height*self.prefs["ratios"]["workspace"])
            a.workspace.sash_place(0, 0, pos)
        self.schedule_reflow()

    def defer_ratios(self):
        if self.ratio_job:
            self.app.after_cancel(self.ratio_job)
        self.ratio_job = self.app.after(80, self.restore_ratios)

    @staticmethod
    def grid_visible(widget, visible):
        widget.grid() if visible else widget.grid_remove()

    @staticmethod
    def panes(pane, desired):
        current = list(pane.panes())
        desired_names = [str(item[0]) for item in desired]
        if [str(x) for x in current] != desired_names:
            for child in current:
                pane.forget(child)
            for child, options in desired:
                pane.add(child, **options)
        else:
            for child, options in desired:
                pane.paneconfigure(child, **options)

    def apply_layout(self):
        a, p = self.app, self.prefs
        self.applying = True
        presenting = a.presenting
        maximum = None if presenting else self.maximized
        collapsed = {key: False for key in p["collapsed"]} if presenting else p["collapsed"]
        scale = p["scale"]
        cam_min, info_min = max(420, round(370*scale)), max(320, round(250*scale))
        camera_open = maximum == "camera" or not collapsed["camera"]
        info_open = maximum == "info" or not collapsed["info"]
        history_open = maximum == "history" or not collapsed["history"]
        self.grid_visible(a.camera_content, camera_open)
        self.grid_visible(a.confirm_button, camera_open)
        self.grid_visible(a.result_content, info_open)
        self.grid_visible(a.technical, not presenting)
        self.grid_visible(a.top_header, presenting or p["visible"]["top3"])
        self.grid_visible(a.top_container, (presenting or p["visible"]["top3"]) and not collapsed["top3"])
        self.grid_visible(a.tree, history_open)
        self.grid_visible(a.history_scroll, history_open)
        self.grid_visible(a.history_hscroll, history_open)
        self.grid_visible(a.session_label, history_open and p["visible"]["stats"])
        self.grid_visible(a.footer, p["visible"]["status"])
        body_panes = []
        if maximum != "info":
            body_panes.append((a.left_panel, dict(minsize=cam_min if camera_open else 190, stretch="always" if camera_open else "never")))
        if maximum != "camera":
            body_panes.append((a.result_panel, dict(minsize=info_min if info_open else 190, stretch="always" if info_open else "never")))
        self.panes(a.body, body_panes)
        outer = []
        if maximum != "history":
            outer.append((a.body, dict(minsize=260, stretch="always")))
        if not presenting and (maximum == "history" or (maximum is None and p["visible"]["history"])):
            outer.append((a.history_panel, dict(minsize=max(100, round(110*scale)) if history_open else 40,
                                             stretch="always" if history_open else "never")))
        self.panes(a.workspace, outer)
        for key, buttons in self.section_buttons.items():
            buttons[-1].configure(text="+" if collapsed[key] else "−", state="disabled" if presenting else "normal")
            if key != "top3":
                buttons[0].configure(text="↙" if maximum == key else "□", state="disabled" if presenting else "normal")
        self.applying = False
        self.defer_ratios()
        self.schedule_reflow()

    def toggle_section(self, name):
        self.capture_ratios()
        self.maximized = None
        self.prefs["collapsed"][name] = not self.prefs["collapsed"][name]
        if name in ("top3", "history"):
            self.prefs["visible"][name] = True
        self.apply_layout()
        self.sync_controls()
        self.save()

    def maximize(self, name):
        self.capture_ratios()
        self.maximized = None if self.maximized == name else name
        self.apply_layout()

    def set_visible(self, key, value):
        self.capture_ratios()
        if key == "history" and not value and self.maximized == "history":
            self.maximized = None
        self.prefs["visible"][key] = bool(value)
        if key in ("top3", "history") and value:
            self.prefs["collapsed"][key] = False
        self.apply_layout()
        self.sync_controls()
        self.save()

    def system_theme(self):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                return "light" if winreg.QueryValueEx(key, "AppsUseLightTheme")[0] else "dark"
        except (ImportError, OSError):
            return "light"

    def apply_theme(self):
        mode = self.system_theme() if self.prefs["theme"] == "Sistema" else "dark" if self.prefs["theme"] == "Oscuro" else "light"
        ctk.set_appearance_mode(mode)
        theme.refresh_native(self.app, self.prefs["scale"], self.prefs["density"])
        theme.style_history(ttk.Style(self.app), self.app.tree, self.prefs["scale"]*self.prefs["fonts"]["history"], self.prefs["density"])
        for pane in (self.app.workspace, self.app.body):
            pane.configure(bg=theme.resolve(theme.BORDER))
        for tooltip in self.tooltips:
            tooltip.hide()
        self.app.needs_render = True
        if self.system_job:
            self.app.after_cancel(self.system_job)
        self.system_job = self.app.after(2500, self.check_system_theme)

    def check_system_theme(self):
        self.system_job = None
        if self.app.closing:
            return
        if self.prefs["theme"] == "Sistema" and ctk.get_appearance_mode().lower() != self.system_theme():
            self.apply_theme()
        else:
            self.system_job = self.app.after(2500, self.check_system_theme)

    def set_theme(self, value):
        self.prefs["theme"] = value
        self.apply_theme()
        self.sync_controls()
        self.save()

    def set_scale(self, value):
        self.prefs["scale"] = max(.85, min(1.45, float(value)))
        self.apply_fonts()
        self.apply_layout()
        self.sync_controls()
        self.save()

    def step_scale(self, direction):
        self.set_scale(round(self.prefs["scale"] + .15*direction, 2))
        return "break"

    def set_density(self, value):
        self.prefs["density"] = value
        self.apply_fonts()
        self.apply_layout()
        self.sync_controls()
        self.save()

    def set_font_factor(self, key, value):
        self.prefs["fonts"][key] = float(value)
        self.apply_fonts()
        self.schedule_reflow()
        self.save()

    def apply_fonts(self):
        a, p = self.app, self.prefs
        scale = p["scale"]
        theme.style_controls(a, scale=scale)
        factor = p["fonts"]
        presentation = a.presenting
        a.name_label.configure(font=theme.font(36 if presentation else 27, "bold", scale*factor["title"]))
        a.confidence_label.configure(font=theme.font(48 if presentation else 34, "bold", scale*factor["confidence"]))
        a.info_text.configure(font=theme.font(18 if presentation else 13, scale=scale*factor["education"]))
        a.category_label.configure(font=theme.font(17 if presentation else 13, scale=scale))
        if presentation:
            for label, value, _ in a.top_rows:
                label.configure(font=theme.font(16, scale=scale))
                value.configure(font=theme.font(16, scale=scale))
        delta = {"Compacta": -4, "Normal": 0, "Cómoda": 6}[p["density"]]
        buttons = (a.start_button, a.stop_button, a.capture_button, a.image_button, a.confirm_button, a.listen_button,
                   a.voice_stop_button, a.repeat_button, a.ui_settings_button, a.presentation_button,
                   a.search_camera_button, a.functional_settings_button)
        for button in buttons:
            button.configure(height=max(28, round(34*scale)+delta))
        for buttons in self.section_buttons.values():
            for button in buttons:
                button.configure(width=27, height=25, font=theme.font(15))
        theme.refresh_native(a, scale, p["density"])
        theme.style_history(ttk.Style(a), a.tree, scale*factor["history"], p["density"])
        width = min(a.winfo_screenwidth()-40, round(600*max(1., scale)))
        height = min(a.winfo_screenheight()-80, round(350*max(1., scale)))
        a.settings_window.geometry(f"{width}x{height}")
        self.schedule_reflow()

    def apply_preferences(self):
        self.prefs = ui_preferences.validate(self.prefs)
        self.apply_theme()
        self.apply_fonts()
        self.apply_layout()
        self.sync_controls()

    def presentation_changed(self, active):
        if active and self.window:
            self.window.withdraw()
        self.apply_fonts()
        self.apply_layout()

    def schedule_reflow(self):
        if self.app.closing:
            return
        if self.reflow_job:
            self.app.after_cancel(self.reflow_job)
        self.reflow_job = self.app.after(70, self.reflow)

    def reflow(self):
        self.reflow_job = None
        a, p = self.app, self.prefs
        if a.closing:
            return
        scale = p["scale"]
        root_size = (a.winfo_width(), a.winfo_height())
        if self.last_root_size != root_size:
            self.last_root_size = root_size
            self.defer_ratios()
        title_width = max(180, a.header.winfo_width()-a.ui_settings_button.winfo_reqwidth()-a.presentation_button.winfo_reqwidth()-155)
        a.app_title_label.configure(wraplength=title_width)
        a.app_subtitle_label.configure(wraplength=title_width)
        width = max(220, a.camera_content.winfo_width()-28)
        compact = width < 690*scale
        for col in range(4):
            active = col < 2 or not compact
            a.controls.grid_columnconfigure(col, weight=1 if active else 0,
                                            uniform="buttons" if active else "", minsize=0)
        for i, button in enumerate((a.start_button, a.stop_button, a.capture_button, a.image_button)):
            button.grid_configure(row=i//2 if compact else 0, column=i%2 if compact else i, pady=3)
        if width < 590*scale:
            a.confirmation_hint.grid_configure(row=1, column=0, columnspan=3, sticky="w", padx=0, pady=(6, 0))
        else:
            a.confirmation_hint.grid_configure(row=0, column=2, columnspan=1, sticky="e", padx=(10, 0), pady=0)
        # Los mismos controles se redistribuyen, conservando variables y comandos.
        if compact:
            a.roi_check.grid_configure(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=6)
            a.functional_settings_button.grid_configure(row=1, column=2, sticky="e", padx=8, pady=6)
        else:
            a.roi_check.grid_configure(row=0, column=3, columnspan=1, padx=(4, 10), pady=7)
            a.functional_settings_button.grid_configure(row=0, column=4, padx=(0, 8), pady=7)
        a.source_label.configure(wraplength=max(70, a.left_panel.winfo_width()-190))
        a.guide_label.configure(wraplength=width)
        a.confirmation_hint.configure(wraplength=width if width < 590*scale else max(180, width-255*scale))
        a.fps_label.grid_remove() if p["collapsed"]["camera"] and not a.presenting and self.maximized != "camera" else a.fps_label.grid()
        controls_height = sum(w.winfo_reqheight() for w in (a.learning_controls, a.guide_label, a.controls, a.confirm_button)) + 8
        if not a.presenting:
            controls_height += a.technical.winfo_reqheight()
        video_height = max(140, a.left_panel.winfo_height()-a.camera_header.winfo_reqheight()-controls_height-70)
        a.video_host.configure(height=video_height)
        result_width = max(230, a.result_content.winfo_width()-36)
        narrow_confidence = (a.confidence_label.winfo_reqwidth() + a.confidence_caption.winfo_reqwidth() + 12 > result_width)
        a.confidence_caption.grid_configure(row=1 if narrow_confidence else 0,
            column=0 if narrow_confidence else 1, padx=0 if narrow_confidence else (8, 0))
        a.confidence_bar.grid_configure(row=2 if narrow_confidence else 1)
        for widget in (a.name_label, a.category_label, a.voice_status, a.evidence_label):
            widget.configure(wraplength=result_width)
        a.status_label.configure(wraplength=max(90, a.result_panel.winfo_width()-95))
        a.top_title.configure(wraplength=max(130, result_width-48))
        for label, value, bar in a.top_rows:
            value.configure(width=round(70*scale))
            label.configure(wraplength=max(75, result_width-round(110*scale)))
        wide_voice = result_width >= 490*scale
        for i, button in enumerate((a.listen_button, a.voice_stop_button, a.repeat_button)):
            button.grid_configure(row=0 if wide_voice else i, column=i if wide_voice else 0,
                                  sticky="ew", padx=(0, 4) if wide_voice else 0, pady=(1, 3))
        for col in range(3):
            a.voice_controls.grid_columnconfigure(col, weight=1 if col == 0 else 0, minsize=0)
        # El texto educativo siempre conserva scroll y un área legible.
        a.info_text.configure(height=max(100, min(330, a.result_panel.winfo_height()-365)))
        a.session_label.configure(wraplength=max(400, a.history_panel.winfo_width()-36), justify="left")
        a.scientific_label.configure(wraplength=max(450, a.footer.winfo_width()-15))
        a.health_label.configure(wraplength=max(220, a.footer.winfo_width()*.55))
        a.notice_label.configure(wraplength=max(220, a.footer.winfo_width()*.4))
        available = max(500, a.tree.winfo_width()-4)
        style = ttk.Style(a)
        heading_font = tkfont.Font(root=a, font=style.lookup("Lab.Treeview.Heading", "font"))
        body_font = tkfont.Font(root=a, font=style.lookup("Lab.Treeview", "font"))
        minima = {key: heading_font.measure(a.tree.heading(key, "text")) + 30
                  for key in ("hora", "clase", "confianza", "categoria")}
        minima["hora"] = max(minima["hora"], body_font.measure("23:59:59") + 24)
        minima["confianza"] = max(minima["confianza"], body_font.measure("100.0 %") + 24)
        extra = max(0, available-sum(minima.values()))
        for key, ratio in (("hora", .04), ("clase", .57), ("confianza", .04), ("categoria", .35)):
            a.tree.column(key, width=round(minima[key]+extra*ratio), minwidth=minima[key], stretch=True)
        a.needs_render = True

    def open_settings(self):
        if self.window is None or not self.window.winfo_exists():
            self.build_settings()
        self.sync_controls()
        self.window.deiconify()
        self.window.lift()
        self.window.focus_set()
        return "break"

    def build_settings(self):
        self.window = ctk.CTkToplevel(self.app)
        self.window.title("Ajustes de interfaz · Laboratorio")
        self.window.geometry(f"560x{min(720, self.app.winfo_screenheight()-90)}")
        self.window.minsize(480, 440)
        self.window.configure(fg_color=theme.BG)
        self.window.transient(self.app)
        self.window.protocol("WM_DELETE_WINDOW", self.window.withdraw)
        content = ctk.CTkScrollableFrame(self.window, fg_color=theme.PANEL,
                                       scrollbar_button_color=theme.SCROLL_THUMB)
        content.pack(fill="both", expand=True, padx=14, pady=14)
        content.grid_columnconfigure(0, weight=1)
        row = 0
        def label(text):
            nonlocal row
            ctk.CTkLabel(content, text=text, font=theme.font(14, "bold"), text_color=theme.TEXT, anchor="w").grid(row=row, column=0, sticky="ew", padx=8, pady=(15, 5))
            row += 1
        def menu(values, command):
            nonlocal row
            widget = ctk.CTkOptionMenu(content, values=values, command=command,
                                      fg_color=theme.SECONDARY, text_color=theme.TEXT)
            widget.grid(row=row, column=0, sticky="ew", padx=8, pady=3)
            row += 1
            return widget
        label("APARIENCIA")
        self.theme_control = menu(["Claro", "Oscuro", "Sistema"], self.set_theme)
        label("TAMAÑO DE TEXTO")
        self.scale_control = menu(["85 %", "100 %", "115 %", "130 %", "145 %"], lambda v: self.set_scale(float(v.split()[0])/100))
        label("DENSIDAD DE INTERFAZ")
        self.density_control = menu(["Compacta", "Normal", "Cómoda"], self.set_density)
        label("VISUALIZACIÓN")
        self.visibility_vars = {}
        for key, text in (("top3", "Mostrar Top 3"), ("history", "Mostrar historial"),
                          ("status", "Mostrar barra de estado"), ("stats", "Mostrar estadísticas de sesión")):
            var = tk.BooleanVar(value=self.prefs["visible"][key])
            self.visibility_vars[key] = var
            ctk.CTkCheckBox(content, text=text, variable=var, text_color=theme.TEXT,
                command=lambda name=key, value=var: self.set_visible(name, value.get())).grid(row=row, column=0, sticky="w", padx=8, pady=5)
            row += 1
        label("PANELES")
        for key, text in (("camera", "Cámara / imagen"), ("info", "Información del elemento")):
            button = ctk.CTkButton(content, text=f"Expandir / contraer {text}", command=lambda name=key: self.toggle_section(name), **theme.button_style())
            button.grid(row=row, column=0, sticky="ew", padx=8, pady=3)
            row += 1
        label("TAMAÑOS INDEPENDIENTES")
        self.font_controls = {}
        for key, text in (("title", "Título del objeto"), ("confidence", "Confianza"),
                          ("education", "Información educativa"), ("history", "Historial")):
            ctk.CTkLabel(content, text=text, text_color=theme.MUTED, anchor="w").grid(row=row, column=0, sticky="ew", padx=8)
            row += 1
            self.font_controls[key] = menu(["100 %", "115 %", "130 %", "145 %"], lambda v, name=key: self.set_font_factor(name, float(v.split()[0])/100))
        label("DISEÑOS RÁPIDOS")
        self.preset_control = menu(["Equilibrado", "Cámara", "Información", "Exposición"], self.preset)
        label("RESTAURAR")
        ctk.CTkButton(content, text="RESTABLECER INTERFAZ", command=self.reset, **theme.button_style()).grid(row=row, column=0, sticky="ew", padx=8, pady=5)
        row += 1
        self.save_notice = ctk.CTkLabel(content, text="Preferencias guardadas automáticamente", text_color=theme.MUTED, wraplength=410)
        self.save_notice.grid(row=row, column=0, sticky="ew", padx=8, pady=12)
        theme.style_controls(self.window, self.prefs["scale"])
        self.sync_controls()

    def sync_controls(self):
        if self.window is None or not self.window.winfo_exists():
            return
        self.theme_control.set(self.prefs["theme"])
        self.scale_control.set(f'{self.prefs["scale"]*100:.0f} %')
        self.density_control.set(self.prefs["density"])
        for key, var in self.visibility_vars.items():
            var.set(self.prefs["visible"][key])
        for key, control in self.font_controls.items():
            control.set(f'{self.prefs["fonts"][key]*100:.0f} %')

    def preset(self, value):
        camera, workspace = {"Equilibrado": (.60, .78), "Cámara": (.75, .87),
                             "Información": (.45, .78), "Exposición": (.65, .90)}[value]
        self.prefs["ratios"] = {"camera": camera, "workspace": workspace}
        self.prefs["collapsed"] = {key: False for key in self.prefs["collapsed"]}
        self.prefs["visible"]["top3"] = self.prefs["visible"]["history"] = True
        self.prefs["fonts"]["title"] = self.prefs["fonts"]["confidence"] = 1.30 if value == "Exposición" else 1.0
        self.prefs["collapsed"]["history"] = value == "Exposición"
        self.maximized = None
        self.apply_preferences()
        self.save()

    def reset(self):
        if not messagebox.askyesno("Restablecer interfaz", "¿Volver al tema claro, texto al 100 % y diseño inicial con todas las secciones visibles?", parent=self.window or self.app):
            return False
        self.prefs = ui_preferences.defaults()
        self.maximized = None
        self.apply_preferences()
        self.save()
        return True

    def close(self):
        self.capture_ratios()
        self.save()
        for job in (self.reflow_job, self.ratio_job, self.system_job):
            if job:
                self.app.after_cancel(job)
        for tooltip in self.tooltips:
            tooltip.hide()
