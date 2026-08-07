#!/usr/bin/env python3
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/pdf/fondazione-semplice-metodo.pdf"
NAVY = colors.HexColor("#101A2B")
BLUE = colors.HexColor("#234A73")
GOLD = colors.HexColor("#C9A227")
PALE = colors.HexColor("#EEF2F6")
INK = colors.HexColor("#17202A")
MUTED = colors.HexColor("#596675")


class MethodDocument(BaseDocTemplate):
    def __init__(self, filename: str):
        super().__init__(
            filename,
            pagesize=A4,
            leftMargin=20 * mm,
            rightMargin=20 * mm,
            topMargin=22 * mm,
            bottomMargin=18 * mm,
            title="Fondazione Semplice - Metodo e Architettura",
            author="Fondazione Semplice",
            subject="Metodo, strategie, hardware e motori decisionali del paper trading",
        )
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="normal")
        self.addPageTemplates(PageTemplate(id="content", frames=frame, onPage=self.decorate))

    def decorate(self, canvas, doc):
        canvas.saveState()
        canvas.setFillColor(NAVY)
        canvas.rect(0, A4[1] - 12 * mm, A4[0], 12 * mm, fill=1, stroke=0)
        canvas.setFillColor(GOLD)
        canvas.setFont("Helvetica-Bold", 8)
        canvas.drawString(20 * mm, A4[1] - 8 * mm, "FONDAZIONE SEMPLICE")
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(A4[0] - 20 * mm, 10 * mm, f"Pagina {doc.page}")
        canvas.restoreState()


styles = getSampleStyleSheet()
styles.add(
    ParagraphStyle(
        "CoverTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=29,
        leading=34,
        textColor=NAVY,
        alignment=TA_CENTER,
        spaceAfter=8 * mm,
    )
)
styles.add(
    ParagraphStyle(
        "CoverSub",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=13,
        leading=19,
        textColor=BLUE,
        alignment=TA_CENTER,
    )
)
styles.add(
    ParagraphStyle(
        "H1x",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=NAVY,
        spaceBefore=4 * mm,
        spaceAfter=4 * mm,
    )
)
styles.add(
    ParagraphStyle(
        "H2x",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=17,
        textColor=BLUE,
        spaceBefore=3 * mm,
        spaceAfter=2 * mm,
    )
)
styles.add(
    ParagraphStyle(
        "Bodyx",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9.6,
        leading=14.2,
        textColor=INK,
        alignment=TA_JUSTIFY,
        spaceAfter=2.7 * mm,
    )
)
styles.add(
    ParagraphStyle(
        "Lead",
        parent=styles["BodyText"],
        fontName="Helvetica-Oblique",
        fontSize=12,
        leading=18,
        textColor=BLUE,
        alignment=TA_JUSTIFY,
        borderColor=GOLD,
        borderWidth=0,
        borderPadding=(0, 0, 0, 8),
        leftIndent=7 * mm,
        spaceAfter=6 * mm,
    )
)
styles.add(
    ParagraphStyle(
        "Small",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=8.2,
        leading=11.5,
        textColor=MUTED,
        alignment=TA_LEFT,
    )
)


def p(text, style="Bodyx"):
    return Paragraph(text, styles[style])


def section(title, body):
    return KeepTogether([p(title, "H2x"), Spacer(1, 1.5 * mm), p(body)])


