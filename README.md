# Transferência de agenda — prévia de homologação

No Windows, extraia o ZIP e abra o PowerShell na pasta extraída. Com Python instalado:

```powershell
py -m pip install playwright
py -m playwright install chromium
$env:AGENDA_ESUS_HML_URL = "URL da agenda do e-SUS de homologação"
py app.py
```

Entre normalmente no SIGSS pela primeira guia do navegador aberto pelo programa.
Abra Atendimento Consulta, selecione unidade, data e profissional e clique em
Buscar. Informe os mesmos filtros no aplicativo e clique em Ler agenda. Se a
consulta estiver paginada, revise cada página. A prévia analisa apenas as linhas
visíveis e, portanto, a detecção de conflito é limitada à página lida.

A segunda guia abre o eSUS de homologação para conferência manual. A versão
atual é uma prévia de leitura. O salvamento no eSUS, a retomada e a integração
com a auditoria online ainda precisam ser implementados e validados.

O SIGSS de Betim já vem configurado. Em AGENDA_ESUS_HML_URL, informe a página inicial da agenda de homologação. Na segunda guia, selecione a data e o profissional pela interface. Use AGENDA_SIGSS_URL apenas se precisar substituir o endereço padrão.
