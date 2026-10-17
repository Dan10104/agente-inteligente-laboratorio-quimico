"""Tema Laboratorio: apariencia clara/oscura sin cambiar acciones o contenido."""
from tkinter import ttk

import customtkinter as ctk

FONT = "Segoe UI"
FONT_SIZES = {
    "caption": 11, "small": 12, "label": 13, "body": 14,
    "subtitle": 16, "title": 22, "display": 32,
}
FONT_CAPTION = FONT_SIZES["caption"]
FONT_SMALL = FONT_SIZES["small"]
FONT_LABEL = FONT_SIZES["label"]
FONT_BODY = FONT_SIZES["body"]
FONT_SUBTITLE = FONT_SIZES["subtitle"]
FONT_TITLE = FONT_SIZES["title"]
FONT_DISPLAY = FONT_SIZES["display"]
DENSITIES = ("Compacta", "Normal", "Cómoda")

LIGHT_THEME = {
    "BG": "#EDF2F4", "PANEL": "#FFFFFF", "INNER": "#F4F7F9",
    "BORDER": "#CDD9E1", "TEXT": "#18323F", "MUTED": "#516775",
    "ACCENT": "#087F8C", "BLUE": "#2563EB", "GREEN": "#177448",
    "YELLOW": "#9A5B08", "RED": "#B5363E", "TRACK": "#DCE6EC",
    "VIDEO_BG": "#263D4B", "VIDEO_TEXT": "#D8E6ED", "ROI_COLOR": "#58DEC9",
    "PRIMARY_HOVER": "#066673", "PRIMARY_TEXT": "#FFFFFF",
    "PRIMARY_DISABLED": "#CAE0E3", "SECONDARY": "#E7EEF3",
    "SECONDARY_HOVER": "#D5E3EB", "DANGER_BG": "#FBECEF",
    "DANGER_HOVER": "#F4D9DF", "DANGER_BORDER": "#E7BCC5",
    "DISABLED": "#768B98", "SELECTED": "#C4E7E3", "SELECTED_HOVER": "#AFD8D4",
    "SCROLL_THUMB": "#B2C6D1", "SCROLL_HOVER": "#8AA6B5",
    "CONTROL_BORDER": "#849BAA", "CHECKMARK": "#FFFFFF",
    "HEADING": "#E7EFF4", "HEADING_HOVER": "#D8E7ED", "ROW_SELECTED": "#CDE8E7",
}

DARK_THEME = {
    "BG": "#111B23", "PANEL": "#192630", "INNER": "#22333F",
    "BORDER": "#3C5363", "TEXT": "#E8F0F5", "MUTED": "#ADBFCD",
    "ACCENT": "#4FC7C5", "BLUE": "#83ACFF", "GREEN": "#78D4A3",
    "YELLOW": "#F1C16D", "RED": "#FF9CA5", "TRACK": "#3B5261",
    "VIDEO_BG": "#0B1720", "VIDEO_TEXT": "#DCEBF4", "ROI_COLOR": "#64E5CF",
    "PRIMARY_HOVER": "#74D6D2", "PRIMARY_TEXT": "#10282D",
    "PRIMARY_DISABLED": "#36585D", "SECONDARY": "#2B414F",
    "SECONDARY_HOVER": "#395667", "DANGER_BG": "#472A36",
    "DANGER_HOVER": "#613A48", "DANGER_BORDER": "#925766",
    "DISABLED": "#7E94A4", "SELECTED": "#245F65", "SELECTED_HOVER": "#2C7479",
    "SCROLL_THUMB": "#526E80", "SCROLL_HOVER": "#7894A6",
    "CONTROL_BORDER": "#7995A6", "CHECKMARK": "#10282D",
    "HEADING": "#293F4D", "HEADING_HOVER": "#37596B", "ROW_SELECTED": "#275C65",
}

# Los alias públicos mantienen los nombres originales. CTk resuelve las tuplas
# automáticamente; Tk nativo, ttk y Pillow deben recibir resolve(alias).
for _name in LIGHT_THEME:
    globals()[_name] = (LIGHT_THEME[_name], DARK_THEME[_name])
