#!/usr/bin/env python3
"""
Build docs/Regles_tables_Notion.pdf: the two-page recap, for the sales team, of
what each synced Notion table shows and why (scope rule of
src/integrations/notion_scope.py, schedules, who fills which column).

Rebuild it whenever one of those rules changes:

    pip install reportlab   # not a dependency of the pipeline
    python scripts/build_notion_rules_pdf.py
"""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

OUT = Path(__file__).resolve().parent.parent / "docs" / "Regles_tables_Notion.pdf"
UPDATED = "30/09/2026"

# Arial covers the French accents, the euro sign and "≥"; Helvetica is the fallback.
FONTS = Path("/System/Library/Fonts/Supplemental")
try:
    pdfmetrics.registerFont(TTFont("Body", str(FONTS / "Arial.ttf")))
    pdfmetrics.registerFont(TTFont("Body-Bold", str(FONTS / "Arial Bold.ttf")))
    pdfmetrics.registerFontFamily("Body", normal="Body", bold="Body-Bold", italic="Body", boldItalic="Body-Bold")
    BODY, BOLD = "Body", "Body-Bold"
except Exception:
    BODY, BOLD = "Helvetica", "Helvetica-Bold"

INK = colors.HexColor("#1F2A37")
MUTED = colors.HexColor("#5B6573")
ACCENT = colors.HexColor("#1E6B5C")
TINT = colors.HexColor("#EAF3F1")
LINE = colors.HexColor("#D5DBE1")

TITLE = ParagraphStyle("title", fontName=BOLD, fontSize=17, leading=21, textColor=INK)
SUB = ParagraphStyle("sub", fontName=BODY, fontSize=9, leading=12, textColor=MUTED)
H = ParagraphStyle("h", fontName=BOLD, fontSize=11.5, leading=15, textColor=ACCENT, spaceBefore=9, spaceAfter=4)
P = ParagraphStyle("p", fontName=BODY, fontSize=9, leading=12.2, textColor=INK, alignment=TA_LEFT)
SMALL = ParagraphStyle("small", parent=P, fontSize=8, leading=10.6, textColor=MUTED)
CELL = ParagraphStyle("cell", parent=P, fontSize=8.4, leading=11)
CELL_B = ParagraphStyle("cellb", parent=CELL, fontName=BOLD)
HEAD = ParagraphStyle("head", parent=CELL, fontName=BOLD, textColor=colors.white)


def bullets(items, style=P, numbered=False):
    marks = [f"<b>{i}.</b>" for i in range(1, len(items) + 1)] if numbered else ["•"] * len(items)
    indent = 12 if numbered else 9
    return [Paragraph(f"{mark}&nbsp;&nbsp;{text}", ParagraphStyle("b", parent=style, leftIndent=indent,
                                                                  firstLineIndent=-indent, spaceAfter=2.5))
            for mark, text in zip(marks, items)]


def grid(rows, widths, header=True, bold_first_column=True):
    data = [[Paragraph(cell, HEAD if header and r == 0 else (CELL_B if c == 0 and bold_first_column else CELL))
             for c, cell in enumerate(row)] for r, row in enumerate(rows)]
    table = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), ACCENT)]
    table.setStyle(TableStyle(style))
    return table


def boxed(flowables, width):
    box = Table([[flowables]], colWidths=[width])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), TINT), ("BOX", (0, 0), (-1, -1), 0.6, ACCENT),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return box


