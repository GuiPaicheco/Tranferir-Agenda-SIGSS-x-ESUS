"""Primeira versão: leitura do SIGSS e prévia para revisão."""
import json
import os
import re
import threading
import time
import tkinter as tk
from collections import Counter
from datetime import datetime, timedelta
from queue import Empty, Queue
from tkinter import messagebox, ttk
from urllib.parse import urlsplit

from transfer import connect_sheets, existing_appointment, journal, migrate, publish

SIGSS = os.environ.get("AGENDA_SIGSS_URL", "http://sigss.betim.mg.gov.br/sigss/atendimentoConsultaAgenda2.jsp")
ESUS = "http://kappa-hml:8080/agenda/"
SHEETS = "https://docs.google.com/spreadsheets/d/1IC0yg-J58_wYIdYwIy19BJPnCyoiw-wzPWC-NH2v2II/edit?usp=sharing"

def convert(hour):
    h, m = map(int, hour.split(":"))
    rounded = 20 * ((h * 60 + m + 10) // 20)
    return "-" if rounded >= 1440 else f"{rounded // 60:02d}:{rounded % 60:02d}"

PREFERENCES = {
    "preferencial +60 anos": "Preferencial +60 anos",
    "preferencial +80 anos": "Preferencial +80 anos",
    "gestante": "Gestante",
    "pessoa com deficiência": "Pessoa com deficiência",
    "pessoa com deficiï¿½ncia": "Pessoa com deficiência",
}


def preference_notes(titles):
    matched = [(title, PREFERENCES[title.strip().casefold()])
               for title in titles if title.strip().casefold() in PREFERENCES]
    labels = list(dict.fromkeys(label for _, label in matched))
    observation = "Prioridade SIGSS: " + "; ".join(labels) if labels else "-"
    originals = " | ".join(title for title, _ in matched) or "-"
    return observation, originals


def preview(records):
    active = Counter(convert(r["hour"]) for r in records if r["status"] != "Cancelado")
    for r in records:
        hour = convert(r["hour"])
        if r["status"] == "Cancelado":
            state = "Cancelado no SIGSS"
        elif r["status"] != "Não":
            state = "Revisar situação no SIGSS"
        elif active[hour] > 1:
            state = "Mais de um agendamento na mesma vaga"
        elif hour[-2:] not in ("00", "20", "40"):
            state = "Revisar horário convertido"
        else:
            state = "Conferir no eSUS"
        yield (r["hour"], "-" if state != "Conferir no eSUS" else hour,
               r["name"], r["code"],
               *preference_notes(r.get("preference_titles", [])), state)

def select_sigss(page, stem, query):
    field = page.locator(f'[id^="{stem}"][id$="_chzn"]')
    selected = field.locator(".chzn-single")
    if query.casefold() in selected.inner_text(timeout=4000).casefold():
        return
    selected.click(timeout=4000)
    search = field.locator(".chzn-input-search")
    search.fill("")
    search.press_sequentially(query)
    search.press("Enter")
    matches = field.locator(".chzn-results tbody tr:visible").filter(
        has_text=re.compile(re.escape(query), re.I))
    matches.first.wait_for(timeout=6000)
    if matches.count() != 1:
        search.press("Escape")
        raise ValueError("Busca ambígua no SIGSS. Informe o nome completo.")
    matches.click(timeout=4000)


def open_esus_agenda(page, day, professional):
    field = page.get_by_role("textbox", name=re.compile("Busque um profissional"))
    field.fill(professional, timeout=5000)
    matches = page.get_by_role("option", name=re.compile(re.escape(professional), re.I))
    matches.first.wait_for(timeout=6000)
    if matches.count() != 1:
        raise ValueError("Busca ambígua no e-SUS. Informe o nome completo.")
    matches.click(timeout=4000)
    page.wait_for_url(re.compile(r"/agenda/\d+(?:/.*)?$"), timeout=6000)
    professional_id = urlsplit(page.url).path.strip("/").split("/")[1]
    page.goto(f"{ESUS}{professional_id}/{day.replace('/', '')}",
              wait_until="domcontentloaded", timeout=12000)
    field.wait_for(timeout=6000)


def search_day(sigss, expected_date, professional, unit):
    date_input = sigss.locator('input[name="agco.agcoData"]')
    if not date_input.is_visible():
        sigss.goto(SIGSS, wait_until="domcontentloaded", timeout=15000)
    if not date_input.is_visible():
        sigss.bring_to_front()
        raise ValueError("Faça login no SIGSS e volte ao aplicativo.")
    date_input.fill(expected_date, timeout=4000)
    date_input.press("Tab")
    select_sigss(sigss, "agco_unidadeSaudeDestino", unit)
    select_sigss(sigss, "agco_profissionalDestino", professional)
    filters = {"day": expected_date, "unit": unit, "professional": professional}
    if not sigss.evaluate("""filters => {
      const selected = stem => document.querySelector(
        '[id^="' + stem + '"][id$="_chzn"] .chzn-single'
      )?.innerText.toLowerCase() || '';
      return document.querySelector('input[name="agco.agcoData"]')?.value === filters.day
        && selected('agco_unidadeSaudeDestino').includes(filters.unit.toLowerCase())
        && selected('agco_profissionalDestino').includes(filters.professional.toLowerCase());
    }""", filters):
        raise ValueError(f"Data pendente: {expected_date}. Os filtros mudaram antes da busca.")
    sigss.evaluate("""() => {
      const indicator = document.querySelector('#load_grid_busca');
      const grid = document.querySelector('#cb_grid_busca')?.closest('.ui-jqgrid');
      const visible = el => el.getClientRects().length > 0
        && getComputedStyle(el).visibility === 'visible';
      if (!grid || !indicator || visible(indicator)) {
        throw new Error('Indicador indisponível ou consulta anterior em andamento');
      }
      const watch = { started: false, finished: 0, changed: 0, invalid: false };
      const isRow = node => node.nodeType === 1 &&
        (node.matches('tr.jqgrow, td[title*="Prontuário:"]') ||
         node.querySelector('tr.jqgrow, td[title*="Prontuário:"]'));
      watch.observer = new MutationObserver(records => {
        if (!indicator.isConnected || !grid.isConnected) {
          watch.invalid = true;
          return;
        }
        if (visible(indicator)) {
          watch.started = true;
          watch.finished = 0;
        } else if (watch.started && !watch.finished) {
          watch.finished = performance.now();
        }
        if (records.some(record =>
          [...record.addedNodes, ...record.removedNodes].some(isRow) ||
          (record.target.nodeType === 1 ? record.target :
            record.target.parentElement)?.closest('.ui-paging-info'))) {
          watch.changed = performance.now();
        }
      });
      watch.observer.observe(grid, {subtree: true, childList: true, characterData: true});
      watch.observer.observe(indicator, {attributes: true,
        attributeFilter: ['style', 'class', 'hidden']});
      window.agendaWatch = watch;
    }""")
    sigss.get_by_role("button", name="Buscar", exact=True).click(timeout=4000)
    try:
        yield
        if not sigss.evaluate("""() => {
          const indicator = document.querySelector('#load_grid_busca');
          const w = window.agendaWatch;
          return indicator && w && !w.invalid && w.changed
            && (!indicator.getClientRects().length ||
                getComputedStyle(indicator).visibility !== 'visible');
        }"""):
            raise ValueError(f"Data pendente: {expected_date}. A grade não confirmou a atualização após 10 segundos.")
    finally:
        if not sigss.is_closed():
            sigss.evaluate("() => window.agendaWatch?.observer.disconnect()")
    result = sigss.evaluate("""() => {
      const selected = stem => document.querySelector(
        '[id^="' + stem + '"][id$="_chzn"] .chzn-single')?.innerText;
      return {
        day: document.querySelector('input[name="agco.agcoData"]')?.value,
        professional: selected('agco_profissionalDestino'),
        unit: selected('agco_unidadeSaudeDestino'),
        empty: /Nenhum registro/i.test(document.querySelector('#cb_grid_busca')?.closest('.ui-jqgrid')?.innerText || ''),
        paging: document.querySelector('#cb_grid_busca')?.closest('.ui-jqgrid')?.innerText.match(/Página\\s+\\d+\\s+de\\s+\\d+/i)?.[0],
        cbo: selected('agco_atividadeProfissionalCnes'),
        rows: [...document.querySelectorAll('td[title*="Prontuário:"]')]
          .filter(c => c.getBoundingClientRect().width)
          .map(c => ({
            hour: c.closest('tr').innerText.match(/\\b\\d\\d:\\d\\d\\b/)?.[0],
            name: c.querySelector('b')?.innerText,
            code: c.title.match(/Prontuário:\\s*([^\\n]+)/)?.[1]?.trim(),
            status: c.closest('tr').cells[c.cellIndex+1]?.innerText.trim(),
            age: c.innerText.split('\\n').find(line => /\\d+ (ano|mês|dia)/.test(line)),
            preference_titles: [...c.closest('tr').querySelectorAll(
              'img.icon-button--transparent[title], i.icon-button--transparent[title]'
            )].map(icon => icon.getAttribute('title'))
          }))
      };
    }""")
    if (result["day"] != expected_date
        or professional.casefold() not in (result["professional"] or "").casefold()
        or unit.casefold() not in (result["unit"] or "").casefold()):
        raise ValueError("Os filtros do SIGSS não correspondem à data, unidade e profissional informados.")
    rows = result["rows"]
    if not rows:
        if not result.get("empty"):
            raise ValueError(f"O SIGSS não confirmou o resultado de {expected_date}.")
        raise ValueError(f"Data pendente: {expected_date}. A grade vazia ainda precisa ser validada no SIGSS.")
    if any(not r.get("name") or not r.get("code") or
           not re.fullmatch(r"\d{2}:\d{2}", r.get("hour") or "") for r in rows):
        raise ValueError("A lista contém registros incompletos. Confira a tela do SIGSS.")
    selected = (result["professional"] or "").split(" — ")
    full_name = selected[1] if len(selected) > 1 else professional
    pages = re.search(r"de (\d+)", result.get("paging") or "")
    cbo = (result.get("cbo") or "").split(" — ")[-1].strip()
    problems = []
    if not pages or pages.group(1) != "1":
        problems.append("página única não confirmada")
    if not cbo or cbo == "...":
        problems.append("CBO não confirmado")
    complete = ", ".join(problems)
    return (expected_date, full_name, result["unit"].split(" — ")[-1], cbo, rows), complete


def browser_worker(commands, events):
    from playwright.sync_api import Error, sync_playwright
    with sync_playwright() as pw:
        for channel in ("chrome", "msedge"):
            try:
                browser = pw.chromium.launch(channel=channel, headless=False, timeout=12000)
                break
            except Error:
                continue
        else:
            events.put(("message", "Não foi possível iniciar o Chrome ou o Edge instalado."))
            return
        context = browser.new_context()
        sigss = context.new_page()
        esus = None
        sheets = None
        batch = None
        events.put(("ready", "Clique em Abrir SIGSS para entrar com sua conta."))
        while True:
            command = commands.get()
            if command is None:
                return
            if not browser.is_connected():
                events.put(("message", "O navegador foi fechado. Clique em Reconectar navegador."))
                return
            if command[0] == "sigss":
                try:
                    if sigss.is_closed():
                        sigss = context.new_page()
                    sigss.bring_to_front()
                    sigss.goto(SIGSS, wait_until="domcontentloaded", timeout=15000)
                    events.put(("progress", "Aguardando login no SIGSS…"))
                    sigss.wait_for_function("""() =>
                      document.querySelector('input[name="agco.agcoData"]') ||
                      [...document.querySelectorAll('a')].some(link =>
                        link.getAttribute('href')?.includes('atendimentoConsultaAgenda2.jsp'))""",
                      timeout=180000)
                    if not sigss.locator('input[name="agco.agcoData"]').is_visible():
                        sigss.goto(SIGSS, wait_until="domcontentloaded", timeout=15000)
                    sigss.locator('input[name="agco.agcoData"]').wait_for(timeout=8000)
                    events.put(("message", "SIGSS conectado. Informe o período, a unidade e o profissional."))
                except Error:
                    events.put(("message", "O SIGSS não confirmou o login e a abertura da agenda. Clique em Abrir SIGSS para tentar novamente."))
                continue
            if command[0] == "sheets":
                try:
                    if sheets is None or sheets.is_closed():
                        sheets = context.new_page()
                    sheets.bring_to_front()
                    sheets.goto(SHEETS, wait_until="domcontentloaded", timeout=15000)
                    events.put(("message", "Entre no Google com uma conta autorizada, se solicitado."))
                except Error:
                    events.put(("message", "Não foi possível abrir a planilha. Confira a conexão."))
                continue
            if command[0] == "esus":
                try:
                    if esus is None or esus.is_closed():
                        esus = context.new_page()
                    esus.bring_to_front()
                    esus.goto(ESUS, wait_until="domcontentloaded", timeout=12000)
                    esus.bring_to_front()
                    events.put(("message", "Entre no e-SUS se solicitado e confira a data e o profissional."))
                except Exception:
                    events.put(("message", "Não foi possível abrir o e-SUS. Confira o endereço e a rede."))
                continue
            if command[0] == "transfer":
                position = None
                phase = ""
                try:
                    if not batch:
                        raise ValueError("Busque e confira as agendas antes de transferir.")
                    if esus is None or esus.is_closed():
                        raise ValueError("Clique em Abrir e-SUS e faça login antes de transferir.")
                    connect_sheets(sheets, SHEETS)
                    total = sum(len(records) for _, _, _, _, records in batch)
                    completed = 0
                    with journal() as db:
                        for day, name, unit, cbo, records in batch:
                            esus.bring_to_front()
                            open_esus_agenda(esus, day, name)
                            agenda_url = esus.url
                            if not agenda_url.startswith(ESUS) or not agenda_url.rstrip("/").endswith(day.replace("/", "")):
                                raise ValueError("Não foi possível confirmar a agenda de homologação.")
                            for record, planned in zip(records, preview(records)):
                                esus.bring_to_front()
                                position = completed
                                phase = "e-SUS"
                                source, target, _, _, observation, _, state = planned
                                events.put(("item", (position, "Conferindo no e-SUS",
                                    f"{completed + 1}/{total} · {day} · {source}")))
                                key = json.dumps([agenda_url, day, unit, cbo, source, record["code"]])
                                saved = db.execute("SELECT result,note FROM jobs WHERE key=?", (key,)).fetchone()
                                if state == "Conferir no eSUS":
                                    if existing_appointment(esus, target, record["name"]):
                                        reason, note = "-", ""
                                    elif saved and saved[0] in ("pendente", "-"):
                                        raise ValueError(f"Não consegui confirmar o agendamento anterior às {source}. Confira o e-SUS antes de retomar.")
                                    else:
                                        def before_save():
                                            db.execute("INSERT OR REPLACE INTO jobs VALUES (?, ?, ?)", (key, "pendente", ""))
                                            db.commit()
                                        reason, note = migrate(esus, datetime.strptime(day, "%d/%m/%Y").date(),
                                                               record, target, observation, before_save)
                                    db.execute("INSERT OR REPLACE INTO jobs VALUES (?, ?, ?)", (key, reason, note))
                                    db.commit()
                                elif state == "Cancelado no SIGSS":
                                    reason, note = "-", state
                                elif state == "Mais de um agendamento na mesma vaga":
                                    reason, note = state, ""
                                else:
                                    reason, note = "Inconsistência na vaga", state
                                success = reason == "-" and note != "Cancelado no SIGSS"
                                phase = "auditoria"
                                outcome = "Transferido" if success else f"Não transferido · {note or reason}"
                                events.put(("item", (position, f"{outcome} · Registrando na planilha",
                                    f"{completed + 1}/{total} · {day} · {source}")))
                                publish(sheets, [day, source, target if success else "-",
                                        "Transferido" if success else "Não transferido",
                                        record["code"], record["name"], name, cbo, unit, reason], note)
                                completed += 1
                                events.put(("item", (position, f"{outcome} · Auditado",
                                    f"{completed}/{total} registrados na planilha.")))
                                position = None
                    events.put(("message", f"Lote concluído. {completed}/{total} registrados na planilha."))
                except Exception as err:
                    if position is not None:
                        events.put(("item", (position, f"Pendente · {phase}", "Lote pausado.")))
                    detail = str(err) if isinstance(err, ValueError) else f"Falha na etapa {phase or 'de preparação'} ({type(err).__name__}). Confira a sessão e a conexão."
                    events.put(("message", f"Lote interrompido. {detail}"))
                continue
            batch = None
            first, last, professional, unit, automatic = command
            expected_date = first
            try:
                previews, batches, incomplete = [], [], []
                day = datetime.strptime(first, "%d/%m/%Y").date()
                end_day = datetime.strptime(last, "%d/%m/%Y").date()
                while day <= end_day:
                    expected_date = day.strftime("%d/%m/%Y")
                    day += timedelta(days=1)
                    events.put(("progress", f"Buscando {expected_date} no SIGSS…"))
                    group = [expected_date]
                    while automatic and len(group) < 5 and day <= end_day:
                        group.append(day.strftime("%d/%m/%Y"))
                        day += timedelta(days=1)
                    if sigss.is_closed():
                        sigss = context.new_page()
                    extra = []
                    searches = []
                    try:
                        for index, expected_date in enumerate(group):
                            page = sigss if index == 0 else context.new_page()
                            if index:
                                extra.append(page)
                            search = search_day(page, expected_date, professional, unit)
                            searches.append(search)
                            next(search)
                        events.put(("progress", f"Aguardando 10 segundos para {len(group)} consulta(s)…"))
                        time.sleep(10)
                        for expected_date, search in zip(group, searches):
                            try:
                                next(search)
                            except StopIteration as finished:
                                item, complete = finished.value
                            if complete:
                                incomplete.append(f"{expected_date}: {complete}")
                            batches.append(item)
                            previews.extend((expected_date, *row) for row in preview(item[-1]))
                    finally:
                        for search in searches:
                            search.close()
                        for page in extra:
                            page.close()
                warning = ("Transferência bloqueada: " + "; ".join(incomplete)
                           if incomplete else "Confira o lote e clique em Transferir e auditar.")
                batch = None if incomplete else batches
                events.put(("rows", (previews, warning, bool(batch))))
            except ValueError as err:
                events.put(("message", str(err)))
            except Exception:
                events.put(("message", f"Falha na busca de {expected_date}. Confira o login e tente novamente."))


def main():
    root = tk.Tk()
    root.title("Transferência de Agenda | Homologação | Prévia")
    root.geometry("1100x560")
    commands, events = Queue(), Queue()
    def worker():
        try:
            browser_worker(commands, events)
        except Exception:
            events.put(("message", "Não foi possível iniciar o Playwright. Confira a instalação."))
        finally:
            events.put(("stopped", None))

    ready_to_transfer = False

    def controls(enabled):
        for button in actions:
            button.configure(state="normal" if enabled else "disabled")
        selector.configure(state="readonly" if enabled else "disabled")
        if enabled:
            refresh_mode()
            transfer_button.configure(state="normal" if ready_to_transfer else "disabled")

    def start_browser():
        nonlocal ready_to_transfer
        ready_to_transfer = False
        while not commands.empty():
            commands.get_nowait()
        reconnect.configure(state="disabled")
        controls(False)
        status.set("Iniciando navegador…")
        threading.Thread(target=worker, daemon=True).start()

    def send(command, message):
        controls(False)
        status.set(message)
        commands.put(command)
    ttk.Label(root, text="Homologação • faça login nos sistemas e selecione a agenda aqui.").pack(pady=12)
    form = ttk.Frame(root)
    form.pack(fill="x", padx=12)
    setup = ttk.Frame(root)
    setup.pack(fill="x", padx=12, pady=(0, 8))
    sheets_button = ttk.Button(setup, text="Abrir planilha",
                               command=lambda: send(("sheets",), "Abrindo planilha…"))
    sheets_button.pack(side="left", padx=6)
    esus_button = ttk.Button(setup, text="Abrir e-SUS",
                             command=lambda: send(("esus",), "Abrindo e-SUS…"))
    esus_button.pack(side="left")
    sigss_button = ttk.Button(setup, text="1. Abrir SIGSS",
                             command=lambda: send(("sigss",), "Abrindo SIGSS…"))
    sigss_button.pack(side="left", padx=6)
    reconnect = ttk.Button(setup, text="Reconectar navegador", command=start_browser)
    reconnect.pack(side="left")
    sigss_button.pack_configure(before=sheets_button)
    esus_button.pack_configure(after=sigss_button)
    steps = ttk.LabelFrame(root, text="Etapas da operação")
    steps.pack(fill="x", padx=12, pady=6)
    mode = tk.StringVar(value="Manual")
    ttk.Label(steps, text="Modo").pack(side="left", padx=6)
    selector = ttk.Combobox(steps, state="readonly", textvariable=mode,
                            values=("Manual", "Automatizado"), width=14)
    selector.pack(side="left", padx=6)
    fields = []
    for label, default in (("Data de início", datetime.now().strftime("%d/%m/%Y")),
                           ("Data de fim", datetime.now().strftime("%d/%m/%Y")),
                           ("Unidade", "UBS CIDADE VERDE"), ("Profissional", "")):
        ttk.Label(form, text=label).pack(side="left", padx=4)
        field = ttk.Entry(form, width=19)
        field.insert(0, default)
        field.pack(side="left", padx=4)
        fields.append(field)
    def read(automatic=False):
        nonlocal ready_to_transfer
        vals = [f.get().strip() for f in fields]
        try:
            if datetime.strptime(vals[0], "%d/%m/%Y") > datetime.strptime(vals[1], "%d/%m/%Y"):
                raise ValueError
            if not all(vals):
                raise ValueError
        except ValueError:
            messagebox.showwarning("Filtros", "Informe início e fim válidos (dd/mm/aaaa), unidade e profissional.")
            return
        if not automatic and vals[0] != vals[1]:
            messagebox.showwarning("Modo manual", "Use a mesma data nos dois campos ou selecione Automatizado.")
            return
        ready_to_transfer = False
        table.delete(*table.get_children())
        send((vals[0], vals[1], vals[3], vals[2], automatic), "Buscando no SIGSS…")

    read_button = ttk.Button(steps, text="2. Realizar busca", command=read)
    auto_button = ttk.Button(steps, text="2. Buscar período", command=lambda: read(True))
    for button in (read_button, auto_button):
        button.pack(side="left", padx=6)
    def transfer():
        if messagebox.askyesno("Homologação", "Transferir a prévia conferida e atualizar a auditoria?"):
            send(("transfer",), "Transferindo e atualizando a auditoria…")

    transfer_button = ttk.Button(steps, text="3. Transferir e auditar", command=transfer)
    transfer_button.pack(side="left", padx=6)
    actions = (sigss_button, esus_button, sheets_button, read_button,
               auto_button, transfer_button)
    def refresh_mode(event=None):
        manual = mode.get() == "Manual"
        read_button.configure(state="normal" if manual else "disabled")
        auto_button.configure(state="disabled" if manual else "normal")
    selector.bind("<<ComboboxSelected>>", refresh_mode)
    frame = ttk.Frame(root)
    frame.pack(fill="both", expand=True, padx=12, pady=12)
    columns = ("Data", "SIGSS", "eSUS previsto", "Paciente", "Cod. SIGSS", "Observação prevista", "Preferências originais", "Situação")
    table = ttk.Treeview(frame, columns=columns, show="headings")
    for col, width in zip(columns, (100, 70, 85, 200, 85, 200, 200, 220)):
        table.heading(col, text=col)
        table.column(col, width=width)
    scroll = ttk.Scrollbar(frame, command=table.yview)
    table.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    horizontal = ttk.Scrollbar(frame, orient="horizontal", command=table.xview)
    table.configure(xscrollcommand=horizontal.set)
    horizontal.pack(side="bottom", fill="x")
    table.pack(fill="both", expand=True)
    status = tk.StringVar(value="Abrindo navegador…")
    ttk.Label(root, textvariable=status).pack(pady=(0, 12))
    def poll():
        nonlocal ready_to_transfer
        try:
            while True:
                kind, data = events.get_nowait()
                if kind == "stopped":
                    controls(False)
                    reconnect.configure(state="normal")
                    continue
                if kind == "item":
                    index, outcome, message = data
                    item = str(index)
                    if table.exists(item):
                        table.set(item, "Situação", outcome)
                        table.see(item)
                    status.set(message)
                    continue
                if kind == "progress":
                    status.set(data)
                    continue
                controls(True)
                if kind == "rows":
                    rows, message, ready_to_transfer = data
                    controls(True)
                    table.delete(*table.get_children())
                    for index, row in enumerate(rows):
                        table.insert("", "end", iid=str(index), values=row)
                    status.set(f"{len(rows)} registros na prévia. {message}")
                else:
                    status.set(data)
        except Empty:
            pass
        root.after(150, poll)
    start_browser()
    root.protocol("WM_DELETE_WINDOW", lambda: (commands.put(None), root.destroy()))
    poll()
    root.mainloop()

if __name__ == "__main__":
    main()
