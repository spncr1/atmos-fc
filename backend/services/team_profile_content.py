"""Manual Atmos FC copy for selected team profile pages."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True)
class TeamProfileContent:
    summary: str
    sections: tuple[tuple[str, str], ...]
    official_website: str | None = None
    major_trophy_count: int | None = None
    major_achievements: tuple[str, ...] = ()
    achievements_source_url: str | None = None


TEAM_PROFILE_CONTENT: dict[str, TeamProfileContent] = {
    "arsenal": TeamProfileContent(
        summary="Arsenal are one of England's most historic clubs, known for expressive football, a huge global support base, and a North London identity built around style and expectation.",
        sections=(
            ("Who they are", "A Premier League club from North London with a reputation for technical football, academy development, and one of the most recognisable fan cultures in England."),
            ("Club history", "Arsenal grew from Woolwich roots into a London giant, with defining eras under Herbert Chapman, George Graham, and Arsene Wenger."),
            ("Titles won", "Major honours include English league titles, FA Cups, League Cups, and European silverware."),
            ("Club legends", "Key figures include Thierry Henry, Dennis Bergkamp, Tony Adams, Patrick Vieira, Ian Wright, and David Seaman."),
        ),
        official_website="https://www.arsenal.com/",
        major_trophy_count=47,
        major_achievements=(
            "13 English top-flight league",
            "14 FA Cup",
            "2 League Cup",
            "17 Community Shield",
            "1 UEFA Cup Winners' Cup",
        ),
        achievements_source_url="https://www.arsenal.com/history",
    ),
    "barcelona": TeamProfileContent(
        summary="Barcelona are a Catalan football institution, famous for possession football, La Masia, and a club identity that stretches well beyond the pitch.",
        sections=(
            ("Who they are", "A Spanish and Catalan giant whose football culture is tied to attacking play, youth development, and a fiercely distinct civic identity."),
            ("Club history", "Barcelona's modern image was shaped by Johan Cruyff, La Masia, and later the Guardiola era, which became one of football's reference points."),
            ("Titles won", "Major honours include La Liga titles, Copa del Rey wins, UEFA Champions League titles, and FIFA Club World Cup titles."),
            ("Club legends", "Key figures include Lionel Messi, Johan Cruyff, Xavi, Andres Iniesta, Ronaldinho, Carles Puyol, and Sergio Busquets."),
        ),
        official_website="https://www.fcbarcelona.com/",
        major_trophy_count=96,
        major_achievements=(
            "29 La Liga",
            "32 Copa del Rey",
            "16 Spanish Super Cup",
            "2 Spanish League Cup",
            "5 UEFA Champions League",
            "4 UEFA Cup Winners' Cup",
            "5 UEFA Super Cup",
            "3 FIFA Club World Cup",
        ),
        achievements_source_url="https://www.fcbarcelona.com/en/football/primer-equipo/palmares",
    ),
    "bayern munchen": TeamProfileContent(
        summary="Bayern Munich are Germany's dominant modern force, built around relentless standards, elite recruitment, and regular deep European runs.",
        sections=(
            ("Who they are", "A Munich-based club with a winning culture, huge domestic expectations, and a consistent place among Europe's elite."),
            ("Club history", "Bayern became a continental powerhouse in the 1970s and have remained the central force in German football across multiple eras."),
            ("Titles won", "Major honours include Bundesliga titles, DFB-Pokal wins, UEFA Champions League titles, and FIFA Club World Cup titles."),
            ("Club legends", "Key figures include Franz Beckenbauer, Gerd Muller, Philipp Lahm, Oliver Kahn, Thomas Muller, Manuel Neuer, and Robert Lewandowski."),
        ),
        official_website="https://fcbayern.com/",
        major_trophy_count=89,
        major_achievements=(
            "35 German championship",
            "21 DFB-Pokal",
            "13 German Super Cup",
            "6 German League Cup",
            "6 UEFA Champions League",
            "1 UEFA Cup",
            "1 UEFA Cup Winners' Cup",
            "2 UEFA Super Cup",
            "2 Intercontinental Cup",
            "2 FIFA Club World Cup",
        ),
        achievements_source_url="https://fcbayern.com/en/club/honours",
    ),
    "inter": TeamProfileContent(
        summary="Inter are one of Italy's defining clubs, carrying a Milanese identity shaped by European nights, tactical edge, and black-and-blue intensity.",
        sections=(
            ("Who they are", "A Serie A giant from Milan known for fierce rivalries, strong defensive traditions, and a long history of international players."),
            ("Club history", "Inter's identity runs through Grande Inter, multiple Serie A eras, and the 2009/10 treble under Jose Mourinho."),
            ("Titles won", "Major honours include Serie A titles, Coppa Italia wins, UEFA Champions League titles, UEFA Cup wins, and FIFA Club World Cup honours."),
            ("Club legends", "Key figures include Javier Zanetti, Giuseppe Meazza, Ronaldo, Sandro Mazzola, Giacinto Facchetti, Diego Milito, and Samuel Eto'o."),
        ),
        official_website="https://www.inter.it/",
        major_trophy_count=48,
        major_achievements=(
            "21 Serie A",
            "10 Coppa Italia",
            "8 Supercoppa Italiana",
            "3 UEFA Champions League",
            "3 UEFA Cup / Europa League",
            "2 Intercontinental Cup",
            "1 FIFA Club World Cup",
        ),
        achievements_source_url="https://www.inter.it/en/club/palmares",
    ),
    "liverpool": TeamProfileContent(
        summary="Liverpool are one of England's biggest clubs, powered by European heritage, Anfield mythology, and a fan culture built around emotional momentum.",
        sections=(
            ("Who they are", "A Merseyside club with a global following, famous for Anfield, European nights, and one of English football's strongest identities."),
            ("Club history", "Liverpool's story runs through the Shankly and Paisley dynasties, modern European revivals, and the Jurgen Klopp era."),
            ("Titles won", "Major honours include English league titles, FA Cups, League Cups, UEFA Champions League titles, and FIFA Club World Cup honours."),
            ("Club legends", "Key figures include Kenny Dalglish, Steven Gerrard, Ian Rush, John Barnes, Graeme Souness, Mohamed Salah, and Virgil van Dijk."),
        ),
        official_website="https://www.liverpoolfc.com/",
        major_trophy_count=68,
        major_achievements=(
            "20 English top-flight league",
            "8 FA Cup",
            "10 League Cup",
            "16 Charity / Community Shield",
            "6 UEFA Champions League",
            "3 UEFA Cup",
            "4 UEFA Super Cup",
            "1 FIFA Club World Cup",
        ),
        achievements_source_url="https://www.liverpoolfc.com/history/honours",
    ),
    "manchester city": TeamProfileContent(
        summary="Manchester City are a modern powerhouse, defined by technical dominance, tactical control, and one of the most successful English eras of recent football.",
        sections=(
            ("Who they are", "A Manchester club whose modern identity is built around possession, depth, and sustained title contention."),
            ("Club history", "City's history includes long stretches of turbulence before the modern era transformed the club into a domestic and European force."),
            ("Titles won", "Major honours include English league titles, FA Cups, League Cups, UEFA Champions League honours, and FIFA Club World Cup honours."),
            ("Club legends", "Key figures include Sergio Aguero, David Silva, Vincent Kompany, Kevin De Bruyne, Colin Bell, Yaya Toure, and Erling Haaland."),
        ),
        official_website="https://www.mancity.com/",
        major_trophy_count=38,
        major_achievements=(
            "10 English top-flight league",
            "8 FA Cup",
            "9 League Cup",
            "7 Charity / Community Shield",
            "1 UEFA Champions League",
            "1 UEFA Cup Winners' Cup",
            "1 UEFA Super Cup",
            "1 FIFA Club World Cup",
        ),
        achievements_source_url="https://www.mancity.com/club/honours/team",
    ),
    "manchester united": TeamProfileContent(
        summary="Manchester United are one of football's biggest global clubs, shaped by attacking tradition, superstar eras, and the weight of Old Trafford expectation.",
        sections=(
            ("Who they are", "A Manchester club with a massive global following, built around history, drama, youth tradition, and constant pressure to win."),
            ("Club history", "United's identity runs through the Busby Babes, the Munich recovery, and the Sir Alex Ferguson era of domestic and European dominance."),
            ("Titles won", "Major honours include English league titles, FA Cups, League Cups, UEFA Champions League titles, and FIFA Club World Cup honours."),
            ("Club legends", "Key figures include Bobby Charlton, George Best, Denis Law, Eric Cantona, Ryan Giggs, Paul Scholes, Wayne Rooney, and Cristiano Ronaldo."),
        ),
        official_website="https://www.manutd.com/",
        major_trophy_count=68,
        major_achievements=(
            "20 English top-flight league",
            "13 FA Cup",
            "6 League Cup",
            "21 Charity / Community Shield",
            "3 UEFA Champions League",
            "1 UEFA Europa League",
            "1 UEFA Cup Winners' Cup",
            "1 UEFA Super Cup",
            "1 Intercontinental Cup",
            "1 FIFA Club World Cup",
        ),
        achievements_source_url="https://www.manutd.com/en/club/history/trophy-room?pageNo=1",
    ),
    "paris saint germain": TeamProfileContent(
        summary="Paris Saint Germain are France's capital club, carrying star power, domestic dominance, and the pressure of converting glamour into European authority.",
        sections=(
            ("Who they are", "A Paris-based club with huge modern visibility, built around elite talent, a strong domestic platform, and major European ambition."),
            ("Club history", "PSG's rise moved from domestic growth into a star-heavy modern era that reshaped the club's profile across world football."),
            ("Titles won", "Major honours include Ligue 1 titles, Coupe de France wins, Coupe de la Ligue wins, and major European trophies."),
            ("Club legends", "Key figures include Zlatan Ibrahimovic, Kylian Mbappe, Neymar, Ronaldinho, Safet Susic, Rai, George Weah, and Thiago Silva."),
        ),
        official_website="https://www.psg.fr/",
        major_trophy_count=59,
        major_achievements=(
            "14 Ligue 1",
            "16 Coupe de France",
            "9 Coupe de la Ligue",
            "14 Trophée des Champions",
            "2 UEFA Champions League",
            "2 UEFA Super Cup",
            "1 UEFA Cup Winners' Cup",
            "1 FIFA Intercontinental Cup",
        ),
        achievements_source_url="https://www.psg.fr/football-masculin/palmares",
    ),
    "nantes": TeamProfileContent(
        summary="Nantes are one of France's most decorated historic clubs, recognised for their yellow-and-green identity, influential academy and a football tradition known as jeu à la nantaise.",
        sections=(),
        official_website="https://www.fcnantes.com/",
        major_trophy_count=15,
        major_achievements=(
            "8 French league",
            "4 Coupe de France",
            "3 Trophée des Champions",
        ),
        achievements_source_url="https://www.fcnantes.com/articles/article2809.php?num=49238",
    ),
    "sheffield united": TeamProfileContent(
        summary="Sheffield United are a historic English club from South Yorkshire, known as the Blades and rooted at Bramall Lane, one of the world's oldest professional football grounds.",
        sections=(),
        official_website="https://www.sufc.co.uk/",
        major_trophy_count=5,
        major_achievements=(
            "1 English top-flight league",
            "4 FA Cup",
        ),
        achievements_source_url="https://en.wikipedia.org/wiki/Sheffield_United_F.C.",
    ),
    "real madrid": TeamProfileContent(
        summary="Real Madrid are football's European benchmark, defined by Champions League gravity, superstar eras, and a culture where winning is the baseline.",
        sections=(
            ("Who they are", "A Spanish giant from Madrid with unmatched European prestige, a global fanbase, and a club culture centred on decisive moments."),
            ("Club history", "Madrid's identity runs from the Di Stefano era through Galacticos, La Decima, and repeated modern Champions League cycles."),
            ("Titles won", "Major honours include La Liga titles, Copa del Rey wins, UEFA Champions League titles, and FIFA Club World Cup honours."),
            ("Club legends", "Key figures include Alfredo Di Stefano, Cristiano Ronaldo, Raul, Zinedine Zidane, Sergio Ramos, Iker Casillas, Luka Modric, and Karim Benzema."),
        ),
        official_website="https://www.realmadrid.com/",
        major_trophy_count=102,
        major_achievements=(
            "36 La Liga",
            "20 Copa del Rey",
            "13 Spanish Super Cup",
            "1 Spanish League Cup",
            "15 UEFA Champions League",
            "2 UEFA Cup",
            "6 UEFA Super Cup",
            "9 Intercontinental / FIFA Club World Cup",
        ),
        achievements_source_url="https://www.realmadrid.com/en-US/the-club/track-record/football",
    ),
}

ALIASES = {
    "fc barcelona": "barcelona",
    "fc bayern munchen": "bayern munchen",
    "bayern munich": "bayern munchen",
    "internazionale": "inter",
    "fc internazionale milano": "inter",
    "man city": "manchester city",
    "man united": "manchester united",
    "man utd": "manchester united",
    "paris saint germain": "paris saint germain",
    "paris saint germain fc": "paris saint germain",
    "paris saint-germain": "paris saint germain",
    "psg": "paris saint germain",
    "real madrid cf": "real madrid",
    "fc nantes": "nantes",
    "sheffield utd": "sheffield united",
    "sheffield united fc": "sheffield united",
}


def profile_content_for(team_name: str | None) -> TeamProfileContent | None:
    key = normalise(team_name)
    canonical = ALIASES.get(key, key)
    return TEAM_PROFILE_CONTENT.get(canonical)


def normalise(value: str | None) -> str:
    normalised = unicodedata.normalize("NFKD", value or "")
    ascii_value = normalised.encode("ascii", "ignore").decode("ascii")
    return " ".join(re.sub(r"[^a-zA-Z0-9]+", " ", ascii_value).lower().split())
