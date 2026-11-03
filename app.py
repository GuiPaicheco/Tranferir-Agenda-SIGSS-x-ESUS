"""Primeira versão: leitura do SIGSS e prévia para revisão."""
import os
import re
import threading
import tkinter as tk
from collections import Counter
from datetime import datetime
from queue import Empty, Queue
from tkinter import messagebox, ttk
from urllib.parse import urlsplit

SIGSS = os.environ.get("AGENDA_SIGSS_URL", "http://sigss.betim.mg.gov.br/sigss/atendimentoConsultaAgenda2.jsp")
ESUS = "http://kappa-hml:8080/agenda/"
SHEETS = "https://docs.google.com/spreadsheets/d/1IC0yg-J58_wYIdYwIy19BJPnCyoiw-wzPWC-NH2v2II/edit?usp=sharing"

def convert(hour):
    h, m = map(int, hour.split(":"))
    rounded = 20 * ((h * 60 + m + 10) // 20)
    return "-" if rounded >= 1440 else f"{rounded // 60:02d}:{rounded % 60:02d}"

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
               f"Prioridade SIGSS: {r['priority']}" if r.get("priority") else "-", state)

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
                    events.put(("message", "Entre no SIGSS se solicitado e volte ao aplicativo."))
                except Error:
                    events.put(("message", "O SIGSS não abriu. Confira a rede da Prefeitura e tente novamente."))
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
            expected_date, professional, unit = command
            try:
                if sigss.is_closed():
                    sigss = context.new_page()
                date_input = sigss.locator('input[name="agco.agcoData"]')
                if not date_input.is_visible():
                    sigss.goto(SIGSS, wait_until="domcontentloaded", timeout=15000)
                if not date_input.is_visible():
                    sigss.bring_to_front()
                    raise ValueError("Faça login no SIGSS e volte ao aplicativo.")
                select_sigss(sigss, "agco_unidadeSaudeDestino", unit)
                date_input.fill(expected_date, timeout=4000)
                date_input.press("Tab")
                select_sigss(sigss, "agco_profissionalDestino", professional)
                sigss.evaluate("""() => {
                  const grid = document.querySelector('#cb_grid_busca')?.closest('.ui-jqgrid');
                  if (!grid) throw new Error('Grade da agenda não encontrada');
                  const watch = { changed: 0 };
                  const isRow = node => node.nodeType === 1 &&
                    (node.matches('tr.jqgrow, td[title*="Prontuário:"]') ||
                     node.querySelector('tr.jqgrow, td[title*="Prontuário:"]'));
                  watch.observer = new MutationObserver(records => {
                    if (records.some(r => [...r.addedNodes, ...r.removedNodes].some(isRow)
                        || r.target.parentElement?.closest('.ui-paging-info'))) {
                      watch.changed = performance.now();
                    }
                  });
                  watch.observer.observe(grid, {subtree: true, childList: true, characterData: true});
                  window.agendaWatch = watch;
                }""")
                try:
                    sigss.get_by_role("button", name="Buscar", exact=True).click(timeout=4000)
                    sigss.wait_for_function("""() => {
                      const changed = window.agendaWatch?.changed;
                      const grid = document.querySelector('#cb_grid_busca')?.closest('.ui-jqgrid');
                      const loading = [...(grid?.querySelectorAll('.loading, .jqgrid-overlay') || [])]
                        .some(el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden');
                      return changed && !loading && performance.now() - changed >= 350;
                    }""", timeout=20000)
                except Error as exc:
                    raise ValueError("O SIGSS não confirmou a atualização da grade. Tente buscar novamente.") from exc
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
                    rows: [...document.querySelectorAll('td[title*="Prontuário:"]')]
                      .filter(c => c.getBoundingClientRect().width)
                      .map(c => ({
                        hour: c.closest('tr').innerText.match(/\\b\\d\\d:\\d\\d\\b/)?.[0],
                        name: c.querySelector('b')?.innerText,
                        code: c.title.match(/Prontuário:\\s*([^\\n]+)/)?.[1]?.trim(),
                        status: c.closest('tr').cells[c.cellIndex+1]?.innerText.trim(),
                        priority: (() => {
                          const headers = [...(document.querySelector('#cb_grid_busca')
                            ?.closest('tr')?.cells || [])];
                          const index = headers.findIndex(h => /^(Risco|Prioridade)$/i.test(h.innerText.trim()));
                          const cell = c.closest('tr').cells[index];
                          if (!cell) return '';
                          return [cell, ...cell.querySelectorAll('[title], [aria-label], img[alt]')]
                            .flatMap(el => [el.innerText, el.title, el.getAttribute('aria-label'), el.getAttribute('alt')])
                            .map(value => value?.trim()).find(value => value && value !== 'Risco') || '';
                        })()
                      }))
                  };
                }""")
                if (result["day"] != expected_date
                    or professional.casefold() not in (result["professional"] or "").casefold()
                    or unit.casefold() not in (result["unit"] or "").casefold()):
                    raise ValueError("Os filtros do SIGSS não correspondem à data, unidade e profissional informados.")
                rows = result["rows"]
                if not rows:
                    raise ValueError("Nenhum registro visível. Confira os filtros e clique em Buscar no SIGSS.")
                if any(not r.get("name") or not r.get("code") or
                       not re.fullmatch(r"\d{2}:\d{2}", r.get("hour") or "") for r in rows):
                    raise ValueError("A lista contém registros incompletos. Confira a tela do SIGSS.")
                message = "Agenda aberta no e-SUS."
                try:
                    if esus is None or esus.is_closed():
                        esus = context.new_page()
                        esus.goto(ESUS, wait_until="domcontentloaded", timeout=12000)
                    selected = (result["professional"] or "").split(" — ")
                    full_name = selected[1] if len(selected) > 1 else professional
                    open_esus_agenda(esus, expected_date, full_name)
                except (Error, ValueError):
                    esus.bring_to_front()
                    message = "Confira o login ou o acesso ao e-SUS e tente novamente."
                events.put(("rows", (list(preview(rows)), message)))
            except ValueError as err:
                events.put(("message", str(err)))
            except Exception:
                events.put(("message", "Falha ao ler o SIGSS. Confira se a guia está aberta e tente novamente."))

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

    def controls(enabled):
        for button in actions:
            button.configure(state="normal" if enabled else "disabled")

    def start_browser():
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
    sigss_button = ttk.Button(setup, text="Abrir SIGSS",
                             command=lambda: send(("sigss",), "Abrindo SIGSS…"))
    sigss_button.pack(side="left", padx=6)
    reconnect = ttk.Button(setup, text="Reconectar navegador", command=start_browser)
    reconnect.pack(side="left")
    fields = []
    for label, default in (("Data", datetime.now().strftime("%d/%m/%Y")),
                           ("Profissional", ""), ("Unidade", "UBS CIDADE VERDE")):
        ttk.Label(form, text=label).pack(side="left", padx=4)
        field = ttk.Entry(form, width=26)
        field.insert(0, default)
        field.pack(side="left", padx=4)
        fields.append(field)
    def read():
        vals = [f.get().strip() for f in fields]
        try:
            datetime.strptime(vals[0], "%d/%m/%Y")
            if not all(vals):
                raise ValueError
        except ValueError:
            messagebox.showwarning("Filtros", "Informe data (dd/mm/aaaa), profissional e unidade.")
            return
        table.delete(*table.get_children())
        send(vals, "Lendo agenda…")
    read_button = ttk.Button(form, text="Buscar agendas", command=read)
    read_button.pack(side="left", padx=8)
    actions = (sigss_button, esus_button, sheets_button, read_button)
    frame = ttk.Frame(root)
    frame.pack(fill="both", expand=True, padx=12, pady=12)
    columns = ("SIGSS", "eSUS previsto", "Paciente", "Cod. SIGSS", "Observação prevista", "Situação")
    table = ttk.Treeview(frame, columns=columns, show="headings")
    for col, width in zip(columns, (75, 90, 235, 95, 235, 245)):
        table.heading(col, text=col)
        table.column(col, width=width)
    scroll = ttk.Scrollbar(frame, command=table.yview)
    table.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    table.pack(fill="both", expand=True)
    status = tk.StringVar(value="Abrindo navegador…")
    ttk.Label(root, textvariable=status).pack(pady=(0, 12))
    def poll():
        try:
            while True:
                kind, data = events.get_nowait()
                if kind == "stopped":
                    controls(False)
                    reconnect.configure(state="normal")
                    continue
                controls(True)
                if kind == "rows":
                    rows, message = data
                    for row in rows:
                        table.insert("", "end", values=row)
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
