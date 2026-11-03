"""Transferência em homologação e auditoria pelo Google Sheets."""
import re
import sqlite3
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

HEADERS = ("Data da agenda", "Hora no SIGSS", "Hora no eSUS", "Resultado",
           "Cod. SIGSS", "Nome completo do paciente", "Profissional", "CBO",
           "Unidade", "Motivo da não transferência")
STATE = Path.home() / ".agenda-sigss"


def compatible(age, birth, day):
    if not age:
        return False
    years, months, days = [int(m.group(1)) if (m := re.search(r"(\d+) " + part, age)) else 0
                           for part in ("ano", "mês", "dia")]
    y, m = divmod(birth.year * 12 + birth.month - 1 + years * 12 + months, 12)
    m += 1
    return date(y, m, min(birth.day, monthrange(y, m)[1])) + timedelta(days=days) == day


def existing_appointment(page, target, name):
    label = page.locator(".rbc-label").get_by_text(target, exact=True)
    if label.count() != 1:
        return False
    cards = label.evaluate("""(label, name) => {
      const norm = s => s.normalize('NFC').trim().replace(/\\s+/g, ' ').toLowerCase();
      const y = label.getBoundingClientRect().top;
      const slot = [...document.querySelectorAll('.rbc-day-slot .rbc-time-slot')]
        .find(el => Math.abs(el.getBoundingClientRect().top-y)<15);
      if (!slot) return null;
      const r = slot.getBoundingClientRect();
      return [...document.querySelectorAll('.rbc-day-slot .rbc-event')]
        .filter(card => {
          const top = card.getBoundingClientRect().top;
          return top >= r.top-2 && top < r.bottom-2;
        }).map(card => card.innerText.split('\\n').some(line => norm(line) === norm(name)));
    }""", name)
    if cards is None or (any(cards) and len(cards) != 1):
        raise ValueError("Não consegui confirmar um único agendamento na vaga.")
    return any(cards)


def migrate(page, day, row, target, observation, before_save):
    label = page.locator(".rbc-label").get_by_text(target, exact=True)
    if label.count() != 1:
        return "Inconsistência na vaga", "Horário ausente no e-SUS"
    label.scroll_into_view_if_needed(timeout=4000)
    index = label.evaluate("""el => {
      const y = el.getBoundingClientRect().top;
      return [...document.querySelectorAll('.rbc-day-slot .rbc-time-slot')]
        .findIndex(slot => Math.abs(slot.getBoundingClientRect().top-y)<15);
    }""")
    if index < 0:
        return "Inconsistência na vaga", "Horário sem vaga correspondente"
    slot = page.locator(".rbc-day-slot .rbc-time-slot").nth(index)
    if "rbc-time-slot-available" not in (slot.get_attribute("class") or ""):
        return "Inconsistência na vaga", "Vaga bloqueada ou ocupada"
    slot.hover(timeout=4000)
    slot.get_by_role("button", name="Adicionar agendamento", exact=True).click(timeout=4000)
    citizen = page.get_by_role("textbox", name="Cidadão*", exact=True)
    citizen.wait_for(timeout=5000)
    try:
        hour = page.get_by_role("textbox", name="Horário*", exact=True)
        if hour.input_value(timeout=4000) != target or not page.get_by_text(day.strftime("%d/%m/%Y"), exact=True).count():
            raise ValueError("Data ou horário divergente no formulário.")
        citizen.fill(row["name"], timeout=4000)
        options = page.get_by_role("option")
        options.first.wait_for(timeout=6000)
        texts = options.all_inner_texts()
        if not texts or (len(texts) == 1 and re.search("nenhum", texts[0], re.I)):
            return "Cadastro não encontrado", ""
        if len(texts) != 1:
            return "Cadastro com duplicidade", ""
        birth = re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", texts[0])
        if not birth or row["name"].casefold() not in texts[0].casefold():
            return "Inconsistência no Cadastro", "Nome ou nascimento não confirmado"
        d, m, y = map(int, birth.groups())
        if not compatible(row.get("age"), date(y, m, d), day):
            return "Inconsistência no Cadastro", "Nascimento incompatível com a idade do SIGSS"
        options.click(timeout=4000)
        if observation != "-":
            page.get_by_role("textbox", name=re.compile("Observação", re.I)).fill(observation, timeout=4000)
        save = page.get_by_role("button", name="Salvar", exact=True)
        save.wait_for(timeout=4000)
        before_save()
        save.click(timeout=4000)
        page.get_by_text("Agendamento de consulta cadastrado com sucesso.", exact=True).wait_for(timeout=6000)
        return "-", ""
    finally:
        if citizen.is_visible():
            close = page.get_by_role("button", name=re.compile("^(Cancelar|Fechar)$"))
            if close.count() == 1:
                close.click(timeout=2000)
            else:
                page.keyboard.press("Escape")


