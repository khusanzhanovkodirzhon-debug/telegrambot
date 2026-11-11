"""Красивое оформление ответов для Telegram.

1. Переводит формулы из «программного» вида (LaTeX: v_0, t^2, \\frac, \\rho)
   в обычные символы: v₀, t², ρ, дроби через «/».
2. Переводит **жирный** текст в HTML-разметку Telegram.
"""

import html
import re

SUPERSCRIPT = str.maketrans("0123456789+-−=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻⁼⁽⁾ⁿ")
SUBSCRIPT = str.maketrans("0123456789+-=()aeoxhklmnpst", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓₕₖₗₘₙₚₛₜ")
SUP_OK = set("0123456789+-−=()n")
SUB_OK = set("0123456789+-=()aeoxhklmnpst")

LATEX_SYMBOLS = {
    r"\cdot": "·", r"\times": "×", r"\div": "÷", r"\pm": "±",
    r"\approx": "≈", r"\neq": "≠", r"\ne": "≠", r"\leq": "≤", r"\le": "≤",
    r"\geq": "≥", r"\ge": "≥", r"\to": "→", r"\rightarrow": "→",
    r"\Rightarrow": "⇒", r"\infty": "∞", r"\circ": "°", r"\degree": "°",
    r"\Delta": "Δ", r"\delta": "δ", r"\alpha": "α", r"\beta": "β",
    r"\gamma": "γ", r"\lambda": "λ", r"\mu": "μ", r"\nu": "ν", r"\pi": "π",
    r"\rho": "ρ", r"\sigma": "σ", r"\tau": "τ", r"\varphi": "φ", r"\phi": "φ",
    r"\omega": "ω", r"\Omega": "Ω", r"\eta": "η", r"\theta": "θ",
    r"\varepsilon": "ε", r"\epsilon": "ε", r"\Phi": "Φ", r"\%": "%",
    r"\,": " ", r"\;": " ", r"\quad": "  ", r"\left": "", r"\right": "",
}


def _script(match: re.Match, table: dict, allowed: set, marker: str) -> str:
    content = match.group(1) or match.group(2)
    if all(ch in allowed for ch in content):
        return content.translate(table)
    if marker == "_":
        return content  # F_{тр} → Fтр (для кириллицы нет нижних индексов)
    return marker + content if len(content) == 1 else f"{marker}({content})"


def latex_to_unicode(text: str) -> str:
    # убираем обёртки формул: $...$, \( \), \[ \]
    text = re.sub(r"\$\$?", "", text)
    text = re.sub(r"\\[\(\)\[\]]", "", text)
    # \text{м/с} → м/с, \mathrm{...} → ...
    text = re.sub(r"\\(?:text|mathrm|mathbf|operatorname)\{([^{}]*)\}", r"\1", text)
    # \sqrt{x} → √x / √(x+y)
    text = re.sub(r"\\sqrt\{([^{}]*)\}",
                  lambda m: "√" + (m.group(1) if len(m.group(1)) <= 2 else f"({m.group(1)})"),
                  text)
    # \frac{a}{b} → a/b или (a+b)/(c)
    def frac(m: re.Match) -> str:
        a, b = m.group(1).strip(), m.group(2).strip()
        wrap = lambda s: s if re.fullmatch(r"[\w·.,²³₀-₉Δ]+", s) else f"({s})"
        return f"{wrap(a)}/{wrap(b)}"
    for _ in range(3):  # несколько раз — для вложенных дробей
        text = re.sub(r"\\d?frac\{([^{}]*)\}\{([^{}]*)\}", frac, text)
    for cmd in sorted(LATEX_SYMBOLS, key=len, reverse=True):
        text = re.sub(re.escape(cmd) + r"(?![A-Za-z])", LATEX_SYMBOLS[cmd], text)
    # степени и индексы: t^2, t^{2}, v_0, v_{0}
    text = re.sub(r"\^\{([^{}]+)\}|\^([-−]?[0-9]+)",
                  lambda m: _script(m, SUPERSCRIPT, SUP_OK, "^"), text)
    text = re.sub(r"\^\s*°", "°", text)  # 20^\circ → 20°
    text = re.sub(r"(?<=[A-Za-zА-Яа-яΔα-ωΑ-Ω])_\{([^{}]+)\}"
                  r"|(?<=[A-Za-zА-Яа-яΔα-ωΑ-Ω])_([0-9a-z])(?![A-Za-zА-Яа-я])",
                  lambda m: _script(m, SUBSCRIPT, SUB_OK, "_"), text)
    return text


def to_telegram_html(text: str) -> str:
    """Markdown-подобный текст модели → безопасный HTML для Telegram."""
    lines = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):                      # заголовок → жирный
            line = "**" + stripped.lstrip("#").strip() + "**"
        elif stripped[:2] in ("* ", "- "):                # пункт списка → •
            line = "• " + stripped[2:]
        lines.append(line)
    text = "\n".join(lines)

    text = html.escape(text, quote=False)                 # <, >, & — безопасно
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)   # **жирный**
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
    text = text.replace("**", "")
    text = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", text)
    return text.strip()


def strip_html(text: str) -> str:
    """Запасной вариант: обычный текст без тегов."""
    return html.unescape(re.sub(r"</?[bi]>", "", text))