def styled_table(data, widths, header=True):
    table = Table(data, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    commands = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.6),
        ("LEADING", (0, 0), (-1, -1), 10.5),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B9C3CE")),
        ("ROWBACKGROUNDS", (0, 1 if header else 0), (-1, -1), [colors.white, PALE]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if header:
        commands += [
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ]
    table.setStyle(TableStyle(commands))
    return table


story = [
    Spacer(1, 23 * mm),
    p("FONDAZIONE SEMPLICE", "CoverTitle"),
    p("Metodo, strategie, architettura e governo di un esperimento di previsione finanziaria", "CoverSub"),
    Spacer(1, 16 * mm),
    p(
        "Non costruiamo un oracolo. Costruiamo un'istituzione capace di dubitare in modo misurabile, "
        "di ricordare ogni decisione e di preferire la sopravvivenza alla vanita di avere ragione.",
        "Lead",
    ),
    Spacer(1, 18 * mm),
    styled_table(
        [
            ["Stato", "Paper trading controllato"],
            ["Capitale virtuale", "310 USDT per corsia"],
            ["Orizzonte dichiarato", "Ricerca del percorso verso 5.000 USDT"],
            ["Principio sovrano", "Nessun modello puo oltrepassare il Risk Engine"],
            ["Release metodologica", "bootstrap-paper-v1.4"],
        ],
        [48 * mm, 112 * mm],
        header=False,
    ),
    Spacer(1, 23 * mm),
    p("Documento di progetto - 7 agosto 2026", "Small"),
    PageBreak(),
    p("1. Il mandato", "H1x"),
    p(
        "Fondazione Semplice e un laboratorio di paper trading autonomo, progettato per confrontare cinque "
        "strategie sul medesimo mercato e con lo stesso capitale iniziale. Ogni corsia riceve gli stessi dati, "
        "ma interpreta il futuro con una diversa combinazione di previsione, ragionamento e prudenza.",
    ),
    p(
        "L'obiettivo sperimentale e osservare quale disciplina possa tentare il cammino da 310 a 5.000 USDT. "
        "Il rapporto richiesto e circa 16,13 volte il capitale, pari a un rendimento cumulato del 1.512,9%. "
        "Questa cifra non e una promessa: e una frontiera di ricerca, severa abbastanza da smascherare ogni "
        "illusione statistica e ogni strategia che confonda velocita con progresso.",
    ),
    section(
        "La prima legge: sopravvivere",
        "Il sistema non forza una posizione per dimostrare di essere vivo. HOLD e una decisione legittima. "
        "Un errore tecnico produce FAIL_CLOSED; una soglia non viene abbassata soltanto per generare attivita. "
        "La corsa verso 5.000 vale soltanto se il percorso resta auditabile, ripetibile e limitato nel rischio.",
    ),
    section(
        "La seconda legge: separare previsione e potere",
        "Kronos prevede, Nemotron propone, il Risk Engine autorizza, Arena simula. Nessun modello linguistico "
        "possiede credenziali Coinbase e nessun componente generativo puo inviare ordini reali.",
    ),
    section(
        "La terza legge: ricordare",
        "Il ledger persistente conserva contanti, posizioni, prezzi medi, commissioni, profitto realizzato, "
        "equity e drawdown. Una richiesta gia elaborata non puo produrre un secondo fill.",
    ),
    PageBreak(),
    p("2. L'architettura della previsione", "H1x"),
    p(
        "Il sistema e diviso in un piano di controllo umano e un piano operativo autonomo. ChatGPT/Codex "
        "prepara modifiche versionate; GitHub ne custodisce il commit immutabile; OpenClaw le applica soltanto "
        "quando l'operatore lo interpella. La VPS continua a osservare il mercato tra un intervento e l'altro.",
    ),
    styled_table(
        [
            ["Livello", "Componente", "Responsabilita"],
            ["Controllo", "Operatore + ChatGPT/Codex", "Disegno, revisione e documentazione delle strategie"],
            ["Controllo", "GitHub", "Sorgente verificabile e commit immutabili"],
            ["Controllo", "OpenClaw su U50", "Deploy esplicito, test, rollback e report"],
            ["Dati", "Coinbase Market Feed", "Candele e bid/ask pubblici, senza chiavi"],
            ["Previsione", "Kronos-base", "Direzione, rendimento atteso, confidenza e volatilita"],
            ["Proposta", "Nemotron 9B v2", "BUY, SELL o HOLD in JSON vincolato"],
            ["Governo", "Risk Engine", "Limiti, cooldown, spread, stop loss e blocco live"],
            ["Simulazione", "Arena", "Cinque portafogli, fill paper e classifica"],
            ["Osservazione", "Prometheus + Grafana", "Metriche, motivazioni e stato delle release"],
        ],
        [25 * mm, 45 * mm, 90 * mm],
    ),
    Spacer(1, 5 * mm),
    section(
        "Il ruolo di OctoBot",
        "OctoBot resta un ambiente ausiliario per dashboard e backtest. Non appartiene al percorso core "
        "Market Feed - Arena - Decision Service - modelli - Risk Engine e non custodisce chiavi nella release paper.",
    ),
    PageBreak(),
    p("3. Hardware e motori", "H1x"),
    styled_table(
        [
            ["Risorsa", "Configurazione", "Funzione"],
            ["VPS", "Google Cloud g2-standard-8", "8 vCPU e 32 GB RAM per servizi e Kronos"],
            ["GPU", "NVIDIA L4, 24 GB VRAM", "Inferenza Nemotron tramite SGLang"],
            ["Disco", "SSD dedicato da circa 350 GB", "Modelli, volumi, ledger e database"],
            ["Rete", "Servizi interni Docker", "Superficie pubblica ridotta al gateway HTTPS"],
        ],
        [30 * mm, 58 * mm, 72 * mm],
    ),
    Spacer(1, 5 * mm),
    section(
        "Kronos-base",
        "Kronos trasforma una finestra di candele OHLCV in una traiettoria futura. Fondazione ne ricava "
        "direzione, rendimento atteso, volatilita e confidenza. Le cinque corsie condividono la stessa "
        "previsione per la medesima candela: duplicare il calcolo non aggiungerebbe informazione.",
    ),
    section(
        "NVIDIA Nemotron Nano 9B v2 e SGLang",
        "Nemotron interpreta forecast e portafoglio e produce una proposta strutturata. SGLang mantiene il "
        "modello sulla L4 e serve le richieste delle corsie AI. Il timeout e un confine operativo: superarlo "
        "non genera un'azione tardiva, ma un HOLD sicuro e osservabile.",
    ),
    section(
        "Risk Engine deterministico",
        "Il Risk Engine non prevede e non persuade. Controlla simboli ammessi, freschezza, spread, confidenza, "
        "allocazione, perdita giornaliera, numero di posizioni, cooldown, stop loss e blocco live. La sua "
        "autorita e superiore a quella dei modelli.",
    ),
    section(
        "Arena e ledger",
        "Arena applica bid/ask, fee e slippage a fill virtuali idempotenti. Ogni corsia possiede contanti e "
        "posizioni indipendenti; nessuna puo utilizzare il capitale o i risultati di un'altra.",
    ),
    PageBreak(),
    p("4. Le cinque scuole", "H1x"),
    p(
        "Le corsie non sono cinque copie dello stesso agente. Sono cinque ipotesi concorrenti sulla relazione "
        "fra prudenza, frequenza, previsione e controllo quantitativo.",
    ),
    styled_table(
        [
            ["Corsia", "Strategia", "AI", "Conf.", "Posizione", "Perdita", "Cooldown"],
            ["1", "Kronos + Nemotron conservativa", "Si", "0,75", "10%", "2%", "30 min"],
            ["2", "Kronos + Nemotron aggressiva", "Si", "0,60", "20%", "4%", "10 min"],
            ["3", "Kronos quantitativa", "No", "0,70", "12%", "2,5%", "20 min"],
            ["4", "Baseline tecnica", "No", "0,65", "10%", "2%", "20 min"],
            ["5", "Agente sperimentale", "Si", "0,68", "8%", "1,5%", "30 min"],
        ],
        [14 * mm, 55 * mm, 12 * mm, 15 * mm, 20 * mm, 18 * mm, 22 * mm],
    ),
    Spacer(1, 5 * mm),
    section(
        "Corsia 1 - selezione",
        "Accetta pochi segnali e protegge il capitale. Misura se una soglia elevata produce una curva piu "
        "stabile, anche al prezzo di lunghi periodi senza posizione.",
    ),
    section(
        "Corsia 2 - accelerazione controllata",
        "Concede maggiore allocazione e un cooldown piu breve. E la candidata naturale a muoversi prima, "
        "ma deve dimostrare che la velocita non moltiplica soltanto fee e drawdown.",
    ),
    section(
        "Corsie 3 e 4 - tribunali quantitativi",
        "Rimuovono Nemotron dalla decisione e misurano il contributo puro del forecast. Sono il controllo "
        "sperimentale necessario per sapere se il linguaggio aggiunge valore o soltanto narrativa.",
    ),
    section(
        "Corsia 5 - esplorazione vincolata",
        "Sperimenta con Nemotron ma dispone dei limiti piu stretti. L'esplorazione e ammessa soltanto entro "
        "un perimetro che renda economico anche l'errore.",
    ),
    PageBreak(),
    p("5. Il cammino da 310 a 5.000", "H1x"),
    p(
        "Una crescita di sedici volte non si governa con un'unica scommessa. Viene scomposta in ere misurabili. "
        "Ogni passaggio richiede un numero sufficiente di osservazioni, drawdown accettabile e assenza di errori "
        "tecnici persistenti. La promozione dipende dal metodo, non dal calendario.",
    ),
    styled_table(
        [
            ["Era", "Soglia indicativa", "Domanda scientifica"],
            ["Bootstrap", "310 USDT", "I dati e i modelli producono decisioni valide e spiegabili?"],
            ["Conservazione", "500 USDT", "Il rendimento sopravvive a fee, spread e slippage?"],
            ["Conferma", "1.000 USDT", "La strategia regge regimi differenti e walk-forward?"],
            ["Espansione", "2.000 USDT", "Il vantaggio resta stabile senza aumentare il rischio?"],
            ["Obiettivo", "5.000 USDT", "Il risultato e ripetibile o dipende da pochi eventi estremi?"],
        ],
        [30 * mm, 35 * mm, 95 * mm],
    ),
    Spacer(1, 5 * mm),
    p(
        "Il punteggio di una corsia non coincide con l'equity. Deve considerare rendimento netto, drawdown, "
        "commissioni, stabilita, frequenza, decisioni respinte e incidenti FAIL_CLOSED. Una corsia che guadagna "
        "rapidamente distruggendo la propria capacita di sopravvivere non sta avanzando verso la meta.",
    ),
    section(
        "La prima mossa",
        "Alla prima candela reale Coinbase di questa release, ogni corsia esegue una sola sonda paper pari "
        "all'1% dell'equity su BTC/USDT. La sonda non e un segnale del modello: e un atto deterministico, marcato "
        "BOOTSTRAP_PROBE, che verifica entro la prima ora l'intera catena di esecuzione. Dati freschi, spread, "
        "stop-loss e limiti restano sottoposti al Risk Engine. Dopo il fill, ogni strategia torna autonoma.",
    ),
    PageBreak(),
    p("6. Governo, osservabilita e sicurezza", "H1x"),
    section(
        "Controllo delle modifiche",
        "Ogni strategia e descritta in file versionati. Una nuova release riceve un identificatore, supera "
        "validazione e test, viene pubblicata su GitHub e infine applicata da OpenClaw mediante commit "
        "immutabile. ChatGPT/Codex non opera direttamente sulla VPS.",
    ),
    section(
        "Osservabilita",
        "Grafana mostra equity, rendimento, cash, fee, posizioni, drawdown, azioni e reason code. Prometheus "
        "raccoglie contatori e gauge. Il ledger resta la fonte persistente per fill e portafogli. Gli smoke "
        "test operano su un ledger effimero isolato e non possono alterare le corsie osservate.",
    ),
    section(
        "Dominio pubblico",
        "La dashboard puo essere pubblicata su fondazione.pianodivino.com tramite HTTPS automatico e reverse "
        "proxy. Soltanto le porte 80 e 443 vengono esposte; Grafana conserva il proprio login e i servizi "
        "interni restano vincolati alla rete Docker o a localhost.",
    ),
    styled_table(
        [
            ["Evento", "Risposta obbligatoria"],
            ["Timeout o errore modello", "HOLD e reason code FAIL_CLOSED"],
            ["Dati vecchi o spread eccessivo", "Rifiuto deterministico"],
            ["Perdita giornaliera oltre soglia", "Blocco della corsia"],
            ["Richiesta live non autorizzata", "Rifiuto indipendente dal modello"],
            ["Commit non verificato", "OpenClaw interrompe il deploy"],
        ],
        [55 * mm, 105 * mm],
    ),
    PageBreak(),
    p("7. Dichiarazione finale", "H1x"),
    p(
        "Fondazione Semplice non pretende di abolire l'incertezza. La organizza. Trasforma ogni previsione in "
        "un'ipotesi, ogni operazione in un esperimento e ogni perdita in informazione che non deve essere "
        "dimenticata. Il suo valore non risiede nella promessa di 5.000, ma nella capacita di distinguere un "
        "vantaggio reale da una coincidenza fortunata prima che il capitale reale venga esposto.",
        "Lead",
    ),
    p(
        "Il sistema resta paper-only. Non costituisce consulenza finanziaria, non garantisce rendimenti e non "
        "autorizza l'impiego di capitale reale. Il passaggio a shadow o live richiede una release separata, "
        "almeno trenta giorni di evidenza, backtest walk-forward, controllo delle chiavi, kill switch e una "
        "decisione umana esplicita.",
    ),
    Spacer(1, 12 * mm),
    styled_table(
        [
            ["Formula istituzionale", "Prevedere senza comandare. Decidere senza improvvisare. Ricordare senza eccezioni."],
            ["Scopo operativo", "Confrontare cinque percorsi paper da 310 verso 5.000 USDT."],
            ["Criterio di verita", "Risultati netti, persistenti, spiegabili e ripetibili."],
        ],
        [45 * mm, 115 * mm],
        header=False,
    ),
]


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    MethodDocument(str(OUTPUT)).build(story)
    print(OUTPUT)


if __name__ == "__main__":
    main()