def connect_sheets(page, sheet_url):
    if page is None or page.is_closed():
        raise ValueError("Clique em Abrir planilha e entre com sua conta Google.")
    expected = urlsplit(sheet_url)
    current = urlsplit(page.url)
    if current.hostname != expected.hostname or current.path.split("/")[:4] != expected.path.split("/")[:4]:
        raise ValueError("Faça login e abra a planilha de auditoria pelo aplicativo.")
    page.bring_to_front()
    page.context.grant_permissions(["clipboard-read", "clipboard-write"],
                                   origin="https://docs.google.com")
    tab = page.get_by_role("button", name="Histórico de transferências", exact=True).or_(
        page.get_by_role("tab", name="Histórico de transferências", exact=True))
    tab.click(timeout=6000)
    if read_range(page, "A1:J1") != [list(HEADERS)]:
        raise ValueError("Cabeçalhos da auditoria divergentes. Confira a planilha.")
    return page


def select_range(page, address):
    box = page.locator("#t-name-box input, input#t-name-box, input[aria-label=\"Caixa de nome\"], input[aria-label=\"Name box\"]")
    box.fill(address, timeout=4000)
    box.press("Enter", timeout=4000)
    return box


def read_range(page, address):
    import csv
    import io

    select_range(page, address)
    page.evaluate("() => navigator.clipboard.writeText('')")
    page.keyboard.press("Control+c")
    page.wait_for_function("async () => Boolean(await navigator.clipboard.readText())",
                           timeout=5000)
    raw = page.evaluate("() => navigator.clipboard.readText()")
    return list(csv.reader(io.StringIO(raw), delimiter="\t"))


def set_audit_color(page, row, transferred):
    select_range(page, f"A{row}:F{row}")
    page.get_by_role("button", name=re.compile(r"^(Cor do texto|Text color)", re.I)).click(timeout=4000)
    color = r"^(Preto|Black)$" if transferred else r"^(Vermelho|Red)$"
    palette = page.get_by_role("gridcell", name=re.compile(color, re.I))
    if not palette.count():
        palette = page.locator("[aria-label]").and_(
            page.get_by_label(re.compile(color, re.I)))
    palette.click(timeout=4000)


def publish(page, values, note):
    page.bring_to_front()
    import csv
    import io

    box = select_range(page, "A1")
    page.keyboard.press("Control+End")
    last = re.fullmatch(r"[A-Z]+(\d+)", box.input_value(timeout=4000))
    if not last:
        raise ValueError("Não consegui confirmar a última linha da planilha.")
    end = int(last.group(1))
    table = read_range(page, f"A1:J{end}")
    if not table or tuple(table[0]) != HEADERS:
        raise ValueError("Cabeçalhos da auditoria divergentes.")
    keys = (0, 1, 4, 6, 7, 8)
    matches = [i for i, r in enumerate(table[1:], 2)
               if len(r) >= 9 and all(r[k] == values[k] for k in keys)]
    if len(matches) > 1:
        raise ValueError("Mais de uma linha correspondente na auditoria.")
    row = matches[0] if matches else len(table) + 1
    if not matches:
        row = max((i for i, cells in enumerate(table, 1) if any(cells)), default=1) + 1
        if any(any(cells) for cells in table[row-1:]):
            raise ValueError("Não consegui confirmar uma linha livre na auditoria.")
    select_range(page, f"A{row}")
    output = io.StringIO()
    csv.writer(output, delimiter="\t", lineterminator="\r\n").writerow(values)
    page.evaluate("text => navigator.clipboard.writeText(text)", output.getvalue())
    page.keyboard.press("Control+v")
    if read_range(page, f"A{row}:J{row}") != [values]:
        raise ValueError("Não consegui confirmar os dados da auditoria após colar.")
    set_audit_color(page, row, values[3] == "Transferido")
    if note:
        select_range(page, f"J{row}")
        page.keyboard.press("Shift+F2")
        editor = page.locator("textarea:visible")
        if editor.count() != 1:
            raise ValueError("Linha gravada, mas não consegui confirmar o editor da nota.")
        editor.fill(note, timeout=4000)
        page.keyboard.press("Tab")


def journal():
    STATE.mkdir(exist_ok=True)
    db = sqlite3.connect(STATE / "transferencias.sqlite3")
    db.execute("CREATE TABLE IF NOT EXISTS jobs (key TEXT PRIMARY KEY, result TEXT, note TEXT)")
    return db
