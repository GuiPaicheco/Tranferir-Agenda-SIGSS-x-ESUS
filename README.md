# Transferência de agenda — prévia de homologação

1. Extraia o ZIP e dê dois cliques em **Abrir Agenda.bat**. O Windows precisa de Python e Chrome ou Edge. O iniciador usa as permissões do usuário.
2. Clique em **Abrir SIGSS**, **Abrir e-SUS** e **Abrir planilha**. Se os sites pedirem login, use sua própria conta na janela que o programa abriu. A planilha exige permissão na conta Google.
3. Volte ao aplicativo, informe a unidade, a data e o profissional e clique em **Buscar agendas**. Use o nome completo quando a busca encontrar mais de uma opção.
4. Confira a prévia. O programa arredonda para a grade de 20 minutos, com empate para a vaga seguinte. Assim, 15h16 vira 15h20 e 15h30 vira 15h40. Dois registros ativos convertidos para a mesma vaga ficam sinalizados.

O e-SUS abre na homologação. A prévia lê as linhas visíveis do SIGSS, por isso consultas paginadas ainda precisam de validação. O botão da planilha abre a auditoria restrita. 

## Transferir e auditar

Na primeira configuração, habilite a API Google Sheets em um projeto Google Cloud autorizado e crie um cliente OAuth do tipo aplicativo para computador. Salve o JSON como `%USERPROFILE%\.agenda-sigss\google-client.json`. Na primeira transferência, autorize pelo navegador com uma conta que já possa editar a auditoria. O botão Abrir planilha serve para consulta.

Busque a agenda, confira a prévia e clique em **Transferir prévia**. A execução é restrita à homologação e exige uma página única e CBO confirmado. Use uma estação executora. O diário local em `%USERPROFILE%\.agenda-sigss` separa gravação no e-SUS e auditoria. Se o salvamento ficar incerto, confira o e-SUS antes de qualquer nova tentativa. Mantenha o diário e o token no computador autorizado.