del _name


def resolve(color):
    """Devolver un color simple para Tk/ttk/Pillow según el modo efectivo de CTk."""
    if isinstance(color, (tuple, list)) and len(color) == 2:
        return color[1] if ctk.get_appearance_mode().lower() == "dark" else color[0]
    return color


def font(size=14, weight="normal", scale=1.0):
    """Fuente central; size puede ser un número o una clave de FONT_SIZES."""
    if isinstance(size, str):
        size = FONT_SIZES[size]
    return FONT, max(8, round(float(size) * float(scale))), weight


def button_style(primary=False, danger=False):
    """Sólo apariencia: no asigna callback, estado, texto ni variable."""
    return dict(
        fg_color=DANGER_BG if danger else ACCENT if primary else SECONDARY,
        hover_color=DANGER_HOVER if danger else PRIMARY_HOVER if primary else SECONDARY_HOVER,
        text_color=RED if danger else PRIMARY_TEXT if primary else TEXT,
        text_color_disabled=PRIMARY_DISABLED if primary else DISABLED,
        border_color=DANGER_BORDER if danger else ACCENT if primary else BORDER,
        border_width=1,
    )


def confidence_color(probability):
    """Color del valor mostrado; no modifica umbrales ni reconocimiento."""
    if probability >= .90:
        return GREEN
    if probability >= .75:
        return ACCENT
    if probability >= .60:
        return YELLOW
    return RED


def _scale_font(widget, scale):
    # Guardar el tamaño base una sola vez evita acumular escala al cambiar tema.
    base = getattr(widget, "_lab_theme_font_base", None)
    if base is None:
        current = widget.cget("font")
        if hasattr(current, "cget"):
            base = (abs(current.cget("size")), current.cget("weight"))
        elif isinstance(current, (tuple, list)) and len(current) >= 2:
            base = (abs(current[1]), current[2] if len(current) >= 3 else "normal")
        else:
            base = (FONT_BODY, "normal")
        widget._lab_theme_font_base = base
    widget.configure(font=font(base[0], base[1], scale))


def _label_color(value):
    # Conservar estados de advertencia/error y textos secundarios ya definidos.
    for name in ("TEXT", "MUTED", "GREEN", "YELLOW", "RED", "ACCENT", "BLUE", "DISABLED", "VIDEO_TEXT"):
        pair = globals()[name]
        if value == pair or value in pair:
            return pair
    return value


def style_controls(parent, scale=1.0):
    """Actualizar controles y fuentes sin tocar variables, contenido ni callbacks."""
    for widget in parent.winfo_children():
        if isinstance(widget, ctk.CTkOptionMenu):
            widget.configure(fg_color=SECONDARY, button_color=SECONDARY,
                button_hover_color=SECONDARY_HOVER, text_color=TEXT,
                text_color_disabled=DISABLED, dropdown_fg_color=PANEL,
                dropdown_hover_color=SECONDARY_HOVER, dropdown_text_color=TEXT,
                font=font(FONT_SMALL, scale=scale), dropdown_font=font(FONT_SMALL, scale=scale), corner_radius=6)
        elif isinstance(widget, ctk.CTkSegmentedButton):
            widget.configure(fg_color=BORDER, selected_color=SELECTED,
                selected_hover_color=SELECTED_HOVER, unselected_color=INNER,
                unselected_hover_color=SECONDARY_HOVER, text_color=TEXT,
                text_color_disabled=DISABLED, font=font(FONT_SMALL, scale=scale), border_width=2, corner_radius=6)
        elif isinstance(widget, ctk.CTkCheckBox):
            widget.configure(fg_color=ACCENT, hover_color=PRIMARY_HOVER,
                border_color=CONTROL_BORDER, checkmark_color=CHECKMARK, text_color=TEXT,
                text_color_disabled=DISABLED, border_width=2, corner_radius=4)
            _scale_font(widget, scale)
        elif isinstance(widget, ctk.CTkSlider):
            widget.configure(fg_color=TRACK, progress_color=ACCENT,
                button_color=ACCENT, button_hover_color=PRIMARY_HOVER)
        elif isinstance(widget, ctk.CTkSwitch):
            widget.configure(fg_color=TRACK, progress_color=ACCENT,
                button_color=CONTROL_BORDER, button_hover_color=SCROLL_HOVER,
                text_color=TEXT, text_color_disabled=DISABLED)
            _scale_font(widget, scale)
        elif isinstance(widget, ctk.CTkLabel):
            widget.configure(text_color=_label_color(widget.cget("text_color")))
            _scale_font(widget, scale)
        elif isinstance(widget, ctk.CTkButton):
            _scale_font(widget, scale)
        elif isinstance(widget, ctk.CTkEntry):
            widget.configure(fg_color=INNER, border_color=BORDER,
                text_color=TEXT, placeholder_text_color=MUTED)
            _scale_font(widget, scale)
        elif isinstance(widget, ctk.CTkTextbox):
            widget.configure(fg_color=INNER, border_color=BORDER, text_color=TEXT,
                scrollbar_button_color=SCROLL_THUMB, scrollbar_button_hover_color=SCROLL_HOVER)
            _scale_font(widget, scale)
        # El selector contiene botones privados que no son acciones independientes.
        if not isinstance(widget, ctk.CTkSegmentedButton):
            style_controls(widget, scale=scale)


