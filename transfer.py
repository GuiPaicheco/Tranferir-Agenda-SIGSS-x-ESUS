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


def migrate(page, day, row, target, observation):
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
        page.get_by_role("button", name="Salvar", exact=True).click(timeout=4000)
        page.get_by_text("Agendamento de consulta cadastrado com sucesso.", exact=True).wait_for(timeout=6000)
        return "-", ""
    finally:
        if citizen.is_visible():
            close = page.get_by_role("button", name=re.compile("^(Cancelar|Fechar)$"))
            if close.count() == 1:
                close.click(timeout=2000)
            else:
                page.keyboard.press("Escape")


def connect_sheets(sheet_url):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    STATE.mkdir(exist_ok=True)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    client, token = STATE / "google-client.json", STATE / "google-token.json"
    creds = Credentials.from_authorized_user_file(str(token), scopes) if token.exists() else None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if not client.exists():
            raise ValueError(f"Coloque o cliente OAuth de aplicativo desktop em {client}.")
        creds = InstalledAppFlow.from_client_secrets_file(str(client), scopes).run_local_server(port=0, timeout_seconds=120)
    token.write_text(creds.to_json(), encoding="utf-8")
    return build("sheets", "v4", credentials=creds).spreadsheets(), urlsplit(sheet_url).path.split("/")[3]


def publish(api, sid, values, note):
    sheet = "'Histórico de transferências'"
    body = api.get(spreadsheetId=sid, ranges=[sheet + "!A1:J"], includeGridData=False).execute()
    meta = next(x["properties"] for x in body["sheets"] if x["properties"]["title"] == "Histórico de transferências")
    table = api.values().get(spreadsheetId=sid, range=sheet + "!A1:J").execute().get("values", [])
    if not table or tuple(table[0]) != HEADERS:
        raise ValueError("Cabeçalhos da auditoria divergentes. Confira a planilha.")
    keys = (0, 1, 4, 6, 7, 8)
    matches = [i for i, r in enumerate(table[1:], 1) if len(r) >= 9 and all(r[k] == values[k] for k in keys)]
    if len(matches) > 1:
        raise ValueError("Mais de uma linha correspondente na auditoria.")
    idx = matches[0] if matches else len(table)
    grid = meta["sheetId"]
    requests = []
    if idx >= meta["gridProperties"]["rowCount"]:
        requests.append({"appendDimension": {"sheetId": grid, "dimension": "ROWS", "length": 100}})
    if not matches and len(table) > 1:
        template = next((i for i, r in enumerate(table[1:], 1) if len(r) > 3 and r[3] == values[3]), 1)
        def area(i):
            return {"sheetId": grid, "startRowIndex": i, "endRowIndex": i+1,
                    "startColumnIndex": 0, "endColumnIndex": 10}
        requests.append({"copyPaste": {"source": area(template), "destination": area(idx), "pasteType": "PASTE_FORMAT"}})
    cells = [{"userEnteredValue": {"stringValue": v}} for v in values]
    d, m, y = map(int, values[0].split("/"))
    cells[0]["userEnteredValue"] = {"numberValue": (date(y,m,d)-date(1899,12,30)).days}
    for i in (1, 2):
        if values[i] != "-":
            h, m = map(int, values[i].split(":"))
            cells[i]["userEnteredValue"] = {"numberValue": (h*60+m)/1440}
    cells[9]["note"] = note
    requests.append({"updateCells": {"start": {"sheetId": grid, "rowIndex": idx, "columnIndex": 0},
                                     "rows": [{"values": cells}], "fields": "userEnteredValue,note"}})
    if len(table) > 1:
        requests.append({"repeatCell": {"range": {"sheetId": grid, "startRowIndex": idx, "endRowIndex": idx+1,
                                                   "startColumnIndex": 0, "endColumnIndex": 6},
            "cell": {"userEnteredFormat": {"textFormat": {"foregroundColorStyle": {
                "rgbColor": {"red": float(values[3] == "Não transferido"), "green": 0, "blue": 0}}}}},
            "fields": "userEnteredFormat.textFormat.foregroundColorStyle"}})
    api.batchUpdate(spreadsheetId=sid, body={"requests": requests}).execute()


def journal():
    STATE.mkdir(exist_ok=True)
    db = sqlite3.connect(STATE / "transferencias.sqlite3")
    db.execute("CREATE TABLE IF NOT EXISTS jobs (key TEXT PRIMARY KEY, result TEXT, note TEXT)")
    return db
