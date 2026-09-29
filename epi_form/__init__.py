"""
EPI request form: a small web page where field workers ask for PPE and work
clothing, backed by the Notion bases "Articles EPI", "Registre EPI",
"Équipe EPI" and "Demandes EPI".

Flow:
1. A worker opens the private link, picks their name and sets quantities with
   "+" / "-" (their sizes are pre-selected from "Équipe EPI").
2. The service creates one "Demandes EPI" page (who, what, when) and one
   "Registre EPI" line per article (Statut "Demandé"), then e-mails the office.
3. The e-mail buttons, or the "Valider" / "Refuser" columns in Notion, open a
   confirmation page. There the office picks, per article, "Remis" (the stock
   formulas of "Articles EPI" move at once), "À commander" (goes to the order
   list) or "Refusé". Nothing changes until that page is confirmed.
"""