def style_history(style, tree, scale=1.0, density="Normal"):
    """Actualizar ttk y bandas de filas sin cambiar selección, datos o columnas."""
    density_name = str(density).casefold()
    row_height = {"compacta": 22, "compacto": 22, "compact": 22,
                  "normal": 26, "cómoda": 32, "comoda": 32}.get(density_name, 26)
    spacing = 2 if row_height == 22 else 3 if row_height == 26 else 5
    style.theme_use("clam")
    style.configure("Lab.Treeview", background=resolve(PANEL), foreground=resolve(TEXT),
        fieldbackground=resolve(PANEL), borderwidth=0,
        rowheight=max(18, round(row_height * scale)), font=font(FONT_CAPTION, scale=scale))
    style.configure("Lab.Treeview.Heading", background=resolve(HEADING), foreground=resolve(TEXT),
        relief="flat", font=font(FONT_CAPTION, "bold", scale),
        padding=(round(8 * scale), round(spacing * scale)))
    style.layout("Lab.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    style.map("Lab.Treeview", background=[("selected", resolve(ROW_SELECTED))],
        foreground=[("selected", resolve(TEXT))])
    style.map("Lab.Treeview.Heading", background=[("active", resolve(HEADING_HOVER))])
    style.configure("Lab.Vertical.TScrollbar", background=resolve(SCROLL_THUMB),
        troughcolor=resolve(INNER), borderwidth=0, arrowcolor=resolve(MUTED),
        arrowsize=max(10, round(12 * scale)))
    style.map("Lab.Vertical.TScrollbar", background=[("active", resolve(SCROLL_HOVER))])
    style.configure("Lab.Horizontal.TScrollbar", background=resolve(SCROLL_THUMB),
        troughcolor=resolve(INNER), borderwidth=0, arrowcolor=resolve(MUTED),
        arrowsize=max(10, round(12 * scale)))
    style.map("Lab.Horizontal.TScrollbar", background=[("active", resolve(SCROLL_HOVER))])
    tree.tag_configure("even", background=resolve(PANEL), foreground=resolve(TEXT))
    tree.tag_configure("odd", background=resolve(INNER), foreground=resolve(TEXT))


def refresh_native(app, scale=1.0, density="Normal"):
    """Sincronizar widgets nativos que no interpretan tuplas de CustomTkinter."""
    video_label = getattr(app, "video_label", None)
    if video_label is not None and video_label.winfo_exists():
        video_label.configure(fg=resolve(VIDEO_TEXT), bg=resolve(VIDEO_BG), font=font(FONT_SUBTITLE, scale=scale))
    tree = getattr(app, "tree", None)
    if tree is not None and tree.winfo_exists():
        style_history(ttk.Style(app), tree, scale=scale, density=density)
