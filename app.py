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
ESUS = os.environ.get("AGENDA_ESUS_HML_URL", "")

def convert(hour):
    h, m = map(int, hour.split(":"))
    m = {1: 0, 21: 20, 31: 40, 41: 40, 30: 40}.get(m, m)
    return f"{h:02d}:{m:02d}"

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
               r["name"], r["code"], state)

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
                    events.put(("message", "Entre no SIGSS se solicitado. Abra Atendimento Consulta e clique em Buscar após selecionar os filtros."))
                except Error:
                    events.put(("message", "O SIGSS não abriu. Confira a rede da Prefeitura e tente novamente."))
                continue
            if command[0] == "esus":
                try:
                    if esus is None or esus.is_closed():
                        esus = context.new_page()
                    esus.bring_to_front()
                    esus.goto(command[1], wait_until="domcontentloaded", timeout=12000)
                    esus.bring_to_front()
                    events.put(("message", "Entre no e-SUS se solicitado e confira a data e o profissional."))
                except Exception:
                    events.put(("message", "Não foi possível abrir o e-SUS. Confira o endereço e a rede."))
                continue
            expected_date, professional, unit = command
            try:
                if sigss.is_closed() or sigss.url.split("?")[0] != SIGSS:
                    raise ValueError("Clique em Abrir SIGSS, faça login se solicitado e abra Atendimento Consulta.")
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
                        status: c.closest('tr').cells[c.cellIndex+1]?.innerText.trim()
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
                events.put(("rows", list(preview(rows))))
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
    ttk.Label(root, text="Homologação • confira os filtros e clique em Buscar no SIGSS antes de ler a agenda.").pack(pady=12)
    form = ttk.Frame(root)
    form.pack(fill="x", padx=12)
    setup = ttk.Frame(root)
    setup.pack(fill="x", padx=12, pady=(0, 8))
    ttk.Label(setup, text="Link do e-SUS de homologação").pack(side="left")
    esus_url = ttk.Entry(setup)
    esus_url.insert(0, ESUS)
    esus_url.pack(side="left", fill="x", expand=True, padx=8)

    def open_esus():
        url = esus_url.get().strip()
        try:
            parsed = urlsplit(url)
            if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username is not None
                or not re.fullmatch(r"/agenda(?:/\d+(?:/\d{8})?)?/?", parsed.path)):
                raise ValueError
        except ValueError:
            messagebox.showwarning("Link do e-SUS", "Cole o endereço de homologação que termina em /agenda ou o link da agenda selecionada.")
            return
        send(("esus", url), "Abrindo e-SUS…")

    esus_button = ttk.Button(setup, text="Abrir e-SUS", command=open_esus)
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
    read_button = ttk.Button(form, text="Ler agenda", command=read)
    read_button.pack(side="left", padx=8)
    actions = (sigss_button, esus_button, read_button)
    frame = ttk.Frame(root)
    frame.pack(fill="both", expand=True, padx=12, pady=12)
    columns = ("SIGSS", "eSUS previsto", "Paciente", "Cod. SIGSS", "Situação")
    table = ttk.Treeview(frame, columns=columns, show="headings")
    for col, width in zip(columns, (80, 100, 280, 110, 300)):
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
                    for row in data:
                        table.insert("", "end", values=row)
                    status.set(f"{len(data)} registros na prévia.")
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