def lifecycle(width):
    """The life of a row in three steps, left to right."""
    step = ParagraphStyle("step", parent=CELL, alignment=1, leading=10.5)
    arrow = ParagraphStyle("arrow", parent=CELL, alignment=1, fontSize=13, textColor=ACCENT, leading=16)
    cells = [
        Paragraph("<b>Dans le périmètre</b><br/>la ligne est visible", step),
        Paragraph("→", arrow),
        Paragraph("<b>Sort du périmètre</b><br/>la synchro l'archive : cachée, gardée", step),
        Paragraph("→", arrow),
        Paragraph("<b>6 mois plus tard</b><br/>corbeille Notion (30 jours pour la récupérer)", step),
    ]
    box = (width - 2 * 9 * mm) / 3
    table = Table([cells], colWidths=[box, 9 * mm, box, 9 * mm, box])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (0, 0), 0.6, ACCENT), ("BOX", (2, 0), (2, 0), 0.6, MUTED), ("BOX", (4, 0), (4, 0), 0.6, MUTED),
        ("BACKGROUND", (0, 0), (0, 0), TINT),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont(BODY, 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(16 * mm, 10 * mm, f"Merci Raymond · synchronisation Furious vers Notion · règles du {UPDATED}")
    canvas.drawRightString(A4[0] - 16 * mm, 10 * mm, f"{doc.page} / 2")
    canvas.restoreState()


def build() -> Path:
    width = A4[0] - 32 * mm
    q = lambda text: f"«&nbsp;{text}&nbsp;»"  # noqa: E731
    story = [
        Paragraph("Tables commerciales Notion : ce qui s'affiche, et pourquoi", TITLE),
        Spacer(1, 3),
        Paragraph("Devis à suivre / relancer · Pipe travaux · Devis gagnés · Devis perdus. "
                  "Remplies chaque matin depuis Furious.", SUB),
        Spacer(1, 8),
        boxed([
            Paragraph("La règle, identique dans les 4 tables", ParagraphStyle("bh", parent=H, spaceBefore=0)),
            Paragraph(f"Une ligne est visible quand <b>{q('Dans le périmètre')} est coché</b> et "
                      f"<b>{q('Pris en charge')} ne l'est pas</b>.", P),
            Spacer(1, 4),
            *bullets([
                f"{q('Dans le périmètre')} est coché par la synchro, chaque matin, tant que le devis correspond "
                "à la table (voir le tableau). Toutes les vues filtrent sur cette case.",
                f"Quand un devis sort du périmètre, la synchro coche {q('Pris en charge')} et remplit la date "
                "d'archivage : la ligne disparaît des vues, rien n'est supprimé.",
                f"Cocher {q('Pris en charge')} vous-même archive la ligne. La synchro ne le décoche jamais. "
                "Elle ne retire que ses propres coches, si le devis revient dans le périmètre.",
                "Le 1<super>er</super> de chaque mois, les lignes archivées, hors périmètre et dont la date "
                "d'archivage a plus de 6 mois partent à la corbeille Notion (récupérables 30 jours).",
                "Horaires, heure de Paris : synchro vers 8 h, Pipe travaux vers 8 h 15 (7 h et 7 h 15 en hiver). "
                "Un changement fait dans Furious aujourd'hui apparaît dans Notion demain matin.",
            ]),
        ], width),
        Spacer(1, 8),
        lifecycle(width),
        Paragraph("Le périmètre de chaque table", H),
        grid([
            ["Table", "Contient", "Une ligne sort quand"],
            ["Devis à suivre / relancer",
             "Tous les devis en attente (Brief, En cours, Envoyée(s) attente réponse), quelle que soit leur date. "
             "Un devis mis en gagné dans Notion y reste jusqu'à la signature.",
             f"Le devis est gagné, perdu ou supprimé. Son {q('Statut')} affiche alors le statut Furious."],
            ["Pipe travaux",
             "Les devis TRAVAUX en attente, de probabilité ≥ 10 %, dont la date du devis ou le début de projet "
             "tombe dans les 365 prochains jours.",
             "Gagné, perdu, probabilité sous 10 %, ou dates hors de la fenêtre."],
            ["Devis gagnés",
             "Les devis signés depuis 12 mois (gagné = signé : date de signature) et les avenants signés "
             "depuis 12 mois, rangés sous leur devis. Un devis plus ancien reste affiché s'il porte un "
             "avenant récent.",
             "Plus de 12 mois, ou n'est plus gagné dans Furious."],
            ["Devis perdus",
             "Les devis perdus depuis 12 mois (date de perte : le jour du passage en perdu, dans Furious ou "
             "depuis Notion), sauf "
             f"{q('Devis en doublon')}.",
             "Plus de 12 mois, ou rouvert dans Furious."],
        ], [31 * mm, 88 * mm, width - 119 * mm]),
        Spacer(1, 4),
        Paragraph(f"Hors de cette règle : {q('Suivi signatures')} (MAINTENANCE gagnés de l'année) et "
                  f"{q('Récent projets travaux')} (projets TRAVAUX créés dans l'année), qui cumulent l'année. "
                  "Les devis de test (titre commençant par TEST) ne vont ni dans Devis gagnés ni dans Devis perdus.",
                  SMALL),
        PageBreak(),
        Paragraph("Qui remplit quoi", ParagraphStyle("h0", parent=H, spaceBefore=0)),
        grid([
            ["Rempli par la synchro : modifié dans Notion, écrasé le lendemain",
             "Rempli par l'équipe : jamais écrasé par la synchro"],
            ["Client, Montant, Statut ou Statut Furious (sauf dans Devis à suivre et Pipe travaux), dates Furious (dont la "
             "Date signature des Devis gagnés), Typologie, "
             f"Probabilité, Commercial, Chef de projet, Lien Furious, Motif de perte, Type (Devis / Avenant), "
             f"{q('Dans le périmètre')}, {q('Archivé par la synchro')} (colonne cachée). "
             "Le Nom n'est écrit qu'à la création : un renommage est conservé.",
             f"Commentaire, Origine Transfo, {q('Pris en charge')}, date d'archivage (remplie automatiquement "
             f"quand on coche), Notes Mathilde, Next Steps Commercial, "
             f"Statut (Pipe travaux), Statut de Devis à suivre (envoyé à Furious, voir ci-dessous)."],
        ], [width / 2, width / 2], bold_first_column=False),
        Spacer(1, 4),
        Paragraph(f"Quand un devis de {q('Devis à suivre')} est gagné ou perdu, son Commentaire et son "
                  f"Origine Transfo sont recopiés dans {q('Devis gagnés')} ou {q('Devis perdus')}, "
                  "à la création de la ligne. Ensuite, les deux lignes sont indépendantes.", SMALL),
        Paragraph("Devis à suivre : le statut se change dans Notion", H),
        *bullets([
            "<b>Brief, En cours, Envoyée(s) attente réponse</b> : changez-les dans Notion, Furious suit en quelques "
            "secondes.",
            f"<b>gagné</b> : le client a dit oui, la signature n'est pas là. Reste dans Notion seulement (Furious garde "
            f"le devis en attente). Vue {q('Gagnés, en attente de signature')}.",
            "<b>Signature reçue</b> : passez le devis en gagné dans Furious, comme avant (le projet se crée). "
            "Le lendemain matin, il quitte Devis à suivre et arrive dans Devis gagnés, à la date de signature.",
            "<b>Perdu : raison</b> (budget trop élevé, sans réponse du client, projet abandonné, concurrent retenu, "
            "garde son prestataire, groupement, doublon, on ne se positionne pas, réponse trop tardive, autre) : "
            "le devis passe en perdu dans Furious avec cette raison, et arrive le lendemain dans Devis perdus, "
            "daté du jour. « Perdu » sans raison est refusé.",
            "Furious refuse (ex. « BU est requis » : champ à remplir dans Furious) : le statut revient comme avant, "
            "avec un commentaire sur la page qui dit pourquoi.",
            "Un statut changé dans Furious arrive dans Notion le lendemain matin, sauf sur une ligne en gagné.",
        ]),
        Paragraph("Bon à savoir sur Devis gagnés", H),
        *bullets([
            "Chaque ligne montre les chiffres d'un seul document Furious : le devis avec son propre montant, "
            "chaque avenant en sous-ligne avec le sien. Le total d'un mois = devis + avenants signés ce mois-là.",
            "Gagné = signé : un devis passe en gagné dans Furious le jour où arrive la signature officielle. "
            "La Date signature est ce jour-là (un avenant, le jour de son acceptation). Rien à saisir.",
            f"Vues : {q('Signés (12 derniers mois)')}, {q('Signés cette année')} (passe seule à l'année suivante "
            f"le 1<super>er</super> janvier) et le graphique {q('Montant signé par mois')}.",
            f"Les graphiques comptent toutes les lignes du périmètre, y compris celles {q('Pris en charge')}.",
            "Gagné (signé) se marque dans Furious ; Notion l'affiche le lendemain matin.",
        ]),
        Paragraph("Je ne vois pas un devis : à vérifier dans l'ordre", H),
        *bullets([
            "Correspond-il au périmètre de cette table ? (tableau page 1)",
            "A-t-il été créé ou modifié dans Furious aujourd'hui ? Il sera là demain matin.",
            f"{q('Pris en charge')} est-il coché ? Retirez le filtre rapide {q('Pris en charge = non')} "
            "pour voir les lignes archivées.",
            f"La vue filtre-t-elle une personne ? ({q('Vue personnelle')} : vous êtes Commercial ou "
            f"Chef de projet ; {q('Pipe Vincent')}, {q('Clémence')}... : cette personne.)",
            f"Est-ce un devis de test ou un {q('Devis en doublon')} ? Ils sont exclus.",
            "Toujours rien : chaque matin vers 9 h 30 (8 h 30 en hiver), un contrôle compare Notion et Furious et signale "
            "tout écart par mail. Prévenez Taddeo.",
        ], numbered=True),
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
                            topMargin=14 * mm, bottomMargin=16 * mm,
                            title="Tables commerciales Notion : règles", author="Merci Raymond")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return OUT


if __name__ == "__main__":
    print(build())
